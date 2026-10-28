#!/usr/bin/env bash
# Сборка переносной версии для Windows (без установки Python) на Linux.
#   portable/build_portable.sh ПАПКА_СБОРКИ
# Нужны: curl, unzip, pip, x86_64-w64-mingw32-gcc/windres; wine — для
# предкомпиляции .pyc (необязательно).
# Результат: ПАПКА_СБОРКИ/Kuskovatost_foto/ и ПАПКА_СБОРКИ/Kuskovatost_foto.zip
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=$(dirname "$HERE")
OUT=$(realpath -m "${1:-build}")
PYVER=3.12.10
DIST=$OUT/Kuskovatost_foto
mkdir -p "$OUT/dl"
rm -rf "$DIST"; mkdir -p "$DIST"

# 1. Python для Windows (полный, из пакета NuGet «python»)
[ -f "$OUT/dl/python.nupkg" ] || curl -sSL -o "$OUT/dl/python.nupkg" \
    "https://api.nuget.org/v3-flatcontainer/python/$PYVER/python.$PYVER.nupkg"
unzip -q "$OUT/dl/python.nupkg" 'tools/*' -d "$OUT/tmp_py"
mv "$OUT/tmp_py/tools" "$DIST/runtime"; rm -rf "$OUT/tmp_py"
R=$DIST/runtime
rm -rf "$R/include" "$R/libs" "$R"/Lib/site-packages/pip* "$R/Lib/ensurepip" "$R/Lib/idlelib" "$R/Lib/turtledemo"

# 2. Библиотеки: готовые сборки для win_amd64 / Python 3.12
pip download -q --only-binary=:all: --platform win_amd64 --python-version 3.12 --implementation cp \
    --no-deps -d "$OUT/dl/wheels" -r "$HERE/requirements-win.txt"
pip install -q --no-deps --no-compile --target "$R/Lib/site-packages" --platform win_amd64 \
    --python-version 3.12 --implementation cp --only-binary=:all: "$OUT"/dl/wheels/*.whl

# 3. Убираем лишнее (QML/Quick, дизайнер, видеокодеки OpenCV, тесты)
SP=$R/Lib/site-packages
P=$SP/PySide6
( cd "$P"
  rm -rf qml metatypes include lib doc typesystems glue scripts resources *.exe opengl32sw.dll *.lib
  rm -f Qt6Qml*.dll Qt6Quick*.dll Qt6Labs*.dll Qt6Lottie*.dll Qt6Designer*.dll Qt6Help.dll Qt6UiTools.dll \
        Qt6Sql.dll Qt6Test.dll Qt6Concurrent.dll Qt6OpenGLWidgets.dll pyside6qml.abi3.dll
  rm -f QtQml*.pyd QtQuick*.pyd QtLabs*.pyd QtDesigner.pyd QtHelp.pyd QtUiTools.pyd QtSql.pyd QtTest.pyd \
        QtConcurrent.pyd QtOpenGLWidgets.pyd QtOpenGL.pyd *.pyi
  find translations -type f ! -name 'qtbase_ru.qm' -delete
  rm -rf plugins/qmltooling plugins/qmllint plugins/designer plugins/sqldrivers plugins/scenegraph )
rm -f "$SP"/cv2/opencv_videoio_ffmpeg*.dll
find "$SP" -type d \( -name tests -o -name __pycache__ \) -prune -exec rm -rf {} +

# 4. Программа
mkdir -p "$DIST/app"
cp -r "$SRC/foto" "$SRC/fragmentation" "$SRC/run.py" "$DIST/app/"
find "$DIST/app" -name __pycache__ -prune -exec rm -rf {} +
cp -r "$SRC/primery" "$DIST/Примеры фото"
cp "$HERE/ПРОЧТИ МЕНЯ.txt" "$DIST/"

# 5. Kuskovatost.exe
( cd "$HERE"
  x86_64-w64-mingw32-windres -c 65001 launcher.rc -O coff -o "$OUT/launcher_res.o"
  x86_64-w64-mingw32-gcc -O2 -s -municode -mwindows -static -o "$DIST/Kuskovatost.exe" \
      launcher.c "$OUT/launcher_res.o" -lole32 -luuid -lshell32 )

# 6. Предкомпиляция .pyc самим Python для Windows — быстрее первый запуск,
#    и ничего не пишется в папку программы (может быть только для чтения)
if command -v wine >/dev/null; then
    ( cd "$DIST" && WINEDEBUG=-all wine runtime/python.exe -E -s -m compileall -q -j 0 \
        runtime/Lib app >/dev/null 2>&1 ) || echo "предкомпиляция пропущена"
fi

# 7. Архив (имена файлов в UTF-8 — правильно открывается в Проводнике Windows)
rm -f "$OUT/Kuskovatost_foto.zip"
( cd "$OUT" && python3 - <<'PY'
import os, zipfile
with zipfile.ZipFile("Kuskovatost_foto.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for root, dirs, files in os.walk("Kuskovatost_foto"):
        dirs.sort()
        for f in sorted(files):
            z.write(os.path.join(root, f))
PY
)
du -sh "$DIST" "$OUT/Kuskovatost_foto.zip"
