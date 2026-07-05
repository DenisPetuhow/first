# -*- coding: utf-8 -*-
"""
Офлайн-подготовка РЕАЛЬНЫХ слоёв OSM для вкладки 3 «Цифровая карта угроз».

Читает локальный экстракт `.osm.pbf` (выгруженный один раз под bbox участка, см.
теория/МЕТОДИЧКА_ЗАГРУЗКА_КАРТ.md), фильтрует объекты по тегам каждого слоя и
сохраняет `geo_cache/threat_layers.npz` в км-фрейме участка. После этого вкладка 3
берёт реальные данные вместо демо-СХЕМЫ.

Логика чтения OSM живёт в model.threat_grid.layers_from_osm (единый источник — тот
же код использует окно «Выбрать цифровые карты» в интерфейсе). Здесь — только CLI-
обёртка: прочитать → сохранить.

Запуск (на машине пользователя, где установлен геостек):
    pip install pyrosm geopandas shapely pyproj
    python tools/build_threat_grid.py geo_cache/severodonetsk.osm.pbf
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import THREAT_BBOX_LONLAT
from model.threat_grid import bbox_center_lonlat, geo_cache_root, layers_from_osm


def main(argv):
    if len(argv) < 2:
        sys.exit("Использование: python tools/build_threat_grid.py <файл.osm.pbf>")
    pbf = argv[1]
    if not os.path.exists(pbf):
        sys.exit(f"Файл не найден: {pbf}")

    lon0, lat0 = bbox_center_lonlat()
    try:
        layers = layers_from_osm(pbf, lon0, lat0, THREAT_BBOX_LONLAT)
    except RuntimeError as e:
        sys.exit(str(e))

    out = {}
    for name, parts in layers.items():
        for i, arr in enumerate(parts):
            out[f"{name}__{i}"] = np.asarray(arr, np.float32)
        print(f"  [{name}] объектов (частей): {len(parts)}")
    if not out:
        sys.exit("Ни одного объекта не извлечено — проверьте, что bbox pbf-файла "
                 "покрывает участок THREAT_BBOX_LONLAT.")

    dst = os.path.join(geo_cache_root(), "threat_layers.npz")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    np.savez_compressed(dst, **out)
    print(f"\nСохранено: {dst}\nВкладка 3 → «Построить карту» теперь возьмёт эти слои.")


if __name__ == "__main__":
    main(sys.argv)
