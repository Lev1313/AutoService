"""Русская орфография и термины мастерской для автономной работы."""

import re
from concurrent.futures import ThreadPoolExecutor
from difflib import get_close_matches
from functools import cache, lru_cache
from itertools import islice
from pathlib import Path
from tempfile import NamedTemporaryFile

from spylls import hunspell

# Берём токен целиком, чтобы не проверять части артикулов, VIN и госномеров.
TOKENS = re.compile(r"\w+(?:[-/‐‑]\w+)*")
RUSSIAN = re.compile(r"[а-яёА-ЯЁ]+")
TERMS = frozenset(
    [
        "автосервис",
        "автосервиса",
        "автосервисе",
        "фольцмастер",
        "фольц",
        "шрус",
        "шруса",
        "шрусу",
        "шрусом",
        "шрусы",
        "шрусов",
        "шрусах",
        "сайлентблок",
        "сайлентблока",
        "сайлентблоку",
        "сайлентблоком",
        "сайлентблоки",
        "сайлентблоков",
        "сайлентблоками",
        "сайлентблоках",
        "подрамник",
        "подрамника",
        "подрамником",
        "дроссель",
        "дросселя",
        "дроссельный",
        "ступичный",
        "ступичного",
        "ступичные",
        "суппорт",
        "суппорта",
        "суппорту",
        "суппортом",
        "суппорты",
        "суппортов",
        "антифриз",
        "антифриза",
        "антифризом",
        "пыльник",
        "пыльника",
        "пыльники",
        "пыльников",
        "лямбда",
        "зонд",
        "интеркулер",
        "интеркулера",
        "турбина",
        "турбины",
        "двс",
        "грм",
        "гур",
        "эур",
        "кпп",
        "мкпп",
        "акпп",
        "ркпп",
        "эбу",
        "абс",
        "птф",
        "дмрв",
        "дпкв",
        "гбц",
    ]
)


def words(text):
    for token in TOKENS.finditer(text):
        # В составном русском слове проверяем каждую часть. Коды пропускаем целиком.
        if re.fullmatch(r"[а-яёА-ЯЁ]+(?:[-‐‑][а-яёА-ЯЁ]+)*", token[0]):
            for word in RUSSIAN.finditer(token[0]):
                if 2 <= len(word[0]) <= 48:
                    yield token.start() + word.start(), word[0]


def load_dictionary():
    path = Path(hunspell.__file__).parent / "data/ru/ru"
    dictionary = hunspell.Dictionary.from_files(str(path))
    dictionary.aff.TRY = "оеаинтсрвлкмдпуяыьгзбчйхжшюцщэфъё"
    dictionary.aff.MAXNGRAMSUGS = 0
    return dictionary


@cache
def dictionary_job():
    pool = ThreadPoolExecutor(max_workers=1)
    job = pool.submit(load_dictionary)
    pool.shutdown(wait=False)
    return job


@lru_cache(maxsize=8192)
def known(word):
    return word.lower() in TERMS or dictionary_job().result().lookup(word)


@lru_cache(maxsize=256)
def suggestions(word):
    base = list(islice(dictionary_job().result().suggest(word), 5))
    terms = get_close_matches(word.lower(), TERMS, n=3, cutoff=0.82)
    if word.isupper():
        terms = [term.upper() for term in terms]
    elif word.istitle():
        terms = [term.capitalize() for term in terms]
    return list(dict.fromkeys(base + terms))[:5]


def read_words(path):
    try:
        return set(path.read_text(encoding="utf-8").splitlines())
    except FileNotFoundError:
        return set()


def save_word(path, word):
    entries = read_words(path) | {word.lower()}
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete_on_close=False
    ) as f:
        f.write("\n".join(sorted(entries)) + "\n")
        f.close()
        Path(f.name).replace(path)
    return entries
