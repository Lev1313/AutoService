"""Дневное расписание приездов и проверка пересечений."""

from datetime import datetime, timedelta

from app.database.connection import connect

STATUSES = {
    "scheduled": "Запланирована",
    "arrived": "Приехал",
    "cancelled": "Отменена",
    "no_show": "Не приехал",
}
ACTIVE_STATUSES = ("scheduled", "arrived")


class Appointments:
    def __init__(self, path):
        self.path = path

    def for_day(self, day):
        return self.between(day, day + timedelta(days=1))

    def between(self, first_day, end_day):
        with connect(self.path) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    """SELECT a.*, v.make, v.model, v.plate, v.vin,
                    c.name AS contact_name, c.phone FROM appointments a
                    LEFT JOIN vehicles v ON v.id=a.vehicle_id
                    LEFT JOIN contacts c ON c.id=v.contact_id
                    WHERE a.starts_at >= ? AND a.starts_at < ?
                    ORDER BY a.starts_at, a.id""",
                    (first_day.isoformat(), end_day.isoformat()),
                )
            ]

    def overlaps(self, starts_at, duration_minutes, exclude_id=None):
        start = datetime.fromisoformat(starts_at)
        end = start + timedelta(minutes=duration_minutes or 1)
        earliest = start - timedelta(days=1)
        with connect(self.path) as connection:
            return [
                row["starts_at"]
                for row in connection.execute(
                    """SELECT id, starts_at, duration_minutes FROM appointments
                    WHERE status IN ('scheduled', 'arrived') AND starts_at >= ? AND starts_at < ?
                    AND id != ? ORDER BY starts_at, id""",
                    (
                        earliest.isoformat(timespec="minutes"),
                        end.isoformat(timespec="minutes"),
                        exclude_id or 0,
                    ),
                )
                if datetime.fromisoformat(row["starts_at"])
                + timedelta(minutes=row["duration_minutes"] or 1)
                > start
            ]

    def save(self, values, appointment_id=None):
        try:
            start = datetime.fromisoformat(values["starts_at"])
            if start.tzinfo is not None or not 1900 <= start.year <= 9998:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("Укажите корректные дату и время приезда.") from None
        duration = values["duration_minutes"]
        if duration is not None and (type(duration) is not int or not 1 <= duration <= 1440):
            raise ValueError("Длительность должна быть от 1 до 1440 минут.")
        vehicle_id = values["vehicle_id"]
        contact = values["contact_text"].strip()
        status = values["status"]
        if vehicle_id is None and not contact:
            raise ValueError("Выберите автомобиль или укажите телефон либо обозначение машины.")
        if status not in STATUSES:
            raise ValueError("Выберите статус записи.")
        fields = (
            start.isoformat(timespec="minutes"),
            duration,
            vehicle_id,
            contact,
            values["reason"].strip(),
            status,
        )
        with connect(self.path) as connection:
            if appointment_id is None:
                return connection.execute(
                    """INSERT INTO appointments
                    (starts_at, duration_minutes, vehicle_id, contact_text, reason, status)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    fields,
                ).lastrowid
            updated = connection.execute(
                """UPDATE appointments SET starts_at=?, duration_minutes=?, vehicle_id=?,
                contact_text=?, reason=?, status=? WHERE id=?
                AND (repair_id IS NULL OR (vehicle_id=? AND ?='arrived'))""",
                (*fields, appointment_id, vehicle_id, status),
            )
            if updated.rowcount != 1:
                raise ValueError("Запись изменилась. Откройте её снова.")
            return appointment_id
