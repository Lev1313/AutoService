"""Инструкция из того же текста, что и печатное руководство."""

import sys
from html import escape
from pathlib import Path

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout

from app.guide_text import guide_blocks


def guide_path():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "Инструкция.txt"
    return Path(__file__).resolve().parents[2] / "docs" / "OWNER-GUIDE.txt"


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Инструкция — AutoService")
        self.resize(820, 650)
        self.setMinimumSize(550, 420)
        layout = QVBoxLayout(self)
        self.guide = QTextBrowser()
        self.guide.setAccessibleName("Инструкция")
        tags = {"title": "h1", "chapter": "h2", "heading": "h3", "body": "p"}
        try:
            content = guide_path().read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            self.guide.setPlainText(
                "Не удалось прочитать инструкцию. Проверьте комплектность программы."
            )
        else:
            self.guide.setHtml(
                "".join(
                    f"<{tags[kind]}>{escape(block)}</{tags[kind]}>"
                    for kind, block in guide_blocks(content)
                )
            )
            self.guide.document().setDocumentMargin(16)
        layout.addWidget(self.guide)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
