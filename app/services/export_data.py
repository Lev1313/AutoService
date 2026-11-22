"""Read-only consistent TXT/XLSX export, including immutable attachments."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep

from app.database.connection import SCHEMA_VERSION
from app.services.export_xlsx import write_workbook
from app.services.records import amount_text, mileage_text
from app.version import VERSION

TABLES = {
    "contacts": "Клиенты",
    "vehicles": "Автомобили",
    "repairs": "Ремонты",
    "appointments": "Записи на ремонт",
    "odometer_readings": "Пробеги",
    "vehicle_contacts": "Связи клиентов",
    "maintenance_plans": "ТО",
    "maintenance_events": "Выполнение ТО",
    "call_tasks": "Звонки",
    "call_attempts": "История звонков",
    "vehicle_call_preferences": "Разрешения звонков",
    "documents": "Документы",
    "document_drafts": "Черновики документов",
    "document_settings": "Реквизиты",
    "import_sources": "Источники TXT",
    "import_paths": "Пути TXT",
    "import_fragments": "Фрагменты TXT",
    "import_batches": "История импорта",
    "document_files": "Файлы PDF",
    "app_metadata": "Метаданные",
}
LABELS = {
    "id": "ID",
    "name": "Имя / название",
    "phone": "Телефон",
    "make": "Марка",
    "model": "Модель",
    "plate": "Госномер",
    "vin": "VIN",
    "year": "Год",
    "notes": "Примечания",
    "contact_id": "ID клиента",
    "vehicle_id": "ID автомобиля",
    "repair_id": "ID ремонта",
    "plan_id": "ID правила ТО",
    "task_id": "ID звонка",
    "document_id": "ID документа",
    "source_id": "ID источника TXT",
    "visit_date": "Дата ремонта",
    "starts_at": "Дата и время приезда",
    "duration_minutes": "Плановая длительность, мин",
    "contact_text": "Контакт / автомобиль со слов клиента",
    "reason": "Причина визита",
    "description": "Описание",
    "recommendations": "Рекомендации",
    "document_items": "Позиции документа",
    "amount_kopecks": "Стоимость, копейки",
    "completed": "Завершён (1/0)",
    "state_known": "Статус известен (1/0)",
    "carried_km": "Старый пробег ?",
    "carried_on": "Дата старого замера",
    "deleted_at": "Удалён",
    "km": "Пробег, км",
    "recorded_on": "Дата замера",
    "verified": "Подтверждён (1/0)",
    "origin": "Происхождение",
    "archived": "Архив (1/0)",
    "title": "Работа ТО",
    "months": "Интервал, месяцев",
    "distance_km": "Интервал, км",
    "lead_days": "Звонить за дней",
    "annual_km": "Прогноз, км/год",
    "baseline_date": "Начало цикла",
    "baseline_km": "Пробег начала",
    "cycle_no": "Номер цикла",
    "enabled": "Включено (1/0)",
    "performed_on": "Выполнено",
    "manual_date": "Ручная дата звонка",
    "calculated_date": "Расчётная дата звонка",
    "status": "Статус",
    "details": "Обоснование срока",
    "attempted_at": "Дата звонка",
    "outcome": "Результат",
    "note": "Примечание",
    "next_date": "Следующий звонок",
    "created_at": "Создан",
    "number": "Номер",
    "kind": "Вид",
    "payload": "Полный снимок JSON",
    "sha256": "SHA256",
    "original_name": "Имя оригинала",
    "encoding": "Кодировка",
    "decoded_text": "Полный исходный текст",
    "original_text": "Точный фрагмент",
    "imported_at": "Импортирован",
    "file": "Файл в комплекте",
    "bytes": "Размер, байт",
    "amount_rub": "Стоимость, рубли",
    "mileage_label": "Пробег с отметкой",
}
GUIDE = (
    "Снимок данных AutoService. TXT/Excel не предназначены для обратного восстановления базы.\n"
    "Для восстановления используйте отдельную резервную копию SQLite.\n"
    "Включены активные, архивные и удалённые записи со всеми ID и связями.\n"
    "Пустое значение в nullable-полях означает неизвестное, сумма 0 означает бесплатно.\n"
    "carried_km с ? — прежний замер, carried_on — его настоящая дата; это не новый замер.\n"
    "deleted_at — дата удаления ремонта. Замер удалённого ремонта не действует, но хранится.\n"
    "verified=0 — неподтверждённый замер. "
    "Отдельное выполнение ТО не отменяется удалением ремонта.\n"
    "amount_kopecks — точные целые копейки; amount_rub — точная запись рублей текстом.\n"
    "Даты ISO: ГГГГ-ММ-ДД. Телефоны, VIN, номера, пользовательские тексты — текст, без формул.\n"
    "Суммы, даты и статусы не угадываются; "
    "call_tasks содержит сохранённые расчёты на момент снимка.\n"
    "Длинный/неподдерживаемый Excel текст сохранён целиком в texts/ с относительной ссылкой.\n"
    "data.json содержит все табличные поля без ограничений Excel. "
    "Состав и SHA256 — manifest.json.\n"
    "Оригиналы TXT и выданные PDF включены только при выборе полного комплекта.\n"
    "Статусы: open — открыто, contacted — связались, completed — выполнено, "
    "declined — приглашения отключены.\n"
    "Результаты: no_answer — не дозвонились, postponed — перенос, reopen — вернуть в список.\n"
    "Записи: scheduled — запланирована, arrived — приехал, cancelled — отменена, "
    "no_show — не приехал.\n"
    "Документы: simple — простой заказ-наряд, detailed — подробный. "
    "Источники: manual/repair — вручную/ремонт, txt/import — TXT, maintenance — ТО.\n"
)


def header(column):
    return f"{LABELS.get(column, column)} ({column})" if column in LABELS else column


def export_database(path: Path, destination: Path, format="txt", include_files=True):
    if format not in ("txt", "xlsx"):
        raise ValueError("Выберите TXT или Excel.")
    path, destination = Path(path).resolve(), Path(destination).resolve()
    if destination.exists():
        raise FileExistsError("Эта папка уже существует. Выберите новое имя выгрузки.")
    if not destination.parent.is_dir():
        raise ValueError("Выберите существующую папку для выгрузки.")
    with (
        TemporaryDirectory(prefix=".autoservice-export-", dir=destination.parent) as temporary,
        closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection,
    ):
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        identity = connection.execute(
            "SELECT value FROM app_metadata WHERE key='application'"
        ).fetchone()
        if identity is None or identity[0] != "AutoService":
            raise ValueError("База AutoService не найдена.")
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ValueError("Сначала откройте базу совместимой версией AutoService.")
        stage = Path(temporary) / "ready"
        stage.mkdir()
        captured = datetime.now().isoformat(timespec="seconds")
        data, attachments = {}, []
        for table in TABLES:
            rows = []
            for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
                record = dict(row)
                if table in ("document_files", "import_sources"):
                    binary_key = "content" if table == "document_files" else "original_bytes"
                    binary = record.pop(binary_key)
                    record["bytes"] = len(binary)
                    if sha256(binary).hexdigest() != record["sha256"]:
                        raise ValueError(
                            "Повреждён сохранённый PDF или TXT. Используйте резервную копию."
                        )
                    record["file"] = None
                    if include_files:
                        relative = (
                            f"pdf/document-{record['document_id']}.pdf"
                            if table == "document_files"
                            else f"originals/source-{record['id']}.txt"
                        )
                        target = stage / relative
                        target.parent.mkdir(exist_ok=True)
                        target.write_bytes(binary)
                        record["file"] = relative
                        attachments.append(
                            {"table": table, "file": relative, "sha256": record["sha256"]}
                        )
                rows.append(record)
            data[table] = rows
        readings = {row["repair_id"]: row for row in data["odometer_readings"] if row["repair_id"]}
        for repair in data["repairs"]:
            reading = readings.get(repair["id"], {})
            repair["km"] = reading.get("km", repair["undated_km"])
            repair["verified"] = reading.get("verified")
            repair["mileage_label"] = mileage_text(repair)
            repair["amount_rub"] = (
                amount_text(repair["amount_kopecks"])
                if repair["amount_kopecks"] is not None
                else None
            )
        metadata = {
            "created_at": captured,
            "version": VERSION,
            "schema": connection.execute("PRAGMA user_version").fetchone()[0],
            "format": format,
            "include_files": include_files,
            "counts": {key: len(rows) for key, rows in data.items()},
            "attachments": attachments,
        }
        (stage / "data.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (stage / "Прочитайте.txt").write_text(
            f"AutoService {VERSION} · {captured}\n\n" + GUIDE, encoding="utf-8-sig"
        )
        if format == "txt":
            write_txt(stage, data, captured)
        else:

            def sidecar(name, text):
                target = stage / "texts" / f"{name}.txt"
                target.parent.mkdir(exist_ok=True)
                target.write_text(text, encoding="utf-8", newline="")
                return target.relative_to(stage).as_posix()

            sheets = [
                (
                    "Описание выгрузки",
                    ["Параметр", "Значение"],
                    [
                        ["Создана", captured],
                        ["Версия", VERSION],
                        ["Состав", GUIDE],
                        ["Оригиналы TXT/PDF", "Включены" if include_files else "Не включены"],
                    ]
                    + [[TABLES[table], len(rows)] for table, rows in data.items()],
                )
            ]
            for table, rows in data.items():
                columns = (
                    list(rows[0])
                    if rows
                    else [
                        r[1]
                        for r in connection.execute(f'PRAGMA table_info("{table}")')
                        if r[1] not in ("original_bytes", "content")
                    ]
                )
                sheets.append(
                    (
                        TABLES[table],
                        [header(c) for c in columns],
                        [[row.get(c) for c in columns] for row in rows],
                    )
                )
            write_workbook(stage / "AutoService.xlsx", sheets, sidecar)
        metadata["files"] = {
            p.relative_to(stage).as_posix(): sha256(p.read_bytes()).hexdigest()
            for p in sorted(stage.rglob("*"))
            if p.is_file()
        }
        (stage / "manifest.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # Windows rename refuses an existing destination, including a racing second export.
        connection.rollback()
        publish_directory(stage, destination)
    return metadata


def record_text(record):
    return "\n".join(
        f"{header(key)}: {'— неизвестно' if value is None else value}"
        for key, value in record.items()
    )


def write_txt(stage, data, captured):
    with (stage / "AutoService.txt").open("x", encoding="utf-8-sig", newline="") as output:
        output.write(f"AutoService · {captured}\n\n{GUIDE}\n")
        for table, rows in data.items():
            output.write(f"\n{'=' * 55}\n{TABLES[table]} · записей: {len(rows)}\n")
            for record in rows:
                output.write("\n" + record_text(record) + "\n")
    directory = stage / "vehicles"
    directory.mkdir()
    contacts = {row["id"]: row for row in data["contacts"]}
    for vehicle in data["vehicles"]:
        with (directory / f"vehicle-{vehicle['id']}.txt").open(
            "x", encoding="utf-8-sig", newline=""
        ) as output:
            output.write("АВТОМОБИЛЬ\n" + record_text(vehicle) + "\n\nКЛИЕНТ\n")
            output.write(
                record_text(contacts.get(vehicle["contact_id"], {})) + "\n\nИСТОРИЯ РЕМОНТОВ\n"
            )
            for repair in data["repairs"]:
                if repair["vehicle_id"] == vehicle["id"]:
                    output.write("\n" + record_text(repair) + "\n")


def publish_directory(stage, destination):
    # Windows scanners can briefly hold a newly written directory open.
    # Retry only transient access errors; never replace an existing export.
    for attempt in range(10):
        try:
            stage.rename(destination)
            return
        except PermissionError as error:
            if (
                destination.exists()
                or getattr(error, "winerror", None) not in (5, 32, 33)
                or attempt == 9
            ):
                raise
            sleep(0.2)
