"""Области интереса (ROI) и карта масштаба мм/px, построенные от найденного ковша."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .bucket import Detection, Walls


@dataclass
class Roi:
    kind: str                 # "load" — порода в поднятом ковше, "face" — забой перед зубьями
    box: tuple                # x0, y0, x1, y1 в исходном кадре
    mask: np.ndarray          # bool, размер box
    mm_per_px: np.ndarray     # float32, размер box: масштаб в каждой точке
    ref_px: float             # ширина эталона в px (для отчёта)

    def crop(self, frame: np.ndarray) -> np.ndarray:
        x0, y0, x1, y1 = self.box
        return frame[y0:y1, x0:x1]

    @property
    def mean_mm_per_px(self) -> float:
        return float(self.mm_per_px[self.mask].mean())


def load_roi(frame_shape, det: Detection, walls: Walls, bucket_width_mm: float,
             rows=(0.40, 0.07), inset=0.06) -> Roi | None:
    """Порода в поднятом ковше: между стенками, от блока до режущей кромки.

    Ширина ковша в px меняется по строкам (перспектива) — масштаб считается
    для каждой строки по расстоянию между стенками.
    """
    H, W = frame_shape[:2]
    wh = det.housing_w
    y0 = int(max(0, det.top - rows[0] * wh))
    y1 = int(min(H, det.top - rows[1] * wh))
    if y1 - y0 < 20:
        return None
    ys = np.arange(y0, y1, dtype=np.float32)
    xl = walls.x_left(ys)
    xr = walls.x_right(ys)
    w = xr - xl
    xl_in = xl + inset * w
    xr_in = xr - inset * w
    x0 = int(max(0, np.floor(xl_in.min())))
    x1 = int(min(W, np.ceil(xr_in.max())))
    xs = np.arange(x0, x1, dtype=np.float32)[None, :]
    mask = (xs >= xl_in[:, None]) & (xs <= xr_in[:, None])
    mmpp = np.repeat((bucket_width_mm / w)[:, None], x1 - x0, axis=1).astype(np.float32)
    return Roi("load", (x0, y0, x1, y1), mask, mmpp, float(np.median(w)))


def face_roi(frame_shape, det: Detection, bucket_px: float, bucket_width_mm: float,
             gap=0.55, height=0.9, half_width=0.55, px_at_row=None) -> Roi | None:
    """Забой непосредственно перед зубьями ковша в момент начала черпания.

    gap — отступ над корпусом блока (в его ширинах), чтобы не захватить зубья;
    height/half_width — размеры области в ширинах ковша.
    px_at_row(y) — видимая ширина ковша, когда его зубья проходили строку y
    при черпании (ковш как движущаяся мерная рейка). Учитывает, что дальний
    край забоя ближе/дальше от камеры, чем ковш в момент начала черпания.
    """
    H, W = frame_shape[:2]
    y1 = int(det.top - gap * det.housing_w)
    y0 = int(max(0, y1 - height * bucket_px))
    x0 = int(max(0, det.cx - half_width * bucket_px))
    x1 = int(min(W, det.cx + half_width * bucket_px))
    if y1 - y0 < 30 or x1 - x0 < 30:
        return None
    mask = np.ones((y1 - y0, x1 - x0), bool)
    if px_at_row is None:
        mmpp = np.full(mask.shape, bucket_width_mm / bucket_px, np.float32)
    else:
        rows = np.asarray(px_at_row(np.arange(y0, y1, dtype=np.float32)), np.float32)
        mmpp = np.repeat((bucket_width_mm / rows)[:, None], x1 - x0, axis=1).astype(np.float32)
    return Roi("face", (x0, y0, x1, y1), mask, mmpp, float(bucket_px))


def draw_roi(img: np.ndarray, roi: Roi, color=(0, 255, 255)) -> None:
    x0, y0, _, _ = roi.box
    m = roi.mask.astype(np.uint8)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts:
        c = c + np.array([[[x0, y0]]])
        cv2.drawContours(img, [c], -1, color, 2)
