from __future__ import annotations

import copy
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from dndsteno.fmt import fmt_duration
from dndsteno.gui.jobs import Job
from dndsteno.gui.pages.base import Page
from dndsteno.gui.session_tabs import SESSION_TABS
from dndsteno.pipeline import TranscribeJob, transcribe
from dndsteno.session import AUDIO_EXTENSIONS, Session, default_session_name, list_sessions, open_session
from dndsteno.transcriber import Progress

STATUS_LABELS = {
    "empty": ("○", "нет аудио"),
    "recorded": ("●", "записана"),
    "stale": ("◐", "есть новые куски"),
    "transcribed": ("✔", "расшифрована"),
}


class SessionsPage(Page):
    page_id = "sessions"
    title = "Сессии"
    icon = "📜"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.session: Session | None = None

        # --- left: session list ---
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._selected)
        refresh_btn = QPushButton("Обновить")
        refresh_btn.clicked.connect(self.refresh)
        import_btn = QPushButton("Импорт аудио…")
        import_btn.setToolTip("Добавить готовые записи (с телефона, из Discord/Craig) в сессию")
        import_btn.clicked.connect(self._import)
        left_buttons = QHBoxLayout()
        left_buttons.addWidget(refresh_btn)
        left_buttons.addWidget(import_btn)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addLayout(left_buttons)
        left_layout.addWidget(self.list, 1)

        # --- right: selected session ---
        self.name_label = QLabel()
        self.name_label.setProperty("heading", True)
        self.info_label = QLabel()
        self.info_label.setProperty("muted", True)

        self.transcribe_btn = QPushButton("Расшифровать")
        self.transcribe_btn.setProperty("role", "primary")
        self.transcribe_btn.clicked.connect(lambda: self.start_transcription())
        self.retranscribe_btn = QPushButton("Расшифровать заново")
        self.retranscribe_btn.setToolTip("Игнорировать сохранённые куски — например, после смены модели или словаря")
        self.retranscribe_btn.clicked.connect(lambda: self.start_transcription(force=True))
        folder_btn = QPushButton("Открыть папку")
        folder_btn.clicked.connect(lambda: self.session and os.startfile(self.session.root))
        actions = QHBoxLayout()
        actions.addWidget(self.transcribe_btn)
        actions.addWidget(self.retranscribe_btn)
        actions.addStretch()
        actions.addWidget(folder_btn)

        self.tabs = QTabWidget()
        self.session_tabs = [tab_cls(ctx) for tab_cls in SESSION_TABS]
        for tab in self.session_tabs:
            self.tabs.addTab(tab, tab.title)

        self.detail = QWidget()
        detail_layout = QVBoxLayout(self.detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(self.name_label)
        detail_layout.addWidget(self.info_label)
        detail_layout.addLayout(actions)
        detail_layout.addWidget(self.tabs, 1)

        self.empty_label = QLabel("Сессий пока нет. Запишите партию на странице «Запись» или импортируйте аудио.")
        self.empty_label.setProperty("muted", True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 0, 0, 0)
        right_layout.addWidget(self.detail, 1)
        right_layout.addWidget(self.empty_label, 1)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 900])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 16)
        layout.addWidget(splitter)

        ctx.session_changed.connect(self._on_session_changed)
        ctx.config_changed.connect(self.refresh)
        ctx.jobs.job_started.connect(lambda _: self._update_actions())
        ctx.jobs.job_finished.connect(lambda _: self._update_actions())
        self.refresh()

    # --- list ---

    def refresh(self) -> None:
        selected = self.session.name if self.session else None
        self.list.blockSignals(True)
        self.list.clear()
        for s in list_sessions(self.ctx.config.sessions_dir):
            icon, label = STATUS_LABELS[s.status]
            item = QListWidgetItem(f"{icon}  {s.name}\n     {fmt_duration(s.audio_duration())} · {label}")
            item.setData(Qt.ItemDataRole.UserRole, s.name)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._select(selected) or self.list.setCurrentRow(0)
        if self.list.count() == 0:
            self._show(None)

    def _select(self, name: str | None) -> bool:
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == name:
                self.list.setCurrentRow(i)
                self._show(open_session(self.ctx.config.sessions_dir, name))
                return True
        return False

    def _selected(self, item: QListWidgetItem | None) -> None:
        name = item.data(Qt.ItemDataRole.UserRole) if item else None
        self._show(open_session(self.ctx.config.sessions_dir, name) if name else None)

    def _show(self, session: Session | None) -> None:
        self.session = session
        self.detail.setVisible(session is not None)
        self.empty_label.setVisible(session is None)
        if session:
            parts = session.audio_files()
            self.name_label.setText(f"Сессия {session.name}")
            self.info_label.setText(
                f"{fmt_duration(session.audio_duration())} · кусков: {len(parts)} · "
                f"{STATUS_LABELS[session.status][1]}")
        for tab in self.session_tabs:
            tab.load(session)
        self._update_actions()

    def _on_session_changed(self, name: str) -> None:
        self.refresh()

    # --- actions ---

    def _update_actions(self) -> None:
        busy = bool(self.ctx.jobs.running("transcribe"))
        recording_here = bool(self.session and self.ctx.jobs.running("record", session=self.session.name))
        has_audio = bool(self.session and self.session.audio_files())
        enabled = has_audio and not busy and not recording_here
        self.transcribe_btn.setEnabled(enabled)
        self.retranscribe_btn.setEnabled(enabled and self.session.parts_dir.exists())
        if busy:
            self.transcribe_btn.setToolTip("Уже идёт расшифровка — дождитесь окончания")
        elif recording_here:
            self.transcribe_btn.setToolTip("Сначала остановите запись этой сессии")
        else:
            self.transcribe_btn.setToolTip("")

    def start_transcription(self, force: bool = False) -> None:
        session = self.session
        if session is None or not self.transcribe_btn.isEnabled():
            return
        cfg = copy.deepcopy(self.ctx.config)
        tjob = TranscribeJob.for_session(session)

        def work(job: Job):
            job.report(-1, "Загрузка модели…")

            def on_progress(p: Progress):
                part = p.position / p.duration if p.duration else 1
                overall = (p.part_index - 1 + part) / p.part_count
                job.report(overall, f"кусок {p.part_index}/{p.part_count} · {overall:.0%}")

            return transcribe(cfg, tjob, force=force, on_progress=on_progress, cancel=job.cancel_event)

        def done(_):
            self.ctx.session_changed.emit(session.name)

        def failed(error: str):
            QMessageBox.warning(self, "Ошибка расшифровки", error)

        self.ctx.jobs.start(f"Расшифровка: {session.name}", work, kind="transcribe",
                            meta={"session": session.name}, on_success=done, on_error=failed,
                            on_cancel=lambda: self.ctx.session_changed.emit(session.name))

    def _import(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in sorted(AUDIO_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(self, "Выберите аудиофайлы", "", f"Аудио ({patterns})")
        if not files:
            return
        name, ok = QInputDialog.getText(
            self, "Импорт", "В какую сессию добавить (файлы станут её следующими кусками)?",
            text=self.session.name if self.session else default_session_name())
        if not ok or not name.strip():
            return
        session = open_session(self.ctx.config.sessions_dir, name.strip())
        session.import_audio(sorted(Path(f) for f in files))
        self.ctx.session_changed.emit(session.name)
        self._select(session.name)

    # --- Page API ---

    def open(self, session: str | None = None, transcribe: bool = False, **_) -> None:
        self.refresh()
        if session:
            self._select(session)
        if transcribe:
            self.start_transcription()

    def on_shown(self) -> None:
        self.refresh()
