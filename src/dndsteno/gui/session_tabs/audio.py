from __future__ import annotations

import os

import soundfile as sf
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout

from dndsteno.fmt import fmt_duration
from dndsteno.gui.session_tabs.base import SessionTab


class AudioTab(SessionTab):
    title = "Аудио"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Файл", "Длительность", "Частота", "Размер"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.cellDoubleClicked.connect(self._play)
        self.table.setToolTip("Двойной щелчок — открыть в плеере")
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)

    def load(self, session) -> None:
        super().load(session)
        files = session.audio_files() if session else []
        self.table.setRowCount(len(files))
        for row, f in enumerate(files):
            try:
                info = sf.info(str(f))
                duration, rate = fmt_duration(info.duration), f"{info.samplerate} Гц"
            except RuntimeError:
                duration, rate = "?", "?"
            for col, text in enumerate([f.name, duration, rate, f"{f.stat().st_size / 2**20:.1f} МБ"]):
                self.table.setItem(row, col, QTableWidgetItem(text))

    def _play(self, row: int, col: int) -> None:
        os.startfile(self.session.audio_files()[row])
