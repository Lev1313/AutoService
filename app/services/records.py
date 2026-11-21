"""Автомобили, контакты и ремонты; суммы — целые копейки."""

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.database.connection import connect


def search_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def identity_key(value: str) -> str:
    return search_key(value).translate(str.maketrans("авекмнорстух", "abekmhopctyx"))


def parse_amount(value: str) -> int | None:
    value = value.replace(" ", "").replace("\u00a0", "").replace(",", ".").strip()
    if not value:
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError("Введите стоимость числом, например 1500,50.") from None
    if not amount.is_finite() or amount < 0 or amount > Decimal("999999999.99"):
        raise ValueError("Стоимость должна быть от 0 до 999 999 999,99 ₽.")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError("Укажите не более двух знаков после запятой.")
    return int(amount * 100)


def amount_text(kopecks: int | None) -> str:
    return "" if kopecks is None else f"{kopecks // 100},{kopecks % 100:02d}"


def mileage_text(repair):
    if repair.get("carried_km") is not None:
        return f"{repair['carried_km']:,} ? · замер {repair['carried_on']}".replace(",", " ")
    km = repair.get("km")
    if km is None:
        return "Не указан"
    return f"{km:,}".replace(",", " ") + (" (?)" if not repair.get("verified", True) else "")


class RestoreMileageConflict(ValueError):
    pass


class Records:
    def __init__(self, path: Path):
        self.path = path

    def contacts(self):
        with connect(self.path) as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM contacts ORDER BY name")]

    def contact(self, contact_id):
        with connect(self.path) as connection:
            row = connection.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if row is None:
            raise ValueError("Клиент не найден.")
        return dict(row)

    def find_contacts(self, query=""):
        with connect(self.path) as connection:
            rows = connection.execute(
                """SELECT c.*, (SELECT COUNT(*) FROM vehicles v
                WHERE v.contact_id=c.id AND v.archived=0) AS vehicle_count
                FROM contacts c ORDER BY c.name COLLATE NOCASE, c.id"""
            ).fetchall()
        return [
            dict(row)
            for row in rows
            if all(
                any(search_key(word) in search_key(row[field] or "") for field in ("name", "phone"))
                for word in query.split()
            )
        ]

    def contact_duplicates(self, name, phone, exclude_id=None):
        name, phone = search_key(name), search_key(phone)
        return [
            row
            for row in self.contacts()
            if row["id"] != exclude_id
            and (
                (phone and search_key(row["phone"]) == phone)
                or (not phone and name and not row["phone"] and search_key(row["name"]) == name)
            )
        ]

    def save_contact(self, values, contact_id=None):
        name, phone = (str(values.get(key, "")).strip() for key in ("name", "phone"))
        if not name and not phone:
            raise ValueError("Укажите имя или телефон клиента.")
        if len(name) > 200 or len(phone) > 200:
            raise ValueError("Имя и телефон: не более 200 символов в каждом поле.")
        with connect(self.path) as connection:
            if contact_id is None:
                return connection.execute(
                    "INSERT INTO contacts(name, phone) VALUES (?,?)", (name, phone)
                ).lastrowid
            cursor = connection.execute(
                "UPDATE contacts SET name=?, phone=? WHERE id=?", (name, phone, contact_id)
            )
            if cursor.rowcount != 1:
                raise ValueError("Клиент не найден.")
        return contact_id

    def contact_vehicles(self, contact_id, include_archived=False):
        rows = self.vehicles()
        if include_archived:
            rows += self.vehicles(archived=True)
        return [row for row in rows if row["contact_id"] == contact_id]

    def assign_vehicle_contact(self, vehicle_id, contact_id, *, previous_contact_id):
        with connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            vehicle = connection.execute(
                "SELECT contact_id FROM vehicles WHERE id=? AND archived=0", (vehicle_id,)
            ).fetchone()
            if vehicle is None:
                raise ValueError("Автомобиль не найден или находится в архиве.")
            if vehicle["contact_id"] == contact_id:
                return
            if vehicle["contact_id"] != previous_contact_id:
                raise ValueError("Клиент автомобиля изменился. Проверьте обновлённый список.")
            if (
                connection.execute("SELECT 1 FROM contacts WHERE id=?", (contact_id,)).fetchone()
                is None
            ):
                raise ValueError("Клиент не найден.")
            connection.execute(
                "UPDATE vehicles SET contact_id=? WHERE id=?", (contact_id, vehicle_id)
            )
            self.record_vehicle_contact(connection, vehicle_id, contact_id)

    @staticmethod
    def record_vehicle_contact(connection, vehicle_id, contact_id):
        connection.execute(
            "INSERT INTO vehicle_contacts(vehicle_id, contact_id, changed_at) VALUES (?, ?, ?)",
            (vehicle_id, contact_id, datetime.now().isoformat(timespec="seconds")),
        )

    def vehicle_names(self):
        with connect(self.path) as connection:
            return [
                dict(row) for row in connection.execute("SELECT DISTINCT make,model FROM vehicles")
            ]

    def vehicles(
        self, query: str = "", archived: bool = False, *, current_repairs_only: bool = False
    ):
        with connect(self.path) as connection:
            rows = connection.execute(
                """SELECT v.*, c.name AS contact_name, c.phone
                FROM vehicles v LEFT JOIN contacts c ON c.id = v.contact_id
                WHERE v.archived = ? AND (
                    NOT ? OR EXISTS (
                        SELECT 1 FROM repairs r WHERE r.vehicle_id = v.id
                        AND r.deleted_at IS NULL AND r.state_known = 1 AND r.completed = 0
                    )
                )
                ORDER BY v.make COLLATE NOCASE, v.model, v.id""",
                (int(archived), int(current_repairs_only)),
            ).fetchall()
        fields = ("make", "model", "plate", "vin", "contact_name", "phone")
        return [
            dict(row)
            for row in rows
            if all(
                any(search_key(word) in search_key(row[field] or "") for field in fields)
                for word in query.split()
            )
        ]

    def vehicle(self, vehicle_id: int):
        with connect(self.path) as connection:
            row = connection.execute(
                """SELECT v.*, c.name AS contact_name, c.phone FROM vehicles v
                LEFT JOIN contacts c ON c.id = v.contact_id WHERE v.id = ?""",
                (vehicle_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Автомобиль не найден.")
        return dict(row)

    def duplicates(self, plate: str, vin: str, exclude_id: int | None = None):
        return [
            vehicle
            for vehicle in self.vehicles() + self.vehicles(archived=True)
            if vehicle["id"] != exclude_id
            and (
                (identity_key(plate) and identity_key(plate) == identity_key(vehicle["plate"]))
                or (identity_key(vin) and identity_key(vin) == identity_key(vehicle["vin"]))
            )
        ]

    def save_vehicle(self, values: dict, vehicle_id: int | None = None) -> int:
        fields = ("make", "model", "plate", "vin", "notes")
        cleaned = {field: str(values.get(field, "")).strip() for field in fields}
        cleaned["plate"] = cleaned["plate"].lower()
        cleaned["vin"] = cleaned["vin"].upper()
        if not any(cleaned[field] for field in ("make", "model", "plate", "vin")):
            raise ValueError("Укажите хотя бы марку, модель, госномер или VIN.")
        year = values.get("year")
        if year is not None and (
            type(year) is not int or not 1886 <= year <= date.today().year + 1
        ):
            raise ValueError("Проверьте год выпуска.")
        name = str(values.get("contact_name", "")).strip()
        phone = str(values.get("phone", "")).strip()
        with connect(self.path) as connection:
            previous = None
            if vehicle_id is not None:
                previous = connection.execute(
                    "SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)
                ).fetchone()
                if previous is None:
                    raise ValueError("Автомобиль не найден.")
            contact_id = values.get("contact_id")
            if contact_id is not None:
                contact = connection.execute(
                    "SELECT * FROM contacts WHERE id = ?", (contact_id,)
                ).fetchone()
                if contact is None:
                    raise ValueError("Контакт не найден.")
                if (contact["name"], contact["phone"]) != (name, phone):
                    # Контакты других машин при редактировании одной не меняются.
                    contact_id = None
            if not name and not phone:
                contact_id = None
            elif contact_id is None:
                contact_id = connection.execute(
                    "INSERT INTO contacts(name, phone) VALUES (?, ?)", (name, phone)
                ).lastrowid
            parameters = tuple(cleaned[field] for field in fields) + (year, contact_id)
            if vehicle_id is None:
                vehicle_id = connection.execute(
                    """INSERT INTO vehicles(make, model, plate, vin, notes, year, contact_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    parameters,
                ).lastrowid
            else:
                connection.execute(
                    """UPDATE vehicles SET make=?, model=?, plate=?, vin=?, notes=?,
                    year=?, contact_id=? WHERE id=?""",
                    (*parameters, vehicle_id),
                )
            if previous is None or previous["contact_id"] != contact_id:
                self.record_vehicle_contact(connection, vehicle_id, contact_id)
        return vehicle_id

    def repairs(self, vehicle_id: int, include_deleted=False):
        with connect(self.path) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    """SELECT r.*, COALESCE(o.km,r.undated_km) AS km, o.verified FROM repairs r
                LEFT JOIN odometer_readings o ON o.repair_id = r.id
                WHERE r.vehicle_id = ? AND (? OR r.deleted_at IS NULL)
                ORDER BY r.visit_date DESC, r.id DESC""",
                    (vehicle_id, include_deleted),
                )
            ]

    def latest_mileage(self, vehicle_id: int, on_date=None, exclude_repair=None):
        with connect(self.path) as connection:
            row = connection.execute(
                """SELECT o.* FROM odometer_readings o
                LEFT JOIN repairs r ON r.id=o.repair_id
                WHERE o.vehicle_id=? AND o.verified=1 AND r.deleted_at IS NULL
                AND (? IS NULL OR o.recorded_on<=?)
                AND (o.repair_id IS NULL OR o.repair_id!=?)
                ORDER BY o.recorded_on DESC, o.id DESC LIMIT 1""",
                (vehicle_id, on_date, on_date, exclude_repair or -1),
            ).fetchone()
        return dict(row) if row else None

    def mileage_consistent(self, vehicle_id: int, visit_date: str, km: int, repair_id=None):
        with connect(self.path) as connection:
            rows = connection.execute(
                """SELECT o.recorded_on, o.km FROM odometer_readings o
                LEFT JOIN repairs r ON r.id=o.repair_id
                WHERE o.vehicle_id=? AND o.verified=1 AND r.deleted_at IS NULL
                AND (o.repair_id IS NULL OR o.repair_id != ?)""",
                (vehicle_id, repair_id if repair_id is not None else -1),
            ).fetchall()
        return all(
            (row["recorded_on"] >= visit_date or row["km"] <= km)
            and (row["recorded_on"] <= visit_date or row["km"] >= km)
            for row in rows
        )

    def previous_recommendations(self, vehicle_id, visit_date):
        with connect(self.path) as connection:
            row = connection.execute(
                """SELECT recommendations FROM repairs WHERE vehicle_id=? AND deleted_at IS NULL
                AND visit_date<=? ORDER BY visit_date DESC,id DESC LIMIT 1""",
                (vehicle_id, visit_date),
            ).fetchone()
        return row[0] if row else ""

    def save_repair(
        self,
        vehicle_id: int,
        values: dict,
        repair_id: int | None = None,
        allow_unverified: bool = False,
        *,
        appointment_id: int | None = None,
    ) -> int:
        visit_date = values.get("visit_date")
        existing = None
        if repair_id is not None:
            existing = next(
                (row for row in self.repairs(vehicle_id) if row["id"] == repair_id), None
            )
        if repair_id is not None and existing is None:
            raise ValueError("Ремонт не найден или удалён. Сначала восстановите его.")
        if visit_date is None and existing and existing["origin"] == "txt":
            pass
        else:
            try:
                date.fromisoformat(visit_date)
            except (ValueError, TypeError):
                raise ValueError("Укажите корректную дату ремонта.") from None
        km = values.get("km")
        if km is not None and (type(km) is not int or not 0 <= km <= 100_000_000):
            raise ValueError("Пробег должен быть целым неотрицательным числом.")
        carried_km, carried_on = values.get("carried_km"), values.get("carried_on")
        if carried_km is not None:
            if km is not None or type(carried_km) is not int or not 0 <= carried_km <= 100_000_000:
                raise ValueError("Старый пробег хранится отдельно от нового замера.")
            try:
                if not visit_date or date.fromisoformat(carried_on) > date.fromisoformat(
                    visit_date
                ):
                    raise ValueError
            except (ValueError, TypeError):
                raise ValueError("Дата исходного замера не может быть позднее ремонта.") from None
            unchanged = existing and (carried_km, carried_on) == (
                existing["carried_km"],
                existing["carried_on"],
            )
            latest = self.latest_mileage(vehicle_id, visit_date, repair_id)
            if not unchanged and (
                latest is None or (carried_km, carried_on) != (latest["km"], latest["recorded_on"])
            ):
                raise ValueError("Исходный замер изменился. Обновите старый пробег.")
        elif carried_on is not None:
            raise ValueError("Для даты исходного замера нужен старый пробег.")
        amount = values.get("amount_kopecks")
        if amount is not None and (type(amount) is not int or not 0 <= amount <= 99_999_999_999):
            raise ValueError("Стоимость должна быть целым неотрицательным числом копеек.")
        requested_verified = (
            bool(values.get("mileage_verified", existing.get("verified", False)))
            if existing and existing["origin"] == "txt"
            else True
        )
        if (
            existing
            and existing["origin"] == "txt"
            and requested_verified
            and (km is None or visit_date is None)
        ):
            raise ValueError("Для достоверного замера нужны дата и пробег.")
        consistent = (
            km is None
            or visit_date is None
            or self.mileage_consistent(vehicle_id, visit_date, km, repair_id)
        )
        verified = requested_verified and consistent and visit_date is not None
        if not consistent and requested_verified and not allow_unverified:
            raise ValueError("Пробег противоречит истории. Проверьте его перед сохранением.")
        state_known = int(
            bool(values.get("state_known", existing["state_known"] if existing else True))
            or bool(values.get("completed"))
        )
        with connect(self.path) as connection:
            exists = connection.execute(
                "SELECT id FROM vehicles WHERE id=?", (vehicle_id,)
            ).fetchone()
            if exists is None:
                raise ValueError("Автомобиль не найден.")
            recommendations = values.get("recommendations")
            if recommendations is None:
                recommendations = (
                    existing["recommendations"]
                    if existing
                    else self.previous_recommendations(vehicle_id, visit_date)
                )
            parameters = (
                recommendations,
                visit_date,
                str(values.get("description", "")),
                amount,
                int(bool(values.get("completed", False))),
            )
            if repair_id is None:
                repair_id = connection.execute(
                    """INSERT INTO repairs(recommendations, visit_date, description, amount_kopecks,
                    completed, vehicle_id) VALUES (?, ?, ?, ?, ?, ?)""",
                    (*parameters, vehicle_id),
                ).lastrowid
            else:
                cursor = connection.execute(
                    """UPDATE repairs SET recommendations=?, visit_date=?, description=?,
                    amount_kopecks=?, completed=?, undated_km=?, state_known=?
                    WHERE id=? AND vehicle_id=?""",
                    (
                        *parameters,
                        km if visit_date is None else None,
                        state_known,
                        repair_id,
                        vehicle_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Ремонт не найден у этого автомобиля.")
            if existing:
                draft = connection.execute(
                    "SELECT payload FROM document_drafts WHERE repair_id=?", (repair_id,)
                ).fetchone()
                if draft:
                    payload = json.loads(draft[0])
                    for key in ("description", "recommendations"):
                        if key in values and values[key] != existing[key]:
                            payload[key] = values[key]
                    connection.execute(
                        "UPDATE document_drafts SET payload=? WHERE repair_id=?",
                        (json.dumps(payload, ensure_ascii=False), repair_id),
                    )
            connection.execute(
                "UPDATE repairs SET carried_km=?,carried_on=? WHERE id=?",
                (carried_km, carried_on, repair_id),
            )
            connection.execute("DELETE FROM odometer_readings WHERE repair_id=?", (repair_id,))
            if km is not None and visit_date is not None:
                connection.execute(
                    """INSERT INTO odometer_readings(vehicle_id, repair_id, recorded_on, km,
                    verified,source) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        vehicle_id,
                        repair_id,
                        visit_date,
                        km,
                        int(verified),
                        "import" if existing and existing["origin"] == "txt" else "repair",
                    ),
                )
            if appointment_id is not None:
                updated = connection.execute(
                    """UPDATE appointments SET repair_id=?, status='arrived'
                    WHERE id=? AND vehicle_id=? AND status IN ('scheduled', 'arrived')
                    AND (repair_id IS NULL OR repair_id=?)""",
                    (repair_id, appointment_id, vehicle_id, repair_id),
                )
                if updated.rowcount != 1:
                    raise ValueError("Запись изменилась. Закройте ремонт и откройте её снова.")
        return repair_id

    def repair_links(self, repair_id):
        with connect(self.path) as connection:
            return {
                key: connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE repair_id=?", (repair_id,)
                ).fetchone()[0]
                for key, table in (
                    ("documents", "documents"),
                    ("sources", "import_fragments"),
                    ("maintenance", "maintenance_events"),
                )
            }

    def set_repair_deleted(self, vehicle_id, repair_id, deleted=True, allow_unverified=False):
        with connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            reading = connection.execute(
                "SELECT * FROM odometer_readings WHERE repair_id=? AND vehicle_id=? AND verified=1",
                (repair_id, vehicle_id),
            ).fetchone()
            if (
                not deleted
                and reading
                and not self.mileage_consistent(
                    vehicle_id, reading["recorded_on"], reading["km"], repair_id
                )
            ):
                if not allow_unverified:
                    raise RestoreMileageConflict(
                        "После удаления появились противоречащие замеры. Восстановить ремонт, "
                        "сохранив его пробег как неподтверждённый? "
                        "Исходное число останется в истории."
                    )
                connection.execute(
                    "UPDATE odometer_readings SET verified=0 WHERE repair_id=?", (repair_id,)
                )
            cursor = connection.execute(
                "UPDATE repairs SET deleted_at=? WHERE id=? AND vehicle_id=?",
                (
                    datetime.now().isoformat(timespec="seconds") if deleted else None,
                    repair_id,
                    vehicle_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Ремонт не найден у этого автомобиля.")
