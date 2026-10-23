from __future__ import annotations

from PySide6.QtWidgets import QWidget

from dndsteno.gui.context import AppContext


class Page(QWidget):
    """One screen in the sidebar. Subclass, set the class attributes, register in pages/__init__.py."""

    page_id: str = ""   # stable id used by ctx.navigate(page_id)
    title: str = ""     # sidebar label
    icon: str = ""      # emoji shown before the label

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx

    def open(self, **kwargs) -> None:
        """Called when navigated to via ctx.navigate(page_id, **kwargs)."""

    def on_shown(self) -> None:
        """Called every time the page becomes visible."""

    def can_close(self) -> bool:
        """Return False to veto closing the app (after asking the user)."""
        return True
