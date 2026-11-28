"""Временные пометки орфографии поверх редактируемого текста."""

from difflib import SequenceMatcher
from functools import lru_cache

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QMessageBox,
    QPlainTextEdit,
    QStyledItemDelegate,
    QTextEdit,
)

from app.services.spelling import dictionary_job, known, read_words, save_word, suggestions, words

SELECT = QTextCursor.MoveMode.KeepAnchor
RED = QColor("#dc3745")


class WorkshopWords(QObject):
    changed = Signal()

    def __init__(self, path):
        super().__init__()
        self.path = path
        try:
            self.entries = read_words(path)
        except (OSError, UnicodeError):
            self.entries = set()

    def add(self, word):
        self.entries = save_word(self.path, word)
        self.changed.emit()


@lru_cache(maxsize=32)
def workshop_words(directory):
    return WorkshopWords(directory / "словарь.txt")


def replace_word(cursor, word, replacement):
    # Оставляем шрифт неизменённых фрагментов в ручном макете.
    start = cursor.selectionStart()
    cursor.beginEditBlock()
    for tag, a, b, c, d in reversed(SequenceMatcher(None, word, replacement).get_opcodes()):
        if tag != "equal":
            cursor.setPosition(start + a)
            cursor.setPosition(start + b, SELECT)
            cursor.insertText(replacement[c:d])
    cursor.endEditBlock()


class SpellMarks(QObject):
    updated = Signal()

    def __init__(self, editor, directory, *, overlay=False):
        super().__init__(editor)
        self.editor = editor
        self.vocabulary = workshop_words(directory)
        self.overlay = overlay
        self.ignored = []
        self.by_block = {}
        self.timer = QTimer(self, singleShot=True, interval=280)
        self.timer.timeout.connect(self.refresh)
        editor.textChanged.connect(self.schedule)
        self.vocabulary.changed.connect(self.refresh)
        self.schedule()

    def schedule(self):
        self.timer.start()

    def refresh(self):
        self.timer.stop()
        if not self.overlay and (not self.editor.isEnabled() or self.editor.isReadOnly()):
            self.by_block.clear()
            self.editor.setExtraSelections([])
            return
        if not dictionary_job().done():
            self.schedule()
            return
        self.ignored = [(cur, word) for cur, word in self.ignored if cur.selectedText() == word]
        ignored = {(cur.selectionStart(), word) for cur, word in self.ignored}
        self.by_block = {}
        selections = []
        doc = self.editor.document()
        block = doc.begin()
        fmt = QTextCharFormat()
        fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SpellCheckUnderline)
        fmt.setUnderlineColor(RED)
        while block.isValid():
            text = block.text()
            last = 0
            offset = block.position()
            for index, word in words(text):
                if word.lower() in self.vocabulary.entries or known(word):
                    continue
                offset += len(text[last:index].encode("utf-16-le")) // 2
                last = index
                if (offset, word) in ignored:
                    continue
                end = offset + len(word)
                self.by_block.setdefault(block.blockNumber(), []).append((offset, end))
                if not self.overlay:
                    mark = QTextEdit.ExtraSelection()
                    mark.cursor = QTextCursor(doc)
                    mark.cursor.setPosition(offset)
                    mark.cursor.setPosition(end, SELECT)
                    mark.format = fmt
                    selections.append(mark)
            block = block.next()
        if not self.overlay:
            self.editor.setExtraSelections(selections)
        self.updated.emit()

    def menu(self, position, menu):
        self.refresh()
        doc = self.editor.document()
        block = doc.findBlock(position)
        for start, end in self.by_block.get(block.blockNumber(), ()):
            if start <= position <= end:
                break
        else:
            return False
        cursor = QTextCursor(doc)
        cursor.setPosition(start)
        cursor.setPosition(end, SELECT)
        word = cursor.selectedText()
        first = next(iter(menu.actions()), None)
        if first:
            first = menu.insertSeparator(first)
        for replacement in suggestions(word):
            action = menu.addAction(replacement)
            action.triggered.connect(
                lambda checked=False, value=replacement: replace_word(cursor, word, value)
            )
            menu.insertAction(first, action)

        def ignore():
            self.ignored.append((cursor, word))
            self.refresh()

        def add():
            try:
                self.vocabulary.add(word)
            except (OSError, UnicodeError) as error:
                QMessageBox.warning(self.editor, "Не удалось сохранить слово", str(error))

        for label, callback in (("Пропустить", ignore), ("Добавить в словарь", add)):
            action = menu.addAction(label, callback)
            menu.insertAction(first, action)
        return True


class SpellField(QPlainTextEdit):
    def __init__(self, directory, text="", parent=None):
        super().__init__(text, parent)
        self.spelling = SpellMarks(self, directory)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange and hasattr(self, "spelling"):
            self.spelling.schedule()

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        if not self.isReadOnly():
            position = self.cursorForPosition(event.pos()).position()
            self.spelling.menu(position, menu)
        menu.exec(event.globalPos())
        menu.deleteLater()


class TitleEditor(SpellField):
    def insertFromMimeData(self, source):
        self.insertPlainText(" ".join(source.text().splitlines()))


class TitleDelegate(QStyledItemDelegate):
    def __init__(self, directory, parent):
        super().__init__(parent)
        self.directory = directory

    def createEditor(self, parent, option, index):
        editor = TitleEditor(self.directory, parent=parent)
        editor.setFrameShape(QFrame.Shape.NoFrame)
        editor.setTabChangesFocus(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.document().setDocumentMargin(1)
        return editor

    def eventFilter(self, editor, event):
        if event.type() == QEvent.Type.KeyPress:
            hints = {
                Qt.Key.Key_Tab: self.EndEditHint.EditNextItem,
                Qt.Key.Key_Backtab: self.EndEditHint.EditPreviousItem,
                Qt.Key.Key_Return: self.EndEditHint.NoHint,
                Qt.Key.Key_Enter: self.EndEditHint.NoHint,
            }
            if event.key() in hints:
                self.commitData.emit(editor)
                self.closeEditor.emit(editor, hints[event.key()])
                return True
        return super().eventFilter(editor, event)

    def setEditorData(self, editor, index):
        editor.setPlainText(index.data() or "")
        editor.selectAll()

    def setModelData(self, editor, model, index):
        model.setData(index, editor.toPlainText(), Qt.ItemDataRole.EditRole)
