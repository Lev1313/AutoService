"""Зашифрованная очередь снимков SQLite с отправкой через Google Drive API."""

import json
import re
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from hashlib import file_digest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from PySide6.QtCore import QLockFile

from app.database.connection import backup_database, validate_backup_content
from app.services.paths import database_path


class DriveBackupError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    with path.open("rb") as file:
        return file_digest(file, "sha256").hexdigest()


def read_status(path: Path) -> dict:
    if not path.is_file():
        return {}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("Повреждён файл состояния резервного копирования.")
    return state


def write_status(path: Path, state: dict) -> None:
    with TemporaryDirectory(prefix="status-", dir=path.parent) as directory:
        temporary = Path(directory) / path.name
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


def run_tool(executable: Path, args: list[str], failure: str) -> str:
    try:
        result = subprocess.run(
            [str(executable), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DriveBackupError(failure) from error
    if result.returncode != 0:
        # Вывод внешних инструментов может содержать данные авторизации.
        raise DriveBackupError(f"{failure} Код выхода {result.returncode}.")
    return result.stdout


def tool_path(settings: dict, field: str, filename: str) -> Path:
    path = Path(settings[field])
    if not path.is_absolute() or path.name.casefold() != filename or not path.is_file():
        raise DriveBackupError(f"Укажите полный путь к {filename} в поле {field}.")
    return path


@dataclass(frozen=True)
class DriveSettings:
    age: Path
    rclone: Path
    rclone_config: Path
    remote: str
    recipient: str

    @classmethod
    def load(cls, path: Path):
        try:
            values = json.loads(path.read_text(encoding="utf-8-sig"))
            config = Path(values["rclone_config"])
            if not config.is_absolute() or not config.is_file():
                raise DriveBackupError("Укажите полный путь к конфигурации rclone.")
            remote = values["remote"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+:[^:\\]+", remote):
                raise DriveBackupError("Укажите папку в формате имя_подключения:папка.")
            folder = remote.split(":", 1)[1].split("/")
            if any(not part.strip() or part in (".", "..") for part in folder):
                raise DriveBackupError("Укажите отдельную папку Google Диска.")
            recipient = values["recipient"]
            if not re.fullmatch(r"age1[0-9a-z]+", recipient):
                raise DriveBackupError("Для шифрования нужен открытый ключ age1…")
            return cls(
                tool_path(values, "age", "age.exe"),
                tool_path(values, "rclone", "rclone.exe"),
                config,
                remote,
                recipient,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DriveBackupError("Проверьте поля файла settings.json.") from error
        except FileNotFoundError as error:
            raise DriveBackupError("Создайте файл drive-backup/settings.json.") from error

    def encrypt(self, source: Path, destination: Path) -> None:
        run_tool(
            self.age,
            ["--encrypt", "--recipient", self.recipient, "--output", str(destination), str(source)],
            "Не удалось зашифровать копию с помощью age.",
        )
        with destination.open("rb") as file:
            if file.readline(64) != b"age-encryption.org/v1\n":
                raise DriveBackupError("Файл копии не соответствует формату age.")

    def drive_command(self, *args: str) -> str:
        return run_tool(
            self.rclone,
            [
                *args,
                "--config",
                str(self.rclone_config),
                "--ask-password=false",
                "--contimeout=10s",
                "--timeout=30s",
                "--retries=2",
            ],
            "Ошибка Google Drive. Проверьте связь, авторизацию и место на Диске.",
        )

    def check_remote(self) -> None:
        remote_name, _ = self.remote.split(":", 1)
        remotes = self.drive_command("listremotes", "--type", "drive", "--exact").splitlines()
        if f"{remote_name}:" not in remotes:
            raise DriveBackupError("Указанное подключение rclone должно иметь тип Google Drive.")

    def upload(self, path: Path) -> None:
        destination = f"{self.remote}/{path.name}"
        self.drive_command("copyto", str(path), destination, "--immutable", "--checksum")
        response = self.drive_command(
            "lsjson", destination, "--stat", "--hash", "--hash-type", "SHA-256"
        )
        record = json.loads(response)
        digest = (record.get("Hashes") or {}).get("sha256")
        if digest != sha256(path) or record.get("Size") != path.stat().st_size:
            raise DriveBackupError("Google Диск не подтвердил размер и SHA-256 копии.")


def queue_snapshot(db: Path, queue: Path, settings: DriveSettings, state: dict) -> None:
    with TemporaryDirectory(prefix="snapshot-", dir=queue.parent) as directory:
        stage = Path(directory)
        source = backup_database(db, stage / "snapshot.sqlite3")
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as connection:
            validate_backup_content(connection)
        digest = sha256(source)
        if state.get("snapshot_sha256") == digest:
            return
        stamp = datetime.now().astimezone()
        name = f"AutoService-{stamp:%Y%m%d-%H%M%S}-{uuid4().hex[:12]}.sqlite3.age"
        encrypted = stage / name
        settings.encrypt(source, encrypted)
        encrypted.rename(queue / name)
    state.update(snapshot_sha256=digest, created_at=stamp.isoformat(timespec="seconds"))


def run_backup(db: Path, folder: Path, settings: DriveSettings, state: dict) -> None:
    destination = {"remote": settings.remote, "config": str(settings.rclone_config)}
    if state.get("destination", destination) != destination:
        raise DriveBackupError("Папка или конфигурация изменились. Используйте отдельный профиль.")
    state["destination"] = destination
    queue = folder / "pending"
    queue.mkdir(exist_ok=True)
    status = folder / "status.json"
    queue_snapshot(db, queue, settings, state)
    write_status(status, state)
    pending = sorted(queue.glob("AutoService-*.sqlite3.age"))
    if pending:
        settings.check_remote()
    for path in pending:
        settings.upload(path)
        state["confirmed_file"] = path.name
        state["confirmed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        write_status(status, state)
        path.unlink()


def command() -> int:
    db = database_path().resolve()
    folder = db.parent / "drive-backup"
    folder.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(folder / "upload.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        return 2
    status = folder / "status.json"
    try:
        state = read_status(status)
        state["checked_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            run_backup(db, folder, DriveSettings.load(folder / "settings.json"), state)
        except (OSError, RuntimeError, sqlite3.Error, ValueError) as error:
            state["error"] = str(error)
            result = 1
        else:
            state.pop("error", None)
            result = 0
        queue = folder / "pending"
        state["pending"] = len(list(queue.glob("AutoService-*.sqlite3.age")))
        write_status(status, state)
        return result
    finally:
        lock.unlock()
