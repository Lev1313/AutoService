import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMessageBox

from app.bootstrap import create_window
from app.database.connection import backup_database, initialize_database, restore_database
from app.services.catalog import VehicleCatalog, builtin_catalog
from app.services.documents import Documents
from app.services.records import Records
from app.ui.customers import CustomerCard, CustomerDialog, CustomerVehiclePicker
from app.ui.vehicles import VehicleDialog


@pytest.fixture
def records(tmp_path):
    path = tmp_path / "customers.sqlite3"
    initialize_database(path)
    return Records(path)


def link_car(records, client, **changes):
    contact = records.contact(client)
    return records.save_vehicle(
        {
            "plate": "ТЕСТ",
            "contact_id": client,
            "contact_name": contact["name"],
            "phone": contact["phone"],
            **changes,
        }
    )


def test_standalone_customer_search_and_reopen(records):
    client = records.save_contact({"name": "  Вымышленный клиент  ", "phone": "+7 (000) 111-22-33"})
    reopened = Records(records.path)
    assert reopened.contact(client)["name"] == "Вымышленный клиент"
    for query in ("ВЫМЫШЛЕННЫЙ", "70001112233", "клиент 111"):
        assert [row["id"] for row in reopened.find_contacts(query)] == [client]
    assert reopened.find_contacts()[0]["vehicle_count"] == 0
    with pytest.raises(ValueError, match="имя или телефон"):
        records.save_contact({})
    with pytest.raises(ValueError, match="не найден"):
        records.save_contact({"name": "Другой"}, client + 100)
    assert len(records.contacts()) == 1


def test_attach_existing_car_from_customer_card(application, records, monkeypatch):
    owner = records.save_contact({"name": "Тестовый первый"})
    customer = records.save_contact({"name": "Тестовый второй"})
    vehicle = link_car(records, owner, plate="ТЕСТ-А123")
    repair = records.save_repair(vehicle, {"visit_date": "2026-11-01", "description": "Тест"})
    decisions = iter((QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "question", lambda *_: next(decisions))

    def interact(picker):
        picker.search.setText("теста123")
        assert picker.table.rowCount() == 1
        assert picker.table.item(0, 3).text() == "Тестовый первый"
        picker.attach_button.click()
        assert records.vehicle(vehicle)["contact_id"] == owner
        picker.attach_button.click()
        return picker.result()

    monkeypatch.setattr(CustomerVehiclePicker, "exec", interact)
    card = CustomerCard(records, customer)
    card.add_vehicle_button.click()
    assert card.rows[0]["id"] == vehicle
    assert records.repairs(vehicle)[0]["id"] == repair
    assert records.contact_vehicles(owner) == []
    assert len(records.vehicles()) == 1
    picker = CustomerVehiclePicker(records, customer, card)
    assert picker.table.item(0, 3).text() == "Уже добавлен"
    assert not picker.attach_button.isEnabled()
    with pytest.raises(ValueError, match="Клиент автомобиля изменился"):
        records.assign_vehicle_contact(vehicle, owner, previous_contact_id=None)


def test_shared_customer_edit_preserves_cars_repairs_and_issued_pdf(records):
    client = records.save_contact({"name": "Вымышленный", "phone": "000"})
    first = link_car(records, client, plate="ТЕСТ 1")
    second = link_car(records, client, plate="ТЕСТ 2")
    repair = records.save_repair(first, {"visit_date": "2026-11-01", "description": "Тест"})
    documents = Documents(records.path)
    issued = documents.create(repair)
    content = documents.pdf_content(issued["id"])
    records.save_contact({"name": "Исправленный клиент", "phone": "000-123"}, client)
    for car in (first, second):
        assert records.vehicle(car)["contact_id"] == client
        assert records.vehicle(car)["contact_name"] == "Исправленный клиент"
        assert records.vehicle(car)["phone"] == "000-123"
    assert len(records.contacts()) == 1
    assert records.find_contacts()[0]["vehicle_count"] == 2
    assert records.repairs(first)[0]["id"] == repair
    assert documents.get(issued["id"])["payload"]["vehicle"]["contact_name"] == "Вымышленный"
    assert documents.pdf_content(issued["id"]) == content
    other = records.save_contact({"name": "Другой вымышленный", "phone": "000-456"})
    values = records.vehicle(first) | {
        "contact_id": other,
        "contact_name": "Другой вымышленный",
        "phone": "000-456",
    }
    records.save_vehicle(values, first)
    assert [car["id"] for car in records.contact_vehicles(client)] == [second]
    assert records.repairs(first)[0]["id"] == repair
    assert records.vehicle(second)["phone"] == "000-123"


def test_customer_backup_restore_includes_standalone_and_shared_links(records):
    client = records.save_contact({"name": "Общий", "phone": "000"})
    standalone = records.save_contact({"name": "Без машины"})
    first, second = (
        link_car(records, client, plate="ТЕСТ 1"),
        link_car(records, client, plate="ТЕСТ 2"),
    )
    copy = backup_database(records.path)
    records.save_contact({"name": "Изменено"}, client)
    restore_database(records.path, copy)
    assert Records(records.path).contact(client)["name"] == "Общий"
    assert records.contact(standalone)["name"] == "Без машины"
    assert {car["id"] for car in records.contact_vehicles(client)} == {first, second}


def test_catalog_available_on_empty_database_and_learns_saved_names(records):
    catalog = VehicleCatalog(records)
    assert len(builtin_catalog()) == 94
    assert "Volkswagen" in catalog.makes()
    assert "Golf" in catalog.models("Volkswagen")
    assert "Golf" in catalog.models("VW")
    assert "Octavia" in catalog.models("Шкода")
    assert "Golf" not in catalog.models("Toyota")
    assert catalog.models("") == []
    records.save_vehicle({"make": "Своя вымышленная марка", "model": "Своя модель"})
    records.save_vehicle({"make": "Volkswagen", "model": "Особая модель владельца"})
    reopened = VehicleCatalog(Records(records.path))
    assert "Своя вымышленная марка" in reopened.makes()
    assert reopened.models("Своя вымышленная марка") == ["Своя модель"]
    assert "Особая модель владельца" in reopened.models("volkswagen")
    assert "Особая модель владельца" not in reopened.models("Toyota")


def test_vehicle_suggestions_preserve_manual_model_and_link_selected_client(application, records):
    client = records.save_contact({"name": "Вымышленный контакт", "phone": "000-123"})
    dialog = VehicleDialog(records)
    dialog.fields["make"].setText("Volkswagen")
    assert "Golf" in dialog.model_suggestions.stringList()
    dialog.fields["model"].setText("Свой вариант модели")
    dialog.fields["make"].setText("Toyota")
    assert dialog.fields["model"].text() == "Свой вариант модели"
    assert "Golf" not in dialog.model_suggestions.stringList()
    dialog.show()
    application.processEvents()
    dialog.fields["contact_name"].setFocus()
    completer = dialog.contact_completers["contact_name"]
    completer.setCompletionPrefix("вымышленный")
    index = completer.completionModel().index(0, 0)
    completer.complete()
    application.processEvents()
    popup = completer.popup()
    popup.setCurrentIndex(index)
    QTest.mouseClick(
        popup.viewport(), Qt.MouseButton.LeftButton, pos=popup.visualRect(index).center()
    )
    assert dialog.contacts.currentData() == client
    assert dialog.fields["contact_name"].text() == "Вымышленный контакт"
    assert dialog.fields["phone"].text() == "000-123"
    dialog.save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert records.vehicle(dialog.vehicle_id)["contact_id"] == client
    assert len(records.contacts()) == 1


def test_add_car_from_client_and_customer_form_persists(application, records):
    customer = CustomerDialog(records)
    customer.show()
    customer.name.setText("Вымышленный клиент")
    customer.phone.setText("000-333")
    QTest.mouseClick(customer.save_button, Qt.MouseButton.LeftButton)
    assert customer.result() == QDialog.DialogCode.Accepted
    vehicle = VehicleDialog(records, contact_id=customer.contact_id)
    assert vehicle.fields["contact_name"].text() == "Вымышленный клиент"
    assert vehicle.fields["phone"].text() == "000-333"
    vehicle.fields["plate"].setText("ТЕСТ КЛИЕНТ")
    vehicle.save()
    assert vehicle.result() == QDialog.DialogCode.Accepted
    card = CustomerCard(records, customer.contact_id)
    assert card.table.rowCount() == 1
    assert card.rows[0]["id"] == vehicle.vehicle_id
    assert len(records.contacts()) == 1
    card.close()


def test_duplicate_phone_requires_explicit_decision(application, records, monkeypatch):
    records.save_contact({"name": "Первый вымышленный", "phone": "000-333"})
    dialog = CustomerDialog(records)
    dialog.name.setText("Второй вымышленный")
    dialog.phone.setText("000333")
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No)
    dialog.save()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert len(records.contacts()) == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    dialog.save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert len(records.contacts()) == 2


def test_customer_page_refreshes_existing_contacts_after_navigation(application, records):
    window = create_window(records.path)
    assert window.navigation.item(1).text() == "Клиенты"
    client = records.save_contact({"name": "Новый вымышленный", "phone": "000-777"})
    window.navigation.setCurrentRow(1)
    assert window.pages.currentWidget() is window.customers_page
    assert window.customers_page.rows[0]["id"] == client
    window.customers_page.search.setText("000777")
    assert window.customers_page.table.rowCount() == 1
    window.navigation.setCurrentRow(0)
    records.save_contact({"name": "Исправлен", "phone": "000-777"}, client)
    window.navigation.setCurrentRow(1)
    assert window.customers_page.table.item(0, 0).text() == "Исправлен"
    window.close()


def test_catalog_overlay_keeps_source_and_separates_sport_models(records):
    import hashlib
    from pathlib import Path

    from app.services.catalog import name_key

    source = Path(__file__).resolve().parents[1] / "app/assets/car_models.json"
    assert hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest() == (
        "42c74b1906cd43ff119081ad6dda1f015424b43c642c17eb436d5e252ec04f3b"
    )
    catalog = VehicleCatalog(records)
    for make in builtin_catalog():
        names = catalog.models(make)
        assert not any("serisi" in name.casefold() for name in names)
        assert len(names) == len({name_key(name) for name in names})
    assert {"1 серия", "1M", "M1", "3 серия", "M3", "X3", "X3 M"} <= set(catalog.models("БМВ"))
    assert {"A-класс", "C-класс", "E-класс", "W123", "W124", "GLC", "SLK"} <= set(
        catalog.models("Мерседес-Бенц")
    )
    assert "3 серия" not in catalog.models("Mercedes-Benz")


def test_catalog_legacy_names_deduplicate_without_changing_records(records):
    cars = []
    for model in ("3 Serisi (M3)", "3 Series", "3 серия", "Свой BMW 3", "X3M"):
        cars.append((records.save_vehicle({"make": "BMW", "model": model}), model))
    catalog = VehicleCatalog(Records(records.path))
    assert catalog.models("BMW").count("3 серия") == 1
    assert catalog.models("BMW").count("X3 M") == 1
    assert "Свой BMW 3" in catalog.models("BMW")
    assert not any("Serisi" in name for name in catalog.models("BMW"))
    for vehicle_id, model in cars:
        assert Records(records.path).vehicle(vehicle_id)["model"] == model
    records.save_vehicle({"make": "Своя марка", "model": "3 Serisi"})
    assert catalog.models("Своя марка") == ["3 Serisi"]


def test_model_alias_completion_inserts_display_name_and_preserves_old_card(application, records):
    vehicle_id = records.save_vehicle({"make": "BMW", "model": "3 Serisi (M3)"})
    dialog = VehicleDialog(records, vehicle_id)
    assert dialog.fields["model"].text() == "3 Serisi (M3)"
    assert dialog.state() == dialog.original
    completer = dialog.model_completer
    for query, expected in (("3 series", "3 серия"), ("3 serisi", "3 серия"), ("X3M", "X3 M")):
        completer.setCompletionPrefix(query)
        model = completer.completionModel()
        labels = [model.index(row, 0).data() for row in range(model.rowCount())]
        assert expected in labels
        index = model.index(labels.index(expected), 0)
        assert completer.pathFromIndex(index) == expected
    dialog.show()
    application.processEvents()
    dialog.fields["model"].setFocus()
    dialog.fields["model"].setText("3 series")
    completer.setCompletionPrefix("3 series")
    completer.complete()
    application.processEvents()
    popup = completer.popup()
    index = completer.completionModel().index(0, 0)
    popup.setCurrentIndex(index)
    QTest.mouseClick(
        popup.viewport(), Qt.MouseButton.LeftButton, pos=popup.visualRect(index).center()
    )
    assert dialog.fields["model"].text() == "3 серия"
    assert records.vehicle(vehicle_id)["model"] == "3 Serisi (M3)"
    dialog.save()
    assert Records(records.path).vehicle(vehicle_id)["model"] == "3 серия"
    second = VehicleDialog(records)
    second.fields["make"].setText("Mercedes")
    second.model_completer.setCompletionPrefix("c-class")
    assert second.model_completer.completionModel().index(0, 0).data() == "C-класс"
    second.done(0)
