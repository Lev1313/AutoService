@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Не найдено окружение .venv. См. README.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m ruff check .
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m ruff format --check .
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pytest
if errorlevel 1 goto failed
echo Все проверки пройдены.
pause
exit /b 0
:failed
echo Проверка обнаружила ошибку. Сохраните вывод выше.
pause
exit /b 1
