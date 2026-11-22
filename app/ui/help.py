"""Инструкция из того же текста, что и печатное руководство."""

import sys
from functools import cache
from hashlib import file_digest
from html import escape
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTextBrowser, QVBoxLayout

from app.database.connection import SCHEMA_VERSION
from app.guide_text import guide_blocks
from app.version import VERSION


def guide_path():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "Инструкция.txt"
    return Path(__file__).resolve().parents[2] / "docs" / "OWNER-GUIDE.txt"


@cache
def build_identifier():
    if not getattr(sys, "frozen", False):
        return "запуск из исходников"
    try:
        with Path(sys.executable).open("rb") as executable:
            return file_digest(executable, "sha256").hexdigest()[:16]
    except OSError:
        return "недоступен"


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Инструкция — AutoService")
        self.resize(820, 650)
        self.setMinimumSize(550, 420)
        layout = QVBoxLayout(self)
        self.build_info = QLabel(
            f"Версия {VERSION} · схема базы {SCHEMA_VERSION} · сборка {build_identifier()}"
        )
        self.build_info.setWordWrap(True)
        self.build_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.build_info)
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
