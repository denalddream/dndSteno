"""GUI entry point: `steno gui`, `steno-gui`, or `python -m dndsteno.gui`."""

from __future__ import annotations

import logging
import sys
import traceback

from PySide6.QtWidgets import QApplication, QMessageBox

from dndsteno.config import Config, load_config
from dndsteno.gui.context import AppContext
from dndsteno.gui.main_window import MainWindow
from dndsteno.gui.theme import apply_theme

LOG_NAME = "dndsteno.log"


def setup_logging(cfg: Config) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(cfg.base_dir / LOG_NAME, encoding="utf-8")],
    )


def _excepthook(exc_type, exc, tb) -> None:
    """Unhandled errors in UI code: log and show instead of silently killing the app."""
    logging.getLogger("dndsteno").error("Unhandled:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
    QMessageBox.critical(None, "Ошибка", f"{exc_type.__name__}: {exc}\n\nПодробности в {LOG_NAME}.")


def run(cfg: Config | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("dndSteno")
    apply_theme(app)

    try:
        cfg = cfg or load_config()
    except Exception as e:
        QMessageBox.critical(None, "Ошибка в настройках", f"Не удалось прочитать dndsteno.toml:\n{e}")
        return 1
    setup_logging(cfg)
    sys.excepthook = _excepthook

    window = MainWindow(AppContext(cfg))
    window.show()
    return app.exec()


def main() -> int:
    return run()
