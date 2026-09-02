# -*- coding: utf-8 -*-
"""
ГРАНИЦЫ УЧАСТКА: что накрывают данные и какую рамку из них брать.

Печатает фактические границы экстракта `.osm.pbf` и файла рельефа, считает рамку участка
с отступом внутрь и сразу проверяет, накрывает ли рельеф эту рамку. Готовую строку
`bbox=(...)` можно вставлять в `config.THREAT_AREAS`.

    python tools/area_bounds.py                          # файлы активного участка
    python tools/area_bounds.py --pad 2                  # другой отступ, км
    python tools/area_bounds.py geo_cache/plesetsk/ARX_1.osm.pbf ARX_1_hh.tif

⚠️ ЗАЧЕМ СВОЙ РАЗБОР `.osm.pbf`. Границы лежат в заголовке файла (`HeaderBBox`), но
прочитать их нечем: `osmium` в окружении не установлен, а `pyrosm` заголовок не отдаёт —
он сразу парсит геометрию, и на это уходят минуты вместо миллисекунд. Поэтому здесь
разбирается ровно первый блок protobuf: BlobHeader → Blob → HeaderBlock → HeaderBBox.
Формат простой и стабильный, а читать нужно 4 числа.

⚠️ ЗАЧЕМ ОТСТУП ВНУТРЬ (`THREAT_AREA_PAD_KM` = 2 км). Экстракт обрезан ровно по своему
прямоугольнику, и у самой кромки объекты приходят обрубками: дорога обрывается на
полуслове, контур посёлка теряет половину, река кончается ничем. Весовая карта из таких
огрызков считает край беднее, чем он есть, а рамка участка проходит ровно по срезу — это
и выглядело как «синяя рамка некорректно обрывается» (журнал п. 207, ОГРАНИЧЕНИЯ 5.1в).
"""
import argparse
import math
import os
import struct
import sys
import zlib

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (THREAT_AREA, THREAT_BBOX_LONLAT, THREAT_SOURCE_FILE,   # noqa: E402
                    THREAT_DEM_FILE, THREAT_AREA_PAD_KM)
from model.threat_grid import area_file, dem_files                          # noqa: E402

KM_PER_DEG_LAT = 110.574


# ── разбор protobuf: ровно столько, сколько нужно для заголовка ────────────────────
def _varint(b, i):
    r = s = 0
    while True:
        x = b[i]; i += 1
        r |= (x & 0x7f) << s
        if not x & 0x80:
            return r, i
        s += 7


def _zigzag(n):
    return (n >> 1) ^ -(n & 1)


def _fields(b):
    """Поля protobuf-сообщения: (номер, тип, значение)."""
    i = 0
    while i < len(b):
        key, i = _varint(b, i)
        fnum, wire = key >> 3, key & 7
        if wire == 0:
            v, i = _varint(b, i); yield fnum, 0, v
        elif wire == 2:
            n, i = _varint(b, i); yield fnum, 2, b[i:i + n]; i += n
        elif wire == 5:
            yield fnum, 5, b[i:i + 4]; i += 4
        elif wire == 1:
            yield fnum, 1, b[i:i + 8]; i += 8
        else:
            raise ValueError("неизвестный тип поля protobuf: %d" % wire)


def pbf_bbox(path):
    """(lon_min, lat_min, lon_max, lat_max) из заголовка .osm.pbf. None — bbox не задан."""
    with open(path, "rb") as f:
        n = struct.unpack(">I", f.read(4))[0]
        head = f.read(n)
        blob_len = next(v for fn, _w, v in _fields(head) if fn == 3)
        blob = f.read(blob_len)
    raw = None
    for fnum, _w, v in _fields(blob):
        if fnum == 1:                    # raw — без сжатия
            raw = v
        elif fnum == 3:                  # zlib_data
            raw = zlib.decompress(v)
    if raw is None:
        return None
    for fnum, _w, v in _fields(raw):
        if fnum == 1:                    # HeaderBBox: left/right/top/bottom, нанорадусы
            o = {gf: _zigzag(gv) * 1e-9 for gf, _gw, gv in _fields(v)}
            return (o.get(1), o.get(4), o.get(2), o.get(3))
    return None


def raster_bounds(path):
    """(lon_min, lat_min, lon_max, lat_max) растра рельефа. None — rasterio не читает."""
    try:
        import rasterio
        with rasterio.open(path) as ds:
            b = ds.bounds
            return (b.left, b.bottom, b.right, b.top), (ds.width, ds.height), str(ds.crs)
    except Exception as e:
        print("  рельеф не прочитан: %s: %s" % (type(e).__name__, e))
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="границы данных участка и рамка из них")
    ap.add_argument("pbf", nargs="?", help="экстракт .osm.pbf (по умолчанию — активного участка)")
    ap.add_argument("dem", nargs="?", help="файл рельефа (по умолчанию — активного участка)")
    ap.add_argument("--pad", type=float, default=float(THREAT_AREA_PAD_KM),
                    help="отступ рамки внутрь от края данных, км (по умолчанию %(default)s)")
    a = ap.parse_args(argv)

    pbf = a.pbf or area_file(THREAT_SOURCE_FILE)
    dems = [a.dem] if a.dem else dem_files()
    if a.dem and not os.path.isabs(a.dem) and not os.path.exists(a.dem):
        dems = [area_file(a.dem)]

    line = "=" * 66
    print(line)
    print("ГРАНИЦЫ УЧАСТКА · активный: %s" % THREAT_AREA)
    print(line)
    print("рамка в config : (%.4f, %.4f, %.4f, %.4f)" % THREAT_BBOX_LONLAT)

    if not os.path.exists(pbf):
        print("ЭКСТРАКТ НЕ НАЙДЕН:", pbf)
        return 1
    box = pbf_bbox(pbf)
    if box is None:
        print("В заголовке %s нет bbox — экстракт собран без него." % os.path.basename(pbf))
        return 1
    lo, la, ho, ha = box
    latc = 0.5 * (la + ha)
    kx = 111.320 * math.cos(math.radians(latc))
    print("\nданные OSM     : %s" % os.path.basename(pbf))
    print("  lon %.4f … %.4f   lat %.4f … %.4f" % (lo, ho, la, ha))
    print("  размер %.1f x %.1f км   (км/° долготы на широте %.2f: %.3f)"
          % ((ho - lo) * kx, (ha - la) * KM_PER_DEG_LAT, latc, kx))

    pad = max(0.0, float(a.pad))
    dlon, dlat = pad / kx, pad / KM_PER_DEG_LAT
    L, B, R, T = lo + dlon, la + dlat, ho - dlon, ha - dlat
    print("\nрамка с отступом %.1f км внутрь (правило ОГРАНИЧЕНИЯ 5.1в):" % pad)
    print("  bbox=(%.4f, %.4f, %.4f, %.4f)," % (L, B, R, T))
    w_km, h_km = (R - L) * kx, (T - B) * KM_PER_DEG_LAT
    print("  размер %.1f x %.1f км, сетка 500 м даёт %d x %d ячеек"
          % (w_km, h_km, round(w_km / 0.5), round(h_km / 0.5)))
    print("  углы для THREAT_BBOX_POINTS:")
    print('    "Северо-запад": (%.4f, %.4f),' % (L, T))
    print('    "Юго-запад":    (%.4f, %.4f),' % (L, B))
    print('    "Юго-восток":   (%.4f, %.4f),' % (R, B))

    print("\nрельеф:")
    if not dems:
        print("  файлов нет (THREAT_DEM_FILE = %r) — рельеф будет молча выключен"
              % THREAT_DEM_FILE)
    for d in dems:
        got = raster_bounds(d)
        if not got:
            continue
        (dl, db, dr, dt), (w, h), crs = got
        covers = dl <= L and dr >= R and db <= B and dt >= T
        print("  %s: lon %.4f … %.4f  lat %.4f … %.4f  %d x %d  %s"
              % (os.path.basename(d), dl, dr, db, dt, w, h, crs))
        print("    рамку накрывает: %s" % ("ДА" if covers else "НЕТ — часть участка без высот"))
    print(line)
    print("Дальше: вписать bbox в config.THREAT_AREAS и ПЕРЕСОБРАТЬ слои —")
    print("  python tools/build_threat_grid.py")
    print("Кэш .npz хранит уже пересчитанные километры: без пересборки слои сдвинутся.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
