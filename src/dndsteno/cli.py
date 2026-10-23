"""Command line: steno devices | record | transcribe."""

from __future__ import annotations

import argparse
import math
import sys
import threading
import time
from pathlib import Path

from dndsteno.config import Config, load_config
from dndsteno.session import AUDIO_EXTENSIONS, Session, latest_session, open_session


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


QUIET_DB = -30  # speech peaking below this is too quiet for reliable recognition


def _level_bar(peak: float, width: int = 20, floor_db: float = -60) -> str:
    """Peak meter on a dB scale: a linear bar shows nothing for a typical quiet mic."""
    db = 20 * math.log10(peak) if peak > 0 else floor_db
    filled = round(width * (max(db, floor_db) - floor_db) / -floor_db)
    return f"{'█' * filled}{'·' * (width - filled)} {max(db, floor_db):4.0f} дБ"


def cmd_devices(cfg: Config, args) -> int:
    import sounddevice as sd

    default_in = sd.default.device[0]
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] < 1:
            continue
        hostapi = sd.query_hostapis(dev["hostapi"])["name"]
        mark = "*" if i == default_in else " "
        name = " ".join(dev["name"].split())  # some drivers put newlines into names
        print(f"{mark} {i:3d}  {name}  [{hostapi}, {int(dev['default_samplerate'])} Гц]")
    print("\n* — устройство по умолчанию. В dndsteno.toml: [record] device = \"<номер или имя>\"")
    return 0


def cmd_record(cfg: Config, args) -> int:
    from dndsteno.recorder import RecordStatus, record

    session = open_session(cfg.sessions_dir, args.session)
    stop = threading.Event()

    def wait_for_enter():
        try:
            input()
        except EOFError:
            return
        stop.set()

    threading.Thread(target=wait_for_enter, daemon=True).start()

    print(f"Сессия: {session.root}")
    print("Запись идёт. Enter или Ctrl+C — остановить.")
    print(f"Скажите что-нибудь: если при речи уровень ниже {QUIET_DB} дБ — "
          "прибавьте громкость микрофона в настройках Windows.\n")

    def on_status(st: RecordStatus):
        name = st.current_file.name if st.current_file else "—"
        warn = f"  потери буфера: {st.overflows}" if st.overflows else ""
        print(f"\r  {_fmt_duration(st.seconds)}  {_level_bar(st.peak)}  {name}{warn}   ",
              end="", flush=True)

    result = record(session, cfg.record, stop, on_status)
    print(f"\n\nЗаписано {_fmt_duration(result.seconds)} ({result.samplerate} Гц):")
    for f in result.files:
        print(f"  {f}")
    if result.overflows:
        print(f"Внимание: {result.overflows} раз терялись кусочки звука (компьютер не успевал).")
    return 0


def _resolve_target(cfg: Config, target: str | None) -> tuple[list[Path], Path, Path, Path, str]:
    """(audio files, cache dir, json out, md out, title) for a session name/dir or a single audio file."""
    if target:
        path = Path(target)
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
            return ([path], path.parent / f"{path.stem}.parts",
                    path.with_suffix(".json"), path.with_suffix(".md"), path.stem)
        session = Session(path) if path.is_dir() else open_session(cfg.sessions_dir, target)
    else:
        session = latest_session(cfg.sessions_dir)
        if session is None:
            raise SystemExit(f"В {cfg.sessions_dir} нет сессий с аудио. Укажите сессию или файл.")

    files = session.audio_files()
    if not files:
        raise SystemExit(f"В {session.audio_dir} нет аудиофайлов.")
    return (files, session.parts_dir, session.transcript_json, session.transcript_md,
            f"Сессия {session.name}")


def cmd_transcribe(cfg: Config, args) -> int:
    from dndsteno.transcriber import Progress, Transcriber, write_outputs

    if args.model:
        cfg.transcribe.model = args.model
    files, cache_dir, json_out, md_out, title = _resolve_target(cfg, args.target)
    glossary = cfg.transcribe.load_glossary(cfg.base_dir)

    t = cfg.transcribe
    print(f"Модель: {t.model} ({t.device}, {t.compute_type}), язык: {t.language or 'авто'}")
    if glossary:
        print(f"Словарь имён и названий: {len(glossary)}")
    print(f"Файлов: {len(files)}\n")

    started = time.monotonic()
    current_part = 0

    def on_progress(p: Progress):
        nonlocal current_part
        if current_part and p.part_index != current_part:
            print()  # finish the previous part's progress line
        current_part = p.part_index
        if p.cached:
            print(f"  [{p.part_index}/{p.part_count}] {p.part_name}: уже расшифрован (кэш)", end="")
            return
        pct = 100 * p.position / p.duration if p.duration else 0
        elapsed = time.monotonic() - started
        print(f"\r  [{p.part_index}/{p.part_count}] {p.part_name}: {pct:5.1f}%  "
              f"({_fmt_duration(p.position)} из {_fmt_duration(p.duration)}, прошло {_fmt_duration(elapsed)})",
              end="", flush=True)

    print("Загрузка модели (первый раз она скачивается, это долго)...")
    transcriber = Transcriber(cfg.transcribe, glossary)
    segments = transcriber.transcribe_parts(files, cache_dir, force=args.force, on_progress=on_progress)
    print("\n")

    write_outputs(segments, json_out, md_out, title)
    print(f"Готово за {_fmt_duration(time.monotonic() - started)}:")
    print(f"  {md_out}")
    print(f"  {json_out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="steno", description="Запись и расшифровка D&D-партий")
    parser.add_argument("--config", type=Path, help="путь к dndsteno.toml (по умолчанию ./dndsteno.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("devices", help="список микрофонов").set_defaults(func=cmd_devices)

    p = sub.add_parser("record", help="записать партию")
    p.add_argument("--session", help="имя сессии (по умолчанию сегодняшняя дата); "
                                     "если уже есть — запись продолжится новым куском")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("transcribe", help="расшифровать запись в текст")
    p.add_argument("target", nargs="?", help="имя сессии, папка сессии или аудиофайл "
                                             "(по умолчанию последняя сессия)")
    p.add_argument("--model", help="переопределить модель Whisper из конфига")
    p.add_argument("--force", action="store_true", help="игнорировать кэш, расшифровать заново")
    p.set_defaults(func=cmd_transcribe)

    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        # Cyrillic and progress bars in the Windows console.
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    return args.func(cfg, args)
