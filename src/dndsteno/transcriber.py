"""Speech-to-text with faster-whisper.

Each audio part is transcribed separately and cached as JSON, so an
interrupted multi-hour run picks up where it stopped. Parts are then merged
into one transcript with absolute timestamps.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from dndsteno.config import TranscribeConfig
from dndsteno.errors import Cancelled  # noqa: F401  (re-exported for callers)

# New paragraph in the markdown after a pause this long, or once a paragraph gets this long.
PARAGRAPH_GAP = 2.0
PARAGRAPH_MAX = 60.0
# Whisper's prompt window is small; an oversized glossary crowds out the audio context.
HOTWORDS_MAX_CHARS = 600
CACHE_VERSION = 2
SAMPLE_RATE = 16000
# Quiet recordings (a laptop mic across the table) confuse both VAD and Whisper.
# Scale so the 99.9th percentile of |sample| lands here; percentile rather than
# peak so a die clattering next to the mic doesn't dictate the gain.
NORMALIZE_TARGET = 0.5
NORMALIZE_MAX_GAIN = 30.0


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Progress:
    part_index: int        # 1-based
    part_count: int
    part_name: str
    position: float        # seconds into the current part
    duration: float        # length of the current part
    cached: bool = False


ProgressCallback = Callable[[Progress], None]

# Whisper learned these from YouTube subtitles and emits them on silence and noise.
HALLUCINATIONS = re.compile(
    r"продолжение следует|субтитр|редактор субтитров|спасибо за просмотр|"
    r"подписывайтесь|ставьте лайк|dimatorzok",
    re.IGNORECASE,
)


def is_hallucination(text: str) -> bool:
    return bool(HALLUCINATIONS.search(text))


def normalize(audio: np.ndarray) -> np.ndarray:
    level = float(np.percentile(np.abs(audio), 99.9)) if len(audio) else 0.0
    if level <= 0:
        return audio
    gain = min(NORMALIZE_TARGET / level, NORMALIZE_MAX_GAIN)
    return np.clip(audio * gain, -1.0, 1.0).astype(np.float32)


def build_prompts(glossary: list[str]) -> tuple[str, str | None]:
    """(initial_prompt, hotwords). Hotwords are re-applied on every 30 s window."""
    initial = "Запись партии в Dungeons & Dragons."
    if not glossary:
        return initial, None
    hotwords = ""
    for word in glossary:
        candidate = f"{hotwords}, {word}" if hotwords else word
        if len(candidate) > HOTWORDS_MAX_CHARS:
            break
        hotwords = candidate
    return f"{initial} Имена и названия: {hotwords}.", hotwords


# Loaded models by (name, device, compute_type): loading large-v3-turbo takes
# seconds and ~1.6 GB of RAM, so repeated runs in one process share it.
_MODELS: dict[tuple[str, str, str], object] = {}
_MODELS_LOCK = threading.Lock()


def get_model(cfg: TranscribeConfig):
    key = (cfg.model, cfg.device, cfg.compute_type)
    with _MODELS_LOCK:
        if key not in _MODELS:
            from faster_whisper import WhisperModel

            _MODELS.clear()  # keep at most one model in memory
            _MODELS[key] = WhisperModel(cfg.model, device=cfg.device, compute_type=cfg.compute_type)
        return _MODELS[key]


class Transcriber:
    def __init__(
        self,
        cfg: TranscribeConfig,
        glossary: list[str] | None = None,
        cancel: threading.Event | None = None,
    ):
        self.cfg = cfg
        self.initial_prompt, self.hotwords = build_prompts(glossary or [])
        self.cancel = cancel or threading.Event()

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

    @property
    def model(self):
        return get_model(self.cfg)

    def transcribe_file(
        self, path: Path, on_position: Callable[[float, float], None] | None = None,
    ) -> tuple[list[Segment], float]:
        """Transcribe one file. Returns (segments, duration in seconds)."""
        from faster_whisper import decode_audio

        self._check_cancel()
        audio = normalize(decode_audio(str(path), sampling_rate=SAMPLE_RATE))
        segments_iter, info = self.model.transcribe(
            audio,
            language=self.cfg.language or None,
            beam_size=self.cfg.beam_size,
            vad_filter=self.cfg.vad,
            vad_parameters={"min_silence_duration_ms": 500},
            # Conditioning on previous text is what produces endless
            # "бухг, бухг, бухг" loops on long noisy recordings.
            condition_on_previous_text=False,
            initial_prompt=self.initial_prompt,
            hotwords=self.hotwords,
        )
        segments = []
        for s in segments_iter:  # lazy: decoding happens while iterating
            self._check_cancel()
            text = s.text.strip()
            if text and not is_hallucination(text):
                segments.append(Segment(round(s.start, 2), round(s.end, 2), text))
            if on_position:
                on_position(s.end, info.duration)
        return segments, info.duration

    def _cache_matches(self, data: dict, audio: Path) -> bool:
        return (
            data.get("version") == CACHE_VERSION
            and data.get("audio") == audio.name
            and data.get("audio_size") == audio.stat().st_size
            and data.get("model") == self.cfg.model
            and data.get("language") == self.cfg.language
            and data.get("initial_prompt") == self.initial_prompt
        )

    def transcribe_parts(
        self,
        audio_files: list[Path],
        cache_dir: Path,
        force: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> list[Segment]:
        """Transcribe parts in order (using cache where valid) and merge with absolute timestamps."""
        cache_dir.mkdir(parents=True, exist_ok=True)
        merged: list[Segment] = []
        offset = 0.0
        count = len(audio_files)

        for i, audio in enumerate(audio_files, 1):
            cache_path = cache_dir / f"{audio.stem}.json"
            data = None
            if not force and cache_path.exists():
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                if not self._cache_matches(data, audio):
                    data = None

            if data is not None:
                if on_progress:
                    on_progress(Progress(i, count, audio.name, data["duration"], data["duration"], cached=True))
            else:
                def on_position(pos, dur, i=i, name=audio.name):
                    if on_progress:
                        on_progress(Progress(i, count, name, pos, dur))

                started = time.monotonic()
                segments, duration = self.transcribe_file(audio, on_position)
                data = {
                    "version": CACHE_VERSION,
                    "audio": audio.name,
                    "audio_size": audio.stat().st_size,
                    "model": self.cfg.model,
                    "language": self.cfg.language,
                    "initial_prompt": self.initial_prompt,
                    "duration": duration,
                    "elapsed": round(time.monotonic() - started, 1),
                    "segments": [asdict(s) for s in segments],
                }
                cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

            for s in data["segments"]:
                merged.append(Segment(round(s["start"] + offset, 2), round(s["end"] + offset, 2), s["text"]))
            offset += data["duration"]

        return merged


def format_timestamp(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def group_paragraphs(segments: list[Segment]) -> list[list[Segment]]:
    paragraphs: list[list[Segment]] = []
    for seg in segments:
        if (
            paragraphs
            and seg.start - paragraphs[-1][-1].end < PARAGRAPH_GAP
            and seg.end - paragraphs[-1][0].start < PARAGRAPH_MAX
        ):
            paragraphs[-1].append(seg)
        else:
            paragraphs.append([seg])
    return paragraphs


def render_markdown(segments: list[Segment], title: str) -> str:
    lines = [f"# {title}", ""]
    for para in group_paragraphs(segments):
        text = " ".join(s.text for s in para)
        lines += [f"`{format_timestamp(para[0].start)}` {text}", ""]
    return "\n".join(lines)


def write_outputs(segments: list[Segment], json_path: Path, md_path: Path, title: str) -> None:
    json_path.write_text(
        json.dumps([asdict(s) for s in segments], ensure_ascii=False, indent=1), encoding="utf-8",
    )
    md_path.write_text(render_markdown(segments, title), encoding="utf-8")
