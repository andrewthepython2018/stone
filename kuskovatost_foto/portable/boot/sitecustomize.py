"""Загрузчик переносной версии «Кусковатость породы по фото».

Kuskovatost.exe — это pythonw.exe из официальной сборки Python (подпись Python
Software Foundation сохраняется), только переименованный. Своего exe, bat-файла
и ярлыка в архиве нет: Smart App Control в Windows 11 молча блокирует .bat и
.lnk из архива, скачанного из интернета, а подписанный exe — запускает.

Python сам импортирует этот модуль при старте (site -> sitecustomize). Здесь:
  1. при первом запуске библиотеки (bin/libs.zip, ~4000 файлов) распаковываются
     в %LOCALAPPDATA%/KuskovatostFoto/libs-<метка> с окном «подготовка… N %»
     (в самом архиве программы файлов мало — Проводник распаковывает его быстро);
  2. запускается bin/app/run.py; аргументы — фото, перетащенные на exe.
"""
import os
import sys

TITLE = "Кусковатость породы по фото"


def _root() -> str:
    return os.path.dirname(os.path.abspath(sys.executable))


def _msg(text: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10)
    except Exception:       # noqa: BLE001
        print(text, file=sys.stderr)


class _Progress:
    """Маленькое окно без рамки со строкой текста (WinAPI, класс STATIC)."""

    def __init__(self, text: str):
        self.hwnd = None
        try:
            import ctypes
            from ctypes import wintypes
            self.u = u = ctypes.windll.user32
            g = ctypes.windll.gdi32
            u.CreateWindowExW.restype = wintypes.HWND
            u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                          wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
            u.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
            u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            u.DestroyWindow.argtypes = [wintypes.HWND]
            u.UpdateWindow.argtypes = [wintypes.HWND]
            u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                       wintypes.UINT, wintypes.UINT]
            g.GetStockObject.restype = wintypes.HANDLE
            self._MSG, self._byref = wintypes.MSG, ctypes.byref
            w, h = 620, 90
            x = (u.GetSystemMetrics(0) - w) // 2
            y = (u.GetSystemMetrics(1) - h) // 2
            # WS_EX_TOPMOST | WS_EX_TOOLWINDOW; WS_POPUP | WS_VISIBLE | WS_BORDER | SS_CENTER | SS_CENTERIMAGE
            self.hwnd = u.CreateWindowExW(0x8 | 0x80, "STATIC", text, 0x80000000 | 0x10000000 | 0x00800000 | 0x1 | 0x200,
                                          x, y, w, h, None, None, None, None)
            if self.hwnd:
                u.SendMessageW(self.hwnd, 0x30, g.GetStockObject(17) or 0, 1)      # WM_SETFONT, DEFAULT_GUI_FONT
                self.pump()
        except Exception:       # noqa: BLE001 — окно необязательно
            self.hwnd = None

    def pump(self) -> None:
        if not self.hwnd:
            return
        m = self._MSG()
        while self.u.PeekMessageW(self._byref(m), None, 0, 0, 1):
            self.u.TranslateMessage(self._byref(m))
            self.u.DispatchMessageW(self._byref(m))
        self.u.UpdateWindow(self.hwnd)

    def text(self, s: str) -> None:
        if self.hwnd:
            self.u.SetWindowTextW(self.hwnd, s)
            self.pump()

    def close(self) -> None:
        if self.hwnd:
            self.u.DestroyWindow(self.hwnd)
            self.hwnd = None


def _writable_bases(root: str):
    la = os.environ.get("LOCALAPPDATA")
    if la:
        yield os.path.join(la, "KuskovatostFoto")
    yield os.path.join(os.path.expanduser("~"), "KuskovatostFoto")
    yield os.path.join(root, "bin", "cache")           # флешка / нет профиля


def ensure_libs(root: str) -> str:
    """Папка с распакованными библиотеками (распаковывает при первом запуске)."""
    binr = os.path.join(root, "bin")
    archive = os.path.join(binr, "libs.zip")
    with open(os.path.join(binr, "libs.id"), encoding="ascii") as f:
        tag = f.read().strip()
    name = "libs-" + tag
    bases = list(_writable_bases(root))
    for b in bases:
        if os.path.isfile(os.path.join(b, name, "ok")):
            return os.path.join(b, name)

    import shutil
    import zipfile
    last = None
    for b in bases:
        tmp = os.path.join(b, f"{name}.tmp{os.getpid()}")
        dst = os.path.join(b, name)
        try:
            os.makedirs(tmp, exist_ok=True)
        except OSError as e:
            last = e
            continue
        prog = _Progress(f"{TITLE}: первый запуск, подготовка…")
        try:
            with zipfile.ZipFile(archive) as z:
                items = z.infolist()
                total = sum(i.file_size for i in items) or 1
                done, shown = 0, -1
                for i in items:
                    z.extract(i, tmp)
                    done += i.file_size
                    pct = int(100 * done / total)
                    if pct != shown:
                        shown = pct
                        prog.text(f"{TITLE}: первый запуск, подготовка библиотек… {pct} %")
            with open(os.path.join(tmp, "ok"), "w", encoding="ascii") as f:
                f.write(tag)
            if os.path.isdir(dst):                      # недораспакованный остаток / параллельный запуск
                if os.path.isfile(os.path.join(dst, "ok")):
                    shutil.rmtree(tmp, ignore_errors=True)
                    return dst
                shutil.rmtree(dst, ignore_errors=True)
            os.replace(tmp, dst)
            _cleanup(b, name)
            return dst
        except OSError as e:
            last = e
            shutil.rmtree(tmp, ignore_errors=True)
        finally:
            prog.close()
    raise OSError(f"Не удалось распаковать библиотеки программы (нужно около 400 МБ на диске): {last}")


def _cleanup(base: str, keep: str) -> None:
    """Удалить библиотеки прошлых версий и остатки прерванных распаковок."""
    import shutil
    try:
        for n in os.listdir(base):
            if n.startswith("libs-") and n != keep:
                shutil.rmtree(os.path.join(base, n), ignore_errors=True)
    except OSError:
        pass


def _boot() -> None:
    if not os.path.basename(sys.executable or "").lower().startswith("kuskovatost"):
        return                                           # обычный python — ничего не делаем
    root = _root()
    code = 0
    try:
        libs = ensure_libs(root)
        import site
        site.addsitedir(os.path.join(libs, "site-packages"))
        app = os.path.join(root, "bin", "app")
        run_py = os.path.join(app, "run.py")
        os.environ["KUSK_ROOT"] = root
        sys.argv = [run_py] + list(getattr(sys, "orig_argv", sys.argv)[1:])
        import runpy
        runpy.run_path(run_py, run_name="__main__")
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException:       # noqa: BLE001
        import traceback
        text = traceback.format_exc()
        try:
            d = os.path.join(root, "logs")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "foto.log"), "a", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass
        _msg("Программа не смогла запуститься.\n\n" + text[-1500:])
        code = 3
    _exit(code)


def _exit(code: int) -> None:
    """sys.exit() внутри sitecustomize Python считает сбоем запуска и пишет
    «Traceback … SystemExit: 0» в журнал. Выходим сами."""
    try:
        import atexit
        atexit._run_exitfuncs()
    except BaseException:       # noqa: BLE001
        pass
    for s in (sys.stdout, sys.stderr):
        try:
            s.flush()
        except Exception:       # noqa: BLE001
            pass
    os._exit(code)


_boot()
