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
    их нет, синтетическая СХЕМА (река через Северодонецк + опорные дороги/ЛЭП/ж-д),
    чтобы вкладка работала офлайн без внешних данных;
  * ThreatModel — обёртка для контроллера вкладки 3: строит карту, держит слои для
    отрисовки, готовит маску запрета датчиков на воде, считает показатели.

МЕХАНИКА ВЕСА (см. теория/МЕТОДИЧКА_КАРТА_УГРОЗ.md, разделы 3–4):
  вклад линии слоя в ячейку = вес_слоя × (длина пересечения / размер ячейки).
  Так вклад пропорционален тому, НАСКОЛЬКО объект проходит через ячейку (а не факту
  «задел/не задел»), и сопоставим между слоями по масштабу, а не по частоте данных.
"""
import os
import math
import numpy as np

from config import (THREAT_BBOX_LONLAT, THREAT_CELL_M, THREAT_LAYERS,
                    THREAT_LAYER_ORDER, THREAT_INTERSECTION_BONUS,
                    THREAT_WATER_BUFFER_M, THREAT_ENTRY, THREAT_TARGET,
                    THREAT_URBAN_NEIGHBORHOOD_KM, THREAT_URBAN_DENSITY_FRAC,
                    THREAT_URBAN_PENALTY)


# ----------------------------------------------------------------------
# Локальная проекция км<->lon/lat (та же формула и якорь, что во view_qt/geomap.py,
# но без импорта view — модель не должна зависеть от слоя отображения). Совпадение
# формул гарантирует, что векторные слои лягут ровно на тайловую подложку вкладки 3.
# ----------------------------------------------------------------------
_KM_PER_DEG_LAT = 110.574


def _km_per_deg_lon(lat0):
    return 111.320 * math.cos(math.radians(lat0))


def lonlat_to_km(lon, lat, lon0, lat0):
    return ((np.asarray(lon, float) - lon0) * _km_per_deg_lon(lat0),
            (np.asarray(lat, float) - lat0) * _KM_PER_DEG_LAT)


def bbox_lonlat_to_km(bbox_lonlat, lon0, lat0):
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
        self._urban = np.zeros((self.ny, self.nx), bool)         # исключено (нас. пункт)

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

    def apply_urban_exclusion(self, neighborhood_km, density_frac, penalty):
        """Исключить НАСЕЛЁННЫЕ ПУНКТЫ из зоны пролёта: где плотность застройки в окне
        neighborhood_km превышает density_frac — ячейка получает штрафной вес penalty
        (город становится «холодным», а не «горячим» из-за перекрёстков) и блокируется
        для построения маршрута. Одиночный хутор (низкая плотность в окне) — НЕ
        блокируется. Плотность считается box-фильтром (через интегральное изображение,
        O(ячеек))."""
        if not self._built_cells.any():
            return
        r = max(1, int(round(0.5 * neighborhood_km / self.h)))     # полуокно в ячейках
        dens = _box_mean(self._built_cells.astype(np.float64), r)
        self._urban = dens >= density_frac
        self.weight[self._urban] = penalty                          # перекрывает вклад дорог/funnel
        self.layers["urban_excl"] = np.where(self._urban, penalty, 0.0)

    def add_bridges_from_grid(self, weight,
                              road_keys=("road_major", "railway")):
        """Мост = ячейка, где ЕСТЬ река И (КРУПНАЯ дорога или ж/д) — переправа. По сетке
        (пересечение булевых масок), O(ячеек) и векторно — вместо прежнего перебора пар
        ломаных O(N²), который на реальных данных подвешивал приложение.

        МЕСТНЫЕ дороги (residential) СПЕЦИАЛЬНО исключены: в плотной сети жилые улицы
        соприкасаются с ручьями/реками в тысячах ячеек и давали тысячи ложных «мостов»
        (перегружали карту и отрисовку). Настоящая переправа — это магистраль/ж-д над
        рекой, таких на порядок меньше и это корректнее."""
        river = self._present.get("river")
        if river is None:
            self._bridge_mask = np.zeros((self.ny, self.nx), bool)
            return
        road = np.zeros((self.ny, self.nx), bool)
        for k in road_keys:
            m = self._present.get(k)
            if m is not None:
                road |= m
        self._bridge_mask = river & road
        contrib = weight * self._bridge_mask
        self.weight += contrib
        self.layers["bridge"] = contrib.astype(float)

    def bridge_cells_km(self):
        """Центры ячеек-мостов (для маркеров на карте)."""
        iy, ix = np.nonzero(self._bridge_mask)
        x = self.ox + (ix + 0.5) * self.h
        y = self.oy + (iy + 0.5) * self.h
        return list(zip(x.tolist(), y.tolist()))

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
# Вспомогательное: точка в полигоне, дилатация, box-фильтр (плотность)
# ----------------------------------------------------------------------
def _box_mean(a, r):
    """Среднее по квадратному окну (2r+1)² вокруг каждой ячейки — через интегральное
    изображение (O(ячеек)). Для оценки плотности застройки в окрестности."""
    if r <= 0:
        return a
    ny, nx = a.shape
    ii = np.zeros((ny + 1, nx + 1), np.float64)
    ii[1:, 1:] = np.cumsum(np.cumsum(a, axis=0), axis=1)
    y0 = np.clip(np.arange(ny) - r, 0, ny); y1 = np.clip(np.arange(ny) + r + 1, 0, ny)
    x0 = np.clip(np.arange(nx) - r, 0, nx); x1 = np.clip(np.arange(nx) + r + 1, 0, nx)
    Y0, X0 = np.meshgrid(y0, x0, indexing="ij"); Y1, X1 = np.meshgrid(y1, x1, indexing="ij")
    total = ii[Y1, X1] - ii[Y0, X1] - ii[Y1, X0] + ii[Y0, X0]
    area = (Y1 - Y0) * (X1 - X0)
    return total / np.maximum(area, 1)


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
    "river":      {"waterway": ["river", "canal", "stream"]},
    "road_major": {"highway": ["motorway", "trunk", "primary", "secondary"]},
    "road_local": {"highway": ["tertiary", "unclassified", "residential"]},
    "railway":    {"railway": ["rail"]},
    "power":      {"power": ["line"]},
    "pipeline":   {"man_made": ["pipeline"]},
    "tree_row":   {"natural": ["tree_row"]},
    "built_up":   {"landuse": ["residential", "industrial"]},
}


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
    osm = OSM(pbf_path, bounding_box=[lo, la, ho, ha])
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
        if parts:
            out[layer] = [p for p in parts if len(p) >= 2]
    return out


def _geom_to_kms(geom, lon0, lat0):
    """shapely-геометрия -> список массивов (N,2) в км (линии и кольца полигонов)."""
    out = []
    gt = geom.geom_type
    if gt == "LineString":
        out.append(_ll(np.asarray(geom.coords, float), lon0, lat0))
    elif gt in ("MultiLineString", "GeometryCollection"):
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
    """Демо-схема района Северодонецка: русло Северского Донца (аттрактор+вода),
    несколько дорог, ж/д, ЛЭП, лесополос и контур застройки. Координаты приблизительные
    — этого достаточно, чтобы показать МЕХАНИКУ наложения, пока нет реальных OSM-данных.
    """
    # Русло Северского Донца (упрощённо, с С-З на Ю-В через Северодонецк/Счастье)
    river = [(38.42, 49.00), (38.49, 48.95), (38.60, 48.83), (38.78, 48.72),
             (38.95, 48.66), (39.10, 48.60), (39.28, 48.57), (39.45, 48.52)]
    # Крупные дороги
    road_a = [(38.49, 48.95), (38.62, 48.90), (38.80, 48.80), (38.95, 48.70),
              (39.10, 48.62), (39.31, 48.57)]              # к Луганску вдоль реки
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
    built = [rect(38.49, 48.95, 0.06, 0.04),        # Северодонецк/Лисичанск
             rect(39.31, 48.57, 0.05, 0.035),        # Луганск (край bbox)
             rect(38.52, 48.34, 0.04, 0.03)]         # Дебальцево (край)

    def L(seqs):
        return [_ll(s, lon0, lat0) for s in seqs]

    return {
        "river":      L([river]),
        "road_major": L([road_a, road_b, road_c]),
        "road_local": L([road_local1, road_local2]),
        "railway":    L([rail]),
        "power":      L([power1, power2]),
        "pipeline":   L([pipeline]),
        "tree_row":   L([tree1, tree2]),
        "built_up":   L(built),
    }


# ----------------------------------------------------------------------
# Построение весовой карты из слоёв
# ----------------------------------------------------------------------
def build_threat_grid(layers, bbox_km, cell_km=None, enabled=None):
    """Собрать ThreatGrid: наложить слои с весами из THREAT_LAYERS, funnel-бонус,
    мосты, отметить воду для запрета датчиков.

    enabled — набор имён слоёв для наложения (из окна «Выбрать цифровые карты»);
    None = все слои. Выключенный слой просто не участвует в сумме весов."""
    cell_km = (THREAT_CELL_M / 1000.0) if cell_km is None else cell_km
    g = ThreatGrid(bbox_km, cell_km)
    for name in THREAT_LAYER_ORDER:
        if name not in THREAT_LAYERS or name == "bridge":
            continue                            # мост считается по сетке ниже
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
    # мост = ячейка «река + дорога/ж-д» (по сетке, быстро)
    if enabled is None or "bridge" in enabled:
        g.add_bridges_from_grid(THREAT_LAYERS.get("bridge", {}).get("weight", 0.0))
    # funnel-бонус за пересечения слоёв-аттракторов
    g.add_intersection_bonus(THREAT_INTERSECTION_BONUS)
    # исключение населённых пунктов (после всех слоёв — перекрывает вес дорог/funnel
    # в городе; см. apply_urban_exclusion). Только если слой застройки включён.
    if enabled is None or "built_up" in enabled:
        g.apply_urban_exclusion(THREAT_URBAN_NEIGHBORHOOD_KM,
                                THREAT_URBAN_DENSITY_FRAC, THREAT_URBAN_PENALTY)
    # вода -> запрет датчиков (буфер из конфига); только если слой реки включён
    if enabled is None or "river" in enabled:
        g.mark_water(layers.get("river", []), THREAT_WATER_BUFFER_M / 1000.0)
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
        self.data_path = None          # явный источник цифровых карт (.npz/.osm.pbf)
        self.enabled_layers = None     # набор включённых слоёв (None = все)
        self.target_km = None          # цель, заданная кликом («указать цель»)
        self.routes = []               # маршруты вход->цель (список (M,2) км)

    # ---- источник данных / выбор слоёв ----
    def set_data_source(self, path):
        """Задать файл цифровых карт (.npz или .osm.pbf); None -> авто (кэш/схема)."""
        self.data_path = path or None
        self.grid = None               # потребуется пересборка

    def set_enabled_layers(self, names):
        """Ограничить набор накладываемых слоёв (iterable имён); None -> все."""
        self.enabled_layers = set(names) if names is not None else None
        self.grid = None

    # ---- построение карты (наложение цифровых слоёв на сетку) ----
    def build(self):
        self.layers, self.source = load_layers(
            self.lon0, self.lat0, self.data_path, THREAT_BBOX_LONLAT)
        self.grid = build_threat_grid(self.layers, self.bbox_km,
                                      enabled=self.enabled_layers)
        self.layers["bridge_pts"] = self.grid.bridge_cells_km()   # мосты — из сетки
        self.sensors = np.empty((0, 2), float)
        self.routes = []
        return self.grid

    # ---- маршруты пролёта вход->цель по весовой карте ----
    def plan_routes(self):
        from .threat_routes import plan_routes
        from config import (THREAT_ROUTE_BASE_COST, THREAT_ROUTE_WEIGHT_SCALE,
                            THREAT_ROUTE_URBAN_COST, THREAT_ROUTE_COUNT)
        g = self.ensure_built()
        entry, target = self.entry_target_km()
        self.routes = plan_routes(g, entry, target, THREAT_ROUTE_COUNT,
                                  THREAT_ROUTE_BASE_COST, THREAT_ROUTE_WEIGHT_SCALE,
                                  THREAT_ROUTE_URBAN_COST)
        return self.routes

    def ensure_built(self):
        if self.grid is None:
            self.build()
        return self.grid

    # ---- кандидатные позиции датчика (сетка минус вода) ----
    def candidate_positions(self):
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

    # ---- расстановка датчиков по весовой карте ----
    def place_sensors(self):
        from .optimization import CoverageCache
        from config import MODES
        g = self.ensure_built()
        cand = self.candidate_positions()
        self.candidates = cand
        cells_xy, cells_w = g.flat_cells(positive_only=False)
        keep = cells_w != 0.0                      # только значимые ячейки (быстрее)
        cells_xy, cells_w = cells_xy[keep], cells_w[keep]
        cache = CoverageCache(cand, self.p.threat_R, 1, self.p.threat_k)
        cache.set_weighted_cells(cells_xy, cells_w)
        mode = getattr(self.p, "mode", "balanced")
        weights = MODES.get(mode, MODES["balanced"])
        self.sensors = cache.greedy_weighted(
            self.p.threat_N, weights,
            min_sep=self.p.threat_min_sep_frac * self.p.threat_R)
        self._metrics = self._evaluate(cells_xy, cells_w)
        return self.sensors

    def _evaluate(self, cells_xy, cells_w):
        """Показатели: суммарный вес карты, покрытый вес, доля, разбивка по слоям."""
        g = self.grid
        total = float(np.sum(np.clip(g.weight, 0, None)))
        covered = 0.0
        if len(self.sensors) and len(cells_xy):
            pos = cells_w > 0
            xy, w = cells_xy[pos], cells_w[pos]
            d = np.linalg.norm(xy[:, None, :] - self.sensors[None, :, :], axis=2)
            seen = (d <= self.p.threat_R).any(axis=1)
            covered = float(np.sum(w[seen]))
        return dict(total_weight=total, covered_weight=covered,
                    covered_frac=(covered / total if total > 0 else 0.0),
                    n_sensors=int(len(self.sensors)),
                    n_candidates=int(len(self.candidates)),
                    by_layer=g.totals_by_layer())

    def metrics(self):
        return self._metrics

    # ---- точка входа (фиксирована у реки) и цель (можно задать кликом) ----
    def set_target(self, x_km, y_km):
        """Задать целевую точку кликом по карте (режим «указать цель»)."""
        self.target_km = (float(x_km), float(y_km))

    def entry_km(self):
        _, elon, elat = THREAT_ENTRY
        ex, ey = lonlat_to_km(elon, elat, self.lon0, self.lat0)
        return (float(ex), float(ey))

    def entry_target_km(self):
        """(вход, цель) в км. Вход — фиксированный (Северодонецк, у реки); цель —
        заданная пользователем, иначе дефолтная метка (Луганск)."""
        entry = self.entry_km()
        if self.target_km is not None:
            return entry, self.target_km
        _, tlon, tlat = THREAT_TARGET
        tx, ty = lonlat_to_km(tlon, tlat, self.lon0, self.lat0)
        return entry, (float(tx), float(ty))
