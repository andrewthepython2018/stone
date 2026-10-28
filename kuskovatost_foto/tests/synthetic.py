"""Генератор синтетического «развала» с известными размерами кусков (для проверки точности)."""
from __future__ import annotations

import cv2
import numpy as np


def rock_pile(size=(600, 800), d_range=(14, 70), seed=0, jpeg=60, fill_target=0.55):
    """Возвращает (BGR-изображение, массив истинных d_eq в px).

    Куски — случайные выпуклые многоугольники с градиентной заливкой (освещение
    грани), между ними тёмные зазоры; затем размытие, шум и JPEG-сжатие, как у
    видео с камеры.
    """
    rng = np.random.default_rng(seed)
    H, W = size
    img = np.full((H, W), 55, np.float32) + rng.normal(0, 6, (H, W)).astype(np.float32)
    occ = np.zeros((H, W), np.uint8)
    truth = []
    tries = 0
    # размеры ~ логнормально: много мелких, мало крупных
    while occ.mean() / 255 < fill_target and tries < 20000:
        tries += 1
        d = float(np.clip(np.exp(rng.normal(np.log(30), 0.45)), *d_range))
        cx, cy = rng.uniform(d, W - d), rng.uniform(d, H - d)
        k = rng.integers(5, 9)
        ang = np.sort(rng.uniform(0, 2 * np.pi, k))
        rad = d / 2 * rng.uniform(0.75, 1.15, k)
        el = rng.uniform(0.7, 1.0)
        rot = rng.uniform(0, np.pi)
        x = rad * np.cos(ang); y = rad * np.sin(ang) * el
        pts = np.stack([cx + x * np.cos(rot) - y * np.sin(rot), cy + x * np.sin(rot) + y * np.cos(rot)], 1)
        hull = cv2.convexHull(pts.astype(np.float32)).astype(np.int32)
        x0, y0, bw, bh = cv2.boundingRect(hull)
        x0, y0 = max(0, x0 - 3), max(0, y0 - 3)
        x1, y1 = min(W, x0 + bw + 6), min(H, y0 + bh + 6)
        m = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.fillConvexPoly(m, hull - [x0, y0], 255)
        # зазор 2 px до соседей
        if (cv2.dilate(m, np.ones((5, 5), np.uint8)) & occ[y0:y1, x0:x1]).any():
            continue
        occ[y0:y1, x0:x1] |= m
        truth.append(2 * np.sqrt(float((m > 0).sum()) / np.pi))
        base = rng.uniform(110, 210)
        gx, gy = rng.normal(0, 0.9, 2)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        shade = base + gx * (xx - cx) + gy * (yy - cy) + rng.normal(0, 4, m.shape)
        sub = img[y0:y1, x0:x1]
        sub[m > 0] = shade[m > 0]
    img = cv2.GaussianBlur(img, (0, 0), 0.9)
    img += rng.normal(0, 3, img.shape).astype(np.float32)
    img = np.clip(img, 0, 255).astype(np.uint8)
    bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    ok, enc = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, jpeg])
    return cv2.imdecode(enc, cv2.IMREAD_COLOR), np.array(truth)


def realistic_pile(size=(400, 600), seed=0, jpeg=40, median_d=14.0, sigma=0.55, d_range=(6, 110),
                   contrast=1.0):
    """Более трудный развал: текстурированный фон-мелочь, грани с разной освещённостью,
    шероховатость поверхности, местами куски вплотную без тёмного зазора,
    крупные куски до 150 px, сильное размытие и JPEG. Возвращает (BGR, d_eq px)."""
    rng = np.random.default_rng(seed)
    H, W = size
    # фон: мелочь ниже разрешения — шум с короткой корреляцией
    noise = rng.normal(0, 1, (H, W)).astype(np.float32)
    img = 105 + 18 * cv2.GaussianBlur(noise, (0, 0), 1.2) / 0.25
    img = np.clip(img, 40, 170)
    occ = np.zeros((H, W), np.uint8)
    truth = []
    # крупные кладём первыми, как при реальной укладке
    ds = np.clip(np.exp(rng.normal(np.log(median_d), sigma, 4000)), *d_range)
    ds = np.sort(ds)[::-1]
    for d in ds:
        if occ.mean() / 255 > 0.62:
            break
        for _ in range(25):
            cx, cy = rng.uniform(d / 2, W - d / 2), rng.uniform(d / 2, H - d / 2)
            k = rng.integers(5, 9)
            ang = np.sort(rng.uniform(0, 2 * np.pi, k))
            rad = d / 2 * rng.uniform(0.75, 1.15, k)
            el = rng.uniform(0.65, 1.0)
            rot = rng.uniform(0, np.pi)
            x = rad * np.cos(ang); y = rad * np.sin(ang) * el
            pts = np.stack([cx + x * np.cos(rot) - y * np.sin(rot), cy + x * np.sin(rot) + y * np.cos(rot)], 1)
            hull = cv2.convexHull(pts.astype(np.float32)).astype(np.int32)
            x0, y0, bw, bh = cv2.boundingRect(hull)
            x0, y0 = max(0, x0 - 3), max(0, y0 - 3)
            x1, y1 = min(W, x0 + bw + 6), min(H, y0 + bh + 6)
            m = np.zeros((y1 - y0, x1 - x0), np.uint8)
            cv2.fillConvexPoly(m, hull - [x0, y0], 255)
            gap = int(rng.choice([1, 3, 3, 5] if contrast >= 1 else [1, 1, 3, 3]))  # 1 — вплотную
            if (cv2.dilate(m, np.ones((gap, gap), np.uint8)) & occ[y0:y1, x0:x1]).any():
                continue
            occ[y0:y1, x0:x1] |= m
            truth.append(2 * np.sqrt(float((m > 0).sum()) / np.pi))
            yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
            base = 130 + contrast * rng.uniform(-45, 45)
            # 2 грани: разная освещённость по разные стороны случайной линии через кусок
            a = rng.uniform(0, np.pi)
            side = (np.cos(a) * (xx - cx) + np.sin(a) * (yy - cy)) > rng.uniform(-0.2, 0.2) * d
            gx, gy = rng.normal(0, 0.5, 2)
            shade = base + np.where(side, rng.uniform(-25, 25), 0) + gx * (xx - cx) + gy * (yy - cy)
            tex = cv2.GaussianBlur(rng.normal(0, 1, m.shape).astype(np.float32), (0, 0), 1.5) * 25
            sub = img[y0:y1, x0:x1]
            sub[m > 0] = (shade + tex)[m > 0]
            # тень снизу-справа от куска
            sh = cv2.dilate(m, np.ones((3, 3), np.uint8)) & ~m
            sub[sh > 0] *= 1 - 0.4 * contrast
            break
    img = cv2.GaussianBlur(img.astype(np.float32), (0, 0), 1.0)
    img += rng.normal(0, 3, img.shape).astype(np.float32)
    bgr = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    ok, enc = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, jpeg])
    return cv2.imdecode(enc, cv2.IMREAD_COLOR), np.array(truth)
