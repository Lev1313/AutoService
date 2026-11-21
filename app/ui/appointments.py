"""Запись клиентов на приезд с переходом в обычную форму ремонта."""

from collections import defaultdict
from datetime import datetime

from PySide6.QtCore import QDate, QLocale, Qt, QTime, Signal
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QStyleOptionButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.appointments import ACTIVE_STATUSES, STATUSES, Appointments
from app.ui.theme import STATUS_TONE, StatusDelegate
from app.ui.vehicles import (
    ERRORS,
    SafeDialog,
    make_table,
    open_repair_window,
    plain_label,
    primary_button,
    vehicle_label,
)

ISO = Qt.DateFormat.ISODate


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
        self.arrival_time = QComboBox()
        self.arrival_time.setStyleSheet("QComboBox { combobox-popup: 0; }")
        self.arrival_time.setMaxVisibleItems(10)
        times = [f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0, 30)]
        if appointment:
            saved_day, saved_time = appointment["starts_at"].split("T")
            day = QDate.fromString(saved_day, ISO)
            if saved_time not in times:
                times.append(saved_time)
                times.sort()
        else:
            now = QTime.currentTime()
            next_slot = min((now.hour() * 60 + now.minute() + 29) // 30, len(times) - 1)
            saved_time = times[next_slot]
        self.arrival_date = QDateEdit(day)
        self.arrival_date.setDisplayFormat("dd.MM.yyyy")
        self.arrival_date.setCalendarPopup(True)
        self.arrival_time.addItems(times)
        self.arrival_time.setCurrentText(saved_time)
        form.addRow("Дата приезда", self.arrival_date)
        form.addRow("Время приезда", self.arrival_time)
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
        save = primary_button("Сохранить")
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
        date_text = self.arrival_date.date().toString(ISO)
        return {
            "starts_at": f"{date_text}T{self.arrival_time.currentText()}",
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


class CalendarVisitButton(QPushButton):
    def __init__(self, time, name):
        super().__init__(f"{time} {name}")
        self.time = time
        self.name = name

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        with QPainter(self) as painter:
            self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)
            painter.setPen(option.palette.color(QPalette.ColorRole.ButtonText))
            text_rect = self.rect().adjusted(5, 0, -5, 0)
            alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            original_font = self.font()
            time_font = self.font()
            time_font.setBold(True)
            painter.setFont(time_font)
            painter.drawText(text_rect, alignment, self.time)
            text_rect.adjust(painter.fontMetrics().horizontalAdvance(self.time) + 4, 0, 0, 0)
            if text_rect.width() > 0:
                painter.setFont(original_font)
                name = painter.fontMetrics().elidedText(
                    self.name, Qt.TextElideMode.ElideRight, text_rect.width()
                )
                painter.drawText(text_rect, alignment, name)


class MonthCalendar(QScrollArea):
    day_requested = Signal(QDate)
    appointment_requested = Signal(object)

    def __init__(self, service):
        super().__init__()
        self.service = service
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAccessibleName("Записи на месяц")
        content = QWidget()
        self.grid = QGridLayout(content)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(4)
        for column in range(7):
            label = plain_label(
                QLocale().standaloneDayName(column + 1, QLocale.FormatType.ShortFormat)
            )
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(label, 0, column)
            self.grid.setColumnStretch(column, 1)
        self.setWidget(content)

    def refresh(self, date):
        while self.grid.count() > 7:
            cell = self.grid.takeAt(7).widget()
            cell.hide()
            cell.deleteLater()
        first = QDate(date.year(), date.month(), 1)
        first_visible = first.addDays(1 - first.dayOfWeek())
        weeks = (first.dayOfWeek() - 1 + first.daysInMonth() + 6) // 7
        for week in range(1, 7):
            self.grid.setRowStretch(week, int(week <= weeks))
        end = first_visible.addDays(weeks * 7)
        entries = defaultdict(list)
        for entry in self.service.between(first_visible.toPython(), end.toPython()):
            entries[entry["starts_at"][:10]].append(entry)
        today = QDate.currentDate()
        for offset in range(weeks * 7):
            day = first_visible.addDays(offset)
            cell = QFrame()
            cell.setObjectName("calendarDay")
            cell.setProperty("outsideMonth", day.month() != date.month())
            cell.setProperty("today", day == today)
            layout = QVBoxLayout(cell)
            layout.setContentsMargins(3, 3, 3, 3)
            layout.setSpacing(2)
            day_entries = entries[day.toString(ISO)]
            button = self.date_button(str(day.day()), day)
            button.setAccessibleName(day.toString("dd.MM.yyyy"))
            button.setProperty("today", day == today)
            layout.addWidget(button)
            for entry in day_entries[:2]:
                layout.addWidget(self.visit_button(entry))
            if len(day_entries) > 2:
                layout.addWidget(self.date_button(f"Ещё {len(day_entries) - 2}", day))
            layout.addStretch()
            cell.setMinimumHeight(layout.minimumSize().height())
            self.grid.addWidget(cell, offset // 7 + 1, offset % 7)

    def compact_button(self, button, name):
        button.setObjectName(name)
        button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        button.setMinimumHeight(button.fontMetrics().height() + 6)
        return button

    def date_button(self, text, day):
        button = self.compact_button(QPushButton(text), "calendarDate")
        button.clicked.connect(lambda: self.day_requested.emit(day))
        return button

    def visit_button(self, entry):
        time = entry["starts_at"][11:16]
        vehicle = vehicle_label(entry)
        label = entry["plate"] or vehicle or entry["contact_text"]
        button = self.compact_button(CalendarVisitButton(time, label), "calendarVisit")
        button.setProperty("arrived", entry["status"] == "arrived")
        button.setProperty("inactive", entry["status"] not in ACTIVE_STATUSES)
        details = (time, vehicle or label, entry["reason"], STATUSES[entry["status"]])
        button.setToolTip(" · ".join(part for part in details if part))
        button.setAccessibleName(button.toolTip())
        button.clicked.connect(lambda: self.appointment_requested.emit(entry))
        return button


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
        self.mode = QButtonGroup(self)
        self.day_button = QPushButton("День")
        self.month_button = QPushButton("Месяц")
        for index, button in enumerate((self.day_button, self.month_button)):
            button.setObjectName("appointmentView")
            button.setCheckable(True)
            self.mode.addButton(button, index)
            toolbar.addWidget(button)
        self.day_button.setChecked(True)
        self.day = QDateEdit(QDate.currentDate())
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("ddd, dd.MM.yyyy")
        self.day.setAccessibleName("Дата расписания")
        previous = QPushButton("‹")
        previous.setAccessibleName("Назад")
        previous.setFixedWidth(32)
        previous.clicked.connect(lambda: self.move_period(-1))
        toolbar.addWidget(previous)
        toolbar.addWidget(self.day)
        following = QPushButton("›")
        following.setAccessibleName("Вперёд")
        following.setFixedWidth(32)
        following.clicked.connect(lambda: self.move_period(1))
        toolbar.addWidget(following)
        today = QPushButton("Сегодня")
        today.clicked.connect(lambda: self.day.setDate(QDate.currentDate()))
        toolbar.addWidget(today)
        toolbar.addStretch()
        add = primary_button("Новая запись")
        add.clicked.connect(lambda: self.edit())
        toolbar.addWidget(add)
        layout.addLayout(toolbar)
        self.views = QStackedWidget()
        self.day_view = QWidget()
        daily_layout = QVBoxLayout(self.day_view)
        daily_layout.setContentsMargins(0, 0, 0, 0)
        self.table = make_table(("Приезд", "Автомобиль", "Контакт", "Причина", "Статус"))
        self.table.setItemDelegateForColumn(4, StatusDelegate(self.table))
        header = self.table.horizontalHeader()
        for column, width in ((0, 92), (2, 110), (4, 115)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(column, width)
        self.table.setAccessibleName("Записи выбранного дня")
        self.table.cellActivated.connect(lambda *_: self.open_selected())
        self.table.itemSelectionChanged.connect(self.update_selection)
        daily_layout.addWidget(self.table, 1)
        self.empty = plain_label("На этот день записей нет.")
        daily_layout.addWidget(self.empty)
        actions = QHBoxLayout()
        self.repair_button = QPushButton("Открыть ремонт")
        self.repair_button.clicked.connect(self.open_selected)
        actions.addWidget(self.repair_button)
        self.edit_button = QPushButton("Изменить запись")
        self.edit_button.clicked.connect(lambda: self.edit(self.selected()))
        actions.addWidget(self.edit_button)
        actions.addStretch()
        daily_layout.addLayout(actions)
        self.calendar = MonthCalendar(self.service)
        self.calendar.day_requested.connect(self.show_day)
        self.calendar.appointment_requested.connect(self.open_appointment)
        self.views.addWidget(self.day_view)
        self.views.addWidget(self.calendar)
        layout.addWidget(self.views, 1)
        self.mode.idToggled.connect(self.change_view)
        self.day.dateChanged.connect(self.refresh)

    def change_view(self, index, checked):
        if not checked:
            return
        self.views.setCurrentIndex(index)
        self.day.setDisplayFormat("MMMM yyyy" if index else "ddd, dd.MM.yyyy")
        self.refresh()

    def move_period(self, step):
        day = self.day.date()
        self.day.setDate(
            day.addMonths(step) if self.month_button.isChecked() else day.addDays(step)
        )

    def show_day(self, day):
        self.day.setDate(day)
        self.day_button.setChecked(True)

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
        if self.views.currentWidget() is self.calendar:
            self.calendar.refresh(self.day.date())
            return
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
            tone = {
                "scheduled": "active",
                "arrived": "done",
                "cancelled": "muted",
                "no_show": "muted",
            }[row["status"]]
            if row["status"] == "scheduled" and starts_at < now.replace(second=0, microsecond=0):
                status = "Время прошло"
                tone = "attention"
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
                if column == 4:
                    item.setData(STATUS_TONE, tone)
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
        self.day.setDate(dialog.arrival_date.date())
        self.refresh()
        for index, row in enumerate(self.rows):
            if row["id"] == dialog.saved_id:
                self.table.selectRow(index)
                self.table.scrollToItem(self.table.item(index, 0))
                break
        return True

    def open_selected(self):
        row = self.selected()
        if row is not None:
            self.open_appointment(row)

    def open_appointment(self, row):
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
        return open_repair_window(
            self.records,
            row["vehicle_id"],
            self,
            repair,
            appointment=row,
        )
