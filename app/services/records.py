"""Автомобили, контакты и ремонты; суммы — целые копейки."""

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


class Records:
    def __init__(self, path: Path):
        self.path = path

    def contacts(self):
        with connect(self.path) as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM contacts ORDER BY name")]

    def vehicles(self, query: str = "", archived: bool = False):
        with connect(self.path) as connection:
            rows = connection.execute(
                """SELECT v.*, c.name AS contact_name, c.phone
                FROM vehicles v LEFT JOIN contacts c ON c.id = v.contact_id
                WHERE v.archived = ? ORDER BY v.make COLLATE NOCASE, v.model, v.id""",
                (int(archived),),
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
                connection.execute(
                    """INSERT INTO vehicle_contacts(vehicle_id, contact_id, changed_at)
                    VALUES (?, ?, ?)""",
                    (vehicle_id, contact_id, datetime.now().isoformat(timespec="seconds")),
                )
        return vehicle_id

    def repairs(self, vehicle_id: int):
        with connect(self.path) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    """SELECT r.*, COALESCE(o.km,r.undated_km) AS km, o.verified FROM repairs r
                LEFT JOIN odometer_readings o ON o.repair_id = r.id
                WHERE r.vehicle_id = ? ORDER BY r.visit_date DESC, r.id DESC""",
                    (vehicle_id,),
                )
            ]

    def latest_mileage(self, vehicle_id: int):
        with connect(self.path) as connection:
            row = connection.execute(
                """SELECT * FROM odometer_readings WHERE vehicle_id=? AND verified=1
                ORDER BY recorded_on DESC, id DESC LIMIT 1""",
                (vehicle_id,),
            ).fetchone()
        return dict(row) if row else None

    def mileage_consistent(self, vehicle_id: int, visit_date: str, km: int, repair_id=None):
        with connect(self.path) as connection:
            rows = connection.execute(
                """SELECT recorded_on, km FROM odometer_readings WHERE vehicle_id=?
                AND verified=1 AND (repair_id IS NULL OR repair_id != ?)""",
                (vehicle_id, repair_id if repair_id is not None else -1),
            ).fetchall()
        return all(
            (row["recorded_on"] >= visit_date or row["km"] <= km)
            and (row["recorded_on"] <= visit_date or row["km"] >= km)
            for row in rows
        )

    def save_repair(
        self,
        vehicle_id: int,
        values: dict,
        repair_id: int | None = None,
        allow_unverified: bool = False,
    ) -> int:
        visit_date = values.get("visit_date")
        existing = None
        if repair_id is not None:
            existing = next(
                (row for row in self.repairs(vehicle_id) if row["id"] == repair_id), None
            )
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
            parameters = (
                visit_date,
                str(values.get("description", "")),
                amount,
                int(bool(values.get("completed", False))),
            )
            if repair_id is None:
                repair_id = connection.execute(
                    """INSERT INTO repairs(visit_date, description, amount_kopecks,
                    completed, vehicle_id) VALUES (?, ?, ?, ?, ?)""",
                    (*parameters, vehicle_id),
                ).lastrowid
            else:
                cursor = connection.execute(
                    """UPDATE repairs SET visit_date=?, description=?, amount_kopecks=?,
                    completed=?, undated_km=?, state_known=? WHERE id=? AND vehicle_id=?""",
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
        return repair_id
