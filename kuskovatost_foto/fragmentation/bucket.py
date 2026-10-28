"""Поиск ковша в кадре и измерение его ширины (эталон px -> мм).

Камера закреплена на стреле и смотрит вниз вдоль подъёмных канатов, поэтому
ковш всегда находится примерно по центру кадра по горизонтали, а при подъёме
и опускании меняются только его положение по вертикали и видимый размер.

1. Грубое положение: многомасштабное сопоставление с шаблонами корпуса блока
   ковша («балка» с канатами) — ближний и дальний ракурсы.
2. Точный эталон: наружные боковые стенки ковша ищутся как две длинные почти
   вертикальные прямые над блоком. Расстояние между ними на каждой строке
   изображения даёт масштаб мм/px именно на той глубине, где лежит порода,
   что автоматически учитывает перспективу.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import DetectorConfig
from .imio import imread

ASSETS = Path(__file__).parent / "assets"


@dataclass
class Detection:
    score: float
    cx: float          # центр корпуса блока, px исходного кадра
    top: float         # верхняя кромка корпуса блока, px
    housing_w: float   # видимая ширина корпуса блока, px
    template: str

    def as_dict(self) -> dict:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


@dataclass
class Walls:
    """Наружные стенки ковша: x = x0 + k * (y - y_ref)."""
    left_x0: float
    left_k: float
    right_x0: float
    right_k: float
    y_ref: float
    y_top: float       # верх полосы, где стенки измерены
    quality: float     # контраст стенок относительно фона (>= ~1.5 — надёжно)

    def x_left(self, y):
        return self.left_x0 + self.left_k * (np.asarray(y, float) - self.y_ref)

    def x_right(self, y):
        return self.right_x0 + self.right_k * (np.asarray(y, float) - self.y_ref)

    def width(self, y):
        return self.x_right(y) - self.x_left(y)


class Template:
    def __init__(self, name: str, image: np.ndarray, hx0: float, hx1: float, htop: float):
        self.name = name
        self.gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        self.hx0, self.hx1, self.htop = hx0, hx1, htop

    @property
    def housing_w(self) -> float:
        return self.hx1 - self.hx0


def load_templates(assets: Path = ASSETS) -> list[Template]:
    meta = json.loads((assets / "templates.json").read_text(encoding="utf-8"))
    out = []
    for m in meta:
        img = imread(assets / m["file"], cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise FileNotFoundError(assets / m["file"])
        out.append(Template(m["name"], img, m["housing_x0"], m["housing_x1"], m["housing_top"]))
    return out


class BucketDetector:
    # Допустимая видимая ширина корпуса блока, px кадра 1920 (масштабируется по ширине кадра)
    MIN_HOUSING_W = 170.0
    MAX_HOUSING_W = 820.0

    def __init__(self, cfg: DetectorConfig | None = None, templates: list[Template] | None = None):
        self.cfg = cfg or DetectorConfig()
        self.templates = templates or load_templates()
        # Ковш висит в плоскости стрелы, поэтому его центр по горизонтали почти
        # неизменен. Положение выучивается по уверенным находкам, после чего
        # поиск ограничивается полосой вокруг него (отсекает ложные совпадения).
        self._cx_hist: list[float] = []
        self.cx_prior: float | None = None

    # ------------------------------------------------------------------ locate
    def locate(self, frame: np.ndarray, prev: Detection | None = None) -> Detection | None:
        """Находит блок ковша. prev — находка на предыдущем кадре (до ~0.5 с назад):
        скачок видимого размера штрафуется, что не даёт перескочить на похожий
        объект (кузов самосвала, борт забоя), когда ковш частично перекрыт."""
        gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        H, W = gray.shape
        f = self.cfg.work_width / W
        small = cv2.resize(gray, (self.cfg.work_width, int(round(H * f))), interpolation=cv2.INTER_AREA)
        k = W / 1920.0
        band = None
        if self.cx_prior is not None:
            band = (self.cx_prior - self.cfg.cx_band * W, self.cx_prior + self.cfg.cx_band * W)
        best = None
        for t in self.templates:
            s_lo = self.MIN_HOUSING_W * k / t.housing_w
            s_hi = self.MAX_HOUSING_W * k / t.housing_w
            scales = np.geomspace(s_lo, s_hi, self.cfg.n_scales)
            for si, s in enumerate(scales):
                off = s * (t.hx0 + t.hx1) / 2 * f   # смещение центра блока от левого края шаблона
                cb = None if band is None else (band[0] * f - off, band[1] * f - off)
                m, loc = _match(small, t.gray, s * f, cb)
                if m is None:
                    continue
                obj = m
                if prev is not None:
                    obj -= self.cfg.scale_jump_penalty * abs(np.log(s * t.housing_w / prev.housing_w))
                if best is None or obj > best[0]:
                    best = (obj, m, loc, s, t, si == 0)
        if best is None:
            return None
        _, m, loc, s, t, at_min = best
        if at_min:
            # совпадение на нижней границе масштабов: настоящий ковш так мал не бывает
            return None
        # уточнение на удвоенном разрешении
        f2 = min(1.0, 2 * f)
        mid = cv2.resize(gray, None, fx=f2, fy=f2, interpolation=cv2.INTER_AREA) if f2 < 1 else gray
        x0, y0 = loc[0] / f * f2, loc[1] / f * f2
        tw, th = t.gray.shape[1] * s * f2, t.gray.shape[0] * s * f2
        pad = 0.15 * max(tw, th) + 8
        X0, Y0 = int(max(0, x0 - pad)), int(max(0, y0 - pad))
        X1, Y1 = int(min(mid.shape[1], x0 + tw + pad)), int(min(mid.shape[0], y0 + th + pad))
        roi = mid[Y0:Y1, X0:X1]
        fine = None
        for s2 in s * np.linspace(0.93, 1.07, 15):
            mm, ll = _match(roi, t.gray, s2 * f2)
            if mm is not None and (fine is None or mm > fine[0]):
                fine = (mm, ll, s2)
        if fine is not None:
            m, ll, s = fine
            x0, y0 = X0 + ll[0], Y0 + ll[1]
        if m < self.cfg.min_score:
            return None
        x0, y0 = x0 / f2, y0 / f2
        det = Detection(
            score=float(m),
            cx=float(x0 + s * (t.hx0 + t.hx1) / 2),
            top=float(y0 + s * t.htop),
            housing_w=float(s * t.housing_w),
            template=t.name,
        )
        if self.cx_prior is None and det.score >= 0.6:
            self._cx_hist.append(det.cx)
            if len(self._cx_hist) >= 15:
                self.cx_prior = float(np.median(self._cx_hist))
        return det

    # ------------------------------------------------------------------- walls
    def find_walls(self, frame: np.ndarray, det: Detection, band=(0.5, 0.03)) -> Walls | None:
        """Ищет наружные боковые стенки ковша в полосе над корпусом блока.

        band — (верх, низ) полосы в долях ширины корпуса над его верхней кромкой.
        """
        gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        H, W = gray.shape
        wh = det.housing_w
        y0 = int(max(0, det.top - band[0] * wh))
        y1 = int(max(0, det.top - band[1] * wh))
        if y1 - y0 < 15:
            return None
        xa = int(max(0, det.cx - 1.0 * wh))
        xb = int(min(W, det.cx + 1.0 * wh))
        crop = gray[y0:y1, xa:xb].astype(np.float32)
        crop = cv2.GaussianBlur(crop, (0, 0), 1.2)
        gx = np.abs(cv2.Sobel(crop, cv2.CV_32F, 1, 0, ksize=3))
        gy = np.abs(cv2.Sobel(crop, cv2.CV_32F, 0, 1, ksize=3))
        # вертикальная кромка: сильный горизонтальный градиент при слабом вертикальном
        edge = gx * (gx > 1.5 * gy)
        bg = float(np.median(gx)) + 1e-3
        ys = np.arange(y1 - y0, dtype=np.float32) - (y1 - y0 - 1)  # 0 на нижней строке
        cols = np.arange(edge.shape[1], dtype=np.float32)
        rows = np.arange(edge.shape[0])[:, None]

        def scan(lo: float, hi: float):
            lo_i, hi_i = int(max(0, lo - xa)), int(min(edge.shape[1] - 1, hi - xa))
            cands = []
            for k in np.linspace(-0.3, 0.3, 31):
                xs = cols[None, lo_i:hi_i] + k * ys[:, None]
                xi = np.clip(np.rint(xs).astype(int), 0, edge.shape[1] - 1)
                vals = edge[rows, xi]
                # стенка — длинная: берём 35-й перцентиль вдоль линии, а не среднее
                sc = np.percentile(vals, 35, axis=0)
                sc = np.maximum(sc, np.percentile(np.maximum(vals, np.roll(vals, 1, axis=1)), 35, axis=0))
                j = int(np.argmax(sc))
                cands.append((float(sc[j]), xa + lo_i + j, float(k)))
                for jj in np.argsort(sc)[::-1][1:6]:
                    cands.append((float(sc[jj]), xa + lo_i + int(jj), float(k)))
            cands.sort(reverse=True)
            out = []
            for c in cands:
                if all(abs(c[1] - o[1]) > 6 for o in out):
                    out.append(c)
                if len(out) >= 6:
                    break
            return out

        left = scan(det.cx - 0.95 * wh, det.cx - 0.30 * wh)
        right = scan(det.cx + 0.30 * wh, det.cx + 0.95 * wh)
        best = None
        for sl, xl, kl in left:
            for sr, xr, kr in right:
                ratio = (xr - xl) / wh
                if not 0.9 <= ratio <= 1.6:
                    continue
                if abs(kl + kr) > 0.25:  # стенки сходятся симметрично (перспектива)
                    continue
                sc = min(sl, sr)
                if best is None or sc > best[0]:
                    best = (sc, xl, kl, xr, kr)
        if best is None:
            return None
        sc, xl, kl, xr, kr = best
        return Walls(left_x0=float(xl), left_k=kl, right_x0=float(xr), right_k=kr,
                     y_ref=float(y1 - 1), y_top=float(y0), quality=float(sc / bg))


def _match(img: np.ndarray, tpl: np.ndarray, s: float, x_band=None):
    """Нормированная корреляция; x_band — допустимый диапазон левого края шаблона."""
    tw, th = int(round(tpl.shape[1] * s)), int(round(tpl.shape[0] * s))
    if tw < 12 or th < 8 or tw >= img.shape[1] or th >= img.shape[0]:
        return None, None
    t = cv2.resize(tpl, (tw, th), interpolation=cv2.INTER_AREA)
    r = cv2.matchTemplate(img, t, cv2.TM_CCOEFF_NORMED)
    if x_band is not None:
        a, b = int(max(0, np.floor(x_band[0]))), int(min(r.shape[1], np.ceil(x_band[1]) + 1))
        if b <= a:
            return None, None
        r = r[:, a:b]
        _, m, _, loc = cv2.minMaxLoc(r)
        return float(m), (loc[0] + a, loc[1])
    _, m, _, loc = cv2.minMaxLoc(r)
    return float(m), loc
