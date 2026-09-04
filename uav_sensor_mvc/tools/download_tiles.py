# -*- coding: utf-8 -*-
"""
Предзагрузка тайлов карты в ОФФЛАЙН-кэш (для работы без интернета).

Запустите ОДИН РАЗ при наличии интернета — тайлы выбранного региона и слоёв
скачаются в дисковый кэш (см. geomap.cache_root()), после чего приложение работает
оффлайн. Кэш ограничен 10 уровнями зума (geomap.ZOOM_MIN..ZOOM_MAX), чтобы не
разрастался. Учитывайте правила источников (OSM запрещает массовую выкачку — для
больших объёмов поднимайте свой тайл-сервер или используйте разрешённые источники).

СТРАТЕГИЯ ЗАПАСА (решение заказчика 03.09.2026). Предзагружаются только ОБЗОРНЫЕ
уровни z6…12 на рамку участка плюс 100 км — это 5 254 тайла (57 МБ, ~9 минут). Детальные
z13…15 НЕ качаются заранее: их приложение само возьмёт из сети при приближении и
положит в кэш (`geomap.fetch_tile_bytes`), поэтому платить за них 3.4 ГБ вперёд не
нужно. ⚠️ Работает только со снятым чекбоксом «офлайн».

Примеры:
  python tools/download_tiles.py --layer osm --pad-km 100 --zmin 6 --zmax 12
  python tools/download_tiles.py --layer osm --area plesetsk_wide --pad-km 100
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

# Консоль Windows отдаёт cp1251, а в выводе есть символы вне неё — без этого скрипт
# падает на печати сметы (UnicodeEncodeError), не начав качать.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _area_bbox(name):
    """Рамка участка из реестра `config.THREAT_AREAS`; None — участка нет."""
    try:
        from config import THREAT_AREAS, THREAT_AREA
    except Exception:
        return None, None
    key = name or THREAT_AREA
    rec = THREAT_AREAS.get(key)
    return (list(rec["bbox"]), key) if rec else (None, key)


def _area_tile_dir(name):
    """Имя папки участка в КЭШЕ ТАЙЛОВ: поле `dir` записи участка (у него та же роль,
    что и в geo_cache — см. ОГРАНИЧЕНИЯ 5.1б и 5.1д). Нет записи -> пустая строка,
    то есть старая общая раскладка."""
    try:
        from config import THREAT_AREAS, THREAT_AREA
    except Exception:
        return ""
    rec = THREAT_AREAS.get(name or THREAT_AREA)
    return (rec.get("dir") or "") if rec else ""


def _pad_km(bbox, km):
    """Раздвинуть рамку на km километров во все стороны (широта — 111.32 км/°,
    долгота — с поправкой на косинус средней широты)."""
    lo, la, ho, ha = bbox
    dlat = km / 111.32
    dlon = km / (111.32 * math.cos(math.radians((la + ha) / 2.0)))
    return [lo - dlon, la - dlat, ho + dlon, ha + dlat]


def main():
    ap = argparse.ArgumentParser(description="Предзагрузка тайлов в оффлайн-кэш")
    ap.add_argument("--layer", default="osm", choices=list(gm.TILE_LAYERS),
                    help="слой тайлов")
    ap.add_argument("--bbox", nargs=4, type=float,
                    metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"),
                    default=None,
                    help="регион (долгота/широта). Не задан -> рамка участка (--area)")
    ap.add_argument("--area", default=None,
                    help="имя участка из config.THREAT_AREAS; по умолчанию активный")
    ap.add_argument("--pad-km", dest="pad_km", type=float, default=0.0,
                    help="запас вокруг рамки, КИЛОМЕТРЫ (для обзора берут 100)")
    ap.add_argument("--tile-area", dest="tile_area", default=None,
                    help="папка участка в кэше тайлов; по умолчанию — поле 'dir' "
                         "участка. Пустая строка ('') — старая общая раскладка")
    ap.add_argument("--dry-run", action="store_true",
                    help="только посчитать тайлы и объём, ничего не качать")
    ap.add_argument("--margin", type=float, default=0.0,
                    help="запас по краям региона, ГРАДУСЫ (старый ключ; см. --pad-km)")
    ap.add_argument("--zmin", type=int, default=gm.ZOOM_MIN)
    ap.add_argument("--zmax", type=int, default=gm.ZOOM_MAX)
    ap.add_argument("--delay", type=float, default=0.1,
                    help="пауза между запросами, с (вежливость к серверу)")
    a = ap.parse_args()

    zmin, zmax = a.zmin, min(a.zmax, a.zmin + 9)          # не больше 10 уровней

    bbox = a.bbox
    src = "--bbox"
    key = a.area
    if bbox is None:                                       # рамку берём из участка
        bbox, key = _area_bbox(a.area)
        src = f"участок {key}"
        if bbox is None:
            raise SystemExit(f"участок '{key}' не найден в config.THREAT_AREAS; "
                             f"задайте регион ключом --bbox")

    # ПАПКА УЧАСТКА В КЭШЕ: качаем сразу в неё (ОГРАНИЧЕНИЯ 5.1д). Старое не трогаем —
    # то, что уже лежит в общей раскладке, находится запасным поиском.
    area_dir = a.tile_area
    if area_dir is None:
        area_dir = _area_tile_dir(key)
    if a.pad_km:
        bbox = _pad_km(bbox, a.pad_km)
        src += f" + {a.pad_km:g} км"

    lo, la, ho, ha = bbox
    lo -= a.margin; la -= a.margin; ho += a.margin; ha += a.margin
    print(f"Слой: {a.layer} | {src}")
    print(f"Регион lon[{lo:.4f},{ho:.4f}] lat[{la:.4f},{ha:.4f}] | зумы {zmin}..{zmax}")
    print(f"Кэш: {gm.cache_root()}")
    where = os.path.join(area_dir, a.layer) if area_dir else a.layer
    print(f"Пишем в: {where}\\{{z}}\\{{x}}\\{{y}}.png"
          + ("" if area_dir else "   (СТАРАЯ общая раскладка — участок не задан)"))

    # ПРЕДВАРИТЕЛЬНАЯ СМЕТА: сколько тайлов и места. Считается ДО скачивания, потому что
    # цена ошибки в границах высокая — лишний уровень зума даёт рост вчетверо.
    plan = []
    for z in range(zmin, zmax + 1):
        x0 = int(math.floor(gm.deg2num(lo, ha, z)[0]))
        x1 = int(math.floor(gm.deg2num(ho, la, z)[0]))
        y0 = int(math.floor(gm.deg2num(lo, ha, z)[1]))
        y1 = int(math.floor(gm.deg2num(ho, la, z)[1]))
        plan.append((z, x0, x1, y0, y1, (x1 - x0 + 1) * (y1 - y0 + 1)))
    n_all = sum(p[5] for p in plan)
    print(f"Смета: {n_all} тайлов ≈ {n_all * 11.1 / 1024:.0f} МБ, "
          f"≈ {n_all * a.delay / 60:.0f} мин при паузе {a.delay} с")
    if a.dry_run:
        for z, _x0, _x1, _y0, _y1, n in plan:
            print(f"  z={z}: {n} тайлов ≈ {n * 11.1 / 1024:.1f} МБ")
        return

    total = new = had = 0
    for z, x0, x1, y0, y1, n in plan:
        print(f"  z={z}: {n} тайлов …", end="", flush=True)
        ok = 0
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                # УЖЕ ЕСТЬ? — ищем в обеих раскладках (папка участка + старая общая),
                # иначе заново скачали бы то, что лежит с прошлых загрузок
                if gm.find_cached_tile(a.layer, z, x, y, area=area_dir):
                    ok += 1; had += 1; continue
                data = gm.fetch_tile_bytes(a.layer, z, x, y, allow_net=True,
                                           area=area_dir)
                if data is not None:
                    ok += 1; new += 1
                if a.delay:
                    time.sleep(a.delay)
        total += ok
        print(f" готово {ok}/{n}")
    print(f"ИТОГО в кэше: ~{total} тайлов (скачано сейчас {new}, было раньше {had}).")
    print("Детальные уровни z13-15 заранее не качаем: приложение докачает их само при "
          "приближении — при СНЯТОМ чекбоксе «офлайн».")


if __name__ == "__main__":
    main()
