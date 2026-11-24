import sqlite3
from contextlib import closing
from datetime import date

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMessageBox

from app.database.connection import (
    SCHEMA_V2,
    SCHEMA_VERSION,
    connect,
    initialize_database,
    restore_database,
)
from app.services.maintenance import Maintenance, add_months
from app.services.records import Records
from app.ui.maintenance import CallsPage, CompletionDialog, PlanDialog


@pytest.fixture
def data(tmp_path):
    path = tmp_path / "maintenance.sqlite3"
    initialize_database(path)
    records = Records(path)
    car = records.save_vehicle({"plate": "ТЕСТ", "contact_name": "Вымышленный", "phone": "000"})
    return records, Maintenance(path), car


def plan_values(**changes):
    return {
        "title": "Вымышленное ТО",
        "months": 6,
        "distance_km": None,
        "baseline_date": "2026-01-31",
        "baseline_km": None,
        "lead_days": 7,
        **changes,
    }


def test_month_ends_and_leap_year():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2024, 2, 29), 12) == date(2025, 2, 28)


def test_forecast_time_and_no_duplicate_tasks(data):
    records, service, car = data
    plan = service.save_plan(car, plan_values())
    for _ in range(4):
        tasks = Maintenance(records.path).tasks(date(2026, 8, 1))
        assert len(tasks) == 1
        assert tasks[0]["due_date"] == "2026-07-24"
        assert tasks[0]["plan_id"] == plan


def test_unknown_baseline_stays_unknown(data):
    _, service, car = data
    service.save_plan(car, plan_values(baseline_date=None))
    task = service.tasks()[0]
    assert task["due_date"] is None
    assert "Неизвестна" in task["details"]


def test_explicit_annual_estimate_is_labeled_and_not_a_fact(data):
    _, service, car = data
    service.save_plan(
        car,
        plan_values(
            months=None,
            distance_km=10000,
            baseline_date="2026-01-01",
            baseline_km=100000,
            annual_km=20000,
            lead_days=0,
        ),
    )
    task = service.tasks(date(2026, 2, 1))[0]
    assert task["due_date"] == "2026-07-03"
    assert "Прогноз по пробегу" in task["details"]
    assert "указанному владельцем" in task["details"]
    assert "Порог достигнут" not in task["details"]


def test_actual_mileage_takes_priority_and_future_or_unverified_readings_are_ignored(data):
    records, service, car = data
    values = {"visit_date": "2026-03-01", "km": 111000}
    records.save_repair(car, values)
    records.save_repair(car, {"visit_date": "2026-11-01", "km": 90000}, allow_unverified=True)
    records.save_repair(car, {"visit_date": "2027-01-01", "km": 150000})
    service.save_plan(
        car,
        plan_values(
            baseline_date="2026-01-01",
            baseline_km=100000,
            distance_km=10000,
            lead_days=0,
        ),
    )
    task = service.tasks(date(2026, 11, 10))[0]
    assert task["due_date"] == "2026-03-01"
    assert "Порог достигнут по фактическому" in task["details"]
    assert "111 000" in task["details"]


def test_distance_forecast_uses_latest_reading_as_anchor(data):
    records, service, car = data
    records.save_repair(car, {"visit_date": "2026-03-01", "km": 102000})
    service.save_plan(
        car,
        plan_values(
            months=None,
            distance_km=10000,
            baseline_date="2026-01-01",
            baseline_km=100000,
            annual_km=36500,
            lead_days=0,
        ),
    )
    assert service.tasks(date(2026, 3, 2))[0]["due_date"] == "2026-05-20"


def test_postpone_and_no_answer_survive_recalculation_and_reopen(data):
    records, service, car = data
    service.save_plan(car, plan_values())
    task = service.tasks()[0]
    service.record_call(task["id"], "postponed", "2026-12-15", "По просьбе клиента")
    service.sync_tasks(date(2026, 12, 1))
    task = Maintenance(records.path).tasks()[0]
    assert task["due_date"] == "2026-12-15"
    service.record_call(task["id"], "no_answer", "2026-12-20")
    assert service.tasks()[0]["due_date"] == "2026-12-20"
    service.record_call(task["id"], "contacted")
    assert service.tasks() == []
    service.record_call(task["id"], "reopen")
    assert len(service.tasks()) == 1
    assert len(service.attempts(task["id"])) == 4


def test_repair_text_never_resets_cycle_and_completion_is_explicit(data):
    records, service, car = data
    plan_id = service.save_plan(car, plan_values())
    old_task = service.tasks()[0]
    repair = records.save_repair(
        car,
        {
            "visit_date": "2026-10-01",
            "description": "Масло заменено или рекомендовано?",
            "completed": True,
            "km": 120000,
        },
    )
    assert service.plans()[0]["cycle_no"] == 1
    service.record_completion(plan_id, "2026-10-01", 120000, repair)
    assert service.plans()[0]["cycle_no"] == 2
    assert service.tasks()[0]["id"] != old_task["id"]
    assert service.tasks()[0]["due_date"] == "2027-03-25"
    with connect(records.path) as connection:
        assert (
            connection.execute(
                "SELECT status FROM call_tasks WHERE id=?",
                (old_task["id"],),
            ).fetchone()[0]
            == "completed"
        )
    with pytest.raises(ValueError, match="уже отмечено"):
        service.record_completion(plan_id, "2026-10-01", 120000, repair)
    with pytest.raises(ValueError, match="завершён"):
        service.record_call(old_task["id"], "contacted")
    assert len(service.tasks()) == 1


def test_completion_checks_plan_start_mileage(data):
    _, service, car = data
    plan = service.save_plan(car, plan_values(baseline_km=80000))
    with pytest.raises(ValueError, match="пробег|Пробег"):
        service.record_completion(plan, "2026-10-01", 75000)
    assert service.plans()[0]["cycle_no"] == 1


@pytest.mark.parametrize("confirm_km", [False, True])
def test_completion_requires_separate_confirmation_for_unverified_km(application, data, confirm_km):
    records, service, car = data
    repair = records.save_repair(car, {"visit_date": "2026-10-01", "km": 50000, "completed": True})
    with connect(records.path) as connection:
        connection.execute("UPDATE repairs SET origin='txt' WHERE id=?", (repair,))
        connection.execute(
            "UPDATE odometer_readings SET verified=0,source='import' WHERE repair_id=?", (repair,)
        )
    plan = service.save_plan(car, plan_values())
    dialog = CompletionDialog(service, service.plans()[0])
    dialog.repairs.setCurrentIndex(dialog.repairs.findData(repair))
    assert dialog.km.text() == ""
    with pytest.raises(ValueError, match="свер"):
        service.record_completion(plan, "2026-10-01", 50000, repair)
    dialog.confirm.setChecked(True)
    if confirm_km:
        dialog.verify_reading.click()
    dialog.save()
    assert service.plans()[0]["baseline_km"] == (50000 if confirm_km else None)
    assert records.repairs(car)[0]["verified"] == 0
    if confirm_km:
        assert records.latest_mileage(car)["source"] == "maintenance"
    else:
        assert records.latest_mileage(car) is None


def test_refusal_disables_all_vehicle_invites_until_explicitly_reopened(data):
    _, service, car = data
    service.save_plan(car, plan_values())
    service.save_plan(car, plan_values(title="Другая работа"))
    task = service.tasks()[0]
    service.record_call(task["id"], "declined")
    assert service.tasks() == []
    service.save_plan(car, plan_values(title="Новое ТО после отказа"))
    assert service.tasks() == []
    service.record_call(task["id"], "reopen")
    assert len(service.tasks()) == 1


def test_update_rule_preserves_manual_date_and_rejects_silent_baseline_reset(data):
    _, service, car = data
    plan_id = service.save_plan(car, plan_values())
    task = service.tasks()[0]
    service.record_call(task["id"], "postponed", "2026-12-01")
    service.save_plan(car, plan_values(months=8), plan_id)
    assert service.tasks()[0]["due_date"] == "2026-12-01"
    with pytest.raises(ValueError, match="ТО выполнено"):
        service.save_plan(car, plan_values(baseline_date="2026-10-01"), plan_id)


def test_manual_completion_adds_real_mileage_with_source(data):
    records, service, car = data
    plan_id = service.save_plan(car, plan_values())
    service.record_completion(plan_id, "2026-10-01", 120000)
    latest = records.latest_mileage(car)
    assert latest["km"] == 120000
    assert latest["source"] == "maintenance"


def test_v2_migration_preserves_cars_and_keeps_v2_backup(tmp_path):
    path = tmp_path / "v2.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE app_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO app_metadata VALUES ('application', 'AutoService')")
        for statement in SCHEMA_V2:
            connection.execute(statement)
        connection.execute("INSERT INTO vehicles(make, plate) VALUES ('Тест', 'ДО МИГРАЦИИ')")
        connection.execute("PRAGMA user_version=2")
        connection.commit()
    initialize_database(path)
    assert Records(path).vehicles()[0]["plate"] == "до миграции"
    with connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    backup = next((tmp_path / "backups").glob("*.sqlite3"))
    with closing(sqlite3.connect(backup)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    with connect(path) as connection:
        connection.execute("UPDATE vehicles SET plate='ПОСЛЕ'")
    safety = restore_database(path, backup)
    assert Records(path).vehicles()[0]["plate"] == "до миграции"
    assert Records(safety).vehicles()[0]["plate"] == "ПОСЛЕ"
    with closing(sqlite3.connect(backup)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_plan_and_completion_forms_require_explicit_confirmation(application, data, monkeypatch):
    _, service, car = data
    dialog = PlanDialog(service, car)
    dialog.show()
    application.processEvents()
    assert dialog.fields["months"].text() == ""
    assert not dialog.known_date.isChecked()
    dialog.fields["title"].setText("Тестовая работа")
    dialog.fields["months"].setText("6")
    QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert dialog.result() == QDialog.DialogCode.Accepted
    completion = CompletionDialog(service, service.plans()[0])
    completion.show()
    application.processEvents()
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: None)
    completion.save()
    assert completion.isVisible()
    assert service.plans()[0]["cycle_no"] == 1
    completion.confirm.setChecked(True)
    QTest.mouseClick(completion.save_button, Qt.MouseButton.LeftButton)
    assert completion.result() == QDialog.DialogCode.Accepted
    assert service.plans()[0]["cycle_no"] == 2


def test_calls_page_unknown_and_due_filters(application, data):
    records, service, car = data
    service.save_plan(car, plan_values())
    service.save_plan(car, plan_values(title="Без истории", baseline_date=None))
    page = CallsPage(records)
    page.show()
    application.processEvents()
    assert page.table.rowCount() == 1
    page.filter.setCurrentIndex(page.filter.findData("unknown"))
    assert page.table.rowCount() == 1
    assert page.rows[0]["title"] == "Без истории"
    assert "Неизвестна" in page.details.toPlainText()
    page.close()
