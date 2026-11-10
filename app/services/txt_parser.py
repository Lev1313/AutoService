"""Осторожные подсказки для TXT; исходные байты и диапазоны не изменяются."""

import re
from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
from pathlib import Path

from app.services.records import parse_amount

MAX_FILE_BYTES = 10 * 1024 * 1024
DATE_HEADER = re.compile(
    r"^[ \t]*(?:(?:дата|визит|ремонт)[ \t]*:?[ \t]*)?"
    r"(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{4})(?=\s|$|[:,;])",
    re.IGNORECASE | re.MULTILINE,
)
PLATE = re.compile(
    r"(?<!\w)[АВЕКМНОРСТУХABEKMHOPCTYX]\s*\d{3}\s*[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s*\d{2,3}(?!\w)",
    re.IGNORECASE,
)
VIN = re.compile(r"(?<![A-Z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Z0-9])", re.IGNORECASE)


@dataclass
class Fragment:
    start: int
    end: int
    original: str
    values: dict
    warnings: list[str] = field(default_factory=list)
    imported: bool = False


@dataclass
class Source:
    path: Path
    relative_name: str
    raw: bytes
    digest: str
    encoding: str
    text: str
    fragments: list[Fragment]
    warnings: list[str] = field(default_factory=list)
    bound_vehicle_id: int | None = None


def decode_text(raw: bytes, encoding: str | None = None):
    if encoding and encoding != "auto":
        return raw.decode(encoding, errors="strict"), encoding, []
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16", []
    try:
        return raw.decode("utf-8-sig"), "utf-8-sig", []
    except UnicodeDecodeError:
        pass
    try:
        text = raw.decode("cp1251", errors="strict")
    except UnicodeDecodeError:
        raise ValueError(
            "Кодировка не определена. Выберите её вручную; байты не заменены."
        ) from None
    return text, "cp1251", ["Кодировка предположительно Windows-1251. Проверьте читаемость текста."]


def labeled(text, names):
    pattern = r"^[ \t]*(?:" + "|".join(re.escape(name) for name in names) + r")[ \t]*:[ \t]*(.+)$"
    return list(dict.fromkeys(match.strip() for match in re.findall(pattern, text, re.I | re.M)))


def parse_date(text):
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text).isoformat()
        day, month, year = re.split(r"[./-]", text)
        return date(int(year), int(month), int(day)).isoformat()
    except (ValueError, TypeError):
        return None


def metadata(text, filename):
    values = {}
    warnings = []
    for key, labels in (
        ("make", ("Марка",)),
        ("model", ("Модель",)),
        ("plate", ("Госномер", "Номер автомобиля")),
        ("vin", ("VIN",)),
        ("contact_name", ("Контакт", "Клиент")),
        ("phone", ("Телефон",)),
    ):
        found = labeled(text, labels)
        values[key] = found[0] if len(found) == 1 else ""
        if len(found) > 1:
            warnings.append(f"Несколько значений поля «{labels[0]}»: выберите автомобиль вручную.")
    for key, expression in (("plate", PLATE), ("vin", VIN)):
        if not values[key]:
            matches = list(dict.fromkeys(expression.findall(filename)))
            if len(matches) == 1:
                values[key] = matches[0]
                title = "Госномер" if key == "plate" else "VIN"
                warnings.append(f"Поле «{title}» предложено по имени файла; проверьте его.")
    return values, warnings


def parse_fragment(text: str, start: int, end: int, vehicle: dict, visit_date=None):
    original = text[start:end]
    warnings = []
    local, notes = metadata(original, "")
    values = vehicle | {key: value for key, value in local.items() if value}
    warnings.extend(notes)
    values |= {
        "visit_date": visit_date,
        "km": None,
        "amount_kopecks": None,
        "description": original,
        "completed": None,
        "mileage_verified": False,
    }
    if visit_date is None:
        warnings.append("Дата визита неизвестна или неоднозначна; текущая дата не подставлена.")
    elif visit_date > date.today().isoformat():
        warnings.append("Дата находится в будущем. Проверьте год.")
    mileage = labeled(original, ("Пробег", "Одометр"))
    parsed_km = []
    for value in mileage:
        match = re.fullmatch(r"([\d\s\u00a0]+)\s*(?:км)?\.?", value, re.I)
        if match:
            number = re.sub(r"\s", "", match[1])
            if number.isascii() and number.isdigit() and int(number) <= 100_000_000:
                parsed_km.append(int(number))
    if len(set(parsed_km)) == 1 and len(parsed_km) == len(mileage):
        values["km"] = parsed_km[0]
    elif mileage:
        warnings.append("Пробег не распознан однозначно; проверьте исходный текст.")
    amounts = labeled(original, ("Итого", "Стоимость", "Сумма ремонта"))
    parsed_amounts = []
    for value in amounts:
        clean = re.sub(r"\s*(?:руб\.?|₽)\s*$", "", value, flags=re.I)
        try:
            parsed_amounts.append(parse_amount(clean))
        except ValueError:
            warnings.append("Неоднозначная стоимость: оставлена неизвестной.")
    if amounts and len(set(parsed_amounts)) == 1 and len(parsed_amounts) == len(amounts):
        values["amount_kopecks"] = parsed_amounts[0]
    elif len(set(parsed_amounts)) > 1:
        warnings.append("Указано несколько разных сумм; итог не выбран автоматически.")
    if "оплач" in original.casefold():
        warnings.append("Упоминание оплаты сохранено в тексте; платёж автоматически не создаётся.")
    warnings.append("Подсказки требуют подтверждения; процедуры ТО из текста не отмечаются.")
    return Fragment(start, end, original, values, warnings)


def read_source(path: Path, root: Path | None = None, encoding: str | None = None) -> Source:
    path = Path(path).resolve()
    if path.suffix.casefold() != ".txt":
        raise ValueError("Выберите TXT-файл.")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Файл больше 10 МБ. Разделите архив для предварительного просмотра.")
    raw = path.read_bytes()
    text, detected, warnings = decode_text(raw, encoding)
    if "\x00" in text:
        raise ValueError("В тексте есть нулевые байты. Проверьте кодировку.")
    if not text.strip():
        raise ValueError("Файл пустой.")
    name = str(path.relative_to(root.resolve())) if root else path.name
    vehicle, notes = metadata(text, path.stem)
    headers = list(DATE_HEADER.finditer(text))
    fragments = []
    if not headers:
        fragments.append(parse_fragment(text, 0, len(text), vehicle))
    else:
        for index, header in enumerate(headers):
            start = 0 if index == 0 else header.start()
            end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
            fragments.append(parse_fragment(text, start, end, vehicle, parse_date(header["date"])))
    return Source(
        path, name, raw, sha256(raw).hexdigest(), detected, text, fragments, warnings + notes
    )


def split_fragment(source: Source, index: int, line_number: int):
    fragment = source.fragments[index]
    lines = fragment.original.splitlines(keepends=True)
    if not 2 <= line_number <= len(lines):
        raise ValueError("Выберите строку внутри записи, начиная со второй.")
    boundary = fragment.start + sum(len(line) for line in lines[: line_number - 1])
    first = parse_fragment(
        source.text, fragment.start, boundary, fragment.values, fragment.values["visit_date"]
    )
    second_text = source.text[boundary : fragment.end]
    header = DATE_HEADER.match(second_text)
    second = parse_fragment(
        source.text,
        boundary,
        fragment.end,
        fragment.values,
        parse_date(header["date"]) if header else None,
    )
    source.fragments[index : index + 1] = [first, second]
