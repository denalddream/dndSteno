"""High-level operations shared by the CLI and the GUI.

Front ends call these and only render progress; no front end should
reimplement a step on its own.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from dndsteno.config import Config
from dndsteno.llm import LLMClient
from dndsteno.obsidian import Change, apply_changes, plan_changes
from dndsteno.session import Session
from dndsteno.summarize import ProgressCallback as SummaryProgressCallback
from dndsteno.summarize import load_summary, summarize_session
from dndsteno.transcriber import ProgressCallback, Transcriber, write_outputs
from dndsteno.vault import VaultIndex


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


def vault_index(cfg: Config) -> VaultIndex | None:
    """Index of the campaign notes, excluding our own session reports; None if no vault is set."""
    campaign = cfg.campaign_path
    if campaign is None or not campaign.is_dir():
        return None
    return VaultIndex(campaign, exclude=[campaign / cfg.obsidian.sessions_dir])


def summarize(
    cfg: Config,
    session: Session,
    force: bool = False,
    on_progress: SummaryProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> dict:
    """Session summary + facts per note into summary.json. Needs a transcript and a running LLM server."""
    if not session.transcript_json.exists():
        raise FileNotFoundError("Сначала расшифруйте сессию.")
    client = LLMClient(cfg.llm, cancel=cancel)
    return summarize_session(cfg, session, client, vault_index(cfg), force=force, on_progress=on_progress)


def plan_obsidian(cfg: Config, session: Session) -> list[Change]:
    summary = load_summary(session)
    if summary is None:
        raise FileNotFoundError("Сначала сделайте саммари.")
    index = vault_index(cfg)
    if index is None:
        raise FileNotFoundError("Не найдена папка кампании в vault (Настройки → Obsidian).")
    return plan_changes(cfg, session, summary, index)


def apply_obsidian(cfg: Config, session: Session, changes: list[Change]) -> list[tuple[Change, str]]:
    """Write the chosen changes into the vault (append-only; see obsidian.py)."""
    return apply_changes(cfg, session, [c for c in changes if c.status != "done"])
