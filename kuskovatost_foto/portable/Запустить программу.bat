@echo off
rem Запуск программы «Кусковатость породы по фото».
rem Запускается подписанный pythonw.exe из папки runtime — своего exe нет,
rem поэтому Smart App Control (Windows 11) программу не блокирует.
rem Фото можно перетащить на этот файл — они откроются сразу.
chcp 65001 >nul
cd /d "%~dp0"
if not exist "runtime\pythonw.exe" (
    echo Не найдены файлы программы ^(папка runtime^).
    echo Распакуйте архив целиком ^(правая кнопка мыши - "Извлечь все..."^)
    echo и запустите этот файл из распакованной папки.
    pause
    exit /b 1
)
start "" "%~dp0runtime\pythonw.exe" -E -s -X utf8 "%~dp0app\run.py" %*
