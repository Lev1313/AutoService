"""Транзакционный импорт подтверждённых фрагментов с неизменяемым источником."""

import json
from datetime import date, datetime
from hashlib import sha256

from app.database.connection import backup_database, connect
from app.services.records import identity_key
from app.services.txt_parser import decode_text, read_source


def semantic_hash(values):
    normalized = {
        "visit_date": values.get("visit_date"),
        "km": values.get("km"),
        "amount_kopecks": values.get("amount_kopecks"),
        "description": " ".join(str(values.get("description", "")).casefold().split()),
    }
    return sha256(
        json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def validate_values(values):
    value = values.get("visit_date")
    if value is not None:
        try:
            date.fromisoformat(value)
        except (ValueError, TypeError):
            raise ValueError("Проверьте дату или явно оставьте её неизвестной.") from None
    for field, limit in (("km", 100_000_000), ("amount_kopecks", 99_999_999_999)):
        number = values.get(field)
        if number is not None and (type(number) is not int or not 0 <= number <= limit):
            raise ValueError("Пробег и стоимость должны быть неотрицательными числами.")
    if values.get("completed") is not None and type(values["completed"]) is not bool:
        raise ValueError("Уточните состояние записи.")
    if not str(values.get("description", "")).strip():
        raise ValueError("Не удаляйте весь текст записи. Исходный фрагмент сохранится отдельно.")
    if values.get("mileage_verified") and (value is None or values.get("km") is None):
        raise ValueError("Для достоверного пробега нужны дата и километры.")


class TxtImport:
    def __init__(self, path):
        self.path = path

    def preview(self, path, root=None, encoding=None):
        source = read_source(path, root, encoding)
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT id,decoded_text FROM import_sources WHERE sha256=?",
                (source.digest,),
            ).fetchone()
            if row:
                if row["decoded_text"] != source.text:
                    raise ValueError(
                        "Этот файл уже импортировался в другой кодировке. "
                        "Исправьте существующие записи или используйте копию базы до импорта."
                    )
                targets = connection.execute(
                    "SELECT DISTINCT vehicle_id FROM import_fragments WHERE source_id=?",
                    (row[0],),
                ).fetchall()
                if len(targets) == 1 and not any(
                    "Несколько значений" in note for note in source.warnings
                ):
                    source.bound_vehicle_id = targets[0][0]
                ranges = connection.execute(
                    "SELECT start_offset,end_offset FROM import_fragments WHERE source_id=?",
                    (row[0],),
                ).fetchall()
                for fragment in source.fragments:
                    fragment.imported = any(
                        start < fragment.end and end > fragment.start for start, end in ranges
                    )
        return source

    def possible_repair_duplicate(self, vehicle_id, values, connection=None):
        if connection is None:
            with connect(self.path) as current:
                return self.possible_repair_duplicate(vehicle_id, values, current)
        rows = connection.execute(
            """SELECT r.*, COALESCE(o.km,r.undated_km) AS km FROM repairs r
            LEFT JOIN odometer_readings o ON o.repair_id=r.id WHERE r.vehicle_id=?""",
            (vehicle_id,),
        ).fetchall()
        key = semantic_hash(values)
        return any(semantic_hash(dict(row)) == key for row in rows)

    def commit(self, entries, scan_errors=None):
        entries = list(entries)
        if not entries:
            raise ValueError("Нет подтверждённых записей для импорта.")
        sources = {}
        ranges = {}
        for entry in entries:
            if not entry.get("confirmed"):
                raise ValueError(
                    "Каждую выбранную запись нужно подтвердить в предварительном просмотре."
                )
            source, fragment = entry["source"], entry["fragment"]
            if source.digest not in sources:
                if sha256(source.raw).hexdigest() != source.digest:
                    raise ValueError("Нарушена целостность исходного файла.")
                if source.path.read_bytes() != source.raw:
                    raise ValueError(
                        f"Файл изменён после просмотра: {source.relative_name}. "
                        "Прочитайте его заново."
                    )
                if decode_text(source.raw, source.encoding)[0] != source.text:
                    raise ValueError("Текст не соответствует выбранной кодировке исходника.")
                sources[source.digest] = source
            if not 0 <= fragment.start < fragment.end <= len(source.text):
                raise ValueError("Некорректный диапазон исходной записи.")
            if source.text[fragment.start : fragment.end] != fragment.original:
                raise ValueError(
                    "Оригинальный фрагмент изменён. Исправляйте описание, сохраняя исходник."
                )
            for start, end in ranges.setdefault(source.digest, []):
                if start < fragment.end and end > fragment.start:
                    raise ValueError("Выбранные фрагменты одного файла перекрываются.")
            ranges[source.digest].append((fragment.start, fragment.end))
            validate_values(entry["values"])
        imported, skipped = [], []
        safety_copy = None
        with connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            pending = []
            for entry in entries:
                fragment, source = entry["fragment"], entry["source"]
                known = connection.execute(
                    "SELECT decoded_text FROM import_sources WHERE sha256=?",
                    (source.digest,),
                ).fetchone()
                if known and known[0] != source.text:
                    raise ValueError(
                        "Нельзя смешивать разные кодировки одного импортированного файла."
                    )
                overlap = connection.execute(
                    """SELECT f.repair_id FROM import_fragments f
                    JOIN import_sources s ON s.id=f.source_id
                    WHERE s.sha256=? AND f.start_offset<? AND f.end_offset>?""",
                    (source.digest, fragment.end, fragment.start),
                ).fetchone()
                if overlap:
                    skipped.append({"name": source.relative_name, "repair_id": overlap[0]})
                else:
                    pending.append(entry)
            if not pending:
                return {"imported": imported, "skipped": skipped, "backup": None}
            safety_copy = backup_database(self.path)
            new_vehicles, saved_sources = {}, {}
            for entry in pending:
                source, fragment, values = entry["source"], entry["fragment"], entry["values"]
                target = entry.get("target")
                if type(target) is int:
                    row = connection.execute(
                        "SELECT id FROM vehicles WHERE id=?", (target,)
                    ).fetchone()
                    if row is None:
                        raise ValueError("Выбранный автомобиль больше не существует.")
                    vehicle_id = target
                else:
                    group = target or f"file:{source.digest}"
                    fields = {
                        key: str(values.get(key, "")).strip()
                        for key in ("make", "model", "plate", "vin", "contact_name", "phone")
                    }
                    if group in new_vehicles:
                        vehicle_id, saved_fields = new_vehicles[group]
                        if saved_fields != fields:
                            raise ValueError(
                                "Поля одной новой машины различаются между записями. "
                                "Уточните привязку."
                            )
                    else:
                        vehicle_id = self.create_vehicle(
                            connection, fields, entry.get("allow_duplicate", False)
                        )
                        new_vehicles[group] = vehicle_id, fields
                if self.possible_repair_duplicate(vehicle_id, values, connection) and not entry.get(
                    "allow_duplicate"
                ):
                    raise ValueError(
                        "У машины уже есть похожий ремонт. "
                        "Проверьте и подтвердите отдельную запись."
                    )
                visit_date, km = values.get("visit_date"), values.get("km")
                verified = bool(values.get("mileage_verified"))
                if verified:
                    rows = connection.execute(
                        "SELECT recorded_on,km FROM odometer_readings o "
                        "LEFT JOIN repairs r ON r.id=o.repair_id "
                        "WHERE o.vehicle_id=? AND o.verified=1 AND r.deleted_at IS NULL",
                        (vehicle_id,),
                    ).fetchall()
                    if not all(
                        (row["recorded_on"] >= visit_date or row["km"] <= km)
                        and (row["recorded_on"] <= visit_date or row["km"] >= km)
                        for row in rows
                    ):
                        raise ValueError(
                            "Подтверждённый пробег противоречит истории. "
                            "Проверьте или снимите отметку достоверности."
                        )
                repair_id = connection.execute(
                    """INSERT INTO repairs(vehicle_id,visit_date,description,amount_kopecks,
                    completed,origin,state_known,undated_km) VALUES (?,?,?,?,?,'txt',?,?)""",
                    (
                        vehicle_id,
                        visit_date,
                        str(values["description"]),
                        values.get("amount_kopecks"),
                        int(bool(values.get("completed"))),
                        int(values.get("completed") is not None),
                        km if visit_date is None else None,
                    ),
                ).lastrowid
                if km is not None and visit_date is not None:
                    connection.execute(
                        """INSERT INTO odometer_readings(
                        vehicle_id,repair_id,recorded_on,km,source,verified)
                        VALUES (?,?,?,?,'import',?)""",
                        (vehicle_id, repair_id, visit_date, km, int(verified)),
                    )
                if source.digest not in saved_sources:
                    row = connection.execute(
                        "SELECT id FROM import_sources WHERE sha256=?", (source.digest,)
                    ).fetchone()
                    source_id = (
                        row[0]
                        if row
                        else connection.execute(
                            """INSERT INTO import_sources(
                        sha256,original_bytes,decoded_text,encoding,
                        original_name,imported_at) VALUES (?,?,?,?,?,?)""",
                            (
                                source.digest,
                                source.raw,
                                source.text,
                                source.encoding,
                                source.relative_name,
                                datetime.now().isoformat(timespec="seconds"),
                            ),
                        ).lastrowid
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO import_paths(source_id,source_path) VALUES (?,?)",
                        (source_id, str(source.path)),
                    )
                    saved_sources[source.digest] = source_id
                source_id = saved_sources[source.digest]
                connection.execute(
                    """INSERT INTO import_fragments(source_id,start_offset,end_offset,original_text,
                    reviewed_json,repair_id,vehicle_id,semantic_hash) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        source_id,
                        fragment.start,
                        fragment.end,
                        fragment.original,
                        json.dumps(values, ensure_ascii=False),
                        repair_id,
                        vehicle_id,
                        semantic_hash(values),
                    ),
                )
                imported.append(
                    {"name": source.relative_name, "vehicle_id": vehicle_id, "repair_id": repair_id}
                )
            report = {
                "imported": imported,
                "skipped": skipped,
                "backup": str(safety_copy),
                "errors": scan_errors or [],
            }
            connection.execute(
                "INSERT INTO import_batches(created_at,report_json) VALUES (?,?)",
                (
                    datetime.now().isoformat(timespec="seconds"),
                    json.dumps(report, ensure_ascii=False),
                ),
            )
        return report

    def history(self):
        with connect(self.path) as connection:
            return [
                dict(row) | {"report": json.loads(row["report_json"])}
                for row in connection.execute("SELECT * FROM import_batches ORDER BY id DESC")
            ]

    @staticmethod
    def create_vehicle(connection, values, allow_duplicate):
        if not any(values[key] for key in ("make", "model", "plate", "vin")):
            raise ValueError(
                "Укажите автомобиль для каждой записи: выберите существующий или заполните поля."
            )
        for row in connection.execute("SELECT plate,vin FROM vehicles"):
            if (
                (
                    identity_key(values["plate"])
                    and identity_key(values["plate"]) == identity_key(row["plate"])
                )
                or (
                    identity_key(values["vin"])
                    and identity_key(values["vin"]) == identity_key(row["vin"])
                )
            ) and not allow_duplicate:
                raise ValueError(
                    "Такой номер или VIN уже есть. "
                    "Выберите существующую машину или подтвердите отдельную."
                )
        contact_id = None
        if values["contact_name"] or values["phone"]:
            contact_id = connection.execute(
                "INSERT INTO contacts(name,phone) VALUES (?,?)",
                (values["contact_name"], values["phone"]),
            ).lastrowid
        vehicle_id = connection.execute(
            "INSERT INTO vehicles(make,model,plate,vin,contact_id) VALUES (?,?,?,?,?)",
            (values["make"], values["model"], values["plate"], values["vin"], contact_id),
        ).lastrowid
        connection.execute(
            "INSERT INTO vehicle_contacts(vehicle_id,contact_id,changed_at) VALUES (?,?,?)",
            (vehicle_id, contact_id, datetime.now().isoformat(timespec="seconds")),
        )
        return vehicle_id

    def provenance(self, repair_id):
        with connect(self.path) as connection:
            row = connection.execute(
                """SELECT s.*, f.original_text AS fragment_text FROM import_sources s
                JOIN import_fragments f ON f.source_id=s.id WHERE f.repair_id=?""",
                (repair_id,),
            ).fetchone()
        if row is None:
            raise ValueError("У записи нет TXT-источника.")
        result = dict(row)
        result["original_bytes"] = bytes(result["original_bytes"])
        if sha256(result["original_bytes"]).hexdigest() != result["sha256"]:
            raise ValueError("Нарушена целостность сохранённого TXT. Используйте резервную копию.")
        return result
