"""Сегментация кусков породы.

Простые контуры по порогу на развале породы не работают: куски соприкасаются,
и один контур охватывает целую группу. Поэтому используется маркерный
водораздел (watershed) — классический метод систем фотопланиметрии
(WipFrag, Split-Desktop):

1. Подавление шума сжатия (двусторонний фильтр) и выравнивание контраста (CLAHE).
2. Карта границ = тени-зазоры между кусками (морфологический black-hat на
   двух масштабах) + перепады яркости между гранями (градиент).
3. «Тело» кусков — пиксели со слабой границей; преобразование расстояния
   внутри них и h-максимумы дают по одному маркеру на кусок, что разделяет
   слипшиеся куски.
4. Водораздел от маркеров, затем отбраковка: слишком мелкие области
   (неразрешимая мелочь), рыхлые/вытянутые (ошибки), обрезанные краем ROI.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import cv2
import numpy as np
from skimage import graph
from skimage.feature import peak_local_max
from skimage.measure import regionprops
from skimage.segmentation import watershed

from .config import SegmentationConfig
from .roi import Roi


@dataclass
class Fragment:
    cx: float           # центр, px исходного кадра
    cy: float
    area_px: float      # px исходного кадра
    d_eq_mm: float      # диаметр равновеликого круга
    feret_max_mm: float
    minor_mm: float     # малая ось эквивалентного эллипса (≈ размер прохода через сито)
    major_mm: float
    label: int = 0      # номер области на карте меток Segmentation.labels


@dataclass
class Segmentation:
    labels: np.ndarray          # метки в координатах ROI (апскейл снят)
    fragments: list
    roi_area_px: float
    resolved_area_px: float     # площадь, занятая принятыми кусками
    fines_area_px: float        # площадь мелочи ниже разрешения
    rejected: dict = None       # сколько областей отбраковано и почему


def _norm(a: np.ndarray, mask: np.ndarray) -> np.ndarray:
    v = a[mask]
    lo, hi = np.percentile(v, 1), np.percentile(v, 99)
    return np.clip((a - lo) / (hi - lo + 1e-6), 0, 1).astype(np.float32)


def boundary_map(gray: np.ndarray, mask: np.ndarray, cfg: SegmentationConfig, u: float):
    """Карта границ кусков 0..~2: градиент яркости + тёмные зазоры (black-hat).

    Возвращает (локально нормированную — для водораздела и слияния,
    абсолютную — для проверки, что контур куска действительно виден)."""
    g = cv2.bilateralFilter(gray, int(4 * u) | 1, cfg.denoise_sigma_color, 2.5 * u)
    g = cv2.createCLAHE(clipLimit=cfg.clahe_clip, tileGridSize=(8, 8)).apply(g).astype(np.float32)
    sm = cv2.GaussianBlur(g, (0, 0), 0.6 * u)
    gx = cv2.Scharr(sm, cv2.CV_32F, 1, 0)
    gy = cv2.Scharr(sm, cv2.CV_32F, 0, 1)
    grad = np.sqrt(gx * gx + gy * gy)
    k = int(round(cfg.blackhat_radius_px * u))
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))
    bh = cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, ker)
    B = cfg.edge_weight * _norm(grad, mask) + _norm(bh, mask)
    B = cv2.GaussianBlur(B, (0, 0), 0.5 * u)
    B_abs = B
    if cfg.local_norm_px > 0:
        # локальная нормировка: в тени и в бликах границы слабее по абсолюту,
        # но так же заметны относительно окружения
        sig = cfg.local_norm_px * u
        loc = cv2.GaussianBlur(B, (0, 0), sig)
        ref = float(np.median(B[mask]))
        B = B * (ref / (0.5 * loc + 0.5 * ref))
    return B, B_abs


def _merge_regions(labels, B, gray, thr_b, thr_i, min_solidity):
    """Жадное слияние соседних областей по возрастанию силы общей границы.

    Слияние разрешено, только если граница слабая, средняя яркость близка и
    объединение остаётся выпуклым (кусок породы — выпуклое тело). Последнее
    условие не даёт «протечь» через разрыв в контуре крупного куска и
    склеить его с окружающей мелочью цепочкой слияний.
    """
    rag = graph.rag_boundary(labels, B)
    n = int(labels.max()) + 1
    area = np.bincount(labels.ravel(), minlength=n).astype(np.float64)
    ssum = np.bincount(labels.ravel(), weights=gray.ravel(), minlength=n)
    parent = np.arange(n)
    hulls: dict[int, np.ndarray] = {}
    for rp in regionprops(labels):
        r0, c0 = rp.bbox[0], rp.bbox[1]
        cnts, _ = cv2.findContours(rp.image.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        pts = np.vstack([c.reshape(-1, 2) for c in cnts]) + [c0, r0]
        hulls[rp.label] = cv2.convexHull(pts.astype(np.int32)).reshape(-1, 2)

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    edges = sorted((d["weight"], a, b) for a, b, d in rag.edges(data=True) if a and b)
    for wgt, a, b in edges:
        if wgt >= thr_b:
            break
        ra, rb = find(a), find(b)
        if ra == rb:
            continue
        ma, mb = ssum[ra] / max(area[ra], 1), ssum[rb] / max(area[rb], 1)
        if abs(ma - mb) > thr_i:
            continue
        hull = cv2.convexHull(np.vstack([hulls[ra], hulls[rb]]).astype(np.int32)).reshape(-1, 2)
        # площадь пиксельной области больше площади многоугольника по центрам
        # граничных пикселей примерно на полпериметра — учитываем это
        ha = cv2.contourArea(hull) + 0.5 * cv2.arcLength(hull.reshape(-1, 1, 2), True) + 1
        if (area[ra] + area[rb]) / ha < min_solidity:
            continue
        parent[rb] = ra
        area[ra] += area[rb]
        ssum[ra] += ssum[rb]
        hulls[ra] = hull
    roots = np.array([find(i) for i in range(n)])
    return roots[labels]


def segment(crop_bgr: np.ndarray, roi: Roi, cfg: SegmentationConfig, debug: dict | None = None) -> Segmentation:
    u = cfg.upscale
    mask0 = roi.mask
    gray0 = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2LAB)[:, :, 0]
    gray = cv2.resize(gray0, None, fx=u, fy=u, interpolation=cv2.INTER_CUBIC)
    mask = cv2.resize(mask0.astype(np.uint8), (gray.shape[1], gray.shape[0]),
                      interpolation=cv2.INTER_NEAREST).astype(bool)
    B, B_abs = boundary_map(gray, mask, cfg, u)
    gray_s = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 0.7 * u)

    # 1) пересегментация: водораздел по карте границ от всех локальных минимумов
    md = max(2, int(round(cfg.min_diameter_px * u / 3)))
    pk = peak_local_max(-B, min_distance=md, labels=mask.astype(np.int32), exclude_border=False)
    markers = np.zeros(B.shape, np.int32)
    markers[tuple(pk.T)] = np.arange(1, len(pk) + 1)
    labels = watershed(B, markers, mask=mask)
    # 2) слияние соседних областей одного куска: общая граница слабая
    #    И средняя яркость близка (кусок и тёмный зазор рядом не сливаются)
    if labels.max() > 1:
        labels = _merge_regions(labels, B, gray_s, cfg.merge_threshold, cfg.merge_intensity,
                                cfg.merge_min_solidity)
        labels[~mask] = 0

    roi_area = float(mask.sum())
    min_area_up = np.pi * (cfg.min_diameter_px * u / 2) ** 2
    max_area = cfg.max_area_frac * roi_area
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    border = ~cv2.erode(mask.astype(np.uint8), ker, iterations=2).astype(bool)
    x0, y0 = roi.box[0], roi.box[1]

    frags = []
    keep = np.zeros(labels.max() + 1, bool)
    fines_area = 0.0
    rej = dict.fromkeys(["fines", "too_big", "border", "solidity", "elongated",
                         "weak_contour", "textured_inside", "open_contour"], 0)
    why = {}
    for rp in regionprops(labels):
        a = rp.area
        if a < min_area_up:
            fines_area += a
            rej["fines"] += 1
            why[rp.label] = "fines"
            continue
        if a > max_area:
            rej["too_big"] += 1
            why[rp.label] = "too_big"
            continue
        r0, c0, r1, c1 = rp.bbox
        if border[r0:r1, c0:c1][rp.image].any():
            rej["border"] += 1
            why[rp.label] = "border"
            continue
        # крупные области должны быть выпуклыми: рваный контур = слияние нескольких кусков
        min_sol = cfg.min_solidity_large if a > 16 * min_area_up else cfg.min_solidity
        if rp.solidity < min_sol:
            rej["solidity"] += 1
            why[rp.label] = "solidity"
            continue
        maj, mnr = rp.axis_major_length, max(rp.axis_minor_length, 1.0)
        if maj / mnr > cfg.max_elongation:
            rej["elongated"] += 1
            why[rp.label] = "elongated"
            continue
        # 3) проверка, что это действительно отдельный кусок: контур чётче, чем
        #    внутренность (иначе это слившаяся группа или размытая мелочь)
        R0, C0 = max(0, r0 - 2), max(0, c0 - 2)
        sub = labels[R0:r1 + 2, C0:c1 + 2] == rp.label
        ring = cv2.dilate(sub.astype(np.uint8), ker, iterations=2).astype(bool) & ~sub
        inner = cv2.erode(sub.astype(np.uint8), ker, iterations=2).astype(bool)
        Bs = B_abs[R0:r1 + 2, C0:c1 + 2]
        if ring.sum() == 0 or inner.sum() == 0:
            continue
        b_ring = float(Bs[ring].mean())
        b_in = float(Bs[inner].mean())
        # доля контура, где граница действительно видна (замкнутость контура)
        closed = float((Bs[ring] > cfg.edge_on).mean())
        if b_ring < cfg.min_boundary:
            rej["weak_contour"] += 1
            why[rp.label] = "weak_contour"
            continue
        if b_in > cfg.max_inner_ratio * b_ring:
            rej["textured_inside"] += 1
            why[rp.label] = "textured_inside"
            continue
        if closed < cfg.min_closed:
            rej["open_contour"] += 1
            why[rp.label] = "open_contour"
            continue
        cy_up, cx_up = rp.centroid
        cy0 = min(int(cy_up / u), roi.mm_per_px.shape[0] - 1)
        cx0 = min(int(cx_up / u), roi.mm_per_px.shape[1] - 1)
        s = float(roi.mm_per_px[cy0, cx0]) / u   # мм на px апскейла
        frags.append(Fragment(
            cx=x0 + cx_up / u, cy=y0 + cy_up / u, area_px=a / u / u,
            d_eq_mm=float(rp.equivalent_diameter_area) * s,
            feret_max_mm=float(rp.feret_diameter_max) * s,
            minor_mm=float(mnr) * s, major_mm=float(maj) * s, label=int(rp.label),
        ))
        keep[rp.label] = True
    if debug is not None:
        debug.update(labels=labels.copy(), why=why, B=B, u=u)
    labels = np.where(keep[labels], labels, 0)
    labels0 = cv2.resize(labels.astype(np.int32), (mask0.shape[1], mask0.shape[0]),
                         interpolation=cv2.INTER_NEAREST)
    resolved = float(sum(f.area_px for f in frags))
    return Segmentation(labels0, frags, roi_area / u / u, resolved, fines_area / u / u, rej)


def segment_multiscale(crop_bgr: np.ndarray, roi: Roi, cfg: SegmentationConfig) -> Segmentation:
    """Двухмасштабная сегментация.

    На мелком масштабе хорошо выделяются мелкие и средние куски, но крупный
    кусок с собственной текстурой (рёбра граней, трещины) дробится на части.
    На грубом масштабе (изображение уменьшено в cfg.coarse_factor раз) внутренние
    рёбра сглаживаются, а широкие тени между крупными кусками остаются.
    Крупный кусок, прошедший на грубом масштабе все проверки (замкнутый
    выпуклый контур, гладкая внутренность), заменяет мелкие обрывки внутри себя.
    """
    fine = segment(crop_bgr, roi, cfg)
    c = cfg.coarse_factor
    if c <= 1:
        return fine
    h, w = roi.mask.shape
    hs, ws = max(8, int(round(h / c))), max(8, int(round(w / c)))
    small = cv2.resize(crop_bgr, (ws, hs), interpolation=cv2.INTER_AREA)
    m_s = cv2.resize(roi.mask.astype(np.uint8), (ws, hs), interpolation=cv2.INTER_NEAREST).astype(bool)
    mm_s = cv2.resize(roi.mm_per_px, (ws, hs), interpolation=cv2.INTER_LINEAR) * (w / ws)
    roi_s = Roi(roi.kind, (0, 0, ws, hs), m_s, mm_s.astype(np.float32), roi.ref_px / c)
    # на грубом масштабе требования к контуру строже: крупный кусок должен
    # быть очерчен почти целиком, иначе это группа мелких
    cfg_c = replace(cfg, min_diameter_px=cfg.coarse_min_diameter_px,
                    min_closed=cfg.coarse_min_closed, max_inner_ratio=cfg.coarse_max_inner_ratio)
    coarse = segment(small, roi_s, cfg_c)
    if not coarse.fragments:
        return fine

    lab_c = cv2.resize(coarse.labels.astype(np.int32), (w, h), interpolation=cv2.INTER_NEAREST)
    x0, y0 = roi.box[0], roi.box[1]
    sx, sy = w / ws, h / hs
    big = []
    for f in coarse.fragments:
        lc = lab_c[min(h - 1, int(f.cy * sy)), min(w - 1, int(f.cx * sx))]
        big.append((f, lc))
    taken = np.zeros(int(lab_c.max()) + 1, bool)
    off = int(fine.labels.max()) + 1
    # площадь, уже разрешённая мелким масштабом внутри каждой крупной области
    fine_cov = np.bincount(lab_c[fine.labels > 0].ravel(), minlength=taken.size).astype(float)
    area_c = np.bincount(lab_c.ravel(), minlength=taken.size).astype(float)
    out = []
    for f, lc in big:
        # если мелкий масштаб уже уверенно разделил область на куски — это группа,
        # а не один крупный кусок; крупный кусок принимаем, только где мелкий не справился
        if lc <= 0 or fine_cov[lc] > cfg.coarse_max_fine_cover * area_c[lc]:
            continue
        taken[lc] = True
        out.append(Fragment(cx=x0 + f.cx * sx, cy=y0 + f.cy * sy, area_px=f.area_px * sx * sy,
                            d_eq_mm=f.d_eq_mm, feret_max_mm=f.feret_max_mm,
                            minor_mm=f.minor_mm, major_mm=f.major_mm, label=int(lc + off)))
    kept_fine = np.zeros(int(fine.labels.max()) + 1, bool)
    for f in fine.fragments:
        lc = lab_c[min(h - 1, int(f.cy - y0)), min(w - 1, int(f.cx - x0))]
        if lc > 0 and taken[lc]:
            continue
        out.append(f)
        kept_fine[f.label] = True
    labels = np.where(kept_fine[fine.labels], fine.labels, 0)
    sel = taken[lab_c]
    labels[sel] = lab_c[sel] + off
    resolved = float(sum(f.area_px for f in out))
    rej = {k: fine.rejected.get(k, 0) for k in fine.rejected}
    rej["replaced_by_coarse"] = len(fine.fragments) - int(kept_fine.sum())
    return Segmentation(labels, out, fine.roi_area_px, resolved, fine.fines_area_px, rej)


def overlay(crop_bgr: np.ndarray, seg: Segmentation, roi: Roi) -> np.ndarray:
    out = crop_bgr.copy()
    lab = seg.labels
    rng = np.random.default_rng(1)
    colors = rng.integers(60, 255, (lab.max() + 1, 3)).astype(np.uint8)
    colors[0] = 0
    col = colors[lab]
    m = lab > 0
    out[m] = (0.55 * out[m] + 0.45 * col[m]).astype(np.uint8)
    edges = (cv2.morphologyEx(lab.astype(np.float32), cv2.MORPH_GRADIENT, np.ones((3, 3))) > 0) & m
    out[edges] = (0, 255, 255)
    cnts, _ = cv2.findContours(roi.mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(out, cnts, -1, (255, 0, 255), 1)
    return out
