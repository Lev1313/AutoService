"""Явные циклы ТО и одна задача на звонок для каждого цикла."""

import calendar
import math
from datetime import date, datetime, timedelta

from app.database.connection import connect
from app.services.records import Records


def optional_integer(text: str, title: str, maximum: int = 100_000_000):
    text = text.replace(" ", "").strip()
    if not text:
        return None
    if not text.isascii() or not text.isdigit() or int(text) > maximum:
        raise ValueError(f"{title}: введите целое неотрицательное число до {maximum}.")
    return int(text)


def add_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def forecast(plan: dict, readings: list[dict], today: date) -> tuple[str | None, str]:
    """Не подменять фактический пробег прогнозным и не выдумывать отсутствующие опоры."""
    dates = []
    details = []
    baseline = date.fromisoformat(plan["baseline_date"]) if plan["baseline_date"] else None
    if baseline and plan["months"]:
        try:
            due = add_months(baseline, plan["months"])
            dates.append(due)
            details.append(f"По времени: {due.strftime('%d.%m.%Y')}")
        except ValueError:
            details.append("Срок по времени выходит за допустимый диапазон дат")
    points = [
        (date.fromisoformat(row["recorded_on"]), row["km"])
        for row in readings
        if row["verified"] and row["recorded_on"] <= today.isoformat()
    ]
    if baseline and baseline <= today and plan["baseline_km"] is not None:
        points.append((baseline, plan["baseline_km"]))
    points = sorted(set(points))
    threshold = (
        plan["baseline_km"] + plan["distance_km"]
        if plan["baseline_km"] is not None and plan["distance_km"]
        else None
    )
    if threshold is not None:
        details.append(f"Порог: {threshold:,} км".replace(",", " "))
        relevant = [point for point in points if baseline is None or point[0] >= baseline]
        if relevant:
            latest_date, latest_km = relevant[-1]
            details.append(
                f"Замер: {latest_km:,} км от {latest_date.strftime('%d.%m.%Y')}".replace(",", " ")
            )
            consistent = all(
                current[1] >= previous[1]
                for previous, current in zip(relevant, relevant[1:], strict=False)
            )
            if consistent and latest_km >= threshold:
                dates.append(latest_date)
                details.append("Порог достигнут по фактическому замеру")
            elif consistent:
                rate = None
                origin = ""
                if plan["annual_km"]:
                    rate = plan["annual_km"] / 365
                    origin = "по годовому пробегу, указанному владельцем"
                elif len(relevant) >= 2 and relevant[0][0] < latest_date:
                    elapsed = (latest_date - relevant[0][0]).days
                    rate = (latest_km - relevant[0][1]) / elapsed
                    origin = "по сохранённым замерам"
                if rate and rate > 0:
                    days = math.ceil((threshold - latest_km) / rate)
                    if days <= (date.max - latest_date).days:
                        prediction = latest_date + timedelta(days=days)
                        dates.append(prediction)
                        details.append(
                            f"Прогноз по пробегу: {prediction.strftime('%d.%m.%Y')} ({origin})"
                        )
                else:
                    details.append("Не хватает данных для прогноза по пробегу")
            else:
                details.append("Замеры противоречат друг другу; прогноз пробега отключён")
        else:
            details.append("Нет достоверного замера для прогноза пробега")
    elif plan["distance_km"]:
        details.append("Неизвестен пробег при последнем выполнении ТО")
    if not baseline and plan["months"]:
        details.append("Неизвестна дата последнего выполнения ТО")
    if not dates:
        return None, "\n".join(details) or "Уточните дату или пробег последнего ТО"
    due = min(dates)
    days = min(plan["lead_days"], (due - date.min).days)
    return (due - timedelta(days=days)).isoformat(), "\n".join(details)


class Maintenance:
    def __init__(self, path):
        self.path = path

    def plans(self, vehicle_id: int | None = None):
        with connect(self.path) as connection:
            query = "SELECT * FROM maintenance_plans"
            parameters = ()
            if vehicle_id is not None:
                query += " WHERE vehicle_id=?"
                parameters = (vehicle_id,)
            return [
                dict(row) for row in connection.execute(query + " ORDER BY title, id", parameters)
            ]

    def invites_enabled(self, vehicle_id):
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT allow_calls FROM vehicle_call_preferences WHERE vehicle_id=?",
                (vehicle_id,),
            ).fetchone()
        return row is None or bool(row[0])

    def save_plan(self, vehicle_id: int, values: dict, plan_id: int | None = None):
        title = str(values.get("title", "")).strip()
        if not title:
            raise ValueError("Укажите название ТО.")
        integers = {}
        for field, maximum in (
            ("months", 1200),
            ("distance_km", 100_000_000),
            ("lead_days", 3650),
            ("annual_km", 10_000_000),
            ("baseline_km", 100_000_000),
        ):
            value = values.get(field)
            if value is not None and (type(value) is not int or not 0 <= value <= maximum):
                raise ValueError("Проверьте интервалы и пробег: нужны целые неотрицательные числа.")
            integers[field] = value
        if not integers["months"] and not integers["distance_km"]:
            raise ValueError("Укажите интервал в месяцах или километрах.")
        baseline = values.get("baseline_date") or None
        if baseline:
            try:
                if date.fromisoformat(baseline) > date.today():
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("Дата выполненного ТО не может быть в будущем.") from None
        base_km = integers["baseline_km"]
        if base_km is not None and not baseline:
            raise ValueError("Для пробега последнего ТО укажите дату замера.")
        if base_km is not None and not Records(self.path).mileage_consistent(
            vehicle_id,
            baseline,
            base_km,
        ):
            raise ValueError("Пробег последнего ТО противоречит истории. Проверьте данные.")
        with connect(self.path) as connection:
            if (
                connection.execute("SELECT id FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
                is None
            ):
                raise ValueError("Автомобиль не найден.")
            if plan_id is not None:
                previous = connection.execute(
                    "SELECT * FROM maintenance_plans WHERE id=? AND vehicle_id=?",
                    (plan_id, vehicle_id),
                ).fetchone()
                if previous is None:
                    raise ValueError("Правило ТО не найдено.")
                if (previous["baseline_date"], previous["baseline_km"]) != (baseline, base_km):
                    raise ValueError("Для нового выполнения ТО используйте «ТО выполнено».")
            parameters = (
                title,
                integers["months"] or None,
                integers["distance_km"] or None,
                integers["lead_days"] or 0,
                integers["annual_km"] or None,
                baseline,
                base_km,
                int(bool(values.get("enabled", self.invites_enabled(vehicle_id)))),
            )
            if plan_id is None:
                plan_id = connection.execute(
                    """INSERT INTO maintenance_plans(title, months, distance_km, lead_days,
                    annual_km, baseline_date, baseline_km, enabled, vehicle_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (*parameters, vehicle_id),
                ).lastrowid
            else:
                connection.execute(
                    """UPDATE maintenance_plans SET title=?, months=?, distance_km=?, lead_days=?,
                    annual_km=?, baseline_date=?, baseline_km=?, enabled=? WHERE id=?""",
                    (*parameters, plan_id),
                )
        self.sync_tasks()
        return plan_id

    def record_completion(self, plan_id: int, performed_on: str, km=None, repair_id=None, note=""):
        try:
            if date.fromisoformat(performed_on) > date.today():
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("Укажите дату фактически выполненного ТО, не в будущем.") from None
        if km is not None and (type(km) is not int or not 0 <= km <= 100_000_000):
            raise ValueError("Проверьте пробег.")
        with connect(self.path) as connection:
            plan = connection.execute(
                "SELECT * FROM maintenance_plans WHERE id=?", (plan_id,)
            ).fetchone()
            if plan is None:
                raise ValueError("Правило ТО не найдено.")
            if plan["baseline_date"] and performed_on < plan["baseline_date"]:
                raise ValueError("Новая отметка ТО не может предшествовать началу текущего цикла.")
            if km is not None and not Records(self.path).mileage_consistent(
                plan["vehicle_id"],
                performed_on,
                km,
            ):
                raise ValueError("Пробег противоречит истории. Проверьте или оставьте поле пустым.")
            if repair_id is not None:
                repair = connection.execute(
                    "SELECT * FROM repairs WHERE id=? AND vehicle_id=? "
                    "AND completed=1 AND deleted_at IS NULL",
                    (repair_id, plan["vehicle_id"]),
                ).fetchone()
                if repair is None:
                    raise ValueError("Выберите завершённый ремонт этого автомобиля.")
                if connection.execute(
                    "SELECT id FROM maintenance_events WHERE plan_id=? AND repair_id=?",
                    (plan_id, repair_id),
                ).fetchone():
                    raise ValueError("Это ТО по выбранному ремонту уже отмечено.")
            elif connection.execute(
                "SELECT id FROM maintenance_events WHERE plan_id=? AND performed_on=? AND km IS ?",
                (plan_id, performed_on, km),
            ).fetchone():
                raise ValueError("Такое выполнение ТО уже отмечено.")
            event_id = connection.execute(
                """INSERT INTO maintenance_events(plan_id, performed_on, km, repair_id, note)
                VALUES (?, ?, ?, ?, ?)""",
                (plan_id, performed_on, km, repair_id, note),
            ).lastrowid
            if km is not None:
                connection.execute(
                    """INSERT INTO odometer_readings(vehicle_id, maintenance_event_id,
                    recorded_on, km, source) VALUES (?, ?, ?, ?, 'maintenance')""",
                    (plan["vehicle_id"], event_id, performed_on, km),
                )
            connection.execute(
                "UPDATE call_tasks SET status='completed' WHERE plan_id=? AND cycle_no=?",
                (plan_id, plan["cycle_no"]),
            )
            connection.execute(
                """UPDATE maintenance_plans SET baseline_date=?, baseline_km=?,
                cycle_no=cycle_no+1 WHERE id=?""",
                (performed_on, km, plan_id),
            )
        self.sync_tasks()

    def sync_tasks(self, today: date | None = None):
        today = today or date.today()
        with connect(self.path) as connection:
            plans = [dict(row) for row in connection.execute("SELECT * FROM maintenance_plans")]
            for plan in plans:
                readings = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT o.* FROM odometer_readings o "
                        "LEFT JOIN repairs r ON r.id=o.repair_id "
                        "WHERE o.vehicle_id=? AND r.deleted_at IS NULL "
                        "ORDER BY o.recorded_on, o.id",
                        (plan["vehicle_id"],),
                    )
                ]
                calculated, details = forecast(plan, readings, today)
                connection.execute(
                    """INSERT INTO call_tasks(plan_id, cycle_no, calculated_date, details)
                    VALUES (?, ?, ?, ?) ON CONFLICT(plan_id, cycle_no) DO UPDATE SET
                    calculated_date=excluded.calculated_date, details=excluded.details""",
                    (plan["id"], plan["cycle_no"], calculated, details),
                )

    def tasks(self, today: date | None = None, include_handled=False):
        today = today or date.today()
        self.sync_tasks(today)
        with connect(self.path) as connection:
            rows = connection.execute(
                """SELECT t.*, p.vehicle_id, p.title, p.enabled,
                COALESCE(t.manual_date, t.calculated_date) AS due_date,
                v.make, v.model, v.plate, c.name AS contact_name, c.phone
                FROM call_tasks t JOIN maintenance_plans p ON p.id=t.plan_id
                JOIN vehicles v ON v.id=p.vehicle_id LEFT JOIN contacts c ON c.id=v.contact_id
                WHERE t.cycle_no=p.cycle_no AND v.archived=0
                ORDER BY due_date IS NULL, due_date, t.id""",
            ).fetchall()
        return [
            dict(row)
            for row in rows
            if include_handled or (row["status"] == "open" and row["enabled"])
        ]

    def record_call(self, task_id: int, outcome: str, next_date=None, note=""):
        if outcome not in ("contacted", "no_answer", "postponed", "declined", "reopen"):
            raise ValueError("Неизвестный результат звонка.")
        if outcome in ("no_answer", "postponed") and not next_date:
            raise ValueError("Выберите дату следующего звонка.")
        if next_date:
            try:
                date.fromisoformat(next_date)
            except (TypeError, ValueError):
                raise ValueError("Проверьте дату следующего звонка.") from None
        with connect(self.path) as connection:
            task = connection.execute(
                """SELECT t.*, p.vehicle_id, p.cycle_no AS current_cycle FROM call_tasks t
                JOIN maintenance_plans p ON p.id=t.plan_id WHERE t.id=?""",
                (task_id,),
            ).fetchone()
            if task is None or task["cycle_no"] != task["current_cycle"]:
                raise ValueError("Этот цикл ТО уже завершён. Обновите список.")
            status = outcome if outcome in ("contacted", "declined") else "open"
            connection.execute(
                "INSERT INTO call_attempts(task_id, attempted_at, outcome, note, next_date) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, datetime.now().isoformat(timespec="seconds"), outcome, note, next_date),
            )
            if outcome == "declined":
                connection.execute(
                    "INSERT INTO vehicle_call_preferences(vehicle_id, allow_calls) VALUES (?, 0) "
                    "ON CONFLICT(vehicle_id) DO UPDATE SET allow_calls=0",
                    (task["vehicle_id"],),
                )
                connection.execute(
                    "UPDATE maintenance_plans SET enabled=0 WHERE vehicle_id=?",
                    (task["vehicle_id"],),
                )
            elif outcome == "reopen":
                connection.execute(
                    "INSERT INTO vehicle_call_preferences(vehicle_id, allow_calls) VALUES (?, 1) "
                    "ON CONFLICT(vehicle_id) DO UPDATE SET allow_calls=1",
                    (task["vehicle_id"],),
                )
                connection.execute(
                    "UPDATE maintenance_plans SET enabled=1 WHERE id=?",
                    (task["plan_id"],),
                )
            if outcome in ("contacted", "declined"):
                connection.execute("UPDATE call_tasks SET status=? WHERE id=?", (status, task_id))
            else:
                connection.execute(
                    "UPDATE call_tasks SET status=?, manual_date=? WHERE id=?",
                    (status, next_date, task_id),
                )

    def attempts(self, task_id: int):
        with connect(self.path) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM call_attempts WHERE task_id=? ORDER BY id DESC",
                    (task_id,),
                )
            ]
