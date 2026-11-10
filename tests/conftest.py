"""Тесты Qt без видимого окна и без доступа к рабочей базе."""

import os

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(scope="session")
def application():
    from app.bootstrap import create_application

    yield create_application([])
