"""Офлайн-подсказки из открытого справочника и названий в базе владельца."""

import json
import unicodedata
from functools import lru_cache
from pathlib import Path


def name_key(value):
    return "".join(
        char for char in unicodedata.normalize("NFKD", value).casefold() if char.isalnum()
    )


def unique_names(values):
    result = {}
    for value in values:
        value = value.strip()
        if value:
            result.setdefault(name_key(value), value)
    return sorted(result.values(), key=str.casefold)


@lru_cache(maxsize=1)
def builtin_catalog():
    path = Path(__file__).resolve().parents[1] / "assets" / "car_models.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not all(
        isinstance(make, str)
        and isinstance(models, list)
        and all(isinstance(model, str) for model in models)
        for make, models in data.items()
    ):
        raise ValueError("Повреждён справочник марок и моделей.")
    return data


# Поддерживаем привычное написание при выборе моделей; введённый текст не меняется.
ALIASES = {
    "vw": "volkswagen",
    "фольксваген": "volkswagen",
    "вольксваген": "volkswagen",
    "шкода": "skoda",
    "лада": "lada",
    "ваз": "lada",
    "бмв": "bmw",
    "ауди": "audi",
    "тойота": "toyota",
    "рено": "renault",
}


def make_key(value):
    key = name_key(value)
    return ALIASES.get(key, key)


class VehicleCatalog:
    def __init__(self, records):
        self.records = records

    def makes(self):
        return unique_names(
            [
                *builtin_catalog(),
                "VW",
                "Фольксваген",
                "Шкода",
                "Лада",
                "ВАЗ",
                "БМВ",
                "Ауди",
                "Тойота",
                "Рено",
                *(row["make"] for row in self.records.vehicle_names()),
            ]
        )

    def models(self, make):
        key = make_key(make)
        if not key:
            return []
        values = [
            model
            for brand, models in builtin_catalog().items()
            if make_key(brand) == key
            for model in models
        ]
        values.extend(
            row["model"] for row in self.records.vehicle_names() if make_key(row["make"]) == key
        )
        return unique_names(values)
