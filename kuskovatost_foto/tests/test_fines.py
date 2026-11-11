import numpy as np

from foto.analyze import Config, analyze, polygon_roi
from tests.synthetic import realistic_pile


def test_fines_share_on_synthetic():
    """Доля мелочи по площади: фон-мелочь между кусками (вместе с тенями ~38 %)
    оценивается консервативно — не завышается и не теряется."""
    for seed in range(2):
        img, truth = realistic_pile(seed=seed)
        H, W = img.shape[:2]
        rock = np.sum(np.pi * (truth / 2) ** 2) / (H * W)
        roi = polygon_roi(img.shape, [(0, 0), (W - 1, 0), (W - 1, H - 1), (0, H - 1)], 10.0)
        r = analyze(img, roi, Config())
        assert 0.5 * (1 - rock) < r.fines_frac < 1 - rock + 0.05
        # с мелочью состав мельче, чем только по кускам
        assert r.stats["passing_pct"]["100"] > 100 * r.fines_frac
