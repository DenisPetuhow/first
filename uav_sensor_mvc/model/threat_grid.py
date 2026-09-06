# -*- coding: utf-8 -*-
"""
MODEL · Весовая карта угроз (вкладка 3).

Задача: по фиксированному участку местности построить регулярную сетку 500×500 м
и «взвешенным наложением» цифровых слоёв (реки, дороги, ж/д, ЛЭП, трубопроводы,
лесополосы, мосты, застройка) получить единую карту «привлекательности» территории
для маршрута БПЛА. По этой карте затем расставляются датчики (см. optimization.py,
CoverageCache.greedy_weighted).

ЧТО ЗДЕСЬ ЕСТЬ:
  * ThreatGrid — сетка ячеек + операции наложения слоёв (ядро на чистом numpy, без
    geopandas: длина пересечения линии с ячейкой считается точным разбиением отрезка
    по линиям сетки — см. _accumulate_polyline);
  * load_layers() — источник геоданных: реальные слои OSM из офлайн-кэша (geo_cache/
    *.npz, подготовленные tools/build_threat_grid.py на pyrosm/geopandas) ИЛИ, если
    их нет, синтетическая СХЕМА (демо-река + опорные дороги/ЛЭП/ж-д),
    чтобы вкладка работала офлайн без внешних данных;
  * ThreatModel — обёртка для контроллера вкладки 3: строит карту, держит слои для
    отрисовки, готовит маску запрета датчиков на воде, считает показатели.

МЕХАНИКА ВЕСА (см. теория/МЕТОДИЧКА_КАРТА_УГРОЗ.md, разделы 3–4):
  вклад линии слоя в ячейку = вес_слоя × (длина пересечения / размер ячейки).
  Так вклад пропорционален тому, НАСКОЛЬКО объект проходит через ячейку (а не факту
  «задел/не задел»), и сопоставим между слоями по масштабу, а не по частоте данных.

ДОКУМЕНТАЦИЯ (почему так, а не как устроено — это здесь в комментариях):
  теория/вкладка_3/МЕТОДИЧКА_КАРТА_УГРОЗ.md — §3.3 формула веса, §4 итог, §4.5 рельеф,
                                              §13 населённые пункты
  теория/ОГРАНИЧЕНИЯ.md — §3 города и деревни, §5 данные и карта, §6 эталон прогона

Карта кода — теория/карта_кода/README.md (генерируется codemap.py).
"""
import os
import re
import json
import math
import threading

import numpy as np

from config import (THREAT_BBOX_LONLAT, THREAT_WORK_LONLAT, THREAT_CELL_M, THREAT_LAYERS,
                    THREAT_LAYERS_FILE, THREAT_AREA_DIR, THREAT_SOURCE_FILE,
                    THREAT_LAYER_ORDER, THREAT_INTERSECTION_BONUS,
                    THREAT_WATER_BUFFER_M, THREAT_ENTRY, THREAT_TARGET,
                    THREAT_URBAN_MIN_AREA_KM2, THREAT_URBAN_BUFFER_KM,
                    THREAT_TOWN_CITY_KM2, THREAT_CITY_ZERO_WEIGHT,
                    THREAT_VILLAGE_PENALTY_CORE, THREAT_VILLAGE_PENALTY_EDGE,
                    THREAT_VILLAGE_RADIUS_MIN_KM, THREAT_VILLAGE_RADIUS_MAX_KM,
                    THREAT_VILLAGE_FLOOR, THREAT_VILLAGE_SKIP_FACTOR,
                    THREAT_VILLAGE_TOWNLIKE_MED, THREAT_RIVER_CORRIDOR_MAX_KM2,
                    THREAT_PLACE_GRADE_ON, THREAT_PLACE_GRADE_TOL,
                    THREAT_PLACE_GRADE_MIN_SAMPLE, THREAT_PLACE_CLOSE_KM,
                    THREAT_PLACE_GRADE_OUTLIER, THREAT_PLACE_GRADE_STRICT_UP,
                    THREAT_PLACE_GRADE_MODE,
                    THREAT_DEM_FILE, THREAT_DEM_ON_START,
                    THREAT_DEM_AREA_KM, THREAT_DEM_LOCAL_KM,
                    THREAT_DEM_AREA_FULL_M, THREAT_DEM_LOCAL_FULL_M,
                    THREAT_DEM_AREA_S, THREAT_DEM_LOCAL_S,
                    THREAT_DEM_K_MIN, THREAT_DEM_K_MAX, THREAT_DEM_FLOOR,
                    THREAT_DEM_CUT_AREA_M, THREAT_DEM_CUT_LOCAL_M,
                    THREAT_ENTRY_FREE_KM, THREAT_TARGET_FREE_KM,
                    THREAT_LMAX_AUTO_FRAC, THREAT_LMAX_CORRIDOR_FRAC,
                    THREAT_SECTOR_HALF_DEG, THREAT_SECTOR_ENTRIES,
                    THREAT_SECTOR_EDGE_KM, THREAT_SECTOR_WIN_ALONG,
                    THREAT_SECTOR_WIN_ACROSS, THREAT_SECTOR_MIN_SEP_KM,
                    THREAT_SECTOR_MIN_ANGLE_FRAC, THREAT_LAYER_MARGIN_KM,
                    THREAT_COMPASS_STEP_DEG, THREAT_COMPASS_APPROACH_KM)
# кольцо и угловой разнос БОЛЬШИХ датчиков — поля Params (правятся в окне «Датчики»),
# поэтому берутся оттуда; здесь только умолчания на случай старого Params
from config import Params as _P
THREAT_BIG_RING_LO = getattr(_P, "threat_big_ring_lo", 0.55)
THREAT_BIG_RING_HI = getattr(_P, "threat_big_ring_hi", 1.0)
THREAT_BIG_ANGLE_TOL = getattr(_P, "threat_big_angle_tol", 0.55)


# ----------------------------------------------------------------------
# Локальная проекция км<->lon/lat (та же формула и якорь, что во view_qt/geomap.py,
# но без импорта view — модель не должна зависеть от слоя отображения). Совпадение
# формул гарантирует, что векторные слои лягут ровно на тайловую подложку вкладки 3.
# ----------------------------------------------------------------------
_KM_PER_DEG_LAT = 110.574


# ПЕРЕВОД ГРАДУСОВ В КИЛОМЕТРЫ ЖИВЁТ В `model/geo_frame.py` — одна формула на весь
# проект (план 8, задача 8.3). Раньше он был здесь и, ОТДЕЛЬНОЙ КОПИЕЙ, в
# `view_qt/geomap.py`: формулы совпадали, но правка одной не доходила до другой, а
# расхождение проявилось бы не ошибкой, а тихим сдвигом карты относительно данных.
# Имена ниже оставлены прежними — на них ссылается весь остальной код и документация.
from .geo_frame import (km_per_deg_lon as _km_per_deg_lon,   # noqa: E402
                        lonlat_to_km, bbox_lonlat_to_km)


def bbox_anchor_lonlat():
    """Якорь локального км-фрейма вкладки 3 = ЮГО-ЗАПАДНЫЙ УГОЛ участка.

    Отсчёт идёт от левого нижнего угла рамки: x — на восток, y — на север, обе
    координаты ПОЛОЖИТЕЛЬНЫЕ, от 0 до размера участка (≈95 × 55 км). Так подпись на оси
    прямо отвечает на вопрос «сколько километров от края участка», а расстояние между
    двумя точками читается вычитанием без оглядки на знак.

    Прежде якорем был ЦЕНТР bbox, и координаты шли от −47 до +47: у любой точки половина
    участка имела минус, а «−12.4 км» ни о чём не говорит, пока не вспомнишь, что ноль
    посередине. На саму математику якорь не влияет (все расчёты — в разностях), это
    вопрос читаемости.

    ⚠️ ПРИ СМЕНЕ ЯКОРЯ КЭШ СЛОЁВ `.npz` НУЖНО ПЕРЕСОБРАТЬ: в нём лежат уже пересчитанные
    километры, и слои, собранные при другом якоре, окажутся сдвинуты на пол-участка
    (`python tools/build_threat_grid.py`)."""
    lo, la, ho, ha = THREAT_BBOX_LONLAT
    return (lo, la)


def geo_cache_root():
    """Каталог кэша ГЕОДАННЫХ (векторные слои весовой карты) — по аналогии с
    tile_cache/ для тайлов. Переопределяется env UAV_GEO_CACHE."""
    env = os.environ.get("UAV_GEO_CACHE")
    if env:
        return env
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "geo_cache")


def area_cache_dir():
    """Папка АКТИВНОГО УЧАСТКА внутри кэша: `geo_cache/<THREAT_AREA_DIR>`.
    У участка без своей папки — сам корень кэша (прежняя раскладка)."""
    root = geo_cache_root()
    return os.path.join(root, THREAT_AREA_DIR) if THREAT_AREA_DIR else root


def area_file(name):
    """Путь к файлу участка по имени. Ищется СНАЧАЛА в папке участка, затем в корне
    кэша — так продолжают работать файлы, положенные в `geo_cache/` по старой
    раскладке. Если файла нет нигде, возвращается путь в папке участка: именно туда
    его и надо класть."""
    if not name:
        return ""
    in_area = os.path.join(area_cache_dir(), name)
    if os.path.exists(in_area):
        return in_area
    in_root = os.path.join(geo_cache_root(), name)
    return in_root if os.path.exists(in_root) else in_area


# ----------------------------------------------------------------------
# Ядро: точная длина пересечения ломаной с ячейками сетки
# ----------------------------------------------------------------------
def _accumulate_segment(x0, y0, x1, y1, ox, oy, h, nx, ny, out):
    """Добавить длину отрезка (x0,y0)->(x1,y1), попавшую в каждую ячейку, в out[iy,ix].

    Отрезок разбивается точками пересечения с линиями сетки (x=ox+k·h, y=oy+k·h);
    внутри каждого под-отрезка ячейка одна — её индекс берём по середине под-отрезка.
    Сумма добавленного = длине части отрезка внутри границ сетки (проверяется тестом).
    """
    dx = x1 - x0
    dy = y1 - y0
    seg = math.hypot(dx, dy)
    if seg < 1e-12:
        return
    ts = {0.0, 1.0}
    if dx != 0.0:
        kmin = math.ceil((min(x0, x1) - ox) / h)
        kmax = math.floor((max(x0, x1) - ox) / h)
        for k in range(kmin, kmax + 1):
            t = (ox + k * h - x0) / dx
            if 0.0 < t < 1.0:
                ts.add(t)
    if dy != 0.0:
        kmin = math.ceil((min(y0, y1) - oy) / h)
        kmax = math.floor((max(y0, y1) - oy) / h)
        for k in range(kmin, kmax + 1):
            t = (oy + k * h - y0) / dy
            if 0.0 < t < 1.0:
                ts.add(t)
    tl = sorted(ts)
    for a, b in zip(tl[:-1], tl[1:]):
        tm = 0.5 * (a + b)
        ix = int(math.floor((x0 + tm * dx - ox) / h))
        iy = int(math.floor((y0 + tm * dy - oy) / h))
        if 0 <= ix < nx and 0 <= iy < ny:
            out[iy, ix] += (b - a) * seg


def _accumulate_polyline(poly, ox, oy, h, nx, ny, out):
    """ТОЧНЫЙ (посегментный) вариант — эталон/для тестов. В рабочем пути (реальные
    объёмы OSM) используется быстрый ВЕКТОРНЫЙ `_rasterize_polylines`."""
    P = np.asarray(poly, float)
    for i in range(len(P) - 1):
        _accumulate_segment(P[i, 0], P[i, 1], P[i + 1, 0], P[i + 1, 1],
                            ox, oy, h, nx, ny, out)


def _rasterize_polylines(polylines, ox, oy, h, nx, ny, step_frac=0.4, chunk=200_000):
    """БЫСТРАЯ векторная растеризация: приближённая длина линий в каждой ячейке.

    Каждый сегмент дискретизируется точками с шагом ~step_frac·h, длина сегмента
    раскидывается по ячейкам его точек (`np.add.at`). Полностью на numpy — на порядки
    быстрее посегментного Python-обхода, поэтому выдерживает реальные объёмы OSM
    (миллионы сегментов). Приближение по длине (единицы %) для весовой карты
    несущественно (вклад всё равно нормируется на размер ячейки)."""
    out = np.zeros((ny, nx), float)
    flat = out.reshape(-1)
    segs = []
    for poly in polylines:
        P = np.asarray(poly, float)
        if P.ndim == 2 and len(P) >= 2 and P.shape[1] == 2:
            segs.append(np.concatenate([P[:-1], P[1:]], axis=1))   # (k,4): x0,y0,x1,y1
    if not segs:
        return out
    S = np.vstack(segs)
    dx = S[:, 2] - S[:, 0]; dy = S[:, 3] - S[:, 1]
    seglen = np.hypot(dx, dy)
    nsamp = np.clip(np.ceil(seglen / (step_frac * h)), 1, 1024).astype(np.int64)
    for a in range(0, len(S), chunk):
        b = min(a + chunk, len(S))
        _splat(S[a:b, 0], S[a:b, 1], dx[a:b], dy[a:b], seglen[a:b], nsamp[a:b],
               ox, oy, h, nx, ny, flat)
    return out


def _splat(x0, y0, dx, dy, seglen, nsamp, ox, oy, h, nx, ny, flat):
    """Раскидать длины сегментов по ячейкам (дискретизация + np.add.at). Внутренняя."""
    seg = np.repeat(np.arange(len(x0)), nsamp)
    starts = np.repeat(np.cumsum(nsamp) - nsamp, nsamp)
    frac = (np.arange(len(seg)) - starts + 0.5) / nsamp[seg]        # середина под-интервала
    xs = x0[seg] + frac * dx[seg]
    ys = y0[seg] + frac * dy[seg]
    clen = seglen[seg] / nsamp[seg]
    ix = np.floor((xs - ox) / h).astype(np.int64)
    iy = np.floor((ys - oy) / h).astype(np.int64)
    ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    np.add.at(flat, iy[ok] * nx + ix[ok], clen[ok])


# ----------------------------------------------------------------------
# Весовая сетка
# ----------------------------------------------------------------------
class ThreatGrid:
    """Регулярная сетка ячеек cell_km×cell_km по прямоугольнику bbox_km (в локальных
    км). Хранит суммарный вес каждой ячейки, разбивку по слоям (для отладки/калибровки)
    и число слоёв-аттракторов в ячейке (для бонуса за пересечения)."""

    def __init__(self, bbox_km, cell_km):
        self.kx0, self.kx1, self.ky0, self.ky1 = map(float, bbox_km)
        self.h = float(cell_km)
        self.nx = max(1, int(math.ceil((self.kx1 - self.kx0) / self.h)))
        self.ny = max(1, int(math.ceil((self.ky1 - self.ky0) / self.h)))
        self.ox, self.oy = self.kx0, self.ky0
        self.weight = np.zeros((self.ny, self.nx), float)
        self.layers = {}                       # name -> (ny,nx) вклад слоя
        self._attr_count = np.zeros((self.ny, self.nx), np.int32)
        self._water = np.zeros((self.ny, self.nx), bool)
        self._present = {}                     # name -> (ny,nx) bool: слой есть в ячейке
        self._bridge_mask = np.zeros((self.ny, self.nx), bool)
        self._built_cells = np.zeros((self.ny, self.nx), bool)   # ячейка в застройке
        self._urban = np.zeros((self.ny, self.nx), bool)         # город: пролёт запрещён
        self._village_penalty = np.zeros((self.ny, self.nx), float)  # мягкий штраф деревень
        self._village_grade = {}       # какие пороги отсечения хуторов сработали
        self._place_tag_raw = None     # исходные коды place из OSM (для сверки)
        self._place_grade = {}         # отчёт собственной градации пунктов
        self._place_dens = None        # компактность пятен застройки
        self._relief_h = None          # высоты поверхности на сетке, м (None — не загружены)
        self._relief_k = None          # множитель рельефа: <1 возвышенность, >1 укрытие
        self._relief_cut = None        # маска «уж очень высокая гора» — вес там снят

    # ---- геометрия ячеек ----
    def cell_centers_km(self):
        cx = self.ox + (np.arange(self.nx) + 0.5) * self.h
        cy = self.oy + (np.arange(self.ny) + 0.5) * self.h
        gx, gy = np.meshgrid(cx, cy)           # (ny,nx)
        return gx, gy

    def extent_km(self):
        return (self.ox, self.ox + self.nx * self.h,
                self.oy, self.oy + self.ny * self.h)

    # ---- наложение слоёв ----
    def add_line_layer(self, name, polylines, weight, attractor=True):
        """Линейный слой: вклад ячейки += weight × (длина пересечения / размер ячейки).

        weight — из THREAT_LAYERS (может быть отрицательным для линейного репеллера).
        Нормировка на размер ячейки делает вклад одной «полной» линии сопоставимым
        между разными слоями по масштабу, а не по частоте данных. Растеризация —
        векторная (`_rasterize_polylines`), выдерживает реальные объёмы OSM."""
        acc = _rasterize_polylines(polylines, self.ox, self.oy, self.h, self.nx, self.ny)
        contrib = weight * (acc / self.h)      # доля «характерной длины» = размера ячейки
        self.weight += contrib
        self.layers[name] = self.layers.get(name, 0.0) + contrib
        self._present[name] = acc > 0          # для определения мостов по сетке
        if attractor:
            self._attr_count += (acc > 0).astype(np.int32)

    def add_area_layer(self, name, polygons, weight, attractor=False, mark_urban=False):
        """Площадной слой (застройка): вклад в ячейки внутри полигона. weight обычно
        отрицательный (репеллер). Проверка «точка в полигоне» — только по ячейкам
        bbox каждого полигона (не по всей сетке), поэтому быстро и на многих домах.
        mark_urban=True — дополнительно помечает ячейки как «застроенные» (для
        последующего исключения населённых пунктов, apply_urban_exclusion)."""
        contrib = np.zeros((self.ny, self.nx), float)
        for poly in polygons:
            P = np.asarray(poly, float)
            if P.ndim != 2 or len(P) < 3:
                continue
            ix0 = max(0, int(math.floor((P[:, 0].min() - self.ox) / self.h)))
            ix1 = min(self.nx - 1, int(math.floor((P[:, 0].max() - self.ox) / self.h)))
            iy0 = max(0, int(math.floor((P[:, 1].min() - self.oy) / self.h)))
            iy1 = min(self.ny - 1, int(math.floor((P[:, 1].max() - self.oy) / self.h)))
            if ix0 > ix1 or iy0 > iy1:
                continue
            cx = self.ox + (np.arange(ix0, ix1 + 1) + 0.5) * self.h
            cy = self.oy + (np.arange(iy0, iy1 + 1) + 0.5) * self.h
            gx, gy = np.meshgrid(cx, cy)
            inside = _points_in_polygon(gx.ravel(), gy.ravel(), P).reshape(gy.shape)
            contrib[iy0:iy1 + 1, ix0:ix1 + 1] += weight * inside
            if mark_urban:
                self._built_cells[iy0:iy1 + 1, ix0:ix1 + 1] |= inside
        self.weight += contrib
        self.layers[name] = self.layers.get(name, 0.0) + contrib
        if attractor:
            self._attr_count += (contrib != 0).astype(np.int32)

    def place_masks_from_polygons(self, polys, settled=None):
        """Растеризовать КОНТУРЫ населённых пунктов: вернуть (маска города, маска
        деревни). Контур — готовая граница НП из OSM, она точнее пятна застройки:
        внутри города парки, площади и промзоны не размечены как landuse=residential,
        и по одной застройке в центре зияли дыры.

        Тип берётся из третьей колонки (код PLACE_CODES): city/suburb/borough — город,
        town — город при площади ≥ town_city_km2 (проверяется вызывающим), village —
        деревня, hamlet и мельче — не учитывается."""
        city = np.zeros((self.ny, self.nx), bool)
        village = np.zeros((self.ny, self.nx), bool)
        for poly in polys or []:
            P = np.asarray(poly, float)
            if P.ndim != 2 or len(P) < 3 or P.shape[1] < 3:
                continue
            code = int(round(P[0, 2]))
            if code < 1:                       # hamlet и мельче — пропускаем
                continue
            ix0 = max(0, int(math.floor((P[:, 0].min() - self.ox) / self.h)))
            ix1 = min(self.nx - 1, int(math.floor((P[:, 0].max() - self.ox) / self.h)))
            iy0 = max(0, int(math.floor((P[:, 1].min() - self.oy) / self.h)))
            iy1 = min(self.ny - 1, int(math.floor((P[:, 1].max() - self.oy) / self.h)))
            if ix0 > ix1 or iy0 > iy1:
                continue
            cx = self.ox + (np.arange(ix0, ix1 + 1) + 0.5) * self.h
            cy = self.oy + (np.arange(iy0, iy1 + 1) + 0.5) * self.h
            gx, gy = np.meshgrid(cx, cy)
            inside = _points_in_polygon(gx.ravel(), gy.ravel(),
                                        P[:, :2]).reshape(gy.shape)
            if not inside.any():
                continue
            # ЗАСТРОЕН ли контур на самом деле. В OSM границы НП часто административные
            # и включают поля вокруг села (замер: контуры town дают 667 км² при участке
            # 7135 км², под запрет уходило 30 % вместо 14). Берём контур, только если
            # внутри реальная застройка — иначе это граница сельсовета, а не сам НП.
            built_in = self._built_cells[iy0:iy1 + 1, ix0:ix1 + 1] & inside
            n_in = float(inside.sum())
            built_frac = float(built_in.sum()) / max(n_in, 1.0)
            built_km2 = float(built_in.sum()) * self.h * self.h
            if built_frac < PLACE_BUILT_FRAC and built_km2 < PLACE_BUILT_MIN_KM2:
                continue
            area = n_in * self.h * self.h
            tgt = city if (code >= 3 or (code == 2 and area >= self._town_city_km2)) \
                else village
            tgt[iy0:iy1 + 1, ix0:ix1 + 1] |= inside
        if settled is not None:
            city &= settled                     # не тянуть поля внутри границы
            village &= settled
        village &= ~city                        # город главнее
        return city, village

    def _free_zone_mask(self, free_points, city, village):
        """Свободные зоны у точки вылета и у цели: где запрет города НЕ действует.

        Точка задаётся как `(x, y, radius_km, expand_km)`:
          * `radius_km` — круг вокруг самой точки;
          * `expand_km` — если не None, добавляется ВЕСЬ населённый пункт, внутри
            которого точка стоит, расширенный на это расстояние наружу.

        Для ЦЕЛИ нужен второй режим: при цели в центре города края НП остались бы
        закрытыми и подлёт был бы возможен лишь с одной стороны. Для ВЫЛЕТА достаточно
        круга: расширение всего НП на радиус зоны освобождало соседние города за
        десятки километров (на карте без минуса оказывались Лисичанск, Тошковка,
        Гірське — они не имеют отношения к точке вылета)."""
        free = np.zeros((self.ny, self.nx), bool)
        if not free_points:
            return free
        gx, gy = self.cell_centers_km()
        occupied = city | village
        lab, nlab = _label8(occupied) if occupied.any() else (None, 0)
        for pt in free_points:
            x, y, rad = float(pt[0]), float(pt[1]), float(max(0.0, pt[2]))
            expand = pt[3] if len(pt) > 3 else None
            zone = np.hypot(gx - x, gy - y) <= rad          # круг вокруг точки
            if expand is not None and lab is not None:
                ix = int(math.floor((x - self.ox) / self.h))
                iy = int(math.floor((y - self.oy) / self.h))
                if 0 <= ix < self.nx and 0 <= iy < self.ny:
                    c = int(lab[iy, ix])
                    if c > 0:                                # точка внутри НП — весь НП
                        zone |= _dilate(lab == c,
                                        int(round(float(expand) / self.h)))
            free |= zone
        return free

    def apply_settlements(self, places_km, min_area_km2, town_city_km2, buffer_km,
                          village_core, village_edge, r_min_km, r_max_km,
                          river_corridor_max_km2, zero_weight=True,
                          place_polys=None, free_points=None, village_floor=0.0,
                          village_skip_factor=0.0, village_townlike_med=0.0,
                          grade_on=False, grade_tol=0.15, grade_min_sample=5,
                          grade_close_km=1.0, grade_z=0.0, grade_strict_up=True,
                          grade_mode="both"):
        """Разметить населённые пункты и применить их к весу и проходимости.

        Классификация пятен застройки (связные компоненты по сырой застройке):
          * ТИП берётся с метки OSM `place`, попавшей в пятно (или ближайшей к нему);
            city/suburb/borough -> город, town -> город при площади ≥ town_city_km2
            (иначе деревня), village -> деревня, hamlet и мельче -> не учитывается;
          * если метки нет — запасной критерий по площади (≥ min_area_km2 -> город).

        ГОРОД: пролёт ЗАПРЕЩЁН (маска `_urban` + буфер buffer_km), вклад ВСЕХ слоёв
        внутри ОБНУЛЯЕТСЯ. Ноль, а не штраф: отрицательный вес делал город «ямой», от
        которой разбегались датчики; запрет держит маска, а не знак веса.

        ДЕРЕВНЯ: пролёт РАЗРЕШЁН, но чем ближе к центру, тем менее вероятен — штраф
        спадает линейно от village_core в центре до village_edge на границе радиуса
        (радиус ~ размеру пятна, в пределах r_min_km..r_max_km) и до 0 дальше. Маршрут
        сам прижимается к окраине, но при необходимости проходит.

        `village_skip_factor` / `village_townlike_med` — СОБСТВЕННАЯ градация пунктов по
        данным участка (см. `_village_size_filter`): слишком мелкие пятна (хутора) и,
        если включено, слишком крупные (посёлки) штраф не накладывают вовсе.

        `village_floor` — ПОЛ штрафа: деревня ослабляет ориентир, но не стирает его.
        Штраф фиксирован (−20/−10), а ориентиры весят немного (река +10, местная дорога
        +3), поэтому без пола деревня уводила их в минус — то есть в полный запрет,
        вопреки собственному правилу «пролёт разрешён». Замер до правки: 1019 ячеек с
        ориентирами теряли коридор только из-за штрафа, среди них 54 ячейки реки.
        0.0 -> прежнее поведение.

        РЕКА сквозь населённый пункт остаётся коридором, пока пятно меньше
        river_corridor_max_km2; у крупных городов русло закрывается вместе с городом."""
        self._urban = np.zeros((self.ny, self.nx), bool)
        self._village_penalty = np.zeros((self.ny, self.nx), float)
        self._free = np.zeros((self.ny, self.nx), bool)
        self.layers["village"] = np.zeros((self.ny, self.nx), float)
        self._town_city_km2 = float(town_city_km2)
        # Контуры НП из OSM (готовые границы) — основа; пятна застройки дополняют их
        # там, где контура нет (у 2/3 пунктов есть только метка-точка). Контур режем
        # по «обжитой зоне» — застройке, расширенной на километр: она закрывает дыры
        # внутри города, но не тянет за собой поля внутри административной границы.
        settled = _dilate(self._built_cells, max(1, int(round(1.0 / self.h)))) \
            if self._built_cells.any() else None
        city, village = self.place_masks_from_polygons(place_polys, settled)
        if not self._built_cells.any():
            lab = np.zeros((self.ny, self.nx), np.int32)
            nlab = 0
            area = np.zeros(1)
            kind = np.zeros(1, np.int8)
        else:
            # Компоненты метим ПО СЫРОЙ застройке: склейка разрывов ДО разметки сцепляла
            # бы цепочки сёл вдоль трассы в «псевдогород» (замер: 17.9 % вместо 4.5 %).
            lab, nlab = _label8(self._built_cells)
            area = np.bincount(lab.ravel(), minlength=nlab + 1) * (self.h * self.h)
            kind = self._classify_components(lab, nlab, area, places_km,
                                             min_area_km2, town_city_km2,
                                             grade_on=grade_on, grade_tol=grade_tol,
                                             grade_min_sample=grade_min_sample,
                                             close_km=grade_close_km, grade_z=grade_z,
                                             grade_strict_up=grade_strict_up,
                                             grade_mode=grade_mode)
            city |= kind[lab] == 2                             # пятна-города
            village |= kind[lab] == 1                          # пятна-деревни
        village &= ~city
        if not (city.any() or village.any()):
            return
        # Свободные зоны (вылет/цель) — там ничего не запрещаем и не гасим
        self._free = self._free_zone_mask(free_points, city, village)
        city &= ~self._free
        village &= ~self._free

        # --- деревни: плавный штраф от центра к краю ---
        # Сперва СОБСТВЕННАЯ градация по данным участка: хутора (и, если включено,
        # посёлки) из маски убираются — они не штрафуют вовсе.
        village, self._village_grade = self._village_size_filter(
            village, village_skip_factor, village_townlike_med)
        if village.any():
            self._village_penalty = self._village_field(
                village, village_core, village_edge, r_min_km, r_max_km)

        # --- города: обнулить вклад всех слоёв внутри ---
        city_zone = city.copy()
        r = int(round(max(0.0, buffer_km) / self.h))
        if r > 0:
            city_zone = _dilate(city_zone, r)                  # буфер облёта
        river = self._present.get("river")
        if river is not None:
            # Русло остаётся коридором, пока сам населённый пункт меньше порога; у
            # крупных (Луганск, Алчевск) река закрывается вместе с городом.
            #
            # РАЗМЕР МЕРЯЕТСЯ ПО ИСХОДНОМУ ПЯТНУ ЗАСТРОЙКИ, а не по связной области
            # `city`. Раньше бралось `_label8(city)`, а туда уже слиты контуры соседних
            # пунктов, «обжитая зона» (застройка + 1 км) и буфер облёта: цепочка сёл
            # вдоль реки давала одну компоненту в 431 км², и правило не срабатывало
            # НИКОГДА — именно вдоль рек посёлки и стоят цепочкой. Тот же приём уже
            # применён при классификации (см. `_label8(self._built_cells)` выше):
            # мерить надо по сырой застройке, иначе получается «псевдогород».
            small = self._own_place_small(city, float(river_corridor_max_km2), r)
            small = _dilate(small, r) & ~_dilate(city & ~small, r)
            city_zone = city_zone & ~(river & small)
        city_zone &= ~self._free          # буфер соседнего города не лезет в свободную зону
        self._urban = city_zone
        # Слой застройки больше НЕ штрафует сам по себе: его роль полностью взяли на
        # себя город (обнуление) и деревня (градиент). Иначе деревня получала минус
        # дважды — от built_up и от village (замер: −26.7 вместо −15).
        bu = self.layers.get("built_up")
        if isinstance(bu, np.ndarray):
            self.weight -= bu
            self.layers["built_up"] = np.zeros_like(self.weight)
        if zero_weight:
            self.weight[city_zone] = 0.0
            for name, arr in self.layers.items():
                if isinstance(arr, np.ndarray) and arr.shape == self.weight.shape:
                    arr[city_zone] = 0.0
            self._attr_count[city_zone] = 0
        # штраф деревень — только ВНЕ города: буфер города и радиус соседней деревни
        # перекрывались, и в городских ячейках оставался минус вместо нуля
        self._village_penalty[city_zone] = 0.0
        before = self.weight.copy()
        self.weight += self._village_penalty
        # ПОЛ: деревня ОСЛАБЛЯЕТ ориентир, но не стирает. Иначе река (+10) под штрафом
        # (−17) уходила в минус, а минус в этой модели — полный запрет (passable_mask
        # берёт weight > 0), а не «маловероятно». Градиент «ближе к центру — менее
        # вероятно» при этом сохраняется: вес падает, просто не ниже пола.
        if village_floor > 0.0:
            pos = before > 0.0
            lo = np.minimum(before, float(village_floor))
            self.weight[pos] = np.maximum(self.weight[pos], lo[pos])
        # в слое «village» — фактическое изменение веса, а не сырой штраф: иначе после
        # применения пола слой врал бы (показывал −17 там, где вес упал на 9)
        self.layers["village"] = self.weight - before

    def own_place_area_km2(self, buf_cells=0):
        """Для КАЖДОЙ ячейки — площадь её «родного» населённого пункта (км²), то есть
        пятна СЫРОЙ застройки, отвечающего за эту ячейку. 0 — рядом застройки нет.

        Зачем не связная маска города. `city` склеивает соседние пункты сразу тремя
        способами: «обжитая зона» расширяет застройку на 1 км, контуры НП из OSM
        накладываются в одну общую маску, сверху идёт буфер облёта. Цепочка сёл вдоль
        реки превращается в одну область в сотни км², и любое правило вида «пункт
        меньше стольких-то км²» не срабатывает никогда. По сырой застройке цепочка
        снова распадается на отдельные посёлки — тот же приём, что при классификации
        (`_label8(self._built_cells)`).

        Ячейка может лежать и вне самой застройки (её привёл контур или буфер), поэтому
        пятно ищется в окрестности `1 км + buf_cells`. Из дотянувшихся берётся САМОЕ
        КРУПНОЕ: на стыке большого города и хутора ячейка обязана считаться городской,
        иначе одинокий хутор смягчил бы правило в центре города."""
        best = np.zeros((self.ny, self.nx), float)
        if not self._built_cells.any():
            return best
        lab, nlab = _label8(self._built_cells)
        if nlab == 0:
            return best
        area = np.bincount(lab.ravel(), minlength=nlab + 1) * (self.h * self.h)
        rr = max(1, int(round(1.0 / self.h)) + max(0, int(buf_cells)))
        for c in range(1, nlab + 1):
            iy, ix = np.nonzero(lab == c)
            if not len(iy):
                continue
            y0, y1 = max(0, iy.min() - rr), min(self.ny, iy.max() + rr + 1)
            x0, x1 = max(0, ix.min() - rr), min(self.nx, ix.max() + rr + 1)
            zone = _dilate((lab == c)[y0:y1, x0:x1], rr)   # дилатация по bbox пятна
            np.maximum(best[y0:y1, x0:x1], np.where(zone, area[c], 0.0),
                       out=best[y0:y1, x0:x1])
        return best

    def _own_place_small(self, city, max_km2, buf_cells):
        """Маска «ячейка принадлежит НЕБОЛЬШОМУ населённому пункту» — по площади его
        собственного пятна застройки (см. `own_place_area_km2`), а не связной области.
        Ячейки, к которым не дотянулось ни одно пятно, небольшими не считаются."""
        best = self.own_place_area_km2(buf_cells)
        return city & (best > 0.0) & (best <= float(max_km2))

    def _village_size_filter(self, village, skip_factor, townlike_med):
        """Убрать из маски деревни пятна, которые по СОБСТВЕННОЙ градации участка
        штрафовать не должны.

        Градация строится по самим данным (`settlement_stats`), а не по тегу OSM
        `place`: тег ненадёжен — одинаковые по размеру пятна размечены то village, то
        town, а хутор из трёх домов тоже village. Пороги берутся ОТНОСИТЕЛЬНО участка,
        поэтому переносятся на другую местность, где масштаб застройки иной.

          * мельче `минимум × skip_factor` — фактически хутор, штрафа нет;
          * крупнее `медиана × townlike_med` — уже посёлок со своей дорожной сетью,
            гасить коридоры вокруг него незачем (правило выключено при 0).

        Возвращает (новая маска, справка о применённых порогах)."""
        info = dict(skip_below=0.0, skip_above=float("inf"), dropped_small=0,
                    dropped_big=0)
        if not village.any() or not self._built_cells.any():
            return village, info
        mn, med, _mx, n = self.settlement_stats()
        if n == 0:
            return village, info
        if skip_factor > 0.0:
            info["skip_below"] = mn * float(skip_factor)
        if townlike_med > 0.0:
            info["skip_above"] = med * float(townlike_med)
        if info["skip_below"] <= 0.0 and not np.isfinite(info["skip_above"]):
            return village, info
        own = self.own_place_area_km2()
        drop_small = village & (own > 0.0) & (own < info["skip_below"])
        drop_big = village & (own > info["skip_above"])
        info["dropped_small"] = int(drop_small.sum())
        info["dropped_big"] = int(drop_big.sum())
        return village & ~(drop_small | drop_big), info

    def settlement_stats(self):
        """РАСПРЕДЕЛЕНИЕ пятен застройки по площади на рабочем участке (км²):
        `(минимум, медиана, максимум, число пятен)` — основа СОБСТВЕННОЙ градации
        населённых пунктов (см. `THREAT_VILLAGE_SKIP_FACTOR`).

        Зачем считать по данным, а не брать фиксированные числа: тег OSM `place`
        ненадёжен — одинаковые по размеру пятна размечены то как village, то как town,
        а хутор из трёх домов тоже значится village. Масштаб застройки к тому же
        меняется от участка к участку. Относительная картина «мелкие / средние /
        крупные» устойчивее, поэтому пороги берутся от неё.
        Пусто -> (0, 0, 0, 0)."""
        if not self._built_cells.any():
            return (0.0, 0.0, 0.0, 0)
        lab, nlab = _label8(self._built_cells)
        if nlab == 0:
            return (0.0, 0.0, 0.0, 0)
        area = np.bincount(lab.ravel(), minlength=nlab + 1)[1:] * (self.h * self.h)
        area = area[area > 0]
        if not area.size:
            return (0.0, 0.0, 0.0, 0)
        return (float(area.min()), float(np.median(area)), float(area.max()), int(area.size))

    def place_profiles(self, lab, nlab, area, close_km=1.0):
        """ПАСПОРТ каждого пятна застройки — два измеримых признака:

          * `area_km2`  — МАСШТАБ: площадь самой застройки;
          * `density`   — КОМПАКТНОСТЬ: та же площадь, делённая на площадь ЗАМКНУТОГО
            контура пятна (0..1).

        Замыкание (дилатация + эрозия радиусом `close_km`) закрывает парки, площади и
        промзоны внутри города — они не размечены как `landuse=residential` и иначе
        рвали бы пятно на куски, — но не захватывает поля снаружи. Знаменатель поэтому
        отражает сам пункт, а не то, как OSM нарисовал административную границу.

        Возвращает (площади, плотности) — массивы длиной nlab+1, нулевой элемент фиктивный.
        Каждое пятно замыкается ПО СВОЕМУ bbox: полная морфология на 411 пятнах вышла бы
        заметно дороже, а результат тот же."""
        dens = np.zeros(nlab + 1, float)
        if nlab == 0:
            return np.asarray(area, float), dens
        r = max(1, int(round(float(close_km) / self.h)))
        for c in range(1, nlab + 1):
            iy, ix = np.nonzero(lab == c)
            if not len(iy):
                continue
            y0, y1 = max(0, iy.min() - r - 1), min(self.ny, iy.max() + r + 2)
            x0, x1 = max(0, ix.min() - r - 1), min(self.nx, ix.max() + r + 2)
            sub = (lab[y0:y1, x0:x1] == c)
            closed = _erode(_dilate(sub, r), r) | sub      # замыкание не теряет ячеек
            n_closed = float(closed.sum())
            dens[c] = (float(sub.sum()) / n_closed) if n_closed > 0 else 0.0
        return np.asarray(area, float), dens

    def grade_places(self, tag, area, dens, nlab, tol=0.15, min_sample=5, q=0.0,
                     strict_up=True, mode="both"):
        """ПЕРЕСЧЁТ типа пункта по данным рабочей области. Возвращает (новый тег, отчёт).

        `tag[c]` — исходный код из OSM (3 city / 2 town / 1 village / 0 мельче;
        4 — район города, в статистику и переводы не входит: это часть города, а не
        самостоятельный пункт, и раньше такие районы ломали класс `city` — см. PLACE_CODES).

        Как считается. По каждой группе тега берутся минимум, среднее и максимум ОБОИХ
        признаков — это и есть градация, посчитанная по текущему участку, а не взятая
        из константы. Пункт переводится в соседний класс, если с допуском `tol`
        дотягивается до его границы, причём ОБА признака согласны: по одному масштабу
        хутор с плотной застройкой ушёл бы в города, по одной плотности — тоже.

        `q` — порог ОТСЕВА ошибок разметки (modified z-score на медиане и MAD). Объект,
        слишком далёкий от середины своего класса, — это не «маленький город», а ошибка
        тега, и границу класса задавать он не должен. Замер: в `city` оказался пункт
        0.50 км² (хутор с ошибочным тегом), и он один опустил нижнюю границу класса до
        уровня деревни — после чего вверх уехали 133 пункта из 208, а городская зона
        выросла вместо сокращения. Порог безразмерный, поэтому одинаков для любого
        участка. `q = 0` -> отсев выключен.

        `strict_up` — пороги перехода отсчитываются от СВОЕГО класса, симметрично в обе
        стороны: чтобы подняться, надо превзойти МАКСИМУМ своего класса; чтобы упасть —
        оказаться ниже его МИНИМУМА. Классы по тегу перекрываются почти полностью, и
        сравнение с чужим классом даёт ошибку в обе стороны: «дотянуться до минимума
        city» могла рядовая деревня, а «оказаться ниже максимума town» (23.50 км²) —
        настоящий город вроде Северодонецка (17.25 км²).

        Пять предохранителей:
          * класс, в котором меньше `min_sample` пунктов, статистики не имеет — В НЕГО
            НЕ ПЕРЕВОДИТСЯ НИЧЕГО, остаётся исходный тег;
          * выбросы отсеиваются до расчёта границ (см. `q`) — один ошибочный тег не
            открывает дверь всему классу;
          * подъём требует превзойти максимум своего класса (см. `strict_up`);
          * если пункт одновременно дотягивается и вверх, и вниз, он не переводится
            (классы пересеклись — данные не дают однозначного ответа);
          * ОДИН проход: пороги считаются до переводов и потом не пересчитываются, иначе
            границы классов сдвинулись бы и часть пунктов замигрировала бы обратно."""
        out = np.array(tag, np.int8, copy=True)
        rep = dict(moved_up=0, moved_down=0, skipped_small=[], stats={}, changes=[])
        if nlab == 0:
            return out, rep
        idx = np.arange(nlab + 1)
        zmax = float(max(0.0, q))

        def keep_mask(v):
            """Кого оставить в выборке класса. Объект, слишком далёкий от середины, —
            это не «маленький город», а ОШИБКА ТЕГА, и границу класса задавать он не
            должен. Мера — modified z-score на медиане и MAD: обе величины сами не
            сдвигаются от выбросов, поэтому порог безразмерный и одинаков для любого
            участка (никакой привязки к километрам этой конкретной области)."""
            if zmax <= 0.0 or v.size < 4:
                return np.ones(v.size, bool)
            med = float(np.median(v))
            mad = float(np.median(np.abs(v - med)))
            if mad <= 1e-12:                     # выборка почти вырождена — не чистим
                return np.ones(v.size, bool)
            return 0.6745 * np.abs(v - med) / mad <= zmax

        def bounds(v):
            """(минимум, среднее, максимум) по ОЧИЩЕННОЙ выборке."""
            if not v.size:
                return (0.0, 0.0, 0.0)
            return (float(v.min()), float(v.mean()), float(v.max()))

        use_mad = mode in ("mad", "both")
        use_cascade = mode in ("cascade", "both")
        prev_max_a = prev_max_d = None                     # планка от класса снизу
        for k in PLACE_LADDER:                             # village / town / city
            sel = (tag == k) & (idx > 0)
            n = int(sel.sum())
            a_v, d_v = area[sel], dens[sel]
            n_mad = n_casc = 0
            if use_mad:
                # выброс по ЛЮБОМУ из признаков исключает объект целиком: пункт с
                # ошибочным тегом врёт обоими числами, половину его данных не оставляем
                keep = keep_mask(a_v) & keep_mask(d_v)
                if int(keep.sum()) >= 3:                   # чистим, пока выборка жива
                    n_mad = int((~keep).sum())
                    a_v, d_v = a_v[keep], d_v[keep]
            if use_cascade and prev_max_a is not None and a_v.size:
                # КАСКАД: «город не может быть меньше самого крупного посёлка».
                # Планка — максимум УЖЕ ОЧИЩЕННОГО класса снизу, иначе одна ошибка
                # разметки в village задрала бы планку всем town и city.
                keep = (a_v >= prev_max_a) | (d_v >= prev_max_d)
                if int(keep.sum()) >= 3:
                    n_casc = int((~keep).sum())
                    a_v, d_v = a_v[keep], d_v[keep]
            n_used = int(a_v.size)
            rep["stats"][k] = dict(n=n, n_used=n_used, dropped=n - n_used,
                                   dropped_mad=n_mad, dropped_cascade=n_casc,
                                   area=bounds(a_v), dens=bounds(d_v))
            if n_used:
                prev_max_a, prev_max_d = rep["stats"][k]["area"][2], rep["stats"][k]["dens"][2]
            if n_used < int(min_sample):
                rep["skipped_small"].append(k)
        up, dn = 1.0 + float(tol), 1.0 - float(tol)
        # ПОРОГИ ПЕРЕХОДА — оба СИММЕТРИЧНО от СВОЕГО класса, а не от чужого.
        #
        # Вверх: мало дотянуться до минимума класса выше — надо превзойти МАКСИМУМ
        # своего. Иначе при перекрытии классов (замер: city 0.50…42.75 против town
        # 0.25…23.50) «дотянуться до минимума city» могла рядовая деревня.
        #
        # Вниз: мало оказаться ниже максимума класса снизу — надо упасть ниже МИНИМУМА
        # своего. Сперва понижение сравнивалось с чужим классом, и это разжаловало
        # настоящие города: максимум town на участке 23.50 км², поэтому Северодонецк
        # (17.25 км², плотность 0.81) уходил в town вместе со всеми городами мельче
        # 27.6 км². Зеркальная ошибка той, что была при подъёме.
        def gate(hi, lo, key, up_dir):
            """Порог перехода из класса `lo` в `hi` по признаку `key`."""
            if up_dir:
                g = rep["stats"][hi][key][0]               # минимум класса выше
                if strict_up and rep["stats"].get(lo, {}).get("n_used", 0) >= 3:
                    g = max(g, rep["stats"][lo][key][2])   # ...но не ниже максимума своего
            else:
                g = rep["stats"][lo][key][2]               # максимум класса ниже
                if strict_up and rep["stats"].get(hi, {}).get("n_used", 0) >= 3:
                    g = min(g, rep["stats"][hi][key][0])   # ...но не выше минимума своего
            return g

        for c in range(1, nlab + 1):
            k = int(tag[c])
            # hamlet и мельче не трогаем; район города — тоже: это часть города,
            # а не самостоятельный пункт, переклассифицировать там нечего
            if k not in PLACE_LADDER:
                continue
            # СВОЙ класс тоже должен иметь статистику. Пороги обоих переходов считаются
            # от границ своего класса, и если в нём меньше min_sample пунктов, эти
            # границы недостоверны — судить не по чему, оставляем тег. Замер: когда в
            # классе `city` осталось 2 пункта, оба НАСТОЯЩИХ города оказались разжалованы
            # по границе, построенной на них же самих.
            if k in rep["skipped_small"]:
                continue
            pos = PLACE_LADDER.index(k)
            hi = PLACE_LADDER[pos + 1] if pos + 1 < len(PLACE_LADDER) else None
            lo = PLACE_LADDER[pos - 1] if pos > 0 else None
            can_up = can_dn = False
            if hi is not None and hi not in rep["skipped_small"] and rep["stats"].get(hi, {}).get("n"):
                can_up = (area[c] * up >= gate(hi, k, "area", True)
                          and dens[c] * up >= gate(hi, k, "dens", True))
            if lo is not None and lo not in rep["skipped_small"] and rep["stats"].get(lo, {}).get("n"):
                can_dn = (area[c] * dn <= gate(k, lo, "area", False)
                          and dens[c] * dn <= gate(k, lo, "dens", False))
            if can_up == can_dn:                           # оба или ни одного — не трогаем
                continue
            out[c] = hi if can_up else lo
            rep["moved_up" if can_up else "moved_down"] += 1
            rep["changes"].append((int(c), k, int(out[c]), float(area[c]), float(dens[c])))
        return out, rep

    def _classify_components(self, lab, nlab, area, places_km,
                             min_area_km2, town_city_km2,
                             grade_on=False, grade_tol=0.15, grade_min_sample=5,
                             close_km=1.0, grade_z=0.0, grade_strict_up=True,
                             grade_mode="both"):
        """Тип каждой компоненты застройки: 2 — город, 1 — деревня, 0 — не учитываем.
        Основа — метка OSM `place` внутри пятна (или ближайшая в пределах 2 км),
        запасной вариант при отсутствии метки — площадь пятна.

        При `grade_on` тип затем ПЕРЕСЧИТЫВАЕТСЯ по данным участка (`grade_places`):
        тег ненадёжен, а раздутый контур делает «городом» пятно, где застроено 27 %
        площади. Исходный тег сохраняется в `self._place_tag_raw`, результат сверки —
        в `self._place_grade`."""
        kind = np.zeros(nlab + 1, np.int8)
        best = np.zeros(nlab + 1, np.int8)                     # макс. код метки в пятне
        found = np.zeros(nlab + 1, bool)
        P = np.asarray(places_km, float) if places_km is not None else np.empty((0, 3))
        if P.ndim == 2 and len(P) and P.shape[1] >= 3:
            ix = np.floor((P[:, 0] - self.ox) / self.h).astype(int)
            iy = np.floor((P[:, 1] - self.oy) / self.h).astype(int)
            ok = (ix >= 0) & (ix < self.nx) & (iy >= 0) & (iy < self.ny)
            ix, iy, code = ix[ok], iy[ok], P[ok, 2].astype(np.int8)
            comp = lab[iy, ix]
            # метка вне застройки — отнести к ближайшему пятну в радиусе 2 ячеек
            miss = comp == 0
            if miss.any():
                rad = max(1, int(round(2.0 / self.h)))
                for j in np.nonzero(miss)[0]:
                    y0, y1 = max(0, iy[j] - rad), min(self.ny, iy[j] + rad + 1)
                    x0, x1 = max(0, ix[j] - rad), min(self.nx, ix[j] + rad + 1)
                    win = lab[y0:y1, x0:x1]
                    nz = win[win > 0]
                    if len(nz):
                        vals, cnt = np.unique(nz, return_counts=True)
                        comp[j] = int(vals[np.argmax(cnt)])
            for c, k in zip(comp, code):
                if c > 0:
                    found[c] = True
                    best[c] = max(best[c], int(k))
        # СОБСТВЕННАЯ ГРАДАЦИЯ: пересчитать код пункта по измеримым признакам участка.
        # Работает по тем же кодам, что и тег (3/2/1), поэтому дальнейшая логика
        # «city -> город, town -> по площади, village -> деревня» не меняется.
        self._place_tag_raw = np.array(best, np.int8, copy=True)
        self._place_grade = {}
        code = best
        if grade_on and nlab:
            tag = np.where(found, best, 0).astype(np.int8)      # без метки — не градуем
            a_km2, dens = self.place_profiles(lab, nlab, area, close_km)
            self._place_dens = dens
            code, self._place_grade = self.grade_places(
                tag, a_km2, dens, nlab, tol=grade_tol, min_sample=grade_min_sample,
                q=grade_z, strict_up=grade_strict_up, mode=grade_mode)
            code = np.where(found, code, best).astype(np.int8)  # метки нет — как было
        for c in range(1, nlab + 1):
            a = area[c]
            if found[c]:
                k = code[c]
                if k >= 3:                                     # city / suburb / borough
                    kind[c] = 2
                elif k == 2:                                   # town: крупный -> город
                    kind[c] = 2 if a >= float(town_city_km2) else 1
                elif k == 1:                                   # village
                    kind[c] = 1
                else:                                          # hamlet и мельче
                    kind[c] = 0
            else:                                              # метки нет — по площади
                kind[c] = 2 if a >= float(min_area_km2) else 0
        return kind

    def _village_field(self, village_mask, core, edge, r_min_km, r_max_km):
        """Поле штрафа деревень: `core` в центре, `edge` на ПОЛОВИНЕ радиуса, 0 на его
        границе — спад без ступеньки (обрыв edge→0 на краю давал маршруту резкую
        границу). Радиус ~ размеру деревни (√площади), в пределах r_min..r_max.
        Работает по МАСКЕ: деревня может прийти и контуром OSM, и пятном застройки."""
        out = np.zeros((self.ny, self.nx), float)
        gx, gy = self.cell_centers_km()
        lab, nlab = _label8(village_mask)
        area = np.bincount(lab.ravel(), minlength=nlab + 1) * (self.h * self.h)
        for c in range(1, nlab + 1):
            iy, ix = np.nonzero(lab == c)
            if not len(ix):
                continue
            cx = self.ox + (ix.mean() + 0.5) * self.h
            cy = self.oy + (iy.mean() + 0.5) * self.h
            rad = float(np.clip(math.sqrt(max(area[c], 1e-6)), r_min_km, r_max_km))
            y0 = max(0, int((cy - rad - self.oy) / self.h))
            y1 = min(self.ny, int((cy + rad - self.oy) / self.h) + 2)
            x0 = max(0, int((cx - rad - self.ox) / self.h))
            x1 = min(self.nx, int((cx + rad - self.ox) / self.h) + 2)
            if y0 >= y1 or x0 >= x1:
                continue
            d = np.hypot(gx[y0:y1, x0:x1] - cx, gy[y0:y1, x0:x1] - cy)
            t = np.clip(d / rad, 0.0, 1.0)                      # 0 в центре, 1 на границе
            val = np.where(t <= 0.5,
                           core + (edge - core) * (t / 0.5),    # центр -> половина радиуса
                           edge * (1.0 - (t - 0.5) / 0.5))      # половина -> край, до нуля
            val = np.where(d <= rad, val, 0.0)
            out[y0:y1, x0:x1] = np.minimum(out[y0:y1, x0:x1], val)   # берём худший штраф
        return out

    def add_relief_layer(self, dem_km, area_km, local_km, area_full_m, local_full_m,
                         area_s, local_s, k_min, k_max, floor, cut_area_m, cut_local_m):
        """Рельеф: МНОЖИТЕЛЬ к уже накопленному ПОЛОЖИТЕЛЬНОМУ весу.

        Считаем превышение над окружающей местностью в ДВУХ масштабах (абсолютная высота
        бесполезна — участок может целиком лежать на плато):
          A — над РАЙОНОМ (area_km): плато, гряда, общий подъём местности;
          B — над ОКРЕСТНОСТЬЮ (local_km): одиночный холм, бугор прямо на пути.
        Без A высокое открытое плато даёт превышение ≈ 0 и не штрафуется вовсе, хотя
        видно оттуда на десятки километров.

        ПОЧЕМУ МНОЖИТЕЛЬ, А НЕ СЛАГАЕМОЕ. Прибавление вклада ко всем ячейкам ломает модель
        дважды. 57 % участка имеет вес ровно 0 — это чистое поле, и бонус за низину делает
        его «коридором»: маршрут пошёл бы по голой пойме без единого ориентира, хотя вся
        модель построена на полёте ВДОЛЬ ориентиров. А дорога, поднявшаяся на гребень,
        уходит в минус, и коридор обрывается посередине. Замер на реальных данных: 4982
        ложных коридора, связность рассыпается с 68 до 148 компонент. Множитель сравнивает
        объект сам с собой — «та же дорога, но в низине / на гребне», и структура карты
        сохраняется точно.

        ПОЧЕМУ НЕ УВОДИМ ВЕС В МИНУС. В этой модели вес ≤ 0 — не «маловероятно», а полный
        запрет: `threat_routes.passable_mask` берёт `weight > 0`, и отрицательная ячейка
        неотличима от застройки. Поэтому низкий приоритет выражается малым ПОЛОЖИТЕЛЬНЫМ
        весом (пол), а полный отказ — нулём по отсечке.

        dem_km — высоты, уже приведённые к сетке (ny,nx); NaN там, где данных нет."""
        if dem_km is None:
            return
        h = np.asarray(dem_km, float)
        if h.shape != (self.ny, self.nx) or not np.isfinite(h).any():
            return
        ok = np.isfinite(h)
        filled = np.where(ok, h, np.nanmean(h[ok]))
        self._relief_h = np.where(ok, filled, np.nan)
        A = filled - _box_mean(filled, max(1, int(round(area_km / self.h))))    # над районом
        B = filled - _box_mean(filled, max(1, int(round(local_km / self.h))))   # над окрестностью
        k = (1.0 - float(area_s) * _relief_f(A / max(1e-6, float(area_full_m)))
                 - float(local_s) * _relief_f(B / max(1e-6, float(local_full_m))))
        k = np.clip(k, float(k_min), float(k_max))
        k[~ok] = 1.0                                   # нет данных о высоте — вес не трогаем
        cut = ok & ((A >= float(cut_area_m)) | (B >= float(cut_local_m)))
        before = self.weight.copy()
        pos = before > 0
        # ПОЛ: коридор не опускается ниже floor, но и НЕ поднимается выше исходного веса —
        # 4.2 % коридоров слабее единицы изначально, и поднимать их было бы искажением
        # в другую сторону (рельеф усилил бы шум вместо того, чтобы ослабить высоты).
        lo = np.minimum(before, float(floor))
        self.weight[pos] = np.maximum(before[pos] * k[pos], lo[pos])
        # отсечка СНИМАЕТ КОРИДОР, а не стирает ячейку: отрицательный вес (застройка,
        # деревня) остаётся своим — он и так означает запрет, а обнуление сделало бы
        # застройку на горе «нейтральной» и исказило тепловую карту
        self.weight[cut & pos] = 0.0
        self._relief_k = k
        self._relief_cut = cut
        self.layers["relief"] = self.weight - before   # что именно рельеф изменил

    def relief_height(self):
        """Высоты поверхности на сетке, м (None — рельеф не загружен)."""
        return self._relief_h

    def relief_k(self):
        """Множитель рельефа: < 1 возвышенность, > 1 укрытие (None — рельеф не применён)."""
        return self._relief_k

    def relief_cut(self):
        """Маска «уж очень высокая гора»: там вес снят полностью (None — рельефа нет)."""
        return self._relief_cut

    def set_bridge_mask_from_layer(self):
        """Ячейки-ПЕРЕПРАВЫ: мост из OSM (`bridge=yes`) НАД ВОДОЙ. Прежде мост
        вычислялся как пересечение реки с дорогой и появлялся там, где по векторным
        данным моста нет.

        Маркерами показываем только переправы: тег `bridge` висит и на путепроводах над
        дорогами и ж/д (замер: 466 мостов, из них 386 над водой и 80 путепроводов), а
        для маршрута значима именно переправа через водную преграду. Сами мосты как
        линии рисуются отдельным слоем — там видно все."""
        m = self._present.get("bridge")
        if m is None:
            self._bridge_mask = np.zeros((self.ny, self.nx), bool)
            return
        water = np.zeros((self.ny, self.nx), bool)
        for key in ("river", "stream"):
            w = self._present.get(key)
            if w is not None:
                water |= w
        self._bridge_mask = m & _dilate(water, 1) if water.any() else m.copy()

    def bridge_points_km(self, bridge_polys):
        """ТОЧНЫЕ координаты переправ: середина каждой мостовой линии OSM, проходящей
        над водой. Раньше маркер ставился в центр ЯЧЕЙКИ (500 м), и при приближении
        треугольник заметно не совпадал с самим мостом — до 250 м в сторону."""
        water = np.zeros((self.ny, self.nx), bool)
        for key in ("river", "stream"):
            w = self._present.get(key)
            if w is not None:
                water |= w
        if not water.any():
            return []
        wet = _dilate(water, 1)
        out = []
        for poly in bridge_polys or []:
            P = np.asarray(poly, float)
            if P.ndim != 2 or len(P) < 2:
                continue
            ix = np.floor((P[:, 0] - self.ox) / self.h).astype(int)
            iy = np.floor((P[:, 1] - self.oy) / self.h).astype(int)
            ok = (ix >= 0) & (ix < self.nx) & (iy >= 0) & (iy < self.ny)
            if not ok.any() or not wet[iy[ok], ix[ok]].any():
                continue                       # мост не над водой — это путепровод
            seg = P[ok]
            mid = seg[len(seg) // 2]           # середина линии моста
            out.append((float(mid[0]), float(mid[1])))
        return out

    def crossing_cells_km(self, min_layers=2):
        """Центры ячеек-ПЕРЕСЕЧЕНИЙ: где сходятся ≥ min_layers РАЗНЫХ слоёв-аттракторов
        (дорога×река=мост, дорога×ЛЭП, ЛЭП×трубопровод и т.д.) — узлы, где маршрут
        может свернуть на другой коридор. Используем уже посчитанный `_attr_count`
        (число разных слоёв в ячейке). Это точки развилок для будущих итераций."""
        iy, ix = np.nonzero(self._attr_count >= int(min_layers))
        x = self.ox + (ix + 0.5) * self.h
        y = self.oy + (iy + 0.5) * self.h
        return np.column_stack([x, y]) if len(ix) else np.empty((0, 2))

    def mark_water(self, polylines, buffer_km):
        """Отметить ячейки воды (по руслам) + буфер — для ЗАПРЕТА датчиков (Задача 1
        из первого исследования). Вода при этом уже дала «плюс» карте как аттрактор —
        двойная роль: маршрут тянется к воде, но свой датчик на воду не ставим."""
        acc = _rasterize_polylines(polylines, self.ox, self.oy, self.h, self.nx, self.ny)
        wet = acc > 0
        r = int(math.ceil(max(0.0, buffer_km) / self.h))
        self._water |= _dilate(wet, r)

    def add_intersection_bonus(self, bonus_per_extra):
        """Funnel-бонус: за каждый слой-аттрактор в ячейке СВЕРХ первого — +bonus.
        Места схождения нескольких линейных ориентиров (перекрёстки, переправы)
        получают дополнительный вес — маршрут там предсказуемее."""
        extra = np.maximum(0, self._attr_count - 1).astype(float)
        contrib = bonus_per_extra * extra
        self.weight += contrib
        self.layers["intersection"] = contrib

    # ---- ЗАПРЕТНЫЕ ЗОНЫ (рисует пользователь) ----
    def set_no_fly_zones(self, polys):
        """Задать запретные зоны — список многоугольников [(x, y), …] в км.

        Зона запрещает ПРОЛЁТ, как город: вычитается из проходимости в `passable_mask`.
        Датчики в ней ставить можно — им запрещена только вода (правило 1.1а).
        Веса карты зона НЕ трогает: она про полёт, а не про местность, поэтому пересборка
        карты её не сбрасывает, а карта остаётся той же."""
        self._no_fly_polys = [np.asarray(p, float) for p in (polys or [])
                              if p is not None and len(p) >= 3]
        self._no_fly = None                     # маска пересчитается по требованию

    def no_fly_mask(self):
        """Маска запретных зон (ny, nx) либо None, если зон нет.

        None, а не пустой массив: вызывающий код тогда не делает лишней работы, и при
        отсутствии зон поведение остаётся ровно прежним."""
        polys = getattr(self, "_no_fly_polys", None)
        if not polys:
            return None
        if getattr(self, "_no_fly", None) is not None:
            return self._no_fly
        gx, gy = self.cell_centers_km()
        m = np.zeros((self.ny, self.nx), bool)
        for poly in polys:                          # та же функция, что у контуров НП
            m |= _points_in_polygon(gx.ravel(), gy.ravel(), poly).reshape(gy.shape)
        self._no_fly = m
        return m

    # ---- выгрузка ----
    def water_mask(self):
        return self._water

    def urban_mask(self):
        return self._urban

    def flat_cells(self, positive_only=False):
        """Центры ячеек (M,2) и веса (M,). positive_only — только аттракторные (w>0):
        нулевые/отрицательные не дают полезного покрытия, их можно не подавать в
        расстановку (репеллеры учитываем отдельно как штраф)."""
        gx, gy = self.cell_centers_km()
        xy = np.column_stack([gx.ravel(), gy.ravel()])
        w = self.weight.ravel()
        if positive_only:
            m = w > 0
            return xy[m], w[m]
        return xy, w

    def totals_by_layer(self):
        return {name: float(np.sum(arr)) for name, arr in self.layers.items()}

    # ---- сохранение растровой карты (ответ на открытый вопрос §9.5: растрово) ----
    def save_npz(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        np.savez_compressed(
            path, weight=self.weight, water=self._water,
            attr_count=self._attr_count,
            bbox=np.array([self.kx0, self.kx1, self.ky0, self.ky1]),
            cell=np.array([self.h]))

    @classmethod
    def load_npz(cls, path):
        d = np.load(path)
        g = cls(tuple(d["bbox"]), float(d["cell"][0]))
        g.weight = d["weight"]
        g._water = d["water"]
        g._attr_count = d["attr_count"]
        return g


# ----------------------------------------------------------------------
# Вспомогательное: точка в полигоне, связные компоненты, дилатация
# ----------------------------------------------------------------------
def _box_mean(a, r):
    """Среднее по квадратному окну (2r+1)² вокруг каждой ячейки — через интегральное
    изображение, O(ячеек). Нужно для «среднего уровня местности» вокруг ячейки."""
    if r <= 0:
        return a
    ny, nx = a.shape
    ii = np.zeros((ny + 1, nx + 1), np.float64)
    ii[1:, 1:] = np.cumsum(np.cumsum(a, axis=0), axis=1)
    y0 = np.clip(np.arange(ny) - r, 0, ny); y1 = np.clip(np.arange(ny) + r + 1, 0, ny)
    x0 = np.clip(np.arange(nx) - r, 0, nx); x1 = np.clip(np.arange(nx) + r + 1, 0, nx)
    Y0, X0 = np.meshgrid(y0, x0, indexing="ij"); Y1, X1 = np.meshgrid(y1, x1, indexing="ij")
    total = ii[Y1, X1] - ii[Y0, X1] - ii[Y1, X0] + ii[Y0, X0]
    return total / np.maximum((Y1 - Y0) * (X1 - X0), 1)


def _relief_f(x):
    """Отклик на превышение (аргумент — превышение, делённое на порог насыщения).

    Вверх круче линейного (`x^1.5`): высунулся — штраф резкий. Вниз слабее (`0.5·x`):
    закопался — выигрыш умеренный. Асимметрия намеренная — зона, откуда аппарат виден,
    растёт быстрее самой высоты. Аргумент насыщается по модулю единицей: без потолка одна
    гора перевесила бы всю карту, а десятки равноценных низких коридоров схлопнулись бы
    в одну долину (вместе с разнообразием маршрутов)."""
    x = np.clip(x, -1.0, 1.0)
    return np.where(x > 0.0, np.power(np.maximum(x, 0.0), 1.5), 0.5 * x)


def _points_in_polygon(px, py, poly):
    """Векторный ray-casting: маска точек (px,py) внутри замкнутого полигона poly."""
    px = np.asarray(px, float); py = np.asarray(py, float)
    x = poly[:, 0]; y = poly[:, 1]
    inside = np.zeros(px.shape, bool)
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi, xj, yj = x[i], y[i], x[j], y[j]
        cond = ((yi > py) != (yj > py))
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = xi + (py - yi) * (xj - xi) / (yj - yi + 1e-30)
        inside ^= cond & (px < xint)
        j = i
    return inside


def _label8(mask):
    """Разметка связных компонент булевой маски (8-связность). Возвращает (lab, n):
    lab — int-массив той же формы (0 = фон, 1..n — номер компоненты). Чистый numpy +
    BFS по застроенным ячейкам — на сетке ~30 тыс. ячеек мгновенно, scipy не нужен."""
    ny, nx = mask.shape
    lab = np.zeros((ny, nx), np.int32)
    n = 0
    nb = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
    for sy, sx in zip(*np.nonzero(mask)):
        if lab[sy, sx]:
            continue
        n += 1
        lab[sy, sx] = n
        stack = [(sy, sx)]
        while stack:
            y, x = stack.pop()
            for dy, dx in nb:
                y2, x2 = y + dy, x + dx
                if 0 <= y2 < ny and 0 <= x2 < nx and mask[y2, x2] and not lab[y2, x2]:
                    lab[y2, x2] = n
                    stack.append((y2, x2))
    return lab, n


def _dilate(mask, r):
    """Дилатация булевой маски на r ячеек (квадратная окрестность, чистый numpy).
    Через паддинг-срез — без заворота краёв (np.roll заворачивает и пометил бы воду
    у противоположной кромки)."""
    if r <= 0:
        return mask.copy()
    ny, nx = mask.shape
    pad = np.zeros((ny + 2 * r, nx + 2 * r), bool)
    out = np.zeros((ny, nx), bool)
    pad[r:r + ny, r:r + nx] = mask
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out |= pad[r + dy:r + dy + ny, r + dx:r + dx + nx]
    return out


def _erode(mask, r):
    """Эрозия булевой маски на r ячеек — обратная к `_dilate`. Нужна для ЗАМЫКАНИЯ
    (дилатация + эрозия), которым сшиваются провалы между коридорами, не расширяя сам
    коридор (см. `threat_routes.passable_mask`).

    За границей массива считаем «занято» (True) — в отличие от `_dilate`, который
    паддится нулями. Иначе замыкание подъедало бы коридоры, прижатые к рамке участка:
    у края не хватило бы соседей, и ячейка ошибочно считалась бы непроходимой."""
    if r <= 0:
        return mask.copy()
    ny, nx = mask.shape
    pad = np.ones((ny + 2 * r, nx + 2 * r), bool)
    out = np.ones((ny, nx), bool)
    pad[r:r + ny, r:r + nx] = mask
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out &= pad[r + dy:r + dy + ny, r + dx:r + dx + nx]
    return out


# ----------------------------------------------------------------------
# Источник геоданных: реальный офлайн-кэш ИЛИ синтетическая схема
# ----------------------------------------------------------------------
def load_layers(lon0, lat0, data_path=None, bbox_lonlat=None):
    """Слои цифровой карты в локальных км (якорь lon0,lat0). Возвращает (dict, источник).

    dict: name -> список ломаных/полигонов/точек (numpy (N,2) в км).
    data_path — явный источник (из окна «Выбрать цифровые карты»):
      * .npz  — готовые слои (из tools/build_threat_grid.py);
      * .osm.pbf / .osm — сырой OSM-экстракт, парсится на месте (нужен pyrosm).
    Если data_path не задан — приоритет: geo_cache/threat_layers.npz -> синтетическая
    СХЕМА (офлайн, без внешних данных)."""
    if data_path:
        ext = os.path.splitext(data_path)[1].lower()
        if ext == ".npz":
            return _load_layers_npz(data_path), f"NPZ: {os.path.basename(data_path)}"
        if ext in (".pbf", ".osm"):
            return (layers_from_osm(data_path, lon0, lat0, bbox_lonlat),
                    f"OSM: {os.path.basename(data_path)}")
        raise ValueError(f"Неизвестный формат данных: {ext} (нужен .npz или .osm.pbf)")
    # файл слоёв СВОЙ у каждого участка (config.THREAT_LAYERS_FILE): при смене куска
    # карты кэш прежнего участка остаётся лежать рядом и не подменяет новый
    path = layers_cache_path()
    if os.path.exists(path):
        try:
            return _load_layers_npz(path), f"OSM (офлайн-кэш: {os.path.basename(path)})"
        except Exception:
            pass
    return _synthetic_layers(lon0, lat0), "СХЕМА (демо, офлайн)"


def _clip_layers_km(layers, bbox_km, margin_km):
    """Оставить в слоях только то, что попало в рамку района плюс запас (всё в км).

    Кэш `.npz` собран на всю ОБЛАСТЬ показа, а работаем в РАЙОНЕ внутри неё.

    ⚠️ ЛИНИИ РЕЖУТСЯ ПО ГЕОМЕТРИИ, А НЕ ПО ГАБАРИТАМ (заказчик 05.09.2026: «векторные
    слои уходят слишком за район действия, максимум 5 км»). Прежнее правило оставляло
    объект ЦЕЛИКОМ, если он хоть чем-то задел рамку, — и река или магистраль, задевшая
    угол района, тянулась через всю область показа на десятки километров. Теперь от неё
    остаётся только кусок в рамке: `_clip_parts` разрывает линию по границе и ставит
    точку пересечения, поэтому обрубок не уходит за край даже на длину сегмента.

    ⚠️ ПЛОЩАДНЫЕ СЛОИ (застройка) режутся ПО ГАБАРИТАМ, как раньше: разрезанный контур
    перестал бы быть замкнутым, и заливка растеклась бы по карте. Их размер — единицы
    километров, за запас они и так почти не выходят.

    ⚠️ Слой имён (`place_names`) идёт ПАРОЙ к `place_pts`: у них общий порядок, и резать
    их поодиночке нельзя — подписи разъедутся с точками. Оба оставляем как есть."""
    x0, x1, y0, y1 = bbox_km
    clip = (x0 - margin_km, x1 + margin_km, y0 - margin_km, y1 + margin_km)
    cx0, cx1, cy0, cy1 = clip
    line_layers = {n for n, d in THREAT_LAYERS.items() if d.get("geom") == "line"}
    out = {}
    for name, parts in layers.items():
        if name in ("place_names", "place_pts"):        # связанная пара — не трогаем
            out[name] = parts
            continue
        if name in line_layers:
            out[name] = _clip_parts(parts, clip)        # рвём по границе рамки
            continue
        kept = []
        for arr in parts:
            a = np.asarray(arr)
            if a.ndim != 2 or a.shape[0] == 0 or a.shape[1] < 2 or a.dtype.kind in "US":
                kept.append(arr)                        # не координаты — оставляем как есть
                continue
            if (a[:, 0].max() >= cx0 and a[:, 0].min() <= cx1 and
                    a[:, 1].max() >= cy0 and a[:, 1].min() <= cy1):
                kept.append(arr)
        out[name] = kept
    return out


def layers_cache_path():
    """Путь к кэшу слоёв активного участка: `geo_cache/<участок>/<THREAT_LAYERS_FILE>`
    (с откатом в корень кэша — см. `area_file`)."""
    return area_file(THREAT_LAYERS_FILE)


# Фильтры тегов OSM по слоям (единый источник правды: используют и офлайн-скрипт
# tools/build_threat_grid.py, и окно выбора карт в интерфейсе).
OSM_LAYER_FILTERS = {
    "river":      {"waterway": ["river", "canal"]},
    "stream":     {"waterway": ["stream"]},
    "road_major": {"highway": ["motorway", "trunk", "primary", "secondary"]},
    "road_local": {"highway": ["tertiary", "unclassified", "residential"]},
    "railway":    {"railway": ["rail"]},
    "power":      {"power": ["line"]},
    "pipeline":   {"man_made": ["pipeline"]},
    "tree_row":   {"natural": ["tree_row"]},
    "bridge":     {"bridge": ["yes", "viaduct"]},
    "built_up":   {"landuse": ["residential", "industrial"]},
}

# Населённые пункты: отдельный служебный слой (в вес НЕ входит) — по нему
# классифицируются пятна застройки: город запрещён, деревня обходится по краю.
# Метка может быть точкой (у 2/3 пунктов границ в OSM нет) — тогда тип берётся с
# точки, а границы даёт связное пятно застройки вокруг неё.
OSM_PLACE_FILTER = {"place": ["city", "town", "village", "hamlet", "suburb", "borough"]}
PLACE_LAYER = "place_pts"          # метки-точки: массив (N,3) — x_км, y_км, код типа
PLACE_POLY_LAYER = "place_poly"    # контуры НП: массивы (M,3) — x_км, y_км, код типа
# НАЗВАНИЯ пунктов: массив строк, i-я строка отвечает i-й точке PLACE_LAYER. Нужны только
# для ПОДПИСЕЙ на карте (на расчёт не влияют): подписи подложки впечатаны в тайлы кеглем
# ~10 px и при обзоре всего участка нечитаемы, поэтому названия рисуются своим шрифтом.
PLACE_NAME_LAYER = "place_names"
# Коды типов населённых пунктов. Всё, что `>= 3`, — город (пролёт запрещён), но РАЙОН
# города (suburb/borough) и сам город различаются: район не самостоятельный пункт и в
# СТАТИСТИКУ собственной градации не входит — иначе он её ломает. Замер: класс `city`
# на участке имел минимум 0.50 км² при максимуме 42.75, потому что 62 района Луганска и
# Северодонецка считались отдельными «городами» по 0.5–2 км². Медиана класса от этого
# падала, и НАСТОЯЩИЕ города оказывались выбросами в собственном классе.
#
# ВАЖЕН ПОРЯДОК: у города код ВЫШЕ, чем у района. Тип пятна берётся как МАКСИМУМ кодов
# попавших в него меток (`best[c] = max(...)`), и при обратном порядке пятно города,
# внутри которого размечены его же районы, получало бы код района — из восьми городов
# участка в класс попадали два.
PLACE_CODES = {"city": 4, "borough": 3, "suburb": 3, "town": 2, "village": 1, "hamlet": 0}
# ЛЕСТНИЦА КЛАССОВ для градации: village -> town -> city. Район (3) в неё не входит —
# переклассифицировать часть города не в чем. Соседний класс берётся по позиции в этом
# списке, а не арифметикой над кодом, — иначе смена кодировки молча ломает переходы.
PLACE_LADDER = (1, 2, 4)
# Контур крупнее этого — административная единица (район, община), а не сам населённый
# пункт: в данных встречаются такие «пятна» до 500 км², их брать нельзя.
PLACE_POLY_MAX_KM2 = 120.0
# Контур засчитывается, только если внутри РЕАЛЬНАЯ застройка: доля застроенных ячеек
# от площади контура ИЛИ абсолютная застроенная площадь. Отсекает административные
# границы (сельсовет вокруг села), оставляя сам населённый пункт.
PLACE_BUILT_FRAC = 0.15
PLACE_BUILT_MIN_KM2 = 2.0

# Запас обрезки геометрии вокруг участка, км. complete_relations=True тянет объекты
# далеко за bbox (замер: трубопровод от −94 до +94 км при участке ±37) — без клипа это
# лишний вес в кэше и линии, уходящие за карту.
#
# ⚠️ 04.09.2026: было 10 км, стало 3. С разделением «область показа / район расчёта»
# (план 8, задача 8.2) слои читаются по РАЙОНУ, и запас в 10 км выводил за его границу
# заметную полосу дорог и рек — карта выглядела так, будто район больше, чем он есть.
# Три километра оставлены намеренно: линия, чуть выходящая за рамку, не должна
# обрываться ровно по ней, иначе край района выглядит обрезанным ножницами.
OSM_CLIP_MARGIN_KM = 3.0


def layers_from_osm(pbf_path, lon0, lat0, bbox_lonlat=None):
    """Прочитать локальный .osm.pbf и вернуть слои в км-фрейме (lon0,lat0).

    Требует pyrosm/geopandas (устанавливаются отдельно, см. requirements). Читает
    только выбранный bbox (по THREAT_BBOX_LONLAT, если не задан), фильтрует объекты
    по OSM_LAYER_FILTERS. Это единая логика чтения OSM — общая для CLI-скрипта и
    окна «Выбрать цифровые карты» в интерфейсе (без дублирования)."""
    try:
        from pyrosm import OSM
    except Exception as e:
        raise RuntimeError(
            "Для чтения .osm.pbf нужен pyrosm/geopandas:\n"
            "  pip install pyrosm geopandas shapely pyproj\n"
            f"(исходная ошибка: {e})")
    lo, la, ho, ha = bbox_lonlat or THREAT_BBOX_LONLAT
    # complete_relations=True: у мультиполигонов-relations (контуры городов) добираются
    # члены за пределами bbox — иначе крупный город на краю участка приходит с дыркой.
    osm = OSM(pbf_path, bounding_box=[lo, la, ho, ha], complete_relations=True)
    # рамка обрезки в км: участок + запас (complete_relations тянет объекты далеко за
    # bbox — без клипа в кэш попадали линии за сотню км от участка)
    clip = _clip_box_km(lon0, lat0, (lo, la, ho, ha), OSM_CLIP_MARGIN_KM)
    out = {}
    for layer, flt in OSM_LAYER_FILTERS.items():
        try:
            gdf = osm.get_data_by_custom_criteria(
                custom_filter=flt, filter_type="keep",
                keep_nodes=False, keep_ways=True, keep_relations=True)
        except Exception:
            continue
        if gdf is None or len(gdf) == 0:
            continue
        parts = []
        for geom in gdf.geometry:
            if geom is None or geom.is_empty:
                continue
            parts += _geom_to_kms(geom, lon0, lat0)
        parts = _clip_parts(parts, clip)
        if parts:
            out[layer] = parts
    place_pts, place_poly, place_names = _places_from_osm(osm, lon0, lat0, clip)
    if len(place_pts):
        out[PLACE_LAYER] = [place_pts]
        # имена идут ОТДЕЛЬНЫМ слоем в том же порядке, что и точки: массив строк рядом
        # с числовым (N,3) не положишь, а в вес этот слой не входит — как и place_pts,
        # он служебный (сборка веса идёт по списку THREAT_LAYERS, чужие ключи не видит)
        out[PLACE_NAME_LAYER] = [np.asarray(place_names, dtype="U")]
    if place_poly:
        out[PLACE_POLY_LAYER] = place_poly
    return out


# ⚠️ ЧТЕНИЕ РАСТРА ВЫСОТ — ПО ОДНОМУ ПОТОКУ ЗА РАЗ.
#
# Растр открывается из ДВУХ мест: `load_dem_grid` — в фоновом потоке пересборки карты
# (`_run_async("build", …)`), `load_dem_display` — в основном, при отрисовке. GDAL, на
# котором стоит rasterio, не рассчитан на одновременную работу с одним файлом из разных
# потоков: это не исключение, а падение процесса целиком — программа просто закрывается.
# Симптом заказчика 04.09.2026: «при повторном добавлении рельефа программа падает»,
# причём в самой модели те же четыре цикла включения-выключения проходят без ошибок.
# Замок дешёвый: чтение и так идёт секунды, а параллельным оно быть не должно.
_DEM_LOCK = threading.Lock()


def load_dem_grid(grid, lon0, lat0, path=None):
    """Высоты рельефа, приведённые к ячейкам сетки: массив (ny,nx) в метрах, NaN — нет
    данных. None, если файла нет (тогда слой рельефа просто не участвует).

    Поддерживает GeoTIFF (через rasterio) и «сырой» SRTM .hgt — последний читается
    чистым numpy: это квадрат 1201² или 3601² int16 big-endian на тайл 1°×1°, где имя
    файла задаёт юго-западный угол (например N48E038.hgt).
    Файл ищется в geo_cache: THREAT_DEM_FILE + .tif/.tiff/.hgt."""
    files = dem_files()
    if path:
        files = [path] + [p for p in files if p != path]
    files = [p for p in files if p and os.path.exists(p)]
    if not files:
        return None
    gx, gy = grid.cell_centers_km()
    lon, lat = km_to_lonlat(gx, gy, lon0, lat0)
    # размер ячейки в градусах — по нему высота усредняется ПО ЯЧЕЙКЕ, а не берётся
    # из случайного пикселя (ячейка 500 м ≈ 400 пикселей SRTM)
    cell_deg = (grid.h / _km_per_deg_lon(lat0), grid.h / _KM_PER_DEG_LAT)
    out = np.full(gx.shape, np.nan)
    for p in files:
        try:
            if p.lower().endswith((".tif", ".tiff")):
                with _DEM_LOCK:                    # GDAL — по одному потоку за раз
                    vals = _sample_geotiff(p, lon, lat, cell_deg)
            else:
                vals = _sample_hgt(p, lon, lat)    # .hgt читается чистым numpy, замок не нужен
        except Exception:
            continue
        if vals is not None:
            take = np.isnan(out) & np.isfinite(vals)
            out[take] = vals[take]
    return out if np.isfinite(out).any() else None


def dem_files():
    """Файлы рельефа участка: `THREAT_DEM_FILE` + .tif/.tiff/.hgt в папке участка (с
    откатом в корень кэша), плюс любые тайлы .hgt рядом — и в папке участка, и в корне
    (N62E040.hgt и т.п.). Пустой список — рельефа нет, и кнопка «Добавить рельеф»
    должна быть неактивна."""
    root = geo_cache_root()
    area = area_cache_dir()
    # у участка может не быть своего DEM (THREAT_DEM_FILE = ""): тогда именованного
    # файла не ищем, но .hgt-тайлы рядом всё равно подхватываем
    cand = ([area_file(THREAT_DEM_FILE + ext)
             for ext in (".tif", ".tiff", ".hgt")] if THREAT_DEM_FILE else [])
    for d in ([area, root] if area != root else [root]):
        if os.path.isdir(d):
            cand += [os.path.join(d, f) for f in sorted(os.listdir(d))
                     if f.lower().endswith(".hgt")]
    seen, out = set(), []
    for p in cand:
        if p not in seen and os.path.exists(p):
            seen.add(p); out.append(p)
    return out


def load_dem_display(lon0, lat0, bbox_km, max_px=1400):
    """Высоты для ОТРИСОВКИ: окно участка в исходном разрешении растра, прорежённое до
    ~max_px по длинной стороне. Возвращает (высоты (ny,nx) в метрах, extent_km) или None.

    Зачем отдельно от `load_dem_grid`: весовая сетка 500 м для показа слишком груба —
    рельеф выглядит лего-кубиками, по такой картинке местность не узнать. Здесь берём
    родные 30 м и прореживаем: 1400 px по длинной стороне — это ~3.6 МБ RGBA, рисуется
    мгновенно. В вес эта версия НЕ идёт, она только для глаз.

    Массив переворачивается по вертикали: у GeoTIFF строка 0 — северный край, а у нашей
    сетки строка 0 — южный (ImageItem рисуется от `oy` вверх)."""
    files = [p for p in dem_files() if p.lower().endswith((".tif", ".tiff"))]
    if not files:
        return None
    try:
        import rasterio
        from rasterio.windows import Window
    except Exception:
        return None
    x0, x1, y0, y1 = [float(v) for v in bbox_km]
    lon_w, lat_s = km_to_lonlat(np.array(x0), np.array(y0), lon0, lat0)
    lon_e, lat_n = km_to_lonlat(np.array(x1), np.array(y1), lon0, lat0)
    try:
        with _DEM_LOCK, rasterio.open(files[0]) as ds:        # GDAL — по одному потоку
            inv = ~ds.transform
            c_w, r_n = inv * (float(lon_w), float(lat_n))     # север -> меньший row
            c_e, r_s = inv * (float(lon_e), float(lat_s))
            c0 = int(np.clip(np.floor(min(c_w, c_e)), 0, ds.width - 1))
            c1 = int(np.clip(np.ceil(max(c_w, c_e)), 1, ds.width))
            r0 = int(np.clip(np.floor(min(r_n, r_s)), 0, ds.height - 1))
            r1 = int(np.clip(np.ceil(max(r_n, r_s)), 1, ds.height))
            if c1 <= c0 or r1 <= r0:
                return None
            band = ds.read(1, window=Window(c0, r0, c1 - c0, r1 - r0)).astype(np.float64)
            step = max(1, int(np.ceil(max(band.shape) / float(max_px))))
            band = band[::step, ::step]
            if ds.nodata is not None:
                band[band == float(ds.nodata)] = np.nan
            band[band < -1000.0] = np.nan
            # фактические границы окна в км (строки/столбцы обрезались до целых пикселей)
            lon_a, lat_b = ds.transform * (c0, r0)            # северо-запад окна
            lon_b, lat_a = ds.transform * (c1, r1)            # юго-восток окна
            ex0, ey0 = lonlat_to_km(np.array(lon_a), np.array(lat_a), lon0, lat0)
            ex1, ey1 = lonlat_to_km(np.array(lon_b), np.array(lat_b), lon0, lat0)
    except Exception:
        return None
    if not np.isfinite(band).any():
        return None
    return np.flipud(band), (float(ex0), float(ex1), float(ey0), float(ey1))


from .geo_frame import km_to_lonlat            # noqa: E402,F401 — общий фрейм (8.3)


def _sample_geotiff(path, lon, lat, cell_deg=None):
    """Выборка высот GeoTIFF в точках (lon,lat).

    `cell_deg` — размер ячейки сетки в градусах (dlon, dlat). Задан — берём СРЕДНЕЕ по
    ячейке, не задан — ближайший пиксель.

    Зачем среднее: ячейка сетки 500 м — это около 400 пикселей SRTM, и брать один из них
    было лотереей. Замер расхождения с усреднением: медиана 1.4 м, 95-й процентиль 6.4 м,
    максимум 42.5 м на обрывах. Вдобавок SRTM — модель ПОВЕРХНОСТИ, и одиночный пиксель
    мог попасть на крону дерева. Считается через интегральное изображение: одна свёртка
    на весь растр независимо от числа ячеек."""
    try:
        import rasterio
    except Exception:
        return None
    with rasterio.open(path) as ds:
        band = np.asarray(ds.read(1), dtype=np.float64)
        bad = ~np.isfinite(band)
        if ds.nodata is not None:
            bad |= (band == float(ds.nodata))
        bad |= (band < -1000.0)                     # типовые «пустые» значения SRTM
        inv = ~ds.transform
        cols, rows = inv * (lon.ravel(), lat.ravel())
        c = np.asarray(cols).reshape(lon.shape)
        r = np.asarray(rows).reshape(lon.shape)
        if cell_deg is None:                        # ближайший пиксель
            ci = np.rint(c).astype(int); ri = np.rint(r).astype(int)
            ok = ((ci >= 0) & (ci < ds.width) & (ri >= 0) & (ri < ds.height))
            vals = np.full(lon.shape, np.nan)
            take = ok & ~bad[np.clip(ri, 0, ds.height - 1), np.clip(ci, 0, ds.width - 1)]
            vals[take] = band[ri[take], ci[take]]
            return vals
        hx = 0.5 * abs(float(cell_deg[0]) / ds.transform.a)   # полуячейка в пикселях
        hy = 0.5 * abs(float(cell_deg[1]) / ds.transform.e)
        return _block_mean_at(band, bad, r, c, hy, hx)


def _block_mean_at(band, bad, r, c, hy, hx):
    """Среднее значение растра по прямоугольнику (2hy × 2hx пикселей) вокруг каждой точки
    (r, c) — через интегральное изображение: O(растр + точки), а не O(точки × окно).
    Пустые пиксели (`bad`) не участвуют ни в сумме, ни в счётчике."""
    good = (~bad).astype(np.float64)
    ny, nx = band.shape
    ii = np.zeros((ny + 1, nx + 1), np.float64)
    ii[1:, 1:] = np.cumsum(np.cumsum(np.where(bad, 0.0, band), axis=0), axis=1)
    nn = np.zeros((ny + 1, nx + 1), np.float64)
    nn[1:, 1:] = np.cumsum(np.cumsum(good, axis=0), axis=1)
    r0 = np.clip(np.floor(r - hy).astype(int), 0, ny)
    r1 = np.clip(np.ceil(r + hy).astype(int), 0, ny)
    c0 = np.clip(np.floor(c - hx).astype(int), 0, nx)
    c1 = np.clip(np.ceil(c + hx).astype(int), 0, nx)
    r1 = np.maximum(r1, np.minimum(r0 + 1, ny))     # окно не бывает пустым
    c1 = np.maximum(c1, np.minimum(c0 + 1, nx))
    tot = ii[r1, c1] - ii[r0, c1] - ii[r1, c0] + ii[r0, c0]
    cnt = nn[r1, c1] - nn[r0, c1] - nn[r1, c0] + nn[r0, c0]
    out = np.full(r.shape, np.nan)
    has = cnt > 0
    out[has] = tot[has] / cnt[has]
    return out


_HGT_NAME = re.compile(r"([NS])(\d{2})([EW])(\d{3})", re.I)


def _sample_hgt(path, lon, lat):
    """Выборка высот из сырого SRTM .hgt (int16 big-endian, тайл 1°×1°)."""
    m = _HGT_NAME.search(os.path.basename(path))
    if not m:
        return None
    lat0_t = int(m.group(2)) * (1 if m.group(1).upper() == "N" else -1)
    lon0_t = int(m.group(4)) * (1 if m.group(3).upper() == "E" else -1)
    raw = np.fromfile(path, dtype=">i2")
    n = int(round(math.sqrt(raw.size)))
    if n * n != raw.size:
        return None
    tile = raw.reshape(n, n).astype(float)
    tile[tile <= -32768] = np.nan
    # строка 0 — СЕВЕРНЫЙ край тайла
    fy = (lat0_t + 1 - lat) * (n - 1)
    fx = (lon - lon0_t) * (n - 1)
    r = np.rint(fy).astype(int)
    c = np.rint(fx).astype(int)
    ok = (r >= 0) & (r < n) & (c >= 0) & (c < n)
    vals = np.full(lon.shape, np.nan)
    vals[ok] = tile[r[ok], c[ok]]
    return vals


def _clip_box_km(lon0, lat0, bbox_lonlat, margin_km):
    """Рамка обрезки (x0,x1,y0,y1) в км: участок плюс запас со всех сторон."""
    x0, x1, y0, y1 = bbox_lonlat_to_km(bbox_lonlat, lon0, lat0)
    m = float(margin_km)
    return (x0 - m, x1 + m, y0 - m, y1 + m)


def _clip_parts(parts, clip):
    """Оставить только куски геометрии, попадающие в рамку.

    Линия рвётся на подотрезки по признаку «точка внутри рамки»: то, что уходит за
    участок, отбрасывается, а проходящая насквозь линия сохраняется куском внутри.
    Полностью внешние объекты исчезают — за этим и нужен клип."""
    x0, x1, y0, y1 = clip
    out = []
    for p in parts:
        P = np.asarray(p, float)
        if P.ndim != 2 or len(P) < 2:
            continue
        inside = ((P[:, 0] >= x0) & (P[:, 0] <= x1) &
                  (P[:, 1] >= y0) & (P[:, 1] <= y1))
        if inside.all():
            out.append(P)
            continue
        if not inside.any():
            continue
        # разрезать на непрерывные куски внутри рамки; на выходе за край добавляем не
        # исходную дальнюю точку (она уводила линию на длину сегмента за рамку —
        # замер: до 11.7 км при допуске 10), а точку ПЕРЕСЕЧЕНИЯ с границей
        idx = np.nonzero(inside)[0]
        for s in np.split(idx, np.nonzero(np.diff(idx) > 1)[0] + 1):
            a, b = int(s[0]), int(s[-1])
            piece = [P[a:b + 1]]
            if a > 0:
                piece.insert(0, _edge_point(P[a], P[a - 1], clip)[None, :])
            if b < len(P) - 1:
                piece.append(_edge_point(P[b], P[b + 1], clip)[None, :])
            seg = np.vstack(piece)
            if len(seg) >= 2:
                out.append(seg)
    return out


def _edge_point(p_in, p_out, clip):
    """Точка на отрезке p_in→p_out, лежащая на границе рамки clip (p_in внутри)."""
    x0, x1, y0, y1 = clip
    d = p_out - p_in
    t = 1.0
    for lo, hi, i in ((x0, x1, 0), (y0, y1, 1)):
        if d[i] > 1e-12:
            t = min(t, (hi - p_in[i]) / d[i])
        elif d[i] < -1e-12:
            t = min(t, (lo - p_in[i]) / d[i])
    return p_in + max(0.0, min(1.0, t)) * d


def _places_from_osm(osm, lon0, lat0, clip):
    """Населённые пункты -> (точки, контуры).

    * точки  — массив (N,3): x_км, y_км, код типа (PLACE_CODES);
    * контуры — список массивов (M,3): те же координаты + код в каждой строке.

    Контуры есть примерно у трети пунктов и это ГОТОВАЯ граница НП — она точнее
    пятна застройки (внутри города парки, площади и промзоны не размечены как
    landuse=residential, и по одной застройке в центре зияли дыры). Для остальных
    пунктов остаётся точка, а границы даёт пятно застройки вокруг неё."""
    empty = (np.empty((0, 3), np.float32), [], np.empty(0, "<U1"))
    try:
        gdf = osm.get_data_by_custom_criteria(
            custom_filter=OSM_PLACE_FILTER, filter_type="keep",
            keep_nodes=True, keep_ways=True, keep_relations=True)
    except Exception:
        return empty
    if gdf is None or len(gdf) == 0 or "place" not in gdf.columns:
        return empty
    x0, x1, y0, y1 = clip
    tags = gdf["tags"] if "tags" in gdf.columns else [None] * len(gdf)
    pts, polys, names = [], [], []
    for geom, kind, tg in zip(gdf.geometry, gdf["place"], tags):
        code = PLACE_CODES.get(str(kind), -1)
        if code < 0 or geom is None or geom.is_empty:
            continue
        c = geom.centroid
        x, y = lonlat_to_km(c.x, c.y, lon0, lat0)
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            continue
        pts.append((float(x), float(y), float(code)))
        names.append(_place_name(tg))
        if geom.geom_type in ("Polygon", "MultiPolygon"):
            for ring in _geom_to_kms(geom, lon0, lat0):
                R = np.asarray(ring, float)
                if len(R) < 3:
                    continue
                area = abs(np.dot(R[:, 0], np.roll(R[:, 1], 1)) -
                           np.dot(R[:, 1], np.roll(R[:, 0], 1))) * 0.5
                if area > PLACE_POLY_MAX_KM2:
                    continue          # это административная единица, а не сам НП
                polys.append(np.column_stack(
                    [R, np.full(len(R), float(code))]).astype(np.float32))
    return (np.asarray(pts, np.float32) if pts else np.empty((0, 3), np.float32),
            polys, np.asarray(names, dtype=object if names else "<U1"))


def _place_name(tags):
    """Название населённого пункта из тегов OSM. Пустая строка — имени нет.

    pyrosm отдаёт теги ОДНОЙ JSON-СТРОКОЙ в колонке `tags`, отдельной колонки `name`
    у этой выборки нет (проверено на ARX_1: колонки lon/tags/place/geometry/…).
    Предпочитается `name:ru`: у части пунктов основное имя записано на латинице."""
    if not tags:
        return ""
    try:
        d = json.loads(tags) if isinstance(tags, str) else dict(tags)
    except Exception:
        return ""
    for key in ("name:ru", "name"):
        v = d.get(key)
        if v:
            return str(v)
    return ""


def _geom_to_kms(geom, lon0, lat0):
    """shapely-геометрия -> список массивов (N,2) в км (линии и кольца полигонов).

    ВАЖНО: pyrosm отдаёт way не цельной линией, а MultiLineString из 2-точечных
    сегментов (дорога из 40 точек = 39 обрезков). Раньше обрезки складывались как есть
    — кэш содержал 10 717 «дорог» вместо 790, и прореживание при отрисовке выкидывало
    большинство (пропадали М-30/Р-66 вдали от города). Теперь смежные сегменты
    сшиваются linemerge в цельные линии."""
    out = []
    gt = geom.geom_type
    if gt == "LineString":
        out.append(_ll(np.asarray(geom.coords, float), lon0, lat0))
    elif gt == "MultiLineString":
        try:
            from shapely.ops import linemerge
            geom = linemerge(geom)             # сшить смежные сегменты одного way
        except Exception:
            pass
        if geom.geom_type == "LineString":
            out.append(_ll(np.asarray(geom.coords, float), lon0, lat0))
        else:                                  # несмежные куски — каждый цельным
            for g in geom.geoms:
                out.append(_ll(np.asarray(g.coords, float), lon0, lat0))
    elif gt == "GeometryCollection":
        for g in geom.geoms:
            out += _geom_to_kms(g, lon0, lat0)
    elif gt == "Polygon":
        out.append(_ll(np.asarray(geom.exterior.coords, float), lon0, lat0))
    elif gt == "MultiPolygon":
        for g in geom.geoms:
            out.append(_ll(np.asarray(g.exterior.coords, float), lon0, lat0))
    return out


def _load_layers_npz(path):
    """Слои из .npz, подготовленного офлайн-скриптом. Формат: ключи '<layer>__<i>'
    -> массив (N,2) в км; наличие ключа задаёт слой."""
    d = np.load(path, allow_pickle=True)
    out = {}
    for key in d.files:
        if "__" not in key:
            continue
        name, _idx = key.rsplit("__", 1)
        out.setdefault(name, []).append(d[key])
    return out


def _ll(seq, lon0, lat0):
    """[(lon,lat),...] -> (N,2) км в фрейме (lon0,lat0)."""
    arr = np.asarray(seq, float)
    x, y = lonlat_to_km(arr[:, 0], arr[:, 1], lon0, lat0)
    return np.column_stack([x, y])


def _synthetic_layers(lon0, lat0):
    """Демо-схема условного участка местности: русло реки (аттрактор+вода),
    несколько дорог, ж/д, ЛЭП, лесополос и контур застройки. Координаты приблизительные
    — этого достаточно, чтобы показать МЕХАНИКУ наложения, пока нет реальных OSM-данных.
    """
    # Русло реки (упрощённо, с С-З на Ю-В)
    river = [(38.42, 49.00), (38.49, 48.95), (38.60, 48.83), (38.78, 48.72),
             (38.95, 48.66), (39.10, 48.60), (39.28, 48.57), (39.45, 48.52)]
    # Крупные дороги
    road_a = [(38.49, 48.95), (38.62, 48.90), (38.80, 48.80), (38.95, 48.70),
              (39.10, 48.62), (39.31, 48.57)]              # вдоль реки
    road_b = [(38.40, 48.60), (38.75, 48.62), (39.10, 48.60), (39.55, 48.58)]
    road_c = [(38.55, 49.01), (38.60, 48.70), (38.66, 48.40)]   # поперечная
    # Местные дороги
    road_local1 = [(38.90, 48.95), (39.05, 48.75), (39.20, 48.55)]
    road_local2 = [(38.50, 48.45), (39.00, 48.48), (39.50, 48.45)]
    # Железная дорога
    rail = [(38.44, 48.98), (38.70, 48.85), (39.00, 48.72), (39.30, 48.60),
            (39.60, 48.50)]
    # ЛЭП
    power1 = [(38.40, 48.85), (38.90, 48.80), (39.40, 48.72)]
    power2 = [(38.70, 49.01), (38.75, 48.60), (38.80, 48.34)]
    # Трубопровод
    pipeline = [(38.38, 48.70), (38.95, 48.68), (39.55, 48.66)]
    # Лесополосы (характерные для степи полезащитные посадки)
    tree1 = [(38.60, 48.55), (39.10, 48.53)]
    tree2 = [(38.80, 48.90), (38.85, 48.45)]
    # Застройка (репеллер): грубые контуры городов
    def rect(clon, clat, dlon, dlat):
        return [(clon - dlon, clat - dlat), (clon + dlon, clat - dlat),
                (clon + dlon, clat + dlat), (clon - dlon, clat + dlat),
                (clon - dlon, clat - dlat)]
    built = [rect(38.49, 48.95, 0.06, 0.04),        # населённый пункт 1
             rect(39.31, 48.57, 0.05, 0.035),        # населённый пункт 2 (край bbox)
             rect(38.52, 48.34, 0.04, 0.03)]         # населённый пункт 3 (край)

    def L(seqs):
        return [_ll(s, lon0, lat0) for s in seqs]

    # мост и метка НП — чтобы демо-схема шла тем же путём, что и реальные данные
    bridge = [(38.62, 48.895), (38.64, 48.885)]        # переправа через русло
    places = np.array([[*lonlat_to_km(38.49, 48.95, lon0, lat0), 3.0],   # город
                       [*lonlat_to_km(39.31, 48.57, lon0, lat0), 1.0],   # деревня
                       [*lonlat_to_km(38.52, 48.34, lon0, lat0), 1.0]],  # деревня
                      dtype=np.float32)
    return {
        "river":      L([river]),
        "stream":     L([road_local1]),                # условный ручей (демо)
        "road_major": L([road_a, road_b, road_c]),
        "road_local": L([road_local1, road_local2]),
        "railway":    L([rail]),
        "power":      L([power1, power2]),
        "pipeline":   L([pipeline]),
        "tree_row":   L([tree1, tree2]),
        "bridge":     L([bridge]),
        "built_up":   L(built),
        PLACE_LAYER:  [places],
    }


# ----------------------------------------------------------------------
# Построение весовой карты из слоёв
# ----------------------------------------------------------------------
def build_threat_grid(layers, bbox_km, cell_km=None, enabled=None,
                      dem=None, free_points=None):
    """Собрать ThreatGrid: наложить слои с весами из THREAT_LAYERS, funnel-бонус,
    рельеф, разметить населённые пункты, отметить воду для запрета датчиков.

    enabled — набор имён слоёв для наложения (из окна «Выбрать цифровые карты»);
    None = все слои. Выключенный слой просто не участвует в сумме весов.
    dem — высоты, приведённые к сетке (ny,nx), или None (рельеф не учитывается).

    ПОРЯДОК ВАЖЕН: сначала линейные/площадные слои и бонусы, затем населённые пункты
    (город обнуляет всё, что накопилось в его ячейках), и рельеф — ПОСЛЕДНИМ.

    Почему рельеф последний. Он МНОЖИТ положительный вес, и гарантия «ноль остаётся нулём,
    коридор не исчезает» верна только относительно ИТОГОВОГО веса. Когда рельеф стоял до
    населённых пунктов, деревенский штраф добавлялся уже к ослабленному весу и уводил его
    в минус: дорога +10 в краю деревни (−8) без рельефа давала +2 и была коридором, а с
    рельефом на возвышенности 10·0.25 − 8 = −5.5, и коридор пропадал. Замер: так терялось
    117 ячеек помимо отсечки. Городу же порядок безразличен — обнулённый вес умножается
    на что угодно и остаётся нулём."""
    cell_km = (THREAT_CELL_M / 1000.0) if cell_km is None else cell_km
    g = ThreatGrid(bbox_km, cell_km)
    for name in THREAT_LAYER_ORDER:
        if name not in THREAT_LAYERS:
            continue
        if enabled is not None and name not in enabled:
            continue
        spec = THREAT_LAYERS[name]
        w = spec["weight"]
        attr = spec.get("attractor", True)
        objs = layers.get(name, [])
        if not objs:
            continue
        if spec["geom"] == "line":
            g.add_line_layer(name, objs, w, attractor=attr)
        elif spec["geom"] == "area":
            g.add_area_layer(name, objs, w, attractor=attr,
                             mark_urban=(name == "built_up"))
    g.set_bridge_mask_from_layer()          # мосты — реальные объекты OSM (bridge=yes)
    # funnel-бонус за пересечения слоёв-аттракторов
    g.add_intersection_bonus(THREAT_INTERSECTION_BONUS)
    # населённые пункты: город запрещён и обнулён, деревня — мягкий штраф от центра
    if enabled is None or "built_up" in enabled:
        g.apply_settlements(layers.get(PLACE_LAYER, [None])[0],
                            THREAT_URBAN_MIN_AREA_KM2, THREAT_TOWN_CITY_KM2,
                            THREAT_URBAN_BUFFER_KM,
                            THREAT_VILLAGE_PENALTY_CORE, THREAT_VILLAGE_PENALTY_EDGE,
                            THREAT_VILLAGE_RADIUS_MIN_KM, THREAT_VILLAGE_RADIUS_MAX_KM,
                            THREAT_RIVER_CORRIDOR_MAX_KM2,
                            zero_weight=THREAT_CITY_ZERO_WEIGHT,
                            place_polys=layers.get(PLACE_POLY_LAYER),
                            free_points=free_points,
                            village_floor=THREAT_VILLAGE_FLOOR,
                            village_skip_factor=THREAT_VILLAGE_SKIP_FACTOR,
                            village_townlike_med=THREAT_VILLAGE_TOWNLIKE_MED,
                            grade_on=THREAT_PLACE_GRADE_ON,
                            grade_tol=THREAT_PLACE_GRADE_TOL,
                            grade_min_sample=THREAT_PLACE_GRADE_MIN_SAMPLE,
                            grade_close_km=THREAT_PLACE_CLOSE_KM,
                            grade_z=THREAT_PLACE_GRADE_OUTLIER,
                            grade_strict_up=THREAT_PLACE_GRADE_STRICT_UP,
                            grade_mode=THREAT_PLACE_GRADE_MODE)
    # РЕЛЬЕФ — последним, по итоговому весу (см. «почему» в шапке функции)
    g.add_relief_layer(dem, THREAT_DEM_AREA_KM, THREAT_DEM_LOCAL_KM,
                       THREAT_DEM_AREA_FULL_M, THREAT_DEM_LOCAL_FULL_M,
                       THREAT_DEM_AREA_S, THREAT_DEM_LOCAL_S,
                       THREAT_DEM_K_MIN, THREAT_DEM_K_MAX, THREAT_DEM_FLOOR,
                       THREAT_DEM_CUT_AREA_M, THREAT_DEM_CUT_LOCAL_M)
    # вода -> запрет датчиков (буфер из конфига); только если слой реки включён
    if enabled is None or "river" in enabled:
        wet = list(layers.get("river", [])) + list(layers.get("stream", []))
        g.mark_water(wet, THREAT_WATER_BUFFER_M / 1000.0)
    return g


# ----------------------------------------------------------------------
# Модель вкладки 3 (обёртка для контроллера)
# ----------------------------------------------------------------------
class ThreatModel:
    """Состояние вкладки 3: цифровая карта угроз + расстановка датчиков по ней."""

    def __init__(self, params):
        self.p = params
        # РАМКА РАЙОНА МОДЕЛИРОВАНИЯ. По умолчанию — рамка участка из config, но её можно
        # задать мышью прямо в программе (план 8, задача 8.2), поэтому она ЖИВЁТ В МОДЕЛИ,
        # а не читается из константы при каждом построении.
        # ЯКОРЬ — ЮГО-ЗАПАДНЫЙ УГОЛ ОБЛАСТИ ПОКАЗА, один на весь сеанс (ОГРАНИЧЕНИЯ 5.1г).
        # Рабочий район его НЕ меняет: иначе уже нарисованное (тайлы, своя карта, рамка)
        # осталось бы в прежних километрах и разъехалось со слоями.
        self.lon0, self.lat0 = bbox_anchor_lonlat()
        self.bbox_lonlat = tuple(THREAT_WORK_LONLAT)
        self.bbox_km = bbox_lonlat_to_km(self.bbox_lonlat, self.lon0, self.lat0)
        # РАЙОН НЕ ЗАДАН, пока пользователь его не выбрал (мышью) и не загрузил свою
        # карту. До этого карта не строится: считать не по чему, а рисовать рамку и
        # слои «по умолчанию» — обманывать (правило заказчика 04.09.2026).
        self.area_ready = tuple(THREAT_WORK_LONLAT) != tuple(THREAT_BBOX_LONLAT)
        self.area_custom = self.area_ready
        self.layers = {}
        self.source = ""
        self.grid = None
        self._reset_sensors()          # sensors, sensors_big и их спутники — см. метод
        self.candidates = np.empty((0, 2), float)
        self._metrics = {}
        self.dem = None                # высоты рельефа на сетке (None — файла нет)
        self.relief_on = bool(THREAT_DEM_ON_START)   # учитывать рельеф в весе (кнопка)
        self._dem_display = None       # высоты в родном разрешении — только для показа
        self.data_path = None          # явный источник цифровых карт (.npz/.osm.pbf)
        self.enabled_layers = None     # набор включённых слоёв (None = все)
        self.target_km = None          # цель, заданная кликом («указать цель»)
        # СЕКТОР ПОЯВЛЕНИЯ БПЛА: точка клика по краю карты задаёт ось «цель → клик»,
        # раствор ±THREAT_SECTOR_HALF_DEG. None — сектор не задан, вход один (демо-точка).
        self.sector_point_km = None
        self.entry_points = []         # отобранные точки входа на краю карты, список (x,y)
        self.routes = []               # примеры коридоров-центров (список (M,2) км)
        self.iter_routes = []          # итерационные (стохастические) маршруты — накопление
        self.iter_iteration = 0        # номер текущей итерации (как t во вкладке 2)
        # ЗАГРУЖЕННАЯ ИЗ ФАЙЛА ВЫБОРКА ПРОЛЁТОВ (задача 8.6). ⚠️ НЕ путать с iter_routes —
        # отдельное поле, иначе счётчик итераций и вся статистика соврут (журнал, план 8
        # §8.6.4). Как и manual_sensors, это ВХОДНЫЕ ДАННЫЕ: не сбрасывается пересборкой
        # карты, сменой цели или входа — только новой загрузкой либо явной очисткой.
        self.loaded_routes = []        # список (M,2) км — то же представление, что iter_routes
        self.show_loaded_routes = False  # чекбокс «показать загруженную выборку»
        self.sensor_source = None      # чем объяснена последняя расстановка: iter/loaded/zone/weight/manual
        self._iter_ctx = None          # контекст выборки (проходимость/поле расстояний/вес)
        self._ctx_built_key = None     # при каком (разрыв, цель) собран контекст — см. _ensure_ctx
        self._iter_rng = None          # ГПСЧ выборки
        self.route_area = None         # маска ВСЕХ возможных мест пролёта (в пределах L_max)
        self.route_min_len = float("inf")   # мин. длина пути вход->цель, км
        # ЗАПРЕТНЫЕ ЗОНЫ, нарисованные пользователем. Живут ЗДЕСЬ, а не в сетке: сетка
        # пересобирается при смене слоёв или добавлении рельефа, а зоны от местности не
        # зависят и переживать пересборку обязаны (переносятся в сетку в `_sync_no_fly`).
        self.no_fly_zones = []
        # ДАТЧИКИ, ЗАДАННЫЕ ЧЕЛОВЕКОМ (мышью, строкой в таблице или из файла) — список
        # `model.sensors.ManualSensor`. По той же причине, что и зоны выше: заданы
        # человеком, от местности не зависят и переживают и пересборку карты, и смену
        # района. Якорь области при смене района не двигается, поэтому их километры
        # остаются верными. Расстановка читает их в `_place_by_routes` (статические —
        # принудительные позиции, см. `manual_of_type`).
        self.manual_sensors = []
        # КЭШ ТОГО, ЧТО ЧИТАЕТСЯ С ДИСКА (слои .osm.pbf и высоты). Сбрасывается вместе с
        # районом и источником — см. `_drop_read_cache`.
        self._read_key = None
        self._layers_cache = None
        self._source_cache = ""
        self._dem_cache = None
        # РАЙОН ИЗ config ПРОХОДИТ ТУ ЖЕ ПРИВОДКУ, что и заданный мышью: округление к
        # целому числу ячеек живёт в `set_area`, и рамка, записанная руками, без него
        # оставалась дробной. Замер: `work` давал 120.3 км при 241 ячейке (120.5) —
        # неполная ячейка по краю получала вес по куску своей площади. Найдено
        # `tools/flow_check.py` с первого же запуска.
        if self.area_ready:
            self.set_area(self.bbox_lonlat)

    def _drop_read_cache(self):
        """Забыть прочитанное с диска: район или источник сменились."""
        self._read_key = None
        self._layers_cache = None
        self._source_cache = ""
        self._dem_cache = None

    # ---- РАБОЧИЙ РАЙОН (план 8, задача 8.2) ----
    #
    # РАЗДЕЛЕНИЕ, о котором просил заказчик: «задаём рабочий район, но не ограничиваем
    # перемещение по большой области — просто не применяем вне района векторные слои и
    # рельеф, а саму карту показываем».
    #
    #   область ПОКАЗА  — участок из config (`arh`: Плесецк + 100 км, 291 × 250 км):
    #                     на неё скачаны тайлы и по ней грузятся векторные слои;
    #   район РАСЧЁТА   — прямоугольник внутри неё: сетка 500 м, веса, маршруты, датчики.
    #
    # Почему нельзя считать по всей области: сетка 500 м на 291 × 250 км — это
    # 582 × 499 = 290 тыс. ячеек против рабочих 21 тыс., в четырнадцать раз больше.
    def set_area(self, bbox_lonlat):
        """Задать РАБОЧИЙ РАЙОН (lon_min, lat_min, lon_max, lat_max).

        Пересчитывает якорь км-фрейма (юго-западный угол нового района) и сбрасывает всё,
        что от рамки зависит: сетку, слои, маршруты, датчики, контекст выборки. Сама карта
        пересобирается следующим `build()`.

        ⚠️ ЯКОРЬ МЕНЯЕТСЯ ВМЕСТЕ С РАЙОНОМ, а в кэше `.npz` лежат уже посчитанные
        километры — от ПРЕЖНЕГО якоря. Поэтому при своём районе слои читаются из
        `.osm.pbf` напрямую (`build`), иначе они легли бы со сдвигом молча."""
        lo, la, ho, ha = (float(v) for v in bbox_lonlat)
        lo, ho = min(lo, ho), max(lo, ho)
        la, ha = min(la, ha), max(la, ha)
        # ⚠️ ЯКОРЬ НЕ МЕНЯЕТСЯ ВМЕСТЕ С РАЙОНОМ. Сначала он переносился в угол нового
        # района — и всё, что уже нарисовано в прежнем фрейме (тайлы, своя карта, рамка,
        # подписи), оставалось на старых километрах: слои легли со сдвигом относительно
        # карты (замечание заказчика 04.09.2026). Якорь один на ОБЛАСТЬ ПОКАЗА, а район —
        # просто прямоугольник в тех же километрах. Заодно исчезла морока с округлением:
        # масштаб «градусы → км» больше не меняется под ногами.
        x0, y0 = lonlat_to_km(lo, la, self.lon0, self.lat0)
        x1, y1 = lonlat_to_km(ho, ha, self.lon0, self.lat0)
        # РАМКА — ЦЕЛОЕ ЧИСЛО ЯЧЕЕК: сетка шагает по 500 м, и неполная ячейка по краю
        # получила бы вес по куску своей площади. Округляем ВВЕРХ — район не станет меньше.
        cell = THREAT_CELL_M / 1000.0
        w = max(cell, math.ceil((float(x1) - float(x0)) / cell - 1e-9) * cell)
        h = max(cell, math.ceil((float(y1) - float(y0)) / cell - 1e-9) * cell)
        self.bbox_km = (float(x0), float(x0) + w, float(y0), float(y0) + h)
        ho2, ha2 = km_to_lonlat(self.bbox_km[1], self.bbox_km[3], self.lon0, self.lat0)
        self.bbox_lonlat = (lo, la, float(ho2), float(ha2))
        self.area_custom = True                    # район задан пользователем
        self.area_ready = True
        # всё, что считалось по прежней рамке, больше не годится
        self._drop_read_cache()                     # район сменился — читать заново
        self.grid = None
        self.layers = {}
        self.dem = None
        self._dem_display = None
        self._reset_sensors()
        self.candidates = np.empty((0, 2), float)
        self.routes = []
        self.iter_routes = []
        self.iter_iteration = 0
        self._iter_ctx = None
        self.route_area = None
        self.target_km = None                       # цель задавалась в прежних км
        self.sector_point_km = None
        self.entry_points = []
        return self.bbox_km

    def clear_area(self):
        """Убрать рабочий район: карта, слои, датчики и маршруты обнуляются.

        Возврат к стартовому состоянию — «район не задан»: на экране остаются только
        подложка и своя карта-картинка, а считать снова не по чему, пока район не задан
        заново (правило заказчика 04.09.2026)."""
        self.area_ready = False
        self.area_custom = False
        self._drop_read_cache()        # освободить память: слои и высоты больше не нужны
        self.bbox_lonlat = tuple(THREAT_WORK_LONLAT)
        self.bbox_km = bbox_lonlat_to_km(self.bbox_lonlat, self.lon0, self.lat0)
        self.grid = None
        self.layers = {}
        self.source = ""
        self.dem = None
        self._dem_display = None
        # ⚠️ РЕЛЬЕФ ТОЖЕ СБРАСЫВАЕТСЯ. Он оставался включённым после снятия района, и
        # следующее построение шло уже «с рельефом» — при том, что кнопка обещала
        # обратное, а высоты грузились по другой рамке (заказчик 04.09.2026: «после
        # моделирования рельеф не сбросился и при повторном построении карты упала
        # программа»).
        self.relief_on = bool(THREAT_DEM_ON_START)
        self._reset_sensors()
        self.candidates = np.empty((0, 2), float)
        self.routes = []
        self.iter_routes = []
        self.iter_iteration = 0
        self._iter_ctx = None
        self.route_area = None
        self.target_km = None
        self.sector_point_km = None
        self.entry_points = []
        self._metrics = {}

    def area_size_km(self):
        """Размер рабочего района (ширина, высота) в км — для проверок и подписей."""
        x0, x1, y0, y1 = self.bbox_km
        return (x1 - x0, y1 - y0)

    # ---- КООРДИНАТЫ НАРУЖУ (план 8, задача 8.3) ----
    #
    # Внутри всё считается в километрах локального фрейма — так короче и быстрее. Но
    # наружу (выгрузка истории полётов, выгрузка позиций датчиков, показ по клику)
    # координаты обязаны уходить в градусах: километры без якоря бессмысленны, а якорь
    # меняется вместе с районом. Перевод — единой формулой из `model/geo_frame.py`.
    def km_to_lonlat_arr(self, pts_km):
        """Массив (N,2) км -> массив (N,2) градусов (долгота, широта)."""
        a = np.asarray(pts_km, float).reshape(-1, 2)
        if len(a) == 0:
            return np.empty((0, 2), float)
        lon, lat = km_to_lonlat(a[:, 0], a[:, 1], self.lon0, self.lat0)
        return np.column_stack([np.asarray(lon, float), np.asarray(lat, float)])

    def sensors_lonlat(self, big=False):
        """Позиции датчиков в градусах: малые (по умолчанию) или большие."""
        return self.km_to_lonlat_arr(self.sensors_big if big else self.sensors)

    def routes_lonlat(self, routes=None):
        """Маршруты в градусах: список массивов (M,2). По умолчанию — накопленные."""
        src = self.iter_routes if routes is None else routes
        return [self.km_to_lonlat_arr(r) for r in src]

    # ---- ЗАГРУЖЕННАЯ ИЗ ФАЙЛА ВЫБОРКА ПРОЛЁТОВ (задача 8.6, model/flight_log.py) ----
    def load_routes_lonlat(self, routes_lonlat):
        """Загрузить маршруты В ГРАДУСАХ (из файла) → сохранить в километрах ТЕКУЩЕГО
        фрейма. Координаты самодостаточны (как и у `add_manual_lonlat`): маршрут из
        другого района просто ляжет в другое место сцены, без ошибки."""
        out = []
        for r in routes_lonlat:
            a = np.asarray(r, float).reshape(-1, 2)
            if len(a) < 2:
                continue
            x, y = lonlat_to_km(a[:, 0], a[:, 1], self.lon0, self.lat0)
            out.append(np.column_stack([np.asarray(x, float), np.asarray(y, float)]))
        self.loaded_routes = out
        return len(out)

    def clear_loaded_routes(self):
        """Убрать загруженную выборку и погасить чекбокс показа. Возвращает, сколько
        маршрутов было."""
        n = len(self.loaded_routes)
        self.loaded_routes = []
        self.show_loaded_routes = False
        return n

    def loaded_routes_lonlat(self):
        """Загруженные маршруты в градусах — для окна «Посмотреть маршруты» (задача 8.6,
        довесок 06.09.2026): показ ведётся в тех же градусах, что и в файле."""
        return [self.km_to_lonlat_arr(r) for r in self.loaded_routes]

    def iterations_complete(self):
        """Все ли T итераций прошли — условие, при котором осмысленно выгружать историю
        полётов (иначе выборка неполная и разная от запуска к запуску, план 8 §8.6.1)."""
        T = int(getattr(self.p, "threat_iter_routes", 0))
        return T > 0 and len(self.iter_routes) >= T

    def cells_lonlat(self):
        """ЦЕНТРЫ ячеек сетки в градусах, (M,2) — вместе с `flat_cells` дают
        «координата ячейки → её вес». Именно центры, а не углы: вес приписан центру."""
        if self.grid is None:
            return np.empty((0, 2), float)
        cx, cy = self.grid.cell_centers_km()
        pts = np.column_stack([np.asarray(cx, float).ravel(),
                               np.asarray(cy, float).ravel()])
        return self.km_to_lonlat_arr(pts)

    # ---- ЗАПРЕТНЫЕ ЗОНЫ (рисует пользователь на карте) ----
    def _sync_no_fly(self):
        """Перенести зоны в сетку (она их вычитает из проходимости) и сбросить то, что
        от проходимости зависит: контекст выборки, маршруты, область залёта."""
        if self.grid is not None:
            self.grid.set_no_fly_zones(self.no_fly_zones)
        self._iter_ctx = None
        self._ctx_built_key = None
        self.routes = []
        self.iter_routes = []
        self.iter_iteration = 0
        self.route_area = None

    def add_no_fly_zone(self, poly):
        """Добавить зону: список вершин [(x, y), …] в км. Меньше трёх вершин — не зона."""
        p = [(float(x), float(y)) for x, y in (poly or [])]
        if len(p) < 3:
            return False
        self.no_fly_zones.append(p)
        self._sync_no_fly()
        return True

    def clear_no_fly_zones(self):
        """Убрать все зоны — карта и веса при этом не меняются."""
        if not self.no_fly_zones:
            return False
        self.no_fly_zones = []
        self._sync_no_fly()
        return True

    def undo_no_fly_zone(self):
        """Убрать последнюю зону (для случая «нарисовал не туда»)."""
        if not self.no_fly_zones:
            return False
        self.no_fly_zones.pop()
        self._sync_no_fly()
        return True

    def no_fly_check(self):
        """Что зоны сделали с задачей. Возвращает словарь с полями:
        `blocked_entry` / `blocked_target` — точка вылета или цель ВНУТРИ зоны;
        `unreachable` — цель из точки вылета больше не достижима (зона перекрыла коридор
        целиком, а в обход весов нет); `area_km2` — площадь зон.

        Нужно потому, что «маршрутов нет» — законный исход: большая зона поперёк русла
        обрывает путь, и это не ошибка программы, а следствие запрета. Но сказать об этом
        надо явно, иначе пустой экран выглядит как поломка."""
        out = dict(zones=len(self.no_fly_zones), area_km2=0.0,
                   blocked_entry=False, blocked_target=False, unreachable=False)
        if not self.no_fly_zones or self.grid is None:
            return out
        m = self.grid.no_fly_mask()
        if m is None:
            return out
        out["area_km2"] = float(m.sum()) * self.grid.h * self.grid.h
        entry, target = self.entry_target_km()
        for key, pt in (("blocked_entry", entry), ("blocked_target", target)):
            ix = int(np.clip((pt[0] - self.grid.ox) / self.grid.h, 0, self.grid.nx - 1))
            iy = int(np.clip((pt[1] - self.grid.oy) / self.grid.h, 0, self.grid.ny - 1))
            out[key] = bool(m[iy, ix])
        # достижима ли цель: тот же критерий, что у самих маршрутов
        try:
            from .threat_routes import flight_envelope
            area, min_len = flight_envelope(self.grid, entry, target, self.p.threat_L_max,
                                            self._route_gap_km(), slack_km=self._route_slack_km())
            out["unreachable"] = not np.isfinite(min_len)
        except Exception:
            pass
        return out

    # ---- источник данных / выбор слоёв ----
    def set_data_source(self, path):
        """Задать файл цифровых карт (.npz или .osm.pbf); None -> авто (кэш/схема)."""
        self.data_path = path or None
        self.grid = None               # потребуется пересборка

    def set_enabled_layers(self, names):
        """Ограничить набор накладываемых слоёв (iterable имён); None -> все."""
        self.enabled_layers = set(names) if names is not None else None
        self.grid = None

    # ---- рельеф ----
    def has_dem(self):
        """Есть ли файл высот (по нему кнопка «Добавить рельеф» активна или нет)."""
        return bool(dem_files())

    def set_relief(self, on):
        """Включить/выключить учёт рельефа в весовой карте (кнопка «Добавить рельеф»).

        Карта ПЕРЕСОБИРАЕТСЯ целиком, а не досчитывается: рельеф обязан применяться ДО
        разметки населённых пунктов — город обнуляет всё, что накопилось в его ячейках,
        включая рельеф. Досчёт поверх готовой карты вернул бы вес городским ячейкам.
        Побочный эффект пересборки — сброс накопленных маршрутов, итераций и датчиков:
        они построены по другой карте и смешивать выборки нельзя."""
        want = bool(on) and self.has_dem()
        if want == self.relief_on:
            return False
        self.relief_on = want
        self.grid = None                               # потребуется пересборка
        # ⚠️ КАРТИНКА ВЫСОТ ТОЖЕ УСТАРЕЛА. `_dem_display` — вырезка растра по рамке, и
        # она кэшируется. Если рамка успела смениться (район задан заново), в памяти
        # лежит кусок ПРЕЖНЕГО района: рельеф «не воспроизводится» — на деле показывается
        # не то место или не показывается вовсе (заказчик 04.09.2026).
        self._dem_display = None
        return True

    def relief_display(self):
        """Высоты в родном разрешении для отрисовки: (массив, extent_km) или None.
        Грузится лениво — только когда включили показ карты высот, дальше из памяти."""
        if self._dem_display is None:
            self._dem_display = load_dem_display(self.lon0, self.lat0, self.bbox_km) or False
        return self._dem_display or None

    # ---- построение карты (наложение цифровых слоёв на сетку) ----
    def build(self):
        """Загрузить цифровые слои (из файла/кэша/демо) и наложить их на сетку 500 м —
        получить весовую (тепловую) карту. Сбрасывает прежние маршруты/датчики/контекст
        выборки (карта пересобрана). Возвращает готовую ThreatGrid."""
        # ВСЁ РАСЧЁТНОЕ — ТОЛЬКО В РАБОЧЕМ РАЙОНЕ (правило заказчика 04.09.2026).
        # Векторные слои, весовая карта и рельеф считаются и рисуются по `bbox_lonlat`,
        # а не по области показа. За его пределами остаётся только подложка: тайлы и своя
        # карта-картинка. Так «район моделирования» и на карте виден как район: где есть
        # слои — там идёт работа.
        #
        # ⚠️ ПРИ СВОЁМ РАЙОНЕ КЭШ `.npz` НЕ ГОДИТСЯ: в нём готовые километры от ПРЕЖНЕГО
        # якоря, а свой район ставит якорь в свой юго-западный угол — слои легли бы со
        # сдвигом, молча и без единой ошибки. Поэтому читаем исходный `.osm.pbf`.
        # ⚠️ ЧИТАЕМ КЭШ `.npz`, А НЕ `.osm.pbf` — даже когда район свой.
        #
        # Сначала при своём районе принудительно читался исходный `.osm.pbf`: кэш хранит
        # готовые километры, и при СМЕНЕ ЯКОРЯ они оказались бы сдвинуты. Но якорь больше
        # не меняется вместе с районом (ОГРАНИЧЕНИЯ 5.1л) — он один на область показа,
        # тот же, с которым собирался кэш. Значит кэш подходит, а лишнее отрежет сетка.
        #
        # Цена ошибки была высокой: чтение `.osm.pbf` требует `pyrosm`, и на машине, где
        # его нет, построение карты падало с «нужен pyrosm/geopandas» — хотя раньше та же
        # программа работала на одном numpy (заказчик 04.09.2026). Теперь геостек нужен
        # ТОЛЬКО для подготовки кэша (`tools/build_threat_grid.py`), один раз на участок.
        src_path = self.data_path
        # КЭШ ЧТЕНИЯ С ДИСКА (04.09.2026). Слои и высоты зависят только от РАЙОНА и
        # ИСТОЧНИКА, а `build()` вызывается на каждый чих: включили рельеф, сменили набор
        # слоёв, нажали «Применить». Замер до кэша: чтение `.osm.pbf` 4.3 с + чтение
        # высот 6.0 с = 9.6 с НА КАЖДОЕ построение, причём высоты читались даже когда
        # рельеф выключен. С кэшем повторное построение — доли секунды.
        key = (self.bbox_lonlat, src_path, THREAT_SOURCE_FILE)
        if getattr(self, "_read_key", None) == key and self._layers_cache is not None:
            self.layers, self.source = dict(self._layers_cache), self._source_cache
            self.dem = self._dem_cache
        else:
            self.layers, self.source = load_layers(
                self.lon0, self.lat0, src_path, self.bbox_lonlat)
            # Кэш собран на ВСЮ область, а работаем в районе: режем по нему с запасом
            # THREAT_LAYER_MARGIN_KM, иначе за границей района тянулась бы вся область —
            # он выглядел бы больше, чем есть (заказчик 04.09.2026).
            # ⚠️ РЕЖЕМ ВСЕГДА, а не только для «своего» района (заказчик 05.09.2026):
            # район по умолчанию тоже меньше области показа, и слои уходили за него.
            self.layers = _clip_layers_km(self.layers, self.bbox_km,
                                          THREAT_LAYER_MARGIN_KM)
            if self.area_custom:
                self.source += " · свой район"
            probe = ThreatGrid(self.bbox_km, THREAT_CELL_M / 1000.0)   # сетка для высот
            self.dem = load_dem_grid(probe, self.lon0, self.lat0)      # None, если файла нет
            self._read_key = key
            self._layers_cache = dict(self.layers)
            self._source_cache = self.source
            self._dem_cache = self.dem
        target = self.target_only_km()
        # вылет — только круг вокруг точки; цель — ещё и её населённый пункт целиком
        # плюс буфер (иначе подлёт к цели в центре города остаётся односторонним).
        # При заданном секторе точек вылета пять — свободна окрестность КАЖДОЙ, иначе
        # часть из них оказалась бы в запрещённой зоне и маршрут оттуда не начался бы.
        free_points = [(e[0], e[1], THREAT_ENTRY_FREE_KM, None)
                       for e in self.entry_points_km()]
        free_points.append((target[0], target[1], THREAT_TARGET_FREE_KM,
                            THREAT_TARGET_FREE_KM))
        # высоты грузим всегда (их можно показать на карте), но в ВЕС отдаём только по
        # кнопке «Добавить рельеф» — иначе карта менялась бы молча, от факта наличия файла
        self.grid = build_threat_grid(self.layers, self.bbox_km,
                                      enabled=self.enabled_layers,
                                      dem=(self.dem if self.relief_on else None),
                                      free_points=free_points)
        # ЗАПРЕТНЫЕ ЗОНЫ ПЕРЕЖИВАЮТ ПЕРЕСБОРКУ. Они нарисованы пользователем и от слоёв
        # с рельефом не зависят: сетка новая — зоны те же. Убираются только «Сбросом».
        self.grid.set_no_fly_zones(self.no_fly_zones)
        if self.dem is not None and self.relief_on:
            self.source += " + рельеф"
        # маркеры переправ — по РЕАЛЬНЫМ координатам мостов, а не по центрам ячеек
        self.layers["bridge_pts"] = self.grid.bridge_points_km(
            self.layers.get("bridge", []))
        # ⚠️ СБРАСЫВАЮТСЯ И БОЛЬШИЕ ТОЖЕ. Раньше здесь обнулялись только малые, и после
        # пересборки карты большие оставались висеть по координатам прежней расстановки —
        # асимметрия, заметная глазом на карте. На контрольный прогон не влияет: там
        # `build()` идёт ДО расстановки, обнулять нечего.
        self._reset_sensors()
        self.routes = []
        self.iter_routes = []
        self.iter_iteration = 0
        self._iter_ctx = None                          # карта пересобрана — контекст устарел
        return self.grid

    def _route_gap_km(self):
        """РАЗРЫВ между весовыми секторами (км) — задаётся в интерфейсе («Входные данные»).
        Пустое место короче разрыва БПЛА перелетит (коридоры сшиваются в один), длиннее —
        коридор разорван и маршрута через него нет. Управляет и областью залёта, и набором
        возможных маршрутов ещё ДО итераций."""
        return max(0.0, float(getattr(self.p, "threat_max_gap_km", 2.0)))

    def _route_slack_km(self):
        """УХОД ОТ ОРИЕНТИРА (км) — насколько маршрут отклоняется вбок от реки или дороги,
        вдоль которой идёт. Отдельно от разрыва: разрыв про перелёт через пустоту МЕЖДУ
        коридорами, а это — про свободу манёвра вдоль коридора (нужна для обхода холмов)."""
        return max(0.0, float(getattr(self.p, "threat_corridor_slack_km", 1.0)))

    def _ctx_key(self):
        """От чего зависит кэш контекста выборки: разрыв и уход от ориентира (оба меняют
        проходимость), цель и ТОЧКИ ВХОДА (сектор). Запас хода L_max сюда НЕ входит —
        контекст от него не зависит (см. build_iter_context)."""
        return (self._route_gap_km(), self._route_slack_km(), self.target_km,
                tuple(self.entry_points))

    def _ensure_ctx(self, entry, target):
        """Контекст выборки (проходимость, поля расстояний, VIA). Пересобирается, только
        если сменился разрыв, уход от ориентира или цель — иначе переиспользуется
        (веер VIA-полей дорогой)."""
        from .threat_routes import build_iter_context
        key = self._ctx_key()
        if self._iter_ctx is None or self._ctx_built_key != key:
            self._iter_ctx = build_iter_context(self.grid, entry, target,
                                                self._route_gap_km(),
                                                slack_km=self._route_slack_km())
            self._ctx_built_key = key
        return self._iter_ctx

    def _route_sig(self, poly):
        """Огрублённая подпись маршрута (для дедупликации почти одинаковых путей)."""
        g = self.grid
        ix = (((poly[:, 0] - g.ox) / g.h) // 4).astype(np.int64)
        iy = (((poly[:, 1] - g.oy) / g.h) // 4).astype(np.int64)
        return hash(frozenset(zip(ix.tolist(), iy.tolist())))

    # ---- маршруты пролёта вход->цель ----
    def plan_routes(self):
        """ВОЗМОЖНЫЕ маршруты вход→цель — показываются ДО итераций, вместе с областью залёта.

        Это ВЫБОРКА, а не полный перебор: всего таких маршрутов ~1e107 (см. count_routes) —
        их нельзя ни перечислить, ни нарисовать. Полную картину «где может пролететь БПЛА»
        даёт ЗАКРАШЕННАЯ ОБЛАСТЬ (route_area) — она точна и учитывает всё.

        Задача выборки — лечь по области МАКСИМАЛЬНО ШИРОКО и РАВНОМЕРНО, а не показать
        «типичный» путь. Поэтому:
          * РАЗБРОС и профиль расхода берутся веером ("mix"): маршруты уходят через
            разные VIA-точки по всей области, а не липнут к прямой;
          * ПРИОРИТЕТ ПО ВЕСУ берётся ИЗ ПАНЕЛИ (`THREAT_ROUTE_MODE_SRC = "panel"`).
            Раньше он тоже был "mix", а "mix" разыгрывает режим КАЖДОМУ маршруту — то
            есть треть синих линий строилась в режиме «приоритет МИНИМАЛЬНОГО веса»,
            целенаправленно уходя с тяжёлых коридоров, и выбор в панели на картину не
            влиял вовсе;
          * из накопленных кандидатов ЖАДНО отбираются те, что приносят больше ещё не
            покрытой ЦЕННОСТИ (тот же субмодулярный принцип, что у датчиков) — маршруты
            расходятся по разным коридорам, а не липнут к одному. Ценность учитывает вес
            ячейки (`THREAT_ROUTE_SPREAD_W`): раньше считалось голое число ячеек, и
            пустое поле стоило столько же, сколько река."""
        from .threat_routes import sample_one_route
        from config import (THREAT_ROUTE_COUNT, THREAT_ROUTE_POOL_MULT,
                            THREAT_ROUTE_SPREAD_W, THREAT_ROUTE_MODE_SRC)
        self.ensure_built()
        entry, target = self._refresh_envelope()       # авто-L_max + огибающая
        ctx = self._ensure_ctx(entry, target)          # общий контекст с итерациями (кэш)
        rng = np.random.default_rng()
        want = max(1, int(THREAT_ROUTE_COUNT))
        wmode = (getattr(self.p, "threat_iter_mode", "mix")
                 if THREAT_ROUTE_MODE_SRC == "panel" else "mix")
        # 1) НАБИРАЕМ пул кандидатов (больше, чем нужно): приоритет по весу — из панели,
        #    разброс и профиль расхода — веером
        pool, seen, tries = [], set(), 0
        cap = int(want * THREAT_ROUTE_POOL_MULT)
        since_new = 0
        while len(pool) < cap and tries < cap * 3 and since_new < 400:
            tries += 1
            r = sample_one_route(ctx, wmode, self.p.threat_L_max,
                                 self.p.threat_turn_interval_km, rng,
                                 spread="mix", spend="mix")
            if r is None:
                since_new += 1
                continue
            sig = self._route_sig(r)
            if sig in seen:                            # уже есть почти такой же маршрут
                since_new += 1
                continue
            seen.add(sig); pool.append(r); since_new = 0
        # 2) ОТБИРАЕМ из пула самые «расходящиеся» — максимальное покрытие области,
        #    но с учётом ценности ячеек: тяжёлый коридор дороже пустого поля
        self.routes = self._spread_over_area(pool, want, spread_w=THREAT_ROUTE_SPREAD_W)
        return self.routes

    def _spread_over_area(self, pool, want, spread_w=0.0):
        """Жадно отобрать `want` маршрутов, покрывающих область залёта КАК МОЖНО ШИРЕ.

        Каждый шаг берём маршрут, который приносит больше всего ЕЩЁ НЕ ПОКРЫТОЙ ценности,
        и помечаем задетые ячейки покрытыми. Так следующий маршрут вынужден идти по
        ДРУГОМУ коридору — выборка расходится по всей области, а не скучивается у
        кратчайшего пути. (Тот же жадный субмодулярный отбор, что у датчиков; гарантия
        ≥ (1−1/e) от оптимума.)

        `spread_w` — ЦЕННОСТЬ ВЕСА. Раньше считалось голое ЧИСЛО задетых ячеек, и пустое
        поле стоило ровно столько же, сколько река: выборка охотно уходила туда, где
        ориентиров нет вовсе. Теперь ценность ячейки = `1 + spread_w · вес(0…1)` —
        разброс сохраняется (единица есть у всех), но при равном покрытии предпочитается
        тяжёлый коридор. 0.0 -> прежнее поведение."""
        if not pool or want >= len(pool):
            return list(pool)
        g = self.grid
        ncell = g.ny * g.nx
        # матрица «маршрут × ячейка» (булева): что задевает каждый кандидат
        M = np.zeros((len(pool), ncell), bool)
        for i, r in enumerate(pool):
            ix = np.clip(((r[:, 0] - g.ox) / g.h).astype(np.int64), 0, g.nx - 1)
            iy = np.clip(((r[:, 1] - g.oy) / g.h).astype(np.int64), 0, g.ny - 1)
            M[i, iy * g.nx + ix] = True
        # ценность ячейки: нормировка веса та же, что в выборке маршрутов (p90 по
        # положительным) — чтобы «тяжело» значило одно и то же в обоих местах
        if spread_w > 0.0:
            w = np.clip(g.weight, 0.0, None)
            pos = w[w > 0]
            ref = float(np.percentile(pos, 90)) if pos.size else 1.0
            val = (1.0 + float(spread_w) * np.clip(w / max(ref, 1e-6), 0.0, 1.0)).ravel()
        else:
            val = np.ones(ncell, float)
        uncovered = np.ones(ncell, bool)
        chosen = []
        alive = np.ones(len(pool), bool)
        for _ in range(want):
            gain = (M & uncovered) @ val               # НОВАЯ ценность у каждого кандидата
            gain[~alive] = -1.0
            best = int(np.argmax(gain))
            if gain[best] <= 0.0:                      # новой ценности нет — веер исчерпан
                break
            alive[best] = False
            uncovered &= ~M[best]
            chosen.append(pool[best])
        if len(chosen) < want:                         # добить остатком (покрытие уже насыщено)
            chosen += [pool[i] for i in np.nonzero(alive)[0][:want - len(chosen)]]
        return chosen

    def count_routes(self):
        """ТОЧНОЕ число возможных маршрутов вход→цель внутри области залёта (без перебора).
        Их порядка 1e107 — отсюда и невозможность «показать все» иначе как областью."""
        from .threat_routes import count_possible_routes
        if self.grid is None or self.route_area is None or self._iter_ctx is None:
            return 0.0
        ctx = self._iter_ctx
        return count_possible_routes(np.asarray(self.route_area), ctx["gt"],
                                     ctx["start"], ctx["goal"])

    def _spend(self):
        """Профиль расхода запаса хода — повадка БПЛА для ИТЕРАЦИЙ (панель справа).
        На «возможные маршруты» не влияет: там перебирается веер всех профилей."""
        return getattr(self.p, "threat_spend", "late")

    # ---- ИТЕРАЦИОННЫЕ маршруты (стохастические пути по коридорам) ----
    # Пошаговая модель как во вкладке 2: iter_reset -> iter_step (××T) / iter_batch.
    def iter_reset(self):
        """Начать выборку заново: пересчитать авто-запас хода и огибающую, подготовить
        контекст выборки, обнулить счётчик. Возвращает True, если цель достижима."""
        self.ensure_built()
        entry, target = self._refresh_envelope()       # авто-L_max + огибающая
        self._ensure_ctx(entry, target)                # веер VIA-полей — один раз на (разрыв, цель)
        self._iter_rng = np.random.default_rng()
        self.iter_routes = []
        self.iter_iteration = 0
        return self._iter_ctx["reachable"]

    def iter_step(self):
        """Одна итерация: сгенерировать ОДИН маршрут (несколько попыток) и добавить его в
        накопление. Счётчик iter_iteration = число накопленных маршрутов. Возвращает
        маршрут (M,2) для анимации или None (не удалось за отведённые попытки)."""
        from .threat_routes import sample_one_route
        if self._iter_ctx is None and not self.iter_reset():
            return None
        route = None
        for _ in range(12):                             # попытки на итерацию (стохастика)
            route = sample_one_route(self._iter_ctx, self.p.threat_iter_mode,
                                     self.p.threat_L_max, self.p.threat_turn_interval_km,
                                     self._iter_rng, spread=self.p.threat_iter_spread,
                                     spend=self._spend())
            if route is not None:
                break
        if route is not None:
            self.iter_routes.append(route)
            self.iter_iteration = len(self.iter_routes)
        return route

    def iter_batch(self):
        """Сгенерировать T маршрутов сразу (без анимации). T = p.threat_iter_routes.

        ⚠️ ПОТОЛОК ПОПЫТОК МАСШТАБИРУЕТСЯ ОТ T. Раньше стоял `min(T*6, 800)`, и число
        800 обрезало заказ: при T = 400 попыток хватало лишь на **234** маршрута —
        генерация молча останавливалась, а на карте показывалось столько, сколько
        построилось. Дело не в дедупликации (замер: 0 отбраковок), а в доле отказов
        «маршрут не уложился в запас хода» — на реальных данных **71 %** попыток.
        Поэтому попыток нужно примерно вчетверо больше, чем маршрутов; берём восьмикратный
        запас. Защита от бесконечного цикла остаётся: `stale` — сколько попыток подряд
        разрешено не давать нового маршрута (тоже от T, иначе крупный заказ обрывался бы
        на середине)."""
        from .threat_routes import sample_one_route
        if not self.iter_reset():
            return []
        T = max(1, int(self.p.threat_iter_routes))
        cap = int(T * 8)                               # было min(T*6, 800) — см. шапку
        stale = max(350, T)
        seen, tries, since_new = set(), 0, 0
        while len(self.iter_routes) < T and tries < cap and since_new < stale:
            tries += 1
            r = sample_one_route(self._iter_ctx, self.p.threat_iter_mode,
                                 self.p.threat_L_max, self.p.threat_turn_interval_km,
                                 self._iter_rng, spread=self.p.threat_iter_spread,
                                 spend=self._spend())
            if r is None:
                since_new += 1
                continue
            sig = self._route_sig(r)
            if sig in seen:                            # дедуп почти одинаковых
                since_new += 1
                continue
            seen.add(sig); self.iter_routes.append(r); since_new = 0
        self.iter_iteration = len(self.iter_routes)
        return self.iter_routes

    # обратная совместимость: пакетная генерация в один вызов (кнопка/чекбокс)
    def iterate_routes(self):
        return self.iter_batch()

    def _refresh_envelope(self):
        """Пересчитать авто-запас хода и огибающую мест пролёта. Возвращает (вход, цель).
        Авто-L_max = |AB|+25 %, НО не меньше кратчайшего коридорного пути +20 % (иначе на
        «узкой» местности маршрут не уместился бы вовсе). Ручной L_max не трогаем."""
        from .threat_routes import flight_envelope
        g = self.grid
        gap = self._route_gap_km()                             # тот же порог, что у маршрутов
        slack = self._route_slack_km()                         # и тот же уход от ориентира
        entry, target = self.entries_target_km()               # при секторе — все пять точек
        self.sync_auto_L_max()                                 # предварительно |AB|+25 %
        _, self.route_min_len = flight_envelope(               # кратчайший путь (от L_max не зависит)
            g, entry, target, self.p.threat_L_max, gap, slack_km=slack)
        self.sync_auto_L_max(self.route_min_len)               # поднять до «кратчайший+20 %», если авто
        self.route_area, self.route_min_len = flight_envelope(
            g, entry, target, self.p.threat_L_max, gap, slack_km=slack)
        return entry, target

    def sync_auto_L_max(self, min_corridor_km=None):
        """Если запас хода НЕ задан руками — авто: |AB|·(1+THREAT_LMAX_AUTO_FRAC), но не меньше
        кратчайшего КОРИДОРНОГО пути с запасом THREAT_LMAX_CORRIDOR_FRAC (по прямой цель может
        быть близко, а по коридорам — вдвое дальше; без этого маршрут не уместился бы вовсе).
        Возвращает L_max, км."""
        if not getattr(self.p, "threat_L_max_manual", False):
            target = self.target_only_km()
            # при секторе точек входа пять: запас хода считаем по САМОЙ ДАЛЬНЕЙ, иначе
            # из неё маршрут не уместился бы и точка вылетала бы из выборки
            ab = max(float(np.hypot(target[0] - e[0], target[1] - e[1]))
                     for e in self.entry_points_km())
            lmax = ab * (1.0 + THREAT_LMAX_AUTO_FRAC)
            if min_corridor_km is not None and np.isfinite(min_corridor_km):
                lmax = max(lmax, min_corridor_km * (1.0 + THREAT_LMAX_CORRIDOR_FRAC))
            self.p.threat_L_max = round(lmax, 1)
        return self.p.threat_L_max

    def ensure_built(self):
        """Гарантировать, что карта построена (построить, если ещё нет), и вернуть сетку."""
        if self.grid is None:
            self.build()
        return self.grid

    # ---- кандидатные позиции датчика (сетка минус вода) ----
    def auto_cand_step_km(self):
        """Каким шаг сетки ДОЛЖЕН быть при текущих радиусах: половина R, 0.5…6 км.

        ⚠️ R берётся НАИМЕНЬШИЙ среди работающих малых типов (1–3), а не радиус типа 1.
        Типы живут в одной сетке кандидатов, и шаг, годный для крупного типа, для мелкого
        окажется грубым: дыра между кандидатами станет шире, чем тот видит (замер при
        R = 2 км — засечка 96.7 % при шаге 0.5 км против 82.7 % при 3 км). Пока работает
        один тип 1, значение то же, что и раньше."""
        from .sensors import min_small_radius
        return round(float(np.clip(0.5 * min_small_radius(self.p), 0.5, 6.0)), 2)

    def sync_cand_step(self, force=False):
        """Подогнать шаг сетки под радиус — но только КОГДА РАДИУС СМЕНИЛСЯ.

        Так пользователь может задать свой шаг и работать с ним, а при смене радиуса
        значение обновится само: держать шаг от прежнего радиуса почти всегда ошибка.

        Зачем вообще привязка: шаг — это шаг дискретизации пространства поиска (датчик
        нельзя поставить «куда угодно», перебирать континуум невозможно). Когда шаг
        КРУПНЕЕ зоны обзора, между соседними кандидатами остаётся дыра шире, чем видит
        датчик, и лучшая точка недоступна для выбора. Замер (150 маршрутов): при R = 2 км
        и пяти датчиках засечка 96.7 % при шаге 0.5 км, 82.7 % при 3 км и 17.3 % при 6 км.
        Чем шире обзор и чем больше датчиков, тем меньше значит шаг — при R = 6 км разницы
        нет вовсе. Половина радиуса — компромисс по цене: при R = 2 км это 4 436 кандидатов
        и 0.8 с против 17 847 и 3.2 с у шага 0.5 км, а выигрыш последнего — доли процента."""
        from .sensors import min_small_radius
        R = float(min_small_radius(self.p))
        # ⚠️ ГАЛОЧКА «АВТО» СНЯТА — шаг задан руками и держится, что бы ни менялось.
        # Раньше введённое значение жило только до следующей смены радиуса, и человек,
        # сознательно выбравший мелкий шаг, молча его терял (по образцу
        # `threat_L_max_manual`, где та же развилка решена флагом).
        if getattr(self.p, "threat_cand_step_manual", False):
            self._cand_step_for_R = R
            return float(self.p.threat_cand_step_km)
        if force or getattr(self, "_cand_step_for_R", None) != R:
            self.p.threat_cand_step_km = self.auto_cand_step_km()
            self._cand_step_for_R = R
        return float(self.p.threat_cand_step_km)

    def cand_step_km(self):
        """Шаг сетки кандидатных позиций, км (не меньше 0.5)."""
        return max(0.5, float(getattr(self.p, "threat_cand_step_km", 0.0) or 0.0))

    def candidate_positions(self):
        """Возможные места установки датчика — узлы регулярной сетки с шагом
        `cand_step_km()`, МИНУС вода (датчик на воду не ставим). Из них жадный алгоритм
        выбирает N лучших (см. place_sensors)."""
        g = self.ensure_built()
        step = self.cand_step_km()
        kx0, kx1, ky0, ky1 = self.bbox_km
        xs = np.arange(kx0 + 0.5 * step, kx1, step)
        ys = np.arange(ky0 + 0.5 * step, ky1, step)
        gx, gy = np.meshgrid(xs, ys)
        cand = np.column_stack([gx.ravel(), gy.ravel()])
        if self.p.threat_exclude_water and g.water_mask().any():
            cand = cand[~self._on_water(cand)]
        return cand

    def _on_water(self, pts):
        """Маска кандидатов, попавших в водную ячейку (или её буфер) — запрет датчика."""
        g = self.grid
        ix = np.floor((pts[:, 0] - g.ox) / g.h).astype(int)
        iy = np.floor((pts[:, 1] - g.oy) / g.h).astype(int)
        ok = (ix >= 0) & (ix < g.nx) & (iy >= 0) & (iy < g.ny)
        out = np.zeros(len(pts), bool)
        wm = g.water_mask()
        out[ok] = wm[iy[ok], ix[ok]]
        return out

    # ---- датчики, заданные человеком (задача 8.7) ----
    def _reset_sensors(self):
        """Обнулить расстановку — координаты И ИХ СПУТНИКИ разом.

        ⚠️ ОДНИМ МЕТОДОМ, а не тремя присваиваниями в каждом месте сброса. У массива
        `sensors` есть спутники той же длины (`sensors_type` — чей это тип, 1–3;
        `sensors_static` — закреплён ли человеком), и разойтись им нельзя: таблица в
        окне и подписи на карте читают их по одному индексу с координатами. Сбросов
        три (смена района, снятие района, пересборка карты), и рассинхрон был бы
        вопросом времени.

        ⚠️ `manual_sensors` ЗДЕСЬ НЕ ТРОГАЕТСЯ. Это не результат расчёта, а задание
        человека: оно переживает и пересборку карты, и смену района — как запретные
        зоны. Убирается только явно, из таблицы окна."""
        self.sensors = np.empty((0, 2), float)       # МАЛЫЕ датчики (типы 1–3)
        self.sensors_big = np.empty((0, 2), float)   # БОЛЬШИЕ (тип 4) — щит у цели
        self.sensors_type = np.empty(0, int)         # тип каждого малого: 1, 2 или 3
        self.sensors_static = np.empty(0, bool)      # РЕЖИМ датчика: «статический»
        self.sensors_big_static = np.empty(0, bool)  # то же для больших
        # ⚠️ ДВЕ РАЗНЫЕ ВЕЩИ, И ПУТАТЬ ИХ НЕЛЬЗЯ. `*_static` — РЕЖИМ, выбранный при
        # добавлении: статический не двигается и при итерациях, динамический — двигается.
        # `*_manual` — КТО ПОСТАВИЛ: позиция названа человеком (клик, таблица, файл), а
        # не подобрана алгоритмом. На карте это разные признаки: форма значка говорит о
        # режиме, чёрная окантовка — о том, что место выбрал человек (заказчик 05.09.2026).
        self.sensors_manual = np.empty(0, bool)      # позицию назвал человек
        self.sensors_big_manual = np.empty(0, bool)
        # ПЕРВОЕ ПОСТРОЕНИЕ — снимок для «анализа размещения» (задача 8.9). `None`
        # значит «ещё не снят»: следующая расстановка ДО итераций его установит заново.
        self.initial_sensors = None       # (N,2) км — малые, на момент первой расстановки
        self.initial_sensors_type = None  # тип каждого (1–3)
        self.initial_sensors_big = None   # (K,2) км — большие (тип 4)

    def _tag_sensors(self):
        """Проставить спутники расстановки: тип датчика, его режим и кто выбрал место.

        Тип заполняется, только если его не выставила сама расстановка (пока типы 2–3
        пусты, всё, что она вернула, — тип 1). Режим и «поставил человек» определяются
        по СОВПАДЕНИЮ КООРДИНАТЫ с заданной позицией, а не по индексу: жадный алгоритм
        возвращает позиции, а не номера кандидатов.

        ⚠️ Допуск сравнения 1 м. Точное равенство float здесь ненадёжно: позиция
        проходит через массив кандидатов и обратно, и последний бит мантиссы может не
        совпасть, а расхождение в метр на карте с ячейкой 500 м ничего не значит."""
        n, nb = len(self.sensors), len(self.sensors_big)
        if len(self.sensors_type) != n:
            self.sensors_type = np.ones(n, int)        # пока все малые — тип 1
        self.sensors_static = self._match_manual(self.sensors, small=True, only_static=True)
        self.sensors_big_static = self._match_manual(self.sensors_big, small=False,
                                                     only_static=True)
        self.sensors_manual = self._match_manual(self.sensors, small=True)
        self.sensors_big_manual = self._match_manual(self.sensors_big, small=False)
        if len(self.sensors_big_static) != nb:         # страховка от рассинхрона
            self.sensors_big_static = np.zeros(nb, bool)
            self.sensors_big_manual = np.zeros(nb, bool)

    def _match_manual(self, pts, small=True, tol_km=0.001, only_static=False):
        """Маска: какие из позиций `pts` названы ЧЕЛОВЕКОМ.

        `only_static=True` — считать лишь помеченные «статический». Так собираются две
        РАЗНЫЕ метки: режим датчика (статический не двигается и при итерациях) и «место
        выбрал человек» (клик, таблица, файл). До итераций они совпадают — там стоят все
        заданные, — а при итерациях расходятся: динамический уезжает, и «поставлен
        человеком» про него уже неверно."""
        pts = np.asarray(pts, float)
        out = np.zeros(len(pts), bool)
        if not len(pts):
            return out
        want = [m for m in self.manual_sensors
                if (m.static or not only_static)
                and ((m.type_id != 4) if small else (m.type_id == 4))]
        for m in want:
            d = np.hypot(pts[:, 0] - m.x_km, pts[:, 1] - m.y_km)
            j = int(np.argmin(d))
            if d[j] <= tol_km:
                out[j] = True
        return out

    def sensors_table(self):
        """ВСЕ датчики на карте одной таблицей — то, что показывает окно «Исходные
        данные» (задача 8.7) и что уходит в файл (8.7.7).

        Возвращает список словарей: номер, тип, режим, координаты в градусах и в км.
        Собирается из результата расстановки, поэтому таблица заполняется В ОБОИХ
        режимах работы окна: и когда позиции считает алгоритм, и когда их задал человек.
        Нумерация сквозная — сперва малые (типы 1–3), потом большие (тип 4)."""
        rows = []
        for pts, static, hand, big in (
                (self.sensors, self.sensors_static, self.sensors_manual, False),
                (self.sensors_big, self.sensors_big_static, self.sensors_big_manual, True)):
            pts = np.asarray(pts, float)
            for i in range(len(pts)):
                if big:
                    tid = 4
                elif i < len(self.sensors_type):
                    tid = int(self.sensors_type[i])
                else:
                    tid = 1
                st = bool(static[i]) if i < len(static) else False
                lon, lat = km_to_lonlat(pts[i, 0], pts[i, 1], self.lon0, self.lat0)
                rows.append(dict(n=len(rows) + 1, type_id=tid, static=st,
                                 # `by_hand` — место назвал человек, а не подобрал
                                 # алгоритм: на карте это чёрная окантовка значка
                                 by_hand=bool(hand[i]) if i < len(hand) else False,
                                 lon=float(lon), lat=float(lat),
                                 x_km=float(pts[i, 0]), y_km=float(pts[i, 1])))
        return rows

    def add_manual_sensor(self, type_id, static, x_km, y_km):
        """Добавить датчик, заданный человеком (клик по карте, строка таблицы, файл).

        Возвращает добавленную запись — окну нужно её показать в таблице."""
        from .sensors import ManualSensor, TYPE_IDS
        tid = int(type_id)
        if tid not in TYPE_IDS:
            raise ValueError("нет типа датчика %r (есть %s)" % (type_id, list(TYPE_IDS)))
        rec = ManualSensor(type_id=tid, static=bool(static),
                           x_km=float(x_km), y_km=float(y_km))
        self.manual_sensors.append(rec)
        return rec

    def _find_placed(self, x_km, y_km, tol_km=0.05):
        """Найти датчик РАССТАНОВКИ в этой точке: `("small"|"big", индекс)` или None.

        Нужно правке на карте: перетащили значок — надо поправить сам массив расстановки,
        а не только заявку. Иначе на карте оказались бы два датчика: старый из расчёта и
        новый как «только что поставленный»."""
        for kind, pts in (("small", self.sensors), ("big", self.sensors_big)):
            a = np.asarray(pts, float)
            if not len(a):
                continue
            d = np.hypot(a[:, 0] - float(x_km), a[:, 1] - float(y_km))
            j = int(np.argmin(d))
            if d[j] <= tol_km:
                return kind, j
        return None

    def move_placed_sensor(self, old_xy, new_xy, type_id, static):
        """Перенести датчик РАССТАНОВКИ в новую точку (правка мышью или строкой таблицы).

        Возвращает True, если датчик нашёлся и перенесён. Смена класса (малый ⇄ большой)
        разрешена: датчик убирается из одного массива и добавляется в другой — вместе со
        всеми спутниками, чтобы длины не разошлись.

        ⚠️ ПЕРЕНЕСЁННЫЙ СТАНОВИТСЯ ЗАДАННЫМ ВРУЧНУЮ. Место назвал человек — значит и метка
        на карте (чёрная окантовка) должна об этом говорить, и следующая расстановка
        обязана считаться с ним, а не переставлять обратно."""
        found = self._find_placed(*old_xy)
        if found is None:
            return False
        kind, j = found
        tid, st = int(type_id), bool(static)
        big_now = (kind == "big")
        want_big = (tid == 4)
        if big_now == want_big:                     # класс тот же — правим на месте
            if big_now:
                self.sensors_big[j] = [float(new_xy[0]), float(new_xy[1])]
                self.sensors_big_static[j] = st
                self.sensors_big_manual[j] = True
            else:
                self.sensors[j] = [float(new_xy[0]), float(new_xy[1])]
                self.sensors_type[j] = tid
                self.sensors_static[j] = st
                self.sensors_manual[j] = True
            return True
        # класс сменился — вынимаем из одного массива и дописываем в другой
        if big_now:
            self.sensors_big = np.delete(self.sensors_big, j, axis=0)
            self.sensors_big_static = np.delete(self.sensors_big_static, j)
            self.sensors_big_manual = np.delete(self.sensors_big_manual, j)
            self.sensors = np.vstack([self.sensors, [new_xy[0], new_xy[1]]])
            self.sensors_type = np.append(self.sensors_type, tid)
            self.sensors_static = np.append(self.sensors_static, st)
            self.sensors_manual = np.append(self.sensors_manual, True)
        else:
            self.sensors = np.delete(self.sensors, j, axis=0)
            self.sensors_type = np.delete(self.sensors_type, j)
            self.sensors_static = np.delete(self.sensors_static, j)
            self.sensors_manual = np.delete(self.sensors_manual, j)
            self.sensors_big = np.vstack([self.sensors_big, [new_xy[0], new_xy[1]]])
            self.sensors_big_static = np.append(self.sensors_big_static, st)
            self.sensors_big_manual = np.append(self.sensors_big_manual, True)
        return True

    def remove_placed_sensor(self, x_km, y_km):
        """Убрать датчик РАССТАНОВКИ в этой точке (клавиша Delete в режиме правки).

        Заявку человека, если она стояла в той же точке, убирает вызывающий: здесь
        трогается только результат расстановки."""
        found = self._find_placed(x_km, y_km)
        if found is None:
            return False
        kind, j = found
        if kind == "big":
            self.sensors_big = np.delete(self.sensors_big, j, axis=0)
            self.sensors_big_static = np.delete(self.sensors_big_static, j)
            self.sensors_big_manual = np.delete(self.sensors_big_manual, j)
        else:
            self.sensors = np.delete(self.sensors, j, axis=0)
            self.sensors_type = np.delete(self.sensors_type, j)
            self.sensors_static = np.delete(self.sensors_static, j)
            self.sensors_manual = np.delete(self.sensors_manual, j)
        return True

    def _mark_manual_placed(self):
        """Пометить заданные датчики как УЧТЁННЫЕ этой расстановкой.

        Нужно только показу. Пока расстановки не было, поставленный мышью датчик рисуется
        в точке клика — обратная связь на клик. После расстановки статический так и стоит
        (он в `sensors`), а ДИНАМИЧЕСКИЙ уехал туда, где его поставил алгоритм, и точку
        клика рисовать нельзя: на карте оставался лишний значок, и режимы выглядели
        неразличимыми — «динамические не двигаются» (заказчик 05.09.2026)."""
        for m in self.manual_sensors:
            m.placed = True

    def remove_manual_sensor(self, index):
        """Убрать запись по номеру строки таблицы. Неверный номер — молча ничего."""
        i = int(index)
        if 0 <= i < len(self.manual_sensors):
            return self.manual_sensors.pop(i)
        return None

    def clear_manual_sensors(self):
        """Убрать все заданные вручную датчики (кнопка «очистить» в таблице)."""
        n = len(self.manual_sensors)
        self.manual_sensors = []
        return n

    def in_iterations(self):
        """Идёт ли моделирование: датчики считаются по НАКОПЛЕННЫМ пролётам, а не по
        «всем возможным путям». Условие то же, что в `_sample_for_sensors`, — иначе фаза
        и выборка разошлись бы."""
        return len(self.iter_routes) >= 5

    def manual_locked(self, type_id=None):
        """Заданные человеком датчики, чью позицию СЕЙЧАС двигать нельзя.

        ⚠️ ЗАВИСИТ ОТ ФАЗЫ РАБОТЫ (правило заказчика 05.09.2026):

        | фаза | что закреплено | зачем так |
        |---|---|---|
        | ДО итераций (кнопка «Расставить датчики») | ВСЕ заданные, и статические, и динамические | человек задаёт обстановку: «эти датчики уже стоят вот здесь», алгоритм доставляет недостающие |
        | ПРИ итерациях (моделирование) | только СТАТИЧЕСКИЕ | в этом и смысл режима: статический стоит на месте, динамический перемещается вслед за накопленной картиной пролётов |

        Иначе различие режимов негде увидеть: до итераций оба стоят там, где заданы, и
        разница проявляется ровно тогда, когда расстановка пересчитывается по выборке."""
        lock_all = not self.in_iterations()
        tid = None if type_id is None else int(type_id)
        return [m for m in self.manual_sensors
                if (m.static or lock_all) and (tid is None or m.type_id == tid)]

    def manual_lonlat(self):
        """Заданные вручную датчики в градусах — для таблицы и для записи в файл.

        Возвращает список `(тип, статический, lon, lat)`. Перевод идёт по якорю ОБЛАСТИ,
        том же, что у всей сцены, поэтому координаты не зависят от выбранного района."""
        out = []
        for m in self.manual_sensors:
            lon, lat = km_to_lonlat(m.x_km, m.y_km, self.lon0, self.lat0)
            out.append((m.type_id, m.static, float(lon), float(lat)))
        return out

    def add_manual_lonlat(self, type_id, static, lon, lat):
        """Добавить датчик по ГРАДУСАМ — так приходят записи из файла (формат 8.7.7)."""
        x, y = lonlat_to_km(float(lon), float(lat), self.lon0, self.lat0)
        return self.add_manual_sensor(type_id, static, x, y)

    def update_manual_lonlat(self, rec, type_id, static, lon, lat):
        """Изменить УЖЕ ЗАДАННЫЙ датчик: тип, режим и координаты (кнопка «Изменить»).

        Правит запись НА МЕСТЕ, сохраняя её порядок в списке: строка остаётся там же в
        таблице. `placed` снимается — позиция стала другой, и до следующего расчёта её
        показывают как только что поставленную, иначе правка не была бы видна на карте."""
        from .sensors import TYPE_IDS
        tid = int(type_id)
        if tid not in TYPE_IDS:
            raise ValueError("нет типа датчика %r (есть %s)" % (type_id, list(TYPE_IDS)))
        x, y = lonlat_to_km(float(lon), float(lat), self.lon0, self.lat0)
        rec.type_id = tid
        rec.static = bool(static)
        rec.x_km, rec.y_km = float(x), float(y)
        rec.placed = False
        return rec

    def manual_outside_area(self):
        """Сколько заданных вручную датчиков лежит ВНЕ рабочего района.

        Не ошибка: человек мог задать позиции заранее, до выбора района, или намеренно
        поставить датчик за краем. Но знать об этом он должен — окно показывает число."""
        if not self.manual_sensors or not self.area_ready:
            return 0
        x0, x1, y0, y1 = self.bbox_km
        return sum(1 for m in self.manual_sensors
                   if not (x0 <= m.x_km <= x1 and y0 <= m.y_km <= y1))

    # ---- расстановка датчиков (ЭТАП 4: по тепловой карте МАРШРУТОВ, §4.6) ----
    def place_sensors(self):
        """Расставить датчики по тепловой карте МАРШРУТОВ (где реально/вероятно летает БПЛА),
        а не по сырому весу — поэтому минус-города датчикам не мешают. Выборка: идут ИТЕРАЦИИ
        → их пролёты; иначе → ВСЕ возможные пути (строятся, если их нет). Правила A/B и разнос."""
        g = self.ensure_built()
        # «РЕЖИМ АНАЛИЗА» (план 8, задача 8.9, довесок 06.09.2026): во время
        # моделирования расстановка (свои датчики и большие) не пересчитывается —
        # см. докстринг `threat_analysis_static` в config.py. Флаг проверяется В ДВУХ
        # МЕСТАХ, а не одним общим `return` в начале функции: у ручного режима свой
        # путь, где «пересчёт» — это просто чтение таблицы `manual_sensors», а не
        # прогон жадного алгоритма (см. ниже), и его замораживать не нужно — иначе
        # свежедобавленная вручную точка не появлялась бы на карте вовсе.
        analysis_freeze = (self.in_iterations()
                           and bool(getattr(self.p, "threat_analysis_static", False))
                           and (len(self.sensors) or len(self.sensors_big)))
        # РЕЖИМ «ЗАДАТЬ ПОЗИЦИИ» (задача 8.7): позиции не подбираются — датчики стоят
        # ровно там, где их задал человек, и кнопка «Расставить датчики» их НЕ ДВИГАЕТ.
        # ⚠️ Но ПОКАЗАТЕЛИ считаются: засечку и кратность заданной расстановки не по чему
        # мерить без выборки пролётов, и без неё отчёт показывал бы «засечено 0 %» просто
        # оттого, что маршрутов ещё нет. Поэтому выборка при необходимости строится —
        # ровно как в обычном режиме.
        if bool(getattr(self.p, "threat_manual_mode", False)):
            self.sensor_source = "manual"
            if len(self.iter_routes) < 5 and len(self.routes) < 5:
                self.plan_routes()
            # ⚠️ НО ПРИ МОДЕЛИРОВАНИИ И ЗДЕСЬ РАБОТАЕТ АЛГОРИТМ (заказчик 05.09.2026):
            # «во время моделирования итераций, если поставленный датчик динамический —
            # он перемещается, если статический — стоит на месте». Число датчиков берётся
            # из ТАБЛИЦЫ (сколько задано, столько и будет), а не из полей N: в этом режиме
            # поля N только для чтения и сами показывают счёт по таблице.
            # ⚠️ `analysis_freeze` гасит именно ЭТОТ прогон (жадный по `manual_locked`),
            # а не сам путь целиком: без него динамический вручную заданный датчик
            # переезжал бы во время моделирования, что и запрещает режим анализа.
            if self.in_iterations() and self._manual_dynamic_count() and not analysis_freeze:
                return self._place_manual_iter()
            return self._place_manual()
        if analysis_freeze:
            return
        self.candidates = self.candidate_positions()
        if len(self.iter_routes) < 5 and len(self.routes) < 5:
            self.plan_routes()                         # до итераций — по всем возможным путям
        sample = self._sample_for_sensors()
        # ОТКУДА ВЗЯТА ВЫБОРКА (задача 8.6, заказчик 06.09.2026) — контроллер показывает
        # это в заголовке («расстановка по загруженной истории»), не только для отладки.
        self.sensor_source = ("iter" if sample is self.iter_routes else
                              "loaded" if sample is self.loaded_routes else "zone")
        if sample and len(sample) >= 5:
            self.sensors = self._place_by_routes(self.candidates, sample)
        else:
            self.sensor_source = "weight"    # выборки мало — расстановка по сырому весу
            self.sensors = self._place_by_weight(self.candidates)
        # БОЛЬШИЕ датчики — отдельным правилом (круговой щит у цели), по той же выборке
        self.sensors_big = self._place_big(sample)
        self._tag_sensors()                            # спутники: тип и «статический»
        self._mark_manual_placed()                     # заявки учтены этой расстановкой
        if self.initial_sensors is None:
            # ПЕРВОЕ ПОСТРОЕНИЕ для «анализа размещения» (задача 8.9) — снимок ровно
            # ОДИН РАЗ за цикл, на самом первом расчёте датчиков, дальше НЕ перезаписы-
            # вается: иначе «до» и «после» совпали бы и сравнивать было бы нечего.
            #
            # ⚠️ БЕЗ УСЛОВИЯ «len(iter_routes) < 5» (было раньше, найден баг 06.09.2026).
            # Датчики вообще в первый раз считаются НЕ обязательно «до итераций» кнопкой
            # «Расставить датчики» — если сразу нажать «Пуск», `_maybe_refresh_sensors`
            # впервые вызовет это место уже при len(iter_routes) == 5 (порог
            # THREAT_SENSOR_REFRESH_EVERY), и с прежним условием снимок не брался НИКОГДА
            # за весь сеанс: заказчик увидел это как «датчики для анализа перемещаются
            # каждые 5 итераций» — на деле снимка не было вовсе, и сравнивать было не с
            # чем. Первый РЕАЛЬНО посчитанный набор — всегда лучшая точка отсчёта,
            # даже если он уже частично учитывает итерации.
            self.initial_sensors = np.array(self.sensors, float, copy=True)
            self.initial_sensors_type = np.array(self.sensors_type, int, copy=True)
            self.initial_sensors_big = np.array(self.sensors_big, float, copy=True)
        cells_xy, cells_w = g.flat_cells(positive_only=False)
        keep = cells_w != 0.0
        self._metrics = self._evaluate(cells_xy[keep], cells_w[keep])
        return self.sensors

    def sensor_movement(self):
        """Сопоставление ПЕРВОГО построения с ТЕКУЩИМ — «анализ размещения» (задача 8.9).

        Пусто, если снимок ещё не снят (`initial_sensors is None`) — до первой
        расстановки анализировать нечего. Большие датчики (тип 4) идут В ТУ ЖЕ задачу
        о назначениях под своим кодом типа — сопоставляются только между собой
        (§8.9.4: типы не смешивать)."""
        from . import sensor_track
        if self.initial_sensors is None:
            return []
        old_xy = np.vstack([self.initial_sensors, self.initial_sensors_big]) \
            if len(self.initial_sensors_big) else np.asarray(self.initial_sensors, float)
        old_types = np.concatenate([self.initial_sensors_type,
                                    np.full(len(self.initial_sensors_big), 4, int)])
        new_xy = np.vstack([self.sensors, self.sensors_big]) \
            if len(self.sensors_big) else np.asarray(self.sensors, float)
        new_types = np.concatenate([self.sensors_type,
                                    np.full(len(self.sensors_big), 4, int)])
        if len(old_xy) == 0 and len(new_xy) == 0:
            return []
        return sensor_track.match_by_type(old_xy, old_types, new_xy, new_types)

    def _manual_dynamic_count(self):
        """Сколько заданных человеком датчиков — ДИНАМИЧЕСКИЕ (их место можно менять)."""
        return sum(1 for m in self.manual_sensors if not m.static)

    def _manual_specs(self):
        """Типы для режима «Задать позиции»: сколько датчиков КАЖДОГО типа в таблице.

        Радиус и кратность берутся из полей окна (они там задаются и в этом режиме),
        а число — из таблицы: «на карте ровно то, что задано» (правило 8.8)."""
        from dataclasses import replace
        from .sensors import sensor_types
        have = {}
        for m in self.manual_sensors:
            have[m.type_id] = have.get(m.type_id, 0) + 1
        out = []
        for s in sensor_types(self.p):
            n = have.get(s.type_id, 0)
            if n:
                out.append(replace(s, n=n))
        return out

    def _place_manual_iter(self):
        """Режим «Задать позиции» ВО ВРЕМЯ МОДЕЛИРОВАНИЯ: статические стоят, динамические
        перемещаются (требование заказчика 05.09.2026).

        ⚠️ ЧЕМ ЭТО ОТЛИЧАЕТСЯ ОТ ОБЫЧНОЙ РАССТАНОВКИ. Только источником числа датчиков:
        здесь оно берётся из таблицы (`_manual_specs`), а не из полей N. Всё остальное —
        та же жадная расстановка по накопленным пролётам, с теми же якорями: `manual_locked`
        при идущих итерациях возвращает ровно статические.

        Почему не оставить «стоят и всё»: до итераций так и есть — человек описал
        обстановку, и её не двигают. А моделирование затем и запускается, чтобы увидеть,
        куда алгоритм переставит то, что переставлять разрешено."""
        specs = self._manual_specs()
        small = [s for s in specs if s.kind == "small"]
        n_big = sum(s.n for s in specs if s.kind == "big")
        self.candidates = self.candidate_positions()
        sample = self._sample_for_sensors()
        self.sensors = (self._place_multi_type(self.candidates, sample, small)
                        if small else np.empty((0, 2), float))
        self.sensors_big = self._place_big(sample, n_big=n_big)
        self._tag_sensors()
        self._mark_manual_placed()
        cells_xy, cells_w = self.grid.flat_cells(positive_only=False)
        keep = cells_w != 0.0
        self._metrics = self._evaluate(cells_xy[keep], cells_w[keep])
        return self.sensors

    def _place_manual(self):
        """Датчики РОВНО ТАМ, где их задал человек (режим «Задать позиции»).

        Ни жадного отбора, ни разноса, ни проверки воды: всё это — правила подбора, а
        подбора здесь нет. Оптимальность расстановки в этом режиме не гарантируется
        вовсе, и это принято сознательно (требование заказчика 04.09.2026): режим нужен
        для проверки и для случаев, когда место установки определяется не расчётом."""
        small, small_t, small_s, big, big_s = [], [], [], [], []
        for m in self.manual_sensors:
            if m.type_id == 4:
                big.append([m.x_km, m.y_km])
                big_s.append(bool(m.static))
            else:
                small.append([m.x_km, m.y_km])
                small_t.append(m.type_id)
                small_s.append(bool(m.static))
        self.sensors = (np.asarray(small, float) if small
                        else np.empty((0, 2), float))
        self.sensors_type = np.asarray(small_t, int) if small_t else np.empty(0, int)
        self.sensors_big = np.asarray(big, float) if big else np.empty((0, 2), float)
        # ⚠️ РЕЖИМ БЕРЁТСЯ ИЗ САМОЙ ЗАПИСИ, а не ставится «всем статический». Стоят здесь
        # и правда все заданные — подбора в этом режиме нет вовсе, — но это про МЕСТО, а
        # не про режим датчика. Прежняя редакция помечала статическими всех, и в таблице
        # режим, выбранный при добавлении, пропадал (замечание заказчика 05.09.2026).
        self.sensors_static = (np.asarray(small_s, bool) if small_s
                               else np.zeros(len(self.sensors), bool))
        self.sensors_big_static = (np.asarray(big_s, bool) if big_s
                                   else np.zeros(len(self.sensors_big), bool))
        # здесь ВСЕ позиции названы человеком — подбора в этом режиме нет вовсе
        self.sensors_manual = np.ones(len(self.sensors), bool)
        self.sensors_big_manual = np.ones(len(self.sensors_big), bool)
        self._mark_manual_placed()
        cells_xy, cells_w = self.grid.flat_cells(positive_only=False)
        keep = cells_w != 0.0
        self._metrics = self._evaluate(cells_xy[keep], cells_w[keep])
        return self.sensors

    def _place_by_weight(self, cand):
        """Расстановка по СТАТИЧЕСКОЙ весовой карте (единица покрытия — клетка веса)."""
        from .optimization import CoverageCache
        from config import MODES
        cells_xy, cells_w = self.grid.flat_cells(positive_only=False)
        keep = cells_w != 0.0
        cells_xy, cells_w = cells_xy[keep], cells_w[keep]
        cache = CoverageCache(cand, self.p.threat_R, 1, self.p.threat_k)
        cache.set_weighted_cells(cells_xy, cells_w)
        weights = MODES.get(getattr(self.p, "mode", "balanced"), MODES["balanced"])
        return cache.greedy_weighted(
            self.p.threat_N, weights, min_sep=self.p.threat_min_sep_frac * self.p.threat_R)

    def _place_by_routes(self, cand, routes):
        """Расстановка по ВЫБОРКЕ маршрутов (частота пролёта). Правила:
        * кандидаты — только в ЗОНЕ ПРОЛЁТА (`route_area`): туда, куда БПЛА способен
          долететь вход→место→цель в пределах запаса хода. Позиции ЗА ЦЕЛЬЮ разрешены,
          если маршруты туда заходят (заход сзади в модели есть);
        * якорей НЕТ — все позиции выбирает жадный субмодулярный алгоритм;
        * датчики не ближе ~1.6·R друг к другу (перекрытие зон ≤ ~10 %, не кучкуются).

        Работает одинаково ДО и ПОСЛЕ итераций: до них выборка — «все возможные пути»
        (`plan_routes`), после — накопленные пролёты (`_sample_for_sensors`)."""
        from .optimization import CoverageCache
        from config import MODES
        if len(cand) == 0 or not routes:
            return np.empty((0, 2), float)
        R = self.p.threat_R
        # ФИЛЬТР КАНДИДАТОВ — ПО ЗОНЕ ПРОЛЁТА, а не по прямой «вход→цель».
        #
        # Прежнее правило (закомментировано ниже) оставляло кандидатов с проекцией на ось
        # A→B в пределах 0…1, то есть в полосе между входом и целью. Оно писалось под ОДИН
        # вход и перестало отвечать смыслу, когда точек входа стало пять: ось строится
        # через их среднюю точку, которой на местности не существует. Замер: фильтр
        # отсекал 588 позиций, из них 260 лежали в зоне пролёта (то есть выбрасывались
        # зря), и при этом оставлял ~1260 позиций, недостижимых вовсе; 5.5 % точек
        # маршрутов оказывались вне полосы — там пролёты есть, а датчик поставить нельзя.
        # Зона пролёта считается перед расстановкой (`_refresh_envelope` → `flight_envelope`)
        # и уже учитывает ВСЕ точки входа, запас хода, проходимость и обходы цели.
        area = self.route_area
        if area is not None and self.grid is not None:
            g = self.grid
            ix = np.clip(((cand[:, 0] - g.ox) / g.h).astype(int), 0, g.nx - 1)
            iy = np.clip(((cand[:, 1] - g.oy) / g.h).astype(int), 0, g.ny - 1)
            keep = np.asarray(area)[iy, ix]
            if keep.any():                             # пустая зона — не терять кандидатов
                cand = cand[keep]
        # ПРЕЖНЕЕ ПРАВИЛО (не удалять: вернуть, если зона пролёта окажется хуже):
        # entry, target = self.entry_target_km()
        # A = np.asarray(entry, float); B = np.asarray(target, float)
        # d = B - A; D2 = float(d @ d)
        # if D2 > 1e-9:                                # оставить кандидатов между A и B
        #     s = ((cand - A) @ d) / D2
        #     cand = cand[(s >= 0.0) & (s <= 1.0)]
        if len(cand) == 0:
            return np.empty((0, 2), float)
        # ЧЕТЫРЕ ТИПА ДАТЧИКОВ (задача 8.7): типы 1–3 — малые, каждый со своим числом,
        # радиусом и кратностью. Работающих типов может быть несколько, и тогда проход
        # идёт по каждому, в порядке номеров: так человек видит в таблице тот же порядок,
        # что и в окне.
        from .sensors import active_small
        types = active_small(self.p)
        # ⚠️ ДАТЧИКИ, ПОСТАВЛЕННЫЕ ЧЕЛОВЕКОМ, — ЭТО УСЛОВИЯ МОДЕЛИРОВАНИЯ, НЕ ЯКОРЯ
        # (уточнение заказчика 05.09.2026). Разница принципиальная:
        #
        #   ЯКОРЬ — механизм ВНУТРИ жадного алгоритма, позиция, которую он назначает себе
        #           сам. Такие якоря (у входа и цели) отключены 29.08.2026, и расстановка
        #           работает без них — правило 1.3 ОГРАНИЧЕНИЙ в силе;
        #   УСЛОВИЯ МОДЕЛИРОВАНИЯ — входные данные задачи: «смоделировать вот такую
        #           обстановку». Человек назвал часть решения, алгоритм ДОБИРАЕТ остаток
        #           до заказанного N.
        #
        # Технически они передаются тем же аргументом `anchors` — это способ передачи, а
        # не суть. Для обоснования гарантии разница важна: самоограничение алгоритма её
        # ломает, а заданная извне часть решения просто сокращает задачу (см. 1.14).
        #
        # ⚠️ ЧТО ЗАКРЕПЛЕНО, РЕШАЕТ ФАЗА (`manual_locked`): до итераций — любой заданный
        # человеком датчик, при итерациях — только помеченный «статический». Динамический
        # при моделировании перемещается вместе с расстановкой (заказчик 05.09.2026).
        if len(types) > 1 or any(m.type_id != 4 for m in self.manual_locked()):
            return self._place_multi_type(cand, routes, types)
        # ⚠️ ОДИН РАБОТАЮЩИЙ ТИП И НИ ОДНОГО ЗАКРЕПЛЁННОГО ДАТЧИКА — ПРЕЖНИЙ КОД БЕЗ
        # ЕДИНОГО ИЗМЕНЕНИЯ. Обычный случай обязан считаться ровно как раньше, иначе
        # контрольный прогон перестанет что-либо доказывать: расхождение с эталоном
        # нельзя будет отличить от настоящей поломки.
        cache = CoverageCache(cand, R, self.p.L_seg, self.p.threat_k)
        for r in routes:
            cache.add_trajectory(r)
        # ЯКОРЯ ВЫКЛЮЧЕНЫ (проба по решению заказчика 29.08.2026). Раньше принудительно
        # ставились два: у входа и у цели, каждый в 0.45·R внутрь коридора, и из кандидатов
        # в круге 0.7·R брался накрывающий больше всего маршрутов. При пяти точках входа
        # правило выродилось: «вход» — средняя точка пяти, до реальных точек от неё
        # 1.2…23.7 км, и якорь ловил лишь 24 маршрута из 150 (16 %), то есть стерёг один
        # вход из пяти. Плюс принуждение снимает гарантию жадного алгоритма (план 6).
        # covsum = cache.routes_covered_by_candidate()
        # u = d / np.sqrt(D2) if D2 > 1e-9 else np.array([1.0, 0.0])
        #
        # def anchor_near(pt):                         # кандидат у точки, max покрытия
        #     near = np.linalg.norm(cand - pt, axis=1) < 0.7 * R
        #     if near.any():
        #         idxs = np.nonzero(near)[0]; return int(idxs[np.argmax(covsum[idxs])])
        #     return int(np.argmin(np.linalg.norm(cand - pt, axis=1)))
        #
        # anchor_b = anchor_near(B - 0.45 * R * u)     # у цели B (внутрь, к A)
        # anchor_a = anchor_near(A + 0.45 * R * u)     # у входа A (внутрь, к B)
        # anchors = [anchor_b] + ([anchor_a] if anchor_a != anchor_b else [])
        anchors = []
        weights = MODES.get(getattr(self.p, "mode", "balanced"), MODES["balanced"])
        # Разнос: ЖЕЛАЕМЫЙ 1.6·R (перекрытие зон ≤ ~10 %), но если при нём полезных
        # позиций не хватило на заказанные N — постепенно ослабляем до нижней границы
        # threat_min_sep_frac·R. Раньше 1.6·R был жёстким: на реальных данных набиралось
        # 9 из 12, а остальные 3 уходили в пустоту (см. greedy — третичный ключ).
        N = int(self.p.threat_N)
        sep_min = max(0.1, float(self.p.threat_min_sep_frac)) * R
        sep = max(1.6 * R, sep_min)
        sens = cache.greedy(N, weights, anchors=anchors, anchor_sep=sep, min_sep=sep)
        while len(sens) < N and sep > sep_min:
            sep = max(sep_min, sep * 0.85)
            sens = cache.greedy(N, weights, anchors=anchors, anchor_sep=sep, min_sep=sep)
        return sens

    def _place_multi_type(self, cand, routes, types):
        """Расстановка, когда работает НЕ ОДИН малый тип либо есть закреплённые датчики.

        Проход по типам в порядке номеров, у каждого свой радиус и своя кратность.
        Результат складывается в `self.sensors`, а тип каждого датчика — в
        `self.sensors_type`.

        ⚠️ ПОЧЕМУ ПО ТИПАМ, А НЕ ОДНИМ ОБЩИМ ЖАДНЫМ ПРОХОДОМ. `CoverageCache` строится
        под ОДИН радиус: в нём заранее посчитано, какие точки маршрутов видит каждый
        кандидат, и при разных радиусах общим такой кэш быть не может. Но ОБА правила
        жадного отбора работают и между типами (требование заказчика 05.09.2026):

        * **кратность** — уже поставленные датчики других типов передаются в кэш
          (`CoverageCache(placed=…)`), и прирост считается ПОВЕРХ их покрытия: тип 2
          знает, что этот кусок маршрута уже закрыт типом 1 нужное число раз. Раньше не
          знал, и это числилось осознанным упрощением (§7) — теперь снято;
        * **разнос** — `THREAT_SPREAD_PAIR·(R₁+R₂)`, то есть то же «не ближе 1.6·R»,
          обобщённое на пару разных радиусов: при равных радиусах даёт ровно 1.6·R.
          Прежняя полусумма (0.5·(R₁+R₂)) была вдвое слабее внутригруппового правила, и
          типы слипались: при R = 1 и 3 км разнос выходил 2 км против 4.8 внутри типа.

        ⚠️ ГАЛОЧКА «распределять между типами» (`threat_spread_types`) отключает ТОЛЬКО
        разнос между разными типами; внутри типа он остаётся всегда. Кратность
        учитывается в любом случае: это не «распределение», а правильный счёт покрытия."""
        from .optimization import CoverageCache
        from config import MODES, THREAT_SPREAD_PAIR
        weights = MODES.get(getattr(self.p, "mode", "balanced"), MODES["balanced"])
        spread = bool(getattr(self.p, "threat_spread_types", True))
        placed = np.empty((0, 2), float)     # уже поставленные, всех типов
        placed_r = np.empty(0, float)        # их радиусы — для разноса между типами
        out_xy, out_tid = [], []
        for spec in types:
            R = float(spec.r_km)
            c = np.asarray(cand, float)
            # не даём типам садиться друг на друга: то же 1.6·R, обобщённое на пару
            if len(placed) and spread:
                d = np.linalg.norm(c[:, None, :] - placed[None, :, :], axis=2)
                keep = (d >= THREAT_SPREAD_PAIR * (R + placed_r)[None, :]).all(axis=1)
                if keep.any():               # все позиции заняты — не терять тип совсем
                    c = c[keep]
            # ЗАКРЕПЛЁННЫЕ ПОЗИЦИИ ЭТОГО ТИПА — прямо в список кандидатов.
            # ⚠️ Именно ДОБАВЛЯЕМ, а не «ищем ближайший узел сетки»: датчик обязан
            # остаться ровно там, куда его поставил человек, а привязка к сетке сдвинула
            # бы его на полшага (до 0.5 км при шаге 1 км — четверть радиуса типа 1).
            # заданные человеком позиции этого типа — условия моделирования.
            # ⚠️ ЧТО ИМЕННО ЗАКРЕПЛЕНО, РЕШАЕТ ФАЗА (`manual_locked`): до итераций — все
            # заданные, при итерациях — только статические, а динамический едет вместе с
            # расстановкой (правило заказчика 05.09.2026).
            st = self.manual_locked(spec.type_id)
            anchors = []
            if st:
                extra = np.array([[m.x_km, m.y_km] for m in st], float)
                anchors = list(range(len(c), len(c) + len(extra)))
                c = np.vstack([c, extra])
            if len(c) == 0:
                continue
            # УЖЕ ПОСТАВЛЕННЫЕ ДРУГИЕ ТИПЫ — в кэш: их покрытие входит в стартовое
            # состояние жадного отбора, и кратность считается общая, а не по типу.
            cache = CoverageCache(c, R, self.p.L_seg, int(spec.k),
                                  placed=[(pt[0], pt[1], rr)
                                          for pt, rr in zip(placed, placed_r)])
            for r in routes:
                cache.add_trajectory(r)
            N = int(spec.n)
            sep_min = max(0.1, float(self.p.threat_min_sep_frac)) * R
            sep = max(1.6 * R, sep_min)
            sens = cache.greedy(N, weights, anchors=anchors,
                                anchor_sep=sep, min_sep=sep)
            while len(sens) < N and sep > sep_min:
                sep = max(sep_min, sep * 0.85)
                sens = cache.greedy(N, weights, anchors=anchors,
                                    anchor_sep=sep, min_sep=sep)
            if not len(sens):
                continue
            out_xy.append(sens)
            out_tid.append(np.full(len(sens), spec.type_id, int))
            placed = np.vstack([placed, sens])
            placed_r = np.concatenate([placed_r, np.full(len(sens), R)])
        if not out_xy:
            self.sensors_type = np.empty(0, int)
            return np.empty((0, 2), float)
        self.sensors_type = np.concatenate(out_tid)
        return np.vstack(out_xy)

    def _place_big(self, routes, n_big=None):
        """БОЛЬШИЕ датчики — «круговой щит» у цели: `threat_N_big` штук радиусом
        `threat_R_big`, разнесённые ПО УГЛУ вокруг цели.

        Почему кольцо, а не общий жадный отбор. Требование заказчика — «прикрывают цель
        со всех сторон, как треугольник (возможно неправильный)» — это про НАПРАВЛЕНИЯ,
        а не про площадь покрытия. Жадный алгоритм по маршрутам сам такого не даст: он
        соберёт все три датчика на самом плотном пучке маршрутов, и с одной стороны цель
        останется открытой. Поэтому позиция ищется в кольце вокруг цели, а угол между
        соседними датчиками ограничен снизу.

        ⚠️ «Не пересекаться» и «накрывать цель» одновременно НЕДОСТИЖИМЫ. Чтобы зоны не
        пересекались, центры должны стоять дальше `2·R_big` друг от друга; тогда при трёх
        датчиках цель оказывается дальше `R_big` от каждого — то есть вне зон. Поэтому
        принято «МИНИМАЛЬНОЕ перекрытие»: датчики стоят у внешней границы кольца
        (`threat_big_ring_hi` ≈ R_big), и при 120° между ними расстояние выходит
        1.73·R_big — перекрытие небольшое, а цель под наблюдением у каждого.

        Внутри допустимого кольца и угла позиция выбирается ПО МАРШРУТАМ: берётся
        кандидат, накрывающий больше всего пролётов."""
        from .optimization import CoverageCache
        # `n_big` задаётся аргументом только в режиме «Задать позиции» при моделировании:
        # там число берётся из ТАБЛИЦЫ, а не из поля N (оно там только для чтения).
        n_big = (int(getattr(self.p, "threat_N_big", 0) or 0) if n_big is None
                 else int(n_big))
        R_big = float(getattr(self.p, "threat_R_big", 0.0) or 0.0)
        # ЗАКРЕПЛЁННЫЕ БОЛЬШИЕ (задача 8.7) ставятся ПЕРВЫМИ и не проходят через кольцо:
        # их позиции названы человеком. Кольцом добираются только оставшиеся места —
        # закреплённые входят в общее число N, иначе заказ «три больших» превращался бы
        # в четыре при одном закреплённом.
        # ⚠️ ЧТО ЗАКРЕПЛЕНО, РЕШАЕТ ФАЗА (`manual_locked`): до итераций — все заданные
        # большие, при итерациях — только статические; динамический большой возвращается
        # в кольцо и встаёт туда, куда его поставит правило щита.
        fixed = np.array([[m.x_km, m.y_km]
                          for m in self.manual_locked(4)], float)
        if len(fixed):
            n_big -= len(fixed)
            if n_big <= 0 or R_big <= 0.0 or not routes:
                return fixed                    # все места заняты закреплёнными
        if n_big <= 0 or R_big <= 0.0 or not routes:
            return np.empty((0, 2), float)
        cand = self.candidates if len(self.candidates) else self.candidate_positions()
        if not len(cand):
            return fixed if len(fixed) else np.empty((0, 2), float)
        B = np.asarray(self.target_only_km(), float)
        d = np.linalg.norm(cand - B, axis=1)
        lo = float(THREAT_BIG_RING_LO) * R_big
        hi = float(THREAT_BIG_RING_HI) * R_big
        ring = (d >= lo) & (d <= hi)
        if not ring.any():                      # кольцо вне карты — берём что ближе к нему
            ring = d <= max(hi, float(d.min()) * 1.05)
        sub = cand[ring]
        if not len(sub):
            return fixed if len(fixed) else np.empty((0, 2), float)
        # ЧЕМ МЕРИТЬ ПОЗИЦИЮ. «Сколько маршрутов накрывает» здесь бесполезно: радиус
        # 15 км так велик, что почти любой кандидат кольца накрывает ВСЮ выборку —
        # замер: 250 из 250 маршрутов у всех 459 кандидатов, критерий не различает ничего,
        # и `argmax` брал первого по порядку сетки. Поэтому берётся ПЛОТНОСТЬ пролётов:
        # сколько ячеек-проходов маршрутов попадает в зону обзора.
        dens = self.route_density_field()
        g = self.grid
        if dens is None:
            score_pos = np.zeros(len(sub))
        else:
            gx, gy = g.cell_centers_km()
            hot = dens > 0
            hx, hy, hw = gx[hot], gy[hot], np.asarray(dens)[hot]
            score_pos = np.array([
                float(hw[(hx - s[0]) ** 2 + (hy - s[1]) ** 2 <= R_big * R_big].sum())
                for s in sub])
        # РАЗБИЕНИЕ КРУГА НА N СЕКТОРОВ, по датчику в каждом. Так щит равномерен ПО
        # ПОСТРОЕНИЮ. Отбор «жадно, но не ближе допуска по углу» этого не давал: первые
        # два датчика садились на самый плотный пучок маршрутов, упирались в допуск и
        # оставляли перекос — замер на участке дал углы 155° / 68° / 137° при идеале 120°.
        #
        # Сектора нужно ещё и ПОВЕРНУТЬ: их границы, поставленные наугад, могут разрезать
        # плотный пучок маршрутов пополам. Поэтому фаза перебирается, и берётся та, при
        # которой суммарное покрытие маршрутов максимально.
        ang = np.arctan2(sub[:, 1] - B[1], sub[:, 0] - B[0])
        step = 2.0 * np.pi / max(1, n_big)
        best, best_score = None, -1.0
        for ph in np.linspace(0.0, step, 12, endpoint=False):
            k = np.floor(((ang - ph) % (2.0 * np.pi)) / step).astype(int)
            take, score = [], 0.0
            for s_i in range(n_big):
                in_sec = np.nonzero(k == s_i)[0]
                if not len(in_sec):
                    continue
                # В СЕКТОРЕ датчик ставится НА ЕГО ОСЬ — так щит равномерен по
                # построению (углы ≈ 360°/N). Маршруты влияют не здесь, а на ПОВОРОТ
                # всей тройки: по ним выбирается фаза `ph`. Обратный порядок (сначала
                # плотность, потом ось) уже пробовался и давал перекос: датчики садились
                # у границ секторов и оказывались в 25–68° друг от друга — формально в
                # разных секторах, а на карте рядом.
                axis = ph + step * (s_i + 0.5)           # ось этого сектора
                off = np.abs((ang[in_sec] - axis + np.pi) % (2 * np.pi) - np.pi)
                near = in_sec[off <= off.min() + np.radians(4.0)]   # почти на оси
                j = int(near[int(np.argmax(score_pos[near]))])      # из них — где гуще пролёты
                take.append(j); score += float(score_pos[j])
            # предпочитаем расстановку, где ЗАПОЛНЕНЫ ВСЕ сектора: щит с дырой хуже,
            # чем щит из тех же датчиков, но по кругу
            score += 1e6 * len(take)
            if score > best_score:
                best, best_score = take, score
        if not best:
            return fixed if len(fixed) else np.empty((0, 2), float)
        out = np.asarray([sub[j] for j in best], float)
        return np.vstack([fixed, out]) if len(fixed) else out

    def _sample_for_sensors(self):
        """Выборка маршрутов, по которой считаются датчики и 2-я тепловая карта (§4.6):
        идут ИТЕРАЦИИ → их выборка; иначе, если включён чекбокс и файл загружен → ЗАГРУ-
        ЖЕННАЯ выборка (задача 8.6); иначе → ВСЕ возможные пути (наиболее вероятные).

        ⚠️ ИТЕРАЦИИ ВСЕГДА В ПРИОРИТЕТЕ. Как только накопится ≥5 своих маршрутов, чекбокс
        «показать загруженную выборку» перестаёт влиять на расстановку — это и есть
        «с пуском итераций он уже не работает» (заказчик, план 8 §8.6.1)."""
        if len(self.iter_routes) >= 5:
            return self.iter_routes
        if self.show_loaded_routes and len(self.loaded_routes) >= 5:
            return self.loaded_routes
        return self.routes

    def generalized_sample(self, frac=0.10):
        """ОБОБЩЁННАЯ выборка итерационных маршрутов для показа: ~frac (10 %) от УЖЕ
        пройденных итераций — не весь веер (напр. 500), а только ~50; если прошло 150 —
        покажет 15. Отбираются НАИБОЛЕЕ ЧАСТЫЕ маршруты (по тепловой карте частоты
        пролёта), но РАСПРЕДЕЛЁННО по всей карте, а не кучкой в одном месте.

        Алгоритм — жадный СУБМОДУЛЯРНЫЙ отбор (как у датчиков) по полю частоты пролёта:
        каждый шаг берём маршрут с максимальным покрытием ещё «непокрытой» частоты, затем
        ГАСИМ покрытые им клетки (×0.15) — поэтому следующий маршрут тяготеет к ДРУГОМУ
        загруженному коридору. Итог: представительно (частые пути) и равномерно (разные
        коридоры), без скучивания."""
        src = self.iter_routes
        if not src or self.grid is None:
            return []
        n = len(src)
        k = max(1, int(round(frac * n)))
        if k >= n:
            return list(src)
        g = self.grid
        cells, dens = [], np.zeros(g.ny * g.nx, np.float64)
        for r in src:                                       # клетки маршрута + поле частоты
            ix = np.clip(((r[:, 0] - g.ox) / g.h).astype(int), 0, g.nx - 1)
            iy = np.clip(((r[:, 1] - g.oy) / g.h).astype(int), 0, g.ny - 1)
            idx = np.unique(iy * g.nx + ix)
            cells.append(idx); dens[idx] += 1.0
        remaining = dens.copy()
        chosen, used = [], np.zeros(n, bool)
        for _ in range(k):
            best, best_gain = -1, -1.0
            for i in range(n):
                if used[i]:
                    continue
                gain = float(remaining[cells[i]].sum())     # покрытие ещё «непокрытой» частоты
                if gain > best_gain:
                    best_gain, best = gain, i
            if best < 0:
                break
            used[best] = True
            chosen.append(src[best])
            remaining[cells[best]] *= 0.15                  # гасим коридор -> следующий в др. месте
        return chosen

    def route_density_field(self):
        """2-я ТЕПЛОВАЯ КАРТА — частота пролёта БПЛА: сколько маршрутов проходит через
        каждую клетку (норм. 0..1). ДО итераций — по всем возможным путям, ПРИ итерациях —
        по выборке пролётов (§4.6). None, если маршрутов нет."""
        src = self._sample_for_sensors()
        if not src or self.grid is None:
            return None
        g = self.grid
        dens = np.zeros(g.ny * g.nx, np.float64)
        for r in src:
            ix = np.clip(((r[:, 0] - g.ox) / g.h).astype(int), 0, g.nx - 1)
            iy = np.clip(((r[:, 1] - g.oy) / g.h).astype(int), 0, g.ny - 1)
            dens[np.unique(iy * g.nx + ix)] += 1.0        # клетка учитывается раз на маршрут
        mx = dens.max()
        return (dens / mx if mx > 0 else dens).reshape(g.ny, g.nx)

    def _sensor_radii(self):
        """Радиус КАЖДОГО малого датчика — по его типу.

        ⚠️ РАНЬШЕ ВЕЗДЕ БРАЛСЯ `threat_R`, радиус типа 1. Пока тип был один, это было
        одно и то же; с четырьмя типами (задача 8.7) показатели врали: датчик типа 3 с
        радиусом 5 км засчитывался как двухкилометровый, и засечка с покрытием выходили
        заниженными тем сильнее, чем крупнее типы. Заметно на отчёте заказчика
        05.09.2026: N=10 у каждого из трёх типов, а кратность считалась по 2 км."""
        from .sensors import sensor_types
        n = len(self.sensors)
        r1 = float(self.p.threat_R)
        if not n:
            return np.empty(0, float)
        by_type = {s.type_id: float(s.r_km) for s in sensor_types(self.p)}
        if len(self.sensors_type) != n:
            return np.full(n, r1)
        return np.array([by_type.get(int(t), r1) for t in self.sensors_type], float)

    def _evaluate(self, cells_xy, cells_w):
        """Показатели: суммарный вес карты, покрытый вес, доля, разбивка по слоям и
        ЗАСЕЧКА МАРШРУТОВ. Последняя — прямая мера качества расстановки: датчики
        ставятся по выборке пролётов, а «покрытый вес» считается по СЫРОЙ весовой карте
        и потому не отражает цель оптимизации (может быть высоким при плохой засечке).

        ⚠️ У КАЖДОГО ДАТЧИКА СВОЙ РАДИУС (см. `_sensor_radii`), поэтому сравнение идёт
        не с одним числом, а с вектором. Разбивка по типам (`by_type`) отвечает на
        вопрос, который при одном типе не стоял вовсе: какой ТИП сколько ловит. Без неё
        по общей кратности 16.7 нельзя понять, работают ли все три типа или два из них
        стоят впустую."""
        g = self.grid
        total = float(np.sum(np.clip(g.weight, 0, None)))
        covered = 0.0
        rad = self._sensor_radii()
        # ⚠️ `xy`/`w` ВЫНЕСЕНЫ ИЗ `if` (были внутри него до довеска 06.09.2026): нужны
        # ЕЩЁ РАЗ ниже, для доли покрытия КАЖДОГО ТИПА в отдельности (заказчик: «и для
        # типов тоже определяй долю покрытия») — второй раз их не пересчитывать.
        pos = cells_w > 0 if len(cells_w) else np.zeros(0, bool)
        xy = cells_xy[pos] if len(cells_xy) else np.empty((0, 2), float)
        w = cells_w[pos] if len(cells_w) else np.empty(0, float)
        if len(self.sensors) and len(xy):
            d = np.linalg.norm(xy[:, None, :] - self.sensors[None, :, :], axis=2)
            seen = (d <= rad[None, :]).any(axis=1)
            covered = float(np.sum(w[seen]))
        det1 = detk = mean_hits = 0.0
        by_type = {}
        sample = self._sample_for_sensors()
        if len(self.sensors) and sample:
            # для каждого маршрута — какие датчики его видят (маска по датчикам)
            seen_by = np.array([
                (np.linalg.norm(r[:, None, :] - self.sensors[None, :, :],
                                axis=2).min(axis=0) <= rad)
                for r in sample])                      # (маршрутов, датчиков)
            hits = seen_by.sum(axis=1)
            det1 = float((hits >= 1).mean())
            detk = float((hits >= self.p.threat_k).mean())
            mean_hits = float(hits.mean())
            from .sensors import sensor_types
            k_of = {s.type_id: int(s.k) for s in sensor_types(self.p)}
            tids = (self.sensors_type if len(self.sensors_type) == len(self.sensors)
                    else np.ones(len(self.sensors), int))
            for t in sorted(set(int(v) for v in tids)):
                col = (tids == t)
                h_t = seen_by[:, col].sum(axis=1)
                # ⚠️ КРАТНОСТЬ У КАЖДОГО ТИПА СВОЯ (режим «у каждого своя» в окне):
                # одна общая строка «засечено ≥ k» в этом случае отвечала бы на вопрос,
                # которого никто не задавал. Здесь — выполнение СОБСТВЕННОГО k типа.
                k_t = max(1, int(k_of.get(t, self.p.threat_k)))
                # ДОЛЯ ПОКРЫТИЯ ЭТОГО ТИПА (довесок 06.09.2026) — тот же приём, что у
                # общего `covered_frac` выше, но по сенсорам ОДНОГО типа: сколько веса
                # карты видит именно он, а не расстановка целиком.
                sens_t, rad_t = self.sensors[col], rad[col]
                if len(sens_t) and len(xy):
                    dT = np.linalg.norm(xy[:, None, :] - sens_t[None, :, :], axis=2)
                    covered_t = float(np.sum(w[(dT <= rad_t[None, :]).any(axis=1)]))
                else:
                    covered_t = 0.0
                by_type[t] = dict(n=int(col.sum()),
                                  r_km=float(rad[col][0]) if col.any() else 0.0,
                                  k=k_t,
                                  detect_frac=float((h_t >= 1).mean()),
                                  detect_k_frac=float((h_t >= k_t).mean()),
                                  mean_hits=float(h_t.mean()),
                                  covered_frac=(covered_t / total if total > 0 else 0.0),
                                  idle=int((seen_by[:, col].sum(axis=0) == 0).sum()))
        return dict(total_weight=total, covered_weight=covered,
                    covered_frac=(covered / total if total > 0 else 0.0),
                    detect_frac=det1, detect_k_frac=detk, mean_hits=mean_hits,
                    n_routes_eval=len(sample) if sample else 0,
                    n_sensors=int(len(self.sensors)),
                    n_big=int(len(self.sensors_big)),
                    n_static=int(np.asarray(self.sensors_static).sum())
                    + int(np.asarray(self.sensors_big_static).sum()),
                    # ⚠️ ДВА РАЗНЫХ ЧИСЛА: `n_static` — сколько датчиков в режиме
                    # «статический», `n_manual` — сколько позиций вообще назвал человек.
                    # До итераций второе больше первого (стоят и динамические), при
                    # итерациях они сходятся: динамические к тому времени уехали.
                    n_manual=int(np.asarray(self.sensors_manual).sum())
                    + int(np.asarray(self.sensors_big_manual).sum()),
                    by_type=by_type,
                    n_candidates=int(len(self.candidates)),
                    by_layer=g.totals_by_layer())

    def _evaluate_current(self):
        """Пересчитать показатели по ТЕКУЩЕЙ расстановке, не трогая сами позиции.

        Нужно после правки датчика на карте: позиции изменились, а расстановку заново
        подбирать нельзя — человек как раз и поставил их своей рукой."""
        if self.grid is None:
            return self._metrics
        cells_xy, cells_w = self.grid.flat_cells(positive_only=False)
        keep = cells_w != 0.0
        return self._evaluate(cells_xy[keep], cells_w[keep])

    def metrics(self):
        return self._metrics

    @staticmethod
    def _approach_bearing(route, target_xy):
        """Азимут ОТ ЦЕЛИ на КОНЕЧНУЮ часть маршрута — направление ЗАХОДА, а не старта
        (см. `sector_stats`). Идёт по точкам маршрута С КОНЦА и берёт первую, что дальше
        `THREAT_COMPASS_APPROACH_KM` от цели; `None` — весь маршрут ближе этого порога
        (вырожденный случай, короче одной клетки сетки)."""
        B = np.asarray(target_xy, float)
        for p in route[::-1]:
            d = np.asarray(p, float) - B
            if np.hypot(d[0], d[1]) >= THREAT_COMPASS_APPROACH_KM:
                return float(np.degrees(np.arctan2(d[0], d[1])) % 360.0)
        return None

    def sector_stats(self):
        """Статистика прихода БПЛА по 12 направлениям КОМПАСА, шаг
        `THREAT_COMPASS_STEP_DEG` = 30° (план 8, задача 8.9, довесок 06.09.2026,
        кнопка «Анализ моделирования»).

        Направление маршрута — азимут ОТ ЦЕЛИ на его КОНЕЧНУЮ часть, направление ЗАХОДА
        (не старта!): 0° = север (+y), по часовой стрелке (90° = восток, +x) — обычный
        компас. Сектор k покрывает [k·шаг − шаг/2, k·шаг + шаг/2) и подписан своим
        центральным углом (0, 30, 60, … 330).

        ⚠️ ИМЕННО ЗАХОД, А НЕ СТАРТ (исправлено 06.09.2026, заказчик: «смотреть...
        конечную точку захода на цель, это данные каждого маршрута»). Точка ВХОДА общая
        у всех маршрутов сразу (их THREAT_SECTOR_ENTRIES = 5 на весь сектор появления),
        и азимут от старта почти всегда стягивался в 3–5 секторов из 12: маршруты
        огибают рельеф и запретные зоны, и БПЛА визуально заходит на цель со всех
        сторон, а разбивка по старту этого не показывала. `_approach_bearing` ищет
        направление С КОНЦА маршрута, пока не отойдёт от цели хотя бы на
        `THREAT_COMPASS_APPROACH_KM` — сама последняя точка лежит В цели.

        Кратность — ОБЩАЯ, по ВСЕМ типам датчиков разом (то же `hits`, что у верхней
        строки отчёта «Засечено ≥ k» в `_evaluate`, а не разбивка `by_type`, — заказчик
        явно просил «по всем типам датчиков», не по каждому отдельно).

        Возвращает список из 12 словарей `dict(deg, n_routes, n_zero, n_ge1, n_ge_k, k,
        ok)`. `ok=False` — сектор НЕ ПУСТОЙ, и хотя бы один его маршрут не набрал
        заданную кратность `k` (`Params.threat_k`): направление помечается красным на
        карте и в тексте отчёта."""
        step = float(THREAT_COMPASS_STEP_DEG)
        n_sec = max(1, int(round(360.0 / step)))
        out = [dict(deg=int(round(i * step)), n_routes=0, n_zero=0, n_ge1=0, n_ge_k=0,
                    k=int(self.p.threat_k), ok=True) for i in range(n_sec)]
        sample = self._sample_for_sensors()
        if not sample or not len(self.sensors):
            return out
        B = np.asarray(self.target_only_km(), float)
        rad = self._sensor_radii()
        k = max(1, int(self.p.threat_k))
        for r in sample:
            bearing = self._approach_bearing(r, B)
            if bearing is None:
                continue                  # маршрут целиком лежит в цели — без направления
            idx = int(round(bearing / step)) % n_sec
            seen = (np.linalg.norm(r[:, None, :] - self.sensors[None, :, :],
                                   axis=2).min(axis=0) <= rad)
            hits = int(seen.sum())
            e = out[idx]
            e["n_routes"] += 1
            e["n_zero"] += int(hits == 0)
            e["n_ge1"] += int(hits >= 1)
            e["n_ge_k"] += int(hits >= k)
        for e in out:
            e["ok"] = (e["n_routes"] == 0) or (e["n_ge_k"] == e["n_routes"])
        return out

    def reachable_stats(self):
        """Корректная мера «всех возможных мест пролёта» — размер огибающей (граф-обход):
        число достижимых ячеек и их площадь (км²). Путей через них — экспоненциально много
        (не считаем), а вот ДОСТИЖИМАЯ ОБЛАСТЬ считается точно."""
        if self.route_area is None or self.grid is None:
            return 0, 0.0
        n = int(np.asarray(self.route_area).sum())
        return n, n * (self.grid.h ** 2)

    # ---- точка входа (фиксирована у реки) и цель (можно задать кликом) ----
    def set_target(self, x_km, y_km):
        """Задать целевую точку кликом по карте (режим «указать цель»). Запас хода
        (если не задан руками) пересчитывается авто под новое |AB|; маршруты сбросятся."""
        self.target_km = (float(x_km), float(y_km))
        self.routes = []
        self.iter_routes = []
        self.iter_iteration = 0
        self._iter_ctx = None                          # цель сменилась — контекст устарел
        self._ctx_built_key = None
        # СЕКТОР ОТСЧИТЫВАЕТСЯ ОТ ЦЕЛИ: сменилась цель — сместился и он, точки входа
        # пересчитываются по той же точке клика
        if self.sector_point_km is not None:
            self.entry_points = self.compute_entry_points()
        self.sync_auto_L_max()
        if self.grid is not None:
            self.build()      # свободная зона привязана к цели -> карту пересобрать

    def entry_km(self):
        """ПРЕДСТАВИТЕЛЬ точки входа — для показателей и авто-запаса хода. При заданном
        секторе это середина отобранных точек, иначе демо-точка участка."""
        if self.entry_points:
            p = np.asarray(self.entry_points, float).mean(axis=0)
            return (float(p[0]), float(p[1]))
        _, elon, elat = THREAT_ENTRY
        ex, ey = lonlat_to_km(elon, elat, self.lon0, self.lat0)
        return (float(ex), float(ey))

    def entry_points_km(self):
        """ВСЕ точки входа списком: пять точек сектора либо одна демо-точка."""
        return list(self.entry_points) if self.entry_points else [self.entry_km()]

    def target_only_km(self):
        """Цель в км: заданная кликом либо демо-метка участка."""
        if self.target_km is not None:
            return self.target_km
        _, tlon, tlat = THREAT_TARGET
        tx, ty = lonlat_to_km(tlon, tlat, self.lon0, self.lat0)
        return (float(tx), float(ty))

    def entry_target_km(self):
        """(вход, цель) в км — ОДНОЙ точкой. Вход: представитель (середина точек сектора)
        либо демо-метка участка. Этим пользуются показатели, |AB| и правила датчиков.
        Для маршрутов нужен ПОЛНЫЙ список — `entries_target_km`."""
        return self.entry_km(), self.target_only_km()

    def entries_target_km(self):
        """(входы, цель): вход — СПИСОК точек, если задан сектор, иначе одна точка.
        Список принимают и `flight_envelope`, и `build_iter_context`; розыгрыш
        конкретного старта — в `sample_one_route`."""
        pts = self.entry_points_km()
        return (pts if len(pts) > 1 else pts[0]), self.target_only_km()

    # ---- СЕКТОР ПОЯВЛЕНИЯ БПЛА ----
    def set_sector(self, x_km, y_km):
        """Задать сектор кликом по краю карты: ось «цель → клик», по
        `THREAT_SECTOR_HALF_DEG` (30°) в каждую сторону — полный раствор 60°.
        Пересчитывает точки входа и сбрасывает всё, что от входа зависит."""
        self.sector_point_km = (float(x_km), float(y_km))
        self.entry_points = self.compute_entry_points()
        self._reset_after_entry_change()
        if self.grid is not None and self.entry_points:
            self.build()      # свободные зоны привязаны к точкам входа -> пересобрать
        return self.entry_points

    def clear_sector(self):
        """Убрать сектор: вход снова один — демо-точка участка."""
        if self.sector_point_km is None and not self.entry_points:
            return False
        self.sector_point_km = None
        self.entry_points = []
        self._reset_after_entry_change()
        return True

    def _reset_after_entry_change(self):
        """Сброс после смены входа: контекст, маршруты, область залёта, запас хода."""
        self._iter_ctx = None
        self._ctx_built_key = None
        self.routes = []
        self.iter_routes = []
        self.iter_iteration = 0
        self.route_area = None
        self.sync_auto_L_max()

    def sector_edges_km(self):
        """Две крайние точки сектора на рамке участка — для отрисовки. None, если
        сектор не задан. Возвращает (вершина=цель, точка на левой границе, на правой)."""
        if self.sector_point_km is None:
            return None
        B = np.asarray(self.target_only_km(), float)
        P = np.asarray(self.sector_point_km, float)
        d = P - B
        n = float(np.hypot(*d))
        if n < 1e-9:
            return None
        half = np.radians(float(THREAT_SECTOR_HALF_DEG))
        out = [B]
        for sign in (+1.0, -1.0):
            c, s = np.cos(sign * half), np.sin(sign * half)
            u = np.array([c * d[0] - s * d[1], s * d[0] + c * d[1]]) / n
            out.append(B + u * self._ray_to_bbox(B, u))
        return tuple(tuple(map(float, p)) for p in out)

    def _ray_to_bbox(self, origin, u):
        """Длина луча из `origin` по орту `u` до рамки участка (км)."""
        x0, x1, y0, y1 = self.bbox_km
        t = float("inf")
        for lo, hi, o, d in ((x0, x1, origin[0], u[0]), (y0, y1, origin[1], u[1])):
            if abs(d) < 1e-12:
                continue
            for edge in (lo, hi):
                tt = (edge - o) / d
                if tt > 1e-9:
                    t = min(t, tt)
        return t if np.isfinite(t) else 0.0

    def compute_entry_points(self):
        """Отобрать точки входа на краю карты внутри сектора — с учётом весовой карты.

        Порядок: (1) ячейки приграничной полосы; (2) оставить те, чьё направление
        «цель → ячейка» попало в сектор — проверка через скалярное произведение, без
        арктангенсов и без разрыва на 180°; (3) выбросить непроходимые и те, ИЗ КОТОРЫХ
        ЦЕЛЬ НЕДОСТИЖИМА; (4) жадно взять N лучших по весу, разнося их между собой,
        иначе все пять слипаются в одном «горячем» углу.

        ⚠️ ДОСТИЖИМОСТЬ ПРОВЕРЯЕТСЯ ЗДЕСЬ, а не потом. Из точки, откуда до цели нет
        пути по коридорам, БПЛА не полетит — значит такая точка не «вариант появления»,
        а пустое место в выборке: каждая попытка из неё гарантированно провалилась бы.
        Считается ОДНИМ полем Дейкстры от цели (то же, что потом у маршрутов), поэтому
        стоит дёшево.

        ВЕС УЧАСТВУЕТ ТОЛЬКО ЗДЕСЬ. Сам старт маршрута разыгрывается между отобранными
        точками равномерно (решение заказчика) — см. `sample_one_route`."""
        if self.sector_point_km is None or self.grid is None:
            return []
        from .threat_routes import passable_mask, _cell_of, _dijkstra_dist, _open_endpoints
        g = self.grid
        B = np.asarray(self.target_only_km(), float)
        d = np.asarray(self.sector_point_km, float) - B
        n = float(np.hypot(*d))
        if n < 1e-9:
            return []
        u = d / n
        cos_half = float(np.cos(np.radians(float(THREAT_SECTOR_HALF_DEG))))

        gx, gy = g.cell_centers_km()
        x0, x1, y0, y1 = self.bbox_km
        band = float(THREAT_SECTOR_EDGE_KM)
        edge = ((gx - x0 <= band) | (x1 - gx <= band) |
                (gy - y0 <= band) | (y1 - gy <= band))
        vx, vy = gx - B[0], gy - B[1]
        r = np.hypot(vx, vy)
        inside = edge & (r > 1e-6) & ((vx * u[0] + vy * u[1]) >= cos_half * r)
        if not inside.any():
            return []
        # проходимость — теми же правилами, что у маршрутов: разрыв и уход от ориентира
        pas = passable_mask(g, self._route_gap_km(), self._route_slack_km())
        goal = _cell_of(g, B)
        # цель открываем так же, как это сделает построение маршрутов: иначе цель в
        # непроходимой ячейке обнулила бы поле, и «недостижимо» вышло бы для всего края
        reach = np.isfinite(_dijkstra_dist(_open_endpoints(pas.copy(), (goal,), g),
                                           goal, g.h))
        # ТОЧКА ВХОДА СТОИТ НА ВЕСОВОЙ ЯЧЕЙКЕ. Ячейка с нулевым весом — это чистое поле
        # без единого ориентира: БПЛА неоткуда там взяться и не за чем туда идти, такие
        # места из рассмотрения исключаются. Замер: на краю в секторе нулевой вес у
        # ПОЛОВИНЫ кандидатов, и прежний отбор мог поставить точку ровно на такую.
        has_w = np.asarray(g.weight, float) > 0.0
        ok = inside & pas & reach & has_w
        if not ok.any():
            ok = inside & pas & reach   # на краю нет ни одной весовой ячейки
        if not ok.any():
            ok = inside & pas      # весь край недостижим при текущем разрыве — оставляем
        if not ok.any():
            ok = inside            # весь край непроходим — не терять сектор совсем:
                                   # концы всё равно открываются в build_iter_context
        iy, ix = np.nonzero(ok)
        wx, wy = gx[iy, ix], gy[iy, ix]
        score = self._entry_window_score(wx, wy, B)  # перспектива движения, а не вес точки
        want = max(1, int(THREAT_SECTOR_ENTRIES))
        # УГЛОВОЙ разнос: доля от идеального шага (раствор сектора / число точек)
        ideal = 2.0 * np.radians(float(THREAT_SECTOR_HALF_DEG)) / want
        need_ang = ideal * float(THREAT_SECTOR_MIN_ANGLE_FRAC)
        sep = float(THREAT_SECTOR_MIN_SEP_KM)
        ang = np.arctan2(wy - B[1], wx - B[0])

        def take(min_sep, min_ang):
            out, outa = [], []
            for i in np.argsort(-score):            # лучшие по перспективе вперёд
                q = (float(wx[i]), float(wy[i]))
                if any(np.hypot(q[0] - s[0], q[1] - s[1]) < min_sep for s in out):
                    continue
                if min_ang > 0.0 and any(
                        abs((ang[i] - a + np.pi) % (2 * np.pi) - np.pi) < min_ang
                        for a in outa):
                    continue
                out.append(q); outa.append(float(ang[i]))
                if len(out) >= want:
                    break
            return out

        picked = take(sep, need_ang)
        # край короткий или беден — ослабляем угловое условие, затем километровое:
        # лучше пять точек рядом, чем три с идеальным разносом
        while len(picked) < want and need_ang > 1e-4:
            need_ang *= 0.6
            picked = take(sep, need_ang)
        if len(picked) < want:
            picked = take(0.0, 0.0)
        return picked

    def _entry_window_score(self, xs, ys, target_km):
        """ПЕРСПЕКТИВА ДВИЖЕНИЯ из точки: средний вес прямоугольника, вытянутого от неё
        В СТОРОНУ ЦЕЛИ (`THREAT_SECTOR_WIN_ALONG` ячеек вдоль курса ×
        `THREAT_SECTOR_WIN_ACROSS` поперёк).

        Зачем не вес самой ячейки: приграничная ячейка отражает лишь то, попал ли край
        карты на дорогу или реку. Замер на участке: у ПОЛОВИНЫ кандидатов вес ячейки
        ровно 0, а лучшая по ячейке точка (29.1) по перспективе оказалась только
        13-й — впереди у неё пусто. Совпадение первых пятёрок двух рейтингов — 2 из 5."""
        g = self.grid
        W = np.asarray(g.weight, float)
        ny, nx = W.shape
        B = np.asarray(target_km, float)
        n_al = max(1, int(THREAT_SECTOR_WIN_ALONG))
        n_ac = max(1, int(THREAT_SECTOR_WIN_ACROSS))
        half = n_ac // 2
        out = np.zeros(len(xs))
        for t, (px, py) in enumerate(zip(xs, ys)):
            d = B - np.array([px, py], float)
            n = float(np.hypot(*d))
            if n < 1e-9:
                continue
            a = d / n                                # орт «на цель»
            b = np.array([-a[1], a[0]])              # поперёк курса
            acc = cnt = 0
            for i in range(n_al):
                for j in range(-half, half + 1):
                    q = np.array([px, py]) + a * (i * g.h) + b * (j * g.h)
                    jx = int((q[0] - g.ox) / g.h)
                    jy = int((q[1] - g.oy) / g.h)
                    if 0 <= jx < nx and 0 <= jy < ny:
                        acc += W[jy, jx]; cnt += 1
            out[t] = acc / max(1, cnt)
        return out
