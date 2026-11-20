from datetime import date

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from app.bootstrap import create_window
from app.database.connection import initialize_database
from app.services.appointments import Appointments
from app.services.records import Records
from app.ui.appointments import AppointmentDialog
from app.ui.vehicles import RepairDialog


def test_calendar_overlap_and_repair_link_are_independent_of_edits(tmp_path):
    path = tmp_path / "test.sqlite3"
    initialize_database(path)
    records = Records(path)
    appointments = Appointments(path)
    vehicle_id = records.save_vehicle({"make": "Тест"})
    values = {
        "starts_at": "2026-11-20T23:30",
        "duration_minutes": 60,
        "vehicle_id": vehicle_id,
        "contact_text": "",
        "reason": "Диагностика",
        "status": "scheduled",
    }
    entry = appointments.save(values)
    assert appointments.overlaps("2026-11-21T00:00", None) == [values["starts_at"]]
    assert appointments.overlaps("2026-11-21T00:30", None) == []
    appointments.save({**values, "status": "cancelled"}, entry)
    assert appointments.overlaps("2026-11-21T00:00", None) == []
    appointments.save({**values, "starts_at": "2026-11-21T08:00"}, entry)
    assert appointments.for_day(date(2026, 11, 20)) == []
    repair_values = {"visit_date": "2026-11-21", "description": "Проверили подвеску"}
    repair_id = records.save_repair(vehicle_id, repair_values, appointment_id=entry)
    with pytest.raises(ValueError, match="Запись изменилась"):
        records.save_repair(vehicle_id, repair_values, appointment_id=entry)
    assert [row["id"] for row in records.repairs(vehicle_id)] == [repair_id]
    row = appointments.for_day(date(2026, 11, 21))[0]
    assert (row["id"], row["status"], row["repair_id"]) == (entry, "arrived", repair_id)


def test_calendar_row_opens_and_reopens_standard_repair(application, tmp_path, monkeypatch):
    path = tmp_path / "test.sqlite3"
    window = create_window(path)
    try:
        vehicle_id = window.records.save_vehicle({"make": "VW", "plate": "А123ТЕ"})
        page = window.appointments_page
        window.navigation.setCurrentRow(window.pages.indexOf(page))
        window.show()
        application.processEvents()
        form = AppointmentDialog(window.records, QDate.currentDate(), page)
        form.search.setText("а123те")
        assert form.vehicle.currentData() == vehicle_id
        form.reason.setText("Замена масла")
        form.save()
        page.refresh()
        assert page.table.rowCount() == 1
        opened = []

        def interact(dialog):
            opened.append(dialog.repair.get("id"))
            assert dialog.vehicle_id == vehicle_id
            assert dialog.appointment["reason"] == "Замена масла"
            if len(opened) == 2:
                assert dialog.description.toPlainText() == ""
                dialog.description.setPlainText("Масло заменили")
                assert dialog.save()
            if len(opened) == 3:
                assert dialog.description.toPlainText() == "Масло заменили"

        monkeypatch.setattr(RepairDialog, "exec", interact)
        cell = page.table.visualItemRect(page.table.item(0, 0)).center()
        QTest.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton, pos=cell)
        QTest.mouseDClick(page.table.viewport(), Qt.MouseButton.LeftButton, pos=cell)
        assert opened == [None]
        assert page.rows[0]["status"] == "scheduled"
        assert window.records.repairs(vehicle_id) == []

        page.repair_button.click()
        repair_id = page.rows[0]["repair_id"]
        assert repair_id is not None
        assert page.rows[0]["status"] == "arrived"
        reloaded = Appointments(path).for_day(QDate.currentDate().toPython())
        assert reloaded[0]["repair_id"] == repair_id

        page.table.setFocus()
        QTest.keyClick(page.table, Qt.Key.Key_Return)
        assert opened == [None, None, repair_id]

        page.mode.setCurrentText("Месяц")
        assert page.views.currentWidget() is page.calendar
        (visit,) = page.calendar.findChildren(QPushButton, "calendarVisit")
        assert "А123ТЕ" in visit.text()
        visit.click()
        assert opened == [None, None, repair_id, repair_id]
    finally:
        window.close()


def test_time_picker_gives_half_hour_slots_and_preserves_old_minutes(application, tmp_path):
    day = QDate(2026, 11, 21)
    window = create_window(tmp_path / "test.sqlite3")
    page = window.appointments_page
    try:
        form = AppointmentDialog(window.records, day, page)
        assert form.arrival_date.calendarPopup()
        assert [form.arrival_time.itemText(index) for index in range(48)] == [
            f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0, 30)
        ]
        form.arrival_time.setCurrentText("10:30")
        form.arrival_date.setDate(day.addDays(1))
        assert form.state()["starts_at"] == "2026-11-22T10:30"

        old = {**form.state(), "starts_at": "2026-11-21T15:49", "contact_text": "VW Polo"}
        page.service.save(old)
        entry = page.service.for_day(day.toPython())[0]
        edit = AppointmentDialog(window.records, day, page, appointment=entry)
        assert edit.arrival_time.currentText() == "15:49"
        edit.reason.setText("Диагностика")
        edit.save()
        saved = page.service.for_day(day.toPython())[0]
        assert saved["starts_at"] == old["starts_at"]
        assert saved["reason"] == "Диагностика"
    finally:
        window.close()


def test_month_grid_shows_visits_and_opens_full_day(application, tmp_path):
    day = QDate(2026, 12, 2)
    window = create_window(tmp_path / "test.sqlite3")
    page = window.appointments_page
    try:
        for index in range(3):
            page.service.save(
                {
                    "starts_at": f"2026-12-02T{10 + index}:30",
                    "duration_minutes": None,
                    "vehicle_id": None,
                    "contact_text": f"Машина {index + 1}",
                    "reason": "ТО",
                    "status": "scheduled",
                }
            )
        window.navigation.setCurrentRow(window.pages.indexOf(page))
        page.day.setDate(day.addDays(-1))
        page.mode.setCurrentText("Месяц")
        assert [
            button.text() for button in page.calendar.findChildren(QPushButton, "calendarVisit")
        ] == [
            "10:30 Машина 1",
            "11:30 Машина 2",
        ]
        more = next(
            button for button in page.calendar.findChildren(QPushButton) if button.text() == "Ещё 1"
        )
        more.click()
        assert page.views.currentWidget() is page.day_view
        assert page.day.date() == day
        assert page.table.rowCount() == 3
    finally:
        window.close()
