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

from config import THREAT_AREA, THREAT_BBOX_LONLAT                    # noqa: E402
from view_qt import geomap as gm                                       # noqa: E402


def count_layer(root, layer, lo, la, ho, ha, z0, z1):
    """[(зум, есть, всего)] по слою в рамке."""
    out = []
    for z in range(z0, z1 + 1):
        x0, y0 = gm.deg2num(lo, ha, z)          # верх-лево: макс. широта
        x1, y1 = gm.deg2num(ho, la, z)          # низ-право
        have = total = 0
        for x in range(int(x0), int(x1) + 1):
            for y in range(int(y0), int(y1) + 1):
                total += 1
                if os.path.exists(os.path.join(root, layer, str(z), str(x), "%d.png" % y)):
                    have += 1
        out.append((z, have, total))
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
    z0, z1 = a.zoom

    line = "=" * 66
    print(line)
    print("ПОКРЫТИЕ ТАЙЛАМИ · участок %s" % (THREAT_AREA if not a.bbox else "своя рамка"))
    print("рамка: (%.4f, %.4f, %.4f, %.4f)" % (lo, la, ho, ha))
    print("кэш  : %s" % root)
    print(line)
    missing_any = False
    for layer in layers:
        rows = count_layer(root, layer, lo, la, ho, ha, z0, z1)
        have = sum(h for _z, h, _t in rows)
        total = sum(t for _z, _h, t in rows)
        print("\nслой %s: есть %d из %d (%.0f %%)"
              % (layer, have, total, 100.0 * have / max(1, total)))
        for z, h, t in rows:
            pct = 100.0 * h / max(1, t)
            mark = " " if pct >= 99.5 else ("~" if pct >= 50 else "!")
            bar = "█" * int(pct / 5) + "·" * (20 - int(pct / 5))
            print("  %s z=%-2d %s %5d / %-5d %3.0f %%" % (mark, z, bar, h, t, pct))
            if pct < 99.5:
                missing_any = True
    print("\n" + line)
    if missing_any:
        print("Недостающее докачать (нужен интернет):")
        print("  python tools/download_tiles.py --layer %s --bbox %.4f %.4f %.4f %.4f"
              % (layers[0], lo, la, ho, ha))
        print("Пустые уровни на расчёт не влияют — только на вид подложки.")
    else:
        print("Кэш покрывает рамку полностью на всех запрошенных зумах.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
