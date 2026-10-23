"""Loading dndsteno.toml into typed settings with defaults."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_CONFIG_NAME = "dndsteno.toml"


@dataclass
class RecordConfig:
    device: str = ""
    samplerate: int = 16000
    chunk_minutes: float = 20


@dataclass
class TranscribeConfig:
    model: str = "large-v3-turbo"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "ru"
    beam_size: int = 5
    vad: bool = True
    glossary: list[str] = field(default_factory=list)
    glossary_file: str = ""

    def load_glossary(self, base_dir: Path) -> list[str]:
        """Glossary from the config list plus the optional file, deduplicated in order."""
        words = list(self.glossary)
        if self.glossary_file:
            path = Path(self.glossary_file)
            if not path.is_absolute():
                path = base_dir / path
            words += [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
        return list(dict.fromkeys(words))


@dataclass
class Config:
    sessions_dir: Path = Path("sessions")
    record: RecordConfig = field(default_factory=RecordConfig)
    transcribe: TranscribeConfig = field(default_factory=TranscribeConfig)
    # Directory the config was loaded from; relative paths resolve against it.
    base_dir: Path = Path(".")


def _build(cls, data: dict, section: str):
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Неизвестные параметры в [{section}]: {', '.join(sorted(unknown))}")
    return cls(**data)


def load_config(path: Path | None = None) -> Config:
    """Load config from `path`, or ./dndsteno.toml if present, else defaults."""
    if path is None:
        candidate = Path(DEFAULT_CONFIG_NAME)
        if not candidate.exists():
            return Config(base_dir=Path.cwd())
        path = candidate

    with open(path, "rb") as f:
        data = tomllib.load(f)

    base_dir = path.resolve().parent
    sessions_dir = Path(data.pop("sessions_dir", "sessions"))
    if not sessions_dir.is_absolute():
        sessions_dir = base_dir / sessions_dir

    record = _build(RecordConfig, data.pop("record", {}), "record")
    transcribe = _build(TranscribeConfig, data.pop("transcribe", {}), "transcribe")
    if data:
        raise ValueError(f"Неизвестные параметры в конфиге: {', '.join(sorted(data))}")

    return Config(sessions_dir=sessions_dir, record=record, transcribe=transcribe, base_dir=base_dir)
