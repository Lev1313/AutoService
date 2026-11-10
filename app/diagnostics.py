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

from PySide6.QtCore import QBuffer, QIODevice, QSize
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
from app.services.documents import Documents
from app.services.maintenance import Maintenance
from app.services.records import Records
from app.services.txt_import import TxtImport
from app.ui.documents import DocumentDialog, print_pdf
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
        "repair": repair,
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
            records.repairs(report["car"])[0]["amount_kopecks"] == 150050, "Повторный запуск: сумма"
        )
        task = Maintenance(path).tasks(date(2026, 8, 1))[0]
        check(task["manual_date"] == "2026-12-01", "Повторный запуск: перенос звонка")
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
        for index in range(window.navigation.count()):
            window.navigation.setCurrentRow(index)
            application.processEvents()
            check(window.pages.currentIndex() == index, "Переключение раздела")
        window.navigation.setCurrentRow(1)
        window.vehicles_page.search.setText("тестпилот")
        QTest.qWait(350)
        check(window.vehicles_page.table.rowCount() == 1, "Поиск в собранном интерфейсе")
        check(window.grab().save(str(root / "window.png")), "Снимок окна")

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
