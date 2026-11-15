"""Локальные инструкции: те же файлы, которые поставляются с выпуском."""

import sys
from pathlib import Path

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QTabWidget, QVBoxLayout

GUIDES = (
    ("Начать работу", "QUICK-START.txt", "Быстрый старт.txt"),
    ("Все действия", "OWNER-GUIDE.txt", "Инструкция.txt"),
)


def guide_path(source_name, release_name):
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / release_name
    return Path(__file__).resolve().parents[2] / "docs" / source_name


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Инструкция — AutoService")
        self.resize(820, 650)
        self.setMinimumSize(550, 420)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        for title, source_name, release_name in GUIDES:
            text = QPlainTextEdit()
            text.setReadOnly(True)
            text.setAccessibleName(title)
            try:
                content = guide_path(source_name, release_name).read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError):
                content = "Не удалось прочитать инструкцию. Проверьте комплектность программы."
            text.setPlainText(content)
            self.tabs.addTab(text, title)
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
