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

Файл слоёв СВОЙ у каждого участка (`config.THREAT_AREAS[...]["layers"]`): кэш
прежнего куска карты остаётся лежать рядом и не затирается.

Запуск (на машине пользователя, где установлен геостек):
    pip install pyrosm geopandas shapely pyproj
    python tools/build_threat_grid.py                       # исходник и приёмник — из участка
    python tools/build_threat_grid.py geo_cache/APX.osm.pbf # явный исходник
    python tools/build_threat_grid.py <исходник> <имя.npz>  # ещё и явный приёмник
"""
import os
import sys
import numpy as np

# Windows-консоль по умолчанию cp1251 — печать «→» роняла скрипт ПОСЛЕ сохранения npz
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (THREAT_AREA, THREAT_BBOX_LONLAT, THREAT_LAYERS_FILE,
                    THREAT_SOURCE_FILE)
from model.threat_grid import (area_cache_dir, area_file, bbox_anchor_lonlat,
                               layers_from_osm)


def main(argv):
    pbf = argv[1] if len(argv) > 1 else area_file(THREAT_SOURCE_FILE)
    out_name = argv[2] if len(argv) > 2 else THREAT_LAYERS_FILE
    if not os.path.exists(pbf):
        sys.exit(f"Файл не найден: {pbf}")
    print(f"участок: {THREAT_AREA}  bbox={THREAT_BBOX_LONLAT}")
    print(f"исходник: {pbf}\nприёмник: {out_name}\n")

    lon0, lat0 = bbox_anchor_lonlat()
    try:
        layers = layers_from_osm(pbf, lon0, lat0, THREAT_BBOX_LONLAT)
    except RuntimeError as e:
        sys.exit(str(e))

    out = {}
    for name, parts in layers.items():
        for i, arr in enumerate(parts):
            a = np.asarray(arr)
            # НАЗВАНИЯ пунктов — массив строк, приводить его к float32 нельзя: слой
            # сохраняется как есть, остальные (координаты) — в float32, как раньше
            out[f"{name}__{i}"] = a if a.dtype.kind in "US" else a.astype(np.float32)
        print(f"  [{name}] объектов (частей): {len(parts)}")
    if not out:
        sys.exit("Ни одного объекта не извлечено — проверьте, что bbox pbf-файла "
                 "покрывает участок THREAT_BBOX_LONLAT.")

    dst = os.path.join(area_cache_dir(), out_name)     # кладём в папку участка
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    np.savez_compressed(dst, **out)
    print(f"\nСохранено: {dst}\nВкладка 3 → «Построить карту» теперь возьмёт эти слои.")


if __name__ == "__main__":
    main(sys.argv)
