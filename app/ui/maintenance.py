"""Настраиваемое ТО и список звонков без автоматического распознавания заметок."""

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
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

from app.services.maintenance import Maintenance, optional_integer
from app.services.records import Records
from app.ui.vehicles import ERRORS, SafeDialog, make_table, plain_label


def formatted_date(value):
    return QDate.fromString(value, "yyyy-MM-dd").toString("dd.MM.yyyy") if value else "Уточнить"


class PlanDialog(SafeDialog):
    def __init__(self, service, vehicle_id, plan=None, parent=None):
        super().__init__(parent)
        self.service, self.vehicle_id, self.plan = service, vehicle_id, plan or {}
        self.setWindowTitle("Правило ТО")
        self.resize(560, 480)
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain_label("Интервалы задаёт владелец. Достаточно месяцев или километров.")
        )
        form = QFormLayout()
        self.fields = {}
        for name, title, maximum in (
            ("title", "Название работы", 200),
            ("months", "Интервал, месяцев", 6),
            ("distance_km", "Интервал, км", 12),
            ("lead_days", "Позвонить заранее, дней", 6),
            ("annual_km", "Оценка владельца, км/год", 12),
            ("baseline_km", "Пробег последнего ТО", 12),
        ):
            widget = QLineEdit()
            widget.setMaxLength(maximum)
            widget.setAccessibleName(title)
            value = self.plan.get(name)
            widget.setText(str(value) if value is not None else "")
            if name != "title":
                widget.setPlaceholderText("Необязательно")
            self.fields[name] = widget
            form.addRow(title, widget)
        self.known_date = QCheckBox("Дата последнего ТО известна")
        self.known_date.setChecked(bool(self.plan.get("baseline_date")))
        form.addRow(self.known_date)
        self.last_date = QDateEdit(QDate.currentDate())
        self.last_date.setDisplayFormat("dd.MM.yyyy")
        self.last_date.setCalendarPopup(True)
        if self.plan.get("baseline_date"):
            self.last_date.setDate(QDate.fromString(self.plan["baseline_date"], "yyyy-MM-dd"))
        self.last_date.setEnabled(self.known_date.isChecked())
        self.known_date.toggled.connect(self.last_date.setEnabled)
        form.addRow("Последнее выполнение", self.last_date)
        if self.plan:
            self.known_date.setEnabled(False)
            self.last_date.setEnabled(False)
            self.fields["baseline_km"].setEnabled(False)
        self.enabled = QCheckBox("Показывать приглашения на это ТО")
        self.enabled.setChecked(bool(self.plan.get("enabled", service.invites_enabled(vehicle_id))))
        form.addRow(self.enabled)
        layout.addLayout(form)
        layout.addWidget(
            plain_label(
                "Пустая дата или пробег остаются неизвестными. "
                "Новое выполненное обслуживание отмечается отдельной кнопкой «ТО выполнено»."
            )
        )
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.save_button = QPushButton("Сохранить правило")
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        self.prepare_guard()

    def state(self):
        return {key: field.text() for key, field in self.fields.items()} | {
            "baseline_date": self.last_date.date().toString("yyyy-MM-dd")
            if self.known_date.isChecked()
            else None,
            "enabled": self.enabled.isChecked(),
        }

    def save(self):
        try:
            values = self.state()
            for key in self.fields:
                if key != "title":
                    values[key] = optional_integer(values[key], self.fields[key].accessibleName())
            self.service.save_plan(self.vehicle_id, values, self.plan.get("id"))
        except ERRORS as error:
            QMessageBox.warning(self, "Не удалось сохранить правило", str(error))
            return
        self.accept()


class CompletionDialog(SafeDialog):
    def __init__(self, service, plan, parent=None):
        super().__init__(parent)
        self.service, self.plan = service, plan
        self.setWindowTitle("ТО выполнено")
        self.resize(560, 400)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(f"Подтверждение выполненной работы: {plan['title']}"))
        form = QFormLayout()
        self.repairs = QComboBox()
        self.repairs.addItem("Отметить без привязки к ремонту", None)
        self.repair_rows = [
            row for row in Records(service.path).repairs(plan["vehicle_id"]) if row["completed"]
        ]
        for row in self.repair_rows:
            self.repairs.addItem(
                f"{formatted_date(row['visit_date'])} · {row['description'][:60]}",
                row["id"],
            )
        form.addRow("Основание", self.repairs)
        self.performed_on = QDateEdit(QDate.currentDate())
        self.performed_on.setCalendarPopup(True)
        self.performed_on.setDisplayFormat("dd.MM.yyyy")
        form.addRow("Дата выполнения", self.performed_on)
        self.km = QLineEdit()
        self.km.setMaxLength(12)
        self.km.setPlaceholderText("Необязательно")
        form.addRow("Пробег, км", self.km)
        self.note = QPlainTextEdit()
        self.note.setMaximumHeight(90)
        form.addRow("Примечание", self.note)
        self.confirm = QCheckBox("Подтверждаю, что эта работа выполнена")
        form.addRow(self.confirm)
        layout.addLayout(form)
        self.repairs.currentIndexChanged.connect(self.choose_repair)
        row = QHBoxLayout()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        row.addStretch()
        self.save_button = QPushButton("Начать новый цикл ТО")
        self.save_button.clicked.connect(self.save)
        row.addWidget(self.save_button)
        layout.addLayout(row)
        self.prepare_guard()

    def choose_repair(self):
        selected = next(
            (row for row in self.repair_rows if row["id"] == self.repairs.currentData()),
            None,
        )
        if selected:
            if selected["visit_date"]:
                self.performed_on.setDate(QDate.fromString(selected["visit_date"], "yyyy-MM-dd"))
            self.km.setText(str(selected["km"]) if selected["km"] is not None else "")
        self.confirm.setChecked(False)

    def state(self):
        return {
            "date": self.performed_on.date().toString("yyyy-MM-dd"),
            "km": self.km.text(),
            "repair_id": self.repairs.currentData(),
            "note": self.note.toPlainText(),
            "confirmed": self.confirm.isChecked(),
        }

    def save(self):
        try:
            values = self.state()
            if not values["confirmed"]:
                raise ValueError("Подтвердите, что выбранная работа действительно выполнена.")
            self.service.record_completion(
                self.plan["id"],
                values["date"],
                optional_integer(values["km"], "Пробег"),
                values["repair_id"],
                values["note"],
            )
        except ERRORS as error:
            QMessageBox.warning(self, "Не удалось отметить ТО", str(error))
            return
        self.accept()


class PlansDialog(QDialog):
    def __init__(self, records, vehicle_id, parent=None):
        super().__init__(parent)
        self.service = Maintenance(records.path)
        self.vehicle_id = vehicle_id
        self.setWindowTitle("ТО и напоминания автомобиля")
        self.resize(820, 450)
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain_label(
                "Добавьте нужные работы и интервалы. "
                "Содержание ремонта само по себе не сбрасывает срок ТО."
            )
        )
        self.table = make_table(("Работа", "Месяцев", "Километров", "Последнее ТО", "Приглашения"))
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.add_button = QPushButton("Добавить правило")
        self.add_button.clicked.connect(self.add)
        row.addWidget(self.add_button)
        self.edit_button = QPushButton("Изменить")
        self.edit_button.clicked.connect(self.edit)
        row.addWidget(self.edit_button)
        self.complete_button = QPushButton("ТО выполнено")
        self.complete_button.clicked.connect(self.complete)
        row.addWidget(self.complete_button)
        row.addStretch()
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit())
        self.refresh()

    def refresh(self):
        self.rows = self.service.plans(self.vehicle_id)
        self.table.setRowCount(len(self.rows))
        for index, plan in enumerate(self.rows):
            for column, value in enumerate(
                (
                    plan["title"],
                    str(plan["months"] or "—"),
                    str(plan["distance_km"] or "—"),
                    formatted_date(plan["baseline_date"]),
                    "Да" if plan["enabled"] else "Нет",
                )
            ):
                self.table.setItem(index, column, QTableWidgetItem(value))
        self.edit_button.setEnabled(bool(self.rows))
        self.complete_button.setEnabled(bool(self.rows))
        if self.rows:
            self.table.selectRow(0)

    def add(self):
        if PlanDialog(self.service, self.vehicle_id, parent=self).exec():
            self.refresh()

    def edit(self):
        row = self.table.currentRow()
        if (
            0 <= row < len(self.rows)
            and PlanDialog(self.service, self.vehicle_id, self.rows[row], self).exec()
        ):
            self.refresh()

    def complete(self):
        row = self.table.currentRow()
        if (
            0 <= row < len(self.rows)
            and CompletionDialog(self.service, self.rows[row], self).exec()
        ):
            self.refresh()


class CallResultDialog(SafeDialog):
    def __init__(self, service, task, parent=None):
        super().__init__(parent)
        self.service, self.task = service, task
        self.setWindowTitle("Результат звонка")
        self.resize(500, 350)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(f"{task['title']} · {task['plate'] or 'без госномера'}"))
        self.outcome = QComboBox()
        for title, value in (
            ("Связались — приглашение обработано", "contacted"),
            ("Не дозвонились — выбрать новую дату", "no_answer"),
            ("Перенести звонок", "postponed"),
            ("Не приглашать эту машину на ТО", "declined"),
            ("Вернуть в список звонков", "reopen"),
        ):
            self.outcome.addItem(title, value)
        layout.addWidget(self.outcome)
        self.next_date = QDateEdit(QDate.currentDate())
        self.next_date.setCalendarPopup(True)
        self.next_date.setDisplayFormat("dd.MM.yyyy")
        layout.addWidget(plain_label("Дата следующего звонка — для переноса или повторной попытки"))
        layout.addWidget(self.next_date)
        self.next_date.setEnabled(False)
        self.outcome.currentIndexChanged.connect(
            lambda: self.next_date.setEnabled(
                self.outcome.currentData() in ("no_answer", "postponed")
            )
        )
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText("Необязательное примечание")
        layout.addWidget(self.note)
        row = QHBoxLayout()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        row.addStretch()
        self.save_button = QPushButton("Сохранить результат")
        self.save_button.clicked.connect(self.save)
        row.addWidget(self.save_button)
        layout.addLayout(row)
        self.prepare_guard()

    def state(self):
        return {
            "outcome": self.outcome.currentData(),
            "date": self.next_date.date().toString("yyyy-MM-dd"),
            "note": self.note.toPlainText(),
        }

    def save(self):
        try:
            value = self.state()
            self.service.record_call(
                self.task["id"],
                value["outcome"],
                value["date"] if value["outcome"] in ("no_answer", "postponed") else None,
                value["note"],
            )
        except ERRORS as error:
            QMessageBox.warning(self, "Не удалось сохранить звонок", str(error))
            return
        self.accept()


class CallsPage(QWidget):
    def __init__(self, records, heading="Кому позвонить для приглашения на ТО"):
        super().__init__()
        self.records = records
        self.service = Maintenance(records.path)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = plain_label(heading)
        title.setObjectName("title")
        layout.addWidget(title)
        row = QHBoxLayout()
        self.filter = QComboBox()
        for name, key in (
            ("Сегодня и просроченные", "due"),
            ("Сегодня", "today"),
            ("Просроченные", "overdue"),
            ("Позже", "later"),
            ("Уточнить срок", "unknown"),
            ("Все открытые", "all"),
            ("Обработанные / отключённые", "handled"),
        ):
            self.filter.addItem(name, key)
        self.filter.currentIndexChanged.connect(self.refresh)
        row.addWidget(self.filter)
        row.addStretch()
        update = QPushButton("Обновить")
        update.clicked.connect(self.refresh)
        row.addWidget(update)
        layout.addLayout(row)
        self.table = make_table(("Позвонить", "Автомобиль", "Контакт / телефон", "Повод"))
        self.table.itemSelectionChanged.connect(self.describe)
        self.table.cellDoubleClicked.connect(lambda *_: self.open_car())
        layout.addWidget(self.table, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(180)
        layout.addWidget(self.details)
        row = QHBoxLayout()
        self.open_button = QPushButton("Открыть автомобиль")
        self.open_button.clicked.connect(self.open_car)
        row.addWidget(self.open_button)
        self.result_button = QPushButton("Результат звонка")
        self.result_button.clicked.connect(self.result)
        row.addWidget(self.result_button)
        row.addStretch()
        layout.addLayout(row)
        self.empty = plain_label(
            "Правила ТО добавляются в карточке автомобиля: «ТО и напоминания»."
        )
        layout.addWidget(self.empty)
        self.refresh()

    def refresh(self):
        try:
            key = self.filter.currentData()
            today = QDate.currentDate().toString("yyyy-MM-dd")
            rows = self.service.tasks(include_handled=(key == "handled"))

            def matches(task):
                due = task["due_date"]
                return {
                    "all": True,
                    "due": due is not None and due <= today,
                    "today": due == today,
                    "overdue": due is not None and due < today,
                    "later": due is not None and due > today,
                    "unknown": due is None,
                    "handled": task["status"] != "open" or not task["enabled"],
                }[key]

            self.rows = [task for task in rows if matches(task)]
            self.table.setRowCount(len(self.rows))
            for index, task in enumerate(self.rows):
                for column, value in enumerate(
                    (
                        formatted_date(task["due_date"]),
                        " ".join(filter(None, (task["make"], task["model"], task["plate"]))),
                        f"{task['contact_name'] or 'Без контакта'} · "
                        f"{task['phone'] or 'нет телефона'}",
                        task["title"],
                    )
                ):
                    self.table.setItem(index, column, QTableWidgetItem(value))
            self.open_button.setEnabled(bool(self.rows))
            self.result_button.setEnabled(bool(self.rows))
            self.details.clear()
            if self.rows:
                self.table.selectRow(0)
                self.describe()
        except ERRORS as error:
            QMessageBox.warning(self, "Не удалось обновить список", str(error))

    def describe(self):
        index = self.table.currentRow()
        if not 0 <= index < len(getattr(self, "rows", [])):
            return
        task = self.rows[index]
        attempts = self.service.attempts(task["id"])
        titles = {
            "contacted": "Связались",
            "no_answer": "Не дозвонились",
            "postponed": "Перенос",
            "declined": "Приглашения отключены",
            "reopen": "Возвращено в список",
        }
        history = "\n".join(
            f"{row['attempted_at']}: {titles[row['outcome']]} · {row['note']}" for row in attempts
        )
        self.details.setPlainText(
            ("Дата звонка задана вручную.\n" if task["manual_date"] else "")
            + task["details"]
            + (f"\n\nИстория звонков:\n{history}" if history else "")
        )

    def open_car(self):
        from app.ui.vehicles import VehicleCard

        row = self.table.currentRow()
        if 0 <= row < len(self.rows):
            VehicleCard(self.records, self.rows[row]["vehicle_id"], self).exec()
            self.refresh()

    def result(self):
        row = self.table.currentRow()
        if (
            0 <= row < len(self.rows)
            and CallResultDialog(self.service, self.rows[row], self).exec()
        ):
            self.refresh()
