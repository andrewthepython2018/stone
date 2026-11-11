#!/usr/bin/env python3
"""Сборка переносной версии для Windows (без установки Python).

    python portable/build_portable.py ПАПКА_СБОРКИ

Работает на Windows и на Linux; нужен интернет (Python с NuGet, библиотеки с
PyPI) и pip. На Linux дополнительно нужен python3.12 — им компилируются .pyc
(на Windows это делает сам скачанный Python 3.12).
Результат: ПАПКА_СБОРКИ/Kuskovatost_foto/ и ПАПКА_СБОРКИ/Kuskovatost_foto.zip

Раскладка (файлы Python и библиотек не меняются — подписи и «репутация»
привязаны к содержимому; меняется только имя pythonw.exe):

    Kuskovatost.exe            pythonw.exe под другим именем (подпись PSF цела)
    Kuskovatost._pth           пути поиска модулей
    python312.dll, vcruntime*  нужны рядом с exe
    bin/python312.zip          стандартная библиотека (.pyc)
    bin/*.pyd, *.dll           модули стандартной библиотеки
    bin/sitecustomize.py       загрузчик (portable/boot)
    bin/libs.zip, libs.id      библиотеки; распаковываются при первом запуске
    bin/app/                   программа

Ни .bat, ни .lnk в архиве нет: Smart App Control молча блокирует их, если архив
скачан из интернета. В архиве ~70 файлов — Проводник распаковывает его за
секунды (7000 файлов с меткой «из интернета» — около 8 минут).
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PYVER = "3.12.10"
HERE = Path(__file__).resolve().parent
SRC = HERE.parent
NAME = "Kuskovatost_foto"

# лишнее в PySide6: QML/Quick, дизайнер, SQL и т. п.
PYSIDE_DIRS = ["qml", "metatypes", "include", "lib", "doc", "typesystems", "glue", "scripts", "resources",
               "plugins/qmltooling", "plugins/qmllint", "plugins/designer", "plugins/sqldrivers",
               "plugins/scenegraph"]
PYSIDE_FILES = ["*.exe", "opengl32sw.dll", "*.lib", "*.pyi",
                "Qt6Qml*.dll", "Qt6Quick*.dll", "Qt6Labs*.dll", "Qt6Lottie*.dll", "Qt6Designer*.dll",
                "Qt6Help.dll", "Qt6UiTools.dll", "Qt6Sql.dll", "Qt6Test.dll", "Qt6Concurrent.dll",
                "Qt6OpenGLWidgets.dll", "pyside6qml.abi3.dll",
                "QtQml*.pyd", "QtQuick*.pyd", "QtLabs*.pyd", "QtDesigner.pyd", "QtHelp.pyd", "QtUiTools.pyd",
                "QtSql.pyd", "QtTest.pyd", "QtConcurrent.pyd", "QtOpenGLWidgets.pyd", "QtOpenGL.pyd"]
STDLIB_SKIP = ["ensurepip", "idlelib", "turtledemo", "test", "tkinter", "lib2to3", "pydoc_data",
               "venv/scripts"]


def run(*cmd, **kw):
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def rm(path: Path):
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def main(out: Path) -> None:
    out = out.resolve()
    dist, stage, dl = out / NAME, out / "stage", out / "dl"
    rm(dist)
    rm(stage)
    for d in (dist / "bin", stage, dl / "wheels"):
        d.mkdir(parents=True, exist_ok=True)

    # 1. Python для Windows (полный, из пакета NuGet «python»)
    nupkg = dl / f"python.{PYVER}.nupkg"
    if not nupkg.exists():
        print("скачиваю Python", PYVER)
        urllib.request.urlretrieve(
            f"https://api.nuget.org/v3-flatcontainer/python/{PYVER}/python.{PYVER}.nupkg", nupkg)
    with zipfile.ZipFile(nupkg) as z:
        z.extractall(stage, [n for n in z.namelist() if n.startswith("tools/")])
    rt = stage / "tools"
    lib, sp = rt / "Lib", rt / "Lib" / "site-packages"
    for d in STDLIB_SKIP:
        rm(lib / d)
    for p in sp.glob("pip*"):
        rm(p)

    # 2. Библиотеки: готовые сборки для win_amd64 / Python 3.12
    print("скачиваю библиотеки")
    plat = ["--only-binary=:all:", "--platform", "win_amd64", "--python-version", "3.12",
            "--implementation", "cp", "--no-deps"]
    run(sys.executable, "-m", "pip", "download", "-q", *plat, "-d", dl / "wheels",
        "-r", HERE / "requirements-win.txt")
    run(sys.executable, "-m", "pip", "install", "-q", "--no-compile", "--no-warn-script-location",
        "--target", sp, *plat, "--no-index", "--find-links", dl / "wheels",
        "-r", HERE / "requirements-win.txt")

    # 3. Убираем лишнее (QML/Quick, дизайнер, видеокодеки OpenCV, тесты)
    ps = sp / "PySide6"
    for d in PYSIDE_DIRS:
        rm(ps / d)
    for f in list(ps.iterdir()):
        if f.is_file() and any(fnmatch.fnmatch(f.name, pat) for pat in PYSIDE_FILES):
            f.unlink()
    for f in (ps / "translations").glob("*"):
        if f.is_file() and f.name != "qtbase_ru.qm":
            f.unlink()
    for f in (sp / "cv2").glob("opencv_videoio_ffmpeg*.dll"):
        f.unlink()
    rm(sp / "bin")
    for d in [p for p in sp.rglob("*") if p.is_dir() and p.name in ("__pycache__", "tests")]:
        # numpy/_core/tests оставляем — numpy.testing импортирует оттуда _natype
        if d.exists() and not (d.name == "tests" and d.parent.name == "_core" and d.parent.parent.name == "numpy"):
            rm(d)

    # 4. Раскладка
    b = dist / "bin"
    shutil.copy2(rt / "pythonw.exe", dist / "Kuskovatost.exe")
    for n in ("python312.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        shutil.copy2(rt / n, dist / n)
    (dist / "Kuskovatost._pth").write_bytes(b"bin\\python312.zip\r\nbin\r\nimport site\r\n")
    for f in (rt / "DLLs").iterdir():
        if f.suffix in (".pyd", ".dll"):
            shutil.copy2(f, b / f.name)
    shutil.copy2(rt / "LICENSE.txt", b / "Python-LICENSE.txt")
    shutil.copy2(HERE / "boot" / "sitecustomize.py", b / "sitecustomize.py")
    ignore = shutil.ignore_patterns("__pycache__")
    shutil.copytree(SRC / "foto", b / "app" / "foto", ignore=ignore)
    shutil.copytree(SRC / "fragmentation", b / "app" / "fragmentation", ignore=ignore)
    shutil.copy2(SRC / "run.py", b / "app" / "run.py")
    shutil.copytree(SRC / "primery", dist / "Примеры фото")
    shutil.copy2(HERE / "ПРОЧТИ МЕНЯ.txt", dist / "ПРОЧТИ МЕНЯ.txt")

    # 5. Компиляция .pyc интерпретатором той же версии, что в сборке
    py312 = [str(rt / "python.exe"), "-I"] if sys.platform == "win32" else [os.environ.get("PY312", "python3.12")]
    comp = [*py312, "-m", "compileall", "-q", "--invalidation-mode", "unchecked-hash"]
    print("компилирую .pyc")
    subprocess.run([*comp, str(b / "app"), str(b / "sitecustomize.py")], check=True)
    subprocess.run([*comp, "-b", "-x", "site-packages", str(lib)], stdout=subprocess.DEVNULL)   # legacy .pyc
    subprocess.run([*comp, "-j", "0", str(sp)], stdout=subprocess.DEVNULL)

    # 6. Стандартная библиотека -> bin/python312.zip (только .pyc, как в «embeddable»-сборке)
    with zipfile.ZipFile(b / "python312.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, dirs, files in os.walk(lib):
            dirs[:] = sorted(d for d in dirs if d not in ("site-packages", "__pycache__"))
            for f in sorted(files):
                p = Path(root) / f
                if f.endswith(".py") and p.with_suffix(".pyc").exists():
                    continue                      # есть .pyc — исходник не нужен
                z.write(p, p.relative_to(lib).as_posix())

    # 7. Библиотеки -> bin/libs.zip; метка версии — bin/libs.id
    print("упаковываю библиотеки")
    h = hashlib.sha256()
    longest = 0
    with zipfile.ZipFile(b / "libs.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, dirs, files in os.walk(sp):
            dirs.sort()
            for f in sorted(files):
                p = Path(root) / f
                rel = p.relative_to(lib).as_posix()
                longest = max(longest, len(rel))
                h.update(rel.encode())
                h.update(p.read_bytes())
                z.write(p, rel)
    assert longest < 170, f"слишком длинный путь в библиотеках: {longest}"
    (b / "libs.id").write_text(h.hexdigest()[:12], encoding="ascii")

    # 8. Архив (имена в UTF-8; libs.zip уже сжат — кладётся без повторного сжатия)
    arc = out / f"{NAME}.zip"
    rm(arc)
    n = 0
    with zipfile.ZipFile(arc, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, dirs, files in os.walk(dist):
            dirs.sort()
            for f in sorted(files):
                p = Path(root) / f
                z.write(p, p.relative_to(out).as_posix(),
                        compress_type=zipfile.ZIP_STORED if f.endswith(".zip") else zipfile.ZIP_DEFLATED)
                n += 1
    rm(stage)
    print(f"готово: {arc}  ({arc.stat().st_size / 1e6:.0f} МБ, файлов в архиве: {n})")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "build"))
