"""Settings page, generated from config.SECTIONS and field metadata."""

from __future__ import annotations

import copy
import os
from dataclasses import fields

from PySide6.QtWidgets import (
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from dndsteno.config import SECTIONS
from dndsteno.gui.pages.base import Page
from dndsteno.gui.widgets.fields import editor_for


class SettingsPage(Page):
    page_id = "settings"
    title = "Настройки"
    icon = "⚙"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.editors: dict[tuple[str, str], QWidget] = {}

        heading = QLabel("Настройки")
        heading.setProperty("heading", True)
        self.path_label = QLabel()
        self.path_label.setProperty("muted", True)

        body = QWidget()
        body.setMaximumWidth(900)
        body_layout = QVBoxLayout(body)
        for key, (title, cls) in SECTIONS.items():
            group = QGroupBox(title)
            form = QFormLayout(group)
            defaults = cls()
            for f in fields(cls):
                editor = editor_for(f, getattr(defaults, f.name), ctx.config.base_dir)
                help_text = f.metadata.get("help", "")
                editor.setToolTip(help_text)
                cell = QWidget()
                cell_layout = QVBoxLayout(cell)
                cell_layout.setContentsMargins(0, 0, 0, 4)
                cell_layout.setSpacing(2)
                cell_layout.addWidget(editor)
                if help_text:
                    hint = QLabel(help_text)
                    hint.setProperty("muted", True)
                    hint.setWordWrap(True)
                    cell_layout.addWidget(hint)
                form.addRow(f.metadata.get("label", f.name) + ":", cell)
                self.editors[(key, f.name)] = editor
            body_layout.addWidget(group)
        body_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        save_btn = QPushButton("Сохранить")
        save_btn.setProperty("role", "primary")
        save_btn.clicked.connect(self.save)
        revert_btn = QPushButton("Отменить изменения")
        revert_btn.clicked.connect(self.load)
        open_btn = QPushButton("Открыть файл настроек")
        open_btn.clicked.connect(self._open_file)
        self.saved_label = QLabel()
        self.saved_label.setProperty("muted", True)
        buttons = QHBoxLayout()
        buttons.addWidget(save_btn)
        buttons.addWidget(revert_btn)
        buttons.addWidget(self.saved_label, 1)
        buttons.addWidget(open_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 16)
        layout.addWidget(heading)
        layout.addWidget(self.path_label)
        layout.addWidget(scroll, 1)
        layout.addLayout(buttons)

        ctx.config_changed.connect(self.load)
        self.load()

    def load(self) -> None:
        cfg = self.ctx.config
        self.path_label.setText(f"Файл: {cfg.path}" + ("" if cfg.path.exists() else " (будет создан при сохранении)"))
        for (key, name), editor in self.editors.items():
            editor.set(getattr(getattr(cfg, key), name))

    def save(self) -> None:
        new = copy.deepcopy(self.ctx.config)
        for (key, name), editor in self.editors.items():
            setattr(getattr(new, key), name, editor.get())
        try:
            new.transcribe.load_glossary(new.base_dir)
        except OSError as e:
            QMessageBox.warning(self, "Файл словаря", f"Не удалось прочитать файл словаря:\n{e}")
            return
        cfg = self.ctx.config
        for key in SECTIONS:
            setattr(cfg, key, getattr(new, key))
        self.ctx.save_config()
        self.saved_label.setText("Сохранено.")

    def _open_file(self) -> None:
        if not self.ctx.config.path.exists():
            self.ctx.save_config()
        os.startfile(self.ctx.config.path)

    def on_shown(self) -> None:
        self.saved_label.clear()
