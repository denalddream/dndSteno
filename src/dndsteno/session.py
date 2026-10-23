"""Session folder layout.

sessions/<name>/
    audio/part_001.flac, part_002.flac, ...
    transcript/part_001.json, ...      per-part cache, lets a long run resume
    transcript.json                    merged segments with absolute timestamps
    transcript.md                      readable text
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

AUDIO_EXTENSIONS = {".flac", ".wav", ".mp3", ".ogg", ".m4a", ".opus", ".webm"}
_PART_RE = re.compile(r"^part_(\d+)$")


@dataclass(frozen=True)
class Session:
    root: Path

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    @property
    def parts_dir(self) -> Path:
        return self.root / "transcript"

    @property
    def transcript_json(self) -> Path:
        return self.root / "transcript.json"

    @property
    def transcript_md(self) -> Path:
        return self.root / "transcript.md"

    def audio_files(self) -> list[Path]:
        """Audio files in recording order (sorted by name: part_001, part_002, ...)."""
        if not self.audio_dir.exists():
            return []
        return sorted(
            p for p in self.audio_dir.iterdir()
            if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
        )

    def audio_duration(self) -> float:
        """Total length of all parts in seconds (formats soundfile can't read count as 0)."""
        import soundfile as sf

        total = 0.0
        for f in self.audio_files():
            try:
                total += sf.info(str(f)).duration
            except RuntimeError:
                pass
        return total

    @property
    def status(self) -> str:
        """'empty' (no audio), 'recorded', 'transcribed', or 'stale' (audio added after transcription)."""
        audio = self.audio_files()
        if not audio:
            return "empty"
        if not self.transcript_md.exists():
            return "recorded"
        newest_audio = max(f.stat().st_mtime for f in audio)
        return "stale" if newest_audio > self.transcript_md.stat().st_mtime else "transcribed"

    def import_audio(self, paths: list[Path]) -> list[Path]:
        """Copy external audio files in as the next parts, keeping their format."""
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        added = []
        for src in paths:
            dst = self.next_part_path(src.suffix.lower())
            shutil.copy2(src, dst)
            added.append(dst)
        return added

    def next_part_path(self, ext: str = ".flac") -> Path:
        """Path for the next recording part; continues numbering if the session already has parts."""
        numbers = [
            int(m.group(1))
            for p in self.audio_files()
            if (m := _PART_RE.match(p.stem))
        ]
        return self.audio_dir / f"part_{max(numbers, default=0) + 1:03d}{ext}"


def default_session_name() -> str:
    return date.today().isoformat()


def open_session(sessions_dir: Path, name: str | None = None) -> Session:
    return Session(sessions_dir / (name or default_session_name()))


def list_sessions(sessions_dir: Path) -> list[Session]:
    """All session folders, newest name first (names default to ISO dates)."""
    if not sessions_dir.exists():
        return []
    return sorted((Session(p) for p in sessions_dir.iterdir() if p.is_dir()),
                  key=lambda s: s.name, reverse=True)


def latest_session(sessions_dir: Path) -> Session | None:
    """Most recently modified session that has audio."""
    if not sessions_dir.exists():
        return None
    candidates = [Session(p) for p in sessions_dir.iterdir() if p.is_dir()]
    candidates = [s for s in candidates if s.audio_files()]
    if not candidates:
        return None
    return max(candidates, key=lambda s: max(f.stat().st_mtime for f in s.audio_files()))
