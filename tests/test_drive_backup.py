import json
from pathlib import Path

from app.database.connection import initialize_database
from app.services.drive_backup import DriveBackupError, DriveSettings, command, read_status, sha256
from app.services.records import Records


def test_drive_keeps_encrypted_file_offline_and_retries_until_verified(tmp_path, monkeypatch):
    data = tmp_path / "AutoService"
    working = data / "autoservice.sqlite3"
    folder = data / "drive-backup"
    folder.mkdir(parents=True)
    initialize_database(working)
    records = Records(working)
    records.save_vehicle({"plate": "тест"})
    age = folder / "age.exe"
    rclone = folder / "rclone.exe"
    config = folder / "rclone.conf"
    for file in (age, rclone, config):
        file.touch()
    (folder / "settings.json").write_text(
        json.dumps(
            {
                "age": str(age),
                "rclone": str(rclone),
                "rclone_config": str(config),
                "remote": "test:AutoService",
                "recipient": "age1test",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    uploads = []
    encrypted_paths = []
    response = "offline"

    def encrypt(self, source, destination):
        assert Records(source).vehicles()[0]["plate"] == "тест"
        destination.write_bytes(b"age-encryption.org/v1\nencrypted fixture")
        encrypted_paths.append(destination)

    def drive(self, action, *args):
        if action == "listremotes":
            return "test:\n"
        if action == "copyto":
            uploads.append(Path(args[0]))
            assert args[1] == f"test:AutoService/{uploads[-1].name}"
            assert "--immutable" in args
            if response == "offline":
                raise DriveBackupError("Нет связи.")
            return ""
        assert action == "lsjson"
        assert "--hash-type" in args
        return json.dumps(
            {
                "Size": uploads[-1].stat().st_size,
                "Hashes": {"sha256": sha256(uploads[-1]) if response == "verified" else "wrong"},
            }
        )

    monkeypatch.setattr(DriveSettings, "encrypt", encrypt)
    monkeypatch.setattr(DriveSettings, "drive_command", drive)
    status = folder / "status.json"
    assert command() == 1
    assert read_status(status)["pending"] == 1
    assert "confirmed_at" not in read_status(status)
    queued = uploads[0]
    assert queued.is_file()
    assert not list(folder.rglob("snapshot.sqlite3"))

    response = "bad hash"
    assert command() == 1
    assert queued.is_file()
    assert "SHA-256" in read_status(status)["error"]

    response = "verified"
    assert command() == 0
    state = read_status(status)
    assert state["pending"] == 0
    assert "error" not in state
    assert state["confirmed_file"] == queued.name
    assert not queued.exists()
    assert uploads == [queued, queued, queued]
    assert len(encrypted_paths) == 1

    assert command() == 0
    assert len(uploads) == 3
