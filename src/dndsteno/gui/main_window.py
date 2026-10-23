from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QStackedWidget,
    QVBoxLayout, QWidget,
)

from dndsteno.gui.context import AppContext
from dndsteno.gui.pages import PAGES
from dndsteno.gui.pages.base import Page
from dndsteno.gui.widgets.jobs_bar import JobsBar


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("dndSteno")
        self.resize(1200, 780)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(200)
        self.stack = QStackedWidget()
        self.pages: list[Page] = []

        for page_cls in PAGES:
            page = page_cls(ctx)
            self.pages.append(page)
            self.stack.addWidget(page)
            item = QListWidgetItem(f"{page.icon}  {page.title}")
            item.setData(Qt.ItemDataRole.UserRole, page.page_id)
            self.sidebar.addItem(item)

        self.sidebar.currentRowChanged.connect(self._show_index)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.stack, 1)
        content_layout.addWidget(JobsBar(ctx.jobs))

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.sidebar)
        layout.addWidget(content, 1)
        self.setCentralWidget(root)

        ctx.navigate_requested.connect(self.navigate)
        self.sidebar.setCurrentRow(0)

    def page(self, page_id: str) -> Page:
        return next(p for p in self.pages if p.page_id == page_id)

    def _show_index(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.pages[index].on_shown()

    def navigate(self, page_id: str, kwargs: dict) -> None:
        page = self.page(page_id)
        self.sidebar.setCurrentRow(self.pages.index(page))
        page.open(**kwargs)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not all(p.can_close() for p in self.pages):
            event.ignore()
            return
        running = self.ctx.jobs.running()
        if running:
            names = "\n".join(f"• {j.title}" for j in running)
            answer = QMessageBox.question(
                self, "Идут задачи",
                f"Сейчас выполняется:\n{names}\n\nОстановить и выйти? "
                "Запись будет сохранена, расшифровка продолжится со следующего куска при новом запуске.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            for job in running:
                job.cancel()
            for job in running:
                job.wait(timeout=10)
        event.accept()
