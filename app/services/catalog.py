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
    "мерседес": "mercedesbenz",
    "мерседесбенц": "mercedesbenz",
    "mercedes": "mercedesbenz",
}


def make_key(value):
    key = name_key(value)
    return ALIASES.get(key, key)


@lru_cache(maxsize=1)
def model_overrides():
    """Точные соответствия; составная строка предлагает несколько вариантов."""
    path = Path(__file__).resolve().parents[1] / "assets" / "car_models.overrides.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError("Неподдерживаемая версия исправлений каталога.")
    result = {}
    for rule in data["rules"]:
        targets = tuple(rule["targets"])
        for alias in (rule["source"], *rule["aliases"]):
            key = (make_key(rule["make"]), name_key(alias))
            previous = result.get(key)
            if previous is not None and previous != targets:
                raise ValueError(f"Неоднозначное соответствие каталога: {alias}")
            result[key] = targets
    return result


def model_names(make, value):
    return model_overrides().get((make_key(make), name_key(value)), (value,))


def model_search_text(make, model):
    """Поиск по прежним подписям без показа дублей в выпадающем списке."""
    key = make_key(make)
    aliases = [name_key(model)]
    for (brand, alias), targets in model_overrides().items():
        if brand == key and model in targets:
            aliases.append(alias)
    return "|".join(aliases)


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
        return unique_names(name for value in values for name in model_names(make, value))
