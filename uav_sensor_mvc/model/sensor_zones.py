# -*- coding: utf-8 -*-
"""
MODEL · Места установки датчиков: постройки, лес, неудобья (план 13, задача 13.17).

Данные для правила места «застройка» (ПВН): где стоят постройки и какая в ячейке самая
высокая, где лес и неудобья (болота, кустарник, пустошь, пески, скалы). Карта ПВН из них —
задача 13.4.

⚠️ В ВЕС КАРТЫ НЕ ИДУТ — и не могут: слои лежат ОТДЕЛЬНЫМ файлом участка
(`sensor_layers_<участок>.npz` рядом с `threat_layers_<участок>.npz`), сборка веса его не
читает. Поэтому кэш слоёв веса пересобирать не нужно, и эталон от этих данных не зависит.

ОТКУДА: `tools/pack_area.py` (шаг «места датчиков») разбирает `.osm.pbf` функцией
`sensor_layers_from_osm` — нужен pyrosm, один раз на участок. Модель читает готовый файл
одним numpy. Файла нет — `load()` отдаёт пустой словарь, и построек с лесом просто нет.

СЛОИ ФАЙЛА:
* `building_pts` — один массив (N, 5): x, y км · этажей (NaN — не указано) · класс типа
  (2 — высокий тип, 1 — тип не указан, 0 — низкий) · площадь пятна, м²;
* `forest` — внешние кольца лесных полигонов, массивы (M, 2) км;
* `rough` — то же для неудобий.
⚠️ Дыры полигонов (поляны в лесу) теряются, как и у застройки: деревню на поляне спасает
полоса у построек (13.4). Разбор и замеры — план 13 §0.4б, §0.4в, §0.4е, §11ж.
"""
import os
import warnings

import numpy as np

BUILDING_LAYER = "building_pts"    # постройки: один массив (N, 5)
FOREST_LAYER = "forest"            # лес: внешние кольца полигонов
ROUGH_LAYER = "rough"              # неудобья: внешние кольца полигонов
WETLAND_LAYER = "wetland"          # болота — отдельно от неудобий (план 13.1, Д3): видеть по отдельности

FOREST_FILTER = {"landuse": ["forest"], "natural": ["wood"]}          # лес — оба способа разметки
# ⚠️ болота вынесены в свой слой (Д3); запрет прежний — по объединению неудобий и болот. Старый файл
# мест без слоя `wetland` держит болота в `rough` — карта та же
ROUGH_FILTER = {"natural": ["scrub", "heath", "sand", "bare_rock", "scree"]}   # прочие неудобья
WETLAND_FILTER = {"natural": ["wetland"]}                                   # болота

# Тип здания — запасной признак высоты: этажность указана лишь у 6 % построек `arh` (В13.19).
TALL_KINDS = frozenset((  # типы обычно не ниже двух этажей
    "apartments", "dormitory", "school", "commercial", "industrial", "office", "hospital", "retail",
    "public", "civic", "college", "university", "hotel", "government"))
LOW_KINDS = frozenset((   # одноэтажные типы
    "house", "detached", "semidetached_house", "bungalow", "garage", "garages", "carport", "shed",
    "hut", "cabin", "barn", "farm_auxiliary", "greenhouse", "kiosk", "roof"))

# Упрощение контуров леса и неудобий при разборе, градусы (~50 м по широте): ячейка сетки —
# 500 м, а контур с точностью до метра делает растеризацию в разы дольше без пользы.
SIMPLIFY_DEG = 0.0005

_cache = {}                        # путь -> (время правки файла, слои): файл читается раз на правку


def file_name(layers_file):
    """Имя файла мест датчиков по имени кэша слоёв участка. Вход: имя кэша слоёв.
    Отдаёт: `threat_layers_arh.npz` → `sensor_layers_arh.npz`; иначе приставка `sensor_layers_`."""
    base = os.path.basename(str(layers_file or "")) or "threat_layers.npz"   # имя без папки
    if "threat_layers" in base:                      # обычное имя кэша участка
        return base.replace("threat_layers", "sensor_layers", 1)
    return "sensor_layers_" + base                   # чужое имя — своя приставка


def layers_path():
    """Полный путь файла мест датчиков активного участка (папка участка, откат в корень кэша)."""
    from config import THREAT_LAYERS_FILE
    from model.threat_grid import area_file
    return area_file(file_name(THREAT_LAYERS_FILE))


def _levels(value):
    """Этажность из тега `building:levels`: «2», «2;3», «1,5» → 2.0, 2.0, 1.5. Отдаёт: float или NaN."""
    try:
        head = str(value).replace(",", ".").split(";")[0].strip().split()[0]   # первое число строки
        lev = float(head)                            # этажей
    except (IndexError, ValueError, TypeError):      # пусто, «yes», мусор
        return float("nan")
    return lev if 0.0 < lev < 200.0 else float("nan")   # ноль и сотни этажей — опечатка


def kind_class(kind):
    """Класс типа здания. Вход: значение тега `building`. Отдаёт: 2 высокий, 0 низкий, 1 не ясно."""
    k = str(kind or "").strip().lower()              # тег без регистра и пробелов
    if k in TALL_KINDS:
        return 2
    return 0 if k in LOW_KINDS else 1


def _buildings(osm, lon0, lat0, clip):
    """Постройки из открытого `.pbf`. Вход: pyrosm.OSM, якорь (град), рамка (x0,x1,y0,y1) км.
    Отдаёт: массив (N, 5) float32 — как описан в шапке модуля."""
    from model.geo_frame import KM_PER_DEG_LAT, KM_PER_DEG_LON_EQ, lonlat_to_km
    b = osm.get_buildings()                          # таблица построек: теги и пятна
    if b is None or len(b) == 0:                     # построек в файле нет
        return np.empty((0, 5), np.float32)
    with warnings.catch_warnings():                  # центр в градусах: для точки на карте хватает
        warnings.simplefilter("ignore")
        cen = b.geometry.centroid                    # центры пятен
        area_deg = np.asarray(b.geometry.area, float)   # площадь в квадратных градусах
    lon, lat = np.asarray(cen.x, float), np.asarray(cen.y, float)   # центры, градусы
    x, y = lonlat_to_km(lon, lat, lon0, lat0)
    x, y = np.asarray(x, float), np.asarray(y, float)   # центры, км
    km_lon = KM_PER_DEG_LON_EQ * np.cos(np.radians(lat))   # км в градусе долготы на широте постройки
    area_m2 = area_deg * km_lon * KM_PER_DEG_LAT * 1e6    # площадь пятна, м²
    lev = np.full(len(b), np.nan)                    # этажность из тега; NaN — не указана
    if "building:levels" in b.columns:               # тег есть хоть у кого-то
        lev = np.array([_levels(v) for v in b["building:levels"]], float)   # разобранная этажность
    cls = np.ones(len(b))                            # класс типа; 1 — «не ясно»
    if "building" in b.columns:                      # тип указан
        cls = np.array([kind_class(v) for v in b["building"]], float)       # класс по тегу
    x0, x1, y0, y1 = clip
    keep = np.isfinite(x) & np.isfinite(y) & (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)   # в рамке
    return np.column_stack([x, y, lev, cls, np.nan_to_num(area_m2)])[keep].astype(np.float32)


def _polygons(osm, flt, lon0, lat0, clip):
    """Внешние кольца полигонов по фильтру тегов. Вход: pyrosm.OSM, фильтр, якорь, рамка км.
    Отдаёт: список массивов (M, 2) км; контур целиком вне рамки отброшен, дыры не берутся."""
    from model.threat_grid import _geom_to_kms
    try:
        gdf = osm.get_data_by_custom_criteria(       # объекты с нужными тегами
            custom_filter=flt, filter_type="keep", keep_nodes=False, keep_ways=True, keep_relations=True)
    except Exception:                                # фильтр ничего не нашёл
        return []
    x0, x1, y0, y1 = clip
    out = []
    for geom in ([] if gdf is None else gdf.geometry):
        if geom is None or geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
            continue                                 # линия или точка — не площадь
        simple = geom.simplify(SIMPLIFY_DEG, preserve_topology=False)   # контур с допуском ~50 м
        if simple.is_empty or simple.geom_type not in ("Polygon", "MultiPolygon"):
            continue                                 # полигон мельче допуска выродился
        for ring in _geom_to_kms(simple, lon0, lat0):
            if len(ring) < 3:                        # не кольцо
                continue
            if ring[:, 0].max() < x0 or ring[:, 0].min() > x1 or ring[:, 1].max() < y0 or ring[:, 1].min() > y1:
                continue                             # целиком за рамкой
            out.append(ring.astype(np.float32))
    return out


def sensor_layers_from_osm(pbf_path, lon0, lat0, bbox_lonlat):
    """Постройки, лес и неудобья из `.osm.pbf` (нужен pyrosm). Вход: путь, якорь и рамка в градусах.
    Отдаёт: dict слой -> список массивов, как у `threat_grid.layers_from_osm`."""
    from pyrosm import OSM
    from model.threat_grid import OSM_CLIP_MARGIN_KM, _clip_box_km
    lo, la, ho, ha = bbox_lonlat
    # complete_relations — как у слоёв веса: лес часто размечен мультиполигоном, и без членов за
    # рамкой крупный массив на краю участка пришёл бы с дырой
    osm = OSM(pbf_path, bounding_box=[lo, la, ho, ha], complete_relations=True)
    clip = _clip_box_km(lon0, lat0, (lo, la, ho, ha), OSM_CLIP_MARGIN_KM)   # участок + запас, км
    out = {}
    pts = _buildings(osm, lon0, lat0, clip)          # постройки (N, 5)
    if len(pts):
        out[BUILDING_LAYER] = [pts]
    for layer, flt in ((FOREST_LAYER, FOREST_FILTER), (WETLAND_LAYER, WETLAND_FILTER), (ROUGH_LAYER, ROUGH_FILTER)):
        polys = _polygons(osm, flt, lon0, lat0, clip)   # кольца слоя
        if polys:
            out[layer] = polys
    return out


def load(path=None):
    """Слои мест датчиков из файла. Вход: путь (None — файл участка). Отдаёт: dict слой -> список
    массивов; файла нет или он битый — пустой dict. Прочитанное держится до правки файла."""
    from model.threat_grid import _load_layers_npz
    fp = path or layers_path()                       # файл мест датчиков
    try:
        mtime = os.path.getmtime(fp)                 # время правки — ключ кэша
    except OSError:                                  # файла нет
        return {}                                    # — построек и леса нет
    hit = _cache.get(fp)                             # прошлое чтение или None
    if hit is not None and hit[0] == mtime:          # файл не менялся
        return hit[1]
    try:
        layers = _load_layers_npz(fp)                # слой -> список массивов
    except Exception:                                # битый файл не должен ронять карту
        layers = {}                                  # — будто файла нет
    _cache[fp] = (mtime, layers)
    return layers


def building_floors(pts):
    """Этажей у построек: указанная этажность, иначе по типу — высокий 2, прочие 1.
    Вход: массив (N, 5). Отдаёт: (N,) float."""
    lev, cls = pts[:, 2], pts[:, 3]                  # этажность из тега, класс типа
    return np.where(np.isfinite(lev), lev, np.where(cls >= 2, 2.0, 1.0))


def building_rank(pts):
    """Кто выше: этажность → тип → площадь пятна (решение заказчика, В13.19).
    Вход: массив (N, 5). Отдаёт: (N,) float — больше значит выше."""
    return building_floors(pts) * 1e6 + pts[:, 3] * 1e5 + np.minimum(pts[:, 4], 99999.0)


def cell_buildings(grid, pts):
    """Постройки по ячейкам сетки. Вход: ThreatGrid, массив (N, 5). Отдаёт: dict — `count` (ny,nx) int32,
    `top_xy` (ny,nx,2) км, `top_floors`, `mean_floors` (ny,nx) — средняя этажность (Д2), `top_known` bool; нет — NaN."""
    ny, nx = grid.ny, grid.nx
    count = np.zeros((ny, nx), np.int32)             # построек в ячейке
    top_xy = np.full((ny, nx, 2), np.nan)            # самая высокая постройка ячейки, км
    top_floors = np.full((ny, nx), np.nan)           # её этажей (оценка)
    top_known = np.zeros((ny, nx), bool)             # этажность указана в данных
    mean_floors = np.full((ny, nx), np.nan)          # средняя этажность построек ячейки (оценка, Д2)
    pts = np.asarray(pts, float).reshape(-1, 5)      # постройки: x, y, этажей, класс, м²
    if not len(pts):                                 # построек нет
        return dict(count=count, top_xy=top_xy, top_floors=top_floors, top_known=top_known,
                    mean_floors=mean_floors)
    ix = np.floor((pts[:, 0] - grid.ox) / grid.h).astype(np.int64)   # столбец ячейки
    iy = np.floor((pts[:, 1] - grid.oy) / grid.h).astype(np.int64)   # строка ячейки
    ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)   # постройка внутри сетки
    pts, ix, iy = pts[ok], ix[ok], iy[ok]
    np.add.at(count, (iy, ix), 1)
    flat = iy * nx + ix                              # номер ячейки
    # ⚠️ самую высокую выбираем сортировкой, а не присваиванием `a[flat] = ...`: при повторных
    # индексах numpy не обещает, какое значение останется
    order = np.lexsort((building_rank(pts), flat))   # по ячейке, внутри — по высоте
    last = np.r_[flat[order][1:] != flat[order][:-1], True]   # последний в ячейке — самый высокий
    top = order[last]                                # номера самых высоких построек
    fl = building_floors(pts)                        # этажей у каждой
    top_xy[iy[top], ix[top]] = pts[top, :2]
    top_floors[iy[top], ix[top]] = fl[top]
    top_known[iy[top], ix[top]] = np.isfinite(pts[top, 2])
    fsum = np.zeros((ny, nx))                        # сумма этажей построек ячейки
    np.add.at(fsum, (iy, ix), fl)
    has = count > 0                                  # ячейки с постройками
    mean_floors[has] = fsum[has] / count[has]
    return dict(count=count, top_xy=top_xy, top_floors=top_floors, top_known=top_known,
                mean_floors=mean_floors)


def polygon_mask(grid, polys):
    """Ячейки, чей центр внутри хоть одного полигона. Вход: ThreatGrid, список колец (M, 2) км.
    Отдаёт: (ny, nx) bool. Проверка — только по ячейкам габарита каждого кольца, как у застройки."""
    from model.threat_grid import _points_in_polygon
    out = np.zeros((grid.ny, grid.nx), bool)
    for ring in polys:
        P = np.asarray(ring, float)                  # вершины кольца, км
        if P.ndim != 2 or len(P) < 3:                # не кольцо
            continue
        ix0 = max(0, int(np.floor((P[:, 0].min() - grid.ox) / grid.h)))            # габарит кольца
        ix1 = min(grid.nx - 1, int(np.floor((P[:, 0].max() - grid.ox) / grid.h)))  # в номерах
        iy0 = max(0, int(np.floor((P[:, 1].min() - grid.oy) / grid.h)))            # ячеек:
        iy1 = min(grid.ny - 1, int(np.floor((P[:, 1].max() - grid.oy) / grid.h)))  # столбцы, строки
        if ix0 > ix1 or iy0 > iy1:                   # кольцо вне сетки
            continue
        cx = grid.ox + (np.arange(ix0, ix1 + 1) + 0.5) * grid.h   # центры столбцов габарита, км
        cy = grid.oy + (np.arange(iy0, iy1 + 1) + 0.5) * grid.h   # центры строк габарита, км
        gx, gy = np.meshgrid(cx, cy)
        out[iy0:iy1 + 1, ix0:ix1 + 1] |= _points_in_polygon(gx.ravel(), gy.ravel(), P).reshape(gy.shape)
    return out


ROAD_LAYERS = ("road_major", "road_local", "railway")   # дороги и ЖД, у которых лес разрешён полосой


def settled_mask(grid):
    """Застройка для места датчика: застроенные ячейки, город с буфером, зона штрафа деревень — как
    если бы свободных зон у целей и входов не было (`ThreatGrid._settle_sensor`). Вход: ThreatGrid.
    Отдаёт: (ny,nx) bool. ⚠️ Свободная зона снимает запрет ПРОЛЁТА, а не застройку."""
    out = getattr(grid, "_settle_sensor", None)      # посчитано при разметке пунктов
    if out is None:                                  # пунктов не размечали — застройка по слою
        out = grid._built_cells | grid._urban | (grid._village_penalty != 0)
    return out


def allowed_mask(grid, zc, st, passable):
    """Карта места типа (план 13, 13.4). Вход: ThreatGrid, сводка `cells`, паспорт типа, проходимость
    (ny,nx) bool. Отдаёт: (ny,nx) bool; None — ограничения нет (место «везде», прежний расчёт до бита)."""
    parts = allowed_parts(grid, zc, st, passable)    # слагаемые карты — из них же и итог (Д3)
    return None if parts is None else parts["result"]


def allowed_parts(grid, zc, st, passable):
    """Слагаемые карты места (план 13.1, Д3) — для показа по отдельности; `result` — сама карта. Вход: как у
    `allowed_mask`. Отдаёт: dict (ny,nx) bool — settled, buildings, strip, forest, wetland, rough, nopass, result."""
    from model.threat_grid import _dilate
    if st is None or st.zone != "застройка":         # тип ставится везде
        return None                                  # — фильтровать нечего
    nb = max(0, int(round(float(st.zone_buf_km) / grid.h)))   # полоса у построек, ячеек
    ns = max(0, int(round(float(st.strip_km) / grid.h)))      # полоса в лесу у дорог и построек, ячеек
    settled = settled_mask(grid)                     # застройка — с пунктами в свободных зонах
    bcell = zc["count"] > 0                          # ячейки с постройками
    roads = np.zeros_like(bcell)                     # ячейки с дорогами и ЖД
    for name in ROAD_LAYERS:
        if name in grid._present:                    # слой есть на карте
            roads |= grid._present[name]
    # ⚠️ полоса — только у ПОСТРОЕК, а не у всей застройки: так снят замер §0.4в (карта N2, 473 км²)
    near = _dilate(bcell, nb)                        # постройки ± полоса
    built = settled | near                           # застройка ∪ постройки ± полоса
    strip = _dilate(roads | bcell, ns)               # полоса у дорог и построек, где лес разрешён
    wet = zc.get("wetland", np.zeros_like(bcell))    # болота; старая сводка — их нет отдельно
    # лес, болота и неудобья запрещены, кроме полосы у дорог и построек: в них 158 ячеек с домами
    cover_ok = ~(zc["forest"] | wet | zc["rough"]) | strip
    # ⚠️ ∩ проходимость: в городе пролёт запрещён, и ПВН там не увидит ни одного маршрута
    pas = np.asarray(passable, bool)                 # куда БПЛА может лететь
    return dict(settled=settled, buildings=near, strip=strip, forest=zc["forest"], wetland=wet,
                rough=zc["rough"], nopass=~pas, result=built & cover_ok & pas)


def cells_of(grid, pts, mask):
    """Какие точки лежат в разрешённых ячейках. Вход: ThreatGrid, точки (M,2) км, маска (ny,nx).
    Отдаёт: (M,) bool; точка вне сетки — False."""
    pts = np.asarray(pts, float).reshape(-1, 2)      # точки, км
    ix = np.floor((pts[:, 0] - grid.ox) / grid.h).astype(np.int64)   # столбец ячейки
    iy = np.floor((pts[:, 1] - grid.oy) / grid.h).astype(np.int64)   # строка ячейки
    ok = (ix >= 0) & (ix < grid.nx) & (iy >= 0) & (iy < grid.ny)     # внутри сетки
    out = np.zeros(len(pts), bool)                   # разрешена ли точка
    out[ok] = np.asarray(mask, bool)[iy[ok], ix[ok]]
    return out


def type_nodes(grid, step_km, mask, zc):
    """Своя сетка типа (план 13, 13.5): узлы шагом `step_km` в разрешённых ячейках без построек, а в
    ячейке с постройками — одна точка, самая высокая постройка. Вход: ThreatGrid, шаг км, маска
    (ny,nx), сводка `cells`. Отдаёт: (точки (M,2) км, крыша ли (M,) bool) — сперва крыши, затем узлы."""
    x0, x1, y0, y1 = grid.extent_km()
    xs = np.arange(x0 + 0.5 * step_km, x1, step_km)   # столбцы узлов, км
    ys = np.arange(y0 + 0.5 * step_km, y1, step_km)   # строки узлов, км
    gx, gy = np.meshgrid(xs, ys)
    nodes = np.column_stack([gx.ravel(), gy.ravel()])  # все узлы района
    mask = np.asarray(mask, bool)                    # карта места типа по ячейкам
    built = mask & (zc["count"] > 0)                 # разрешённые ячейки с постройками
    # ⚠️ в ячейке с постройками узлы не нужны: ПВН встаёт на крышу, а не рядом (В13.15); замер
    # §0.4в — привязка бесплатна по засечке и сокращает кандидатов в 3.4 раза
    free = cells_of(grid, nodes, mask & ~built)      # узлы в разрешённых ячейках без построек
    roofs = zc["top_xy"][built].reshape(-1, 2)      # самые высокие постройки, км
    pts = np.vstack([roofs, nodes[free]])            # кандидаты типа, км
    return pts, np.arange(len(pts)) < len(roofs)


def bonus_vector(grid, zc, pts, st):
    """Модульный бонус места (план 13, 13.7): высота крыши и размер пункта. Вход: сетка, сводка
    `cells`, точки (M,2) км, паспорт (`bonus_height`, `bonus_city`). Отдаёт: (M,) — доля от лучшего
    прироста первого шага; None — бонусов у типа нет. Сумма с субмодулярной остаётся субмодулярной."""
    bh = float(getattr(st, "bonus_height", 0.0) or 0.0)   # вес высоты
    bc = float(getattr(st, "bonus_city", 0.0) or 0.0)     # вес размера пункта
    pts = np.asarray(pts, float).reshape(-1, 2)      # места, км
    if (bh <= 0 and bc <= 0) or not len(pts):        # бонусов нет — выбор прежний до бита
        return None
    ix = np.clip(np.floor((pts[:, 0] - grid.ox) / grid.h).astype(np.int64), 0, grid.nx - 1)   # ячейки
    iy = np.clip(np.floor((pts[:, 1] - grid.oy) / grid.h).astype(np.int64), 0, grid.ny - 1)   # и строки
    roof = np.hypot(*(zc["top_xy"][iy, ix] - pts).T) < 1e-9   # точка — крыша самой высокой постройки
    floors = np.where(roof, np.nan_to_num(zc["top_floors"][iy, ix], nan=1.0), 1.0)   # этажей под точкой
    rank_h = np.clip((floors - 1.0) / 4.0, 0.0, 1.0)   # 1 этаж — 0, 5 и выше — 1
    own = grid.own_place_area_km2()                  # площадь «родного» пункта ячейки, км²
    rank_c = own[iy, ix] / own.max() if own.max() > 0 else np.zeros(len(pts))   # крупнейший — 1
    return bh * rank_h + bc * rank_c


def cells(grid, layers):
    """Всё о местах датчиков по ячейкам сетки. Вход: ThreatGrid, слои (`load()`).
    Отдаёт: dict — поля `cell_buildings` плюс `forest`, `wetland`, `rough` (ny,nx) bool."""
    pts = (layers.get(BUILDING_LAYER) or [np.empty((0, 5))])[0]   # постройки участка
    out = cell_buildings(grid, pts)
    out["forest"] = polygon_mask(grid, layers.get(FOREST_LAYER, []))
    out["rough"] = polygon_mask(grid, layers.get(ROUGH_LAYER, []))
    out["wetland"] = polygon_mask(grid, layers.get(WETLAND_LAYER, []))   # старый файл — пусто, болота в `rough`
    return out
