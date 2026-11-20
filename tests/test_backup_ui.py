from PySide6.QtWidgets import QFileDialog, QMessageBox

from app.bootstrap import create_window


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
