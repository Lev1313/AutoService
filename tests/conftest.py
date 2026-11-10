"""Тесты Qt без видимого окна и без доступа к рабочей базе."""

import gc
import os

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(scope="session")
def application():
    from PySide6.QtCore import QCoreApplication, QEvent

    from app.bootstrap import create_application

    instance = create_application([])
    yield instance
    # Удаляем оставшиеся Qt-объекты до уничтожения QApplication на Windows.
    for widget in instance.topLevelWidgets():
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    instance.processEvents()
    gc.collect()
