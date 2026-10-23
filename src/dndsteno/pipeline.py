"""High-level operations shared by the CLI and the GUI.

Front ends call these and only render progress; no front end should
reimplement a step on its own.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from dndsteno.config import Config
from dndsteno.session import Session
from dndsteno.transcriber import ProgressCallback, Transcriber, write_outputs


@dataclass
class TranscribeJob:
    """What to transcribe and where the results go."""
    audio_files: list[Path]
    cache_dir: Path
    json_out: Path
    md_out: Path
    title: str

    @classmethod
    def for_session(cls, session: Session) -> TranscribeJob:
        files = session.audio_files()
        if not files:
            raise FileNotFoundError(f"В {session.audio_dir} нет аудиофайлов.")
        return cls(files, session.parts_dir, session.transcript_json, session.transcript_md,
                   f"Сессия {session.name}")

    @classmethod
    def for_file(cls, path: Path) -> TranscribeJob:
        return cls([path], path.parent / f"{path.stem}.parts",
                   path.with_suffix(".json"), path.with_suffix(".md"), path.stem)


def transcribe(
    cfg: Config,
    job: TranscribeJob,
    force: bool = False,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Run speech-to-text for `job`; returns the markdown path. Raises Cancelled if `cancel` is set."""
    glossary = cfg.transcribe.load_glossary(cfg.base_dir)
    transcriber = Transcriber(cfg.transcribe, glossary, cancel=cancel)
    segments = transcriber.transcribe_parts(job.audio_files, job.cache_dir, force=force,
                                            on_progress=on_progress)
    write_outputs(segments, job.json_out, job.md_out, job.title)
    return job.md_out
