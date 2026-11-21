# Среда разработки AutoService

Для повторения проверок используйте Windows x64 и Python 3.13 x64. Зависимости
зафиксированы в `requirements.txt`. PySide6 предоставляет интерфейс, SQLite входит
в Python, ReportLab формирует документы, pytest и Ruff используются для проверки.

## Новый компьютер

Синхронизируйте ветку `main` по [инструкции GitHub](GITHUB.md) и откройте корень проекта.
Создайте отдельное окружение `.venv` обычными командами PowerShell.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

Если установщик Python не добавил `py`, выполните `-m venv .venv` явным путём
к своему `python.exe` версии 3.13. Команды запуска используют интерпретатор из `.venv`,
активировать его отдельной командой не требуется.

## Запуск и проверка

- `run.bat` или F5 в VS Code запускает приложение с локальным `.venv`.
- `check.bat` выполняет Ruff, проверку форматирования и pytest.
- Для проверки окон и данных используйте временную базу. Если временный каталог
  недоступен, задайте pytest новый отдельный `--basetemp` внутри `artifacts/`.
- Для сборки установите `requirements-build.txt` поверх основных зависимостей.
  [Сборка Windows и проверки выпуска](WINDOWS-RELEASE.md).

Рабочая база находится в `%LOCALAPPDATA%\AutoService` и переносится через резервную
копию. [Копии и восстановление](OWNER-GUIDE.txt).
