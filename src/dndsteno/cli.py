"""Command line: steno devices | record | transcribe | gui."""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

from dndsteno.config import Config, load_config
from dndsteno.fmt import FLOOR_DB, QUIET_DB, fmt_duration, peak_db
from dndsteno.session import AUDIO_EXTENSIONS, Session, latest_session, open_session


def _level_bar(peak: float, width: int = 20) -> str:
    """Peak meter on a dB scale: a linear bar shows nothing for a typical quiet mic."""
    db = peak_db(peak)
    filled = round(width * (db - FLOOR_DB) / -FLOOR_DB)
    return f"{'█' * filled}{'·' * (width - filled)} {db:4.0f} дБ"


def cmd_devices(cfg: Config, args) -> int:
    from dndsteno.recorder import list_input_devices

    for d in list_input_devices():
        mark = "*" if d.is_default else " "
        print(f"{mark} {d.index:3d}  {d.name}  [{d.hostapi}, {d.samplerate} Гц]")
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
    print(f"Скажите что-нибудь: если при речи уровень ниже {QUIET_DB:.0f} дБ — "
          "прибавьте громкость микрофона в настройках Windows.\n")

    def on_status(st: RecordStatus):
        name = st.current_file.name if st.current_file else "—"
        warn = f"  потери буфера: {st.overflows}" if st.overflows else ""
        print(f"\r  {fmt_duration(st.seconds)}  {_level_bar(st.peak)}  {name}{warn}   ",
              end="", flush=True)

    result = record(session, cfg.record, stop, on_status)
    print(f"\n\nЗаписано {fmt_duration(result.seconds)} ({result.samplerate} Гц):")
    for f in result.files:
        print(f"  {f}")
    if result.overflows:
        print(f"Внимание: {result.overflows} раз терялись кусочки звука (компьютер не успевал).")
    return 0


def _resolve_target(cfg: Config, target: str | None):
    from dndsteno.pipeline import TranscribeJob

    if target:
        path = Path(target)
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
            return TranscribeJob.for_file(path)
        session = Session(path) if path.is_dir() else open_session(cfg.sessions_dir, target)
    else:
        session = latest_session(cfg.sessions_dir)
        if session is None:
            raise SystemExit(f"В {cfg.sessions_dir} нет сессий с аудио. Укажите сессию или файл.")
    try:
        return TranscribeJob.for_session(session)
    except FileNotFoundError as e:
        raise SystemExit(str(e))


def cmd_transcribe(cfg: Config, args) -> int:
    from dndsteno.pipeline import transcribe
    from dndsteno.transcriber import Progress

    if args.model:
        cfg.transcribe.model = args.model
    job = _resolve_target(cfg, args.target)
    glossary = cfg.transcribe.load_glossary(cfg.base_dir)

    t = cfg.transcribe
    print(f"Модель: {t.model} ({t.device}, {t.compute_type}), язык: {t.language or 'авто'}")
    if glossary:
        print(f"Словарь имён и названий: {len(glossary)}")
    print(f"Файлов: {len(job.audio_files)}\n")

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
              f"({fmt_duration(p.position)} из {fmt_duration(p.duration)}, прошло {fmt_duration(elapsed)})",
              end="", flush=True)

    print("Загрузка модели (первый раз она скачивается, это долго)...")
    md = transcribe(cfg, job, force=args.force, on_progress=on_progress)
    print("\n")
    print(f"Готово за {fmt_duration(time.monotonic() - started)}:")
    print(f"  {md}")
    print(f"  {job.json_out}")
    return 0


def _session_arg(cfg: Config, name: str | None) -> Session:
    session = open_session(cfg.sessions_dir, name) if name else latest_session(cfg.sessions_dir)
    if session is None or not session.root.exists():
        raise SystemExit("Сессия не найдена.")
    return session


def cmd_summarize(cfg: Config, args) -> int:
    from dndsteno.pipeline import summarize
    from dndsteno.summarize import SummaryProgress

    session = _session_arg(cfg, args.session)
    names = {"map": "кусок", "reduce": "итог", "dedupe": "сверка статей"}

    def on_progress(p: SummaryProgress):
        tail = " (кэш)" if p.cached else f" · {p.chars} симв." if p.chars else ""
        print(f"\r  {names[p.stage]} {p.index}/{p.count}{tail}          ", end="", flush=True)

    print(f"Саммари сессии {session.name} через {cfg.llm.base_url}")
    summary = summarize(cfg, session, force=args.force, on_progress=on_progress)
    print(f"\n\n{summary.get('title', '')}")
    for t in summary.get("tldr", []):
        print(f"  - {t}")
    print(f"\nСтатей с новыми фактами: {len(summary.get('entities', []))}. Записать: steno obsidian {session.name}")
    return 0


def cmd_obsidian(cfg: Config, args) -> int:
    from dndsteno.pipeline import apply_obsidian, plan_obsidian

    session = _session_arg(cfg, args.session)
    changes = plan_obsidian(cfg, session)
    for ch in changes:
        mark = "✔" if ch.status == "done" else "+" if ch.action == "append" else "new"
        print(f"  [{mark}] {ch.title}  →  {ch.rel_path}")
    pending = [c for c in changes if c.status != "done"]
    if not args.apply:
        print(f"\nК записи: {len(pending)}. Записать: steno obsidian {session.name} --apply")
        return 0
    for ch, result in apply_obsidian(cfg, session, pending):
        print(f"  {result}: {ch.rel_path}")
    return 0


def cmd_gui(cfg: Config, args) -> int:
    from dndsteno.gui.app import run

    return run(cfg)


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

    p = sub.add_parser("summarize", help="саммари сессии через нейросеть (LM Studio)")
    p.add_argument("session", nargs="?", help="имя сессии (по умолчанию последняя)")
    p.add_argument("--force", action="store_true", help="не использовать сохранённые результаты")
    p.set_defaults(func=cmd_summarize)

    p = sub.add_parser("obsidian", help="показать (и записать) изменения для Obsidian")
    p.add_argument("session", nargs="?", help="имя сессии (по умолчанию последняя)")
    p.add_argument("--apply", action="store_true", help="записать все изменения в vault")
    p.set_defaults(func=cmd_obsidian)

    sub.add_parser("gui", help="открыть окно программы").set_defaults(func=cmd_gui)

    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32" and sys.stdout:
        # Cyrillic and progress bars in the Windows console.
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    return args.func(cfg, args)
