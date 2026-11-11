@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Установка: кусковатость породы по фото (демо)

echo ============================================================
echo  Установка демо-программы: кусковатость породы по фото
echo  Создаётся окружение .venv и ставятся библиотеки из requirements.txt
echo ============================================================
echo.

rem Нужен Python 3.10-3.13: под более новые версии (3.14, 3.15) у части
rem библиотек (scikit-image, PySide6) ещё нет готовых сборок, и pip пытается
rem компилировать их из исходников. Ищем подходящую версию среди установленных.
call :find_python
if not defined PY (
    echo Подходящий Python 3.10-3.13 не найден.
    where winget >nul 2>&1
    if errorlevel 1 goto :no_python
    echo Устанавливаю Python 3.12 через winget ^(рядом с уже установленными версиями^) ...
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    call :find_python
)
if not defined PY goto :no_python
!PY! -c "import sys; print(sys.version.split()[0])" > "%TEMP%\kusk_pyver.txt"
set /p PYVER=<"%TEMP%\kusk_pyver.txt"
echo Использую Python !PYVER!
echo.

rem окружение от другой версии Python (например, от 3.15 после неудачной установки) пересоздаём
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo Удаляю старое окружение .venv от неподходящей версии Python ...
        rmdir /s /q ".venv"
    )
)
if not exist ".venv\Scripts\python.exe" (
    echo Создаю окружение .venv ...
    !PY! -m venv .venv
    if errorlevel 1 goto :fail
)

echo Обновляю pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail

echo Ставлю библиотеки (первый раз 5-10 минут, нужен интернет) ...
rem только готовые сборки: без компилятора на компьютере
".venv\Scripts\python.exe" -m pip install --only-binary=:all: -r requirements.txt
if errorlevel 1 goto :fail

echo Проверяю установку ...
".venv\Scripts\python.exe" -c "import cv2, numpy, scipy, skimage, matplotlib, PySide6; import foto.gui; print('OK')"
if errorlevel 1 goto :fail

rem --- ярлык на рабочем столе -> start.bat
echo Создаю ярлык на рабочем столе ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Kuskovatost foto.lnk')); $s.TargetPath='%~dp0start.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.IconLocation='%SystemRoot%\System32\imageres.dll,186'; $s.Description='Kuskovatost porody po foto (demo)'; $s.Save()"
if errorlevel 1 echo [*] Ярлык создать не удалось - программу можно запускать файлом start.bat

echo.
echo ============================================================
echo  Готово. Запуск: ярлык "Kuskovatost foto" на рабочем столе или start.bat
echo ============================================================
pause
exit /b 0

rem ------------------------------------------------------------------
:find_python
set "PY="
for %%v in (3.12 3.13 3.11 3.10) do (
    if not defined PY (
        py -%%v -c "import sys" >nul 2>&1 && set "PY=py -%%v"
    )
)
if not defined PY (
    for %%v in (312 313 311 310) do (
        if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe""
    )
)
if not defined PY (
    python -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)" >nul 2>&1 && set "PY=python"
)
exit /b 0

:no_python
echo.
echo [ОШИБКА] Нужен Python 3.12 ^(подойдут 3.10-3.13^). Более новые 3.14/3.15 пока не подходят:
echo под них ещё нет готовых сборок нужных библиотек.
echo Скачайте Python 3.12 с https://www.python.org/downloads/windows/
echo ^(раздел Python 3.12, "Windows installer 64-bit"^), установите - он встанет рядом с 3.15, ничего удалять не нужно -
echo и запустите install.bat снова.
echo.
pause
exit /b 1

:fail
echo.
echo [ОШИБКА] Установка не завершена - см. сообщения выше.
echo Если причина неясна, пришлите текст этого окна разработчику.
pause
exit /b 1
