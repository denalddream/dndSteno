"""Editors for config fields, chosen from the field's default type and metadata.

Each editor has get() / set(value). `editor_for` picks one; a new kind of
setting needs a new branch here and nothing else.
"""

from __future__ import annotations

from dataclasses import Field
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLineEdit, QPlainTextEdit,
    QPushButton, QSpinBox, QWidget,
)


class LineEditor(QLineEdit):
    def get(self):
        return self.text().strip()

    def set(self, value):
        self.setText(str(value))


class IntEditor(QSpinBox):
    def __init__(self):
        super().__init__()
        self.setRange(0, 1_000_000)
        self.setMaximumWidth(160)

    def get(self):
        return self.value()

    def set(self, value):
        self.setValue(int(value))


class FloatEditor(QDoubleSpinBox):
    def __init__(self):
        super().__init__()
        self.setRange(0, 1_000_000)
        self.setDecimals(1)
        self.setMaximumWidth(160)

    def get(self):
        return self.value()

    def set(self, value):
        self.setValue(float(value))


class BoolEditor(QCheckBox):
    def get(self):
        return self.isChecked()

    def set(self, value):
        self.setChecked(bool(value))


class ChoiceEditor(QComboBox):
    """Combo with suggested values; editable so a value outside the list still fits."""

    def __init__(self, choices: list):
        super().__init__()
        self.setEditable(True)
        self.addItems([str(c) for c in choices])

    def get(self):
        return self.currentText().strip()

    def set(self, value):
        self.setCurrentText(str(value))


class ListEditor(QPlainTextEdit):
    """list[str], one item per line."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(110)
        self.setPlaceholderText("по одному на строку")

    def get(self):
        return [line.strip() for line in self.toPlainText().splitlines() if line.strip()]

    def set(self, value):
        self.setPlainText("\n".join(value))


class PathEditor(QWidget):
    def __init__(self, directory: bool, base_dir: Path):
        super().__init__()
        self.directory, self.base_dir = directory, base_dir
        self.edit = QLineEdit()
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self._browse)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(browse)

    def _browse(self):
        start = str(self.base_dir / self.edit.text()) if self.edit.text() else str(self.base_dir)
        if self.directory:
            path = QFileDialog.getExistingDirectory(self, "Выберите папку", start)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Выберите файл", start)
        if path:
            # Keep paths inside the project relative, so the folder can be moved.
            p = Path(path)
            try:
                p = p.relative_to(self.base_dir)
            except ValueError:
                pass
            self.edit.setText(str(p))

    def get(self):
        return self.edit.text().strip()

    def set(self, value):
        self.edit.setText(str(value))


class AudioInputEditor(QComboBox):
    def __init__(self):
        super().__init__()
        self.addItem("Системный по умолчанию", "")
        try:
            from dndsteno.recorder import list_input_devices

            for d in list_input_devices():
                self.addItem(f"{d.name}  [{d.hostapi}]", d.key)
        except Exception:
            pass

    def get(self):
        return self.currentData() or ""

    def set(self, value):
        index = self.findData(value)
        if index < 0:
            self.addItem(f"{value}  (из настроек)", value)
            index = self.count() - 1
        self.setCurrentIndex(index)


def editor_for(f: Field, default, base_dir: Path) -> QWidget:
    meta = f.metadata
    kind = meta.get("kind", "")
    if kind == "audio_input":
        return AudioInputEditor()
    if kind in ("dir", "file"):
        return PathEditor(directory=kind == "dir", base_dir=base_dir)
    if meta.get("choices"):
        return ChoiceEditor(meta["choices"])
    if isinstance(default, bool):
        return BoolEditor()
    if isinstance(default, int):
        return IntEditor()
    if isinstance(default, float):
        return FloatEditor()
    if isinstance(default, list):
        return ListEditor()
    return LineEditor()
