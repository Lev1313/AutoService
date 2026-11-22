import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app.bootstrap import create_window, open_startup_window
from app.database.connection import backup_database, initialize_database
from app.services.records import Records


def test_last_backup_date_survives_reopen(application, tmp_path, monkeypatch):
    database = tmp_path / "data" / "autoservice.sqlite3"
    backup = tmp_path / "external" / "copy.sqlite3"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(backup), ""))
    notices = []
    monkeypatch.setattr(QMessageBox, "information", lambda *_: notices.append(True))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: notices.append(False))

    window = create_window(database)
    assert "неизвестна" in window.backup_status.text()
    window.create_backup()
    shown_date = window.backup_status.text()
    assert "создана" in shown_date
    assert notices == [True]
    assert backup.is_file()
    assert database.with_suffix(".ini").is_file()
    window.close()

    reopened = create_window(database)
    try:
        assert reopened.backup_status.text() == shown_date
    finally:
        reopened.close()


def test_failed_backup_does_not_set_date(application, tmp_path, monkeypatch):
    database = tmp_path / "autoservice.sqlite3"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(database), ""))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[1]))
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)

    window = create_window(database)
    try:
        previous_status = window.backup_status.text()
        window.create_backup()
        assert warnings == ["Не удалось создать копию"]
        assert window.backup_status.text() == previous_status
        assert not database.with_suffix(".ini").exists()
    finally:
        window.close()


@pytest.mark.parametrize("original", [b"", b"damaged SQLite file"])
def test_startup_can_cancel_or_recover(application, tmp_path, monkeypatch, original):
    working = tmp_path / "autoservice.sqlite3"
    working.write_bytes(original)
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
    assert open_startup_window(working) is None
    assert working.read_bytes() == original

    source = tmp_path / "source.sqlite3"
    initialize_database(source)
    records = Records(source)
    car = records.save_vehicle({"plate": "ТЕСТ"})
    records.save_repair(car, {"visit_date": "2026-11-22", "description": "Тест восстановления"})
    backup = backup_database(source, tmp_path / "verified.sqlite3")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(backup), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)

    def choose_backup():
        dialog = QApplication.activeModalWidget()
        next(button for button in dialog.buttons() if "Выбрать копию" in button.text()).click()

    QTimer.singleShot(0, choose_backup)
    window = open_startup_window(working)
    try:
        assert window.records.repairs(car)[0]["description"] == "Тест восстановления"
        (archive,) = (tmp_path / "backups").glob("damaged-*/autoservice.sqlite3")
        assert archive.read_bytes() == original
    finally:
        window.close()
