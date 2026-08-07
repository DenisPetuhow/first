# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Переиспользуемый слой ТАЙЛОВОЙ ПОДЛОЖКИ для карт на pyqtgraph.

Выделен из логики вкладки 2 (view_qt/area_view.py), чтобы вкладка 3 (цифровая карта
угроз) использовала тот же тайловый слой, но со СВОИМ якорем (lon0/lat0 демо-участка)
и с ЖЁСТКИМ ограничением обзора пределами bbox (setLimits) — карту нельзя
утащить/отдалить за границы участка.

Здесь же применяется исправление размытия карты: подложка собирается на зуме, при
котором мозаика тайлов НЕ мельче виджета (см. geomap._pick_zoom), а ImageItem
включает autoDownsample — чёткое уменьшение вместо мыльного растяжения.

Класс-миксин рассчитан на носителя с атрибутами self.plot/self.pi/self.vb
(pyqtgraph PlotWidget/PlotItem/ViewBox) и методами-геттерами текущего слоя/офлайна.
"""
import numpy as np
from pyqtgraph.Qt import QtCore
import pyqtgraph as pg

from config import THEME
from . import geomap as gm


class _BasemapSignals(QtCore.QObject):
    done = QtCore.pyqtSignal(object)          # (req_id, layer, res|None)


class _BasemapTask(QtCore.QRunnable):
    """Сборка растровой подложки в ФОНОВОМ потоке (чтение/декод/склейка тайлов),
    чтобы интерфейс не подвисал при панораме/зуме/смене слоя."""

    def __init__(self, req_id, layer, box, target_px, allow_net, lon0, lat0, signals):
        super().__init__()
        self._req = req_id; self._layer = layer; self._box = box
        self._target_px = target_px; self._allow_net = allow_net
        self._lon0 = lon0; self._lat0 = lat0; self._sig = signals

    def run(self):
        try:
            kx0, kx1, ky0, ky1 = self._box
            res = gm.build_raster_basemap(kx0, kx1, ky0, ky1, layer=self._layer,
                                          allow_net=self._allow_net,
                                          target_px=self._target_px,
                                          lon0=self._lon0, lat0=self._lat0)
        except Exception:
            res = None
        try:
            self._sig.done.emit((self._req, self._layer, res))
        except RuntimeError:
            pass                                  # окно закрыто во время сборки


class BasemapMixin:
    """Подмешивается к QWidget-представлению карты. Ожидает self.plot/self.pi/self.vb."""

    # ГАШЕНИЕ ПОДЛОЖКИ (насыщенность, осветление): 0, 0 — оставить как есть.
    # Нужно там, где поверх карты лежат СВОИ слои того же смысла: OSM рисует свои дороги,
    # воду и лес, мы кладём сверху свои — оранжевое на оранжевом, синее на синем, и обе
    # картинки борются за внимание. Выцветшая подложка остаётся контекстом («тут посёлок,
    # тут лес»), но перестаёт спорить с оверлеями. Подклассы переопределяют по месту:
    # вкладке 2 гашение не нужно, у неё поверх карты почти ничего нет.
    BASEMAP_FADE = (0.0, 0.0)

    def _init_basemap(self, lon0, lat0, get_layer, get_offline, scheme_points=None,
                      step_deg=0.2):
        """Инициализация тайловой подложки и офлайн-схемы.

        lon0/lat0 — якорь км-фрейма; get_layer/get_offline — колбэки текущего слоя и
        офлайн-флага (инверсия зависимостей: миксин не знает, откуда их брать);
        scheme_points — список (имя, lon, lat) ориентиров для схемы; step_deg — шаг
        сетки широт/долгот. Подклассы могут доопределить `_draw_scheme_extra` и
        `_set_scheme_extra_visible` (напр. вкладка 2 — рамка области)."""
        self._geo_lon0 = float(lon0); self._geo_lat0 = float(lat0)
        self._get_layer = get_layer            # callable -> ключ слоя
        self._get_offline = get_offline        # callable -> bool
        self._scheme_step_deg = float(step_deg)
        self._map_req = 0

        self.basemap = pg.ImageItem(); self.basemap.setZValue(-20)
        # Чёткость обеспечивает _pick_zoom (мозаика ≥ экрана) + гладкое масштабирование
        # изображения Qt. autoDownsample НЕ включаем: он пересчитывает картинку на КАЖДЫЙ
        # зум (numpy на CPU) и заметно тормозит интерактив на плотной карте.
        self.basemap.setOpts(axisOrder="row-major")
        self.basemap.setVisible(False)
        self.pi.addItem(self.basemap)

        self.graticule = self.pi.plot([], [], pen=pg.mkPen(gm_qcolor(THEME["grid"], 150),
                                                           width=1.0))
        self.graticule.setZValue(-15)
        self._scheme_pts = list(scheme_points or [])
        self._scheme_labels = []
        for name, lon, lat in self._scheme_pts:
            x, y = gm.lonlat_to_km(lon, lat, self._geo_lon0, self._geo_lat0)
            t = pg.TextItem(name, color=THEME["muted"], anchor=(0.5, 1.2))
            t.setPos(float(x), float(y)); t.setZValue(-12); t.setVisible(False)
            self.pi.addItem(t); self._scheme_labels.append((t, float(x), float(y)))
        self._scheme_scatter = pg.ScatterPlotItem(
            size=7, symbol="o", brush=pg.mkBrush(gm_qcolor(THEME["text"], 200)),
            pen=pg.mkPen(gm_qcolor(THEME["accent"]), width=0.8))
        self._scheme_scatter.setZValue(-13); self.pi.addItem(self._scheme_scatter)

        self._map_pool = QtCore.QThreadPool.globalInstance()
        self._map_signals = _BasemapSignals()
        self._map_signals.done.connect(self._on_basemap_ready)
        self._map_timer = QtCore.QTimer(self); self._map_timer.setSingleShot(True)
        self._map_timer.timeout.connect(lambda: self._refresh_basemap())
        self.vb.sigRangeChanged.connect(lambda *a: self._map_timer.start(180))

    def _set_view_limits(self, bbox_km, out_margin=1.4):
        """Предел пана/зума. out_margin>1 даёт «ещё один зум мельче»: можно отдалить так,
        что ВЕСЬ прямоугольник участка виден сразу (по краям — чёрные поля, т.к. тайлы
        есть только внутри bbox). При жёстком пределе ==bbox широкий экран из-за фиксации
        соотношения сторон подрезал участок сверху/снизу — теперь помещается целиком."""
        kx0, kx1, ky0, ky1 = bbox_km
        cx, cy = 0.5 * (kx0 + kx1), 0.5 * (ky0 + ky1)
        w = (kx1 - kx0) * out_margin; h = (ky1 - ky0) * out_margin
        self.vb.setLimits(xMin=cx - 0.5 * w, xMax=cx + 0.5 * w,
                          yMin=cy - 0.5 * h, yMax=cy + 0.5 * h,
                          maxXRange=w, maxYRange=h,
                          minXRange=(kx1 - kx0) / 60.0,
                          minYRange=(ky1 - ky0) / 60.0)

    def _refresh_basemap(self, force=False):
        # ИЗВЕСТНОЕ ОГРАНИЧЕНИЕ (вкладка 2): при некоторых промежуточных масштабах
        # (вид попадает МЕЖДУ уровнями зума) подложка бывает слегка мутноватой, плюс
        # кратковременно во время самого жеста панорамы/зума (мозаика пересобирается
        # по дебаунсу 180 мс). Радикальное решение — послойная отрисовка тайлов
        # отдельными QGraphicsPixmapItem (как slippy-map) или веб-виджет с Leaflet;
        # оставлено на будущее (см. МЕТОДИЧКА_ВКЛАДКА3_ДОРАБОТКИ.md, §4). TODO(map-blur).
        try:
            (kx0, kx1), (ky0, ky1) = self.vb.viewRange()
        except Exception:
            return
        if not np.isfinite([kx0, kx1, ky0, ky1]).all():
            return
        layer = self._get_layer()
        if layer == "scheme":
            self.basemap.setVisible(False)
            self._draw_scheme(kx0, kx1, ky0, ky1)
            return
        self._set_scheme_visible(False)
        self._map_req += 1
        # ЛОГИЧЕСКАЯ ширина (без DPR): даёт зум ~1:1 и родной размер подписей, как в
        # обычном OSM. С домножением на DPR брался зум на уровень выше -> подписи мельче.
        target_px = max(self.plot.width(), 256)
        allow_net = not self._get_offline()
        self._map_pool.start(_BasemapTask(self._map_req, layer,
                                          (kx0, kx1, ky0, ky1), target_px, allow_net,
                                          self._geo_lon0, self._geo_lat0,
                                          self._map_signals))

    def _on_basemap_ready(self, payload):
        req_id, layer, res = payload
        if req_id != self._map_req or layer != self._get_layer():
            return
        if res is None:
            try:
                (kx0, kx1), (ky0, ky1) = self.vb.viewRange()
                self.basemap.setVisible(False)
                self._draw_scheme(kx0, kx1, ky0, ky1)
            except Exception:
                pass
            return
        img8, (ex0, ex1, ey0, ey1) = res
        self.basemap.setImage(self._fade_basemap(img8), autoLevels=False)
        self.basemap.setRect(QtCore.QRectF(ex0, ey0, ex1 - ex0, ey1 - ey0))
        self.basemap.setVisible(True)

    def _fade_basemap(self, img):
        """Обесцветить и осветлить тайлы — «выцветшая» подложка под своими слоями.

        Именно ПИКСЕЛИ, а не прозрачность элемента: полупрозрачная подложка на тёмном
        фоне приложения ушла бы в грязно-тёмное, а нам нужен светлый бледный фон, на
        котором читаются и линии, и заливки. Сначала тянем к серому (убираем спор цветов),
        потом к белому (убираем спор яркостей)."""
        sat, light = self.BASEMAP_FADE
        if sat <= 0.0 and light <= 0.0:
            return img
        a = np.asarray(img)
        if a.ndim != 3 or a.shape[2] < 3:
            return img
        out = a[..., :3].astype(np.float32)
        if sat > 0.0:
            grey = out.mean(axis=2, keepdims=True)
            out += (grey - out) * float(np.clip(sat, 0.0, 1.0))
        if light > 0.0:
            out += (255.0 - out) * float(np.clip(light, 0.0, 1.0))
        res = a.copy()
        res[..., :3] = np.clip(out, 0, 255).astype(a.dtype)
        return res

    def _set_scheme_visible(self, vis):
        self.graticule.setVisible(vis); self._scheme_scatter.setVisible(vis)
        for t, _x, _y in self._scheme_labels:
            t.setVisible(vis)
        self._set_scheme_extra_visible(vis)

    def _draw_scheme(self, kx0, kx1, ky0, ky1):
        xs, ys = [], []
        for kind, val, _lab in gm.graticule_km(kx0, kx1, ky0, ky1,
                                               self._geo_lon0, self._geo_lat0,
                                               step_deg=self._scheme_step_deg):
            if kind == "v":
                xs += [val, val, np.nan]; ys += [ky0, ky1, np.nan]
            else:
                xs += [kx0, kx1, np.nan]; ys += [val, val, np.nan]
        self.graticule.setData(xs, ys)
        if self._scheme_labels:
            self._scheme_scatter.setData([x for _t, x, _y in self._scheme_labels],
                                         [y for _t, _x, y in self._scheme_labels])
        self._draw_scheme_extra(kx0, kx1, ky0, ky1)
        self._set_scheme_visible(True)

    # хуки для подклассов (по умолчанию — пусто); напр. вкладка 2 рисует рамку области
    def _draw_scheme_extra(self, kx0, kx1, ky0, ky1):
        pass

    def _set_scheme_extra_visible(self, vis):
        pass


def gm_qcolor(hex_or_rgba, alpha=None):
    c = pg.mkColor(hex_or_rgba)
    if alpha is not None:
        c.setAlpha(int(alpha))
    return c
