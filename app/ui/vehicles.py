"""Компактный список и простые формы автомобилей и ремонтов."""

import json
import sqlite3

from PySide6.QtCore import QDate, QModelIndex, QStringListModel, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.catalog import VehicleCatalog, make_search_text, model_search_text, name_key
from app.services.records import (
    Records,
    RestoreMileageConflict,
    amount_text,
    mileage_text,
    parse_amount,
    search_key,
)
from app.ui.theme import STATUS_TONE, StatusDelegate
from app.ui.work_windows import work_windows

ERRORS = (ValueError, OSError, sqlite3.Error)


def recommendations_label():
    label = plain_label("Рекомендации")
    label.setStyleSheet("color: black; font-weight: bold;")
    return label


def plain_label(text: str) -> QLabel:
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    return result


def primary_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("primary", True)
    return button


def vehicle_label(vehicle):
    model = " ".join(part for part in (vehicle["make"], vehicle["model"]) if part)
    identity = vehicle["plate"] or vehicle["vin"]
    return " · ".join(part for part in (model, identity) if part)


def make_table(headers: tuple[str, ...]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(38)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    return table


class SafeDialog(QDialog):
    """Закрытие и Esc не теряют изменённую форму; Ctrl+S сохраняет её."""

    def prepare_guard(self):
        self.original = self.state()
        shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        shortcut.activated.connect(self.save)

    def reject(self):
        if self.state() != self.original:
            answer = QMessageBox.question(
                self,
                "Несохранённые изменения",
                "Сохранить изменения перед закрытием?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.Save:
                self.save()
                return
        super().reject()


class ContactCompleter(QCompleter):
    """В списке показываем имя и телефон, в поле вставляем только его значение."""

    def pathFromIndex(self, index):
        return index.data(Qt.ItemDataRole.UserRole + 1) or ""


class VehicleSuggestions(QStringListModel):
    def __init__(self, search_text, parent):
        super().__init__(parent)
        self.search_text = search_text

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.UserRole + 1:
            label = super().data(index, Qt.ItemDataRole.DisplayRole)
            return self.search_text(label) if label is not None else ""
        return super().data(index, role)


class VehicleCompleter(QCompleter):
    def __init__(self, suggestions, parent):
        super().__init__(suggestions, parent)
        self.setCompletionRole(Qt.ItemDataRole.UserRole + 1)

    def splitPath(self, path):
        return [name_key(path)]

    def pathFromIndex(self, index):
        return index.data(Qt.ItemDataRole.DisplayRole) or ""


class VehicleDialog(SafeDialog):
    def __init__(self, records: Records, vehicle_id=None, parent=None, *, contact_id=None):
        super().__init__(parent)
        self.records = records
        self.vehicle_id = vehicle_id
        vehicle = records.vehicle(vehicle_id) if vehicle_id is not None else None
        self.saved_vehicle = vehicle
        self.setWindowTitle(
            f"Изменить автомобиль · {vehicle_label(vehicle)}"
            if vehicle is not None
            else "Добавить автомобиль"
        )
        self.setMinimumWidth(530)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label("Достаточно марки, модели, госномера или VIN."))
        form = QFormLayout()
        self.fields = {}
        for key, title in (
            ("make", "Марка"),
            ("model", "Модель"),
            ("plate", "Госномер"),
            ("vin", "VIN"),
            ("year", "Год выпуска"),
        ):
            field = QLineEdit()
            field.setAccessibleName(title)
            field.setMaxLength(200 if key != "year" else 4)
            self.fields[key] = field
            form.addRow(title, field)
        self.contacts = QComboBox()
        self.contacts.addItem("Новый клиент / без клиента", None)
        self.contact_rows = records.contacts()
        for contact in self.contact_rows:
            self.contacts.addItem(
                f"{contact['name'] or 'Без имени'} · {contact['phone'] or 'нет телефона'}",
                contact["id"],
            )
        form.addRow("Выбрать клиента", self.contacts)
        for key, title in (("contact_name", "Клиент"), ("phone", "Телефон")):
            self.fields[key] = QLineEdit()
            self.fields[key].setMaxLength(200)
            form.addRow(title, self.fields[key])
        self.notes = QPlainTextEdit()
        self.notes.setMaximumHeight(100)
        form.addRow("Описание", self.notes)
        layout.addLayout(form)
        self.contacts.currentIndexChanged.connect(self.choose_contact)
        if vehicle is not None:
            index = self.contacts.findData(vehicle["contact_id"])
            self.contacts.setCurrentIndex(max(0, index))
            for key, field in self.fields.items():
                field.setText(str(vehicle[key]) if vehicle[key] is not None else "")
            self.notes.setPlainText(vehicle["notes"])
        elif contact_id is not None:
            row = self.contacts.findData(contact_id)
            if row < 0:
                raise ValueError("Клиент не найден.")
            self.contacts.setCurrentIndex(row)
        self.setup_suggestions()
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        self.save_button = primary_button("Сохранить")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.save)
        row.addWidget(cancel)
        row.addWidget(self.save_button)
        layout.addLayout(row)
        self.prepare_guard()

    def setup_suggestions(self):
        self.catalog = VehicleCatalog(self.records)
        self.make_suggestions = VehicleSuggestions(make_search_text, self)
        self.make_suggestions.setStringList(self.catalog.makes())
        self.make_completer = VehicleCompleter(self.make_suggestions, self)
        self.fields["make"].setCompleter(self.make_completer)
        self.model_suggestions = VehicleSuggestions(
            lambda label: model_search_text(self.fields["make"].text(), label), self
        )
        self.model_completer = VehicleCompleter(self.model_suggestions, self)
        self.fields["model"].setCompleter(self.model_completer)
        self.fields["make"].textChanged.connect(self.update_models)
        self.update_models()
        completers = [self.make_completer, self.model_completer]
        self.contact_completers = {}
        for key in ("contact_name", "phone"):
            model = QStandardItemModel(self)
            for contact in self.contact_rows:
                values = (contact["name"] or "Без имени", contact["phone"] or "нет телефона")
                if key == "phone":
                    values = values[::-1]
                item = QStandardItem(" · ".join(values))
                item.setData(contact["id"], Qt.ItemDataRole.UserRole)
                item.setData(
                    contact["name" if key == "contact_name" else "phone"],
                    Qt.ItemDataRole.UserRole + 1,
                )
                model.appendRow(item)
            completer = ContactCompleter(model, self)
            completer.activated[QModelIndex].connect(self.select_suggested_contact)
            self.fields[key].setCompleter(completer)
            self.contact_completers[key] = completer
            completers.append(completer)
        for completer in completers:
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setMaxVisibleItems(12)
        self.fields["make"].setPlaceholderText("Начните вводить или укажите свою марку")
        self.fields["model"].setPlaceholderText("Модели выбранной марки или своё название")

    def update_models(self, *_):
        self.model_suggestions.setStringList(self.catalog.models(self.fields["make"].text()))

    def select_suggested_contact(self, index):
        contact_id = index.data(Qt.ItemDataRole.UserRole)
        row = self.contacts.findData(contact_id)
        if row >= 0:
            self.contacts.blockSignals(True)
            self.contacts.setCurrentIndex(row)
            self.contacts.blockSignals(False)
            self.choose_contact()

    def choose_contact(self):
        selected = self.contacts.currentData()
        contact = next((row for row in self.contact_rows if row["id"] == selected), {})
        self.fields["contact_name"].setText(contact.get("name", ""))
        self.fields["phone"].setText(contact.get("phone", ""))

    def state(self):
        return {key: field.text() for key, field in self.fields.items()} | {
            "notes": self.notes.toPlainText(),
            "contact_id": self.contacts.currentData(),
        }

    def save(self):
        try:
            if (
                self.saved_vehicle is not None
                and self.records.vehicle(self.vehicle_id) != self.saved_vehicle
            ):
                raise ValueError(
                    "Карточка изменилась в другом окне. Откройте её заново перед сохранением."
                )
            values = self.state()
            year = values["year"].strip()
            if year and (not year.isascii() or not year.isdigit()):
                raise ValueError("Год выпуска должен быть целым числом.")
            values["year"] = int(year) if year else None
            duplicates = self.records.duplicates(values["plate"], values["vin"], self.vehicle_id)
            phone_matches = [
                contact
                for contact in self.contact_rows
                if search_key(values["phone"])
                and search_key(contact["phone"]) == search_key(values["phone"])
                and contact["id"] != values["contact_id"]
            ]
            if duplicates or phone_matches:
                text = (
                    "В базе уже есть совпадение госномера или VIN. Создать отдельную запись?"
                    if duplicates
                    else "Такой телефон уже есть у другого контакта. Сохранить отдельный контакт?"
                )
                if (
                    QMessageBox.question(
                        self,
                        "Возможное совпадение",
                        text,
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    != QMessageBox.StandardButton.Yes
                ):
                    return
            self.vehicle_id = self.records.save_vehicle(values, self.vehicle_id)
        except ERRORS as error:
            QMessageBox.warning(self, "Не удалось сохранить автомобиль", str(error))
            return
        self.accept()


class RepairDialog(SafeDialog):
    repair_saved = Signal(int)

    def __init__(
        self, records: Records, vehicle_id: int, repair=None, parent=None, *, appointment=None
    ):
        super().__init__(parent)
        self.records = records
        self.vehicle_id = vehicle_id
        self.repair = repair or {}
        self.appointment = appointment
        identity = vehicle_label(records.vehicle(vehicle_id)) or "Автомобиль"
        self.setWindowTitle(f"Ремонт · {identity}")
        self.resize(740, 610)
        self.setMinimumSize(550, 450)
        layout = QVBoxLayout(self)
        heading = plain_label(identity)
        heading.setObjectName("repairTitle")
        layout.addWidget(heading)
        if appointment and appointment["reason"]:
            layout.addWidget(plain_label(f"Причина визита: {appointment['reason']}"))
        form = QFormLayout()
        self.visit_date = QDateEdit(QDate.currentDate())
        self.visit_date.setCalendarPopup(True)
        self.visit_date.setDisplayFormat("dd.MM.yyyy")
        if self.repair.get("visit_date"):
            self.visit_date.setDate(QDate.fromString(self.repair["visit_date"], "yyyy-MM-dd"))
        form.addRow("Дата", self.visit_date)
        self.date_known = QCheckBox("Дата известна")
        self.date_known.setChecked(bool(self.repair.get("visit_date")) if self.repair else True)
        self.date_known.toggled.connect(self.visit_date.setEnabled)
        self.date_known.setVisible(self.repair.get("origin") == "txt")
        self.visit_date.setEnabled(self.date_known.isChecked())
        form.addRow(self.date_known)
        self.mileage = QLineEdit()
        self.mileage.setMaxLength(12)
        self.mileage.setAccessibleName("Пробег, км")
        self.carried = None
        self.mileage.setPlaceholderText("Необязательно")
        self.measured = QCheckBox("Пробег измерен при этом ремонте")
        self.measured.setChecked(self.repair.get("km") is not None)
        self.mileage_note = plain_label("")
        form.addRow("Пробег, км", self.mileage)
        form.addRow(self.measured)
        form.addRow(self.mileage_note)
        if self.repair.get("carried_km") is not None:
            self.carried = (self.repair["carried_km"], self.repair["carried_on"])
            self.show_carried()
        elif self.repair.get("km") is not None:
            self.mileage.setText(str(self.repair["km"]))
        elif not self.repair:
            self.suggest_mileage()
        self.mileage.textEdited.connect(self.mileage_edited)
        self.measured.toggled.connect(self.measurement_changed)
        self.visit_date.dateChanged.connect(self.date_changed)
        self.mileage_verified = QCheckBox("Пробег проверен")
        self.mileage_verified.setChecked(bool(self.repair.get("verified")))
        self.mileage_verified.setVisible(self.repair.get("origin") == "txt")
        form.addRow(self.mileage_verified)
        self.amount = QLineEdit(amount_text(self.repair.get("amount_kopecks")))
        self.amount.setMaxLength(30)
        self.amount.setPlaceholderText("Пусто — неизвестно; 0 — бесплатно")
        form.addRow("Общая стоимость, ₽", self.amount)
        layout.addLayout(form)
        layout.addWidget(plain_label("Что сделали"))
        self.description = QPlainTextEdit(self.repair.get("description", ""))
        self.description.setAccessibleName("Описание ремонта")
        layout.addWidget(self.description, 1)
        layout.addWidget(recommendations_label())
        self.inherited = self.repair.get("recommendations", "")
        if not self.repair:
            self.inherited = records.previous_recommendations(
                vehicle_id, self.visit_date.date().toString("yyyy-MM-dd")
            )
        self.recommendations = QPlainTextEdit(self.inherited)
        self.recommendations.setAccessibleName("Рекомендации")
        layout.addWidget(self.recommendations, 1)
        self.positions = QPlainTextEdit()
        self.positions.setReadOnly(True)
        self.positions.setAccessibleName("Позиции заказ-наряда")
        self.positions.setMaximumHeight(80)
        layout.addWidget(self.positions)
        self.show_positions(self.repair.get("document_items", "[]"))
        self.save_notice = plain_label("")
        self.save_notice.hide()
        layout.addWidget(self.save_notice)
        layout.addWidget(plain_label("Завершение ремонта не означает получение оплаты."))
        row = QHBoxLayout()
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.clicked.connect(self.reject)
        row.addWidget(self.cancel_button)
        self.document_button = QPushButton("Документ / Печать")
        self.document_button.clicked.connect(self.open_documents)
        row.addWidget(self.document_button)
        if self.repair.get("origin") == "txt":
            original_button = QPushButton("Исходный TXT")
            original_button.clicked.connect(self.open_source)
            row.addWidget(original_button)
        row.addStretch()
        self.save_button = primary_button("Сохранить")
        self.save_button.clicked.connect(self.save)
        self.save_button.setDefault(True)
        row.addWidget(self.save_button)
        self.finish_button = QPushButton("Завершить ремонт")
        self.finish_button.setAutoDefault(False)
        self.finish_button.clicked.connect(lambda: self.save(completed=True))
        row.addWidget(self.finish_button)
        self.finish_button.setVisible(not self.repair.get("completed", False))
        layout.addLayout(row)
        self.prepare_guard()

    def show_positions(self, raw_items):
        lines = []
        for item in json.loads(raw_items):
            kind = "Запчасть" if item.get("kind") == "part" else "Работа"
            price = item.get("price")
            price_text = f" × {price} ₽" if price not in ("", None) else ""
            lines.append(
                f"{kind} · {item.get('title', '')} · {item.get('quantity', '')}{price_text}"
            )
        self.positions.setPlainText("\n".join(lines))
        self.positions.setVisible(bool(lines))

    def show_carried(self):
        if self.carried:
            self.mileage.setText(f"{self.carried[0]} ?")
            self.mileage_note.setText(
                f"Последний известный · замер {self.carried[1]}. Новый пробег не указан."
            )
        else:
            self.mileage.clear()
            self.mileage_note.setText("Нет подходящего подтверждённого замера на эту дату.")

    def suggest_mileage(self):
        latest = self.records.latest_mileage(
            self.vehicle_id, self.visit_date.date().toString("yyyy-MM-dd"), self.repair.get("id")
        )
        self.carried = (latest["km"], latest["recorded_on"]) if latest else None
        self.show_carried()

    def date_changed(self, *_):
        if not self.repair and not self.recommendations.document().isModified():
            self.inherited = self.records.previous_recommendations(
                self.vehicle_id, self.visit_date.date().toString("yyyy-MM-dd")
            )
            self.recommendations.setPlainText(self.inherited)
        if not self.measured.isChecked():
            self.suggest_mileage()

    def mileage_edited(self, text):
        self.carried = None
        self.measured.blockSignals(True)
        self.measured.setChecked(bool(text.strip()))
        self.measured.blockSignals(False)
        if "?" in text:
            self.mileage.setText(text.replace("?", "").strip())
        self.mileage_note.setText("Новый замер" if text.strip() else "Пробег не указан")

    def measurement_changed(self, measured):
        if measured:
            value = self.carried[0] if self.carried else None
            self.carried = None
            if value is not None:
                self.mileage.setText(str(value))
            self.mileage_note.setText("Новый замер — укажите фактическое значение.")
            self.mileage.setFocus()
            self.mileage.selectAll()
        else:
            self.suggest_mileage()

    def state(self):
        values = {
            "visit_date": self.visit_date.date().toString("yyyy-MM-dd")
            if self.date_known.isChecked()
            else None,
            "km": self.mileage.text() if self.carried is None else "",
            "carried_km": self.carried[0] if self.carried else None,
            "carried_on": self.carried[1] if self.carried else None,
            "amount": self.amount.text(),
            "description": self.description.toPlainText(),
            "recommendations": self.recommendations.toPlainText(),
        }
        if self.repair.get("origin") == "txt":
            values["mileage_verified"] = self.mileage_verified.isChecked()
        return values

    def save(self, checked=False, *, completed=None, close=True):
        try:
            values = self.state()
            km = values["km"].replace(" ", "").replace("\u00a0", "").strip()
            if km and (not km.isascii() or not km.isdigit()):
                raise ValueError("Введите пробег целым неотрицательным числом.")
            values["km"] = int(km) if km else None
            values["amount_kopecks"] = parse_amount(values.pop("amount"))
            values["completed"] = (
                self.repair.get("completed", False) if completed is None else completed
            )
            allow_unverified = False
            if (
                values["km"] is not None
                and values["visit_date"] is not None
                and values.get("mileage_verified", True)
                and not self.records.mileage_consistent(
                    self.vehicle_id,
                    values["visit_date"],
                    values["km"],
                    self.repair.get("id"),
                )
            ):
                allow_unverified = (
                    QMessageBox.question(
                        self,
                        "Проверьте пробег",
                        "Пробег противоречит истории. Сохранить как неподтверждённый? "
                        "Он останется в ремонте, но не заменит достоверный пробег автомобиля.",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    == QMessageBox.StandardButton.Yes
                )
                if not allow_unverified:
                    return
                values["mileage_verified"] = False
            repair_id = self.records.save_repair(
                self.vehicle_id,
                values,
                self.repair.get("id"),
                allow_unverified=allow_unverified,
                appointment_id=self.appointment["id"] if self.appointment else None,
            )
        except ERRORS as error:
            QMessageBox.warning(self, "Не удалось сохранить ремонт", str(error))
            return False
        self.repair = next(
            row for row in self.records.repairs(self.vehicle_id) if row["id"] == repair_id
        )
        self.mileage_verified.setChecked(bool(self.repair["verified"]))
        self.original = self.state()
        self.finish_button.setVisible(not self.repair["completed"])
        self.repair_saved.emit(repair_id)
        if close:
            self.accept()
        else:
            self.save_notice.setText("Ремонт сохранён.")
            self.save_notice.show()
            self.cancel_button.setText("Закрыть")
        return True

    def open_documents(self):
        if self.repair.get("id") is None or self.state() != self.original:
            if (
                QMessageBox.question(
                    self,
                    "Сохранить ремонт?",
                    "Сначала сохранить запись ремонта для документа?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
            if not self.save(close=False):
                return
        return open_document_window(self.records, self.repair["id"], self)

    def open_source(self):
        from app.ui.txt_import import SourceDialog

        try:
            SourceDialog(self.records.path, self.repair["id"], self).exec()
        except ERRORS as error:
            QMessageBox.warning(self, "Не удалось открыть источник", str(error))


class VehicleCard(QDialog):
    def __init__(self, records: Records, vehicle_id: int, parent=None, *, workspace=None):
        super().__init__(parent)
        self.records = records
        self.vehicle_id = vehicle_id
        self._work_windows = workspace or work_windows(self)
        self.resize(940, 700)
        self.setMinimumSize(650, 500)
        layout = QVBoxLayout(self)
        self.heading = plain_label("")
        self.heading.setStyleSheet("font-size: 18pt; font-weight: 600")
        layout.addWidget(self.heading)
        self.summary = plain_label("")
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary)
        row = QHBoxLayout()
        self.edit_button = QPushButton("Изменить автомобиль")
        self.edit_button.clicked.connect(self.edit_vehicle)
        row.addWidget(self.edit_button)
        maintenance_button = QPushButton("ТО и напоминания")
        maintenance_button.clicked.connect(self.open_maintenance)
        row.addWidget(maintenance_button)
        row.addStretch()
        self.add_repair_button = primary_button("Добавить ремонт")
        self.add_repair_button.clicked.connect(self.add_repair)
        row.addWidget(self.add_repair_button)
        layout.addLayout(row)
        self.history = make_table(("Дата", "Пробег, км", "Описание", "Стоимость, ₽", "Запись"))
        header = self.history.horizontalHeader()
        for column, width in ((0, 95), (1, 108), (3, 100), (4, 135)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.history.setColumnWidth(column, width)
        self.history.setItemDelegateForColumn(4, StatusDelegate(self.history))
        self.history.setAccessibleName("История ремонтов")
        self.history.cellDoubleClicked.connect(lambda *_: self.open_repair())
        self.history.itemSelectionChanged.connect(self.show_description)
        self.show_deleted = QCheckBox("Показать удалённые ремонты")
        self.show_deleted.toggled.connect(lambda _: self.refresh())
        layout.addWidget(self.show_deleted)
        self.save_notice = plain_label("")
        self.save_notice.hide()
        layout.addWidget(self.save_notice)
        layout.addWidget(self.history, 1)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Выберите ремонт, чтобы прочитать полную запись.")
        layout.addWidget(self.preview, 1)
        bottom = QHBoxLayout()
        self.open_button = QPushButton("Открыть выбранный ремонт")
        self.open_button.clicked.connect(self.open_repair)
        bottom.addWidget(self.open_button)
        self.document_button = QPushButton("Документы / Печать")
        self.document_button.clicked.connect(self.open_documents)
        bottom.addWidget(self.document_button)
        self.delete_button = QPushButton("Удалить ремонт")
        self.delete_button.clicked.connect(self.toggle_deleted)
        self.source_button = QPushButton("Исходный TXT")
        self.source_button.clicked.connect(self.open_source)
        bottom.addWidget(self.source_button)
        bottom.addStretch()
        bottom.addWidget(self.delete_button)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        self.rows = []
        self.refresh()
        self._work_windows.vehicle_saved.connect(self.refresh_vehicle)
        self._work_windows.repair_saved.connect(self.refresh_repair)

    def refresh_vehicle(self, vehicle_id):
        if self.vehicle_id == vehicle_id:
            self.refresh()

    def refresh_repair(self, vehicle_id, repair_id):
        if self.vehicle_id == vehicle_id:
            self.refresh(repair_id)

    def refresh(self, repair_id=None):
        saved = repair_id is not None
        previous = self.history.currentRow()
        if repair_id is None and 0 <= previous < len(self.rows):
            repair_id = self.rows[previous]["id"]
        vehicle = self.records.vehicle(self.vehicle_id)
        name = " ".join(filter(None, (vehicle["make"], vehicle["model"]))) or "Автомобиль"
        self.setWindowTitle(vehicle["plate"] or vehicle["vin"] or name)
        self.heading.setText(f"{name} · {vehicle['plate'] or 'без госномера'}")
        latest = self.records.latest_mileage(self.vehicle_id)
        source_name = {"maintenance": "из ТО", "import": "из TXT"}.get(
            latest["source"] if latest else "", "из ремонта"
        )
        mileage = (
            f"{latest['km']:,} км · {latest['recorded_on']} · {source_name}".replace(",", " ")
            if latest
            else "Нет подтверждённого замера пробега"
        )
        self.summary.setText(
            f"Контакт: {vehicle['contact_name'] or 'не указан'} · "
            f"{vehicle['phone'] or 'нет телефона'}\n"
            f"VIN: {vehicle['vin'] or '—'} · Год: {vehicle['year'] or '—'}\n"
            f"{mileage}" + (f"\n{vehicle['notes']}" if vehicle["notes"] else "")
        )
        self.rows = self.records.repairs(self.vehicle_id, self.show_deleted.isChecked())
        self.history.setRowCount(len(self.rows))
        self.preview.clear()
        for index, repair in enumerate(self.rows):
            km = mileage_text(repair)
            values = (
                QDate.fromString(repair["visit_date"], "yyyy-MM-dd").toString("dd.MM.yyyy")
                if repair["visit_date"]
                else "Неизвестна",
                km,
                repair["description"].replace("\n", " ")[:100],
                amount_text(repair["amount_kopecks"]) or "Неизвестна",
                (
                    "Удалён"
                    if repair["deleted_at"]
                    else "Импорт: статус не уточнён"
                    if not repair["state_known"]
                    else "В истории"
                    if repair["completed"]
                    else "Текущий"
                ),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 4:
                    tone = {
                        "Удалён": "muted",
                        "Импорт: статус не уточнён": "attention",
                        "В истории": "done",
                        "Текущий": "active",
                    }[value]
                    item.setData(STATUS_TONE, tone)
                self.history.setItem(index, column, item)
        if self.rows:
            self.history.selectRow(
                next((i for i, row in enumerate(self.rows) if row["id"] == repair_id), 0)
            )
        self.show_description()
        self.save_notice.setText("Ремонт сохранён." if saved else "")
        self.save_notice.setVisible(saved)

    def show_description(self):
        row = self.history.currentRow()
        selected = self.rows[row] if 0 <= row < len(self.rows) else None
        self.open_button.setEnabled(bool(selected) and not selected["deleted_at"])
        self.document_button.setEnabled(bool(selected))
        self.delete_button.setEnabled(bool(selected))
        self.source_button.setVisible(bool(selected) and selected["origin"] == "txt")
        self.delete_button.setText(
            "Восстановить ремонт" if selected and selected["deleted_at"] else "Удалить ремонт"
        )
        self.preview.setPlainText(
            (
                ("Удалён: " + selected["deleted_at"] + "\n")
                if selected and selected["deleted_at"]
                else ""
            )
            + (selected["description"] if selected else "")
            + (
                "\n\nРекомендации\n" + selected["recommendations"]
                if selected and selected["recommendations"]
                else ""
            )
        )

    def open_source(self):
        from app.ui.txt_import import SourceDialog

        row = self.history.currentRow()
        if 0 <= row < len(self.rows) and self.rows[row]["origin"] == "txt":
            try:
                SourceDialog(self.records.path, self.rows[row]["id"], self).exec()
            except ERRORS as error:
                QMessageBox.warning(self, "Не удалось открыть источник", str(error))

    def toggle_deleted(self):
        from app.ui.documents import DocumentDialog

        row = self.history.currentRow()
        if not 0 <= row < len(self.rows):
            return
        repair = self.rows[row]
        deleting = not repair["deleted_at"]
        try:
            links = self.records.repair_links(repair["id"])
            text = (
                f"{self.heading.text()}\nДата: {repair['visit_date'] or 'неизвестна'}\n"
                f"{repair['description'][:250]}\n\n"
            )
            if deleting:
                text += (
                    "Убрать ремонт из истории? Его можно восстановить. "
                    "Замер этого ремонта перестанет определять текущий пробег.\n"
                    f"Сохранены: документы — {links['documents']}, TXT — {links['sources']}.\n"
                )
                if links["maintenance"]:
                    text += (
                        f"Связанных отметок ТО: {links['maintenance']}. "
                        "Выполнение ТО и его отдельные замеры не отменяются."
                    )
            else:
                text += "Восстановить ремонт со всеми связями и замером пробега?"
            if (
                QMessageBox.question(
                    self,
                    "Удалить ремонт?" if deleting else "Восстановить ремонт?",
                    text,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
            for dialog_type in (RepairDialog, DocumentDialog):
                editor = self._work_windows.find((dialog_type, repair["id"]))
                if editor is not None and not editor.close():
                    return
            try:
                self.records.set_repair_deleted(self.vehicle_id, repair["id"], deleting)
            except RestoreMileageConflict as conflict:
                if (
                    QMessageBox.question(
                        self,
                        "Проверьте пробег при восстановлении",
                        str(conflict),
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                        QMessageBox.StandardButton.Cancel,
                    )
                    != QMessageBox.StandardButton.Yes
                ):
                    return
                self.records.set_repair_deleted(
                    self.vehicle_id, repair["id"], False, allow_unverified=True
                )
            self._work_windows.vehicle_saved.emit(self.vehicle_id)
        except ERRORS as error:
            QMessageBox.warning(self, "Не удалось изменить ремонт", str(error))

    def edit_vehicle(self):
        return open_vehicle_editor(self.records, self, self.vehicle_id)

    def open_maintenance(self):
        from app.ui.maintenance import PlansDialog

        PlansDialog(self.records, self.vehicle_id, self).exec()
        self.refresh()

    def edit_repair(self, repair=None):
        return open_repair_window(self.records, self.vehicle_id, self, repair)

    def add_repair(self):
        return self.edit_repair()

    def open_repair(self):
        row = self.history.currentRow()
        if 0 <= row < len(self.rows) and not self.rows[row]["deleted_at"]:
            return self.edit_repair(self.rows[row])

    def open_documents(self):
        row = self.history.currentRow()
        if 0 <= row < len(self.rows):
            return open_document_window(self.records, self.rows[row]["id"], self)


def open_vehicle_card(records, vehicle_id, owner):
    workspace = work_windows(owner)
    return workspace.open(
        (VehicleCard, vehicle_id), lambda: VehicleCard(records, vehicle_id, workspace=workspace)
    )


def open_vehicle_editor(records, owner, vehicle_id=None, *, contact_id=None, after_create=None):
    workspace = work_windows(owner)

    def create():
        editor = VehicleDialog(records, vehicle_id, contact_id=contact_id)

        def saved():
            workspace.vehicle_saved.emit(editor.vehicle_id)
            if after_create is not None and not workspace.closing:
                after_create(editor.vehicle_id)

        editor.accepted.connect(saved)
        return editor

    key = (VehicleDialog, vehicle_id) if vehicle_id is not None else None
    return workspace.open(key, create)


def open_repair_window(records, vehicle_id, owner, repair=None, *, appointment=None):
    workspace = work_windows(owner)
    if repair is not None:
        key = (RepairDialog, repair["id"])
    elif appointment is not None:
        key = (RepairDialog, "appointment", appointment["id"])
    else:
        key = (RepairDialog, "new", vehicle_id)

    def create():
        editor = RepairDialog(records, vehicle_id, repair, appointment=appointment)

        def saved(repair_id):
            workspace.dialogs[editor] = (RepairDialog, repair_id)
            workspace.repair_saved.emit(vehicle_id, repair_id)

        editor.repair_saved.connect(saved)
        return editor

    return workspace.open(key, create)


def open_document_window(records, repair_id, owner):
    from app.ui.documents import DocumentDialog

    workspace = work_windows(owner)

    def create():
        dialog = DocumentDialog(records, repair_id)

        def saved():
            repair = dialog.source_repair()
            editor = workspace.find((RepairDialog, repair_id))
            if editor:
                for field, widget, new_value in (
                    ("amount", editor.amount, amount_text(repair["amount_kopecks"])),
                    ("description", editor.description, repair["description"]),
                    ("recommendations", editor.recommendations, repair["recommendations"]),
                ):
                    if editor.state()[field] == editor.original[field]:
                        if field == "amount":
                            widget.setText(new_value)
                        else:
                            widget.setPlainText(new_value)
                    editor.original[field] = new_value
                editor.show_positions(repair["document_items"])
                editor.repair.update(repair)
            workspace.repair_saved.emit(repair["vehicle_id"], repair_id)

        dialog.repair_updated.connect(saved)
        return dialog

    return workspace.open((DocumentDialog, repair_id), create)


class VehiclesPage(QWidget):
    def __init__(self, records: Records):
        super().__init__()
        self.records = records
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 30, 32, 24)
        title = plain_label("Автомобили")
        title.setObjectName("title")
        layout.addWidget(title)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setAccessibleName("Поиск автомобилей")
        self.search.setPlaceholderText("Госномер, VIN, марка, модель, контакт или телефон")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        row.addWidget(self.search, 1)
        self.add_button = primary_button("Добавить автомобиль")
        self.add_button.clicked.connect(self.add_vehicle)
        row.addWidget(self.add_button)
        import_button = QPushButton("Импорт TXT")
        import_button.clicked.connect(self.import_txt)
        row.addWidget(import_button)
        layout.addLayout(row)
        self.current_only = QCheckBox("Только автомобили с текущим ремонтом")
        self.current_only.toggled.connect(self.refresh)
        layout.addWidget(self.current_only)
        self.table = make_table(("Автомобиль", "Госномер", "Контакт", "Телефон"))
        self.table.setAccessibleName("Автомобили")
        self.table.cellDoubleClicked.connect(lambda *_: self.open_vehicle())
        layout.addWidget(self.table, 1)
        self.empty = plain_label("")
        layout.addWidget(self.empty)
        self.open_button = QPushButton("Открыть карточку")
        self.open_button.clicked.connect(self.open_vehicle)
        layout.addWidget(self.open_button, alignment=Qt.AlignmentFlag.AlignLeft)
        enter = QShortcut(QKeySequence("Return"), self.table)
        enter.setContext(Qt.ShortcutContext.WidgetShortcut)
        enter.activated.connect(self.open_vehicle)
        self.refresh()

    def refresh(self):
        previous = self.table.currentRow()
        selected_id = (
            self.rows[previous]["id"] if 0 <= previous < len(getattr(self, "rows", [])) else None
        )
        try:
            self.rows = self.records.vehicles(
                self.search.text(), current_repairs_only=self.current_only.isChecked()
            )
        except ERRORS as error:
            QMessageBox.warning(self, "Не удалось открыть список", str(error))
            return
        self.table.setRowCount(len(self.rows))
        for index, vehicle in enumerate(self.rows):
            values = (
                " ".join(filter(None, (vehicle["make"], vehicle["model"]))) or "Без названия",
                vehicle["plate"] or "—",
                vehicle["contact_name"] or "—",
                vehicle["phone"] or "—",
            )
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(value))
        self.empty.setText(
            f"Найдено: {len(self.rows)}"
            if self.rows
            else (
                "Автомобили с текущими ремонтами не найдены. Измените поиск или отключите фильтр."
                if self.current_only.isChecked()
                else "Ничего не найдено. Измените поиск или добавьте автомобиль."
                if self.search.text().strip()
                else "Начните с «Добавить автомобиль». Затем откройте карточку и добавьте ремонт."
            )
        )
        self.open_button.setEnabled(bool(self.rows))
        if self.rows:
            self.table.selectRow(
                next((i for i, row in enumerate(self.rows) if row["id"] == selected_id), 0)
            )

    def add_vehicle(self):
        def created(vehicle_id):
            self.search.clear()
            self.refresh()
            open_vehicle_card(self.records, vehicle_id, self)

        return open_vehicle_editor(self.records, self, after_create=created)

    def import_txt(self):
        from app.ui.txt_import import ImportDialog

        ImportDialog(self.records, self).exec()
        self.refresh()

    def open_vehicle(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.rows):
            return open_vehicle_card(self.records, self.rows[row]["id"], self)
