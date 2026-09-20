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
import os

import numpy as np
from pyqtgraph.Qt import QtCore
import pyqtgraph as pg

from config import THEME
from . import geomap as gm
from .tile_layer import TileLayer, set_span_x   # подложка поштучными тайлами (10.10)

# ⚠️ ВОЗВРАТ К СТАРОМУ ПУТИ — на случай, если поштучная укладка себя не покажет.
# UAV_TILES_MOSAIC=1 возвращает склейку всей подложки в одну картинку (как было до
# 13.09.2026). Держится до тех пор, пока новый путь не обкатан; потом убрать вместе с
# `_BasemapTask` и `build_raster_basemap`.
TILES_MOSAIC = os.environ.get("UAV_TILES_MOSAIC", "0") == "1"

# ПРИВЯЗКА МАСШТАБА К УРОВНЯМ ТАЙЛОВ — то, чем браузерная карта отличается от нашей.
# Тайлы существуют только на целых зумах, каждый следующий вдвое подробнее. Пока зум
# вида плавный, тайл почти всегда приходится масштабировать: замер 13.09.2026 —
# масштаб 1:1 лишь в 23 % случаев, в остальных от 0.71 до 1.41. Любой такой множитель
# портит подписи, потому что буквы на тайле нарисованы попиксельно.
# Слиппи-карты (Leaflet, OSM в браузере) решают это `zoomSnap`: после жеста вид
# подтягивается к ближайшему уровню, и тайл всегда рисуется пиксель в пиксель.
# UAV_ZOOM_SNAP=0 — выключить и вернуть полностью плавный зум.
ZOOM_SNAP = os.environ.get("UAV_ZOOM_SNAP", "1") == "1"
# Насколько близко к 1:1 считаем «уже достаточно» — чтобы не дёргать вид из-за мелочи
# и не зациклить подгонку (она сама вызывает сигнал об изменении вида).
SNAP_TOL = 0.02


class _BasemapSignals(QtCore.QObject):
    done = QtCore.pyqtSignal(object)          # (req_id, layer, res|None)


class _BasemapTask(QtCore.QRunnable):
    """Сборка растровой подложки в ФОНОВОМ потоке (чтение/декод/склейка тайлов),
    чтобы интерфейс не подвисал при панораме/зуме/смене слоя."""

    def __init__(self, req_id, layer, box, target_px, allow_net, lon0, lat0, signals,
                 area=None):
        super().__init__()
        self._req = req_id; self._layer = layer; self._box = box
        self._target_px = target_px; self._allow_net = allow_net
        self._lon0 = lon0; self._lat0 = lat0; self._sig = signals
        self._area = area

    def run(self):
        try:
            kx0, kx1, ky0, ky1 = self._box
            res = gm.build_raster_basemap(kx0, kx1, ky0, ky1, layer=self._layer,
                                          allow_net=self._allow_net,
                                          target_px=self._target_px,
                                          lon0=self._lon0, lat0=self._lat0,
                                          area=self._area)
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
                      step_deg=0.2, tile_area=None):
        """Инициализация тайловой подложки и офлайн-схемы.

        lon0/lat0 — якорь км-фрейма; get_layer/get_offline — колбэки текущего слоя и
        офлайн-флага (инверсия зависимостей: миксин не знает, откуда их брать);
        scheme_points — список (имя, lon, lat) ориентиров для схемы; step_deg — шаг
        сетки широт/долгот. Подклассы могут доопределить `_draw_scheme_extra` и
        `_set_scheme_extra_visible` (напр. вкладка 2 — рамка области).

        tile_area — ПАПКА УЧАСТКА в кэше тайлов (ОГРАНИЧЕНИЯ 5.1д). Вкладка 3 передаёт
        имя своего участка, вкладка 2 не передаёт ничего и работает со старой общей
        раскладкой: у неё свой район (Курск) и свои уже скачанные тайлы."""
        self._geo_lon0 = float(lon0); self._geo_lat0 = float(lat0)
        self._tile_area = tile_area
        self._get_layer = get_layer            # callable -> ключ слоя
        self._get_offline = get_offline        # callable -> bool
        self._scheme_step_deg = float(step_deg)
        self._map_req = 0

        # ⚠️ СГЛАЖИВАНИЕ ЖИВЁТ У САМОГО ТАЙЛА (`tile_layer._TileImage`), а НЕ у виджета.
        # Включённое на весь виджет, оно размыло весовую карту: её ячейки 500 м должны
        # читаться квадратиками — так видно сетку расчёта (заказчик 13.09.2026).
        self.basemap = pg.ImageItem(); self.basemap.setZValue(-20)
        # Чёткость обеспечивает _pick_zoom (мозаика ≥ экрана) + гладкое масштабирование
        # изображения Qt. autoDownsample НЕ включаем: он пересчитывает картинку на КАЖДЫЙ
        # зум (numpy на CPU) и заметно тормозит интерактив на плотной карте.
        self.basemap.setOpts(axisOrder="row-major")
        self.basemap.setVisible(False)
        self.pi.addItem(self.basemap)
        # ПОШТУЧНЫЕ ТАЙЛЫ (задача 10.10). При старом пути слоя нет вовсе, и `self.basemap`
        # работает как прежде — одной картинкой на всё окно.
        self._tiles = None if TILES_MOSAIC else TileLayer(
            self.pi, self._geo_lon0, self._geo_lat0, get_layer, get_offline,
            area=tile_area, fade_fn=self._fade_basemap)

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
        # ЗАДЕРЖКА ПЕРЕД ПЕРЕСЧЁТОМ. Старый путь пересобирал мозаику целиком, и 180 мс
        # были платой за то, чтобы не делать этого на каждом кадре жеста. Поштучным
        # тайлам столько не нужно: уже показанные остаются, доезжают только недостающие —
        # и 40 мс убирают ту самую задержку, из-за которой карта «догоняла» мышь.
        self._map_delay = 180 if self._tiles is None else 40
        self.vb.sigRangeChanged.connect(lambda *a: self._map_timer.start(self._map_delay))

    def _set_view_limits(self, bbox_km, pad_km=5.0, min_ref=None, max_span=None):
        """РАЙОН ОТОБРАЖЕНИЯ: рамка участка плюс `pad_km` километров поля вокруг.
        Дальше этого прямоугольника камеру не увести — за ним ни данных, ни тайлов.
        `min_ref` — от какой рамки считать предел ПРИБЛИЖЕНИЯ (по умолчанию от своей).

        ⚠️ ОГРАНИЧЕН РАЙОН, А НЕ МАСШТАБ. Прежде вместе с районом задавались `maxXRange`
        и `maxYRange` — предел «насколько мелко разрешено смотреть»; он мешал: при
        фиксированном соотношении сторон широкий экран упирался в предел по одной оси и
        подрезал участок по другой. Теперь ограничены только границы прямоугольника, а
        насколько отдалить вид — дело пользователя: дальше границ вид всё равно не уйдёт.

        ⚠️ ГРАНИЦЫ ДУШАТ КАДР ТАК ЖЕ, как душил `maxXRange` — это та же болезнь, вторая
        её половина. При `setAspectLocked` кадр на широком окне шире данных, и предел,
        посчитанный по самим данным, обрезает их по ДРУГОЙ оси: замер 12.09.2026 на окне
        1560×960 — область 249.6 км показывалась как 197, срезано по 26 км сверху и снизу.
        Поэтому тот, кто зовёт, обязан передать рамку с запасом на соотношение окна —
        см. `ThreatMapView._apply_view_limits`.

        Поле вокруг рамки нужно, чтобы граница участка не липла к краю окна: у самой
        рамки должно быть видно, что за ней ничего нет. `minXRange`/`minYRange` остаются —
        это предел ПРИБЛИЖЕНИЯ, без него колесо мыши уводит в бесконечный зум."""
        kx0, kx1, ky0, ky1 = bbox_km
        pad = max(0.0, float(pad_km))
        # предел приближения — от рамки ДАННЫХ, а не от границ: границы растут вместе с
        # окном, и приближение молча слабело бы на широком мониторе
        mx0, mx1, my0, my1 = min_ref if min_ref else (kx0, kx1, ky0, ky1)
        min_x, min_y = (mx1 - mx0) / 60.0, (my1 - my0) / 60.0
        # ⚠️ ПРЕДЕЛ ПРИБЛИЖЕНИЯ ДОЛЖЕН ПУСКАТЬ ДО САМЫХ ПОДРОБНЫХ ТАЙЛОВ. Доля участка
        # (1/60) для области 302 км даёт 5.04 км — а тайлы есть до зума 15, где вид
        # 1:1 занимает ~2.6 км. Вид упирался в предел, колесо продолжало крутиться, и
        # карта дрожала на месте (замечание заказчика «после 10 прокруток дёргается»).
        deep = self._deep_zoom_span_km()
        if deep:                                   # тайлы позволяют ближе, чем доля участка
            min_x = min(min_x, deep)               # берём то, что мельче
            min_y = min(min_y, deep * (my1 - my0) / max(mx1 - mx0, 1e-9))
        # ⚠️ ОТДАЛЕНИЕ УПИРАЕТСЯ В УРОВЕНЬ, НА КОТОРОМ ОБЛАСТЬ УЖЕ ВИДНА ЦЕЛИКОМ (заказчик
        # 19.09.2026: «максимальный какой уровень загружается изначально — выше него не
        # уходит»). Без этого щелчок «за последний уровень» не менял тайлы, а РАСТЯГИВАЛ
        # вид до границ: 435.3 → 449.9 км на тех же тайлах z = 8, масштаб 1.000 → 0.97, и
        # дальше он не менялся. `max_span` приходит от `_zoom_out_frame` — это ширина вида
        # 1:1 на том самом уровне, поэтому предел не «дышит» вместе с формой окна.
        max_x = float(max_span) if max_span else (kx1 - kx0)     # предел отдаления по X, км
        max_y = max_x * (ky1 - ky0) / max(kx1 - kx0, 1e-9)       # по Y — в форме кадра
        self.vb.setLimits(xMin=kx0 - pad, xMax=kx1 + pad,
                          yMin=ky0 - pad, yMax=ky1 + pad,
                          minXRange=min_x, minYRange=min_y,
                          maxXRange=max_x, maxYRange=max_y)

    def _map_px(self):
        # Ширина карты в ФИЗИЧЕСКИХ пикселях — от неё считается уровень тайлов.
        # Отдаёт: px (float), не меньше 256.
        # ⚠️ Qt раскладывает виджеты в логических, а рисует в физических: при масштабе
        # Windows 125 % тайл на 256 логических растянут в 1.25 раза и мылит. При dpr = 1
        # значение прежнее — поведение обычного монитора не меняется.
        try:
            w = float(self.plot.width())
            dpr = float(self.plot.devicePixelRatioF())    # 1.0 · 1.25 · 1.5 · 2.0 …
        except Exception:                                # виджет ещё не разложен
            return 256.0
        return max(w * (dpr if dpr > 0 else 1.0), 256.0)

    def _report_tiles(self, z, x_range, tiles):
        # Строка подложки над картой: уровень, число тайлов, масштаб отрисовки.
        # Вход: уровень (или None), края вида по X (км), слой тайлов. Отдаёт: ничего.
        # ⚠️ Масштаб — главное число: экранных пикселей на пиксель тайла. 1.00 — резко,
        # больше — растянут (мыло), меньше — ужат (подписи мельчают).
        show = getattr(self, "set_tiles_info", None)
        if show is None:                              # у вкладки нет такой строки
            return                                    # — молча, это не ошибка
        if not z:                                     # тайлов нет: схема, своя карта, пусто
            show("подложка: —")
            return
        try:
            span_km = float(x_range[1] - x_range[0])
            w_px = self._map_px()                     # та же мера, что при выборе уровня
            km_per_deg = gm._km_per_deg_lon(self._geo_lat0)
            nx = span_km / km_per_deg / 360.0 * (2.0 ** z)     # тайлов по ширине вида
            scale = w_px / max(nx * 256.0, 1e-9)               # экранных px на px тайла
        except Exception:                             # виджет ещё не разложен
            show("подложка: уровень %d" % z)
            return
        keys = sorted(k for k in getattr(tiles, "_items", {}) if k[0] == z)   # (z,x,y) уровня
        mark = "1:1" if 0.99 <= scale <= 1.01 else ("растянут" if scale > 1 else "ужат")
        if keys:
            # ⚠️ Порядок — от первого к последнему по (x, y): по нему видно, тот ли кусок
            # карты на экране и нет ли дыр в ряду (заказчик 19.09.2026).
            xs = [k[1] for k in keys]; ys = [k[2] for k in keys]
            where = ("x %d–%d · y %d–%d · первый %d/%d/%d … последний %d/%d/%d"
                     % (min(xs), max(xs), min(ys), max(ys),
                        keys[0][0], keys[0][1], keys[0][2],
                        keys[-1][0], keys[-1][1], keys[-1][2]))
        else:                                         # тайлы ещё едут из потока
            where = "тайлы грузятся"
        show("подложка: z = %d · тайлов %d · %s · масштаб %.2f (%s) · вид %.1f км · %s"
             % (z, len(keys), where, scale, mark, span_km,
                "офлайн" if self._get_offline() else "сеть"))

    def _deep_zoom_span_km(self):
        # Самый узкий осмысленный вид: тайлы предельного зума, растянутые вдвое.
        # Отдаёт: ширину вида в км либо None, если считать не из чего.
        try:
            width_px = self._map_px()
            km_per_deg = gm._km_per_deg_lon(self._geo_lat0)
        except Exception:                          # виджет ещё не разложен
            return None
        one_to_one = (width_px / 256.0) / (2.0 ** gm.ZOOM_MAX) * 360.0 * km_per_deg
        return one_to_one * 0.5                    # разрешаем приблизиться ещё вдвое

    def _refresh_basemap(self, force=False):
        # ИЗВЕСТНОЕ ОГРАНИЧЕНИЕ (вкладка 2): при некоторых промежуточных масштабах
        # (вид попадает МЕЖДУ уровнями зума) подложка бывает слегка мутноватой, плюс
        # кратковременно во время самого жеста панорамы/зума (мозаика пересобирается
        # по дебаунсу 180 мс). Радикальное решение — послойная отрисовка тайлов
        # отдельными QGraphicsPixmapItem (как slippy-map) или веб-виджет с Leaflet;
        # оставлено на будущее (см. МЕТОДИЧКА_ВКЛАДКА3_ДОРАБОТКИ.md, §4). TODO(map-blur).
        # РЕЖИМ «ТОЛЬКО СВОЯ КАРТА» (план 8, задача 8.5): подложка не собирается вовсе.
        # Флаг проверяется здесь, а не в месте включения, потому что перерисовка идёт по
        # сигналу изменения вида — иначе тайлы вернулись бы при первом же движении мыши.
        if getattr(self, "_basemap_off", False):
            self.basemap.setVisible(False)
            tiles = getattr(self, "_tiles", None)
            if tiles is not None:
                tiles.set_visible(False)
            self._set_scheme_visible(False)
            return
        try:
            (kx0, kx1), (ky0, ky1) = self.vb.viewRange()
        except Exception:
            return
        if not np.isfinite([kx0, kx1, ky0, ky1]).all():
            return
        layer = self._get_layer()
        if layer == "scheme":                         # тайлов нет — рисуем векторную схему
            self.basemap.setVisible(False)
            tiles = getattr(self, "_tiles", None)
            if tiles is not None:
                tiles.set_visible(False)
            self._draw_scheme(kx0, kx1, ky0, ky1)
            return
        self._set_scheme_visible(False)
        # ЛОГИЧЕСКАЯ ширина (без DPR) — см. ниже, у старого пути причина та же.
        tiles = getattr(self, "_tiles", None)
        if tiles is not None:                         # новый путь: каждый тайл сам по себе
            if layer != getattr(self, "_tiles_layer", None):   # слой сменили — старые не годятся
                tiles.clear()                                  # чужие картинки убрать разом
                self._tiles_layer = layer
            tiles.set_visible(True)
            z = tiles.set_view((kx0, kx1, ky0, ky1), self._map_px())
            self._report_tiles(z, (kx0, kx1), tiles)
            return
        self._map_req += 1
        # ЛОГИЧЕСКАЯ ширина (без DPR): даёт зум ~1:1 и родной размер подписей, как в
        # обычном OSM. С домножением на DPR брался зум на уровень выше -> подписи мельче.
        target_px = self._map_px()
        allow_net = not self._get_offline()
        self._map_pool.start(_BasemapTask(self._map_req, layer,
                                          (kx0, kx1, ky0, ky1), target_px, allow_net,
                                          self._geo_lon0, self._geo_lat0,
                                          self._map_signals,
                                          getattr(self, "_tile_area", None)))

    def snap_view_to_zoom(self):
        """Подогнать масштаб вида так, чтобы тайлы рисовались ПИКСЕЛЬ В ПИКСЕЛЬ.

        Зовётся ПОСЛЕ вписывания (`frame_bbox`, «Весь участок»), а не на каждое движение:
        колесо и так ходит ровно по уровням (`SteppedViewBox`), а вписывание даёт
        произвольный масштаб — вот его и приводим к уровню.

        ⚠️ ОХВАТ ТОЛЬКО РАСТЁТ. Уменьшить его — значит обрезать то, что только что
        вписали: чёрная рамка области перестала бы помещаться целиком. Поэтому берётся
        уровень, при котором вид не уже вписанного, и картинка выходит крупнее не более
        чем вдвое."""
        if not ZOOM_SNAP or getattr(self, "_tiles", None) is None:
            return
        try:
            (kx0, kx1), _ = self.vb.viewRange()
        except Exception:
            return
        span_km = float(kx1 - kx0)
        width_px = self._map_px()
        if span_km <= 0:
            return
        km_per_deg = gm._km_per_deg_lon(self._geo_lat0)
        # ⚠️ ОТ КРУПНОГО ЗУМА К МЕЛКОМУ, и берётся первый уровень НЕ УЖЕ вписанного. До
        # 13.09.2026 цикл шёл от мелкого к крупному и брал первый «не шире» — то есть
        # СУЖАЛ вид: область arh 302 км в окне 1142 px вписывалась в 320 км, а привязка
        # сжимала её до 165 км (z9 вместо z8), и чёрная рамка при запуске обрезалась.
        for z in range(gm.ZOOM_MAX, gm.ZOOM_MIN - 1, -1):
            # ширина вида, при которой тайлов ровно столько, сколько «окон» по 256 px
            want_km = (width_px / 256.0) / (2.0 ** z) * 360.0 * km_per_deg
            if want_km >= span_km * (1.0 - SNAP_TOL):  # первый уровень, что не уже вписанного
                cx = (kx0 + kx1) * 0.5                 # центр держим, двигаем только края
                set_span_x(self.vb, (cx - want_km * 0.5, cx + want_km * 0.5))
                return
        # вписанное шире самого мелкого уровня — привязывать не к чему, оставляем как есть

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
