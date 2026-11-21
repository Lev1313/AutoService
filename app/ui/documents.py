"""Квитанции: редактируемый черновик, фактический PDF и сохранённые экземпляры."""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QPageSize, QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
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
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.documents import Documents, TotalMismatch, normalize_items
from app.services.receipt_pdf import pdf_bytes, save_pdf
from app.services.records import amount_text
from app.ui.vehicles import ERRORS, SafeDialog, plain_label, primary_button


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
    def __init__(self, records, repair_id, parent=None):
        super().__init__(parent)
        self.service = Documents(records.path)
        self.records, self.repair_id = records, repair_id
        self.busy = True
        self.previous_index = -1
        self.current_document = None
        self.content = None
        self.setWindowTitle("Документ / Печать")
        self.resize(1180, 790)
        self.setMinimumSize(850, 580)
        layout = QVBoxLayout(self)
        self.document_status = plain_label("")
        self.document_status.setWordWrap(False)
        self.document_status.setObjectName("badge")
        self.document_status.setAccessibleName("Статус квитанции")
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
        self.kind.addItem("Фольц Мастер: сервисный лист", "service_sheet")
        self.kind.addItem("Простая: описание и общая сумма", "simple")
        self.kind.addItem("Подробная: работы и запчасти", "detailed")
        self.kind.currentIndexChanged.connect(self.changed)
        left.addWidget(self.kind)
        left.addWidget(plain_label("Описание для документа"))
        self.description = QPlainTextEdit()
        self.description.textChanged.connect(self.changed)
        left.addWidget(self.description, 1)
        self.recommendations_panel = QWidget()
        recommendations_layout = QVBoxLayout(self.recommendations_panel)
        recommendations_layout.setContentsMargins(0, 0, 0, 0)
        recommendations_layout.addWidget(plain_label("Рекомендации для сервисного листа"))
        self.recommendations = QPlainTextEdit()
        self.recommendations.setPlaceholderText(
            "Необязательно. Сохраняются в черновике и документе."
        )
        self.recommendations.textChanged.connect(self.changed)
        recommendations_layout.addWidget(self.recommendations)
        recommendations_layout.addWidget(
            plain_label("Шапка и контакты — из шаблона «Фольц Мастер».")
        )
        left.addWidget(self.recommendations_panel, 1)
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
        split.addWidget(editor)
        self.pdf = QPdfDocument(self)
        self.buffer = None
        self.view = QPdfView()
        self.view.setDocument(self.pdf)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.preview_panel = QStackedWidget()
        self.preview_note = plain_label("")
        self.preview_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_note.setContentsMargins(18, 12, 18, 12)
        self.preview_panel.addWidget(self.preview_note)
        self.preview_panel.addWidget(self.view)
        split.addWidget(self.preview_panel)
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

    def load_history(self, selected=None):
        self.deleted_repair = bool(self.source_repair()["deleted_at"])
        documents = self.service.history(self.repair_id)
        self.history.blockSignals(True)
        self.history.clear()
        for document in documents:
            kind = "Простая" if document["kind"] == "simple" else "Подробная"
            if document["number"].startswith("СЛ-"):
                kind = "Сервисный лист"
            stamp = datetime.fromisoformat(document["created_at"]).strftime("%d.%m.%Y %H:%M")
            self.history.addItem(f"{document['number']} · {stamp} · {kind}", document["id"])
        self.history.setPlaceholderText("Выберите документ" if documents else "Документов пока нет")
        self.history.setEnabled(bool(documents))
        index = self.history.findData(selected) if selected else 0 if documents else -1
        self.history.setCurrentIndex(index)
        self.history.blockSignals(False)
        self.previous_index = index

    def new_receipt(self):
        self.history.setCurrentIndex(-1)

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
            self.document_status.setText("Квитанция недоступна")
            self.clear_preview("Квитанцию не удалось открыть.")
            self.original = self.state()
            QMessageBox.warning(self, "Не удалось открыть документ", str(error))

    def apply_selection(self, index):
        self.busy = True
        self.preview_timer.stop()
        self.previous_index = index
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
        self.current_document = self.service.get(document_id) if saved else None
        if self.current_document:
            self.document_status.setText(f"Оформлена · {self.current_document['number']}")
            payload = self.current_document["payload"]
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
            self.document_status.setText("Черновик" if editable else "Квитанций нет")
            with_repair = self.service.draft(self.repair_id)
            with_repair = with_repair or {
                "kind": "simple",
                "layout": "service_sheet",
                "description": self.source_repair()["description"],
                "items": [],
            }
            values = with_repair
        selected_kind = (
            "service_sheet" if values.get("layout") == "service_sheet" else values["kind"]
        )
        self.kind.setCurrentIndex(max(0, self.kind.findData(selected_kind)))
        self.description.setPlainText(values["description"])
        self.recommendations.setPlainText(values.get("recommendations", ""))
        self.items.setRowCount(0)
        for row in values["items"]:
            self.add_item(values=row)
        self.items_panel.setVisible(values["kind"] == "detailed")
        self.recommendations_panel.setVisible(selected_kind == "service_sheet")
        self.original = self.state()
        self.busy = False
        self.update_total()
        if saved:
            self.load_pdf(self.service.pdf_content(self.current_document["id"]))
            self.message.setText("В окне печати Windows выберите принтер.")
        elif editable:
            self.message.setText("Документ получит номер при оформлении.")
            self.preview()
        else:
            self.clear_preview(
                "Для этого ремонта ещё нет оформленных квитанций. "
                "Чтобы создать квитанцию, восстановите ремонт."
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
            "kind": "simple"
            if self.kind.currentData() == "service_sheet"
            else self.kind.currentData(),
            "layout": "service_sheet" if self.kind.currentData() == "service_sheet" else "receipt",
            "recommendations": self.recommendations.toPlainText(),
            "description": self.description.toPlainText(),
            "items": rows,
        }

    def update_total(self):
        if self.kind.currentData() == "service_sheet":
            self.total.setText("Сервисный лист без стоимости работ и запчастей")
            return
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
        self.recommendations_panel.setVisible(self.kind.currentData() == "service_sheet")
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

    def preview(self):
        try:
            draft = self.service.create(
                self.repair_id, **self.state(), allow_difference=True, preview=True
            )
            self.load_pdf(pdf_bytes(draft))
        except (*ERRORS, RuntimeError) as error:
            self.clear_preview(f"Предпросмотр недоступен.\n{error}")

    def create(self):
        if self.current_document or self.deleted_repair:
            return False
        value = self.state()
        try:
            try:
                result = self.service.create(self.repair_id, **value, consume_draft=True)
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
                result = self.service.create(
                    self.repair_id, **value, allow_difference=True, consume_draft=True
                )
        except (*ERRORS, RuntimeError) as error:
            QMessageBox.warning(self, "Не удалось оформить документ", str(error))
            return False
        self.current_document = result
        self.preview_timer.stop()
        self.create_button.setEnabled(False)
        self.clear_preview()
        try:
            self.load_history(result["id"])
            self.load_selection(self.history.currentIndex())
        except (*ERRORS, RuntimeError) as error:
            QMessageBox.warning(self, f"Квитанция {result['number']} оформлена", str(error))
        return self.content is not None

    def print_or_create(self):
        if self.current_document or self.create():
            self.print_document()

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
        if not self.content or not self.current_document:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить документ",
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
                "Квитанцию можно выгрузить кнопкой «Сохранить PDF».",
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
                    "Квитанцию можно выгрузить кнопкой «Сохранить PDF».",
                )
            else:
                self.message.setText(
                    "Задание отправлено на печать. Если лист не вышел, проверьте очередь в Windows."
                )

        else:
            self.message.setText(
                f"Печать отменена. Квитанция {self.current_document['number']} сохранена."
            )
