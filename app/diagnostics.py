"""Явная проверка выпуска на вымышленных данных, вне рабочей базы.

Только служебные --self-test НОВЫЙ_КАТАЛОГ и --self-test-reopen ТОТ_ЖЕ_КАТАЛОГ.
Нормальный запуск этот модуль не импортирует. Существующие каталоги не затираются.
"""

import json
import sqlite3
import sys
import traceback
from contextlib import closing
from copy import deepcopy
from datetime import date
from hashlib import sha256
from pathlib import Path

from PySide6.QtCore import QBuffer, QDate, QIODevice, QSize
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtTest import QTest

from app.bootstrap import create_application, create_window
from app.database.connection import (
    SCHEMA_V2,
    SCHEMA_VERSION,
    backup_database,
    connect,
    initialize_database,
    restore_database,
)
from app.services.appointments import Appointments
from app.services.catalog import VehicleCatalog
from app.services.documents import Documents
from app.services.export_data import export_database
from app.services.maintenance import Maintenance
from app.services.records import Records
from app.services.txt_import import TxtImport
from app.ui.customers import CustomerVehiclePicker
from app.ui.documents import DocumentDialog, print_pdf
from app.ui.help import HelpDialog
from app.version import VERSION

MARKER = "AutoService synthetic release check v1"


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def prepare(root):
    path = root / "self-test.sqlite3"
    # Старый формат создаётся только внутри нового выделенного каталога.
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE app_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        connection.execute("INSERT INTO app_metadata VALUES ('application','AutoService')")
        for statement in SCHEMA_V2:
            connection.execute(statement)
        connection.execute("INSERT INTO vehicles(id,plate) VALUES (1,'ТЕСТ-СТАРЫЙ')")
        connection.execute("PRAGMA user_version=2")
        connection.commit()
    initialize_database(path)
    check(Records(path).vehicle(1)["plate"] == "ТЕСТ-СТАРЫЙ", "Миграция потеряла машину")
    old_copy = next((root / "backups").glob("*.sqlite3"))
    with closing(sqlite3.connect(old_copy)) as connection:
        check(connection.execute("PRAGMA user_version").fetchone()[0] == 2, "Копия до миграции")

    records = Records(path)
    car = records.save_vehicle(
        {
            "make": "Вымышленная марка",
            "plate": "ТЕСТ-ПИЛОТ",
            "contact_name": "Вымышленный контакт",
            "phone": "000-000",
        }
    )
    repair = records.save_repair(
        car,
        {
            "visit_date": "2026-01-31",
            "km": 100000,
            "description": "Тест выпуска. Данные вымышлены.",
            "amount_kopecks": 150050,
            "completed": True,
        },
    )
    maintenance = Maintenance(path)
    maintenance.save_plan(
        car,
        {"title": "Только тестовое ТО", "months": 6, "baseline_date": "2026-01-31", "lead_days": 7},
    )
    task = maintenance.tasks(date(2026, 8, 1))[0]
    check(task["due_date"] == "2026-07-24", "Срок тестового ТО")
    maintenance.record_call(task["id"], "postponed", "2026-12-01", "Тест переноса")

    documents = Documents(path)
    simple = documents.create(repair)
    detailed = documents.create(
        repair,
        "detailed",
        [{"kind": "work", "title": "Тестовая работа", "quantity": "1", "price": "1500,50"}],
    )
    content = documents.pdf_content(detailed["id"])
    (root / "receipt.pdf").write_bytes(content)

    source_path = root / "synthetic.txt"
    raw = (
        "Марка: Вымышленная\r\nГосномер: ТЕСТ-ИМПОРТ\r\n"
        "10.02.2026\r\nПробег: 123456\r\nИтого: 100,50\r\nТест импорта.\r\n"
    ).encode("cp1251")
    source_path.write_bytes(raw)
    importer = TxtImport(path)

    def reviewed(source):
        return [
            {
                "source": source,
                "fragment": fragment,
                "values": deepcopy(fragment.values),
                "confirmed": True,
                "target": source.bound_vehicle_id or f"file:{source.digest}",
                "allow_duplicate": False,
            }
            for fragment in source.fragments
        ]

    report = importer.commit(reviewed(importer.preview(source_path)))
    check(len(report["imported"]) == 1, "Импорт записи")
    check(
        not importer.commit(reviewed(importer.preview(source_path)))["imported"], "Повтор импорта"
    )
    imported = report["imported"][0]
    check(
        records.latest_mileage(imported["vehicle_id"]) is None, "Достоверность импортного пробега"
    )
    check(source_path.read_bytes() == raw, "Оригинал TXT изменён")

    customer_id = records.vehicle(car)["contact_id"]
    records.save_contact(
        {"name": "Вымышленный клиент (исправлен)", "phone": "000-000"}, customer_id
    )
    standalone_customer = records.save_contact(
        {"name": "Клиент без машины (тест)", "phone": "000-999"}
    )
    check(records.find_contacts("000000")[0]["vehicle_count"] == 1, "Общий список клиентов")
    check("Volkswagen" in VehicleCatalog(records).makes(), "Встроенные марки")
    check("Golf" in VehicleCatalog(records).models("VW"), "Встроенные модели")
    carried = records.save_repair(
        car,
        {
            "visit_date": "2026-02-20",
            "carried_km": 100000,
            "carried_on": "2026-01-31",
            "description": "Вымышленная запись без нового замера",
        },
    )
    carried_document = documents.create(carried)
    records.set_repair_deleted(imported["vehicle_id"], imported["repair_id"])
    check(not records.repairs(imported["vehicle_id"]), "Удалённый TXT скрыт")
    check(
        importer.preview(source_path).fragments[0].imported,
        "Удалённый TXT не импортируется повторно",
    )
    appointment_id = Appointments(path).save(
        {
            "starts_at": "2026-12-01T10:30",
            "duration_minutes": 60,
            "vehicle_id": car,
            "contact_text": "",
            "reason": "Диагностика подвески",
            "status": "scheduled",
        }
    )
    appointment_repair = records.save_repair(
        car,
        {"visit_date": "2026-12-01", "description": "Подвеску проверили"},
        appointment_id=appointment_id,
    )
    export_database(path, root / "txt-export", "txt")
    export_database(path, root / "excel-export", "xlsx")
    check((root / "excel-export" / "AutoService.xlsx").is_file(), "Выгрузка XLSX")
    backup = backup_database(path, root / "release-backup.sqlite3")
    records.save_vehicle(records.vehicle(car) | {"plate": "ИЗМЕНЕНО-ДЛЯ-ТЕСТА"}, car)
    restore_database(path, backup)
    check(records.vehicle(car)["plate"] == "ТЕСТ-ПИЛОТ", "Восстановление машины")
    check(documents.pdf_content(detailed["id"]) == content, "Восстановление PDF")
    check(importer.provenance(imported["repair_id"])["original_bytes"] == raw, "Архив TXT в копии")
    return {
        "marker": MARKER,
        "version": VERSION,
        "frozen": bool(getattr(sys, "frozen", False)),
        "car": car,
        "customer_id": customer_id,
        "standalone_customer": standalone_customer,
        "repair": repair,
        "appointment_repair": appointment_repair,
        "carried_repair": carried,
        "carried_document": carried_document["id"],
        "documents": [simple["id"], detailed["id"]],
        "imported_repair": imported["repair_id"],
        "pdf_sha256": sha256(content).hexdigest(),
        "txt_sha256": sha256(raw).hexdigest(),
        "stage": "prepared",
    }


def reopen(root, report):
    application = create_application(["AutoService release check"])
    path = root / "self-test.sqlite3"
    window = create_window(path)
    try:
        window.show()
        records = window.records
        check(records.vehicle(report["car"])["plate"] == "ТЕСТ-ПИЛОТ", "Повторный запуск: машина")
        check(
            next(r for r in records.repairs(report["car"]) if r["id"] == report["repair"])[
                "amount_kopecks"
            ]
            == 150050,
            "Повторный запуск: сумма",
        )
        carried = next(
            r for r in records.repairs(report["car"]) if r["id"] == report["carried_repair"]
        )
        check(
            carried["carried_km"] == 100000 and carried["km"] is None,
            "Перенесённый пробег после перезапуска",
        )
        check(
            records.latest_mileage(report["car"])["recorded_on"] == "2026-01-31",
            "Прежняя дата замера",
        )
        with connect(path) as connection:
            archived_vehicle = connection.execute(
                "SELECT vehicle_id FROM repairs WHERE id=?", (report["imported_repair"],)
            ).fetchone()[0]
        check(not records.repairs(archived_vehicle), "Удаление после backup/restore и перезапуска")
        records.set_repair_deleted(archived_vehicle, report["imported_repair"], False)
        check(records.repairs(archived_vehicle)[0]["origin"] == "txt", "Восстановление TXT")
        records.set_repair_deleted(archived_vehicle, report["imported_repair"])
        task = Maintenance(path).tasks(date(2026, 8, 1))[0]
        check(task["manual_date"] == "2026-12-01", "Повторный запуск: перенос звонка")
        check(
            records.contact(report["customer_id"])["name"] == "Вымышленный клиент (исправлен)",
            "Повторный запуск: изменение общего клиента",
        )
        check(
            records.contact(report["standalone_customer"])["name"] == "Клиент без машины (тест)",
            "Повторный запуск: клиент без машины",
        )
        check(
            records.vehicle(report["car"])["contact_id"] == report["customer_id"],
            "Связь клиента и машины",
        )
        check("Golf" in VehicleCatalog(records).models("Volkswagen"), "Справочник внутри EXE")
        documents = Documents(path)
        content = documents.pdf_content(report["documents"][1])
        check(sha256(content).hexdigest() == report["pdf_sha256"], "Повторный запуск: PDF")
        original = TxtImport(path).provenance(report["imported_repair"])["original_bytes"]
        check(sha256(original).hexdigest() == report["txt_sha256"], "Повторный запуск: TXT")
        with connect(path) as connection:
            check(
                connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION,
                "Версия схемы",
            )
            check(
                connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
                "Целостность базы",
            )
        check(
            window.pages.currentWidget() is window.vehicles_page, "Стартовая страница автомобилей"
        )
        check(window.navigation.item(1).text() == "Клиенты", "Раздел клиентов после автомобилей")
        check(window.navigation.item(2).text() == "Звонки", "Раздел звонков после клиентов")
        check(window.grab().save(str(root / "startup-window.png")), "Снимок стартовой страницы")
        for index in range(window.navigation.count()):
            window.navigation.setCurrentRow(index)
            application.processEvents()
            check(window.pages.currentIndex() == index, "Переключение раздела")
        check(window.navigation.item(3).text() == "Записи", "Записи перед настройками")
        check(not window.windowIcon().pixmap(16, 16).isNull(), "Значок программы")
        window.navigation.setCurrentRow(window.pages.indexOf(window.appointments_page))
        window.appointments_page.day.setDate(QDate(2026, 12, 1))
        check(
            window.appointments_page.rows[0]["repair_id"] == report["appointment_repair"],
            "Связь записи и ремонта после восстановления и перезапуска",
        )
        QTest.qWait(150)
        check(window.grab().save(str(root / "appointments-window.png")), "Снимок записей")
        window.appointments_page.month_button.click()
        QTest.qWait(150)
        check(window.grab().save(str(root / "appointments-month.png")), "Снимок календаря записей")
        window.navigation.setCurrentRow(window.pages.indexOf(window.calls_page))
        window.calls_page.filter.setCurrentIndex(window.calls_page.filter.findData("all"))
        window.calls_page.view_mode.setCurrentIndex(1)
        window.calls_page.calendar.setSelectedDate(QDate(2026, 12, 1))
        check(window.calls_page.rows[0]["manual_date"] == "2026-12-01", "Одна задача в календаре")
        QTest.qWait(200)
        check(window.grab().save(str(root / "calls-calendar.png")), "Снимок календаря")
        window.navigation.setCurrentRow(window.pages.indexOf(window.customers_page))
        window.customers_page.search.setText("000000")
        QTest.qWait(200)
        check(window.customers_page.table.rowCount() == 1, "Поиск клиентов в EXE")
        check(window.customers_page.rows[0]["id"] == report["customer_id"], "Клиент из общей базы")
        check(window.grab().save(str(root / "customers-window.png")), "Снимок клиентов")
        picker = CustomerVehiclePicker(records, report["customer_id"], window)
        picker.search.setText("тестпилот")
        check(picker.table.rowCount() == 1, "Поиск существующей машины в карточке клиента")
        check(not picker.attach_button.isEnabled(), "Машину нельзя привязать повторно")
        picker.close()
        window.navigation.setCurrentRow(window.pages.indexOf(window.vehicles_page))
        window.vehicles_page.search.setText("тестпилот")
        QTest.qWait(350)
        check(window.vehicles_page.table.rowCount() == 1, "Поиск в собранном интерфейсе")
        check(window.grab().save(str(root / "window.png")), "Снимок окна")
        window.navigation.setCurrentRow(window.navigation.count() - 1)
        window.theme_select.setCurrentIndex(window.theme_select.findData("dark"))
        check(application.property("appearanceTheme") == "dark", "Переключение темы")
        QTest.qWait(150)
        check(window.grab().save(str(root / "dark-settings.png")), "Снимок тёмной темы")
        window.theme_select.setCurrentIndex(window.theme_select.findData("light"))

        help_dialog = HelpDialog(window)
        check(help_dialog.tabs.count() == 2, "Только инструкция в приложении")
        for index in range(2):
            check(
                len(help_dialog.tabs.widget(index).toPlainText()) > 500,
                "Инструкция в комплекте EXE",
            )
        help_dialog.show()
        QTest.qWait(200)
        check(help_dialog.grab().save(str(root / "help-window.png")), "Снимок инструкции")
        help_dialog.close()

        buffer = QBuffer()
        buffer.setData(content)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        pdf = QPdfDocument()
        pdf.load(buffer)
        check(pdf.status() == QPdfDocument.Status.Ready and pdf.pageCount() >= 1, "QtPdf загрузка")
        check(not pdf.render(0, QSize(600, 850)).isNull(), "QtPdf рендеринг")
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(root / "printed.pdf"))
        print_pdf(pdf, printer)
        check((root / "printed.pdf").stat().st_size > 1000, "Путь печати")
        pdf.close()
        buffer.close()
        dialog = DocumentDialog(records, report["repair"], window)
        dialog.history.setCurrentIndex(dialog.history.findData(report["documents"][1]))
        dialog.show()
        QTest.qWait(800)
        check(dialog.grab().save(str(root / "document-window.png")), "Снимок документа")
        dialog.close()
        report.update(stage="passed", schema=SCHEMA_VERSION)
    finally:
        window.close()
        application.processEvents()
    return report


def command(arguments):
    if len(arguments) != 2 or arguments[0] not in ("--self-test", "--self-test-reopen"):
        return 2
    root = Path(arguments[1]).resolve()
    report_path = root / "release-check.json"
    if arguments[0] == "--self-test":
        try:
            root.mkdir(parents=True, exist_ok=False)
        except OSError:
            return 2
        report = {"marker": MARKER, "stage": "failed"}
    else:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            check(report.get("marker") == MARKER, "Нужен каталог служебной проверки")
        except (OSError, ValueError, RuntimeError):
            return 2
    try:
        report = prepare(root) if arguments[0] == "--self-test" else reopen(root, report)
        code = 0
    except Exception:
        report.update(stage="failed", error=traceback.format_exc())
        code = 1
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return code
