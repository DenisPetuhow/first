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
"""
import os
import re
import math
import numpy as np

from config import (THREAT_BBOX_LONLAT, THREAT_CELL_M, THREAT_LAYERS,
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
                    THREAT_LMAX_AUTO_FRAC, THREAT_LMAX_CORRIDOR_FRAC)


# ----------------------------------------------------------------------
# Локальная проекция км<->lon/lat (та же формула и якорь, что во view_qt/geomap.py,
# но без импорта view — модель не должна зависеть от слоя отображения). Совпадение
# формул гарантирует, что векторные слои лягут ровно на тайловую подложку вкладки 3.
# ----------------------------------------------------------------------
_KM_PER_DEG_LAT = 110.574


def _km_per_deg_lon(lat0):
    """Сколько км в одном градусе ДОЛГОТЫ на широте lat0. Меридианы сходятся к полюсам,
    поэтому длина градуса долготы = 111.32 км × cos(широта) (по широте она почти постоянна,
    _KM_PER_DEG_LAT). Это и есть локальная равнопромежуточная проекция «градусы → км»."""
    return 111.320 * math.cos(math.radians(lat0))


def lonlat_to_km(lon, lat, lon0, lat0):
    """Перевод (долгота, широта) → (x, y) в км относительно опорной точки (lon0, lat0).
    Работает и со скалярами, и с массивами numpy. Единая км-система для сетки/маршрутов/датчиков."""
    return ((np.asarray(lon, float) - lon0) * _km_per_deg_lon(lat0),
            (np.asarray(lat, float) - lat0) * _KM_PER_DEG_LAT)


def bbox_lonlat_to_km(bbox_lonlat, lon0, lat0):
    """Прямоугольник участка из градусов (lon_min,lat_min,lon_max,lat_max) → в км
    (x0,x1,y0,y1), с сортировкой границ по возрастанию."""
    lo, la, ho, ha = bbox_lonlat
    x0, y0 = lonlat_to_km(lo, la, lon0, lat0)
    x1, y1 = lonlat_to_km(ho, ha, lon0, lat0)
    return (float(min(x0, x1)), float(max(x0, x1)),
            float(min(y0, y1)), float(max(y0, y1)))


def bbox_center_lonlat():
    """Якорь локального км-фрейма вкладки 3 = центр bbox."""
    lo, la, ho, ha = THREAT_BBOX_LONLAT
    return (0.5 * (lo + ho), 0.5 * (la + ha))


def geo_cache_root():
    """Каталог кэша ГЕОДАННЫХ (векторные слои весовой карты) — по аналогии с
    tile_cache/ для тайлов. Переопределяется env UAV_GEO_CACHE."""
    env = os.environ.get("UAV_GEO_CACHE")
    if env:
        return env
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "geo_cache")


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

    def add_point_layer(self, name, points_km, weight):
        """Точечный слой (мост): плоский бонус weight в ячейку с точкой."""
        contrib = np.zeros((self.ny, self.nx), float)
        for x, y in points_km:
            ix = int(math.floor((x - self.ox) / self.h))
            iy = int(math.floor((y - self.oy) / self.h))
            if 0 <= ix < self.nx and 0 <= iy < self.ny:
                contrib[iy, ix] += weight
        self.weight += contrib
        self.layers[name] = self.layers.get(name, 0.0) + contrib

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

    def bridge_cells_km(self):
        """Центры ячеек-мостов — грубая привязка (шаг сетки 500 м). Для маркеров лучше
        `bridge_points_km`: тот даёт реальные координаты."""
        iy, ix = np.nonzero(self._bridge_mask)
        x = self.ox + (ix + 0.5) * self.h
        y = self.oy + (iy + 0.5) * self.h
        return list(zip(x.tolist(), y.tolist()))

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

    # ---- выгрузка ----
    def water_mask(self):
        return self._water

    def urban_mask(self):
        return self._urban

    def urban_cells_km(self):
        """Центры исключённых (городских) ячеек — для показа на карте."""
        iy, ix = np.nonzero(self._urban)
        x = self.ox + (ix + 0.5) * self.h
        y = self.oy + (iy + 0.5) * self.h
        return np.column_stack([x, y]) if len(ix) else np.empty((0, 2))

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
    path = os.path.join(geo_cache_root(), "threat_layers.npz")
    if os.path.exists(path):
        try:
            return _load_layers_npz(path), "OSM (офлайн-кэш)"
        except Exception:
            pass
    return _synthetic_layers(lon0, lat0), "СХЕМА (демо, офлайн)"


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
OSM_CLIP_MARGIN_KM = 10.0


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
    place_pts, place_poly = _places_from_osm(osm, lon0, lat0, clip)
    if len(place_pts):
        out[PLACE_LAYER] = [place_pts]
    if place_poly:
        out[PLACE_POLY_LAYER] = place_poly
    return out


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
                vals = _sample_geotiff(p, lon, lat, cell_deg)
            else:
                vals = _sample_hgt(p, lon, lat)
        except Exception:
            continue
        if vals is not None:
            take = np.isnan(out) & np.isfinite(vals)
            out[take] = vals[take]
    return out if np.isfinite(out).any() else None


def dem_files():
    """Файлы рельефа в geo_cache: `THREAT_DEM_FILE` + .tif/.tiff/.hgt, плюс любые тайлы
    .hgt рядом (N48E038.hgt и т.п.). Пустой список — рельефа нет, и кнопка «Добавить
    рельеф» должна быть неактивна."""
    root = geo_cache_root()
    base = os.path.join(root, THREAT_DEM_FILE)
    cand = [base + ext for ext in (".tif", ".tiff", ".hgt")]
    if os.path.isdir(root):
        cand += [os.path.join(root, f) for f in sorted(os.listdir(root))
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
        with rasterio.open(files[0]) as ds:
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


def km_to_lonlat(x_km, y_km, lon0, lat0):
    """Обратное к lonlat_to_km: км в локальном фрейме -> (долгота, широта)."""
    return (np.asarray(x_km, float) / _km_per_deg_lon(lat0) + lon0,
            np.asarray(y_km, float) / _KM_PER_DEG_LAT + lat0)


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
    empty = (np.empty((0, 3), np.float32), [])
    try:
        gdf = osm.get_data_by_custom_criteria(
            custom_filter=OSM_PLACE_FILTER, filter_type="keep",
            keep_nodes=True, keep_ways=True, keep_relations=True)
    except Exception:
        return empty
    if gdf is None or len(gdf) == 0 or "place" not in gdf.columns:
        return empty
    x0, x1, y0, y1 = clip
    pts, polys = [], []
    for geom, kind in zip(gdf.geometry, gdf["place"]):
        code = PLACE_CODES.get(str(kind), -1)
        if code < 0 or geom is None or geom.is_empty:
            continue
        c = geom.centroid
        x, y = lonlat_to_km(c.x, c.y, lon0, lat0)
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            continue
        pts.append((float(x), float(y), float(code)))
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
            polys)


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
        self.lon0, self.lat0 = bbox_center_lonlat()
        self.bbox_km = bbox_lonlat_to_km(THREAT_BBOX_LONLAT, self.lon0, self.lat0)
        self.layers = {}
        self.source = ""
        self.grid = None
        self.sensors = np.empty((0, 2), float)
        self.candidates = np.empty((0, 2), float)
        self._metrics = {}
        self.dem = None                # высоты рельефа на сетке (None — файла нет)
        self.relief_on = bool(THREAT_DEM_ON_START)   # учитывать рельеф в весе (кнопка)
        self._dem_display = None       # высоты в родном разрешении — только для показа
        self.data_path = None          # явный источник цифровых карт (.npz/.osm.pbf)
        self.enabled_layers = None     # набор включённых слоёв (None = все)
        self.target_km = None          # цель, заданная кликом («указать цель»)
        self.routes = []               # примеры коридоров-центров (список (M,2) км)
        self.iter_routes = []          # итерационные (стохастические) маршруты — накопление
        self.iter_iteration = 0        # номер текущей итерации (как t во вкладке 2)
        self._iter_ctx = None          # контекст выборки (проходимость/поле расстояний/вес)
        self._ctx_built_key = None     # при каком (разрыв, цель) собран контекст — см. _ensure_ctx
        self._iter_rng = None          # ГПСЧ выборки
        self.route_area = None         # маска ВСЕХ возможных мест пролёта (в пределах L_max)
        self.route_min_len = float("inf")   # мин. длина пути вход->цель, км

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
        self.layers, self.source = load_layers(
            self.lon0, self.lat0, self.data_path, THREAT_BBOX_LONLAT)
        probe = ThreatGrid(self.bbox_km, THREAT_CELL_M / 1000.0)   # сетка для выборки высот
        self.dem = load_dem_grid(probe, self.lon0, self.lat0)      # None, если файла нет
        entry, target = self.entry_target_km()
        # вылет — только круг вокруг точки; цель — ещё и её населённый пункт целиком
        # плюс буфер (иначе подлёт к цели в центре города остаётся односторонним)
        free_points = [(entry[0], entry[1], THREAT_ENTRY_FREE_KM, None),
                       (target[0], target[1], THREAT_TARGET_FREE_KM,
                        THREAT_TARGET_FREE_KM)]
        # высоты грузим всегда (их можно показать на карте), но в ВЕС отдаём только по
        # кнопке «Добавить рельеф» — иначе карта менялась бы молча, от факта наличия файла
        self.grid = build_threat_grid(self.layers, self.bbox_km,
                                      enabled=self.enabled_layers,
                                      dem=(self.dem if self.relief_on else None),
                                      free_points=free_points)
        if self.dem is not None and self.relief_on:
            self.source += " + рельеф"
        # маркеры переправ — по РЕАЛЬНЫМ координатам мостов, а не по центрам ячеек
        self.layers["bridge_pts"] = self.grid.bridge_points_km(
            self.layers.get("bridge", []))
        self.sensors = np.empty((0, 2), float)
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
        проходимость) и цель. Запас хода L_max сюда НЕ входит — контекст от него не
        зависит (см. build_iter_context)."""
        return (self._route_gap_km(), self._route_slack_km(), self.target_km)

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
        from .threat_routes import count_possible_routes, _cell_of
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
        """Сгенерировать T маршрутов сразу (без анимации). T = p.threat_iter_routes."""
        from .threat_routes import sample_one_route
        if not self.iter_reset():
            return []
        T = max(1, int(self.p.threat_iter_routes))
        seen, tries, cap, since_new = set(), 0, min(T * 6, 800), 0
        while len(self.iter_routes) < T and tries < cap and since_new < 350:
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
        entry, target = self.entry_target_km()
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
            entry, target = self.entry_target_km()
            ab = float(np.hypot(target[0] - entry[0], target[1] - entry[1]))
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
    def candidate_positions(self):
        """Возможные места установки датчика — узлы регулярной сетки с шагом
        threat_cand_step_km, МИНУС вода (датчик на воду не ставим). Из них жадный алгоритм
        выбирает N лучших (см. place_sensors)."""
        g = self.ensure_built()
        step = max(0.5, float(self.p.threat_cand_step_km))
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

    # ---- расстановка датчиков (ЭТАП 4: по тепловой карте МАРШРУТОВ, §4.6) ----
    def place_sensors(self):
        """Расставить датчики по тепловой карте МАРШРУТОВ (где реально/вероятно летает БПЛА),
        а не по сырому весу — поэтому минус-города датчикам не мешают. Выборка: идут ИТЕРАЦИИ
        → их пролёты; иначе → ВСЕ возможные пути (строятся, если их нет). Правила A/B и разнос."""
        g = self.ensure_built()
        self.candidates = self.candidate_positions()
        if len(self.iter_routes) < 5 and len(self.routes) < 5:
            self.plan_routes()                         # до итераций — по всем возможным путям
        sample = self._sample_for_sensors()
        if sample and len(sample) >= 5:
            self.sensors = self._place_by_routes(self.candidates, sample)
        else:
            self.sensors = self._place_by_weight(self.candidates)
        cells_xy, cells_w = g.flat_cells(positive_only=False)
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
        """Расстановка по ВЫБОРКЕ маршрутов (частота пролёта). Правила (как просил):
        * кандидаты — только МЕЖДУ входом A и целью B по оси (не ЗА B и не ПЕРЕД A);
        * ДВА якоря — у входа A и у цели B, каждый в ~0.45·R от точки внутрь коридора, из
          ближних — тот, что накрывает больше всего маршрутов (не «залипают» в самих A/B);
        * остальные датчики не ближе ~1.6·R к любому уже стоящему (перекрытие зон ≤ ~10 %,
          не кучкуются) — далее обычный жадный субмодулярный выбор по выборке."""
        from .optimization import CoverageCache
        from config import MODES
        if len(cand) == 0 or not routes:
            return np.empty((0, 2), float)
        entry, target = self.entry_target_km()
        A = np.asarray(entry, float); B = np.asarray(target, float)
        R = self.p.threat_R
        d = B - A; D2 = float(d @ d)
        if D2 > 1e-9:                                  # оставить кандидатов между A и B (0≤s≤1)
            s = ((cand - A) @ d) / D2
            cand = cand[(s >= 0.0) & (s <= 1.0)]
        if len(cand) == 0:
            return np.empty((0, 2), float)
        cache = CoverageCache(cand, R, self.p.L_seg, self.p.threat_k)
        for r in routes:
            cache.add_trajectory(r)
        covsum = cache.routes_covered_by_candidate()   # сколько маршрутов накрывает кандидат
        u = d / np.sqrt(D2) if D2 > 1e-9 else np.array([1.0, 0.0])

        def anchor_near(pt):                           # кандидат у точки, max покрытия маршрутов
            near = np.linalg.norm(cand - pt, axis=1) < 0.7 * R
            if near.any():
                idxs = np.nonzero(near)[0]; return int(idxs[np.argmax(covsum[idxs])])
            return int(np.argmin(np.linalg.norm(cand - pt, axis=1)))

        anchor_b = anchor_near(B - 0.45 * R * u)       # у цели B (внутрь, к A)
        anchor_a = anchor_near(A + 0.45 * R * u)       # у входа A (внутрь, к B)
        anchors = [anchor_b] + ([anchor_a] if anchor_a != anchor_b else [])
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

    def _sample_for_sensors(self):
        """Выборка маршрутов, по которой считаются датчики и 2-я тепловая карта (§4.6):
        идут ИТЕРАЦИИ → их выборка; иначе → ВСЕ возможные пути (наиболее вероятные)."""
        if len(self.iter_routes) >= 5:
            return self.iter_routes
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

    def _evaluate(self, cells_xy, cells_w):
        """Показатели: суммарный вес карты, покрытый вес, доля, разбивка по слоям и
        ЗАСЕЧКА МАРШРУТОВ. Последняя — прямая мера качества расстановки: датчики
        ставятся по выборке пролётов, а «покрытый вес» считается по СЫРОЙ весовой карте
        и потому не отражает цель оптимизации (может быть высоким при плохой засечке)."""
        g = self.grid
        total = float(np.sum(np.clip(g.weight, 0, None)))
        covered = 0.0
        if len(self.sensors) and len(cells_xy):
            pos = cells_w > 0
            xy, w = cells_xy[pos], cells_w[pos]
            d = np.linalg.norm(xy[:, None, :] - self.sensors[None, :, :], axis=2)
            seen = (d <= self.p.threat_R).any(axis=1)
            covered = float(np.sum(w[seen]))
        det1 = detk = mean_hits = 0.0
        sample = self._sample_for_sensors()
        if len(self.sensors) and sample:
            hits = np.array([int((np.linalg.norm(
                r[:, None, :] - self.sensors[None, :, :], axis=2).min(axis=0)
                <= self.p.threat_R).sum()) for r in sample])
            det1 = float((hits >= 1).mean())
            detk = float((hits >= self.p.threat_k).mean())
            mean_hits = float(hits.mean())
        return dict(total_weight=total, covered_weight=covered,
                    covered_frac=(covered / total if total > 0 else 0.0),
                    detect_frac=det1, detect_k_frac=detk, mean_hits=mean_hits,
                    n_routes_eval=len(sample) if sample else 0,
                    n_sensors=int(len(self.sensors)),
                    n_candidates=int(len(self.candidates)),
                    by_layer=g.totals_by_layer())

    def metrics(self):
        return self._metrics

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
        self.sync_auto_L_max()
        if self.grid is not None:
            self.build()      # свободная зона привязана к цели -> карту пересобрать

    def entry_km(self):
        _, elon, elat = THREAT_ENTRY
        ex, ey = lonlat_to_km(elon, elat, self.lon0, self.lat0)
        return (float(ex), float(ey))

    def entry_target_km(self):
        """(вход, цель) в км. Вход — фиксированный (демо-точка появления у реки); цель —
        заданная пользователем, иначе дефолтная демо-метка контролируемого объекта."""
        entry = self.entry_km()
        if self.target_km is not None:
            return entry, self.target_km
        _, tlon, tlat = THREAT_TARGET
        tx, ty = lonlat_to_km(tlon, tlat, self.lon0, self.lat0)
        return entry, (float(tx), float(ty))
