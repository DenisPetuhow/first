# -*- coding: utf-8 -*-
"""
ПОКРЫТИЕ КЭША ТАЙЛАМИ по рамке участка: сколько картинок подложки есть, а сколько нет,
с разбивкой по зумам — и готовая команда докачки недостающего.

    python tools/tile_coverage.py                 # активный участок, слои из config
    python tools/tile_coverage.py --zoom 7 15     # другой диапазон зумов
    python tools/tile_coverage.py --layer osm

⚠️ ЗАЧЕМ РАЗБИВКА ПО ЗУМАМ, А НЕ ОДНО ЧИСЛО. Общий процент обманчив: тайлов на уровне
вчетверо больше, чем на предыдущем, поэтому пустые z = 14–15 утягивают итог к нулю, даже
когда обзорные уровни покрыты полностью. Замер на участке `plesetsk_wide`: суммарно
**7 %**, а по уровням — z ≤ 12 по 100 %, z 13 — 85 %, z 14 — 6 %, z 15 — 0 %. Первое
число пугает, второе объясняет: на обзоре карта полная, пустеет только при сильном
приближении.

⚠️ Тайлы нужны ТОЛЬКО для подложки. Расчёт (весовая карта, маршруты, датчики) идёт по
`.npz` и рельефу и от кэша не зависит вовсе — пустая подложка не ломает ничего, кроме
внешнего вида. Сборка `.exe` кладёт внутрь лишь тайлы своего участка (см. build_demo.spec).
"""
import argparse
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import THREAT_AREA, THREAT_BBOX_LONLAT, THREAT_AREA_DIR   # noqa: E402
from view_qt import geomap as gm                                       # noqa: E402


def count_layer(root, layer, lo, la, ho, ha, z0, z1, area=""):
    """[(зум, есть, всего, в папке участка)] по слою в рамке.

    ⚠️ Считает ОБЕ раскладки: папку участка (`<кэш>/<участок>/<слой>/…`, туда качается
    новое) и прежнюю общую (`<кэш>/<слой>/…`, там всё скачанное до 03.09.2026 — его
    намеренно не перекладывали, ОГРАНИЧЕНИЯ 5.1д). Тайл, найденный в любой из них,
    считается имеющимся: программа ищет так же."""
    out = []
    for z in range(z0, z1 + 1):
        x0, y0 = gm.deg2num(lo, ha, z)          # верх-лево: макс. широта
        x1, y1 = gm.deg2num(ho, la, z)          # низ-право
        have = total = in_area = 0
        for x in range(int(x0), int(x1) + 1):
            for y in range(int(y0), int(y1) + 1):
                total += 1
                found = gm.find_cached_tile(layer, z, x, y, area=area)
                if found:
                    have += 1
                    if area and os.path.sep + area + os.path.sep in found:
                        in_area += 1
        out.append((z, have, total, in_area))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="покрытие кэша тайлами по рамке участка")
    ap.add_argument("--layer", action="append", default=None,
                    help="слой подложки; можно повторять (по умолчанию — osm и osm_hot: "
                         "именно они кладутся в сборку)")
    ap.add_argument("--zoom", nargs=2, type=int, default=(7, 15), metavar=("МИН", "МАКС"))
    ap.add_argument("--bbox", nargs=4, type=float, default=None,
                    metavar=("LON0", "LAT0", "LON1", "LAT1"),
                    help="своя рамка вместо рамки активного участка")
    a = ap.parse_args(argv)

    lo, la, ho, ha = a.bbox if a.bbox else THREAT_BBOX_LONLAT
    # умолчание — те же два слоя, что кладутся в сборку .exe (config.DEMO_MAP_LAYERS
    # в сборочной ветке); список задан здесь, чтобы скрипт работал в ЛЮБОЙ ветке
    layers = a.layer or ["osm", "osm_hot"]
    root = gm.cache_root()
    # папка участка в кэше; со своей рамкой (--bbox) участок неизвестен — считаем обе
    # раскладки, но «в папке участка» показываем по активному
    area = THREAT_AREA_DIR or ""
    z0, z1 = a.zoom

    line = "=" * 66
    print(line)
    print("ПОКРЫТИЕ ТАЙЛАМИ · участок %s" % (THREAT_AREA if not a.bbox else "своя рамка"))
    print("рамка: (%.4f, %.4f, %.4f, %.4f)" % (lo, la, ho, ha))
    print("кэш  : %s" % root)
    print(line)
    missing_any = False
    for layer in layers:
        rows = count_layer(root, layer, lo, la, ho, ha, z0, z1, area=area)
        have = sum(h for _z, h, _t, _a in rows)
        total = sum(t for _z, _h, t, _a in rows)
        in_area = sum(ia for _z, _h, _t, ia in rows)
        print("\nслой %s: есть %d из %d (%.0f %%), из них в папке участка %d"
              % (layer, have, total, 100.0 * have / max(1, total), in_area))
        for z, h, t, ia in rows:
            pct = 100.0 * h / max(1, t)
            mark = " " if pct >= 99.5 else ("~" if pct >= 50 else "!")
            bar = "█" * int(pct / 5) + "·" * (20 - int(pct / 5))
            print("  %s z=%-2d %s %5d / %-5d %3.0f %%%s"
                  % (mark, z, bar, h, t, pct, ("   в участке %d" % ia) if ia else ""))
            if pct < 99.5:
                missing_any = True
    print("\n" + line)
    if missing_any:
        print("Недостающее докачать (нужен интернет):")
        print("  python tools/download_tiles.py --layer %s --pad-km 0 --zmin %d --zmax %d"
              % (layers[0], z0, min(z1, z0 + 9)))
        print("Обзорные уровни берут z6-12; z13-15 приложение докачает само при")
        print("приближении — при СНЯТОМ чекбоксе «офлайн».")
        print("Пустые уровни на расчёт не влияют — только на вид подложки.")
    else:
        print("Кэш покрывает рамку полностью на всех запрошенных зумах.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
