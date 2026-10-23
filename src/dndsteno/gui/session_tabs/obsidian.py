"""Preview and apply vault changes. Nothing is written until the user ticks changes and confirms."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

from dndsteno.gui.session_tabs.base import SessionTab
from dndsteno.obsidian import Change
from dndsteno.pipeline import apply_obsidian, plan_obsidian

ICONS = {"create": "✚", "append": "＋"}


class ObsidianTab(SessionTab):
    title = "Obsidian"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.changes: list[Change] = []

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setProperty("muted", True)
        self.settings_btn = QPushButton("Открыть настройки")
        self.settings_btn.clicked.connect(lambda: self.ctx.navigate("settings"))
        self.reload_btn = QPushButton("Обновить список")
        self.reload_btn.clicked.connect(lambda: self.load(self.session))
        self.apply_btn = QPushButton("Записать отмеченное в Obsidian")
        self.apply_btn.setProperty("role", "primary")
        self.apply_btn.clicked.connect(self.apply)

        bar = QHBoxLayout()
        bar.addWidget(self.status, 1)
        bar.addWidget(self.settings_btn)
        bar.addWidget(self.reload_btn)
        bar.addWidget(self.apply_btn)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._preview)
        self.list.itemChanged.connect(lambda _: self._update_apply())
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont("Consolas", 10))

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_title = QLabel()
        self.preview_title.setWordWrap(True)
        right_layout.addWidget(self.preview_title)
        right_layout.addWidget(self.preview, 1)

        splitter = QSplitter()
        splitter.addWidget(self.list)
        splitter.addWidget(right)
        splitter.setSizes([380, 600])

        hint = QLabel("Нейросеть ничего не удаляет: в существующие статьи дописывается помеченный блок в конец, "
                      "новые статьи создаются рядом. Перед правкой копия статьи сохраняется в папку сессии.")
        hint.setProperty("muted", True)
        hint.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(splitter, 1)
        layout.addWidget(hint)

    def load(self, session) -> None:
        super().load(session)
        self.list.blockSignals(True)
        self.list.clear()
        self.changes = []
        self.preview.clear()
        self.preview_title.clear()
        self.settings_btn.setVisible(False)
        if session is None:
            self.list.blockSignals(False)
            self._update_apply()
            return
        try:
            self.changes = plan_obsidian(self.ctx.config, session)
        except FileNotFoundError as e:
            self.status.setText(str(e))
            self.settings_btn.setVisible("Настройки" in str(e))
        except Exception as e:
            self.status.setText(f"Не удалось подготовить изменения: {e}")
        for ch in self.changes:
            done = ch.status == "done"
            icon = "✔" if done else ICONS[ch.action]
            item = QListWidgetItem(f"{icon}  {ch.title}\n     {ch.rel_path}")
            if done:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                item.setToolTip("Уже записано в vault")
                item.setForeground(self.palette().placeholderText())
            else:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(item)
        if self.changes:
            pending = sum(c.status != "done" for c in self.changes)
            self.status.setText(f"Изменений: {len(self.changes)}, ещё не записано: {pending}. "
                                "Снимите галочки с того, что не нужно.")
            self.list.setCurrentRow(0)
        self.list.blockSignals(False)
        self._preview(self.list.currentRow())
        self._update_apply()

    def _checked(self) -> list[Change]:
        return [ch for i, ch in enumerate(self.changes)
                if ch.status != "done" and self.list.item(i).checkState() == Qt.CheckState.Checked]

    def _update_apply(self) -> None:
        self.apply_btn.setEnabled(bool(self.changes) and bool(self._checked()))

    def _preview(self, row: int) -> None:
        if not (0 <= row < len(self.changes)):
            return
        ch = self.changes[row]
        what = {"create": "Будет создан файл", "append": "Будет дописано в конец файла"}[ch.action]
        if ch.status == "done":
            what = "Уже записано в"
        self.preview_title.setText(f"<b>{what}:</b> {ch.rel_path}")
        self.preview.setPlainText(ch.content)

    def apply(self) -> None:
        chosen = self._checked()
        if not chosen:
            return
        creates = sum(c.action == "create" for c in chosen)
        appends = len(chosen) - creates
        answer = QMessageBox.question(
            self, "Записать в Obsidian",
            f"Дописать в статьи: {appends}\nСоздать новых: {creates}\n\n"
            "Существующий текст не меняется. Копии статей до правки сохранятся в папке сессии. Продолжить?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        results = apply_obsidian(self.ctx.config, self.session, chosen)
        errors = [f"• {ch.rel_path}: {r}" for ch, r in results if r not in ("done", "skipped")]
        done = sum(r == "done" for _, r in results)
        if errors:
            QMessageBox.warning(self, "Obsidian", f"Записано: {done}. С ошибками:\n" + "\n".join(errors))
        else:
            QMessageBox.information(self, "Obsidian", f"Готово, записано изменений: {done}.")
        self.load(self.session)
