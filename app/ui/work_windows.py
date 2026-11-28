"""Открытые рабочие окна одной запущенной программы."""

from PySide6.QtCore import QObject, Qt, Signal


class WorkWindows(QObject):
    customer_saved = Signal()
    vehicle_saved = Signal(int)
    repair_saved = Signal(int, int)

    def __init__(self, owner):
        super().__init__(owner)
        self.dialogs = {}
        self.covered = {}
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
            dialog.finished.connect(lambda *_: self.forget(dialog))
            dialog.destroyed.connect(lambda *_: self.forget(dialog))
        return self.activate(dialog)

    def cover(self, card, repair):
        if repair is not None and card in self.dialogs:
            self.covered[card] = repair
            card.hide()

    def forget(self, dialog):
        self.dialogs.pop(dialog, None)
        self.covered.pop(dialog, None)
        for card, repair in tuple(self.covered.items()):
            if repair is dialog:
                del self.covered[card]
                if not self.closing:
                    self.activate(card)

    def activate(self, dialog):
        dialog = self.covered.get(dialog, dialog)
        if dialog in self.dialogs:
            if dialog.isMinimized():
                dialog.showNormal()
            else:
                dialog.show()
            dialog.raise_()
            dialog.activateWindow()
        return dialog

    def close_all(self):
        self.closing = True
        covered = tuple(self.covered)
        try:
            dialogs = reversed(tuple(self.dialogs))
            for dialog in sorted(dialogs, key=lambda window: window in self.covered):
                if not dialog.close():
                    for card in covered:
                        if card in self.dialogs and card not in self.covered:
                            card.show()
                    self.activate(dialog)
                    return False
                self.forget(dialog)
            return True
        finally:
            self.closing = False


def work_windows(widget):
    while widget.parentWidget() is not None and not hasattr(widget, "_work_windows"):
        widget = widget.parentWidget()
    if not hasattr(widget, "_work_windows"):
        widget._work_windows = WorkWindows(widget)
    return widget._work_windows
