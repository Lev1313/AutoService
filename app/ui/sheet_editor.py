"""Редактирование поверх фактических страниц PDF. Координаты строк даёт печатный шаблон."""

from bisect import bisect_left

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.services.foltz_pdf import FOLTZ_PAGE
from app.services.service_sheet_pdf import (
    BODY_SIZE,
    TEXT_LEFT,
    build_service_sheet,
    sheet_blocks,
    sheet_signature,
)
from app.ui.spelling import RED, SpellMarks

KEEP = QTextFormat.Property.UserProperty + 1
SOURCE = QTextFormat.Property.UserProperty + 2
MM = 72 / 25.4
MOVE = QTextCursor.MoveMode.MoveAnchor
SELECT = QTextCursor.MoveMode.KeepAnchor


def qt_length(text):
    return len(text.encode("utf-16-le")) // 2


def run_format(run):
    fmt = QTextCharFormat()
    fmt.setFontPointSize(run["size"])
    fmt.setFontWeight(700 if run["bold"] else 400)
    return fmt


def block_runs(block):
    out = []
    iterator = block.begin()
    while not iterator.atEnd():
        fragment = iterator.fragment()
        fmt = fragment.charFormat()
        out.append(
            {
                "text": fragment.text(),
                "size": fmt.fontPointSize() or BODY_SIZE,
                "bold": fmt.fontWeight() >= 600,
            }
        )
        iterator += 1
    if not out:
        fmt = block.charFormat()
        out.append(
            {"text": "", "size": fmt.fontPointSize() or BODY_SIZE, "bold": fmt.fontWeight() >= 600}
        )
    return out


class SheetCanvas(QAbstractScrollArea):
    edited = Signal()
    locationChanged = Signal()
    failed = Signal(str)

    def __init__(self, parent, directory):
        super().__init__(parent)
        self.setAccessibleName("Текст сервисного листа на странице А4")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self.input = QTextEdit(self)
        self.input.hide()
        self.spelling = SpellMarks(self.input, directory, overlay=True)
        self.spelling.updated.connect(self.viewport().update)
        self.input.setAcceptRichText(False)
        self.input.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.input.textChanged.connect(self.changed)
        self.input.cursorPositionChanged.connect(self.cursor_changed)
        self.pdf = QPdfDocument(self)
        self.buffer = QBuffer(self)
        self.snapshot = None
        self.source = ""
        self.lines = []
        self.images = {}
        self.spans = []
        self.drag = None
        self.hover_block = -1
        self.viewport().setMouseTracking(True)
        self.loading = False
        self.timer = QTimer(self, singleShot=True, interval=20)
        self.timer.timeout.connect(self.refresh)
        self.verticalScrollBar().valueChanged.connect(self.locationChanged)
        self.blink = QTimer(self, interval=500)
        self.blink.timeout.connect(self.blink_cursor)
        self.caret = True

    def blink_cursor(self):
        self.caret = not self.caret
        self.viewport().update()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.caret = True
        self.blink.start()
        self.viewport().update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.blink.stop()
        self.viewport().update()

    def blocks(self):
        result = []
        block = self.input.document().begin()
        while block.isValid():
            fmt = block.blockFormat()
            result.append(
                {
                    "gap": fmt.topMargin(),
                    "right": fmt.alignment() == Qt.AlignmentFlag.AlignRight,
                    "keep": bool(fmt.property(KEEP)),
                    "runs": block_runs(block),
                }
            )
            block = block.next()
        return result

    def manual(self):
        source = self.input.document().firstBlock().blockFormat().property(SOURCE) or self.source
        manual = {"source": source, "blocks": self.blocks()}
        if self.artwork:
            manual["artwork"] = self.artwork
        return manual

    def populate(self, blocks, undoable=False):
        cursor = self.input.textCursor()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.removeSelectedText()
        for index, block in enumerate(blocks):
            fmt = QTextBlockFormat()
            fmt.setTopMargin(block["gap"])
            fmt.setAlignment(
                Qt.AlignmentFlag.AlignRight if block["right"] else Qt.AlignmentFlag.AlignLeft
            )
            fmt.setProperty(KEEP, block["keep"])
            if not index:
                fmt.setProperty(SOURCE, self.source)
            first = run_format(block["runs"][0])
            if index:
                cursor.insertBlock(fmt, first)
            else:
                cursor.setBlockFormat(fmt)
                cursor.setBlockCharFormat(first)
            for run in block["runs"]:
                cursor.insertText(run["text"], run_format(run))
        cursor.endEditBlock()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.input.setTextCursor(cursor)
        if not undoable:
            self.input.document().clearUndoRedoStacks()

    def open(self, snapshot, original_pdf=None):
        self.timer.stop()
        self.spelling.ignored.clear()
        self.loading = True
        self.snapshot = snapshot
        self.hover_block = -1
        existing = snapshot["payload"].get("manual_sheet")
        blocks = existing["blocks"] if existing else sheet_blocks(snapshot["payload"])
        self.source = existing["source"] if existing else sheet_signature(blocks)
        self.artwork = dict(existing.get("artwork", {})) if existing else {}
        try:
            if original_pdf and not existing:
                from app.ui.sheet_archive import archived_blocks

                self.load_pdf(original_pdf)
                blocks, self.artwork = archived_blocks(self.pdf, snapshot, blocks)
            self.populate(blocks)
            self.initial_manual = self.manual()
            self.render(original_pdf)
        finally:
            self.loading = False

    def changed(self):
        if not self.loading and self.snapshot is not None:
            self.timer.start()
            self.edited.emit()

    def refresh(self):
        self.timer.stop()
        try:
            self.render()
        except (RuntimeError, ValueError, OSError) as error:
            self.failed.emit(str(error))

    def load_pdf(self, content):
        self.pdf.close()
        self.buffer.close()
        self.buffer.setData(content)
        self.buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.pdf.load(self.buffer)
        if self.pdf.status() != QPdfDocument.Status.Ready:
            raise ValueError("Не удалось показать страницы сервисного листа.")

    def render(self, original_pdf=None):
        lines = []
        data = self.snapshot["payload"] | {"manual_sheet": self.manual()}
        content = build_service_sheet(self.snapshot | {"payload": data}, trace=lines)
        self.load_pdf(original_pdf or content)
        self.lines = lines
        self.images.clear()
        self.spans = []
        # QTextCursor counts UTF-16 code units, while Python indexes Unicode characters.
        previous = {}
        for line in lines:
            block = self.input.document().findBlockByNumber(line.block)
            text = block.text().replace("\u00a0", " ")
            start = previous.get(line.block, 0)
            visible = line.text.replace("\u00a0", " ")
            offset = text.find(visible, start) if visible else start
            if offset < 0:
                offset = start
            end = min(len(text), offset + len(visible))
            position = block.position() + qt_length(text[:offset])
            positions = [position]
            for char in text[offset:end]:
                positions.append(positions[-1] + qt_length(char))
            self.spans.append(positions)
            previous[line.block] = end
        self.update_range()
        self.viewport().update()
        self.locationChanged.emit()

    def cursor_changed(self):
        self.caret = True
        self.viewport().update()
        if not self.loading:
            self.locationChanged.emit()

    def scale(self):
        return max(0.1, (self.viewport().width() - 12) / FOLTZ_PAGE[0])

    def stride(self):
        return FOLTZ_PAGE[1] * self.scale() + 6

    def update_range(self):
        bar = self.verticalScrollBar()
        bar.setPageStep(self.viewport().height())
        bar.setRange(
            0, max(0, round(self.pdf.pageCount() * self.stride() + 6) - self.viewport().height())
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.images.clear()
        self.update_range()

    def scrollContentsBy(self, dx, dy):
        self.viewport().update()

    def page_at_scroll(self):
        return min(self.pdf.pageCount(), 1 + int(self.verticalScrollBar().value() / self.stride()))

    def active_line(self):
        cursor = self.input.textCursor()
        pos = cursor.position()
        for i in reversed(range(len(self.lines))):
            if self.spans[i][0] <= pos:
                return i
        return 0

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.fillRect(event.rect(), QColor("#45494e"))
        if not self.pdf.pageCount():
            painter.end()
            return
        scale, scroll = self.scale(), self.verticalScrollBar().value()
        cursor = self.input.textCursor()
        lo, hi = cursor.selectionStart(), cursor.selectionEnd()
        active = self.active_line() if self.lines else -1
        for page in range(self.page_at_scroll() - 1, self.pdf.pageCount()):
            y = 6 + page * self.stride() - scroll
            if y > self.viewport().height():
                break
            width, height = FOLTZ_PAGE
            dest = QRectF(6, y, width * scale, height * scale)
            if page not in self.images:
                if len(self.images) >= 5:
                    self.images.clear()
                dpi = self.devicePixelRatioF()
                self.images[page] = self.pdf.render(
                    page,
                    QSize(max(1, round(dest.width() * dpi)), max(1, round(dest.height() * dpi))),
                )
            painter.fillRect(dest, Qt.GlobalColor.white)
            painter.drawImage(dest, self.images[page])
            painter.save()
            painter.translate(6, y)
            painter.scale(scale, scale)
            for i, line in enumerate(self.lines):
                if line.page != page:
                    continue
                positions = self.spans[i]
                edges = line.edges
                start, end = max(lo, positions[0]), min(hi, positions[-1])
                if start < end:
                    left = edges[bisect_left(positions, start)]
                    right = edges[min(bisect_left(positions, end), len(edges) - 1)]
                    painter.fillRect(
                        QRectF(left, line.top, right - left, line.height), QColor(33, 114, 218, 88)
                    )
                for start, end in self.spelling.by_block.get(line.block, ()):
                    low, high = max(start, positions[0]), min(end, positions[-1])
                    if low >= high:
                        continue
                    left = edges[min(bisect_left(positions, low), len(edges) - 1)]
                    right = edges[min(bisect_left(positions, high), len(edges) - 1)]
                    y = line.top + line.height - 1
                    wave = QPainterPath(QPointF(left, y))
                    x, step = left, 1
                    while x < right:
                        x = min(x + 1.8, right)
                        wave.lineTo(x, y + step * 0.8)
                        step = -step
                    painter.setPen(QPen(RED, 1 / scale))
                    painter.drawPath(wave)
                if i == active and not cursor.hasSelection() and self.caret and self.hasFocus():
                    x = edges[min(bisect_left(positions, lo), len(positions) - 1, len(edges) - 1)]
                    painter.setPen(QPen(QColor("#101419"), 1.3 / scale))
                    painter.drawLine(QPointF(x, line.top), QPointF(x, line.top + line.height))
                if (i == 0 or self.lines[i - 1].block != line.block) and line.block in (
                    cursor.blockNumber(),
                    self.hover_block,
                ):
                    painter.setPen(QPen(QColor("#2774bd"), 1.4 / scale))
                    for dy in (-3, 0, 3):
                        painter.drawLine(
                            QPointF(TEXT_LEFT - 17, line.top + line.height / 2 + dy),
                            QPointF(TEXT_LEFT - 7, line.top + line.height / 2 + dy),
                        )
            painter.restore()
        painter.end()

    def hit(self, point, *, text_only=False):
        if self.timer.isActive():
            self.refresh()
        scroll_y = point.y() + self.verticalScrollBar().value() - 6
        page = max(0, min(self.pdf.pageCount() - 1, int(scroll_y / self.stride())))
        y = (scroll_y - page * self.stride()) / self.scale()
        x = (point.x() - 6) / self.scale()
        rows = [i for i, line in enumerate(self.lines) if line.page == page]
        if not rows:
            return None
        i = min(rows, key=lambda j: abs(y - self.lines[j].top - self.lines[j].height / 2))
        line, positions = self.lines[i], self.spans[i]
        if text_only and not (
            line.top <= y <= line.top + line.height and line.edges[0] <= x <= line.edges[-1]
        ):
            return None
        char = bisect_left(line.edges, x)
        if char and (char == len(line.edges) or x - line.edges[char - 1] < line.edges[char] - x):
            char -= 1
        handle = (
            TEXT_LEFT - 24 <= x < TEXT_LEFT - 2
            and (i == 0 or self.lines[i - 1].block != line.block)
            and line.top - 2 <= y <= line.top + line.height + 2
        )
        return positions[min(char, len(positions) - 1)], handle

    def contextMenuEvent(self, event):
        hit = self.hit(event.pos(), text_only=True)
        if hit and not hit[1]:
            menu = QMenu(self)
            if self.spelling.menu(hit[0], menu):
                menu.exec(event.globalPos())
            menu.deleteLater()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.lines:
            return
        hit = self.hit(event.position())
        if hit is None:
            return
        pos, handle = hit
        cursor = self.input.textCursor()
        cursor.setPosition(
            pos, SELECT if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else MOVE
        )
        self.input.setTextCursor(cursor)
        self.setFocus()
        if handle:
            cursor.beginEditBlock()
            self.drag = (cursor, event.position().y(), cursor.blockFormat().topMargin())
            self.viewport().setCursor(Qt.CursorShape.SizeVerCursor)

    def mouseMoveEvent(self, event):
        if not event.buttons() & Qt.MouseButton.LeftButton:
            hit = self.hit(event.position()) if self.lines else None
            self.hover_block = self.input.document().findBlock(hit[0]).blockNumber() if hit else -1
            self.viewport().setCursor(
                Qt.CursorShape.SizeVerCursor if hit and hit[1] else Qt.CursorShape.IBeamCursor
            )
            self.viewport().update()
            return
        if self.drag:
            cursor, y, gap = self.drag
            self.set_gap(cursor, gap + (event.position().y() - y) / self.scale())
            # QTextDocument groups contentsChanged until the drag's undo block ends.
            self.timer.start()
        else:
            hit = self.hit(event.position())
            if hit:
                cursor = self.input.textCursor()
                cursor.setPosition(hit[0], SELECT)
                self.input.setTextCursor(cursor)
            if event.position().y() < 0 or event.position().y() > self.viewport().height():
                bar = self.verticalScrollBar()
                bar.setValue(bar.value() + (-18 if event.position().y() < 0 else 18))

    def mouseReleaseEvent(self, event):
        if self.drag:
            self.drag[0].endEditBlock()
            self.drag = None
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)

    def mouseDoubleClickEvent(self, event):
        cursor = self.input.textCursor()
        cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        self.input.setTextCursor(cursor)

    def set_gap(self, cursor, gap):
        fmt = cursor.blockFormat()
        fmt.setTopMargin(max(0, min(360, gap)))
        cursor.setBlockFormat(fmt)

    def move_block(self, mm):
        cursor = self.input.textCursor()
        cursor.clearSelection()
        self.set_gap(cursor, cursor.blockFormat().topMargin() + mm * MM)
        self.setFocus()

    def set_size(self, size):
        fmt = QTextCharFormat()
        fmt.setFontPointSize(size)
        self.input.mergeCurrentCharFormat(fmt)
        self.setFocus()

    def keyPressEvent(self, event):
        key = event.key()
        cursor = self.input.textCursor()
        shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        if (
            key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Home, Qt.Key.Key_End)
            and not ctrl
            and self.lines
        ):
            if self.timer.isActive():
                self.refresh()
            i = self.active_line()
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                slot = min(
                    bisect_left(self.spans[i], cursor.position()), len(self.lines[i].edges) - 1
                )
                x = self.lines[i].edges[slot]
                i = max(0, min(len(self.lines) - 1, i + (-1 if key == Qt.Key.Key_Up else 1)))
                slot = min(bisect_left(self.lines[i].edges, x), len(self.spans[i]) - 1)
            else:
                slot = 0 if key == Qt.Key.Key_Home else -1
            cursor.setPosition(self.spans[i][slot], SELECT if shift else MOVE)
            self.input.setTextCursor(cursor)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not ctrl:
            fmt = QTextBlockFormat(cursor.blockFormat())
            fmt.setTopMargin(0)
            fmt.setProperty(KEEP, False)
            fmt.clearProperty(SOURCE)
            cursor.insertBlock(fmt, self.input.currentCharFormat())
            self.input.setTextCursor(cursor)
        elif event.matches(QKeySequence.StandardKey.Paste):
            self.input.insertPlainText(QApplication.clipboard().text().expandtabs(4))
        elif key == Qt.Key.Key_Escape:
            super().keyPressEvent(event)
            return
        else:
            self.input.keyPressEvent(event)
        event.accept()
        QTimer.singleShot(25, self.follow_cursor)

    def follow_cursor(self):
        if not self.lines or not self.isVisible():
            return
        if self.timer.isActive():
            self.refresh()
        line = self.lines[self.active_line()]
        bar = self.verticalScrollBar()
        top = round(6 + line.page * self.stride() + line.top * self.scale())
        bottom = round(top + line.height * self.scale())
        if top < bar.value():
            bar.setValue(top - 8)
        elif bottom > bar.value() + self.viewport().height():
            bar.setValue(bottom - self.viewport().height() + 8)


class SheetEditor(QWidget):
    done = Signal()

    def __init__(self, parent, directory):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Изменения на листе относятся только к печати"))
        self.canvas = SheetCanvas(self, directory)
        row = QHBoxLayout()
        layout.addLayout(row)
        self.size_field = QDoubleSpinBox()
        self.size_field.setRange(1, 144)
        self.size_field.setDecimals(2)
        self.size_field.setSuffix(" пт")
        self.size_field.setAccessibleName("Размер шрифта")
        self.size_field.setKeyboardTracking(False)
        self.size_field.valueChanged.connect(self.canvas.set_size)
        row.addWidget(self.size_field)

        def button(target, text, slot):
            b = QPushButton(text)
            b.setAutoDefault(False)
            b.clicked.connect(slot)
            target.addWidget(b)
            return b

        button(row, "А−", self.size_field.stepDown)
        button(row, "А+", self.size_field.stepUp)
        button(row, "Поднять", lambda: self.canvas.move_block(-1))
        button(row, "Опустить", lambda: self.canvas.move_block(1))
        row.addStretch()
        row2 = QHBoxLayout()
        layout.addLayout(row2)
        self.undo = button(row2, "Отменить", self.canvas.input.undo)
        self.redo = button(row2, "Повторить", self.canvas.input.redo)
        self.pages = QLabel()
        row2.addWidget(self.pages)
        row2.addStretch()
        button(row2, "Готово", self.done.emit)
        layout.addWidget(self.canvas, 1)
        self.canvas.locationChanged.connect(self.update_controls)
        self.canvas.input.undoAvailable.connect(self.undo.setEnabled)
        self.canvas.input.redoAvailable.connect(self.redo.setEnabled)

    def update_controls(self):
        canvas = self.canvas
        self.pages.setText(f"Страница {canvas.page_at_scroll()} из {canvas.pdf.pageCount()}")
        self.size_field.blockSignals(True)
        self.size_field.setValue(canvas.input.currentCharFormat().fontPointSize() or BODY_SIZE)
        self.size_field.blockSignals(False)
        self.undo.setEnabled(canvas.input.document().isUndoAvailable())
        self.redo.setEnabled(canvas.input.document().isRedoAvailable())
