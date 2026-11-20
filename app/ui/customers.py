"""Клиенты используют существующие контакты и связи автомобилей."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui.vehicles import (
    ERRORS,
    SafeDialog,
    VehicleCard,
    VehicleDialog,
    make_table,
    plain_label,
    primary_button,
    vehicle_label,
)


class CustomerDialog(SafeDialog):
    def __init__(self, records, contact_id=None, parent=None):
        super().__init__(parent)
        self.records = records
        self.contact_id = contact_id
        self.setWindowTitle("Изменить клиента" if contact_id else "Добавить клиента")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.phone = QLineEdit()
        for title, field in (("Имя", self.name), ("Телефон", self.phone)):
            field.setMaxLength(200)
            field.setAccessibleName(title)
            form.addRow(title, field)
        layout.addLayout(form)
        layout.addWidget(plain_label("Достаточно имени или телефона."))
        if contact_id is not None:
            contact = records.contact(contact_id)
            self.name.setText(contact["name"])
            self.phone.setText(contact["phone"])
            cars = records.contact_vehicles(contact_id)
            layout.addWidget(
                plain_label(
                    f"Связано автомобилей: {len(cars)}. Изменения клиента появятся в их карточках."
                )
            )
            if cars:
                linked_cars = QPlainTextEdit()
                linked_cars.setReadOnly(True)
                linked_cars.setMaximumHeight(110)
                linked_cars.setAccessibleName("Связанные автомобили клиента")
                linked_cars.setPlainText(
                    "\n".join(
                        " · ".join(filter(None, (car["make"], car["model"], car["plate"])))
                        or "Без названия"
                        for car in cars
                    )
                )
                layout.addWidget(linked_cars)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        self.save_button = primary_button("Сохранить")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        self.prepare_guard()

    def state(self):
        return {"name": self.name.text(), "phone": self.phone.text()}

    def save(self):
        try:
            values = self.state()
            duplicates = self.records.contact_duplicates(
                values["name"], values["phone"], self.contact_id
            )
            if (
                duplicates
                and QMessageBox.question(
                    self,
                    "Возможное совпадение",
                    "Такой телефон или клиент без телефона уже есть. Сохранить отдельного клиента?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
            self.contact_id = self.records.save_contact(values, self.contact_id)
        except ERRORS as error:
            QMessageBox.warning(self, "Не удалось сохранить клиента", str(error))
            return
        self.accept()


class CustomerVehiclePicker(QDialog):
    def __init__(self, records, contact_id, parent):
        super().__init__(parent)
        self.records, self.contact_id = records, contact_id
        self.vehicle_id = None
        self.create_new = False
        self.setWindowTitle("Добавить автомобиль клиенту")
        self.resize(760, 475)
        self.setMinimumSize(500, 340)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Госномер, VIN, марка или модель")
        self.search.setAccessibleName("Поиск автомобиля")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.table = make_table(("Автомобиль", "Госномер", "VIN", "Текущий клиент"))
        self.table.setAccessibleName("Автомобили в базе")
        self.table.itemSelectionChanged.connect(self.update_selection)
        self.table.cellDoubleClicked.connect(lambda *_: self.attach())
        layout.addWidget(self.table, 1)
        self.empty = plain_label("Автомобили не найдены. Измените поиск или создайте карточку.")
        layout.addWidget(self.empty)
        actions = QHBoxLayout()
        create = QPushButton("Создать автомобиль")
        create.clicked.connect(self.new_vehicle)
        actions.addWidget(create)
        actions.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.attach_button = primary_button("Привязать автомобиль")
        self.attach_button.setDefault(True)
        self.attach_button.clicked.connect(self.attach)
        actions.addWidget(self.attach_button)
        layout.addLayout(actions)
        self.rows = []
        self.refresh()
        self.search.setFocus()

    def refresh(self):
        try:
            self.rows = self.records.vehicles(self.search.text())
        except ERRORS as error:
            self.rows = []
            QMessageBox.warning(self, "Не удалось открыть автомобили", str(error))
        self.table.clearContents()
        self.table.setRowCount(len(self.rows))
        for index, car in enumerate(self.rows):
            owner = car["contact_name"] or car["phone"] or "Без клиента"
            if car["contact_id"] == self.contact_id:
                owner = "Уже добавлен"
            values = (
                " ".join(filter(None, (car["make"], car["model"]))) or "—",
                car["plate"] or "—",
                car["vin"] or "—",
                owner,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(index, column, item)
        self.empty.setVisible(not self.rows)
        if self.rows:
            self.table.selectRow(0)
        self.update_selection()

    def selected(self):
        index = self.table.currentRow()
        return self.rows[index] if 0 <= index < len(self.rows) else None

    def update_selection(self):
        car = self.selected()
        self.attach_button.setEnabled(car is not None and car["contact_id"] != self.contact_id)

    def attach(self):
        car = self.selected()
        if car is None or car["contact_id"] == self.contact_id:
            return
        if car["contact_id"] is not None:
            old_owner = car["contact_name"] or car["phone"] or "Клиент без имени"
            answer = QMessageBox.question(
                self,
                "Сменить клиента автомобиля?",
                f"{vehicle_label(car)} сейчас связан с клиентом «{old_owner}». "
                "Привязать автомобиль к выбранному клиенту?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            self.records.assign_vehicle_contact(
                car["id"], self.contact_id, previous_contact_id=car["contact_id"]
            )
        except ERRORS as error:
            QMessageBox.warning(self, "Не удалось привязать автомобиль", str(error))
            self.refresh()
            return
        self.vehicle_id = car["id"]
        self.accept()

    def new_vehicle(self):
        self.create_new = True
        self.accept()


class CustomerCard(QDialog):
    def __init__(self, records, contact_id, parent=None):
        super().__init__(parent)
        self.records, self.contact_id = records, contact_id
        self.setWindowTitle("Клиент")
        self.resize(850, 560)
        layout = QVBoxLayout(self)
        self.heading = plain_label("")
        self.heading.setObjectName("title")
        layout.addWidget(self.heading)
        self.phone = plain_label("")
        layout.addWidget(self.phone)
        actions = QHBoxLayout()
        self.edit_button = QPushButton("Изменить клиента")
        self.edit_button.clicked.connect(self.edit)
        self.add_vehicle_button = primary_button("Добавить автомобиль")
        self.add_vehicle_button.clicked.connect(self.add_vehicle)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.add_vehicle_button)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addWidget(plain_label("Автомобили клиента"))
        self.table = make_table(("Автомобиль", "Госномер", "VIN"))
        self.table.cellDoubleClicked.connect(lambda *_: self.open_vehicle())
        layout.addWidget(self.table, 1)
        self.empty = plain_label("")
        layout.addWidget(self.empty)
        buttons = QHBoxLayout()
        self.open_button = QPushButton("Открыть автомобиль")
        self.open_button.clicked.connect(self.open_vehicle)
        buttons.addWidget(self.open_button)
        buttons.addStretch()
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        shortcut = QShortcut(QKeySequence("Return"), self.table)
        shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        shortcut.activated.connect(self.open_vehicle)
        self.refresh()

    def refresh(self, vehicle_id=None):
        contact = self.records.contact(self.contact_id)
        self.heading.setText(contact["name"] or "Клиент без имени")
        self.phone.setText(contact["phone"] or "Телефон не указан")
        self.rows = self.records.contact_vehicles(self.contact_id)
        self.table.setRowCount(len(self.rows))
        for row, car in enumerate(self.rows):
            for column, value in enumerate(
                (
                    " ".join(filter(None, (car["make"], car["model"]))) or "Без названия",
                    car["plate"] or "—",
                    car["vin"] or "—",
                )
            ):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.empty.setText(
            f"Автомобилей: {len(self.rows)}" if self.rows else "У клиента пока нет автомобилей."
        )
        self.open_button.setEnabled(bool(self.rows))
        if self.rows:
            self.table.selectRow(
                next((i for i, car in enumerate(self.rows) if car["id"] == vehicle_id), 0)
            )

    def edit(self):
        if CustomerDialog(self.records, self.contact_id, self).exec():
            self.refresh()

    def add_vehicle(self):
        picker = CustomerVehiclePicker(self.records, self.contact_id, self)
        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        if not picker.create_new:
            self.refresh(picker.vehicle_id)
            return
        dialog = VehicleDialog(self.records, parent=self, contact_id=self.contact_id)
        if dialog.exec():
            self.refresh()
            VehicleCard(self.records, dialog.vehicle_id, self).exec()
            self.refresh()

    def open_vehicle(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.rows):
            VehicleCard(self.records, self.rows[row]["id"], self).exec()
            self.refresh()


class CustomersPage(QWidget):
    def __init__(self, records):
        super().__init__()
        self.records = records
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 30, 32, 24)
        heading = plain_label("Клиенты")
        heading.setObjectName("title")
        layout.addWidget(heading)
        actions = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setAccessibleName("Поиск клиентов")
        self.search.setPlaceholderText("Имя или телефон")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        actions.addWidget(self.search, 1)
        self.add_button = primary_button("Добавить клиента")
        self.add_button.clicked.connect(self.add_customer)
        actions.addWidget(self.add_button)
        layout.addLayout(actions)
        self.table = make_table(("Клиент", "Телефон", "Автомобилей"))
        self.table.setAccessibleName("Клиенты")
        self.table.cellDoubleClicked.connect(lambda *_: self.open_customer())
        layout.addWidget(self.table, 1)
        self.empty = plain_label("")
        layout.addWidget(self.empty)
        self.open_button = QPushButton("Открыть клиента")
        self.open_button.clicked.connect(self.open_customer)
        layout.addWidget(self.open_button, alignment=Qt.AlignmentFlag.AlignLeft)
        shortcut = QShortcut(QKeySequence("Return"), self.table)
        shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        shortcut.activated.connect(self.open_customer)
        self.refresh()

    def refresh(self):
        previous = self.table.currentRow()
        selected_id = (
            self.rows[previous]["id"] if 0 <= previous < len(getattr(self, "rows", [])) else None
        )
        try:
            self.rows = self.records.find_contacts(self.search.text())
        except ERRORS as error:
            QMessageBox.warning(self, "Не удалось открыть клиентов", str(error))
            return
        self.table.setRowCount(len(self.rows))
        for row, contact in enumerate(self.rows):
            for column, value in enumerate(
                (
                    contact["name"] or "Без имени",
                    contact["phone"] or "—",
                    str(contact["vehicle_count"]),
                )
            ):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.empty.setText(
            f"Найдено: {len(self.rows)}"
            if self.rows
            else (
                "Клиенты не найдены. Измените поиск или добавьте клиента."
                if self.search.text().strip()
                else "Здесь появятся клиенты, добавленные отдельно или вместе с автомобилем."
            )
        )
        self.open_button.setEnabled(bool(self.rows))
        if self.rows:
            self.table.selectRow(
                next((i for i, row in enumerate(self.rows) if row["id"] == selected_id), 0)
            )

    def add_customer(self):
        dialog = CustomerDialog(self.records, parent=self)
        if dialog.exec():
            self.search.clear()
            self.refresh()
            CustomerCard(self.records, dialog.contact_id, self).exec()
            self.refresh()

    def open_customer(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.rows):
            CustomerCard(self.records, self.rows[row]["id"], self).exec()
            self.refresh()
