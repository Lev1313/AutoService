"""Shared Qt palette for native controls, calendar and table statuses."""

from string import Template

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

THEME_KEY = "appearance/theme"
COLORS = {
    "window": ("#f5f7fa", "#181e28"),
    "surface": ("#ffffff", "#232c3a"),
    "raised": ("#edf1f7", "#2c384a"),
    "alternate": ("#f8fafc", "#273243"),
    "text": ("#243247", "#edf2fa"),
    "muted": ("#596980", "#b4c3d7"),
    "disabled": ("#63738a", "#a0aec2"),
    "border": ("#dce3ec", "#44536a"),
    "grid": ("#e8edf3", "#344157"),
    "accent": ("#175cd3", "#2c6fe2"),
    "accent_hover": ("#104eba", "#235fc6"),
    "accent_pressed": ("#0d429f", "#194d9f"),
    "accent_ink": ("#1754bb", "#b1d1ff"),
    "accent_tint": ("#eaf2ff", "#304a70"),
    "accent_edge": ("#3970c4", "#89b7ff"),
    "good_bg": ("#dcefe5", "#234638"),
    "good_fg": ("#1b5537", "#bcf0d2"),
    "warning_bg": ("#f8ebd0", "#513d20"),
    "warning_fg": ("#694505", "#ffe0a1"),
}

STYLESHEET = Template("""
QWidget { color: $text; font-family: 'Segoe UI'; font-size: 10pt; }
QWidget:disabled { color: $disabled; }
QMainWindow, QDialog, QStackedWidget { background: $window; }
QFrame#sidebar { background: $surface; border-right: 1px solid $border; }
QFrame#settingsGroup { background: $surface; border: 1px solid $border; border-radius: 6px; }
QLabel#brand { font-size: 16pt; font-weight: 700; color: $accent_ink; }
QLabel#title { font-size: 23pt; font-weight: 600; }
QLabel#repairTitle { font-size: 17pt; font-weight: 600; }
QLabel#sectionTitle { font-size: 12pt; font-weight: 600; }
QLabel#muted { color: $muted; }
QLabel#badge { color: $accent_ink; background: $accent_tint; padding: 6px; border-radius: 6px; }
QListWidget { background: $surface; }
QListWidget#navigation { background: transparent; border: none; outline: none; }
QListWidget#navigation::item { padding: 12px 10px; margin: 3px 0; border-radius: 6px; }
QListWidget#navigation::item:selected {
    background: $accent_tint; color: $accent_ink; font-weight: 600;
}
QListWidget#navigation::item:hover:!selected { background: $raised; }
QListWidget#navigation::item:focus { border: 1px solid $accent_edge; }
QPushButton[primary="true"] {
    background: $accent; color: white; border: 1px solid $accent; border-radius: 4px;
    padding: 4px 10px; font-weight: 600;
}
QPushButton[primary="true"]:hover { background: $accent_hover; }
QPushButton[primary="true"]:pressed { background: $accent_pressed; }
QPushButton[primary="true"]:focus { border-color: $accent_edge; text-decoration: underline; }
QPushButton[primary="true"]:disabled {
    color: $disabled; background: $raised; border-color: $border;
}
QPushButton#appointmentView {
    background: $surface; border: 1px solid $border; border-radius: 4px; padding: 3px 10px;
}
QPushButton#appointmentView:hover { background: $raised; }
QPushButton#appointmentView:checked {
    background: $accent_tint; color: $accent_ink; font-weight: 600;
}
QPushButton#appointmentView:focus { border-color: $accent_edge; }
QTableWidget, QTreeWidget { background: $surface; border: 1px solid $border;
    border-radius: 6px; gridline-color: $grid; selection-background-color: $accent_tint;
    selection-color: $text; alternate-background-color: $alternate; }
QHeaderView::section { background: $raised; padding: 9px; border: none;
    border-bottom: 1px solid $border; font-weight: 600; }
QStatusBar { background: $surface; color: $muted; border-top: 1px solid $border; }
QFrame#calendarDay { background: $surface; border: 1px solid $border; border-radius: 4px; }
QFrame#calendarDay[outsideMonth="true"] { background: $raised; }
QFrame#calendarDay[today="true"] { border: 2px solid $accent_edge; }
QPushButton#calendarDate, QPushButton#calendarVisit {
    min-width: 0; min-height: 0; padding: 2px 4px; text-align: left;
    border: 1px solid transparent; border-radius: 3px;
}
QPushButton#calendarDate { background: transparent; }
QPushButton#calendarDate[today="true"] {
    background: $accent_tint; color: $accent_ink; font-weight: 700;
}
QPushButton#calendarVisit { background: $accent_tint; color: $accent_ink; }
QPushButton#calendarVisit[arrived="true"] { background: $good_bg; color: $good_fg; }
QPushButton#calendarVisit[inactive="true"] { background: $raised; color: $muted; }
QPushButton#calendarDate:hover, QPushButton#calendarVisit:hover,
QPushButton#calendarDate:focus, QPushButton#calendarVisit:focus { border-color: $accent_edge; }
""")


def theme_colors(theme):
    index = int(theme == "dark")
    return {name: colors[index] for name, colors in COLORS.items()}


def apply_theme(application, theme):
    colors = theme_colors(theme)
    application.setProperty("appearanceTheme", theme)
    palette = QPalette()
    for role, color in {
        "Window": "window",
        "Base": "surface",
        "AlternateBase": "alternate",
        "WindowText": "text",
        "Text": "text",
        "ButtonText": "text",
        "Button": "raised",
        "ToolTipBase": "surface",
        "ToolTipText": "text",
        "PlaceholderText": "muted",
        "Highlight": "accent",
        "Link": "accent_ink",
        "Mid": "border",
        "Light": "raised",
        "Midlight": "raised",
        "Dark": "border",
        "Shadow": "window",
    }.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(colors[color]))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("white"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.WindowText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled"]))
    application.setPalette(palette)
    application.setStyleSheet(STYLESHEET.substitute(colors))


def set_primary(button, primary):
    if button.property("primary") != primary:
        button.setProperty("primary", primary)
        button.style().unpolish(button)
        button.style().polish(button)
        button.update()


STATUS_TONE = Qt.ItemDataRole.UserRole


class StatusDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        tone = index.data(STATUS_TONE)
        if tone is None:
            super().paint(painter, option, index)
            return
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        text = styled.text
        styled.text = ""
        widget = styled.widget
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, widget)
        colors = theme_colors(QApplication.instance().property("appearanceTheme"))
        background, foreground = {
            "active": ("accent_tint", "accent_ink"),
            "done": ("good_bg", "good_fg"),
            "attention": ("warning_bg", "warning_fg"),
            "muted": ("raised", "muted"),
        }[tone]
        rect = styled.rect.adjusted(6, 3, -6, -3)
        if rect.width() <= 12:
            return
        text = styled.fontMetrics.elidedText(text, Qt.TextElideMode.ElideRight, rect.width() - 12)
        rect.setWidth(min(rect.width(), styled.fontMetrics.horizontalAdvance(text) + 12))
        height = min(rect.height(), styled.fontMetrics.height() + 6)
        rect.translate(0, (rect.height() - height) // 2)
        rect.setHeight(height)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colors[background]))
        painter.drawRoundedRect(rect, 5, 5)
        painter.setPen(QColor(colors[foreground]))
        painter.setFont(styled.font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()
