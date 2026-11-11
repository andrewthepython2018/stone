"""Чтение и запись файлов OpenCV с любыми путями.

cv2.imread / cv2.imwrite / cv2.VideoCapture под Windows не открывают пути с
кириллицей (например, C:\\Users\\...\\куски\\...). Картинки читаются и пишутся
через numpy (байты файла -> imdecode / imencode -> байты), видео — по
короткому имени 8.3 или, если его нет, через копию во временной папке.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np


def imread(path, flags=cv2.IMREAD_COLOR):
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, flags) if data.size else None


def imwrite(path, img, params=None) -> bool:
    ok, buf = cv2.imencode(Path(path).suffix or ".png", img, params or [])
    if ok:
        buf.tofile(str(path))
    return bool(ok)


def _short_path(path: str) -> str | None:
    if sys.platform != "win32":
        return None
    buf = ctypes.create_unicode_buffer(32768)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf))
    return buf.value if n and buf.value.isascii() else None


def open_video(source):
    """cv2.VideoCapture для номера камеры, URL или файла с любым именем."""
    if not isinstance(source, (str, os.PathLike)) or "://" in str(source):
        return cv2.VideoCapture(source)
    path = str(source)
    cap = cv2.VideoCapture(path)
    if cap.isOpened() or path.isascii() or not os.path.exists(path):
        return cap
    short = _short_path(path)
    if short:
        cap = cv2.VideoCapture(short)
        if cap.isOpened():
            return cap
    # короткие имена отключены на диске — читаем копию с латинским именем
    tmp = Path(tempfile.gettempdir()) / f"stone_video_{abs(hash(path)) % 10**8}{Path(path).suffix}"
    if not tmp.exists() or tmp.stat().st_size != os.path.getsize(path):
        shutil.copyfile(path, tmp)
    return cv2.VideoCapture(str(tmp))
