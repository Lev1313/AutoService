# AutoService

Бесплатная офлайн-программа для небольшого автосервиса на Windows.
Первый этап: запускаемый каркас интерфейса и готовая среда разработки.

## Открыть программу

1. Откройте папку `C:\Projects\Autoservice` в VS Code.
2. Нажмите **F5** → «AutoService — запуск».

Также можно дважды щёлкнуть `run.bat`. Интернет для запуска не нужен.
Разделы переключаются мышью, стрелками в боковой панели или **Ctrl+1…5**.
Клавиша **Tab** перемещает фокус между элементами.

Сейчас доступны пять экранов и демонстрационная таблица автомобилей.
Записи вымышлены и не сохраняются. Ремонты, календарь и финансы пока не реализованы.

## Данные

При обычном запуске база создаётся здесь:
`%LOCALAPPDATA%\AutoService\autoservice.sqlite3`.
Полный путь показан в разделе «Настройки». База находится отдельно от исходников
и будущего exe. Сейчас она содержит только служебную информацию о приложении.
Не удаляйте её при ошибках запуска. Автоматические резервные копии пока не реализованы.
Перед будущими миграциями нужна копия базы при закрытой программе.

## Проверки одной командой

Дважды щёлкните `check.bat` или в VS Code выберите
**Терминал → Запустить задачу → AutoService: все проверки**.
Тесты используют временные базы и не открывают рабочую базу.

Отдельные команды PowerShell из папки проекта:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

## Восстановление среды на этом компьютере

Окружение уже подготовлено. Следующие команды нужны только при его пересоздании:

```powershell
& 'C:\Python\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Проверенная версия — Python 3.13.1. На другом компьютере укажите путь к установленному
Python 3.13. Python 3.14 допускается настройками проекта, но здесь не установлен
и фактически не проверен. Не переносите `.venv` между компьютерами — пересоздайте его.

В новых терминалах VS Code команда `python` использует `.venv` через PATH проекта.
Активация PowerShell и изменение системной ExecutionPolicy не требуются.
Если VS Code ранее запомнил другой интерпретатор: **Ctrl+Shift+P → Python: Select
Interpreter → Enter interpreter path**, выберите `.venv\Scripts\python.exe`.
F5 в любом случае использует путь из `launch.json`.

Если Ruff не форматирует после установки расширения, выполните
**Ctrl+Shift+P → Developer: Reload Window**.

## Обновление зависимостей

`requirements.in` — прямые зависимости и допустимые диапазоны.
`requirements.txt` — точные проверенные версии, включая зависимости библиотек.
Перед обновлением сохраните текущий файл; установку выполняйте после разрешения:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade -r requirements.in
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pytest
```

Проверьте окно через F5. Только после успешных проверок обновите фиксацию версий:

```powershell
.\.venv\Scripts\python.exe -m pip freeze | Set-Content -Encoding UTF8 requirements.txt
```

## Навигация по проекту

- `main.py`, `app/bootstrap.py` — запуск и сборка приложения.
- `app/ui/` — окно и экраны на Qt Widgets.
- `app/database/` — безопасная инициализация SQLite.
- `app/services/` — пользовательские пути.
- `tests/` — автоматические проверки.
- `docs/requirements.md` — предварительные требования.
- `docs/architecture.md` — принятые технические решения.
- `docs/environment.md` — результаты проверки окружения.
- `AGENTS.md` — инструкции для дальнейшей работы Codex.

## Git

Локальный репозиторий инициализирован; удалённый репозиторий не настроен.
Файлы ещё не закоммичены. После оценки каркаса можно сделать первый локальный коммит
«Каркас и окружение». Следующие контрольные точки: «Автомобили», «История ремонтов».

Базы, `.venv`, `data/`, `imports/`, `exports/`, `private/`, `backups/`, логи,
сборки и локальные секреты исключены через `.gitignore`.
Реальные клиентские TXT храните только вне репозитория либо в `imports/` / `private/`.
Перед коммитами проверяйте `git status` и не добавляйте персональные данные в документы.

## Источники настройки

- [Python в VS Code: параметры](https://code.visualstudio.com/docs/python/settings-reference)
- [Отладка Python через debugpy](https://code.visualstudio.com/docs/python/debugging)
- [Ruff в редакторе](https://docs.astral.sh/ruff/editors/setup/)
- [Настройки Codex IDE — OpenAI Docs](https://learn.chatgpt.com/docs/ide/settings)
- [PySide6: совместимость и пакеты](https://pypi.org/project/PySide6/)

Следующий этап — автомобили и история ремонтов после согласования полей карточки.
