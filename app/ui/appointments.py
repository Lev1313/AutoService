"""Запись клиентов на приезд с переходом в обычную форму ремонта."""

from datetime import datetime

from PySide6.QtCore import QDate, QDateTime, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.appointments import ACTIVE_STATUSES, STATUSES, Appointments
from app.ui.vehicles import ERRORS, RepairDialog, SafeDialog, make_table, plain_label

ISO = Qt.DateFormat.ISODate


def vehicle_label(vehicle):
    model = " ".join(part for part in (vehicle["make"], vehicle["model"]) if part)
    identity = vehicle["plate"] or vehicle["vin"]
    return " · ".join(part for part in (model, identity) if part)


class AppointmentDialog(SafeDialog):
    def __init__(self, records, day, parent, *, appointment=None, vehicle_id=None, reason=""):
        super().__init__(parent)
        self.records = records
        self.service = Appointments(records.path)
        self.appointment = appointment or {}
        self.setWindowTitle("Изменить запись" if appointment else "Новая запись")
        self.setMinimumWidth(485)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        now = QDateTime.currentDateTime()
        now.setDate(day)
        self.starts_at = QDateTimeEdit(now)
        self.starts_at.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.starts_at.setCalendarPopup(True)
        if appointment:
            self.starts_at.setDateTime(QDateTime.fromString(appointment["starts_at"], ISO))
        form.addRow("Дата и время приезда", self.starts_at)
        self.duration = QSpinBox()
        self.duration.setRange(0, 1440)
        self.duration.setSpecialValueText("Не указана")
        self.duration.setSuffix(" мин")
        self.duration.setValue(self.appointment.get("duration_minutes") or 0)
        form.addRow("Плановая длительность", self.duration)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Госномер, телефон, марка или модель")
        self.vehicle = QComboBox()
        self.search.textChanged.connect(self.load_vehicles)
        self.load_vehicles("", self.appointment.get("vehicle_id", vehicle_id))
        form.addRow("Найти автомобиль", self.search)
        form.addRow("Автомобиль", self.vehicle)
        self.contact = QLineEdit(self.appointment.get("contact_text", ""))
        self.contact.setPlaceholderText("Телефон или обозначение машины, если её пока нет в базе")
        form.addRow("Контакт / машина", self.contact)
        self.reason = QLineEdit(self.appointment.get("reason", reason))
        form.addRow("Причина визита", self.reason)
        self.status = QComboBox()
        for value, label in STATUSES.items():
            self.status.addItem(label, value)
        self.status.setCurrentIndex(
            self.status.findData(self.appointment.get("status", "scheduled"))
        )
        form.addRow("Статус", self.status)
        if self.appointment.get("repair_id"):
            self.search.setEnabled(False)
            self.vehicle.setEnabled(False)
            self.status.setEnabled(False)
        layout.addLayout(form)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        save = QPushButton("Сохранить")
        save.setDefault(True)
        save.clicked.connect(self.save)
        row.addWidget(save)
        layout.addLayout(row)
        self.prepare_guard()

    def load_vehicles(self, query, selected=None):
        selected = self.vehicle.currentData() if selected is None else selected
        self.vehicle.clear()
        self.vehicle.addItem("Без привязки", None)
        for vehicle in self.records.vehicles(query):
            contact = vehicle["phone"] or vehicle["contact_name"]
            label = " · ".join(part for part in (vehicle_label(vehicle), contact) if part)
            self.vehicle.addItem(label, vehicle["id"])
        if not query and selected is not None and self.vehicle.findData(selected) < 0:
            vehicle = self.records.vehicle(selected)
            self.vehicle.addItem(vehicle_label(vehicle), selected)
        index = self.vehicle.findData(selected)
        if query and index <= 0 and self.vehicle.count() == 2:
            index = 1
        self.vehicle.setCurrentIndex(max(0, index))

    def state(self):
        return {
            "starts_at": self.starts_at.dateTime().toString(ISO)[:16],
            "duration_minutes": self.duration.value() or None,
            "vehicle_id": self.vehicle.currentData(),
            "contact_text": self.contact.text().strip(),
            "reason": self.reason.text().strip(),
            "status": self.status.currentData(),
        }

    def save(self):
        values = self.state()
        try:
            if values["vehicle_id"] is None and not values["contact_text"]:
                raise ValueError("Выберите автомобиль или укажите телефон либо обозначение машины.")
            time_changed = any(
                values[key] != self.appointment.get(key)
                for key in ("starts_at", "duration_minutes")
            )
            if values["status"] in ACTIVE_STATUSES and (
                time_changed or self.appointment.get("status") not in ACTIVE_STATUSES
            ):
                overlaps = self.service.overlaps(
                    values["starts_at"], values["duration_minutes"], self.appointment.get("id")
                )
                if overlaps:
                    other_time = datetime.fromisoformat(overlaps[0]).strftime("%d.%m %H:%M")
                    answer = QMessageBox.question(
                        self,
                        "Пересечение записей",
                        f"На это время уже есть запись ({other_time}). Всё равно сохранить?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if answer != QMessageBox.StandardButton.Yes:
                        return
            self.saved_id = self.service.save(values, self.appointment.get("id"))
        except ERRORS as error:
            QMessageBox.warning(self, "Не удалось сохранить запись", str(error))
            return
        self.accept()


class AppointmentsPage(QWidget):
    def __init__(self, records):
        super().__init__()
        self.records = records
        self.service = Appointments(records.path)
        self.rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = plain_label("Записи на ремонт")
        title.setObjectName("title")
        layout.addWidget(title)
        toolbar = QHBoxLayout()
        self.day = QDateEdit(QDate.currentDate())
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("ddd, dd.MM.yyyy")
        self.day.setAccessibleName("Дата расписания")
        previous = QPushButton("‹")
        previous.setAccessibleName("Предыдущий день")
        previous.clicked.connect(lambda: self.day.setDate(self.day.date().addDays(-1)))
        toolbar.addWidget(previous)
        toolbar.addWidget(self.day)
        following = QPushButton("›")
        following.setAccessibleName("Следующий день")
        following.clicked.connect(lambda: self.day.setDate(self.day.date().addDays(1)))
        toolbar.addWidget(following)
        today = QPushButton("Сегодня")
        today.clicked.connect(lambda: self.day.setDate(QDate.currentDate()))
        toolbar.addWidget(today)
        toolbar.addStretch()
        add = QPushButton("Новая запись")
        add.clicked.connect(lambda: self.edit())
        toolbar.addWidget(add)
        layout.addLayout(toolbar)
        self.table = make_table(("Приезд", "Автомобиль", "Контакт", "Причина визита", "Статус"))
        self.table.setAccessibleName("Записи выбранного дня")
        self.table.cellActivated.connect(lambda *_: self.open_selected())
        self.table.itemSelectionChanged.connect(self.update_selection)
        layout.addWidget(self.table, 1)
        self.empty = plain_label("На этот день записей нет.")
        layout.addWidget(self.empty)
        actions = QHBoxLayout()
        self.repair_button = QPushButton("Открыть ремонт")
        self.repair_button.clicked.connect(self.open_selected)
        actions.addWidget(self.repair_button)
        self.edit_button = QPushButton("Изменить запись")
        self.edit_button.clicked.connect(lambda: self.edit(self.selected()))
        actions.addWidget(self.edit_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.day.dateChanged.connect(self.refresh)

    def selected(self):
        index = self.table.currentRow()
        return self.rows[index] if 0 <= index < len(self.rows) else None

    def update_selection(self):
        row = self.selected()
        self.edit_button.setEnabled(row is not None)
        self.repair_button.setEnabled(
            bool(row and row["vehicle_id"] is not None and row["status"] in ACTIVE_STATUSES)
        )

    def refresh(self, *_):
        selected = self.selected()
        selected_id = selected["id"] if selected else None
        self.rows = self.service.for_day(self.day.date().toPython())
        self.table.clearContents()
        self.table.setRowCount(len(self.rows))
        now = datetime.now()
        for index, row in enumerate(self.rows):
            starts_at = datetime.fromisoformat(row["starts_at"])
            time_text = starts_at.strftime("%H:%M")
            if row["duration_minutes"]:
                time_text += f" · {row['duration_minutes']} мин"
            status = STATUSES[row["status"]]
            if row["status"] == "scheduled" and starts_at < now.replace(second=0, microsecond=0):
                status = "Время прошло"
            contact = " · ".join(part for part in (row["contact_name"], row["phone"]) if part)
            values = (
                time_text,
                vehicle_label(row),
                row["contact_text"] or contact,
                row["reason"],
                status,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(index, column, item)
            if row["id"] == selected_id:
                self.table.selectRow(index)
        self.empty.setVisible(not self.rows)
        self.update_selection()

    def edit(self, appointment=None, *, vehicle_id=None, reason=""):
        day = QDate.currentDate() if vehicle_id is not None else self.day.date()
        dialog = AppointmentDialog(
            self.records, day, self, appointment=appointment, vehicle_id=vehicle_id, reason=reason
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        self.day.setDate(dialog.starts_at.date())
        self.refresh()
        for index, row in enumerate(self.rows):
            if row["id"] == dialog.saved_id:
                self.table.selectRow(index)
                self.table.scrollToItem(self.table.item(index, 0))
                break
        return True

    def open_selected(self):
        row = self.selected()
        if row is None:
            return
        if row["vehicle_id"] is None or row["status"] not in ACTIVE_STATUSES:
            self.edit(row)
            return
        repair = None
        if row["repair_id"] is not None:
            repair = next(
                (
                    repair
                    for repair in self.records.repairs(row["vehicle_id"])
                    if repair["id"] == row["repair_id"]
                ),
                None,
            )
            if repair is None:
                QMessageBox.warning(
                    self, "Ремонт удалён", "Восстановите связанный ремонт в карточке автомобиля."
                )
                return
        RepairDialog(
            self.records,
            row["vehicle_id"],
            repair,
            self,
            appointment={**row, "vehicle_label": vehicle_label(row)},
        ).exec()
        self.refresh()
