"""Навигация, автомобили и резервное копирование."""

import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.database.connection import backup_database, restore_database
from app.services.records import Records
from app.ui.customers import CustomersPage
from app.ui.maintenance import CallsPage
from app.ui.vehicles import VehiclesPage
from app.version import VERSION

SECTIONS = ("Автомобили", "Клиенты", "Звонки", "Финансы", "Настройки")

STYLESHEET = """
QWidget { color: #243247; font-family: 'Segoe UI'; font-size: 10pt; }
QMainWindow, QStackedWidget { background: #f5f7fa; }
QFrame#sidebar { background: white; border-right: 1px solid #dce3ec; }
QLabel#brand { font-size: 18pt; font-weight: 700; color: #175cd3; }
QLabel#title { font-size: 23pt; font-weight: 600; }
QLabel#muted { color: #596980; }
QLabel#badge { color: #175cd3; background: #eaf2ff; padding: 8px; border-radius: 6px; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 12px 10px; margin: 3px 0; border-radius: 6px; }
QListWidget::item:selected { background: #eaf2ff; color: #175cd3; font-weight: 600; }
QListWidget::item:hover:!selected { background: #f0f3f7; }
QListWidget::item:focus { border: 1px solid #175cd3; }
QTableWidget { background: white; border: 1px solid #dce3ec;
    border-radius: 6px; gridline-color: #e8edf3; selection-background-color: #eaf2ff;
    selection-color: #243247; alternate-background-color: #f8fafc; }
QHeaderView::section { background: #edf1f7; padding: 9px; border: none;
    border-bottom: 1px solid #dce3ec; font-weight: 600; }
QStatusBar { background: white; color: #596980; border-top: 1px solid #dce3ec; }
"""


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


class MainWindow(QMainWindow):
    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self.setWindowTitle("АвтоСервис — учёт автомобилей и ремонтов")
        self.resize(1100, 720)
        self.setMinimumSize(800, 540)
        self.setStyleSheet(STYLESHEET)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(210)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(18, 25, 18, 18)
        side_layout.addWidget(label("АвтоСервис", "brand"))
        side_layout.addWidget(label("Рабочее пространство", "muted"))
        side_layout.addSpacing(24)
        self.navigation = QListWidget()
        self.navigation.setAccessibleName("Разделы программы")
        self.navigation.addItems(SECTIONS)
        side_layout.addWidget(self.navigation)
        side_layout.addWidget(label("Локально · Без интернета", "muted"))
        side_layout.addWidget(label("Автомобили · Ремонты · ТО", "muted"))
        layout.addWidget(sidebar)

        self.pages = QStackedWidget()
        self.db_path = db_path
        self.records = Records(db_path)
        self.vehicles_page = VehiclesPage(self.records)
        self.customers_page = CustomersPage(self.records)
        self.calls_page = CallsPage(self.records, "Звонки")
        for name in SECTIONS:
            if name == "Автомобили":
                self.pages.addWidget(self.vehicles_page)
                continue
            if name == "Клиенты":
                self.pages.addWidget(self.customers_page)
                continue
            if name == "Звонки":
                self.pages.addWidget(self.calls_page)
                continue
            page = QWidget()
            content = QVBoxLayout(page)
            content.setContentsMargins(32, 30, 32, 24)
            content.setSpacing(18)
            content.addWidget(label(name, "title"))
            if name == "Настройки":
                content.addWidget(label("Хранение данных", "badge"))
                path_label = label(str(db_path), "", True)
                path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                content.addWidget(path_label)
                content.addWidget(
                    label(
                        "Создайте резервную копию на другом диске или флешке. "
                        "Перед восстановлением программа сохранит текущую базу отдельно.",
                        "muted",
                        True,
                    )
                )
                backup_button = QPushButton("Создать резервную копию")
                backup_button.clicked.connect(self.create_backup)
                content.addWidget(backup_button)
                restore_button = QPushButton("Восстановить из копии")
                restore_button.clicked.connect(self.restore_backup)
                content.addWidget(restore_button)
                export_button = QPushButton("Экспорт базы в TXT / Excel")
                export_button.clicked.connect(self.export_data)
                content.addWidget(export_button)
                document_settings = QPushButton("Реквизиты для квитанций")
                document_settings.clicked.connect(self.document_settings)
                content.addWidget(document_settings)
            else:
                content.addWidget(label("Будет реализовано позже", "badge"))
                description = "Финансовый учёт отложен до отдельного согласования."
                content.addWidget(label(description, "muted", True))
            if name != "Автомобили":
                content.addStretch()
            self.pages.addWidget(page)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.currentRowChanged.connect(self.refresh_page)
        self.navigation.setCurrentRow(0)
        self.navigation.setFocus()
        self.statusBar().showMessage(
            f"Версия {VERSION} · Пилот  ·  Разделы: Ctrl+1…{len(SECTIONS)}"
            "  ·  Навигация: Tab и стрелки"
        )
        for index in range(len(SECTIONS)):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda row=index: self.navigation.setCurrentRow(row))
        search_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        search_shortcut.activated.connect(self.focus_search)

    def refresh_page(self, index):
        page = self.pages.widget(index)
        if page in (self.vehicles_page, self.customers_page):
            page.refresh()
        elif page is self.calls_page:
            self.calls_page.refresh()

    def focus_search(self):
        self.navigation.setCurrentRow(self.pages.indexOf(self.vehicles_page))
        self.vehicles_page.search.setFocus()
        self.vehicles_page.search.selectAll()

    def document_settings(self):
        from app.services.documents import Documents
        from app.ui.documents import DocumentSettingsDialog

        DocumentSettingsDialog(Documents(self.db_path), self).exec()

    def export_data(self):
        from app.ui.export import ExportDialog

        ExportDialog(self.records, self).exec()

    def create_backup(self):
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить резервную копию",
            "AutoService-copy.sqlite3",
            "База AutoService (*.sqlite3)",
        )
        if not filename:
            return
        try:
            backup_database(self.db_path, Path(filename))
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            QMessageBox.warning(self, "Не удалось создать копию", str(error))
            return
        QMessageBox.information(self, "Резервная копия готова", filename)

    def restore_backup(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Выбрать резервную копию", "", "База AutoService (*.sqlite3)"
        )
        if not filename:
            return
        if (
            QMessageBox.question(
                self,
                "Восстановить данные?",
                "Текущие данные будут заменены данными из выбранной копии. "
                "Перед заменой программа сохранит текущую базу. Продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            safety_copy = restore_database(self.db_path, Path(filename))
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, "Не удалось восстановить данные", str(error))
            return
        self.vehicles_page.refresh()
        QMessageBox.information(
            self, "Данные восстановлены", f"Предыдущие данные сохранены здесь:\n{safety_copy}"
        )
