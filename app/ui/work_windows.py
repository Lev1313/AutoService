"""Открытые рабочие окна одной запущенной программы."""

from PySide6.QtCore import QObject, Qt, Signal


class WorkWindows(QObject):
    customer_saved = Signal()
    vehicle_saved = Signal(int)
    repair_saved = Signal(int, int)

    def __init__(self, owner):
        super().__init__(owner)
        self.dialogs = {}
        self.closing = False

    def find(self, key):
        return next((dialog for dialog, current in self.dialogs.items() if current == key), None)

    def open(self, key, create):
        if self.closing:
            return None
        dialog = self.find(key) if key is not None else None
        if dialog is None:
            dialog = create()
            dialog._work_windows = self
            dialog.setWindowFlags(Qt.WindowType.Window)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.dialogs[dialog] = key
            dialog.finished.connect(lambda *_: self.dialogs.pop(dialog, None))
            dialog.destroyed.connect(lambda *_: self.dialogs.pop(dialog, None))
        self.activate(dialog)
        return dialog

    def activate(self, dialog):
        if dialog in self.dialogs:
            if dialog.isMinimized():
                dialog.showNormal()
            else:
                dialog.show()
            dialog.raise_()
            dialog.activateWindow()

    def close_all(self):
        self.closing = True
        try:
            return all(dialog.close() for dialog in reversed(tuple(self.dialogs)))
        finally:
            self.closing = False


def work_windows(widget):
    while widget.parentWidget() is not None and not hasattr(widget, "_work_windows"):
        widget = widget.parentWidget()
    if not hasattr(widget, "_work_windows"):
        widget._work_windows = WorkWindows(widget)
    return widget._work_windows
