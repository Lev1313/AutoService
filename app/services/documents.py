"""Неизменяемые снимки квитанций, отдельные черновики детализации и реквизиты."""

import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from hashlib import sha256
from uuid import uuid4

from app.database.connection import connect
from app.services.records import parse_amount

SETTINGS = ("name", "address", "phone", "requisites")


class TotalMismatch(ValueError):
    pass


def quantity_milli(text: str) -> int:
    try:
        value = Decimal(text.strip().replace(",", "."))
        if not value.is_finite() or not 0 < value <= 100000:
            raise ValueError
        if value != value.quantize(Decimal("0.001")):
            raise ValueError
    except (InvalidOperation, ValueError):
        raise ValueError(
            "Количество: положительное число, не более трёх знаков после запятой."
        ) from None
    return int(value * 1000)


def normalize_items(rows: list[dict]) -> list[dict]:
    result = []
    for index, row in enumerate(rows, 1):
        title = str(row.get("title", "")).strip()
        if not title:
            raise ValueError(f"Строка {index}: укажите наименование.")
        kind = row.get("kind", "work")
        if kind not in ("work", "part"):
            raise ValueError("Выберите работу или запчасть.")
        quantity = quantity_milli(str(row.get("quantity", "")))
        price = parse_amount(str(row.get("price", "")))
        if price is None:
            raise ValueError(f"Строка {index}: укажите цену; 0 означает бесплатно.")
        total = int((Decimal(price) * quantity / 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        result.append(
            {
                "kind": kind,
                "title": title,
                "quantity_milli": quantity,
                "unit_price_kopecks": price,
                "total_kopecks": total,
            }
        )
    if not result:
        raise ValueError("Добавьте хотя бы одну позицию для подробной квитанции.")
    if sum(row["total_kopecks"] for row in result) > 99_999_999_999:
        raise ValueError("Итог позиций превышает допустимую сумму.")
    return result


class Documents:
    def __init__(self, path):
        self.path = path

    def settings(self):
        with connect(self.path) as connection:
            stored = dict(connection.execute("SELECT key, value FROM document_settings"))
        return {key: stored.get(key, "") for key in SETTINGS}

    def save_settings(self, values):
        with connect(self.path) as connection:
            for key in SETTINGS:
                connection.execute(
                    "INSERT INTO document_settings(key,value) VALUES (?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, str(values.get(key, "")).strip()),
                )

    def draft(self, repair_id):
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT payload FROM document_drafts WHERE repair_id=?",
                (repair_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save_draft(self, repair_id, values):
        # Черновик допускает незаполненные поля; документ проверяется отдельно.
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT deleted_at FROM repairs WHERE id=?", (repair_id,)
            ).fetchone()
            if row is None or row["deleted_at"]:
                raise ValueError("Ремонт не найден или удалён. Сначала восстановите его.")
            connection.execute(
                "INSERT INTO document_drafts(repair_id,payload) VALUES (?,?) "
                "ON CONFLICT(repair_id) DO UPDATE SET payload=excluded.payload",
                (repair_id, json.dumps(values, ensure_ascii=False)),
            )

    def create(
        self,
        repair_id,
        kind="simple",
        items=None,
        description=None,
        allow_difference=False,
        preview=False,
        *,
        consume_draft=False,
    ):
        if kind not in ("simple", "detailed"):
            raise ValueError("Выберите формат квитанции.")
        normalized = normalize_items(items or []) if kind == "detailed" else []
        with connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            repair = connection.execute(
                """SELECT r.*, COALESCE(o.km,r.undated_km) AS km, o.verified FROM repairs r
                LEFT JOIN odometer_readings o ON o.repair_id=r.id
                WHERE r.id=?""",
                (repair_id,),
            ).fetchone()
            if repair is not None and repair["deleted_at"]:
                raise ValueError(
                    "Сначала восстановите удалённый ремонт. Выданные PDF доступны в архиве."
                )
            if repair is None:
                raise ValueError("Сначала сохраните ремонт.")
            vehicle = connection.execute(
                """SELECT v.*, c.name AS contact_name, c.phone FROM vehicles v
                LEFT JOIN contacts c ON c.id=v.contact_id WHERE v.id=?""",
                (repair["vehicle_id"],),
            ).fetchone()
            total = (
                repair["amount_kopecks"]
                if kind == "simple"
                else sum(row["total_kopecks"] for row in normalized)
            )
            mismatch = (
                kind == "detailed"
                and repair["amount_kopecks"] is not None
                and total != repair["amount_kopecks"]
            )
            if mismatch and not allow_difference:
                raise TotalMismatch(
                    "Итог позиций отличается от стоимости в записи ремонта. "
                    "Использовать в документе сумму позиций? Запись ремонта останется прежней."
                )
            stored_settings = dict(connection.execute("SELECT key,value FROM document_settings"))
            payload = {
                "schema": 1,
                "repair": dict(repair),
                "vehicle": dict(vehicle),
                "service": {key: stored_settings.get(key, "") for key in SETTINGS},
                "description": repair["description"] if description is None else str(description),
                "items": normalized,
                "total_kopecks": total,
                "total_difference_confirmed": bool(mismatch and allow_difference),
            }
            created = datetime.now().isoformat(timespec="seconds")
            if preview:
                return {
                    "id": None,
                    "number": "ПРЕДПРОСМОТР",
                    "created_at": created,
                    "kind": kind,
                    "payload": payload,
                }
            document_id = connection.execute(
                "INSERT INTO documents(number,repair_id,created_at,kind,payload) "
                "VALUES (?,?,?,?,?)",
                (uuid4().hex, repair_id, created, kind, json.dumps(payload, ensure_ascii=False)),
            ).lastrowid
            connection.execute(
                "UPDATE documents SET number=? WHERE id=?",
                (f"КВ-{document_id:06d}", document_id),
            )
            from app.services.receipt_pdf import pdf_bytes

            snapshot = {
                "id": document_id,
                "number": f"КВ-{document_id:06d}",
                "created_at": created,
                "kind": kind,
                "payload": payload,
            }
            content = pdf_bytes(snapshot)
            connection.execute(
                "INSERT INTO document_files(document_id,content,sha256) VALUES (?,?,?)",
                (document_id, content, sha256(content).hexdigest()),
            )
            if consume_draft:
                connection.execute("DELETE FROM document_drafts WHERE repair_id=?", (repair_id,))
        return self.get(document_id)

    def pdf_content(self, document_id):
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT content, sha256 FROM document_files WHERE document_id=?",
                (document_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Сохранённый PDF не найден.")
        content = bytes(row["content"])
        if sha256(content).hexdigest() != row["sha256"]:
            raise ValueError("Нарушена целостность сохранённого PDF. Используйте резервную копию.")
        return content

    def history(self, repair_id):
        with connect(self.path) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT id, number, created_at, kind FROM documents "
                    "WHERE repair_id=? ORDER BY id DESC",
                    (repair_id,),
                )
            ]

    def get(self, document_id):
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id=?", (document_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Документ не найден.")
        value = dict(row)
        value["payload"] = json.loads(value["payload"])
        return value
