"""GUI smoke tests: run headless, check that pages build and jobs deliver results."""

import os
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from dndsteno.config import Config, load_config  # noqa: E402
from dndsteno.gui.context import AppContext  # noqa: E402
from dndsteno.gui.jobs import JobManager  # noqa: E402
from dndsteno.gui.main_window import MainWindow  # noqa: E402
from dndsteno.gui.pages import PAGES  # noqa: E402
from dndsteno.session import open_session  # noqa: E402
from dndsteno.transcriber import Cancelled  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_until(app, cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond():
        app.processEvents()
        if time.monotonic() > end:
            raise TimeoutError
        time.sleep(0.01)


def make_config(tmp_path: Path) -> Config:
    cfg = load_config(tmp_path / "dndsteno.toml")  # absent file -> defaults saved there
    s = open_session(cfg.sessions_dir, "2026-10-23")
    s.audio_dir.mkdir(parents=True)
    sf.write(s.audio_dir / "part_001.flac", np.zeros(16000, dtype=np.float32), 16000)
    s.transcript_md.write_text("# Сессия 2026-10-23\n\n`00:00:01` Бросьте инициативу.\n", encoding="utf-8")
    return cfg


def test_job_success_cancel_and_error(app):
    jobs = JobManager()
    results = {}

    jobs.start("ok", lambda job: (job.report(0.5, "half"), 42)[1],
               on_success=lambda r: results.setdefault("ok", r))

    def cancellable(job):
        while not job.cancel_event.is_set():
            time.sleep(0.01)
        raise Cancelled()

    slow = jobs.start("slow", cancellable, kind="t", meta={"session": "x"},
                      on_cancel=lambda: results.setdefault("cancelled", True))
    assert jobs.running("t", session="x") == [slow]
    assert jobs.running("t", session="y") == []
    slow.cancel()

    jobs.start("bad", lambda job: 1 / 0, on_error=lambda e: results.setdefault("error", e))

    wait_until(app, lambda: len(results) == 3 and not jobs.jobs)
    assert results["ok"] == 42
    assert results["cancelled"] is True
    assert "ZeroDivisionError" in results["error"]


def test_main_window_builds_all_pages(app, tmp_path):
    cfg = make_config(tmp_path)
    window = MainWindow(AppContext(cfg))
    assert [p.page_id for p in window.pages] == [cls.page_id for cls in PAGES]

    for i in range(len(window.pages)):
        window.sidebar.setCurrentRow(i)
        app.processEvents()

    sessions = window.page("sessions")
    window.navigate("sessions", {"session": "2026-10-23"})
    assert sessions.session.name == "2026-10-23"
    assert sessions.transcribe_btn.isEnabled()
    transcript_tab = sessions.session_tabs[0]
    assert "Бросьте инициативу" in transcript_tab.view.toPlainText()


def test_settings_page_saves_config(app, tmp_path):
    cfg = make_config(tmp_path)
    ctx = AppContext(cfg)
    window = MainWindow(ctx)
    settings = window.page("settings")

    settings.editors[("transcribe", "model")].set("small")
    settings.editors[("transcribe", "glossary")].set(["Гримтоф", "Эльдрин"])
    settings.editors[("record", "chunk_minutes")].set(15)
    settings.save()

    reloaded = load_config(cfg.path)
    assert reloaded.transcribe.model == "small"
    assert reloaded.transcribe.glossary == ["Гримтоф", "Эльдрин"]
    assert reloaded.record.chunk_minutes == 15
