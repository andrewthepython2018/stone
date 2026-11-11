"""Гранулометрические показатели по набору кусков."""
from __future__ import annotations

import numpy as np


def size_stats(d_mm, oversize_mm: float, sieves_mm, unresolved_frac: float | None = None) -> dict:
    """Показатели кусковатости.

    d_mm — размеры кусков (диаметр равновеликого круга), мм.
    Массовая доля куска принимается пропорциональной d^3 (объём), как в
    стандартной фотопланиметрии. Xnn — размер, мельче которого nn % массы.
    """
    d = np.asarray(d_mm, float)
    d = d[np.isfinite(d) & (d > 0)]
    out = {"n": int(d.size)}
    if d.size < 5:
        return out
    w = d ** 3
    order = np.argsort(d)
    ds, ws = d[order], w[order]
    cum = np.cumsum(ws) / ws.sum()

    def xp(p):
        return float(np.interp(p, cum, ds))

    out.update({
        "d_mean_mm": float(np.sum(w * d) / w.sum()),   # средневзвешенный по массе размер куска
        "d_mean_count_mm": float(d.mean()),            # среднеарифметический по числу кусков
        "x10_mm": xp(0.10), "x50_mm": xp(0.50), "x80_mm": xp(0.80), "x90_mm": xp(0.90),
        "d_max_mm": float(d.max()),
        "oversize_mass_pct": float(100 * w[d > oversize_mm].sum() / w.sum()),
        "passing_pct": {str(int(s)): float(100 * w[d <= s].sum() / w.sum()) for s in sieves_mm},
    })
    rr = rosin_rammler(ds, cum)
    if rr:
        out["rosin_rammler"] = rr
    if unresolved_frac is not None:
        out["unresolved_area_pct"] = float(100 * unresolved_frac)
    return out


def rosin_rammler(ds, cum) -> dict | None:
    """P(x) = 1 - exp(-(x / xc)^n): xc — характерный размер, n — однородность."""
    m = (cum > 0.03) & (cum < 0.97)
    if m.sum() < 5:
        return None
    x = np.log(ds[m])
    y = np.log(-np.log(1 - cum[m]))
    n, b = np.polyfit(x, y, 1)
    if n <= 0:
        return None
    xc = float(np.exp(-b / n))
    return {"xc_mm": xc, "n": float(n)}


def combine(frame_stats: list[dict]) -> dict:
    """Сводит показатели нескольких кадров одного ковша: медиана и разброс."""
    keys = ["d_mean_mm", "d_mean_count_mm", "x10_mm", "x50_mm", "x80_mm", "x90_mm",
            "d_max_mm", "oversize_mass_pct", "unresolved_area_pct"]
    good = [s for s in frame_stats if "x50_mm" in s]
    out = {"frames": len(good)}
    if not good:
        return out
    for k in keys:
        v = np.array([s[k] for s in good if k in s], float)
        if v.size:
            out[k] = float(np.median(v))
            if v.size >= 3:
                out[k + "_iqr"] = float(np.subtract(*np.percentile(v, [75, 25])))
    out["n_median"] = int(np.median([s["n"] for s in good]))
    sieves = good[0].get("passing_pct", {}).keys()
    out["passing_pct"] = {s: float(np.median([g["passing_pct"][s] for g in good])) for s in sieves}
    return out
