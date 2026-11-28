"""Заказ-наряды: редактируемый черновик, фактический PDF и сохранённые экземпляры."""

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QBuffer,
    QIODevice,
    QMargins,
    QRectF,
    QSignalBlocker,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QKeySequence, QPageSize, QPainter, QShortcut
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.documents import (
    Documents,
    SheetSourceChanged,
    TotalMismatch,
    item_price_total,
    normalize_items,
)
from app.services.receipt_pdf import pdf_bytes, save_pdf
from app.services.records import amount_text
from app.services.service_sheet_pdf import sheet_signature
from app.ui.sheet_editor import SheetEditor
from app.ui.spelling import SpellField, TitleDelegate
from app.ui.vehicles import ERRORS, SafeDialog, plain_label, primary_button, vehicle_label

ITEM_FIELDS = ("title", "quantity", "price")


def print_pdf(document: QPdfDocument, printer: QPrinter):
    if document.status() != QPdfDocument.Status.Ready or not document.pageCount():
        raise ValueError("PDF не готов к печати. Откройте сохранённый экземпляр заново.")
    if not printer.isValid():
        raise OSError("Выбранный принтер недоступен. Выберите другой принтер.")
    first = max(0, printer.fromPage() - 1)
    last = min(document.pageCount(), printer.toPage() or document.pageCount())
    if first >= last:
        raise ValueError("Выберите существующие страницы документа.")
    printer.setFullPage(True)
    painter = QPainter()
    if not painter.begin(printer):
        raise OSError("Не удалось начать печать. Проверьте выбранный принтер.")
    try:
        for page in range(first, last):
            if page != first and not printer.newPage():
                raise OSError("Принтер не смог подготовить следующую страницу.")
            points = document.pagePointSize(page)
            image = document.render(
                page, QSize(round(points.width() * 300 / 72), round(points.height() * 300 / 72))
            )
            if image.isNull():
                raise OSError("Не удалось подготовить страницу к печати.")
            rectangle = printer.pageLayout().fullRectPixels(printer.resolution())
            painter.drawImage(QRectF(rectangle), image)
    finally:
        finished = painter.end()
    if not finished or printer.printerState() in (
        QPrinter.PrinterState.Error,
        QPrinter.PrinterState.Aborted,
    ):
        raise OSError("Не удалось завершить отправку задания на печать.")


class DocumentSettingsDialog(SafeDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Реквизиты для заказ-нарядов")
        self.resize(540, 390)
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain_label(
                "Введите сведения сервиса, которые должны появляться в новых документах. "
                "Сохранённые экземпляры останутся прежними."
            )
        )
        form = QFormLayout()
        settings = service.settings()
        self.fields = {}
        for key, title in (("name", "Название"), ("address", "Адрес"), ("phone", "Телефон")):
            self.fields[key] = QLineEdit(settings[key])
            form.addRow(title, self.fields[key])
        self.requisites = QPlainTextEdit(settings["requisites"])
        form.addRow("Другие реквизиты", self.requisites)
        layout.addLayout(form)
        row = QHBoxLayout()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        row.addStretch()
        save = primary_button("Сохранить")
        save.clicked.connect(self.save)
        row.addWidget(save)
        layout.addLayout(row)
        self.prepare_guard()

    def state(self):
        return {key: field.text() for key, field in self.fields.items()} | {
            "requisites": self.requisites.toPlainText(),
        }

    def save(self):
        try:
            self.service.save_settings(self.state())
        except ERRORS as error:
            QMessageBox.warning(self, "Не удалось сохранить реквизиты", str(error))
            return
        self.accept()


class DocumentDialog(SafeDialog):
    notes_saved = Signal()

    def __init__(self, records, repair_id, parent=None):
        super().__init__(parent)
        self.service = Documents(records.path)
        self.records, self.repair_id = records, repair_id
        self.busy = True
        self.manual_sheet = None
        self.sheet_loaded = False
        self.previous_index = -1
        self.current_document = None
        self.content = None
        vehicle = self.records.vehicle(self.source_repair()["vehicle_id"])
        self.setWindowTitle(f"Документ / Печать · {vehicle_label(vehicle) or 'Автомобиль'}")
        self.resize(1180, 790)
        self.setMinimumSize(850, 580)
        layout = QVBoxLayout(self)
        self.document_status = plain_label("")
        self.document_status.setWordWrap(False)
        self.document_status.setObjectName("badge")
        self.document_status.setAccessibleName("Статус заказ-наряда")
        layout.addWidget(self.document_status, alignment=Qt.AlignmentFlag.AlignLeft)
        history_row = QHBoxLayout()
        history_label = plain_label("Оформленные документы")
        history_row.addWidget(history_label)
        self.history = QComboBox()
        self.history.currentIndexChanged.connect(self.switch_history)
        history_label.setBuddy(self.history)
        history_row.addWidget(self.history, 1)
        self.new_button = QPushButton("Новый документ")
        self.new_button.clicked.connect(self.new_receipt)
        history_row.addWidget(self.new_button)
        layout.addLayout(history_row)
        split = QSplitter(Qt.Orientation.Horizontal)
        editor = QWidget()
        left = QVBoxLayout(editor)
        left.setContentsMargins(0, 0, 8, 0)
        self.kind = QComboBox()
        self.kind.addItem("Старый формат: сервисный лист «Фольц Мастер»", "service_sheet")
        self.kind.addItem("Новый формат: заказ-наряд", "receipt")
        self.kind.currentIndexChanged.connect(self.changed)
        left.addWidget(self.kind)
        left.addWidget(plain_label("Ход работ"))
        self.description = SpellField(records.path.parent)
        self.description.textChanged.connect(self.changed)
        left.addWidget(self.description, 1)
        self.recommendations_panel = QWidget()
        recommendations_layout = QVBoxLayout(self.recommendations_panel)
        recommendations_layout.setContentsMargins(0, 0, 0, 0)
        recommendations_layout.addWidget(plain_label("Рекомендации"))
        self.recommendations = SpellField(records.path.parent)
        self.recommendations.setPlaceholderText(
            "Сохраняются в ремонте и переносятся в следующий визит этой машины."
        )
        self.recommendations.setMaximumHeight(self.recommendations.minimumSizeHint().height())
        self.recommendations.textChanged.connect(self.changed)
        recommendations_layout.addWidget(self.recommendations)
        recommendations_layout.addWidget(
            plain_label("Шапка и контакты — из шаблона «Фольц Мастер».")
        )
        left.addWidget(self.recommendations_panel, 1)
        self.items_panel = QWidget()
        positions = QVBoxLayout(self.items_panel)
        positions.setContentsMargins(0, 0, 0, 0)
        positions.addWidget(plain_label("Цена необязательна"))
        self.item_tables = {}
        self.item_subtotals = {}
        for kind, heading, add_text, shortcut in (
            ("part", "Запчасти", "Добавить запчасть", "Alt+1"),
            ("work", "Работы", "Добавить работу", "Alt+2"),
        ):
            summary = QHBoxLayout()
            label = plain_label(heading)
            font = label.font()
            font.setBold(True)
            label.setFont(font)
            summary.addWidget(label)
            summary.addStretch()
            self.item_subtotals[kind] = plain_label("")
            summary.addWidget(self.item_subtotals[kind])
            positions.addLayout(summary)

            table = QTableWidget(0, len(ITEM_FIELDS))
            table.setItemDelegateForColumn(0, TitleDelegate(records.path.parent, table))
            table.setAccessibleName(heading)
            table.setHorizontalHeaderLabels(("Наименование", "Кол-во", "Цена, ₽"))
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            table.setColumnWidth(1, 72)
            table.setColumnWidth(2, 96)
            table.itemChanged.connect(self.changed)
            self.item_tables[kind] = table
            positions.addWidget(table, 1)

            buttons = QHBoxLayout()
            add = QPushButton(add_text)
            add.setAutoDefault(False)
            add.setToolTip(f"{add_text} ({shortcut})")
            add.clicked.connect(lambda checked=False, key=kind: self.add_item(key))
            add_shortcut = QShortcut(QKeySequence(shortcut), add)
            add_shortcut.setAutoRepeat(False)
            add_shortcut.activated.connect(add.click)
            buttons.addWidget(add)
            remove = QPushButton("Убрать строку")
            remove.setAutoDefault(False)
            remove.clicked.connect(lambda checked=False, target=table: self.remove_item(target))
            buttons.addWidget(remove)
            buttons.addStretch()
            positions.addLayout(buttons)
        left.addWidget(self.items_panel, 2)
        self.total = plain_label("")
        left.addWidget(self.total)
        split.addWidget(editor)
        self.pdf = QPdfDocument(self)
        self.buffer = None
        self.view = QPdfView()
        self.view.setDocument(self.pdf)
        self.view.setDocumentMargins(QMargins(6, 6, 6, 6))
        self.view.setPageSpacing(6)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.preview_panel = QStackedWidget()
        self.preview_note = plain_label("")
        self.preview_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_note.setContentsMargins(18, 12, 18, 12)
        self.preview_panel.addWidget(self.preview_note)
        self.preview_panel.addWidget(self.view)
        self.sheet_editor = SheetEditor(self, records.path.parent)
        self.sheet_editor.done.connect(self.finish_sheet)
        self.sheet_editor.canvas.edited.connect(self.sheet_changed)
        self.sheet_editor.canvas.failed.connect(self.sheet_error)
        self.preview_panel.addWidget(self.sheet_editor)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.edit_sheet = QPushButton("Редактировать лист")
        self.edit_sheet.setAutoDefault(False)
        self.edit_sheet.clicked.connect(self.start_sheet)
        right_layout.addWidget(self.edit_sheet, alignment=Qt.AlignmentFlag.AlignLeft)
        right_layout.addWidget(self.preview_panel, 1)
        split.addWidget(right)
        split.setSizes([530, 650])
        layout.addWidget(split, 1)
        self.message = plain_label("")
        layout.addWidget(self.message)
        bottom = QHBoxLayout()
        settings = QPushButton("Реквизиты сервиса")
        settings.clicked.connect(self.settings)
        bottom.addWidget(settings)
        self.draft_button = QPushButton("Сохранить черновик")
        self.draft_button.clicked.connect(lambda: self.save(close=False))
        bottom.addWidget(self.draft_button)
        bottom.addStretch()
        self.export_button = QPushButton("Сохранить PDF")
        self.export_button.clicked.connect(self.export)
        bottom.addWidget(self.export_button)
        self.create_button = QPushButton("Оформить без печати")
        self.create_button.setAutoDefault(False)
        self.create_button.clicked.connect(self.create)
        bottom.addWidget(self.create_button)
        self.print_button = primary_button("")
        self.print_button.setAutoDefault(False)
        self.print_button.clicked.connect(self.print_or_create)
        bottom.addWidget(self.print_button)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(300)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.preview)
        self.finished.connect(self.preview_timer.stop)
        self.load_history()
        self.load_selection(self.history.currentIndex())
        self.prepare_guard()

    def load_history(self, selected=None, revision=0):
        self.deleted_repair = bool(self.source_repair()["deleted_at"])
        documents = self.service.history(self.repair_id)
        self.history.blockSignals(True)
        self.history.clear()
        selected_index = 0 if documents else -1
        for index, document in enumerate(documents):
            kind = "Заказ-наряд"
            if document["number"].startswith("СЛ-"):
                kind = "Сервисный лист"
            stamp = datetime.fromisoformat(document["created_at"]).strftime("%d.%m.%Y %H:%M")
            label = document["number"]
            if document["revision"]:
                label += f" · копия {document['revision']}"
            self.history.addItem(f"{label} · {stamp} · {kind}", document["id"])
            self.history.setItemData(index, document["revision"], Qt.ItemDataRole.UserRole + 1)
            if (document["id"], document["revision"]) == (selected, revision):
                selected_index = index
        self.history.setPlaceholderText("Выберите документ" if documents else "Документов пока нет")
        self.history.setEnabled(bool(documents))
        index = selected_index
        self.history.setCurrentIndex(index)
        self.history.blockSignals(False)
        self.previous_index = index

    def new_receipt(self):
        self.history.setCurrentIndex(-1)

    def switch_history(self, index):
        if self.busy:
            return
        target = self.history.itemData(index)
        revision = self.history.itemData(index, Qt.ItemDataRole.UserRole + 1) or 0
        if self.state() != self.original:
            choice = self.unsaved_choice()
            if choice == QMessageBox.StandardButton.Cancel or (
                choice == QMessageBox.StandardButton.Save and not self.save(close=False)
            ):
                with QSignalBlocker(self.history):
                    self.history.setCurrentIndex(self.previous_index)
                return
            if target is not None:
                self.load_history(target, revision)
                index = self.history.currentIndex()
            else:
                with QSignalBlocker(self.history):
                    self.history.setCurrentIndex(-1)
                index = -1
        self.load_selection(index)

    def unsaved_choice(self):
        buttons = QMessageBox.StandardButton
        if not self.manual_sheet:
            return QMessageBox.question(
                self,
                "Несохранённые изменения",
                "Сохранить текущий черновик?",
                buttons.Save | buttons.Discard | buttons.Cancel,
                buttons.Save,
            )
        box = QMessageBox(self)
        box.setWindowTitle("Несохранённые изменения")
        box.setText("Сохранить текущие изменения документа?")
        save = box.addButton("Сохранить", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Не сохранять", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Продолжить редактирование", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return {save: buttons.Save, discard: buttons.Discard}.get(
            box.clickedButton(), buttons.Cancel
        )

    def reject(self):
        if self.state() != self.original:
            choice = self.unsaved_choice()
            if choice == QMessageBox.StandardButton.Cancel:
                return
            if choice == QMessageBox.StandardButton.Save:
                self.save()
                return
        QDialog.reject(self)

    def load_selection(self, index):
        try:
            self.apply_selection(index)
        except (*ERRORS, RuntimeError) as error:
            self.busy = False
            self.document_status.setText("Заказ-наряд недоступен")
            self.clear_preview("Заказ-наряд не удалось открыть.")
            self.original = self.state()
            QMessageBox.warning(self, "Не удалось открыть документ", str(error))

    def apply_selection(self, index):
        self.busy = True
        self.preview_timer.stop()
        self.previous_index = index
        self.sheet_loaded = False
        self.manual_sheet = None
        self.sheet_editor.canvas.timer.stop()
        revision = self.history.itemData(index, Qt.ItemDataRole.UserRole + 1) or 0
        document_id = self.history.itemData(index)
        saved = document_id is not None
        editable = not saved and not self.deleted_repair
        self.current_document = None
        self.clear_preview()
        self.new_button.setEnabled(saved and not self.deleted_repair)
        for widget in (self.kind, self.description, self.items_panel, self.recommendations_panel):
            widget.setEnabled(editable)
        for button in (self.draft_button, self.create_button):
            button.setVisible(editable)
            button.setEnabled(editable)
        self.export_button.setVisible(saved)
        self.print_button.setVisible(editable or saved)
        self.print_button.setEnabled(editable)
        self.print_button.setText("Печать" if saved else "Оформить и печатать")
        self.print_button.setToolTip(
            "Проверьте документ здесь. В окне Windows выберите принтер."
            if saved
            else "Сохранить документ с номером и открыть окно печати Windows."
        )
        self.current_document = self.service.get(document_id, revision) if saved else None
        if self.current_document:
            self.document_status.setText(f"Оформлена · {self.current_document['number']}")
            if revision:
                self.document_status.setText(
                    f"Оформлена · {self.current_document['number']} · копия {revision}"
                )
            payload = self.current_document["payload"]
            self.manual_sheet = payload.get("manual_sheet")
            values = {
                "kind": self.current_document["kind"],
                "layout": payload.get("layout", "receipt"),
                "recommendations": payload.get("recommendations", ""),
                "description": payload["description"],
                "items": [
                    {
                        "kind": item["kind"],
                        "title": item["title"],
                        "quantity": str(item["quantity_milli"] / 1000),
                        "price": amount_text(item["unit_price_kopecks"]),
                    }
                    for item in payload["items"]
                ],
            }
        else:
            self.document_status.setText("Черновик" if editable else "Заказ-нарядов нет")
            repair = self.source_repair()
            self.expected_notes = repair
            with_repair = self.service.draft(self.repair_id)
            with_repair = with_repair or {
                "kind": "simple",
                "layout": "service_sheet",
                "description": repair["description"],
                "recommendations": repair["recommendations"],
                "items": json.loads(repair["document_items"]),
            }
            values = {"recommendations": repair["recommendations"]} | with_repair
        if not saved:
            self.manual_sheet = values.get("manual_sheet")
        selected_layout = values.get("layout", "receipt")
        self.kind.setCurrentIndex(max(0, self.kind.findData(selected_layout)))
        self.description.setPlainText(values["description"])
        self.recommendations.setPlainText(values.get("recommendations", ""))
        for table in self.item_tables.values():
            table.setRowCount(0)
        for row in values["items"]:
            self.add_item(row.get("kind", "work"), values=row)
        self.original = self.state()
        self.busy = False
        self.update_total()
        self.sheet_controls()
        if saved:
            self.load_pdf(self.service.pdf_content(self.current_document["id"], revision))
            self.message.setText("В окне печати Windows выберите принтер.")
        elif editable:
            self.message.setText("Документ получит номер при оформлении.")
            self.preview()
        else:
            self.clear_preview(
                "Для этого ремонта ещё нет оформленных заказ-нарядов. "
                "Чтобы создать заказ-наряд, восстановите ремонт."
            )
            self.message.clear()

    def source_repair(self):
        from app.database.connection import connect

        with connect(self.records.path) as connection:
            repair = connection.execute(
                "SELECT * FROM repairs WHERE id=?", (self.repair_id,)
            ).fetchone()
        if repair is None:
            raise ValueError("Ремонт не найден.")
        return dict(repair)

    def add_item(self, kind, *, values=None):
        table = self.item_tables[kind]
        row = table.rowCount()
        defaults = {"title": "", "quantity": "1", "price": ""}
        with QSignalBlocker(table):
            table.insertRow(row)
            for column, field in enumerate(ITEM_FIELDS):
                value = defaults[field] if values is None else values.get(field, defaults[field])
                table.setItem(
                    row, column, QTableWidgetItem(str(value) if value is not None else "")
                )
        if values is None:
            table.setFocus()
            table.setCurrentCell(row, 0)
            table.editItem(table.item(row, 0))
        self.changed()

    def remove_item(self, table):
        row = table.currentRow()
        if row >= 0:
            table.removeRow(row)
            self.changed()

    def commit_item_editor(self):
        for table in self.item_tables.values():
            editor = table.indexWidget(table.currentIndex())
            if editor is not None:
                table.commitData(editor)
                table.setFocus()
                break

    def item_rows(self, kind):
        table = self.item_tables[kind]
        result = []
        for row in range(table.rowCount()):
            fields = {field: table.item(row, col).text() for col, field in enumerate(ITEM_FIELDS)}
            # «Добавить строку» оставляет количество 1 даже у пустой позиции.
            if (
                fields["title"].strip()
                or fields["price"].strip()
                or fields["quantity"].strip()
                not in (
                    "",
                    "1",
                )
            ):
                result.append({"kind": kind} | fields)
        return result

    def state(self):
        items = [row for kind in self.item_tables for row in self.item_rows(kind)]
        return {
            "kind": "detailed" if items else "simple",
            "layout": self.kind.currentData(),
            "recommendations": self.recommendations.toPlainText(),
            "description": self.description.toPlainText(),
            "items": items,
        } | (
            {"manual_sheet": self.manual_sheet}
            if self.manual_sheet is not None and self.kind.currentData() == "service_sheet"
            else {}
        )

    def editing_sheet(self):
        return self.preview_panel.currentWidget() is self.sheet_editor

    def copy_changed(self):
        return self.current_document is not None and self.manual_sheet != self.current_document[
            "payload"
        ].get("manual_sheet")

    def sheet_controls(self):
        editing = self.editing_sheet()
        saved = self.current_document is not None
        self.edit_sheet.setVisible(
            self.kind.currentData() == "service_sheet"
            and not editing
            and (saved or not self.deleted_repair)
        )
        self.edit_sheet.setText("Редактировать копию" if saved else "Редактировать лист")
        for widget in (self.kind, self.description, self.items_panel, self.recommendations_panel):
            widget.setEnabled(not saved and not self.deleted_repair and not editing)
        if saved:
            dirty = self.copy_changed()
            self.draft_button.setVisible(dirty)
            self.draft_button.setEnabled(dirty)
            self.draft_button.setText("Сохранить копию")
            self.print_button.setText("Сохранить копию и печатать" if dirty else "Печать")
            self.export_button.setText("Копия в PDF" if dirty else "Сохранить PDF")
            self.export_button.setEnabled(self.content is not None or dirty)
        else:
            self.draft_button.setText("Сохранить черновик")

    def start_sheet(self):
        self.preview_timer.stop()
        self.commit_item_editor()
        try:
            archive = self.current_document
            snapshot = archive or self.service.create(
                self.repair_id, **self.state(), allow_difference=True, preview=True
            )
            scroll = self.view.verticalScrollBar()
            fraction = scroll.value() / max(1, scroll.maximum())
            canvas = self.sheet_editor.canvas
            if not self.sheet_loaded:
                if self.manual_sheet:
                    snapshot = snapshot | {
                        "payload": snapshot["payload"] | {"manual_sheet": self.manual_sheet}
                    }
                canvas.open(snapshot, self.content if archive and not self.copy_changed() else None)
                self.sheet_loaded = True
            self.preview_panel.setCurrentWidget(self.sheet_editor)
            self.sheet_controls()
            canvas.update_range()
            canvas.verticalScrollBar().setValue(
                round(fraction * canvas.verticalScrollBar().maximum())
            )
            canvas.setFocus()
            self.sheet_editor.update_controls()
        except (*ERRORS, RuntimeError) as error:
            QMessageBox.warning(self, "Не удалось открыть редактор", str(error))

    def sheet_changed(self):
        if self.busy or not self.sheet_loaded:
            return
        canvas = self.sheet_editor.canvas
        manual = canvas.manual()
        if manual != canvas.initial_manual:
            self.manual_sheet = manual
        else:
            self.manual_sheet = canvas.snapshot["payload"].get("manual_sheet")
        self.sheet_controls()

    def sheet_error(self, message):
        self.message.setText(f"Проверьте печатный лист. {message}")

    def finish_sheet(self):
        canvas = self.sheet_editor.canvas
        scroll = canvas.verticalScrollBar()
        fraction = scroll.value() / max(1, scroll.maximum())
        self.preview()
        if self.manual_sheet is None:
            self.sheet_loaded = False
        self.sheet_controls()
        bar = self.view.verticalScrollBar()
        bar.setValue(round(fraction * bar.maximum()))

    def update_total(self):
        rows = []
        for kind, label in self.item_subtotals.items():
            group = self.item_rows(kind)
            rows.extend(group)
            try:
                subtotal = item_price_total(normalize_items(group)) if group else None
                label.setText("" if subtotal is None else f"{amount_text(subtotal)} ₽")
            except ValueError:
                label.setText("Проверьте позиции")
        try:
            items = normalize_items(rows) if rows else []
            total = item_price_total(items)
            missing_prices = any(item["unit_price_kopecks"] is None for item in items)
            caption = "Итог по указанным ценам" if missing_prices else "Итог позиций"
            self.total.setText("" if total is None else f"{caption}: {amount_text(total)} ₽")
        except ValueError as error:
            self.total.setText(str(error))

    def changed(self, *_):
        if self.busy:
            return
        self.sheet_controls()
        if self.editing_sheet():
            return
        if not self.manual_sheet:
            self.sheet_loaded = False
        self.clear_preview("Обновляем предпросмотр…")
        self.document_status.setText("Черновик")
        self.message.setText("Документ получит номер при оформлении.")
        self.update_total()
        self.preview_timer.start()

    def clear_preview(self, text="Загружаем предпросмотр…"):
        self.pdf.close()
        self.content = None
        self.export_button.setEnabled(False)
        self.preview_note.setText(text)
        self.preview_panel.setCurrentWidget(self.preview_note)

    def load_pdf(self, content):
        self.pdf.close()
        if self.buffer:
            self.buffer.close()
            self.buffer.deleteLater()
        self.buffer = QBuffer(self)
        self.buffer.setData(content)
        self.buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.pdf.load(self.buffer)
        if self.pdf.status() != QPdfDocument.Status.Ready:
            raise ValueError("Не удалось открыть предпросмотр PDF.")
        self.content = content
        # Выдача/печать только сохранённого экземпляра с постоянным номером.
        saved = self.current_document is not None
        self.export_button.setEnabled(saved)
        self.print_button.setEnabled(True)
        self.preview_panel.setCurrentWidget(self.view)
        self.sheet_controls()

    def preview(self):
        try:
            if self.current_document:
                doc = self.current_document
                if self.copy_changed():
                    self.load_pdf(
                        pdf_bytes(
                            doc | {"payload": doc["payload"] | {"manual_sheet": self.manual_sheet}}
                        )
                    )
                else:
                    self.load_pdf(self.service.pdf_content(doc["id"], doc["revision"]))
                return
            draft = self.service.create(
                self.repair_id, **self.state(), allow_difference=True, preview=True
            )
            self.load_pdf(pdf_bytes(draft))
        except (*ERRORS, RuntimeError) as error:
            self.clear_preview(f"Предпросмотр недоступен.\n{error}")

    def create(self):
        if self.current_document or self.deleted_repair:
            return False
        self.commit_item_editor()

        def issue(value, expected):
            options = {"consume_draft": True, "expected_notes": expected}
            while True:
                try:
                    return self.service.create(self.repair_id, **value, **options)
                except SheetSourceChanged as error:
                    box = QMessageBox(self)
                    box.setWindowTitle("Изменились данные для печати")
                    box.setText("Какой сервисный лист оформить?")
                    keep = box.addButton("Оставить мой лист", QMessageBox.ButtonRole.AcceptRole)
                    rebuild = box.addButton(
                        "Собрать из обновлённых данных", QMessageBox.ButtonRole.ActionRole
                    )
                    box.addButton("Продолжить редактирование", QMessageBox.ButtonRole.RejectRole)
                    box.exec()
                    if box.clickedButton() == keep:
                        options["keep_manual"] = sheet_signature(error.blocks)
                        continue
                    if box.clickedButton() == rebuild:
                        self.start_sheet()
                        if self.editing_sheet():
                            canvas = self.sheet_editor.canvas
                            canvas.source = sheet_signature(error.blocks)
                            canvas.populate(error.blocks, undoable=True)
                            canvas.refresh()
                    return False
                except TotalMismatch as error:
                    if (
                        QMessageBox.question(
                            self,
                            "Проверьте итог",
                            str(error),
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.No,
                        )
                        != QMessageBox.StandardButton.Yes
                    ):
                        return False
                    options["allow_difference"] = True

        try:
            result = self.save_shared_fields(self.state(), self.expected_notes, issue)
            if not result:
                return False
        except (*ERRORS, RuntimeError) as error:
            QMessageBox.warning(self, "Не удалось оформить документ", str(error))
            return False
        self.current_document = result
        self.notes_saved.emit()
        self.preview_timer.stop()
        self.create_button.setEnabled(False)
        self.clear_preview()
        try:
            self.load_history(result["id"])
            self.load_selection(self.history.currentIndex())
        except (*ERRORS, RuntimeError) as error:
            QMessageBox.warning(self, f"Заказ-наряд {result['number']} оформлен", str(error))
        return self.content is not None

    def prepare_saved_output(self):
        if self.copy_changed() and not self.save(close=False):
            return False
        doc = self.current_document
        try:
            self.load_pdf(self.service.pdf_content(doc["id"], doc["revision"]))
        except (*ERRORS, RuntimeError) as error:
            self.clear_preview(str(error))
            QMessageBox.warning(self, "Не удалось открыть сохранённый PDF", str(error))
            return False
        return True

    def print_or_create(self):
        if (self.current_document or self.create()) and self.prepare_saved_output():
            self.print_document()

    def save(self, checked=False, *, close=True):
        self.commit_item_editor()
        try:
            if self.copy_changed():
                document_id = self.current_document["id"]
                revision = self.service.save_copy(document_id, self.manual_sheet)
                self.load_history(document_id, revision)
                self.load_selection(self.history.currentIndex())
                if self.content is None:
                    return False
            elif not self.current_document:
                merged = self.save_shared_fields(
                    self.state(),
                    self.expected_notes,
                    lambda proposed, expected: self.service.save_draft(
                        self.repair_id, proposed, expected_notes=expected
                    ),
                )
                if merged is None:
                    return False
                self.expected_notes = merged
                self.description.setPlainText(merged["description"])
                self.recommendations.setPlainText(merged["recommendations"])
                self.notes_saved.emit()
            self.original = self.state()
        except (*ERRORS, RuntimeError) as error:
            QMessageBox.warning(self, "Не удалось сохранить документ", str(error))
            return False
        if close:
            self.accept()
        elif not self.current_document:
            self.document_status.setText("Черновик сохранён")
            self.message.setText("Документ получит номер при оформлении.")
        return True

    def settings(self):
        if (
            DocumentSettingsDialog(self.service, self).exec()
            and not self.current_document
            and not self.deleted_repair
        ):
            self.preview_timer.stop()
            self.preview()

    def export(self):
        if not self.current_document or not self.prepare_saved_output():
            return
        name = self.current_document["number"]
        if self.current_document["revision"]:
            name += f"-копия-{self.current_document['revision']}"
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить документ",
            f"{name}.pdf",
            "PDF (*.pdf)",
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != ".pdf":
            path = path.with_suffix(".pdf")
        overwrite = False
        if path.exists():
            overwrite = (
                QMessageBox.question(
                    self,
                    "Заменить файл?",
                    f"Заменить существующий файл {path.name}?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                == QMessageBox.StandardButton.Yes
            )
            if not overwrite:
                return
        try:
            save_pdf(self.content, path, overwrite=overwrite)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Не удалось сохранить PDF", str(error))

    def print_document(self):
        if not self.current_document or not self.content:
            return
        if not self.pdf.pageCount():
            QMessageBox.warning(
                self, "Не удалось напечатать", "Откройте сохранённый экземпляр заново."
            )
            return
        if not QPrinterInfo.availablePrinterNames():
            QMessageBox.warning(
                self,
                "Нет доступных принтеров",
                "Добавьте принтер в Windows и проверьте службу «Диспетчер печати». "
                "Заказ-наряд можно выгрузить кнопкой «Сохранить PDF».",
            )
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        printer.setResolution(300)
        dialog = QPrintDialog(printer, self)
        dialog.setMinMax(1, self.pdf.pageCount())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                print_pdf(self.pdf, printer)
            except (OSError, ValueError) as error:
                QMessageBox.warning(
                    self,
                    "Не удалось напечатать",
                    f"{error}\n\nПринтер: {printer.printerName() or 'не выбран'}. "
                    "Проверьте подключение, бумагу и очередь печати в Windows. "
                    "Заказ-наряд можно выгрузить кнопкой «Сохранить PDF».",
                )
            else:
                self.message.setText(
                    "Задание отправлено на печать. Если лист не вышел, проверьте очередь в Windows."
                )

        else:
            self.message.setText(
                f"Печать отменена. Заказ-наряд {self.current_document['number']} сохранён."
            )
