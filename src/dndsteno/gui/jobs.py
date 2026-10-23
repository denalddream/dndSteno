"""Background jobs: long work off the UI thread, with progress and cancellation.

A job is a plain function `fn(job) -> result` running in a worker thread. It
reports through `job.report(...)` / `job.emit_data(...)` and checks
`job.cancel_event`. Qt delivers the signals to widgets on the UI thread.

    def work(job):
        for i in range(10):
            if job.cancel_event.is_set():
                raise Cancelled()
            job.report(i / 10, f"шаг {i}")
        return "готово"

    ctx.jobs.start("Пример", work, on_success=lambda r: ...)
"""

from __future__ import annotations

import logging
import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from dndsteno.errors import Cancelled

log = logging.getLogger(__name__)


class Job(QObject):
    # fraction in 0..1, or -1 when unknown; human-readable message
    progress = Signal(float, str)
    # arbitrary payload for job-specific widgets (e.g. recorder level updates)
    data = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    finished = Signal()  # always emitted last, after one of the three above

    def __init__(self, title: str, fn: Callable[[Job], Any], kind: str = "", cancel_label: str = "Отменить",
                 meta: dict | None = None):
        super().__init__()
        self.title = title
        self.kind = kind  # lets pages find their own jobs, e.g. "record", "transcribe"
        self.cancel_label = cancel_label
        self.meta = meta or {}  # free-form tags, e.g. {"session": "2026-10-23"}
        self.fn = fn
        self.cancel_event = threading.Event()
        self.last_fraction = -1.0
        self.last_message = ""
        self._thread: threading.Thread | None = None

    # --- called from the worker thread ---

    def report(self, fraction: float, message: str = "") -> None:
        self.last_fraction, self.last_message = fraction, message
        self.progress.emit(fraction, message)

    def emit_data(self, payload: Any) -> None:
        self.data.emit(payload)

    # --- called from the UI thread ---

    def cancel(self) -> None:
        self.cancel_event.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait(self, timeout: float | None = None) -> bool:
        if self._thread:
            self._thread.join(timeout)
        return not self.running

    def _run(self) -> None:
        try:
            result = self.fn(self)
        except Cancelled:
            self.cancelled.emit()
        except Exception as e:
            log.error("Job %r failed:\n%s", self.title, traceback.format_exc())
            self.failed.emit(f"{type(e).__name__}: {e}")
        else:
            # A job may stop early on cancel without raising; that is still a success.
            self.succeeded.emit(result)
        finally:
            self.finished.emit()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"job:{self.title}", daemon=True)
        self._thread.start()


class JobManager(QObject):
    job_started = Signal(object)   # Job
    job_finished = Signal(object)  # Job

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.jobs: list[Job] = []

    def start(
        self,
        title: str,
        fn: Callable[[Job], Any],
        kind: str = "",
        cancel_label: str = "Отменить",
        meta: dict | None = None,
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
    ) -> Job:
        job = Job(title, fn, kind=kind, cancel_label=cancel_label, meta=meta)
        if on_success:
            job.succeeded.connect(on_success)
        if on_error:
            job.failed.connect(on_error)
        if on_cancel:
            job.cancelled.connect(on_cancel)
        job.finished.connect(lambda: self._on_finished(job))
        self.jobs.append(job)
        self.job_started.emit(job)
        job.start()
        return job

    def _on_finished(self, job: Job) -> None:
        if job in self.jobs:
            self.jobs.remove(job)
        self.job_finished.emit(job)

    def running(self, kind: str | None = None, **meta) -> list[Job]:
        """Running jobs, optionally filtered by kind and meta values: running("record", session="x")."""
        return [
            j for j in self.jobs
            if (kind is None or j.kind == kind) and all(j.meta.get(k) == v for k, v in meta.items())
        ]
