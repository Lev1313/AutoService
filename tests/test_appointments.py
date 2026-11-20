from datetime import date

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QTest

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
    finally:
        window.close()
