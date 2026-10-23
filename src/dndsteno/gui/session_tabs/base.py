from __future__ import annotations

from PySide6.QtWidgets import QWidget

from dndsteno.gui.context import AppContext
from dndsteno.session import Session


class SessionTab(QWidget):
    """A tab in the session view. Subclass, set `title`, register in session_tabs/__init__.py."""

    title: str = ""

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.session: Session | None = None

    def load(self, session: Session | None) -> None:
        """Show `session` (None = nothing selected). Called on selection and whenever it changes on disk."""
        self.session = session
