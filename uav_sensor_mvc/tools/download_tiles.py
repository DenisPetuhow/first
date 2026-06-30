# -*- coding: utf-8 -*-
"""
Предзагрузка тайлов карты в ОФФЛАЙН-кэш (для работы без интернета).

Запустите ОДИН РАЗ при наличии интернета — тайлы выбранного региона и слоёв
скачаются в дисковый кэш (см. geomap.cache_root()), после чего приложение работает
оффлайн. Кэш ограничен 10 уровнями зума (geomap.ZOOM_MIN..ZOOM_MAX), чтобы не
разрастался. Учитывайте правила источников (OSM запрещает массовую выкачку — для
больших объёмов поднимайте свой тайл-сервер или используйте разрешённые источники).

Примеры:
  python tools/download_tiles.py --layer osm
  python tools/download_tiles.py --layer satellite --zmax 15 --zmin 9
  python tools/download_tiles.py --layer osm --bbox 34.0 50.9 38.5 52.4
"""
import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from view_qt import geomap as gm   # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Предзагрузка тайлов в оффлайн-кэш")
    ap.add_argument("--layer", default="osm", choices=list(gm.TILE_LAYERS),
                    help="слой тайлов")
    ap.add_argument("--bbox", nargs=4, type=float,
                    metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"),
                    default=list(gm.OBLAST_BBOX),
                    help="регион (долгота/широта); по умолчанию — Курская область")
    ap.add_argument("--margin", type=float, default=0.3,
                    help="запас по краям региона, градусы")
    ap.add_argument("--zmin", type=int, default=gm.ZOOM_MIN)
    ap.add_argument("--zmax", type=int, default=gm.ZOOM_MAX)
    ap.add_argument("--delay", type=float, default=0.1,
                    help="пауза между запросами, с (вежливость к серверу)")
    a = ap.parse_args()

    zmin, zmax = a.zmin, min(a.zmax, a.zmin + 9)          # не больше 10 уровней
    lo, la, ho, ha = a.bbox
    lo -= a.margin; la -= a.margin; ho += a.margin; ha += a.margin
    print(f"Слой: {a.layer} | регион lon[{lo:.2f},{ho:.2f}] lat[{la:.2f},{ha:.2f}] "
          f"| зумы {zmin}..{zmax}")
    print(f"Кэш: {gm.cache_root()}")

    total = 0
    for z in range(zmin, zmax + 1):
        x0 = int(math.floor(gm.deg2num(lo, ha, z)[0]))
        x1 = int(math.floor(gm.deg2num(ho, la, z)[0]))
        y0 = int(math.floor(gm.deg2num(lo, ha, z)[1]))
        y1 = int(math.floor(gm.deg2num(ho, la, z)[1]))
        n = (x1 - x0 + 1) * (y1 - y0 + 1)
        print(f"  z={z}: {n} тайлов …", end="", flush=True)
        ok = 0
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                path = gm.tile_cache_path(a.layer, z, x, y)
                if os.path.exists(path):
                    ok += 1; continue
                data = gm.fetch_tile_bytes(a.layer, z, x, y, allow_net=True)
                if data is not None:
                    ok += 1
                if a.delay:
                    time.sleep(a.delay)
        total += ok
        print(f" готово {ok}/{n}")
    print(f"ИТОГО в кэше: ~{total} тайлов. Теперь карта работает оффлайн.")


if __name__ == "__main__":
    main()
