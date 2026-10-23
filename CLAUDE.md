# dndSteno — notes for AI assistants

Records D&D sessions, transcribes them (faster-whisper), and — planned — summarizes them
and writes Obsidian notes. Windows, Python 3.12, `.venv` in the repo. UI text is Russian.

## Commands

- Tests: `.venv\Scripts\python -m pytest -q` (GUI tests run headless, `QT_QPA_PLATFORM=offscreen`)
- GUI: `dndSteno.cmd` or `.venv\Scripts\steno gui`; CLI: `.venv\Scripts\steno --help`

## Architecture — keep these boundaries

```
src/dndsteno/
  config.py       settings dataclasses; field metadata (label/help/choices/kind) drives the settings page
  session.py      session folder layout (audio/, transcript/, transcript.md, ...)
  recorder.py     mic -> chunked FLAC on disk
  transcriber.py  faster-whisper, per-part JSON cache, Cancelled exception
  llm.py          OpenAI-compatible client (LM Studio), streaming, JSON-schema replies
  vault.py        read-only index of campaign notes; name resolution (cases, ё, aliases)
  summarize.py    transcript -> summary.json (map per chunk, reduce, dedupe facts vs notes)
  obsidian.py     summary -> Change list; APPEND-ONLY writer into the vault
  pipeline.py     high-level operations both front ends call (transcribe, summarize, plan/apply_obsidian)
  cli.py          argparse front end
  gui/            PySide6 front end — presentation only
    context.py    AppContext: config, jobs, signals (config_changed, session_changed, navigate)
    jobs.py       background jobs with progress/cancel; never block the UI thread
    pages/        sidebar pages; registry in pages/__init__.py
    session_tabs/ tabs of the session view; registry in session_tabs/__init__.py
    widgets/      reusable widgets; fields.py = settings editors
    theme.py      colors + QSS; style via properties (role=primary|danger, muted, heading)
```

Rules:
1. **Core never imports Qt.** Logic goes in the core modules (or a new one), is tested
   without a GUI, and both `cli.py` and `gui/` call it. Long operations accept
   `on_progress` callbacks and a `cancel: threading.Event`, and raise `Cancelled`.
2. **Every pipeline step reads and writes files in the session folder**, so steps can
   be rerun independently. New outputs get a property on `Session`.
3. **Anything slower than ~100 ms runs as a job**: `ctx.jobs.start(title, fn, kind=..., meta={"session": name})`.
   Copy config (`copy.deepcopy`) before handing it to a worker thread.
4. **The Obsidian vault is the user's handwritten work. Never delete or rewrite it.**
   Only `obsidian.py` writes there: append a marked `> [!ai]` callout with the
   `%% dndsteno:session=… %%` marker, create files exclusively, back up before changing,
   verify the old bytes are a prefix of the new. The LLM returns JSON only and never
   touches files. Changes go through the preview in the GUI. Tests in tests/test_obsidian.py
   guard this — keep them passing, and test on a copy of the vault, never the real one.
5. **Pages don't call each other.** Use `ctx.navigate(page_id, **kwargs)` and
   `ctx.session_changed.emit(name)` after writing into a session.

## How to add things

- **Setting**: add a field with `opt(default, "Label", "help", choices=..., kind=...)` to a
  dataclass in `config.py`. The settings page and TOML save/load pick it up. A new
  section: dataclass + entry in `SECTIONS` + attribute on `Config`.
- **Session tab** (e.g. summary, Obsidian export): subclass `SessionTab` in
  `gui/session_tabs/`, implement `load(session)`, append to `SESSION_TABS`.
- **Page**: subclass `Page` in `gui/pages/` (`page_id`, `title`, `icon`), append to `PAGES`.
- **Pipeline step**: core function in its own module + wrapper in `pipeline.py` +
  CLI subcommand + button/tab in the GUI that runs it as a job. Add tests.

## Gotchas

- `av<19` is pinned: av 19 broke `faster_whisper.decode_audio`.
- No NVIDIA GPU on the dev machine: Whisper runs on CPU int8 (~0.3× realtime for large-v3-turbo).
- Writing Cyrillic through a shell heredoc into Python on Windows can corrupt text — edit files directly.
- Recordings, `dndsteno.toml`, `dndsteno.log` and `sessions/` are git-ignored; never commit audio.
