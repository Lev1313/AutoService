"""Навигация, автомобили и резервное копирование."""

import sqlite3
from pathlib import Path

from PySide6.QtCore import QDateTime, QSettings, Qt
from PySide6.QtGui import QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from app.database.connection import backup_database, restore_database
from app.services.records import Records
from app.ui.appointments import AppointmentsPage
from app.ui.customers import CustomersPage
from app.ui.help import HelpDialog
from app.ui.maintenance import CallsPage
from app.ui.theme import THEME_KEY, apply_theme
from app.ui.vehicles import VehiclesPage, primary_button
from app.version import VERSION

SECTIONS = ("Автомобили", "Клиенты", "Звонки", "Записи", "Настройки")
LAST_BACKUP_KEY = "last_manual_backup"


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


class NavigationDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if index.row() == len(SECTIONS) - 1:
            size.setHeight(size.height() + 12)
        return size

    def paint(self, painter, option, index):
        if index.row() == len(SECTIONS) - 1:
            option = QStyleOptionViewItem(option)
            rect = option.rect
            painter.save()
            painter.setPen(option.palette.color(QPalette.ColorRole.Mid))
            painter.drawLine(rect.left() + 10, rect.top() + 5, rect.right() - 10, rect.top() + 5)
            painter.restore()
            option.rect = rect.adjusted(0, 12, 0, 0)
        super().paint(painter, option, index)


class MainWindow(QMainWindow):
    def __init__(self, db_path: Path, *, demo: bool = False) -> None:
        super().__init__()
        self.setWindowTitle("АвтоСервис — учёт автомобилей и ремонтов")
        if demo:
            self.setWindowTitle(self.windowTitle() + " — демонстрационный режим")
        self.resize(1100, 720)
        self.setMinimumSize(800, 540)
        self.db_path = db_path
        self.backup_preferences = QSettings(
            str(db_path.with_suffix(".ini")), QSettings.Format.IniFormat, self
        )
        theme = self.backup_preferences.value(THEME_KEY, "light", type=str)
        apply_theme(QApplication.instance(), "dark" if theme == "dark" else "light")

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(210)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(18, 25, 18, 18)
        brand = QHBoxLayout()
        brand.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(self.windowIcon().pixmap(32, 32))
        brand.addWidget(logo)
        brand.addWidget(label("АвтоСервис", "brand"), 1)
        side_layout.addLayout(brand)
        if demo:
            side_layout.addWidget(label("Демонстрационный режим. Отдельные данные.", "muted", True))
        side_layout.addSpacing(24)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setAccessibleName("Разделы программы")
        self.navigation.addItems(SECTIONS)
        self.navigation.setItemDelegate(NavigationDelegate(self.navigation))
        side_layout.addWidget(self.navigation)
        self.help_button = QPushButton("Инструкция · F1")
        self.help_button.clicked.connect(self.show_help)
        side_layout.addWidget(self.help_button)
        layout.addWidget(sidebar)

        self.pages = QStackedWidget()
        self.records = Records(db_path)
        self.vehicles_page = VehiclesPage(self.records)
        self.customers_page = CustomersPage(self.records)
        self.calls_page = CallsPage(self.records, "Звонки")
        self.appointments_page = AppointmentsPage(self.records)
        self.calls_page.appointment_requested.connect(self.schedule_from_call)
        for page in (
            self.vehicles_page,
            self.customers_page,
            self.calls_page,
            self.appointments_page,
            self.settings_page(theme),
        ):
            self.pages.addWidget(page)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.currentRowChanged.connect(self.refresh_page)
        self.navigation.setCurrentRow(0)
        self.navigation.setFocus()
        self.statusBar().showMessage(
            f"Версия {VERSION} ·  Разделы: Ctrl+1…{len(SECTIONS)}  ·  Инструкция: F1"
        )
        for index in range(len(SECTIONS)):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda row=index: self.navigation.setCurrentRow(row))
        search_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        search_shortcut.activated.connect(self.focus_search)
        help_shortcut = QShortcut(QKeySequence("F1"), self)
        help_shortcut.activated.connect(self.show_help)

    def settings_page(self, theme):
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        page = QWidget()
        content = QVBoxLayout(page)
        content.setContentsMargins(32, 30, 32, 24)
        content.setSpacing(12)
        content.addWidget(label("Настройки", "title"))

        appearance = self.settings_group(content, "Оформление")
        row = QHBoxLayout()
        caption = label("Тема")
        self.theme_select = QComboBox()
        self.theme_select.addItem("Светлая", "light")
        self.theme_select.addItem("Тёмная", "dark")
        self.theme_select.setCurrentIndex(1 if theme == "dark" else 0)
        self.theme_select.currentIndexChanged.connect(self.change_theme)
        caption.setBuddy(self.theme_select)
        row.addWidget(caption)
        row.addWidget(self.theme_select)
        row.addStretch()
        appearance.addLayout(row)

        storage = self.settings_group(content, "Резервные копии")
        path_label = label(str(self.db_path), "muted", True)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        storage.addWidget(path_label)
        self.backup_status = label("", "muted", True)
        self.show_backup_date(
            QDateTime.fromString(
                self.backup_preferences.value(LAST_BACKUP_KEY, "", type=str), Qt.DateFormat.ISODate
            )
        )
        storage.addWidget(self.backup_status)
        backup = primary_button("Создать резервную копию")
        backup.clicked.connect(self.create_backup)
        restore = QPushButton("Восстановить из копии")
        restore.clicked.connect(self.restore_backup)
        row = QHBoxLayout()
        row.addWidget(backup)
        row.addWidget(restore)
        row.addStretch()
        storage.addLayout(row)
        storage.addWidget(
            label(
                "Для восстановления нужна копия .sqlite3. Храните её на другом диске или флешке.",
                "muted",
                True,
            )
        )

        documents = self.settings_group(content, "Документы и экспорт")
        receipts = QPushButton("Реквизиты для квитанций")
        receipts.clicked.connect(self.document_settings)
        export = QPushButton("Экспорт базы в TXT / Excel")
        export.clicked.connect(self.export_data)
        row = QHBoxLayout()
        row.addWidget(receipts)
        row.addWidget(export)
        row.addStretch()
        documents.addLayout(row)
        content.addStretch()
        scroll.setWidget(page)
        return scroll

    @staticmethod
    def settings_group(parent, title):
        frame = QFrame()
        frame.setObjectName("settingsGroup")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.addWidget(label(title, "sectionTitle"))
        parent.addWidget(frame)
        return layout

    def change_theme(self):
        theme = self.theme_select.currentData()
        apply_theme(QApplication.instance(), theme)
        self.backup_preferences.setValue(THEME_KEY, theme)
        self.backup_preferences.sync()
        if self.backup_preferences.status() != QSettings.Status.NoError:
            QMessageBox.warning(
                self, "Не удалось сохранить тему", "Оформление действует до закрытия программы."
            )

    def refresh_page(self, index):
        page = self.pages.widget(index)
        if page in (
            self.vehicles_page,
            self.customers_page,
            self.calls_page,
            self.appointments_page,
        ):
            page.refresh()

    def schedule_from_call(self, vehicle_id, reason):
        if self.appointments_page.edit(vehicle_id=vehicle_id, reason=reason):
            self.navigation.setCurrentRow(self.pages.indexOf(self.appointments_page))

    def focus_search(self):
        page = self.pages.currentWidget()
        if page not in (self.vehicles_page, self.customers_page):
            page = self.vehicles_page
            self.navigation.setCurrentRow(self.pages.indexOf(page))
        page.search.setFocus()
        page.search.selectAll()

    def show_help(self):
        HelpDialog(self).exec()

    def document_settings(self):
        from app.services.documents import Documents
        from app.ui.documents import DocumentSettingsDialog

        DocumentSettingsDialog(Documents(self.db_path), self).exec()

    def export_data(self):
        from app.ui.export import ExportDialog

        ExportDialog(self.records, self).exec()

    def show_backup_date(self, completed_at: QDateTime):
        if completed_at.isValid():
            formatted = completed_at.toLocalTime().toString("dd.MM.yyyy 'в' HH:mm")
            self.backup_status.setText(f"Последняя ручная копия создана {formatted}.")
        else:
            self.backup_status.setText("Дата последней ручной копии пока неизвестна.")

    def create_backup(self):
        stamp = QDateTime.currentDateTime().toString("yyyy-MM-dd-HHmmss")
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить резервную копию",
            f"AutoService-copy-{stamp}.sqlite3",
            "База AutoService (*.sqlite3)",
        )
        if not filename:
            return
        try:
            destination = backup_database(self.db_path, Path(filename))
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            QMessageBox.warning(self, "Не удалось создать копию", str(error))
            return
        completed_at = QDateTime.currentDateTimeUtc()
        self.show_backup_date(completed_at)
        self.backup_preferences.setValue(
            LAST_BACKUP_KEY, completed_at.toString(Qt.DateFormat.ISODate)
        )
        self.backup_preferences.sync()
        if self.backup_preferences.status() != QSettings.Status.NoError:
            QMessageBox.warning(
                self,
                "Резервная копия готова",
                f"{destination}\n\nНе удалось запомнить дату копии для следующего запуска.",
            )
            return
        QMessageBox.information(self, "Резервная копия готова", str(destination))

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
