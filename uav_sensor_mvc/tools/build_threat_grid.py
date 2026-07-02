# -*- coding: utf-8 -*-
"""
Офлайн-подготовка РЕАЛЬНЫХ слоёв OSM для вкладки 3 «Цифровая карта угроз».

Читает локальный экстракт `.osm.pbf` (выгруженный один раз под bbox участка через
BBBike Extract, см. теория/МЕТОДИЧКА_КАРТА_УГРОЗ.md §7), фильтрует объекты по тегам
каждого слоя, переводит их в локальный км-фрейм участка (тот же якорь и формула, что
в приложении — совпадение гарантирует, что слои лягут ровно на тайловую подложку) и
сохраняет `geo_cache/threat_layers.npz`. После этого вкладка 3 → «Построить карту»
берёт реальные данные вместо демо-СХЕМЫ.

Запуск (на машине пользователя, где установлен геостек):
    pip install pyrosm geopandas shapely pyproj
    python tools/build_threat_grid.py geo_cache/severodonetsk.osm.pbf

Скрипт НЕ является частью приложения (offline-препроцессинг). Приложение работает и
без него — тогда рисуется офлайн-СХЕМА (демо).
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import THREAT_BBOX_LONLAT
from model.threat_grid import bbox_center_lonlat, lonlat_to_km, geo_cache_root

# Фильтры тегов OSM по слоям (см. config.THREAT_LAYERS / методичку §2).
LAYER_FILTERS = {
    "river":      {"waterway": ["river", "canal", "stream"]},
    "road_major": {"highway": ["motorway", "trunk", "primary", "secondary"]},
    "road_local": {"highway": ["tertiary", "unclassified", "residential"]},
    "railway":    {"railway": ["rail"]},
    "power":      {"power": ["line"]},
    "pipeline":   {"man_made": ["pipeline"]},
    "tree_row":   {"natural": ["tree_row"]},
    "built_up":   {"landuse": ["residential", "industrial"]},
}


def _require_pyrosm():
    try:
        from pyrosm import OSM
        return OSM
    except Exception as e:
        sys.exit("Нужен pyrosm/geopandas. Установите:\n"
                 "  pip install pyrosm geopandas shapely pyproj\n"
                 "(на Windows при ошибках сборки GDAL — через conda:\n"
                 "  conda install -c conda-forge geopandas pyrosm)\n"
                 f"Исходная ошибка: {e}")


def _geom_to_kms(geom, lon0, lat0):
    """shapely-геометрия -> список массивов (N,2) в км (линии/кольца полигонов)."""
    out = []
    gt = geom.geom_type
    if gt == "LineString":
        xy = np.asarray(geom.coords, float)
        out.append(_ll(xy, lon0, lat0))
    elif gt in ("MultiLineString", "GeometryCollection"):
        for g in geom.geoms:
            out += _geom_to_kms(g, lon0, lat0)
    elif gt == "Polygon":
        out.append(_ll(np.asarray(geom.exterior.coords, float), lon0, lat0))
    elif gt == "MultiPolygon":
        for g in geom.geoms:
            out.append(_ll(np.asarray(g.exterior.coords, float), lon0, lat0))
    return out


def _ll(xy, lon0, lat0):
    x, y = lonlat_to_km(xy[:, 0], xy[:, 1], lon0, lat0)
    return np.column_stack([x, y]).astype(np.float32)


def main(argv):
    if len(argv) < 2:
        sys.exit("Использование: python tools/build_threat_grid.py <файл.osm.pbf>")
    pbf = argv[1]
    if not os.path.exists(pbf):
        sys.exit(f"Файл не найден: {pbf}")
    OSM = _require_pyrosm()

    lon0, lat0 = bbox_center_lonlat()
    lo, la, ho, ha = THREAT_BBOX_LONLAT
    osm = OSM(pbf, bounding_box=[lo, la, ho, ha])

    out = {}
    for layer, flt in LAYER_FILTERS.items():
        try:
            gdf = osm.get_data_by_custom_criteria(
                custom_filter=flt, filter_type="keep",
                keep_nodes=False, keep_ways=True, keep_relations=True)
        except Exception as e:
            print(f"  [{layer}] пропущен ({e})")
            continue
        if gdf is None or len(gdf) == 0:
            print(f"  [{layer}] объектов: 0")
            continue
        n = 0
        for geom in gdf.geometry:
            if geom is None or geom.is_empty:
                continue
            for arr in _geom_to_kms(geom, lon0, lat0):
                if len(arr) >= 2:
                    out[f"{layer}__{n}"] = arr
                    n += 1
        print(f"  [{layer}] объектов (частей): {n}")

    if not out:
        sys.exit("Ни одного объекта не извлечено — проверьте, что bbox pbf-файла "
                 "покрывает участок THREAT_BBOX_LONLAT.")

    dst = os.path.join(geo_cache_root(), "threat_layers.npz")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    np.savez_compressed(dst, **out)
    print(f"\nСохранено: {dst}\nВкладка 3 → «Построить карту» теперь возьмёт эти слои.")


if __name__ == "__main__":
    main(sys.argv)
