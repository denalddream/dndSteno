from __future__ import annotations

import copy
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from dndsteno.fmt import QUIET_DB, fmt_duration, peak_db
from dndsteno.gui.jobs import Job
from dndsteno.gui.pages.base import Page
from dndsteno.gui.widgets.level_meter import LevelMeter
from dndsteno.recorder import RecordResult, RecordStatus, list_input_devices, record
from dndsteno.session import default_session_name, open_session

# Warn if speech never got louder than QUIET_DB over this window.
QUIET_WINDOW = 8.0


class RecordPage(Page):
    page_id = "record"
    title = "Запись"
    icon = "🎙"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.job: Job | None = None
        self._loud_at = 0.0

        heading = QLabel("Запись партии")
        heading.setProperty("heading", True)

        self.session_edit = QLineEdit()
        self.session_edit.setPlaceholderText(default_session_name())
        self.session_edit.textChanged.connect(self._update_session_hint)
        self.session_hint = QLabel()
        self.session_hint.setProperty("muted", True)

        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(420)
        self.device_combo.currentIndexChanged.connect(self._device_chosen)
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self._load_devices)
        device_row = QHBoxLayout()
        device_row.addWidget(self.device_combo, 1)
        device_row.addWidget(refresh)

        form = QFormLayout()
        form.addRow("Сессия:", self.session_edit)
        form.addRow("", self.session_hint)
        form.addRow("Микрофон:", device_row)

        self.record_btn = QPushButton()
        self.record_btn.setMinimumHeight(52)
        self.record_btn.clicked.connect(self._toggle)

        self.elapsed = QLabel("0:00:00")
        self.elapsed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.elapsed.setStyleSheet("font-size: 40pt; font-weight: 300;")

        self.meter = LevelMeter()
        self.level_hint = QLabel(
            f"Скажите что-нибудь: при речи полоска должна заходить за пунктир ({QUIET_DB:.0f} дБ).")
        self.level_hint.setProperty("muted", True)
        self.level_hint.setWordWrap(True)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)

        self.after_row = QWidget()
        after = QHBoxLayout(self.after_row)
        after.setContentsMargins(0, 0, 0, 0)
        open_btn = QPushButton("Открыть сессию")
        open_btn.clicked.connect(self._open_session)
        transcribe_btn = QPushButton("Расшифровать")
        transcribe_btn.setProperty("role", "primary")
        transcribe_btn.clicked.connect(lambda: self._open_session(transcribe=True))
        after.addWidget(open_btn)
        after.addWidget(transcribe_btn)
        after.addStretch()
        self.after_row.setVisible(False)

        # Fixed-width column: a full-width meter and fields look lost on a large screen.
        column = QWidget()
        column.setMaximumWidth(860)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(column, 1)
        outer.addStretch(0)

        layout = QVBoxLayout(column)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(14)
        layout.addWidget(heading)
        layout.addLayout(form)
        layout.addSpacing(8)
        layout.addWidget(self.record_btn)
        layout.addWidget(self.elapsed)
        layout.addWidget(self.meter)
        layout.addWidget(self.level_hint)
        layout.addWidget(self.status)
        layout.addWidget(self.after_row)
        layout.addStretch()

        self._load_devices()
        self._set_recording(False)
        self._update_session_hint()
        ctx.config_changed.connect(self._load_devices)

    # --- devices ---

    def _load_devices(self) -> None:
        current = self.ctx.config.record.device
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItem("Системный по умолчанию", "")
        try:
            devices = list_input_devices()
        except Exception as e:  # no PortAudio / no devices
            devices = []
            self.status.setText(f"Не удалось получить список микрофонов: {e}")
        for d in devices:
            label = f"{d.name}  [{d.hostapi}]" + ("  — по умолчанию" if d.is_default else "")
            self.device_combo.addItem(label, d.key)
        index = self.device_combo.findData(current)
        if index < 0 and current:
            # Unknown value from the config (old index or name substring): keep it selectable.
            self.device_combo.addItem(f"{current}  (из настроек)", current)
            index = self.device_combo.count() - 1
        self.device_combo.setCurrentIndex(max(index, 0))
        self.device_combo.blockSignals(False)

    def _device_chosen(self) -> None:
        self.ctx.config.record.device = self.device_combo.currentData() or ""
        self.ctx.save_config()

    # --- session ---

    def _session(self):
        return open_session(self.ctx.config.sessions_dir, self.session_edit.text().strip() or None)

    def _update_session_hint(self) -> None:
        session = self._session()
        parts = session.audio_files()
        if parts:
            self.session_hint.setText(f"Сессия уже есть ({len(parts)} куск.) — запись продолжится новым куском.")
        else:
            self.session_hint.setText(f"Будет создана новая сессия «{session.name}».")
        self.session_hint.setToolTip(str(session.root))

    def _open_session(self, transcribe: bool = False) -> None:
        self.ctx.navigate("sessions", session=self._session().name, transcribe=transcribe)

    # --- recording ---

    def _set_recording(self, on: bool) -> None:
        self.record_btn.setText("■  Остановить запись" if on else "●  Начать запись")
        self.record_btn.setProperty("role", "danger" if on else "primary")
        self.record_btn.style().unpolish(self.record_btn)
        self.record_btn.style().polish(self.record_btn)
        self.session_edit.setEnabled(not on)
        self.device_combo.setEnabled(not on)

    def _toggle(self) -> None:
        if self.job and self.job.running:
            self.record_btn.setEnabled(False)
            self.job.cancel()
            return

        session = self._session()
        cfg = copy.deepcopy(self.ctx.config.record)
        self.after_row.setVisible(False)
        self.status.setText(f"Сессия: {session.root}")
        self.meter.reset()
        self._loud_at = time.monotonic()

        def work(job: Job) -> RecordResult:
            return record(session, cfg, job.cancel_event, on_status=job.emit_data, status_interval=0.1)

        self.job = self.ctx.jobs.start(
            f"Запись: {session.name}", work, kind="record", cancel_label="Остановить",
            meta={"session": session.name},
            on_success=self._finished, on_error=self._failed,
        )
        self.job.data.connect(self._on_status)
        self._set_recording(True)

    def _on_status(self, st: RecordStatus) -> None:
        self.elapsed.setText(fmt_duration(st.seconds))
        self.meter.set_peak(st.peak)
        self.job.report(-1, fmt_duration(st.seconds))

        now = time.monotonic()
        if peak_db(st.peak) >= QUIET_DB:
            self._loud_at = now
        quiet = now - self._loud_at > QUIET_WINDOW
        self.level_hint.setText(
            "⚠ Очень тихо. Если сейчас кто-то говорит — прибавьте громкость микрофона в Windows "
            "или выберите другой микрофон." if quiet else
            f"Скажите что-нибудь: при речи полоска должна заходить за пунктир ({QUIET_DB:.0f} дБ).")
        self.level_hint.setProperty("muted", not quiet)
        self.level_hint.style().polish(self.level_hint)

        parts = [f"Файл: {st.current_file.name}" if st.current_file else ""]
        if st.overflows:
            parts.append(f"<span style='color:#e0b92c'>потери буфера: {st.overflows}</span>")
        self.status.setText("  ·  ".join(p for p in parts if p))

    def _finished(self, result: RecordResult) -> None:
        self._set_recording(False)
        self.record_btn.setEnabled(True)
        self.meter.reset()
        session = self._session()
        self.ctx.session_changed.emit(session.name)
        self._update_session_hint()
        msg = f"Записано {fmt_duration(result.seconds)} в {len(result.files)} файл(а) — сессия «{session.name}»."
        if result.overflows:
            msg += f"<br><span style='color:#e0b92c'>Внимание: {result.overflows} раз терялись кусочки звука.</span>"
        self.status.setText(msg)
        self.after_row.setVisible(bool(result.files))

    def _failed(self, error: str) -> None:
        self._set_recording(False)
        self.record_btn.setEnabled(True)
        self.meter.reset()
        self.status.setText(f"<span style='color:#d9534f'>Ошибка записи: {error}</span>")

    def on_shown(self) -> None:
        if not (self.job and self.job.running):
            self._update_session_hint()
