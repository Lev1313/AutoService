"""Локальные инструкции: те же файлы, которые поставляются с выпуском."""

import sys
from html import escape
from pathlib import Path

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTabWidget, QTextBrowser, QVBoxLayout

from app.guide_text import guide_blocks

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
        tags = {"title": "h1", "chapter": "h2", "heading": "h3", "body": "p"}
        for title, source_name, release_name in GUIDES:
            text = QTextBrowser()
            text.setAccessibleName(title)
            try:
                content = guide_path(source_name, release_name).read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError):
                text.setPlainText(
                    "Не удалось прочитать инструкцию. Проверьте комплектность программы."
                )
            else:
                text.setHtml(
                    "".join(
                        f"<{tags[kind]}>{escape(block)}</{tags[kind]}>"
                        for kind, block in guide_blocks(content)
                    )
                )
                text.document().setDocumentMargin(16)
            self.tabs.addTab(text, title)
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
