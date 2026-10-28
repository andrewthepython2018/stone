"""Параметры системы. Все значения можно переопределить в YAML/JSON-конфиге."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class ScaleConfig:
    # Реальная ширина ковша по наружным боковым стенкам у режущей кромки, мм.
    # ЭТО ЕДИНСТВЕННЫЙ ПАРАМЕТР, КОТОРЫЙ НУЖНО ИЗМЕРИТЬ НА МАШИНЕ.
    bucket_width_mm: float = 3000.0


@dataclass
class DetectorConfig:
    work_width: int = 480            # ширина кадра для грубого поиска ковша
    n_scales: int = 18
    min_score: float = 0.38          # ниже — ковш не найден (вне кадра / перекрыт)
    scale_jump_penalty: float = 0.3  # штраф за скачок размера между соседними кадрами (на 1 ед. ln)
    cx_band: float = 0.06            # допуск по горизонтали от выученного центра ковша, доля ширины кадра


@dataclass
class PhaseConfig:
    sample_fps: float = 5.0          # частота анализа кадров при поиске фаз
    smooth_s: float = 1.0            # окно медианного сглаживания сигналов, с
    min_raised_s: float = 5.0        # поднят короче (без поворота и разгрузки) — прерванное черпание
    min_lowered_s: float = 2.5       # ковш внизу короче этого — не новое черпание
    rise_start_frac: float = 0.12    # начало черпания = 12 % подъёма от нижнего уровня
    min_dig_s: float = 1.0           # подъём по забою короче этого — не черпание
    fill_min_energy: float = 6.0     # текстура содержимого: ниже — ковш пустой/неполный
    fill_window_s: float = 3.0       # заполненность — по первым секундам после подъёма
    empty_fill_frac: float = 0.6     # падение текстуры ниже 0.6*порога = разгрузка
    max_load_frames: int = 10        # сколько кадров полного ковша анализировать
    face_frames: int = 6             # сколько кадров забоя перед черпанием


@dataclass
class SegmentationConfig:
    upscale: float = 2.0             # апскейл ROI перед сегментацией (куски в 1080p мелкие)
    clahe_clip: float = 2.0
    denoise_sigma_color: float = 20.0
    blackhat_radius_px: float = 3.0  # полуширина тени-зазора между кусками, px кадра
    edge_weight: float = 1.0         # вклад градиента яркости в карту границ
    local_norm_px: float = 25.0      # радиус локальной нормировки контраста, px кадра (0 — выкл.)
    merge_threshold: float = 0.45    # слияние соседних областей со слабой границей
    merge_intensity: float = 30.0    # ...и разницей средней яркости не больше, уровней 0..255
    merge_min_solidity: float = 0.7  # ...и объединение не становится сильно невыпуклым
    min_boundary: float = 0.28       # минимальная средняя «сила» контура куска
    max_inner_ratio: float = 0.75    # внутренность / контур (больше — группа или мелочь)
    edge_on: float = 0.3             # уровень карты границ, считающийся «видимой границей»
    min_closed: float = 0.55         # доля контура с видимой границей
    min_diameter_px: float = 5.0     # меньше — неразрешимая «мелочь», px кадра
    max_area_frac: float = 0.25      # больше доли ROI — ошибка сегментации
    coarse_factor: float = 1.6       # грубый масштаб для крупных кусков (1 — выключить)
    coarse_min_diameter_px: float = 5.0  # мин. диаметр на грубом масштабе, px уменьшенного кадра
    coarse_min_closed: float = 0.7       # требования к контуру на грубом масштабе
    coarse_max_inner_ratio: float = 0.6
    coarse_max_fine_cover: float = 0.35  # крупный кусок не принимается, если мелкий масштаб
                                         # уже разрешил больше этой доли его площади
    min_solidity: float = 0.75
    min_solidity_large: float = 0.86 # для областей крупнее 4 мин. диаметров
    max_elongation: float = 4.0
    # второй проход внутри отбракованных «групп»: слияние строже
    refine: bool = True
    refine_merge_threshold: float = 0.45
    refine_merge_min_solidity: float = 0.70
    refine_min_closed: float = 0.55
    refine_max_inner_ratio: float = 0.75
    refine_min_contrast: float = 0.0
    min_contrast: float = 0.0        # мин. перепад яркости кусок/окружение (0 — не проверять)
    # третий проход: мелкие светлые куски среди мелочи (white top-hat)
    small_pieces: bool = True
    small_max_diameter_px: float = 18.0   # px кадра
    small_min_contrast: float = 12.0      # ярче кольца вокруг, уровней 0..255
    small_k_mad: float = 3.0


@dataclass
class TeethConfig:
    enabled: bool = True
    n_teeth: int = 0                 # число зубьев; 0 — определить по видео
    auto_n: tuple = (4, 9)           # диапазон поиска числа зубьев
    auto_slot_rel: float = 0.70      # отклик каждой позиции >= 0.7 медианы по позициям
    band: tuple = (1.15, 0.05)       # полоса поиска над верхом блока, в ширинах блока
    half_width: float = 0.85         # полуширина полосы поиска от центра ковша
    tooth_w: tuple = (0.05, 0.06, 0.07)  # ширины шаблона зуба, в ширинах блока
    tooth_shear: tuple = (0.0,)      # наклоны зуба dx/dy для оценки позиций (напр. -0.15, 0, 0.15)
    tooth_len: float = 0.16          # длина боковых граней зуба в шаблоне
    tooth_above: float = 0.03        # пустое поле над концом зуба в шаблоне
    work_w: float = 220.0            # ширина блока в рабочем масштабе поиска, px
    row_tol: float = 0.035           # допуск по высоте зуба относительно линии гребёнки
    max_tilt: float = 0.12           # наклон линии зубьев
    pitch: tuple = (0.12, 0.26)      # шаг зубьев, в ширинах блока
    center_penalty: float = 0.5      # штраф за смещение гребёнки от оси ковша
    bg_shift: float = 0.15           # окно фона: выше линии зубьев на столько ширин блока
    axis_tol: float = 0.20           # после обучения: допуск середины гребёнки от оси, в шагах
    min_quality: float = 0.45        # гребёнка уверенна: 4 лучших зуба в среднем выше
    min_learn_views: int = 8         # минимум кадров для обучения
    learn_cycles: int = 2            # обучение по кадрам первых циклов
    view_fps: float = 2.5            # частота кадров пустого ковша для контроля
    empty_fill: float = 4.5          # текстура содержимого ниже — ковш пустой
    present: float = 0.70            # контраст позиции за цикл / эталон: зуб на месте
    missing: float = 0.45            # ниже — зуба нет, если и конец зуба не найден на месте
    gone: float = 0.15               # ниже — зуба нет в любом случае
    min_views: int = 6               # минимум уверенных кадров в цикле для решения
    alarm_cycles: int = 2            # отсутствие столько циклов подряд — уведомление
    baseline_cycles: int = 1         # зуб должен быть уверенно виден до этого
    # Длина зуба: конец каждого зуба ищется отдельно и сравнивается с прямой,
    # проведённой через концы остальных зубьев. Обломанный зуб короче —
    # его конец ниже прямой. Не требует эталона: виден и зуб, обломанный
    # ещё до начала записи.
    tip_w: float = 330.0             # ширина блока в масштабе поиска концов зубьев, px
    tip_level: float = 0.5           # конец зуба: граница слабее этой доли уровня тела зуба
    short: float = 0.035             # конец ниже прямой на столько ширин блока — зуб обломан
    short_edge: float = 0.150        # то же для крайних зубьев (у стенок кромка в перспективе ниже по-разному)
    lost: float = 0.10               # ниже на столько или конец не найден — зуба нет
    # Утеря — резкое событие: «есть» в одном цикле, «нет» в следующих. Если же
    # отклик падал постепенно (через «?»: налипание породы, другой ракурс),
    # выдаётся предупреждение «проверить зуб», а не уведомление об утере.


@dataclass
class ReportConfig:
    oversize_mm: float = 1000.0      # негабарит, мм
    sieve_sizes_mm: list = field(default_factory=lambda: [50, 100, 200, 300, 400, 500, 700, 1000, 1500])
    save_overlays: bool = True


@dataclass
class Config:
    scale: ScaleConfig = field(default_factory=ScaleConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    phases: PhaseConfig = field(default_factory=PhaseConfig)
    segmentation: SegmentationConfig = field(default_factory=SegmentationConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    teeth: TeethConfig = field(default_factory=TeethConfig)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def load(cls, path: str | Path | None) -> "Config":
        cfg = cls()
        if path is None:
            return cfg
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in (".yaml", ".yml"):
            import yaml  # optional dependency
            data = yaml.safe_load(text) or {}
        else:
            data = json.loads(text)
        for section in fields(cls):
            values = data.get(section.name) or {}
            target = getattr(cfg, section.name)
            known = {f.name for f in fields(target)}
            for k, v in values.items():
                if k not in known:
                    raise KeyError(f"Неизвестный параметр {section.name}.{k}")
                setattr(target, k, v)
        return cfg
