"""Демо: кусковатость породы по фотографиям.

Запуск:  python -m foto [ФОТО ...]
Для каждого фото программа пытается найти поднятый ковш с породой и сразу
считает кусковатость. Если ковш не найден (или нужна другая область) —
область обводится мышью, и расчёт идёт по ней.
"""
from __future__ import annotations

import csv
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QLibraryInfo, QLocale, QObject, QPointF, QRectF, Qt, QTranslator, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSizePolicy,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

import matplotlib
import matplotlib.ticker
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from fragmentation.bucket import BucketDetector  # noqa: E402
from fragmentation.config import Config  # noqa: E402
from fragmentation.imio import imread, imwrite  # noqa: E402

from .analyze import (SIEVES, BucketInfo, PhotoResult, analyze, finalize, find_bucket,  # noqa: E402
                      fmt_x, polygon_roi, size_classes, to_work)

PHOTO_FILTER = "Фото (*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp);;Все файлы (*)"
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
DETAIL = [("Точно", 1.0), ("Быстро", 0.5)]


def to_qimage(img: np.ndarray) -> QImage:
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888).copy()


def draw_bucket(img: np.ndarray, b: BucketInfo | None) -> None:
    """Найденный ковш: рамка корпуса блока и боковые стенки (эталон ширины)."""
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


@dataclass(eq=False)
class Photo:
    path: str
    img: np.ndarray                      # рабочее фото (не шире 1920 px)
    bucket: BucketInfo | None = None
    polygon: list = field(default_factory=list)
    result: PhotoResult | None = None
    scale: float | None = None           # мм/пикс для обведённой области
    scale_src: str = ""                  # откуда взят масштаб

    @property
    def name(self) -> str:
        return Path(self.path).name


# ---------------------------------------------------------------- просмотр
class PhotoView(QWidget):
    """Фото с наложением. Режимы: просмотр, обвод контура, эталонная линия.

    Контур: щелчки левой кнопкой ставят вершины, двойной щелчок или правая
    кнопка замыкают; можно и обвести, ведя мышь с нажатой кнопкой.
    Backspace — убрать последнюю точку, Esc — отменить.
    Колесо мыши — увеличение, перетаскивание (в просмотре) или средняя
    кнопка — сдвиг, двойной щелчок в просмотре — вписать фото в окно."""
    polygonDone = Signal(list)
    lineDone = Signal(float)             # длина линии, px рабочего фото
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(640, 400)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.pix: QPixmap | None = None
        self.img_size = (1, 1)
        self.mode = "view"
        self.pts: list[tuple[float, float]] = []
        self.shown_poly: list = []       # уже заданный контур (показывается пунктиром)
        self.hover: tuple[float, float] | None = None
        self.dragging = False
        self.zoom = 1.0
        self.pan = QPointF(0, 0)
        self._pan_from: QPointF | None = None
        self.hint = "Откройте фото кнопкой «Открыть фото…» или перетащите файлы в окно"

    def set_image(self, img: np.ndarray | None, poly=None, reset: bool = False):
        if reset:
            self.zoom, self.pan = 1.0, QPointF(0, 0)
        if img is None:
            self.pix = None
        else:
            self.pix = QPixmap.fromImage(to_qimage(img))
            self.img_size = (img.shape[1], img.shape[0])
        self.shown_poly = list(poly or [])
        self.update()

    def start(self, mode: str):
        self.mode, self.pts, self.hover, self.dragging = mode, [], None, False
        self.setCursor(Qt.CrossCursor if mode != "view" else Qt.ArrowCursor)
        self.setFocus()
        self.update()

    def stop(self):
        self.start("view")

    # координаты: экран <-> фото
    def _rect(self) -> QRectF:
        W, H = self.width(), self.height()
        iw, ih = self.img_size
        s = min(W / iw, H / ih) * self.zoom
        return QRectF((W - iw * s) / 2 + self.pan.x(), (H - ih * s) / 2 + self.pan.y(), iw * s, ih * s)

    def _to_img(self, p) -> tuple[float, float]:
        r = self._rect()
        s = r.width() / self.img_size[0]
        x = min(max((p.x() - r.x()) / s, 0), self.img_size[0] - 1)
        y = min(max((p.y() - r.y()) / s, 0), self.img_size[1] - 1)
        return x, y

    def _to_scr(self, x, y) -> QPointF:
        r = self._rect()
        s = r.width() / self.img_size[0]
        return QPointF(r.x() + x * s, r.y() + y * s)

    def paintEvent(self, e):
        qp = QPainter(self)
        qp.fillRect(self.rect(), QColor("#1b1b1b"))
        if self.pix is None:
            qp.setPen(QColor("#aaa"))
            qp.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, self.hint)
            return
        qp.setRenderHint(QPainter.SmoothPixmapTransform)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.drawPixmap(self._rect(), self.pix, QRectF(self.pix.rect()))
        if self.shown_poly and self.mode == "view":
            pen = QPen(QColor(255, 0, 255), 2, Qt.DashLine)
            qp.setPen(pen)
            qp.drawPolygon(QPolygonF([self._to_scr(*p) for p in self.shown_poly]))
        if self.mode in ("poly", "line") and self.pts:
            pts = [self._to_scr(*p) for p in self.pts]
            if self.hover and not self.dragging:
                pts.append(self._to_scr(*self.hover))
            col = QColor(0, 255, 255) if self.mode == "poly" else QColor(255, 200, 0)
            qp.setPen(QPen(col, 2))
            qp.drawPolyline(QPolygonF(pts))
            if self.mode == "poly" and len(pts) > 2:
                qp.setPen(QPen(col, 1, Qt.DotLine))
                qp.drawLine(pts[-1], pts[0])
            qp.setPen(QPen(col, 1))
            qp.setBrush(col)
            for p in pts[:len(self.pts)] if len(self.pts) < 60 else pts[:1]:
                qp.drawEllipse(p, 3, 3)
        if self.mode != "view":
            msg = ("Обведите область: щелчки — вершины, двойной щелчок или правая кнопка — замкнуть; "
                   "можно вести мышь с нажатой кнопкой.  Backspace — шаг назад, Esc — отмена"
                   if self.mode == "poly" else
                   "Эталон: проведите линию по предмету известной длины (нажать — тянуть — отпустить).  Esc — отмена")
            qp.fillRect(0, 0, self.width(), 28, QColor(0, 0, 0, 170))
            qp.setPen(QColor("white"))
            qp.drawText(8, 19, msg)

    def wheelEvent(self, e):
        if self.pix is None:
            return
        pos = e.position()
        x, y = self._to_img(pos)
        z = float(np.clip(self.zoom * 1.25 ** (e.angleDelta().y() / 120), 1.0, 12.0))
        if z == 1.0:
            self.zoom, self.pan = 1.0, QPointF(0, 0)
        else:
            self.zoom = z
            q = self._to_scr(x, y)              # точка под курсором остаётся на месте
            self.pan += pos - q
        self.update()

    def mousePressEvent(self, e):
        if self.pix is None:
            return
        if e.button() == Qt.MiddleButton or (self.mode == "view" and e.button() == Qt.LeftButton):
            self._pan_from = e.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        if self.mode == "view":
            return
        p = self._to_img(e.position())
        if self.mode == "line":
            if e.button() == Qt.LeftButton:
                self.pts = [p]
                self.dragging = True
            return
        if e.button() == Qt.RightButton:
            self._close_poly()
            return
        if e.button() == Qt.LeftButton:
            self.pts.append(p)
            self.dragging = True
            self._drag_n = 0
            self.update()

    def mouseMoveEvent(self, e):
        if self._pan_from is not None:
            self.pan += e.position() - self._pan_from
            self._pan_from = e.position()
            self.update()
            return
        if self.pix is None or self.mode == "view":
            return
        p = self._to_img(e.position())
        self.hover = p
        if self.dragging and e.buttons() & Qt.LeftButton:
            if self.mode == "line":
                self.pts = [self.pts[0], p]
            else:
                q = self._to_scr(*self.pts[-1])
                if abs(q.x() - e.position().x()) + abs(q.y() - e.position().y()) > 6:
                    self.pts.append(p)
                    self._drag_n += 1
        self.update()

    def mouseReleaseEvent(self, e):
        if self._pan_from is not None:
            self._pan_from = None
            self.setCursor(Qt.CrossCursor if self.mode != "view" else Qt.ArrowCursor)
            return
        if self.mode == "line" and self.dragging:
            self.dragging = False
            if len(self.pts) == 2:
                (x0, y0), (x1, y1) = self.pts
                L = float(np.hypot(x1 - x0, y1 - y0))
                if L > 5:
                    self.stop()
                    self.lineDone.emit(L)
                    return
            self.pts = []
            self.update()
        elif self.mode == "poly" and self.dragging:
            self.dragging = False
            # обвели одним движением — сразу замыкаем
            if getattr(self, "_drag_n", 0) >= 12:
                self._close_poly()

    def mouseDoubleClickEvent(self, e):
        if self.mode == "poly":
            self._close_poly()
        elif self.mode == "view":
            self.zoom, self.pan = 1.0, QPointF(0, 0)
            self.update()

    def keyPressEvent(self, e):
        if self.mode == "view":
            return super().keyPressEvent(e)
        if e.key() == Qt.Key_Escape:
            self.stop()
            self.cancelled.emit()
        elif e.key() == Qt.Key_Backspace and self.pts:
            self.pts.pop()
            self.update()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter) and self.mode == "poly":
            self._close_poly()

    def _close_poly(self):
        pts = []
        for p in self.pts:                      # двойной щелчок дублирует точку
            if not pts or abs(p[0] - pts[-1][0]) + abs(p[1] - pts[-1][1]) > 1:
                pts.append(p)
        if len(pts) < 3:
            return
        self.stop()
        self.polygonDone.emit(pts)


# ---------------------------------------------------------------- графики
class Chart(FigureCanvasQTAgg):
    """Сверху — гранулометрический состав текущего фото (остальные — серым),
    снизу — X50 и X80 по всем фото, средние — пунктиром."""

    def __init__(self):
        self.fig = Figure(figsize=(5, 5.4), tight_layout=True)
        super().__init__(self.fig)
        self.ax1 = self.fig.add_subplot(211)
        self.ax2 = self.fig.add_subplot(212)
        self.update_data([], None)

    def update_data(self, photos: list[Photo], current: Photo | None):
        a1, a2 = self.ax1, self.ax2
        a1.clear()
        a2.clear()
        done = [p for p in photos if p.result is not None and "x50_mm" in p.result.stats]
        a1.set_title("Гранулометрический состав", fontsize=10)
        a1.set_xlabel("размер куска, мм", fontsize=9)
        a1.set_ylabel("проход, % по массе", fontsize=9)
        a1.grid(True, which="both", alpha=0.3)
        a1.set_ylim(0, 100)
        cur = current if current in done else None
        for p in done:
            if p is cur:
                continue
            x, y = self._curve(p.result.stats)
            a1.plot(x, y, color="#bbbbbb", lw=1)
        if cur is not None:
            st = cur.result.stats
            x, y = self._curve(st)
            a1.plot(x, y, color="#4c78a8", lw=2.5, label=cur.name[:30])
            for k, c in (("x50_mm", "#4c78a8"), ("x80_mm", "#e45756")):
                v = st[k]
                a1.axvline(v, color=c, ls="--", lw=1)
                a1.text(v, 3, f" {k[:3].upper()} {fmt_x(st, k)}", color=c, fontsize=8, rotation=90, va="bottom")
            a1.legend(fontsize=8, loc="upper left")
        if done:
            a1.set_xscale("log")
            a1.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
            a1.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
            a1.set_xticks([t for t in (10, 20, 50, 100, 200, 500, 1000, 2000)])
            xs = np.concatenate([self._curve(p.result.stats)[0] for p in done])
            a1.set_xlim(max(10, xs.min() * 0.8), xs.max() * 1.2)
        else:
            a1.text(0.5, 0.5, "нет результатов", ha="center", va="center", transform=a1.transAxes, color="#888")

        a2.set_title("Кусковатость по фото", fontsize=10)
        a2.set_ylabel("размер куска, мм", fontsize=9)
        a2.grid(True, axis="y", alpha=0.3)
        if done:
            x = np.arange(len(done))
            x50 = [p.result.stats["x50_mm"] for p in done]
            x80 = [p.result.stats["x80_mm"] for p in done]
            colors = ["#4c78a8" if p is cur else "#9ab5d3" for p in done]
            a2.bar(x, x50, 0.6, color=colors, label="X50 — медианный размер")
            a2.plot(x, x80, "o-", color="#e45756", lw=2, label="X80")
            a2.axhline(np.mean(x50), color="#4c78a8", ls="--", lw=1)
            a2.axhline(np.mean(x80), color="#e45756", ls="--", lw=1)
            a2.set_xticks(x, [str(i + 1) for i in x])
            a2.set_xlabel("фото №", fontsize=9)
            a2.set_ylim(0, max(x80) * 1.5)
            a2.legend(fontsize=8, loc="upper left", ncol=2)
        else:
            a2.set_xticks([])
        self.draw_idle()

    @staticmethod
    def _curve(st: dict):
        """Кривая проход/размер (с мелочью, если она учитывается)."""
        if st.get("curve"):
            c = np.array(st["curve"])
            return c[:, 0], c[:, 1]
        pts = sorted((float(k), v) for k, v in st["passing_pct"].items())
        x = [st["x10_mm"], st["x50_mm"], st["x80_mm"], st["x90_mm"], st["d_max_mm"]]
        y = [10, 50, 80, 90, 100]
        allp = sorted(set([(a, b) for a, b in pts if a < st["d_max_mm"]] + list(zip(x, y))))
        xs = np.array([a for a, _ in allp])
        ys = np.maximum.accumulate(np.array([b for _, b in allp]))
        return xs, ys


# ---------------------------------------------------------------- фоновая работа
class Bridge(QObject):
    bucket = Signal(object, object)       # Photo, BucketInfo
    result = Signal(object, object)       # Photo, PhotoResult
    failed = Signal(object, str)


class MainWindow(QMainWindow):
    def __init__(self, files=()):
        super().__init__()
        self.setWindowTitle("Кусковатость породы по фото")
        self.resize(1600, 950)
        self.setAcceptDrops(True)
        self.cfg = Config()
        self.detector = BucketDetector(self.cfg.detector)
        self.photos: list[Photo] = []
        self.cur: Photo | None = None
        self.busy = False
        self.bridge = Bridge()
        self.bridge.bucket.connect(self._on_bucket)
        self.bridge.result.connect(self._on_result)
        self.bridge.failed.connect(self._on_failed)

        # --- панель управления
        self.open_btn = QPushButton("📂  Открыть фото…")
        self.auto_btn = QPushButton("🔍  Найти ковш")
        self.auto_btn.setToolTip("Найти поднятый ковш и посчитать породу в нём")
        self.poly_btn = QPushButton("✎  Обвести контур")
        self.poly_btn.setToolTip("Обвести мышью область, в которой считать кусковатость")
        self.line_btn = QPushButton("📏  Эталон")
        self.line_btn.setToolTip("Провести линию по предмету известной длины — задаёт масштаб мм/пикс")
        self.width_mm = QDoubleSpinBox()
        self.width_mm.setRange(300, 10000)
        self.width_mm.setDecimals(0)
        self.width_mm.setValue(self.cfg.scale.bucket_width_mm)
        self.width_mm.setSuffix(" мм")
        self.width_mm.setToolTip("Реальная ширина ковша по наружным стенкам — эталон размера")
        self.scale = QDoubleSpinBox()
        self.scale.setRange(0.01, 500)
        self.scale.setDecimals(2)
        self.scale.setValue(5.0)
        self.scale.setSuffix(" мм/пикс")
        self.scale.editingFinished.connect(self._scale_typed)
        self.manual_scale: float | None = None   # последний масштаб по эталону / введённый вручную
        self.scale.setToolTip("Масштаб для обведённой области. Ставится сам по ковшу, если он есть "
                              "на фото, или кнопкой «Эталон»; можно ввести вручную.")
        self.detail = QComboBox()
        for name, _ in DETAIL:
            self.detail.addItem(name)
        self.detail.setToolTip("«Быстро» — расчёт на уменьшенном фото: в 2–4 раза быстрее, "
                               "но мелкие куски теряются (годится для снимков крупным планом)")
        self.use_fines = QCheckBox("учитывать мелочь")
        self.use_fines.setChecked(True)
        self.use_fines.setToolTip("Участки между кусками, где отдельные куски мельче предела разрешения "
                                  "(голубая штриховка на фото), входят в состав как мелкая фракция. "
                                  "Выключите, чтобы видеть состав только по выделенным кускам.")
        self.use_fines.toggled.connect(self._fines_toggled)
        self.auto_open = QCheckBox("искать ковш при открытии")
        self.auto_open.setChecked(True)
        self.run_btn = QPushButton("▶  Распознать")
        self.run_btn.setToolTip("Пересчитать текущее фото (после смены масштаба или детальности)")
        self.save_btn = QPushButton("💾  Сохранить отчёт…")
        bar = QHBoxLayout()
        for w in (self.open_btn, self.auto_btn, self.poly_btn, self.line_btn,
                  QLabel(" Ширина ковша:"), self.width_mm, QLabel(" Масштаб:"), self.scale,
                  QLabel(" Расчёт:"), self.detail, self.use_fines, self.auto_open, self.run_btn, self.save_btn):
            bar.addWidget(w)
        bar.addStretch(1)
        self.open_btn.clicked.connect(self.open_files)
        self.auto_btn.clicked.connect(self.auto_bucket)
        self.poly_btn.clicked.connect(self.draw_polygon)
        self.line_btn.clicked.connect(self.draw_line)
        self.run_btn.clicked.connect(self.rerun)
        self.save_btn.clicked.connect(self.save_report)

        # --- слева: список фото
        self.list = QListWidget()
        self.list.setMaximumWidth(260)
        self.list.currentRowChanged.connect(self._select)

        # --- центр: фото
        self.view = PhotoView()
        self.view.polygonDone.connect(self._on_polygon)
        self.view.lineDone.connect(self._on_line)
        self.view.cancelled.connect(lambda: self._status("Отменено"))
        self.show_ov = QCheckBox("показать найденные куски")
        self.show_ov.setChecked(True)
        self.show_ov.toggled.connect(self._refresh_view)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("font-size:14px; padding:4px;")
        mid = QVBoxLayout()
        mid.addWidget(self.view, 1)
        self.status.setMinimumHeight(4 * self.status.fontMetrics().height() + 14)
        self.status.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        mid.addWidget(self.show_ov)
        mid.addWidget(self.status)
        mw = QWidget()
        mw.setLayout(mid)

        # --- справа: итог, графики, таблица
        self.total = QLabel("Кусковатость: —")
        self.total.setWordWrap(True)
        self.total.setStyleSheet("font-size:16px; font-weight:bold; padding:4px;")
        self.total.setMinimumHeight(64)
        self.chart = Chart()
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Класс крупности", "Это фото, %", "Среднее по фото, %"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setToolTip("Доля массы породы по классам крупности (по выделенным кускам; "
                              "куски мельче предела разрешения не различимы)")
        right = QVBoxLayout()
        right.addWidget(self.total)
        right.addWidget(self.chart, 5)
        right.addWidget(QLabel("Содержание кусков по классам крупности (% по массе)"))
        right.addWidget(self.table, 3)
        rw = QWidget()
        rw.setLayout(right)

        split = QSplitter()
        split.addWidget(self.list)
        split.addWidget(mw)
        split.addWidget(rw)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 3)
        split.setStretchFactor(2, 2)
        split.setSizes([220, 860, 520])
        root = QVBoxLayout()
        root.addLayout(bar)
        root.addWidget(split, 1)
        cw = QWidget()
        cw.setLayout(root)
        self.setCentralWidget(cw)
        self._fill_table()
        self._update_buttons()
        if files:
            self.add_files(files)

    # ------------------------------------------------------------ файлы
    def open_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Фотографии", "", PHOTO_FILTER)
        if files:
            self.add_files(files)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = []
        for u in e.mimeData().urls():
            p = Path(u.toLocalFile())
            if p.is_dir():
                files += sorted(str(f) for f in p.iterdir() if f.suffix.lower() in PHOTO_EXT)
            elif p.suffix.lower() in PHOTO_EXT:
                files.append(str(p))
        if files:
            self.add_files(files)

    def add_files(self, files):
        first = None
        bad = []
        for f in files:
            img = imread(f)
            if img is None:
                bad.append(Path(f).name)
                continue
            work, _ = to_work(img)
            ph = Photo(str(f), work)
            self.photos.append(ph)
            it = QListWidgetItem(f"{len(self.photos)}. {ph.name}")
            it.setToolTip(str(f))
            self.list.addItem(it)
            first = first if first is not None else len(self.photos) - 1
        if bad:
            QMessageBox.warning(self, "Фото", "Не удалось открыть: " + ", ".join(bad))
        if first is not None:
            self.list.setCurrentRow(first)

    def _select(self, row: int):
        if self.busy:
            # пока идёт расчёт, переключение запрещено
            if self.cur is not None and row != self.photos.index(self.cur):
                self.list.blockSignals(True)
                self.list.setCurrentRow(self.photos.index(self.cur))
                self.list.blockSignals(False)
            return
        if not 0 <= row < len(self.photos):
            return
        self.view.stop()
        self.cur = self.photos[row]
        self.view.set_image(None, reset=True)
        self._show_scale(self.cur)
        self._refresh_view()
        self._refresh_results()
        ph = self.cur
        if ph.result is not None:
            self._status(self._describe(ph.result))
        elif ph.bucket is None and self.auto_open.isChecked():
            self.auto_bucket()
        elif ph.bucket is not None:
            self._status(ph.bucket.message or "")
        else:
            self._status("Нажмите «Найти ковш» или «Обвести контур»")
        self._update_buttons()

    # ------------------------------------------------------------ действия
    def auto_bucket(self):
        ph = self.cur
        if ph is None or self.busy:
            return
        self._set_busy(True, "Ищу ковш…")
        self.cfg.scale.bucket_width_mm = float(self.width_mm.value())

        def job():
            try:
                info = find_bucket(ph.img, self.cfg, self.detector)
                self.bridge.bucket.emit(ph, info)
            except Exception as e:      # noqa: BLE001
                self.bridge.failed.emit(ph, f"Ошибка поиска ковша: {e}")
        threading.Thread(target=job, daemon=True).start()

    def _on_bucket(self, ph: Photo, info: BucketInfo):
        ph.bucket = info
        self._set_busy(False)
        if ph is not self.cur:
            return
        mmpp = info.mm_per_px(float(self.width_mm.value()))
        if mmpp and ph.scale_src in ("", "ковш"):
            ph.scale, ph.scale_src = mmpp, "ковш"
        self._show_scale(ph)
        if info.roi is not None:
            ph.polygon = []
            self._run(ph, info.roi, "ковш", "по ширине ковша (по строкам, с учётом перспективы)")
            return
        self._refresh_view()
        why = info.message or "Ковш не найден"
        if ph.scale_src == "ковш":
            tail = f"Масштаб по ковшу ≈ {mmpp:.2f} мм/пикс (точен для области на глубине ковша)."
        elif ph.scale is not None:
            tail = f"Масштаб {ph.scale:.2f} мм/пикс — {self._scale_note(ph)}."
        else:
            tail = ("Масштаб на этом фото не известен: задайте «Эталон» (линия по предмету известной "
                    "длины) или введите мм/пикс, иначе размеры будут условными.")
        self._status(f"{why}. Обведите мышью область для расчёта. {tail}", warn=True)
        self.draw_polygon()

    def draw_polygon(self):
        if self.cur is None or self.busy:
            return
        self.show_ov.setChecked(False)
        self._refresh_view()
        self.view.start("poly")

    def draw_line(self):
        if self.cur is None or self.busy:
            return
        self.view.start("line")

    def _on_line(self, L: float):
        mm, ok = QInputDialog.getDouble(self, "Эталон", f"Длина линии {L:.0f} пикс. Реальная длина, мм:",
                                        1000.0, 1.0, 100000.0, 0)
        if not ok:
            return
        self.manual_scale = mm / L
        if self.cur is not None:
            self.cur.scale, self.cur.scale_src = mm / L, "эталон"
            self._show_scale(self.cur)
        self._status(f"Масштаб по эталону: {mm / L:.2f} мм/пикс")
        if self.cur and self.cur.polygon:
            self.rerun()

    def _on_polygon(self, pts: list):
        ph = self.cur
        if ph is None:
            return
        ph.polygon = pts
        self._run_polygon(ph)

    def _run_polygon(self, ph: Photo):
        if ph.scale_src == "ковш" and ph.bucket is not None and ph.bucket.found:
            ph.scale = ph.bucket.mm_per_px(float(self.width_mm.value()))   # могли сменить ширину ковша
        if ph.scale is None:
            # масштаб не задан для этого фото: берём последний эталон / введённый
            ph.scale = float(self.scale.value())
            ph.scale_src = "прошлое" if self.manual_scale is not None else "условный"
        roi = polygon_roi(ph.img.shape, ph.polygon, ph.scale)
        if roi is None:
            self._status("Область слишком маленькая — обведите больше", warn=True)
            return
        self._run(ph, roi, "контур", self._scale_note(ph))

    @staticmethod
    def _scale_note(ph: Photo) -> str:
        return {"ковш": "по ковшу на фото (приблизительно)", "эталон": "по эталону",
                "вручную": "введён вручную", "прошлое": "взят с прошлого эталона / ввода",
                "условный": "УСЛОВНЫЙ (задайте эталон)"}.get(ph.scale_src, "")

    def _show_scale(self, ph: Photo | None):
        v = ph.scale if ph is not None and ph.scale is not None else self.manual_scale
        if v is not None:
            self.scale.blockSignals(True)
            self.scale.setValue(v)
            self.scale.blockSignals(False)

    def _scale_typed(self):
        v = float(self.scale.value())
        ph = self.cur
        if ph is not None and (ph.scale is None or abs(ph.scale - v) > 0.004):
            ph.scale, ph.scale_src = v, "вручную"
            self.manual_scale = v

    def rerun(self):
        ph = self.cur
        if ph is None or self.busy:
            return
        if ph.polygon:
            self._run_polygon(ph)
        else:
            ph.bucket = None
            self.auto_bucket()

    def _run(self, ph: Photo, roi, source: str, scale_note: str):
        self._set_busy(True, "Распознаю куски… (несколько секунд)")
        self.cfg.scale.bucket_width_mm = float(self.width_mm.value())
        detail = DETAIL[self.detail.currentIndex()][1]

        def job():
            try:
                t = time.time()
                r = analyze(ph.img, roi, self.cfg, ph.name, source, detail, scale_note)
                r.time_s = time.time() - t
                finalize(r, self.use_fines.isChecked())
                self.bridge.result.emit(ph, r)
            except Exception as e:      # noqa: BLE001
                self.bridge.failed.emit(ph, f"Ошибка распознавания: {e}")
        threading.Thread(target=job, daemon=True).start()

    def _on_result(self, ph: Photo, r: PhotoResult):
        ph.result = r
        self._set_busy(False)
        self._list_text(ph)
        if ph is self.cur:
            self.show_ov.setChecked(True)
            self._refresh_view()
            self._status(self._describe(r), warn=r.source == "контур" and ph.scale_src == "условный")
        self._refresh_results()

    def _list_text(self, ph: Photo):
        r = ph.result
        i = self.photos.index(ph)
        mark = f"X50 {fmt_x(r.stats, 'x50_mm')} мм" if "x50_mm" in r.stats else "мало кусков"
        self.list.item(i).setText(f"{i + 1}. {ph.name}\n     {mark} ({r.source})")

    def _fines_toggled(self, on: bool):
        for ph in self.photos:
            if ph.result is not None:
                finalize(ph.result, on)
                self._list_text(ph)
        if self.cur is not None and self.cur.result is not None:
            self._status(self._describe(self.cur.result))
        self._refresh_results()

    def _on_failed(self, ph: Photo, msg: str):
        self._set_busy(False)
        self._status(msg, warn=True)

    # ------------------------------------------------------------ отображение
    def _describe(self, r: PhotoResult) -> str:
        st = r.stats
        if "x50_mm" not in st:
            return (f"Выделено кусков: {st.get('n', 0)} — слишком мало для расчёта. "
                    "Обведите область побольше или выберите расчёт «Точно».")
        src = "порода в ковше (найден автоматически)" if r.source == "ковш" else "обведённая область"
        fines = (f"Голубая штриховка — мелочь < {st.get('fines_below_mm', st['resolution_mm']):.0f} мм, "
                 f"{st['fines_pct']:.0f} % площади" +
                 (" (учтена)" if st.get("fines_used") else " (не учтена)") + ". ")
        unk = (f"Серая — не распознано, {st['unknown_pct']:.0f} % (не учитывается). "
               if st.get("unknown_pct", 0) >= 1 else "")
        return (f"{src.capitalize()}: кусков {st['n']}, масштаб {st['mm_per_px']:.2f} мм/пикс ({r.scale_note}). "
                + fines + unk + f"Расчёт {getattr(r, 'time_s', 0):.1f} с.")

    def _refresh_view(self):
        ph = self.cur
        if ph is None:
            self.view.set_image(None)
            return
        if ph.result is not None and self.show_ov.isChecked():
            img = ph.result.overlay.copy()
            draw_bucket(img, ph.bucket)
            self.view.set_image(img)
        else:
            img = ph.img.copy()
            draw_bucket(img, ph.bucket)
            self.view.set_image(img, ph.polygon)

    def _refresh_results(self):
        self.chart.update_data(self.photos, self.cur)
        self._fill_table()

    def _fill_table(self):
        done = [p for p in self.photos if p.result is not None and "passing_pct" in p.result.stats]
        cur = self.cur.result if self.cur is not None and self.cur in done else None
        last = cur.classes if cur else None
        avg = None
        if done:
            mean = {str(s): float(np.mean([p.result.stats["passing_pct"][str(s)] for p in done])) for s in SIEVES}
            avg = size_classes(mean)
            x50 = np.mean([p.result.stats["x50_mm"] for p in done])
            x80 = np.mean([p.result.stats["x80_mm"] for p in done])
            head = ""
            if cur:
                s = cur.stats
                head = (f"Это фото: X50 = {fmt_x(s, 'x50_mm')} мм, X80 = {fmt_x(s, 'x80_mm')} мм, "
                        f"d_ср = {s['d_mean_mm']:.0f} мм, макс. {s['d_max_mm']:.0f} мм\n")
            ms = {"x50_mm": x50, "x80_mm": x80, "fines_used": self.use_fines.isChecked(),
                  "fines_below_mm": float(np.mean([p.result.stats.get("fines_below_mm", 0) for p in done]))}
            self.total.setText(head + f"Среднее по фото ({len(done)}): X50 = {fmt_x(ms, 'x50_mm')} мм, "
                                      f"X80 = {fmt_x(ms, 'x80_mm')} мм")
        else:
            self.total.setText("Кусковатость: —")
        names = [n for n, _ in size_classes({str(s): 0 for s in SIEVES})]
        self.table.setRowCount(len(names) + 1)
        for i, name in enumerate(names):
            self.table.setItem(i, 0, QTableWidgetItem(name))
            for j, src in ((1, last), (2, avg)):
                it = QTableWidgetItem(f"{src[i][1]:.1f}" if src else "—")
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, j, it)
        # отдельной строкой — сколько из этого мелочь (не различимая поштучно)
        k = len(names)
        on = self.use_fines.isChecked()
        it = QTableWidgetItem("в т.ч. мелочь" if on else "мелочь (не учтена)")
        it.setToolTip("Доля площади пород, занятая мелочью мельче предела разрешения (голубая штриховка)")
        it.setForeground(QColor("#1f6fb5"))
        self.table.setItem(k, 0, it)
        vals = [cur.stats.get("fines_pct") if cur else None,
                float(np.mean([p.result.stats.get("fines_pct", 0) for p in done])) if done else None]
        for j, v in ((1, vals[0]), (2, vals[1])):
            it = QTableWidgetItem("—" if v is None else f"{v:.1f}")
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            it.setForeground(QColor("#1f6fb5"))
            self.table.setItem(k, j, it)

    def _status(self, text: str, warn: bool = False):
        self.status.setText(text)
        self.status.setStyleSheet("font-size:14px; padding:4px;" +
                                  (" background:#ffe8a0;" if warn else ""))

    def _set_busy(self, busy: bool, text: str = ""):
        self.busy = busy
        if text:
            self._status(text)
        QApplication.setOverrideCursor(Qt.WaitCursor) if busy else QApplication.restoreOverrideCursor()
        self._update_buttons()

    def _update_buttons(self):
        has = self.cur is not None and not self.busy
        for b in (self.auto_btn, self.poly_btn, self.line_btn, self.run_btn):
            b.setEnabled(has)
        self.open_btn.setEnabled(not self.busy)
        self.save_btn.setEnabled(not self.busy and any(p.result for p in self.photos))

    # ------------------------------------------------------------ отчёт
    def save_report(self):
        done = [p for p in self.photos if p.result is not None]
        if not done:
            return
        start = str(Path(done[0].path).parent)
        d = QFileDialog.getExistingDirectory(self, "Папка для отчёта", start)
        if not d:
            return
        out = Path(d) / f"kuskovatost_foto_{time.strftime('%Y%m%d_%H%M%S')}"
        (out / "overlays").mkdir(parents=True, exist_ok=True)
        names = [n for n, _ in size_classes({str(s): 0 for s in SIEVES})]
        with open(out / "results.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["№", "фото", "область", "кусков", "масштаб, мм/пикс", "X10, мм", "X50, мм", "X80, мм",
                        "X90, мм", "d_ср, мм", "макс., мм", "мелочь (по площади), %", "мелочь учтена",
                        "не распознано, %"] + [f"{n}, %" for n in names])
            for p in done:
                s = p.result.stats
                i = self.photos.index(p) + 1

                def v(k):
                    if k in ("x10_mm", "x50_mm", "x80_mm", "x90_mm"):
                        return fmt_x(s, k)
                    return f"{s[k]:.1f}".replace(".", ",") if k in s else ""
                w.writerow([i, p.name, p.result.source, s.get("n", 0), v("mm_per_px"), v("x10_mm"), v("x50_mm"),
                            v("x80_mm"), v("x90_mm"), v("d_mean_mm"), v("d_max_mm"), v("fines_pct"),
                            "да" if s.get("fines_used") else "нет", v("unknown_pct")] +
                           [f"{c:.1f}".replace(".", ",") for _, c in p.result.classes])
                stem = f"{i:02d}_{Path(p.name).stem}"
                imwrite(out / "overlays" / f"{stem}_kuski.jpg", p.result.overlay, [cv2.IMWRITE_JPEG_QUALITY, 90])
        self.chart.fig.savefig(str(out / "granulometry.png"), dpi=130)
        self._status(f"Отчёт сохранён: {out}")


def log_file() -> Path:
    """Журнал logs/foto.log рядом с программой; если папка только для чтения
    (например, Program Files) — в %LOCALAPPDATA%/KuskovatostFoto/logs."""
    for d in (Path(__file__).resolve().parent.parent / "logs",
              Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "KuskovatostFoto" / "logs"):
        try:
            d.mkdir(parents=True, exist_ok=True)
            f = d / "foto.log"
            with open(f, "a", encoding="utf-8"):
                pass
            return f
        except OSError:
            continue
    return Path(os.devnull)


def setup_logging() -> Path:
    path = log_file()
    if sys.stderr is None or sys.stdout is None:
        # запуск без консоли (pythonw, ярлык): сообщения и ошибки — в журнал
        f = open(path, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = f
    print(f"--- запуск {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return path


def install_excepthook(log: Path):
    """Ошибка в обработчике кнопки не закрывает окно: пишется в журнал и показывается."""
    def hook(tp, val, tb):
        traceback.print_exception(tp, val, tb)
        try:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(None, "Ошибка", f"{tp.__name__}: {val}\n\nПодробности в журнале:\n{log}")
        except Exception:      # noqa: BLE001
            pass
    sys.excepthook = hook


def make_app() -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Кусковатость породы по фото")
    # русские подписи стандартных кнопок (OK/Отмена) в диалогах Qt
    tr = QTranslator(app)
    if tr.load(QLocale("ru_RU"), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(tr)
    icon = Path(__file__).resolve().parent / "icon.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    return app


def main(argv=None, splash=None):
    argv = sys.argv[1:] if argv is None else argv
    log = setup_logging()
    install_excepthook(log)
    app = make_app()
    win = MainWindow([a for a in argv if Path(a).exists()])
    win.showMaximized()
    if splash is not None:
        splash.finish(win)
    from PySide6.QtCore import QTimer
    from .shortcut import offer
    QTimer.singleShot(800, lambda: offer(win))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
