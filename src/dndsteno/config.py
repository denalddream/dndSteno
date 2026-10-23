"""Loading and saving dndsteno.toml as typed settings with defaults.

Field metadata drives the settings page in the GUI: a new option needs only a
field here with `label` (and optionally `help`, `choices`, `kind`), no GUI code.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

import tomli_w

DEFAULT_CONFIG_NAME = "dndsteno.toml"
CONFIG_ENV = "DNDSTENO_CONFIG"


def opt(default, label: str, help: str = "", choices: list | None = None, kind: str = ""):
    """Dataclass field with GUI metadata. `kind` refines the editor: dir, file, audio_input."""
    meta = {"label": label, "help": help, "choices": choices, "kind": kind}
    if isinstance(default, list):
        return field(default_factory=lambda: list(default), metadata=meta)
    return field(default=default, metadata=meta)


@dataclass
class RecordConfig:
    device: str = opt("", "Микрофон", "Имя или номер устройства. Пусто — системный по умолчанию.",
                      kind="audio_input")
    samplerate: int = opt(16000, "Частота, Гц",
                          "16000 — то, что нужно Whisper. Если устройство не умеет, будет его родная.")
    chunk_minutes: float = opt(20, "Длина куска, мин",
                               "Запись делится на файлы такой длины. При сбое теряется максимум один кусок.")


@dataclass
class TranscribeConfig:
    model: str = opt("large-v3-turbo", "Модель Whisper",
                     "Больше — точнее и медленнее. large-v3-turbo — лучший баланс.",
                     choices=["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"])
    device: str = opt("cpu", "Устройство", "cuda — только для видеокарт NVIDIA.",
                      choices=["cpu", "cuda", "auto"])
    compute_type: str = opt("int8", "Точность вычислений", "int8 — быстро на CPU; float16 — для NVIDIA.",
                            choices=["int8", "int8_float16", "float16", "float32"])
    language: str = opt("ru", "Язык", "Код языка (ru, en). Пусто — определять автоматически.")
    beam_size: int = opt(5, "Beam size", "Больше — чуть точнее и медленнее.")
    vad: bool = opt(True, "Вырезать тишину (VAD)", "Меньше галлюцинаций и быстрее.")
    glossary: list[str] = opt([], "Словарь имён",
                              "Персонажи, NPC, локации — по одному на строку. Whisper будет писать их правильно.")
    glossary_file: str = opt("", "Файл словаря", "Текстовый файл: по одному имени на строку.", kind="file")

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
class GeneralConfig:
    sessions_dir: str = opt("sessions", "Папка сессий", "Здесь хранятся записи и расшифровки.", kind="dir")


# Section name in the TOML file -> dataclass. Order is the order on the settings page.
SECTIONS: dict[str, tuple[str, type]] = {
    "general": ("Общие", GeneralConfig),
    "record": ("Запись", RecordConfig),
    "transcribe": ("Расшифровка", TranscribeConfig),
}


@dataclass
class Config:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    record: RecordConfig = field(default_factory=RecordConfig)
    transcribe: TranscribeConfig = field(default_factory=TranscribeConfig)
    # Where the config lives (or will be saved); relative paths resolve against its folder.
    path: Path = Path(DEFAULT_CONFIG_NAME)

    @property
    def base_dir(self) -> Path:
        return self.path.resolve().parent

    @property
    def sessions_dir(self) -> Path:
        path = Path(self.general.sessions_dir)
        return path if path.is_absolute() else self.base_dir / path


def _build(cls, data: dict, section: str):
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Неизвестные параметры в [{section}]: {', '.join(sorted(unknown))}")
    return cls(**data)


def default_config_path() -> Path:
    return Path(os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG_NAME)


def load_config(path: Path | None = None) -> Config:
    """Load config from `path`, $DNDSTENO_CONFIG or ./dndsteno.toml; defaults if the file is absent."""
    path = Path(path) if path else default_config_path()
    if not path.exists():
        return Config(path=path.absolute())

    with open(path, "rb") as f:
        data = tomllib.load(f)

    # Older configs had sessions_dir at the top level.
    if "sessions_dir" in data:
        data.setdefault("general", {})["sessions_dir"] = data.pop("sessions_dir")

    sections = {
        key: _build(cls, data.pop(key, {}), key) for key, (_, cls) in SECTIONS.items()
    }
    if data:
        raise ValueError(f"Неизвестные параметры в конфиге: {', '.join(sorted(data))}")
    return Config(**sections, path=path.absolute())


def save_config(cfg: Config) -> None:
    data = {}
    for key in SECTIONS:
        section = getattr(cfg, key)
        data[key] = {f.name: getattr(section, f.name) for f in fields(section)}
    cfg.path.parent.mkdir(parents=True, exist_ok=True)
    cfg.path.write_text(tomli_w.dumps(data), encoding="utf-8")
