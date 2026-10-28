"""Кусковатость породы по одной фотографии.

1. Фото приводится к рабочему размеру (не шире 1920 px — как кадр видео, под
   который настроены поиск ковша и сегментация).
2. Автоматически ищется ковш (шаблоны корпуса блока) и его боковые стенки.
   Если ковш поднят и в нём видна порода, область анализа — порода между
   стенками, масштаб мм/px — по ширине ковша на каждой строке.
3. Если ковш не найден (или пользователь хочет другую область), область
   задаётся контуром, обведённым вручную. Масштаб — по ковшу, если он есть
   в кадре, иначе по эталону (линия известной длины) или введённому числу мм/px.
4. Сегментация кусков и показатели — те же, что в основной программе.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from fragmentation.bucket import BucketDetector, Detection, Walls
from fragmentation.config import Config
from fragmentation.metrics import size_stats
from fragmentation.roi import Roi, load_roi
from fragmentation.segment import Segmentation, overlay, segment_multiscale

WORK_WIDTH = 1920          # рабочая ширина фото, px
MAX_ROI_AREA = 650_000     # больше — область уменьшается перед сегментацией (время счёта)
# Порог совпадения с шаблоном ковша для отдельного фото. В видео ложные
# совпадения отсекаются по соседним кадрам и выученному положению ковша, а на
# фото этого нет: на снимках развала без ковша совпадение доходит до ~0.46.
MIN_SCORE = 0.48
SIEVES = (50, 100, 150, 200, 300, 500, 1000)


def to_work(img: np.ndarray) -> tuple[np.ndarray, float]:
    """Фото -> рабочий размер. Возвращает (картинка, во сколько раз уменьшена)."""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    h, w = img.shape[:2]
    if w <= WORK_WIDTH:
        return img, 1.0
    f = WORK_WIDTH / w
    return cv2.resize(img, (WORK_WIDTH, int(round(h * f))), interpolation=cv2.INTER_AREA), f


def texture_energy(gray: np.ndarray, scale: float) -> float:
    """Текстура содержимого ковша: у пустого ковша (гладкое днище) мала."""
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), max(0.5, 0.8 * scale))
    lap = np.abs(cv2.Laplacian(g, cv2.CV_32F))
    return float(np.median(cv2.GaussianBlur(lap, (0, 0), max(1.0, 4 * scale))))


@dataclass
class BucketInfo:
    det: Detection | None = None
    walls: Walls | None = None
    roi: Roi | None = None          # порода в ковше, если её можно мерить
    message: str = ""               # почему ковш не подходит для замера
    fill: float = 0.0

    @property
    def found(self) -> bool:
        return self.det is not None

    def mm_per_px(self, bucket_width_mm: float) -> float | None:
        """Приблизительный масштаб на глубине ковша (для ручного контура)."""
        if self.det is None:
            return None
        if self.walls is not None:
            w = float(self.walls.width(self.walls.y_ref))
            if 0.95 <= w / self.det.housing_w <= 1.35:
                return bucket_width_mm / w
        # видимая ширина ковша ≈ 1.1–1.2 ширины корпуса блока (зависит от ракурса)
        return bucket_width_mm / (1.15 * self.det.housing_w)


def find_bucket(img: np.ndarray, cfg: Config, detector: BucketDetector | None = None) -> BucketInfo:
    det = detector or BucketDetector(cfg.detector)
    det.cx_prior = None                         # фото независимы: без «памяти» видео
    d = det.locate(img)
    if d is None or d.score < MIN_SCORE:
        return BucketInfo(message="Ковш не найден")
    info = BucketInfo(det=d)
    walls = det.find_walls(img, d)
    info.walls = walls
    H, W = img.shape[:2]
    k = W / 1920.0
    if d.housing_w < 330 * k:
        info.message = "Ковш внизу (у забоя) — порода в ковше не видна"
        return info
    if walls is None or not 0.95 <= walls.width(walls.y_ref) / d.housing_w <= 1.35:
        info.message = "Не найдены стенки ковша"
        return info
    roi = load_roi(img.shape, d, walls, cfg.scale.bucket_width_mm)
    if roi is None or roi.mask.sum() < 3000 * k * k:
        info.message = "Порода в ковше за краем кадра"
        return info
    gray = cv2.cvtColor(roi.crop(img), cv2.COLOR_BGR2GRAY)
    info.fill = texture_energy(gray, d.housing_w / 440.0)
    if info.fill < cfg.phases.fill_min_energy:
        info.message = "Ковш пустой или почти пустой"
        return info
    info.roi = roi
    return info


def polygon_roi(shape, poly, mm_per_px: float) -> Roi | None:
    """Область анализа по контуру, обведённому вручную (координаты рабочего фото)."""
    H, W = shape[:2]
    pts = np.asarray(poly, np.float32).reshape(-1, 2)
    if len(pts) < 3:
        return None
    pts[:, 0] = np.clip(pts[:, 0], 0, W - 1)
    pts[:, 1] = np.clip(pts[:, 1], 0, H - 1)
    x0, y0 = np.floor(pts.min(0)).astype(int)
    x1, y1 = np.ceil(pts.max(0)).astype(int) + 1
    if x1 - x0 < 20 or y1 - y0 < 20:
        return None
    mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
    cv2.fillPoly(mask, [np.rint(pts - [x0, y0]).astype(np.int32)], 1)
    if mask.sum() < 400:
        return None
    mmpp = np.full(mask.shape, mm_per_px, np.float32)
    return Roi("manual", (int(x0), int(y0), int(x1), int(y1)), mask.astype(bool), mmpp, 0.0)


def _shrink(crop: np.ndarray, roi: Roi, f: float) -> tuple[np.ndarray, Roi]:
    h, w = roi.mask.shape
    ws, hs = max(16, int(round(w * f))), max(16, int(round(h * f)))
    c = cv2.resize(crop, (ws, hs), interpolation=cv2.INTER_AREA)
    m = cv2.resize(roi.mask.astype(np.uint8), (ws, hs), interpolation=cv2.INTER_NEAREST).astype(bool)
    mm = cv2.resize(roi.mm_per_px, (ws, hs), interpolation=cv2.INTER_LINEAR) * (w / ws)
    return c, Roi(roi.kind, (0, 0, ws, hs), m, mm.astype(np.float32), roi.ref_px * f)


@dataclass
class PhotoResult:
    name: str
    source: str                       # "ковш" / "контур"
    stats: dict
    roi: Roi
    seg: Segmentation
    overlay: np.ndarray               # рабочее фото с найденными кусками
    scale_note: str = ""
    classes: list = field(default_factory=list)
    d_mm: np.ndarray | None = None    # размеры выделенных кусков, мм
    fines_mask: np.ndarray | None = None
    fines_frac: float = 0.0           # доля мелочи по площади (мелочь / (мелочь + куски))
    unknown_pct: float = 0.0          # % области без кусков и без мелочи рядом с ними
    unresolved: float = 0.0
    mm_per_px: float = 0.0
    resolution_mm: float = 0.0
    oversize_mm: float = 1000.0
    sieves: list = field(default_factory=list)


def analyze(img: np.ndarray, roi: Roi, cfg: Config, name: str = "", source: str = "",
            detail: float = 1.0, scale_note: str = "") -> PhotoResult:
    """Сегментация кусков в области roi рабочего фото img и показатели кусковатости.

    detail — множитель разрешения сегментации: < 1 — для фото крупным планом,
    где куски большие (быстрее и меньше дробления крупных кусков по трещинам)."""
    crop = roi.crop(img)
    f = detail
    area = float(roi.mask.sum()) * f * f
    if area > MAX_ROI_AREA:
        f *= np.sqrt(MAX_ROI_AREA / area)
    if f < 0.999:
        c_s, roi_s = _shrink(crop, roi, f)
        seg_s = segment_multiscale(c_s, roi_s, cfg.segmentation)
        h, w = roi.mask.shape
        labels = cv2.resize(seg_s.labels.astype(np.int32), (w, h), interpolation=cv2.INTER_NEAREST)
        labels[~roi.mask] = 0
        sx, sy = w / roi_s.mask.shape[1], h / roi_s.mask.shape[0]
        for fr in seg_s.fragments:
            fr.cx, fr.cy, fr.area_px = roi.box[0] + fr.cx * sx, roi.box[1] + fr.cy * sy, fr.area_px * sx * sy
        seg = Segmentation(labels, seg_s.fragments, float(roi.mask.sum()),
                           seg_s.resolved_area_px * sx * sy, seg_s.fines_area_px * sx * sy, seg_s.rejected)
    else:
        seg = segment_multiscale(crop, roi, cfg.segmentation)
    unresolved = 1 - seg.resolved_area_px / max(1.0, seg.roi_area_px)
    fines, unknown = fines_mask(crop, roi, seg)
    res = PhotoResult(name, source, {}, roi, seg, img, scale_note)
    res.d_mm = np.array([fr.d_eq_mm for fr in seg.fragments], float)
    res.fines_mask = fines
    res.unknown_pct = 100 * float(unknown.sum()) / max(1.0, float(roi.mask.sum()))
    fa, ra = float(fines.sum()), float(seg.resolved_area_px)
    res.fines_frac = fa / (fa + ra) if fa + ra > 0 else 0.0
    res.unresolved = unresolved
    res.mm_per_px = roi.mean_mm_per_px
    res.resolution_mm = roi.mean_mm_per_px * cfg.segmentation.min_diameter_px / min(1.0, f)
    res.oversize_mm = cfg.report.oversize_mm
    res.sieves = sorted(set(cfg.report.sieve_sizes_mm) | set(SIEVES))
    finalize(res, use_fines=True)
    ov = img.copy()
    x0, y0, x1, y1 = roi.box
    ov[y0:y1, x0:x1] = overlay(crop, seg, roi)
    sub = ov[y0:y1, x0:x1]
    if fines.any():
        # мелочь — голубая штриховка: видно, что участок учтён, а не пропущен
        hatch = np.zeros(fines.shape, bool)
        yy, xx = np.mgrid[0:fines.shape[0], 0:fines.shape[1]]
        hatch[((xx + yy) % 7) < 2] = True
        tint = np.array(FINES_BGR, np.float32)
        sub[fines] = (0.65 * sub[fines] + 0.35 * tint).astype(np.uint8)
        sub[fines & hatch] = (0.3 * sub[fines & hatch] + 0.7 * tint).astype(np.uint8)
    if unknown.any():
        # не распознано (нет кусков поблизости) — серая штриховка, в расчёт не входит
        yy, xx = np.mgrid[0:unknown.shape[0], 0:unknown.shape[1]]
        hatch = unknown & ((((xx - yy) % 9) < 2))
        sub[hatch] = UNKNOWN_BGR
    cnts, _ = cv2.findContours(roi.mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(ov, [c + [[x0, y0]] for c in cnts], -1, (255, 0, 255), 3)
    res.overlay = ov
    return res


FINES_BGR = (255, 170, 60)       # голубой (BGR)
FINES_GGS_N = 0.6                # показатель Годэна–Шумана для распределения внутри мелочи
FINES_REACH = 4.0                # мелочь — не дальше стольких медианных диаметров куска от кусков
UNKNOWN_BGR = (150, 150, 150)


def fines_mask(crop: np.ndarray, roi: Roi, seg: Segmentation) -> tuple[np.ndarray, np.ndarray]:
    """Мелочь: часть области без выделенных кусков, кроме узких тёмных зазоров
    и теней между кусками (они не порода, а промежутки). Это материал мельче
    предела разрешения — по одной фотографии его поштучно не разделить."""
    m = roi.mask.copy()
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    taken = cv2.dilate((seg.labels > 0).astype(np.uint8), ker, iterations=1).astype(bool)
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)[:, :, 0]
    g = cv2.GaussianBlur(g, (0, 0), 0.8)
    bh = cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    gaps = bh > 18
    free = m & ~taken & ~gaps
    # убираем одиночные пиксели и узкие перемычки между кусками
    free = cv2.morphologyEx(free.astype(np.uint8), cv2.MORPH_OPEN, ker).astype(bool)
    # мелочь лежит между кусками; большой участок вдали от любых кусков может
    # быть и крупной глыбой, и не породой (борт, металл) — его не относим к мелочи
    if seg.fragments:
        d_px = np.median([2 * np.sqrt(fr.area_px / np.pi) for fr in seg.fragments])
        reach = max(15.0, FINES_REACH * d_px)
        dist = cv2.distanceTransform((~taken).astype(np.uint8), cv2.DIST_L2, 3)
        unknown = free & (dist > reach)
        free &= dist <= reach
    else:
        unknown, free = free, np.zeros_like(free)
    return free, unknown


def finalize(res: PhotoResult, use_fines: bool = True) -> None:
    """Показатели по кускам; с учётом мелочи — по принципу Делесса (доля площади
    сечения = доля объёма) мелочь даёт долю F массы мельче самого мелкого
    различимого куска, а распределение выделенных кусков масштабируется на 1 − F."""
    d = res.d_mm
    stats = size_stats(d, res.oversize_mm, res.sieves, res.unresolved)
    stats["mm_per_px"] = res.mm_per_px
    stats["resolution_mm"] = res.resolution_mm
    F = res.fines_frac if use_fines else 0.0
    stats["fines_pct"] = 100 * res.fines_frac
    stats["fines_used"] = bool(use_fines)
    stats["unknown_pct"] = res.unknown_pct
    if "x50_mm" in stats:
        ds = np.sort(d[np.isfinite(d) & (d > 0)])
        cum = np.cumsum(ds ** 3) / np.sum(ds ** 3)
        dmin = float(ds[0])
        stats["fines_below_mm"] = dmin
        xs = np.concatenate([[dmin], ds])
        ps = np.concatenate([[F], F + (1 - F) * cum])

        def passing(x):
            x = float(x)
            if x < dmin:
                return F * (x / dmin) ** FINES_GGS_N
            return float(np.interp(x, xs, ps))

        def size_at(p):
            if p <= F and F > 0:
                return dmin * (p / F) ** (1 / FINES_GGS_N)
            return float(np.interp(p, ps, xs))

        if F > 0:
            for k, p in (("x10_mm", .1), ("x50_mm", .5), ("x80_mm", .8), ("x90_mm", .9)):
                stats[k] = size_at(p)
            stats["passing_pct"] = {str(int(s_)): 100 * passing(s_) for s_ in res.sieves}
            stats["d_mean_mm"] = (1 - F) * stats["d_mean_mm"] + F * dmin * FINES_GGS_N / (FINES_GGS_N + 1)
            stats["oversize_mass_pct"] *= (1 - F)
        grid = np.geomspace(max(1.0, dmin * 0.25), ds[-1], 60)
        stats["curve"] = [(float(x), 100 * passing(x)) for x in grid]
    res.stats = stats
    res.classes = size_classes(stats["passing_pct"], SIEVES) if "passing_pct" in stats else []


def fmt_x(st: dict, key: str) -> str:
    """Размер Xnn для показа: если он попал в мелочь (мельче самого мелкого
    различимого куска), точного значения нет — пишем «< N»."""
    v = st.get(key)
    if v is None:
        return "—"
    lim = st.get("fines_below_mm")
    if st.get("fines_used") and lim and v < lim:
        return f"< {lim:.0f}"
    return f"{v:.0f}"


def size_classes(passing: dict, sieves=SIEVES) -> list[tuple[str, float]]:
    """Проход через сита -> содержание по классам крупности, % по массе."""
    out, prev, lo = [], 0.0, 0
    for s in sieves:
        p = float(passing[str(int(s))])
        out.append((f"{lo}–{int(s)} мм" if lo else f"до {int(s)} мм", max(0.0, p - prev)))
        prev, lo = p, int(s)
    out.append((f"более {lo} мм", max(0.0, 100.0 - prev)))
    return out
