@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Не найдено окружение .venv. Откройте README.md для настройки.
    pause
    exit /b 1
)
set PYTHONUTF8=1
".venv\Scripts\python.exe" main.py
if errorlevel 1 (
    echo Программа завершилась с ошибкой. Сохраните сообщение выше для диагностики.
    pause
    exit /b 1
)
