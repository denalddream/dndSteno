"""Shared application state passed to every page.

Pages never talk to each other directly: they read `ctx.config`, start work
through `ctx.jobs`, and communicate through the signals below.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from dndsteno.config import Config, save_config
from dndsteno.gui.jobs import JobManager


class AppContext(QObject):
    config_changed = Signal()
    # Something on disk changed inside a session (new audio, new transcript, ...); arg: session name
    session_changed = Signal(str)
    # Ask the main window to show a page: (page_id, kwargs for Page.open)
    navigate_requested = Signal(str, dict)

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.jobs = JobManager(self)

    def save_config(self) -> None:
        save_config(self.config)
        self.config_changed.emit()

    def navigate(self, page_id: str, **kwargs) -> None:
        self.navigate_requested.emit(page_id, kwargs)
