import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMessageBox

from app.bootstrap import create_window
from app.database.connection import initialize_database
from app.services.records import Records
from app.ui.vehicles import RepairDialog, VehicleCard, VehicleDialog


@pytest.fixture
def records(tmp_path):
    path = tmp_path / "forms.sqlite3"
    initialize_database(path)
    return Records(path)


def close_clean(dialog):
    if hasattr(dialog, "original"):
        dialog.original = dialog.state()
    dialog.reject()


def test_vehicle_and_repair_forms_persist_through_actual_buttons(application, records):
    vehicle = VehicleDialog(records)
    vehicle.show()
    application.processEvents()
    vehicle.fields["plate"].setText("ТЕСТ 777")
    vehicle.fields["make"].setText("Тестовая машина")
    QTest.mouseClick(vehicle.save_button, Qt.MouseButton.LeftButton)
    assert vehicle.result() == QDialog.DialogCode.Accepted
    car = vehicle.vehicle_id
    editor = RepairDialog(records, car)
    editor.show()
    application.processEvents()
    text = "Заменили масло.\nРекомендовано проверить тормоза.\n" * 20
    editor.description.setPlainText(text)
    editor.mileage.setText("120 000")
    editor.amount.setText("1 500,50")
    QTest.mouseClick(editor.save_button, Qt.MouseButton.LeftButton)
    assert editor.result() == QDialog.DialogCode.Accepted
    saved = Records(records.path).repairs(car)[0]
    assert saved["description"] == text
    assert saved["completed"] == 0
    assert saved["amount_kopecks"] == 150050
    finished = RepairDialog(records, car, saved)
    finished.show()
    application.processEvents()
    QTest.mouseClick(finished.finish_button, Qt.MouseButton.LeftButton)
    assert Records(records.path).repairs(car)[0]["completed"] == 1
    card = VehicleCard(records, car)
    card.show()
    application.processEvents()
    assert card.history.rowCount() == 1
    assert card.preview.toPlainText() == text
    assert "120 000" in card.summary.text()
    card.close()


def test_unsaved_cancel_discard_and_save_on_escape(application, records, monkeypatch):
    car = records.save_vehicle({"plate": "ТЕСТ"})
    editor = RepairDialog(records, car)
    editor.show()
    application.processEvents()
    editor.description.setPlainText("Не потерять этот текст")
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Cancel)
    QTest.keyClick(editor.description, Qt.Key.Key_Escape)
    assert editor.isVisible()
    assert editor.description.toPlainText() == "Не потерять этот текст"
    assert records.repairs(car) == []
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Save)
    editor.reject()
    assert not editor.isVisible()
    assert records.repairs(car)[0]["description"] == "Не потерять этот текст"
    discarded = RepairDialog(records, car)
    discarded.show()
    discarded.description.setPlainText("Не сохранять")
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Discard)
    discarded.close()
    assert not discarded.isVisible()
    assert len(records.repairs(car)) == 1


def test_invalid_input_keeps_form_and_duplicate_requires_confirmation(
    application, records, monkeypatch
):
    records.save_vehicle({"plate": "ТЕСТ"})
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No)
    vehicle = VehicleDialog(records)
    vehicle.show()
    vehicle.fields["plate"].setText("ДРУГОЙ")
    vehicle.fields["year"].setText("abcd")
    vehicle.save()
    assert vehicle.isVisible()
    assert len(records.vehicles()) == 1
    vehicle.fields["year"].clear()
    vehicle.fields["plate"].setText("ТЕСТ")
    vehicle.save()
    assert vehicle.isVisible()
    assert len(records.vehicles()) == 1
    assert warnings
    close_clean(vehicle)


def test_ctrl_f_and_ctrl_s(application, records):
    window = create_window(records.path)
    window.show()
    window.activateWindow()
    application.processEvents()
    QTest.keyClick(window, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    assert window.pages.currentWidget() is window.vehicles_page
    assert window.vehicles_page.search.hasFocus()
    car = records.save_vehicle({"plate": "ТЕСТ"})
    editor = RepairDialog(records, car, parent=window)
    editor.show()
    editor.activateWindow()
    application.processEvents()
    editor.description.setFocus()
    editor.description.setPlainText("Сохранено клавиатурой")
    QTest.keyClick(editor.description, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert editor.result() == QDialog.DialogCode.Accepted
    assert records.repairs(car)[0]["description"] == "Сохранено клавиатурой"
    window.close()


def test_enter_saves_repair_without_completing_it(application, records):
    car = records.save_vehicle({"plate": "ТЕСТ"})
    editor = RepairDialog(records, car)
    editor.show()
    editor.amount.setFocus()
    editor.amount.setText("1500")
    application.processEvents()

    QTest.keyClick(editor.amount, Qt.Key.Key_Return)

    assert editor.result() == QDialog.DialogCode.Accepted
    repair = records.repairs(car)[0]
    assert repair["amount_kopecks"] == 150000
    assert not repair["completed"]


def test_repair_saved_for_document_appears_even_when_editor_is_closed(
    application, records, monkeypatch
):
    from app.ui.documents import DocumentDialog

    car = records.save_vehicle({"plate": "ТЕСТ"})
    card = VehicleCard(records, car)
    assert not card.document_button.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(DocumentDialog, "exec", lambda _: QDialog.DialogCode.Rejected)

    def edit_then_close(editor):
        editor.description.setPlainText("Диагностика")
        editor.document_button.click()
        assert card.history.rowCount() == 1
        assert editor.save_notice.text() == "Ремонт сохранён."
        editor.cancel_button.click()
        return editor.result()

    monkeypatch.setattr(RepairDialog, "exec", edit_then_close)
    card.add_repair()

    assert card.document_button.isEnabled()
    assert card.save_notice.text() == "Ремонт сохранён."
    assert card.preview.toPlainText() == "Диагностика"
    assert len(records.repairs(car)) == 1
    card.close()


def test_edit_keeps_the_same_repair_selected_when_history_reorders(
    application, records, monkeypatch
):
    car = records.save_vehicle({"plate": "ТЕСТ"})
    edited = records.save_repair(car, {"visit_date": "2026-10-01", "description": "Старый"})
    records.save_repair(car, {"visit_date": "2026-10-10", "description": "Другой"})
    other = records.save_repair(car, {"visit_date": "2026-11-01", "description": "Новый"})
    card = VehicleCard(records, car)
    assert card.rows[card.history.currentRow()]["id"] == other
    card.history.selectRow(2)

    def edit_and_save(editor):
        editor.visit_date.setDate(QDate(2026, 10, 15))
        editor.description.setPlainText("Проверено")
        editor.save_button.click()
        return editor.result()

    monkeypatch.setattr(RepairDialog, "exec", edit_and_save)
    card.open_repair()
    assert card.history.currentRow() == 1
    assert card.rows[card.history.currentRow()]["id"] == edited
    assert card.preview.toPlainText() == "Проверено"
    card.close()


def test_current_repair_filter_combines_with_search(application, records):
    from app.ui.vehicles import VehiclesPage

    current = records.save_vehicle({"make": "Ford", "plate": "ТЕСТ 001"})
    complete = records.save_vehicle({"make": "Ford", "plate": "ТЕСТ 002"})
    deleted = records.save_vehicle({"make": "Honda", "plate": "ТЕСТ 003"})
    records.save_vehicle({"make": "Honda", "plate": "ТЕСТ 004"})
    records.save_repair(current, {"visit_date": "2026-11-20"})
    records.save_repair(complete, {"visit_date": "2026-11-20", "completed": True})
    discarded = records.save_repair(deleted, {"visit_date": "2026-11-20"})
    records.set_repair_deleted(deleted, discarded)
    page = VehiclesPage(records)
    assert page.table.rowCount() == 4

    page.current_only.click()
    assert [row["id"] for row in page.rows] == [current]
    page.search.setText("Honda")
    assert page.table.rowCount() == 0
    assert "отключите фильтр" in page.empty.text()

    page.current_only.click()
    assert page.table.rowCount() == 2
    page.close()
