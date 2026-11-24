"""Неизменяемые снимки заказ-нарядов, отдельные черновики детализации и реквизиты."""

import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from hashlib import sha256
from uuid import uuid4

from app.database.connection import connect
from app.services.records import merge_repair_fields, parse_amount

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


def quantity_text(milli: int) -> str:
    whole, fraction = divmod(milli, 1000)
    return f"{whole},{fraction:03d}".rstrip("0") if fraction else str(whole)


def item_price_total(items):
    amounts = [item["total_kopecks"] for item in items if item["total_kopecks"] is not None]
    return sum(amounts) if amounts else None


def item_total_label(items):
    return (
        "Итого"
        if all(item["unit_price_kopecks"] is not None for item in items)
        else "Итого по указанным ценам"
    )


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
        raw_price = row.get("price")
        price = parse_amount(str(raw_price)) if raw_price is not None else None
        total = None
        if price is not None:
            amount = Decimal(price) * quantity / 1000
            total = int(amount.quantize(Decimal(1), rounding=ROUND_HALF_UP))
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
        raise ValueError("Добавьте хотя бы одну запчасть или работу.")
    if (item_price_total(result) or 0) > 99_999_999_999:
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

    def save_draft(self, repair_id, values, *, expected_notes=None):
        # Черновик допускает незаполненные поля; документ проверяется отдельно.
        with connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM repairs WHERE id=?", (repair_id,)).fetchone()
            if row is None or row["deleted_at"]:
                raise ValueError("Ремонт не найден или удалён. Сначала восстановите его.")
            notes = {
                k: values[k]
                for k in ("description", "recommendations")
                if values.get(k) is not None
            }
            merged = merge_repair_fields(connection, row, notes, expected_notes)
            values = values | {k: merged[k] for k in notes}
            connection.execute(
                "INSERT INTO document_drafts(repair_id,payload) VALUES (?,?) "
                "ON CONFLICT(repair_id) DO UPDATE SET payload=excluded.payload",
                (repair_id, json.dumps(values, ensure_ascii=False)),
            )
        return merged

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
        layout="receipt",
        recommendations=None,
        expected_notes=None,
    ):
        if kind not in ("simple", "detailed"):
            raise ValueError("Выберите формат заказ-наряда.")
        if layout not in ("receipt", "service_sheet"):
            raise ValueError("Выберите шаблон документа.")
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
            notes = {"description": description, "recommendations": recommendations}
            merged = merge_repair_fields(
                connection,
                repair,
                {k: v for k, v in notes.items() if v is not None},
                expected_notes,
                save=consume_draft and not preview,
            )
            vehicle = connection.execute(
                """SELECT v.*, c.name AS contact_name, c.phone FROM vehicles v
                LEFT JOIN contacts c ON c.id=v.contact_id WHERE v.id=?""",
                (repair["vehicle_id"],),
            ).fetchone()
            total = repair["amount_kopecks"] if kind == "simple" else item_price_total(normalized)
            mismatch = (
                kind == "detailed"
                and all(item["unit_price_kopecks"] is not None for item in normalized)
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
                "layout": layout,
                "recommendations": merged["recommendations"],
                "repair": dict(repair) | merged,
                "vehicle": dict(vehicle),
                "service": {key: stored_settings.get(key, "") for key in SETTINGS},
                "description": merged["description"],
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
            prefix = "СЛ" if layout == "service_sheet" else "КВ"
            series = connection.execute(
                "SELECT value FROM app_metadata WHERE key='document_series'"
            ).fetchone()
            if series:
                prefix = f"{prefix}-{series[0]}"
            number = f"{prefix}-{document_id:06d}"
            connection.execute(
                "UPDATE documents SET number=? WHERE id=?",
                (number, document_id),
            )
            from app.services.receipt_pdf import pdf_bytes

            snapshot = {
                "id": document_id,
                "number": number,
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
