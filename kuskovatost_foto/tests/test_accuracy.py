import numpy as np
import pytest

from fragmentation.config import SegmentationConfig
from fragmentation.metrics import rosin_rammler, size_stats
from fragmentation.roi import Roi
from fragmentation.segment import segment, segment_multiscale
from tests.synthetic import realistic_pile, rock_pile


def _roi(shape, mmpp=10.0):
    h, w = shape[:2]
    return Roi("load", (0, 0, w, h), np.ones((h, w), bool), np.full((h, w), mmpp, np.float32), float(w))


@pytest.mark.parametrize("fn", [segment, segment_multiscale])
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_synthetic_pile_sizes(seed, fn):
    """Сегментация на синтетическом развале: X50/X80 по массе в пределах 15 % от истины."""
    img, truth_px = rock_pile(seed=seed)
    mmpp = 10.0
    seg = fn(img, _roi(img.shape, mmpp), SegmentationConfig())
    est = size_stats([f.d_eq_mm for f in seg.fragments], 1000, [100, 300])
    ref = size_stats(truth_px * mmpp, 1000, [100, 300])
    assert est["n"] > 0.6 * ref["n"]
    for k in ("x50_mm", "x80_mm"):
        assert abs(est[k] / ref[k] - 1) < 0.15, (k, est[k], ref[k])


@pytest.mark.parametrize("contrast", [1.0, 0.5])
def test_realistic_pile_multiscale(contrast):
    """Трудная синтетика (грани, шероховатость, мелочь, куски вплотную, JPEG 40):
    средняя по 3 сценам ошибка X50 и X80 по массе — не больше 15 %."""
    errs = []
    for seed in range(3):
        img, truth_px = realistic_pile(seed=seed, contrast=contrast)
        seg = segment_multiscale(img, _roi(img.shape), SegmentationConfig())
        est = size_stats([f.d_eq_mm for f in seg.fragments], 1000, [100])
        ref = size_stats(truth_px * 10.0, 1000, [100])
        errs.append([est["x50_mm"] / ref["x50_mm"] - 1, est["x80_mm"] / ref["x80_mm"] - 1])
    mean = np.abs(np.mean(errs, axis=0))
    assert (mean < 0.15).all(), errs


def test_size_stats_and_rr():
    rng = np.random.default_rng(0)
    # Розин-Раммлер: xc=300, n=1.5 по массе -> генерируем через обратную функцию на массовых долях
    d = np.linspace(20, 1500, 4000)
    p = 1 - np.exp(-(d / 300) ** 1.5)
    # веса по массе = dP; d^3 * count = dP -> count = dP / d^3
    cnt = np.diff(np.r_[0, p]) / d ** 3
    sample = rng.choice(d, size=200000, p=cnt / cnt.sum())
    s = size_stats(sample, 1000, [100])
    assert abs(s["x50_mm"] / (300 * np.log(2) ** (1 / 1.5)) - 1) < 0.05
    rr = s["rosin_rammler"]
    assert abs(rr["xc_mm"] / 300 - 1) < 0.08 and abs(rr["n"] / 1.5 - 1) < 0.12
    assert rosin_rammler(np.array([1.0]), np.array([0.5])) is None
