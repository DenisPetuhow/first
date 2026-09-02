# -*- coding: utf-8 -*-
"""
КОНТРОЛЬНЫЙ ПРОГОН модели — тот самый, которым сверяется любая правка.

Печатает все показатели эталона [ОГРАНИЧЕНИЯ §6](../теория/ОГРАНИЧЕНИЯ.md) и сам
сравнивает их с записанной нормой активного участка: расхождение помечается ⚠, нарушенный
инвариант — ✗. Итоговая строка отвечает на единственный вопрос: сломала правка модель
или нет.

    python tools/reference_run.py                 # полный прогон активного участка
    python tools/reference_run.py --quick         # без 250 «возможных маршрутов» (вдвое быстрее)
    python tools/reference_run.py --routes 400    # другое число итерационных маршрутов

⚠️ ПОЧЕМУ ЭТО СКРИПТ, А НЕ «НАПИШУ КАЖДЫЙ РАЗ ЗАНОВО». Прогон писался руками трижды, и
дважды — с ошибкой в порядке вызовов; на этом уже терялось время:

* `set_relief(True)` вызывается ДО `build()`. Иначе карта собирается без высот, коридоров
  выходит другое число, и кажется, что правка сломала карту (ОГРАНИЧЕНИЯ §6);
* `iter_batch()` вызывается БЕЗ аргумента и сам набирает `p.threat_iter_routes` — в цикле
  его крутить не нужно;
* «датчик на воде» проверяется методом модели `_on_water(...)`: он учитывает смещение
  начала сетки (`ox`, `oy`), а ручной пересчёт `x / h` даёт ложные срабатывания;
* печать идёт в utf-8 принудительно: консоль Windows (cp1251) роняет вывод на «→» и «×»
  ПОСЛЕ того, как прогон отработал, — обиднее всего.
"""
import argparse
import os
import sys
import time

import numpy as np

if hasattr(sys.stdout, "reconfigure"):          # см. про cp1251 в шапке
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Params, THREAT_AREA, THREAT_BBOX_LONLAT      # noqa: E402
from model.threat_grid import ThreatModel, _label8              # noqa: E402


# ЭТАЛОН — по участкам. Числа взяты из ОГРАНИЧЕНИЯ §6/§6.0; там же условия замера.
# Ключ: показатель -> (норма, допуск). Допуск 0 — обязано совпасть точно, иначе указано
# «сколько можно» (стохастика выборки: маршруты разыгрываются случайно).
ETALON = {
    "plesetsk_wide": {
        "сетка":              ((192, 110), 0),
        "коридоров":          (1875, 0),
        "компонент":          (133, 0),
        "крупнейшая":         (520, 0),
        "городская зона":     (51, 0),
        "маршрутов":          (250, 0),
        "итераций":           (150, 0),
        "датчиков малых":     (15, 0),
        "датчиков больших":   (3, 0),
        "область залёта":     (10466, 0),
        "L_max":              (103.7, 0.1),
        "длина средняя":      (92.3, 0.6),
        "длина максимум":     (103.4, 0.6),
        "средняя кратность":  (7.95, 0.4),
    },
    "severodonetsk": {
        "коридоров":          (11101, 0),
        "компонент":          (42, 0),
        "крупнейшая":         (9583, 0),
        "городская зона":     (3441, 0),
        "маршрутов":          (250, 0),
        "итераций":           (150, 0),
        "датчиков малых":     (15, 0),
        "область залёта":     (13987, 0),
        "L_max":              (107.3, 0.1),
    },
}
# ИНВАРИАНТЫ — это не «числа участка», а правила модели: они обязаны выполняться на
# ЛЮБОМ участке. Если хоть один нарушен, сломано соответствующее правило §1–§4.
ZERO_INVARIANTS = ("превышений запаса хода", "датчиков на воде",
                   "датчиков без пролёта", "выходов за область залёта")


def _fmt(v):
    if isinstance(v, tuple):
        return "%d x %d" % v
    if isinstance(v, float):
        return "%.2f" % v
    return str(v)


def main(argv=None):
    ap = argparse.ArgumentParser(description="контрольный прогон модели вкладки 3")
    ap.add_argument("--quick", action="store_true",
                    help="без plan_routes (250 «возможных маршрутов») — вдвое быстрее")
    ap.add_argument("--routes", type=int, default=None,
                    help="сколько итерационных маршрутов набрать (по умолчанию из Params)")
    ap.add_argument("--sensors", type=int, default=15,
                    help="малых датчиков; 15 — как в эталоне (умолчание программы 20)")
    a = ap.parse_args(argv)

    t0 = time.time()
    p = Params()
    p.threat_N = int(a.sensors)
    if a.routes:
        p.threat_iter_routes = int(a.routes)
    m = ThreatModel(p)
    m.set_relief(True)                      # ДО build — см. шапку
    g = m.build()

    got = {}
    W = np.asarray(g.weight, float)
    got["сетка"] = (g.nx, g.ny)
    got["коридоров"] = int((W >= 10).sum())
    lab, n = _label8(W >= 10)
    sizes = np.bincount(np.asarray(lab).ravel())[1:]
    got["компонент"] = int(n)
    got["крупнейшая"] = int(sizes.max()) if len(sizes) else 0
    got["городская зона"] = int(np.asarray(g.urban_mask()).sum())

    if not a.quick:
        m.plan_routes()
        got["маршрутов"] = len(m.routes)
    if m.route_area is not None:
        got["область залёта"] = int(np.asarray(m.route_area).sum())
    got["L_max"] = float(m.p.threat_L_max)

    m.iter_batch()
    R = m.iter_routes
    got["итераций"] = int(m.iter_iteration)
    lens = [float(np.hypot(*np.diff(np.asarray(r, float), axis=0).T).sum())
            for r in R if len(r) > 1]
    if lens:
        got["длина средняя"] = float(np.mean(lens))
        got["длина максимум"] = float(max(lens))
        got["превышений запаса хода"] = sum(1 for L in lens if L > got["L_max"] + 1e-6)
    if m.route_area is not None and R:
        area = np.asarray(m.route_area)
        out = 0
        for r in R:
            xy = np.asarray(r, float)
            ix = np.clip(((xy[:, 0] - g.ox) / g.h).astype(int), 0, g.nx - 1)
            iy = np.clip(((xy[:, 1] - g.oy) / g.h).astype(int), 0, g.ny - 1)
            out += int((~area[iy, ix]).any())
        got["выходов за область залёта"] = out

    m.place_sensors()
    S = np.asarray(m.sensors, float)
    SB = np.asarray(m.sensors_big, float)
    got["датчиков малых"] = len(S)
    got["датчиков больших"] = len(SB)
    got["датчиков на воде"] = int(np.asarray(m._on_water(S)).sum()) if len(S) else 0
    if len(S) and R:
        Rr = float(p.threat_R)
        hit = np.zeros(len(S), int)
        for r in R:
            xy = np.asarray(r, float)
            for i, s in enumerate(S):
                if np.min(np.hypot(xy[:, 0] - s[0], xy[:, 1] - s[1])) <= Rr:
                    hit[i] += 1
        got["датчиков без пролёта"] = int((hit == 0).sum())
    md = m.metrics() or {}
    got["средняя кратность"] = float(md.get("mean_hits", 0.0))
    detect_k = float(md.get("detect_k_frac", 0.0))
    covered = float(md.get("covered_frac", 0.0))

    line = "=" * 66
    print(line)
    print("КОНТРОЛЬНЫЙ ПРОГОН · участок %s %s" % (THREAT_AREA, THREAT_BBOX_LONLAT))
    print(line)
    et = ETALON.get(THREAT_AREA, {})
    bad = 0
    for key, val in got.items():
        mark, note = " ", ""
        if key in ZERO_INVARIANTS:
            if val != 0:
                mark, note, bad = "✗", "  ← ИНВАРИАНТ НАРУШЕН (правила §1–§4)", bad + 1
        elif key in et:
            norm, tol = et[key]
            ok = (val == norm) if tol == 0 else abs(float(val) - float(norm)) <= tol
            if not ok:
                mark, note, bad = "⚠", "  ← эталон: %s" % _fmt(norm), bad + 1
        print(" %s %-26s %s%s" % (mark, key, _fmt(val), note))
    print(" %s %-26s %.3f%s"
          % (" " if detect_k >= 0.99 else "✗", "засечка ≥ k", detect_k,
             "" if detect_k >= 0.99 else "  ← ИНВАРИАНТ НАРУШЕН"))
    if detect_k < 0.99:
        bad += 1
    print("   %-26s %.1f %%" % ("покрытая доля веса", 100 * covered))
    print("   %-26s %.1f с" % ("время прогона", time.time() - t0))
    print(line)
    if not et:
        print("ЭТАЛОНА ДЛЯ ЭТОГО УЧАСТКА НЕТ — числа выведены, сверять не с чем.")
        print("Снять эталон: записать эти числа в ОГРАНИЧЕНИЯ §6 и в ETALON этого скрипта.")
    elif bad == 0:
        print("ВСЁ СОШЛОСЬ С ЭТАЛОНОМ.")
    else:
        print("РАСХОЖДЕНИЙ: %d — разобрать каждое ДО того, как правка уйдёт в коммит." % bad)
        print("Стохастика даёт разброс только там, где в ETALON указан допуск; всё")
        print("остальное обязано совпадать точно.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
