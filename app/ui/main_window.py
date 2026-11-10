"""Навигация первого этапа. Демонстрационные записи не сохраняются в базу."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMainWindow,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

SECTIONS = ("Главная", "Автомобили", "Календарь", "Финансы", "Настройки")

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
        side_layout.addWidget(label("Этап 1 · Каркас приложения", "muted"))
        layout.addWidget(sidebar)

        self.pages = QStackedWidget()
        for name in SECTIONS:
            page = QWidget()
            content = QVBoxLayout(page)
            content.setContentsMargins(32, 30, 32, 24)
            content.setSpacing(18)
            content.addWidget(label(name, "title"))
            if name == "Главная":
                content.addWidget(label("Всё начинается с автомобиля", "badge"))
                content.addWidget(
                    label(
                        "Среда разработки готова к первому этапу. Выберите раздел слева.",
                        "muted",
                        True,
                    )
                )
                content.addWidget(
                    label(
                        "История ремонтов, напоминания о звонках и запись на обслуживание "
                        "будут реализованы позже.",
                        "",
                        True,
                    )
                )
            elif name == "Автомобили":
                content.addWidget(
                    label(
                        "Демонстрационные данные — вымышленные автомобили. "
                        "Добавление и редактирование будут реализованы позже.",
                        "badge",
                        True,
                    )
                )
                table = QTableWidget(3, 4)
                table.setAccessibleName("Демонстрационные автомобили")
                table.setHorizontalHeaderLabels(("Автомобиль", "Год", "Пробег, км", "Тип записи"))
                rows = (
                    ("Лада Веста", "2020", "82 000", "Демонстрация"),
                    ("Киа Рио", "2018", "115 000", "Демонстрация"),
                    ("Рено Логан", "2016", "164 000", "Демонстрация"),
                )
                for row, values in enumerate(rows):
                    for column, value in enumerate(values):
                        table.setItem(row, column, QTableWidgetItem(value))
                table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
                table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
                table.setAlternatingRowColors(True)
                table.verticalHeader().hide()
                table.verticalHeader().setDefaultSectionSize(38)
                table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
                content.addWidget(table, 1)
            elif name == "Настройки":
                content.addWidget(label("Хранение данных", "badge"))
                path_label = label(str(db_path), "", True)
                path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                content.addWidget(path_label)
                content.addWidget(
                    label(
                        "База SQLite хранится отдельно от программы. "
                        "Настройки и резервное копирование будут реализованы позже.",
                        "muted",
                        True,
                    )
                )
            else:
                content.addWidget(label("Будет реализовано позже", "badge"))
                description = (
                    "Здесь появится запись автомобилей на ремонт."
                    if name == "Календарь"
                    else "Здесь появится учёт полученных платежей, частичных оплат и расходов."
                )
                content.addWidget(label(description, "muted", True))
            if name != "Автомобили":
                content.addStretch()
            self.pages.addWidget(page)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self.navigation.setFocus()
        self.statusBar().showMessage("Готово  ·  Разделы: Ctrl+1…5  ·  Навигация: Tab и стрелки")
        for index in range(len(SECTIONS)):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda row=index: self.navigation.setCurrentRow(row))
