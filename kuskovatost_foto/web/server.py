"""Веб-версия «Кусковатость породы по фото».

Заказчик открывает страницу в браузере, загружает фото и получает тот же
результат, что и в программе для Windows: расчёт идёт на сервере тем же кодом
(foto/analyze.py, fragmentation/*). Фото хранятся только в памяти сервера,
пока с ними работают (не дольше FOTO_TTL), и никуда не записываются.

Запуск:  python -m web            (http://127.0.0.1:8000)
         PORT=7860 python -m web  (порт задаётся переменной PORT)
"""
from __future__ import annotations

import base64
import csv
import io
import os
import secrets
import threading
import time
import uuid
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from foto.analyze import (SIEVES, BucketInfo, PhotoResult, analyze, finalize, find_bucket, fmt_x,
                          polygon_roi, size_classes, to_work)
from fragmentation.bucket import BucketDetector
from fragmentation.config import Config

STATIC = Path(__file__).with_name("static")
MAX_UPLOAD = 40 * 1024 * 1024          # байт на одно фото
MAX_PHOTOS = int(os.environ.get("FOTO_CACHE", "60"))      # фото в памяти сервера (все пользователи)
FOTO_TTL = float(os.environ.get("FOTO_TTL", str(3 * 3600)))  # с, сколько фото живёт без обращений
WORKERS = int(os.environ.get("FOTO_WORKERS", str(max(1, min(4, (os.cpu_count() or 2) - 1)))))
DETAIL = {"precise": 1.0, "fast": 0.5}

CFG = Config()
_slots = threading.BoundedSemaphore(WORKERS)    # одновременно считаемых фото
_tls = threading.local()                        # детектор ковша на поток (у него есть состояние)


def detector() -> BucketDetector:
    d = getattr(_tls, "det", None)
    if d is None:
        d = _tls.det = BucketDetector(CFG.detector)
    return d


@dataclass(eq=False)
class Photo:
    name: str
    img: np.ndarray                         # рабочее фото (не шире 1920 px)
    bucket: BucketInfo | None = None
    result: PhotoResult | None = None
    used: float = field(default_factory=time.time)


class Store:
    """Фото в памяти: последние MAX_PHOTOS, каждое — не дольше FOTO_TTL без обращений."""

    def __init__(self):
        self.items: OrderedDict[str, Photo] = OrderedDict()
        self.lock = threading.Lock()

    def add(self, ph: Photo) -> str:
        pid = uuid.uuid4().hex
        with self.lock:
            self._expire()
            self.items[pid] = ph
            while len(self.items) > MAX_PHOTOS:
                self.items.popitem(last=False)
        return pid

    def get(self, pid: str) -> Photo:
        with self.lock:
            self._expire()
            ph = self.items.get(pid)
            if ph is None:
                # браузер загрузит фото заново (оно у него осталось)
                raise HTTPException(410, "Фото уже удалено с сервера, загружаю заново")
            ph.used = time.time()
            self.items.move_to_end(pid)
            return ph

    def drop(self, pid: str) -> None:
        with self.lock:
            self.items.pop(pid, None)

    def _expire(self):
        now = time.time()
        for k in [k for k, p in self.items.items() if now - p.used > FOTO_TTL]:
            del self.items[k]


store = Store()
app = FastAPI(title="Кусковатость породы по фото", docs_url=None, redoc_url=None)

# Необязательный пароль на всю страницу: переменная ACCESS_CODE. Браузер спросит
# имя и пароль; имя любое, пароль — ACCESS_CODE.
ACCESS_CODE = os.environ.get("ACCESS_CODE", "")


@app.middleware("http")
async def access_code(request: Request, call_next):
    if ACCESS_CODE and request.url.path != "/api/health":
        ok = False
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("basic "):
            try:
                ok = secrets.compare_digest(base64.b64decode(auth[6:]).decode().partition(":")[2], ACCESS_CODE)
            except (ValueError, UnicodeDecodeError):
                ok = False
        if not ok:
            return Response("Нужен пароль", status_code=401, media_type="text/plain; charset=utf-8",
                            headers={"WWW-Authenticate": 'Basic realm="kuskovatost", charset="UTF-8"'})
    return await call_next(request)


# ---------------------------------------------------------------- картинки
def jpeg_b64(img: np.ndarray, quality: int = 85) -> str:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode() if ok else ""


def draw_bucket(img: np.ndarray, b: BucketInfo | None) -> None:
    """Найденный ковш: рамка корпуса блока и боковые стенки (как в программе)."""
    if b is None or b.det is None:
        return
    d = b.det
    w = d.housing_w
    cv2.rectangle(img, (int(d.cx - w / 2), int(d.top)), (int(d.cx + w / 2), int(d.top + 0.25 * w)),
                  (255, 200, 0), 3)
    wl = b.walls
    if wl is not None and 0.95 <= wl.width(wl.y_ref) / w <= 1.35:
        y0, y1 = wl.y_top, wl.y_ref
        for f in (wl.x_left, wl.x_right):
            cv2.line(img, (int(f(y0)), int(y0)), (int(f(y1)), int(y1)), (0, 165, 255), 3)


# ---------------------------------------------------------------- результат -> JSON
def _clean(v):
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, (np.floating, float)):
        v = float(v)
        return v if np.isfinite(v) else None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def stats_view(r: PhotoResult, use_fines: bool) -> dict:
    finalize(r, use_fines)
    st = dict(r.stats)
    out = {"stats": _clean(st), "classes": [[n, float(c)] for n, c in r.classes]}
    if "x50_mm" in st:
        out["x_text"] = {k: fmt_x(st, k) for k in ("x10_mm", "x50_mm", "x80_mm", "x90_mm")}
    return out


def describe(r: PhotoResult, st: dict) -> str:
    if "x50_mm" not in st:
        return (f"Выделено кусков: {st.get('n', 0)} — слишком мало для расчёта. "
                "Обведите область побольше или выберите расчёт «Точно».")
    src = "порода в ковше (найден автоматически)" if r.source == "ковш" else "обведённая область"
    fines = (f"Голубая штриховка — мелочь < {st.get('fines_below_mm', st['resolution_mm']):.0f} мм, "
             f"{st['fines_pct']:.0f} % площади" + (" (учтена)" if st.get("fines_used") else " (не учтена)") + ". ")
    unk = (f"Серая — не распознано, {st['unknown_pct']:.0f} % (не учитывается). "
           if st.get("unknown_pct", 0) >= 1 else "")
    return (f"{src.capitalize()}: кусков {st['n']}, масштаб {st['mm_per_px']:.2f} мм/пикс ({r.scale_note}). "
            + fines + unk + f"Расчёт {getattr(r, 'time_s', 0):.1f} с.")


def result_json(ph: Photo, use_fines: bool) -> dict:
    r = ph.result
    on, off = stats_view(r, True), stats_view(r, False)
    finalize(r, use_fines)
    ov = r.overlay.copy()
    draw_bucket(ov, ph.bucket)
    return {"ok": True, "source": r.source, "scale_note": r.scale_note, "time_s": getattr(r, "time_s", 0.0),
            "with_fines": on | {"text": describe(r, on["stats"])},
            "without_fines": off | {"text": describe(r, off["stats"])},
            "overlay": jpeg_b64(ov)}


def bucket_json(info: BucketInfo, width_mm: float) -> dict:
    return {"found": info.found, "usable": info.roi is not None, "message": info.message,
            "mm_per_px": info.mm_per_px(width_mm)}


# ---------------------------------------------------------------- API
@app.post("/api/photo")
async def upload(file: UploadFile = File(...)):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Фото больше 40 МБ")

    def prep():
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        return to_work(img)[0]
    img = await run_in_threadpool(prep)
    if img is None:
        raise HTTPException(415, "Не удалось прочитать фото (нужен jpg, png, bmp, tif или webp)")
    ph = Photo(file.filename or "фото", img)
    pid = store.add(ph)
    return {"id": pid, "width": img.shape[1], "height": img.shape[0], "image": jpeg_b64(img, 88)}


@app.delete("/api/photo/{pid}")
def remove(pid: str):
    store.drop(pid)
    return {"ok": True}


class RunIn(BaseModel):
    mode: str = "auto"                 # auto — найти ковш; polygon — по обведённому контуру
    polygon: list[list[float]] = []    # точки контура в координатах рабочего фото
    scale: float | None = None         # мм/пикс для контура (None — по ковшу, если найден)
    scale_src: str = ""                # ковш / эталон / вручную / условный
    bucket_width_mm: float = 3000.0
    detail: str = "precise"
    use_fines: bool = True


SCALE_NOTE = {"ковш": "по ковшу на фото (приблизительно)", "эталон": "по эталону",
              "вручную": "введён вручную", "условный": "УСЛОВНЫЙ (задайте эталон)"}


def _run(ph: Photo, p: RunIn) -> dict:
    cfg = Config()
    cfg.scale.bucket_width_mm = float(min(20000.0, max(300.0, p.bucket_width_mm)))
    detail = DETAIL.get(p.detail, 1.0)
    with _slots:
        if p.mode == "auto" or ph.bucket is None:
            ph.bucket = find_bucket(ph.img, cfg, detector())
        b = ph.bucket
        out = {"bucket": bucket_json(b, cfg.scale.bucket_width_mm)}
        if p.mode == "auto":
            if b.roi is None:
                out["ok"] = False
                return out
            roi, source, note = b.roi, "ковш", "по ширине ковша (по строкам, с учётом перспективы)"
        else:
            scale, src = p.scale, p.scale_src
            if src in ("", "ковш") and b.found:
                scale, src = b.mm_per_px(cfg.scale.bucket_width_mm), "ковш"
            if not scale or scale <= 0:
                scale, src = 10.0, "условный"
            roi = polygon_roi(ph.img.shape, p.polygon, float(scale))
            if roi is None:
                raise HTTPException(422, "Область слишком маленькая — обведите больше")
            source, note = "контур", SCALE_NOTE.get(src, "")
            out["scale"], out["scale_src"] = float(scale), src
        t = time.time()
        r = analyze(ph.img, roi, cfg, ph.name, source, detail, note)
        r.time_s = time.time() - t
        ph.result = r
    return out | result_json(ph, p.use_fines)


@app.post("/api/photo/{pid}/run")
async def run(pid: str, p: RunIn):
    ph = store.get(pid)
    return await run_in_threadpool(_run, ph, p)


class ReportIn(BaseModel):
    ids: list[str]
    names: list[str] = []
    use_fines: bool = True


@app.post("/api/report")
async def report(p: ReportIn):
    rows = []
    for i, pid in enumerate(p.ids):
        try:
            ph = store.get(pid)
        except HTTPException:
            continue
        if ph.result is not None:
            rows.append((i + 1, ph))
    if not rows:
        raise HTTPException(410, "Нет посчитанных фото на сервере — посчитайте их ещё раз")
    data = await run_in_threadpool(build_report, rows, p.use_fines)
    name = f"kuskovatost_foto_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def build_report(rows: list, use_fines: bool) -> bytes:
    names = [n for n, _ in size_classes({str(s): 0 for s in SIEVES})]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        sio = io.StringIO()
        w = csv.writer(sio, delimiter=";")
        w.writerow(["№", "фото", "область", "кусков", "масштаб, мм/пикс", "X10, мм", "X50, мм", "X80, мм",
                    "X90, мм", "d_ср, мм", "макс., мм", "мелочь (по площади), %", "мелочь учтена",
                    "не распознано, %"] + [f"{n}, %" for n in names])
        for i, ph in rows:
            r = ph.result
            finalize(r, use_fines)
            s = r.stats

            def v(k):
                if k in ("x10_mm", "x50_mm", "x80_mm", "x90_mm"):
                    return fmt_x(s, k)
                return f"{s[k]:.1f}".replace(".", ",") if k in s else ""
            w.writerow([i, ph.name, r.source, s.get("n", 0), v("mm_per_px"), v("x10_mm"), v("x50_mm"),
                        v("x80_mm"), v("x90_mm"), v("d_mean_mm"), v("d_max_mm"), v("fines_pct"),
                        "да" if s.get("fines_used") else "нет", v("unknown_pct")] +
                       [f"{c:.1f}".replace(".", ",") for _, c in r.classes])
            ov = r.overlay.copy()
            draw_bucket(ov, ph.bucket)
            ok, jpg = cv2.imencode(".jpg", ov, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if ok:
                z.writestr(f"overlays/{i:02d}_{Path(ph.name).stem}_kuski.jpg", jpg.tobytes())
        z.writestr("results.csv", "﻿" + sio.getvalue())
        png = granulometry_png([ph for _, ph in rows])
        if png:
            z.writestr("granulometry.png", png)
    return buf.getvalue()


def granulometry_png(photos: list[Photo]) -> bytes | None:
    """График для отчёта: кривые состава по всем фото и X50/X80 по фото."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    done = [p for p in photos if "x50_mm" in p.result.stats]
    if not done:
        return None
    fig = Figure(figsize=(7, 7.5), tight_layout=True)
    a1, a2 = fig.add_subplot(211), fig.add_subplot(212)
    for k, p in enumerate(done):
        c = np.array(p.result.stats["curve"])
        a1.plot(c[:, 0], c[:, 1], lw=1.8, label=f"{k + 1}. {p.name[:28]}")
    a1.set_xscale("log")
    a1.set_ylim(0, 100)
    a1.grid(True, which="both", alpha=0.3)
    a1.set_title("Гранулометрический состав")
    a1.set_xlabel("размер куска, мм")
    a1.set_ylabel("проход, % по массе")
    a1.legend(fontsize=7)
    x = np.arange(len(done))
    x50 = [p.result.stats["x50_mm"] for p in done]
    x80 = [p.result.stats["x80_mm"] for p in done]
    a2.bar(x, x50, 0.6, color="#4c78a8", label="X50")
    a2.plot(x, x80, "o-", color="#e45756", label="X80")
    a2.set_xticks(x, [str(i + 1) for i in x])
    a2.set_xlabel("фото №")
    a2.set_ylabel("размер куска, мм")
    a2.set_title("Кусковатость по фото")
    a2.grid(True, axis="y", alpha=0.3)
    a2.legend()
    b = io.BytesIO()
    fig.savefig(b, format="png", dpi=130)
    return b.getvalue()


@app.get("/api/examples")
def examples():
    d = Path(__file__).resolve().parents[1] / "primery"
    return [f.name for f in sorted(d.glob("*.jpg"))] if d.is_dir() else []


@app.get("/api/examples/{name}")
def example(name: str):
    d = Path(__file__).resolve().parents[1] / "primery"
    f = d / Path(name).name
    if not f.is_file():
        raise HTTPException(404)
    return FileResponse(f)


@app.get("/api/health")
def health():
    return {"ok": True, "workers": WORKERS, "photos": len(store.items)}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
