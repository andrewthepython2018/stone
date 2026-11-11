"""Ярлык на рабочем столе для переносной версии.

Ярлык открывает Kuskovatost.exe (подписанный pythonw.exe под другим именем,
см. portable/boot/sitecustomize.py) через Проводник: explorer.exe "…exe".
Ярлык прямо на exe с меткой «из интернета» Smart App Control блокирует, как и
ярлык из скачанного архива; проверено на Windows 11.
Создаётся через COM (IShellLink) средствами comtypes, без PowerShell.
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

LINK_NAME = "Кусковатость породы по фото.lnk"
APP = Path(__file__).resolve().parent.parent          # папка app/
ROOT = Path(os.environ.get("KUSK_ROOT") or APP.parent)
EXE = ROOT / "Kuskovatost.exe"


def portable() -> bool:
    """Запущены из переносной сборки (Kuskovatost.exe)."""
    return sys.platform == "win32" and bool(os.environ.get("KUSK_ROOT")) and EXE.exists()


def desktop() -> Path | None:
    buf = ctypes.create_unicode_buffer(32768)
    # CSIDL_DESKTOPDIRECTORY = 0x10 (учитывает рабочий стол, перенесённый в OneDrive)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) != 0:
        return None
    return Path(buf.value)


def link_path() -> Path | None:
    d = desktop()
    return d / LINK_NAME if d else None


def _flag() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "KuskovatostFoto" / "shortcut_asked"


def create(path: Path) -> None:
    import comtypes
    import comtypes.client
    from comtypes.persist import IPersistFile
    from comtypes.shelllink import IShellLinkW, ShellLink

    comtypes.CoInitialize()
    link = comtypes.client.CreateObject(ShellLink, interface=IShellLinkW)
    # Ярлык прямо на Kuskovatost.exe Smart App Control блокирует, если у exe есть
    # метка «из интернета» («Dangerous file extension from the web»); двойной щелчок
    # по exe разрешён — поэтому ярлык открывает exe через Проводник.
    windir = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    link.SetPath(os.path.join(windir, "explorer.exe"))
    link.SetArguments(f'"{EXE}"')
    link.SetWorkingDirectory(str(ROOT))
    link.SetIconLocation(str(APP / "foto" / "icon.ico"), 0)
    link.SetDescription("Кусковатость породы по фото")
    link.QueryInterface(IPersistFile).Save(str(path), True)


def offer(parent=None) -> None:
    """Первый запуск — спросить про ярлык; если ярлык уже есть — молча обновить
    (папку с программой могли перенести)."""
    if not portable():
        return
    try:
        path = link_path()
        if path is None:
            return
        if path.exists():
            create(path)
            return
        flag = _flag()
        if flag.exists():
            return
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("1", encoding="utf-8")
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(parent, "Ярлык", "Создать ярлык программы на рабочем столе?") \
                == QMessageBox.StandardButton.Yes:
            create(path)
    except Exception as e:      # noqa: BLE001 — ярлык не должен мешать работе
        print(f"ярлык: {e}", file=sys.stderr)
