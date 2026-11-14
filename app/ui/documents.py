"""Квитанции: редактируемый черновик, фактический PDF и сохранённые экземпляры."""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QRectF, QSize, Qt
from PySide6.QtGui import QPageSize, QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.documents import Documents, TotalMismatch, normalize_items
from app.services.receipt_pdf import pdf_bytes, save_pdf
from app.services.records import amount_text
from app.ui.vehicles import ERRORS, SafeDialog, plain_label


def print_pdf(document: QPdfDocument, printer: QPrinter):
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
        painter.end()
    if printer.printerState() == QPrinter.PrinterState.Error:
        raise OSError("Принтер сообщил об ошибке печати.")


class DocumentSettingsDialog(SafeDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Реквизиты для квитанций")
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
        save = QPushButton("Сохранить")
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
    def __init__(self, records, repair_id, parent=None):
        super().__init__(parent)
        self.service = Documents(records.path)
        self.records, self.repair_id = records, repair_id
        self.busy = True
        self.previous_index = 0
        self.current_document = None
        self.content = None
        self.confirmed_state = None
        self.setWindowTitle("Квитанция / Печать")
        self.resize(1180, 790)
        self.setMinimumSize(850, 580)
        layout = QVBoxLayout(self)
        self.history = QComboBox()
        self.history.currentIndexChanged.connect(self.switch_history)
        layout.addWidget(self.history)
        split = QSplitter(Qt.Orientation.Horizontal)
        editor = QWidget()
        left = QVBoxLayout(editor)
        left.setContentsMargins(0, 0, 8, 0)
        self.kind = QComboBox()
        self.kind.addItem("Простая: описание и общая сумма", "simple")
        self.kind.addItem("Подробная: работы и запчасти", "detailed")
        self.kind.currentIndexChanged.connect(self.changed)
        left.addWidget(self.kind)
        left.addWidget(plain_label("Описание для документа"))
        self.description = QPlainTextEdit()
        self.description.textChanged.connect(self.changed)
        left.addWidget(self.description, 1)
        self.items_panel = QWidget()
        positions = QVBoxLayout(self.items_panel)
        positions.setContentsMargins(0, 0, 0, 0)
        self.items = QTableWidget(0, 4)
        self.items.setHorizontalHeaderLabels(("Тип", "Наименование", "Кол-во", "Цена, ₽"))
        self.items.horizontalHeader().setStretchLastSection(True)
        self.items.setColumnWidth(0, 95)
        self.items.setColumnWidth(1, 180)
        self.items.setColumnWidth(2, 65)
        self.items.itemChanged.connect(self.changed)
        positions.addWidget(self.items, 1)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("Добавить позицию")
        self.add_button.clicked.connect(self.add_item)
        buttons.addWidget(self.add_button)
        remove = QPushButton("Убрать позицию")
        remove.clicked.connect(self.remove_item)
        buttons.addWidget(remove)
        positions.addLayout(buttons)
        left.addWidget(self.items_panel, 1)
        self.total = plain_label("")
        left.addWidget(self.total)
        actions = QHBoxLayout()
        self.preview_button = QPushButton("Предпросмотр")
        self.preview_button.clicked.connect(self.preview)
        actions.addWidget(self.preview_button)
        self.create_button = QPushButton("Сохранить экземпляр")
        self.create_button.clicked.connect(self.create)
        actions.addWidget(self.create_button)
        left.addLayout(actions)
        split.addWidget(editor)
        self.pdf = QPdfDocument(self)
        self.buffer = None
        self.view = QPdfView()
        self.view.setDocument(self.pdf)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        split.addWidget(self.view)
        split.setSizes([530, 650])
        layout.addWidget(split, 1)
        self.message = plain_label(
            "Предпросмотр не резервирует номер. Для выдачи сохраните экземпляр."
        )
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
        self.print_button = QPushButton("Печать")
        self.print_button.clicked.connect(self.print_document)
        bottom.addWidget(self.print_button)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        self.load_history()
        self.load_selection(0)
        self.busy = False
        self.prepare_guard()

    def load_history(self, selected=None):
        self.history.blockSignals(True)
        self.history.clear()
        self.deleted_repair = bool(self.source_repair()["deleted_at"])
        if not self.deleted_repair:
            self.history.addItem("Новый документ / черновик", None)
        for document in self.service.history(self.repair_id):
            kind = "Простая" if document["kind"] == "simple" else "Подробная"
            stamp = datetime.fromisoformat(document["created_at"]).strftime("%d.%m.%Y %H:%M")
            self.history.addItem(f"{document['number']} · {stamp} · {kind}", document["id"])
        index = max(0, self.history.findData(selected)) if selected else 0
        self.history.setCurrentIndex(index)
        self.history.blockSignals(False)
        self.previous_index = index

    def switch_history(self, index):
        if self.busy:
            return
        if self.state() != self.original:
            choice = QMessageBox.question(
                self,
                "Сохранить черновик?",
                "Перед переключением сохранить текущий черновик?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                self.history.blockSignals(True)
                self.history.setCurrentIndex(self.previous_index)
                self.history.blockSignals(False)
                return
            if choice == QMessageBox.StandardButton.Save and not self.save(close=False):
                self.history.blockSignals(True)
                self.history.setCurrentIndex(self.previous_index)
                self.history.blockSignals(False)
                return
        self.load_selection(index)

    def load_selection(self, index):
        try:
            self.apply_selection(index)
        except (*ERRORS, RuntimeError) as error:
            self.busy = False
            self.clear_preview()
            self.original = self.state()
            QMessageBox.warning(self, "Не удалось открыть документ", str(error))

    def apply_selection(self, index):
        self.busy = True
        document_id = self.history.itemData(index)
        self.current_document = self.service.get(document_id) if document_id else None
        if self.current_document:
            payload = self.current_document["payload"]
            values = {
                "kind": self.current_document["kind"],
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
            with_repair = self.service.draft(self.repair_id)
            with_repair = with_repair or {
                "kind": "simple",
                "description": self.source_repair()["description"],
                "items": [],
            }
            values = with_repair
        self.kind.setCurrentIndex(max(0, self.kind.findData(values["kind"])))
        self.description.setPlainText(values["description"])
        self.items.setRowCount(0)
        for row in values["items"]:
            self.add_item(values=row)
        saved = self.current_document is not None
        for widget in (
            self.kind,
            self.description,
            self.items_panel,
            self.preview_button,
            self.create_button,
            self.draft_button,
        ):
            widget.setEnabled(not saved and not self.deleted_repair)
        self.items_panel.setVisible(values["kind"] == "detailed")
        self.previous_index = index
        self.original = self.state()
        self.busy = False
        self.update_total()
        if saved:
            self.load_pdf(self.service.pdf_content(self.current_document["id"]))
            self.message.setText(
                "Сохранённый экземпляр. Изменения ремонта и реквизитов его не меняют."
            )
        else:
            self.clear_preview()

    def source_repair(self):
        from app.database.connection import connect

        with connect(self.records.path) as connection:
            repair = connection.execute(
                "SELECT * FROM repairs WHERE id=?", (self.repair_id,)
            ).fetchone()
        if repair is None:
            raise ValueError("Ремонт не найден.")
        return dict(repair)

    def add_item(self, checked=False, *, values=None):
        row = self.items.rowCount()
        self.items.insertRow(row)
        values = values or {"kind": "work", "title": "", "quantity": "1", "price": ""}
        kind = QComboBox()
        kind.addItem("Работа", "work")
        kind.addItem("Запчасть", "part")
        kind.setCurrentIndex(max(0, kind.findData(values["kind"])))
        kind.currentIndexChanged.connect(self.changed)
        self.items.setCellWidget(row, 0, kind)
        for column, key in enumerate(("title", "quantity", "price"), 1):
            self.items.setItem(row, column, QTableWidgetItem(values[key]))
        self.changed()

    def remove_item(self):
        row = self.items.currentRow()
        if row >= 0:
            self.items.removeRow(row)
            self.changed()

    def state(self):
        rows = []
        for row in range(self.items.rowCount()):
            kind = self.items.cellWidget(row, 0)
            rows.append(
                {"kind": kind.currentData() if kind else "work"}
                | {
                    key: self.items.item(row, column).text() if self.items.item(row, column) else ""
                    for column, key in enumerate(("title", "quantity", "price"), 1)
                }
            )
        return {
            "kind": self.kind.currentData(),
            "description": self.description.toPlainText(),
            "items": rows,
        }

    def update_total(self):
        if self.kind.currentData() == "simple":
            value = amount_text(
                self.current_document["payload"]["total_kopecks"]
                if self.current_document
                else self.source_repair()["amount_kopecks"]
            )
            self.total.setText(f"Итог: {value + ' ₽' if value else 'стоимость не указана'}")
        else:
            try:
                items = normalize_items(self.state()["items"])
                self.total.setText(
                    f"Итог позиций: {amount_text(sum(row['total_kopecks'] for row in items))} ₽"
                )
            except ValueError:
                self.total.setText("Заполните наименования, количество и цены позиций.")

    def changed(self, *_):
        if self.busy:
            return
        self.items_panel.setVisible(self.kind.currentData() == "detailed")
        self.clear_preview()
        self.update_total()

    def clear_preview(self):
        self.pdf.close()
        self.content = None
        self.export_button.setEnabled(False)
        self.print_button.setEnabled(False)
        self.message.setText("Нажмите «Предпросмотр». Для выдачи сохраните экземпляр документа.")

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
        self.print_button.setEnabled(saved)

    def build(self, persist):
        value = self.state()
        try:
            try:
                result = self.service.create(
                    self.repair_id,
                    value["kind"],
                    value["items"],
                    value["description"],
                    allow_difference=self.confirmed_state == value,
                    preview=not persist,
                )
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
                    return
                self.confirmed_state = value
                result = self.service.create(
                    self.repair_id,
                    value["kind"],
                    value["items"],
                    value["description"],
                    allow_difference=True,
                    preview=not persist,
                )
            content = self.service.pdf_content(result["id"]) if persist else pdf_bytes(result)
            if persist:
                self.service.save_draft(self.repair_id, value)
                self.load_history(result["id"])
                self.load_selection(self.history.currentIndex())
            else:
                self.load_pdf(content)
                self.message.setText(
                    "Предпросмотр. Для постоянного номера нажмите «Сохранить экземпляр»."
                )
        except (*ERRORS, RuntimeError) as error:
            QMessageBox.warning(self, "Не удалось подготовить документ", str(error))

    def preview(self):
        self.build(False)

    def create(self):
        self.build(True)

    def save(self, checked=False, *, close=True):
        try:
            if not self.current_document:
                self.service.save_draft(self.repair_id, self.state())
            self.original = self.state()
        except ERRORS as error:
            QMessageBox.warning(self, "Не удалось сохранить черновик", str(error))
            return False
        if close:
            self.accept()
        return True

    def settings(self):
        if DocumentSettingsDialog(self.service, self).exec() and not self.current_document:
            self.clear_preview()

    def export(self):
        if not self.content or not self.current_document:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить квитанцию",
            f"{self.current_document['number']}.pdf",
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
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        printer.setResolution(300)
        dialog = QPrintDialog(printer, self)
        dialog.setMinMax(1, self.pdf.pageCount())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                print_pdf(self.pdf, printer)
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Не удалось напечатать", str(error))
