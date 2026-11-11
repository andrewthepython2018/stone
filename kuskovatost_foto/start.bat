@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Программа ещё не установлена. Сначала запустите install.bat
    pause
    exit /b 1
)

rem Окно программы. Можно перетащить фотографии на start.bat — они откроются сразу.
start "" ".venv\Scripts\pythonw.exe" -m foto %*
