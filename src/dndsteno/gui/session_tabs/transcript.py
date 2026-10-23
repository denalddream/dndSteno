from __future__ import annotations

import os

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout

from dndsteno.gui.session_tabs.base import SessionTab


class TranscriptTab(SessionTab):
    title = "Расшифровка"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.placeholder = QLabel()
        self.placeholder.setProperty("muted", True)
        self.open_btn = QPushButton("Открыть в редакторе")
        self.open_btn.clicked.connect(lambda: os.startfile(self.session.transcript_md))

        bar = QHBoxLayout()
        bar.addWidget(self.placeholder, 1)
        bar.addWidget(self.open_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.view, 1)

    def load(self, session) -> None:
        super().load(session)
        exists = session is not None and session.transcript_md.exists()
        self.open_btn.setVisible(exists)
        self.view.setVisible(exists)
        if exists:
            self.placeholder.setText(str(session.transcript_md))
            self.view.setMarkdown(session.transcript_md.read_text(encoding="utf-8"))
        else:
            self.placeholder.setText("Расшифровки пока нет — нажмите «Расшифровать».")
            self.view.clear()
