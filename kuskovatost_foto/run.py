"""Точка входа переносной версии: Kuskovatost.exe запускает
runtime/pythonw.exe -E -s -X utf8 app/run.py [ФОТО ...].

Пока грузятся библиотеки (первый запуск — до полуминуты), показывается
заставка. Любая ошибка запуска пишется в журнал и показывается окном,
а не пропадает молча.
"""
import faulthandler
import os
import sys
import time
import traceback
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))

REPORTED = 3            # код выхода «ошибка уже показана пользователю»


def _message(text: str):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "Кусковатость породы по фото", 0x10)
    except Exception:      # noqa: BLE001
        print(text, file=sys.stderr)


def _log_path() -> Path:
    for d in (APP.parent / "logs", Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "KuskovatostFoto" / "logs"):
        try:
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "foto.log", "a", encoding="utf-8"):
                pass
            return d / "foto.log"
        except OSError:
            continue
    return Path(os.devnull)


def main() -> int:
    log = _log_path()
    if sys.stderr is None or sys.stdout is None:
        f = open(log, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = f
    print(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')}  Python {sys.version.split()[0]}  {APP}")
    faulthandler.enable(sys.stderr)
    try:
        if sys.platform == "win32":
            import ctypes
            # своя иконка и группа на панели задач, а не значок python
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kuskovatost.Foto")
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
        from PySide6.QtWidgets import QApplication, QSplashScreen

        app = QApplication(sys.argv[:1])
        pm = QPixmap(460, 150)
        pm.fill(QColor("#263240"))
        p = QPainter(pm)
        p.setPen(QColor("white"))
        f = QFont()
        f.setPointSize(15)
        f.setBold(True)
        p.setFont(f)
        p.drawText(pm.rect().adjusted(0, -20, 0, 0), Qt.AlignCenter, "Кусковатость породы по фото")
        f.setPointSize(10)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor("#ffd600"))
        p.drawText(pm.rect().adjusted(0, 60, 0, 0), Qt.AlignCenter, "Загрузка…")
        p.end()
        splash = QSplashScreen(pm)
        splash.show()
        app.processEvents()

        from foto.gui import main as gui_main          # долгий импорт: numpy, scipy, OpenCV, matplotlib
        return gui_main(sys.argv[1:], splash=splash) or 0
    except Exception:      # noqa: BLE001
        traceback.print_exc()
        _message("Программа не смогла запуститься.\n\n"
                 f"{traceback.format_exc(limit=3)}\n"
                 f"Журнал: {log}\n\nПришлите этот файл разработчику.")
        return REPORTED


if __name__ == "__main__":
    sys.exit(main())
