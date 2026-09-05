# -*- coding: utf-8 -*-
"""
ПРОВЕРКА ОТЧЁТА: согласованы ли показатели между собой и с параметрами.

    python tools/metrics_check.py           # три типа датчиков + большие
    python tools/metrics_check.py --quick   # без итераций

ЧТО ЭТО ЛОВИТ — и чего не ловят остальные скрипты. `reference_run.py` сверяет числа с
эталоном, `flow_check.py` — порядок работы, `gui_check.py` — состояние виджетов. Никто
из них не проверяет САМ ОТЧЁТ: числа могут быть «нормальными» по отдельности и при этом
противоречить друг другу или считаться не по тем параметрам.

Так и вышло 05.09.2026: засечка и покрытие считались радиусом ТИПА 1 для ВСЕХ датчиков.
Пока тип был один, это было одно и то же; с типами R = 3 и 5 км кратность занижалась —
и заметить это можно было только по отчёту заказчика, глазами. Эталон при этом сходился:
он снят на одном типе.

⚠️ ПОЧЕМУ ПРОВЕРЯЮТСЯ СООТНОШЕНИЯ, А НЕ ЗНАЧЕНИЯ. Значения зависят от местности и от
случайной выборки — «кратность 6.2» само по себе ни о чём не говорит. А вот
«кратность типа не больше общей» или «сумма по типам равна числу датчиков» обязаны
выполняться ВСЕГДА, на любом участке и при любой выборке. Нарушение такого соотношения —
всегда ошибка счёта, а не особенность данных.
"""
import argparse
import os
import sys

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Params, THREAT_AREA                     # noqa: E402
from model.threat_grid import ThreatModel                  # noqa: E402
from model.sensors import sensor_types                     # noqa: E402

LINE = "=" * 70
_fails = []


def check(ok, what, detail=""):
    print("   %s %-48s %s" % ("[ ok ]" if ok else "[ НЕТ]", what, detail))
    if not ok:
        _fails.append(what)
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description="согласованность показателей отчёта")
    ap.add_argument("--quick", action="store_true", help="без итераций")
    a = ap.parse_args(argv)

    p = Params()
    # ТРИ РАЗНЫХ ТИПА С РАЗНЫМИ РАДИУСАМИ — иначе проверка вырождается: при одном типе
    # «радиус каждого датчика» и «threat_R» совпадают, и подмена одного другим не видна.
    p.threat_N, p.threat_R = 8, 2.0
    p.threat_N2, p.threat_R2 = 5, 3.0
    p.threat_N3, p.threat_R3 = 4, 5.0
    p.threat_N_big, p.threat_R_big = 3, 15.0

    m = ThreatModel(p)
    m.build()
    if not a.quick:
        m.iter_reset()
        m.iter_batch()
    m.place_sensors()
    me = m.metrics() or {}
    by = me.get("by_type") or {}

    print(LINE)
    print("ПРОВЕРКА ОТЧЁТА · участок %s · типы 1/2/3 = %d/%d/%d, большие %d"
          % (THREAT_AREA, p.threat_N, p.threat_N2, p.threat_N3, p.threat_N_big))
    print(LINE)

    print("1. СОСТАВ РАССТАНОВКИ")
    n_small, n_big = me.get("n_sensors", 0), me.get("n_big", 0)
    check(n_small == len(m.sensors) and n_big == len(m.sensors_big),
          "числа в отчёте совпадают с расстановкой",
          "малых %d, больших %d" % (n_small, n_big))
    check(sum(d["n"] for d in by.values()) == n_small,
          "сумма по типам равна числу малых датчиков",
          " + ".join("%d" % by[t]["n"] for t in sorted(by)) + " = %d" % n_small)
    check(len(m.sensors_type) == n_small,
          "у каждого датчика проставлен тип", "типов %d" % len(m.sensors_type))
    want = {s.type_id: s.n for s in sensor_types(p) if s.active and s.kind == "small"}
    got = {t: by[t]["n"] for t in by}
    check(got == want, "поставлено столько, сколько заказано по каждому типу",
          "заказано %s, стоит %s" % (want, got))

    print("\n2. РАДИУСЫ (тот самый дефект 05.09.2026)")
    r_want = {s.type_id: s.r_km for s in sensor_types(p)}
    ok_r = all(abs(by[t]["r_km"] - r_want[t]) < 1e-9 for t in by)
    check(ok_r, "у каждого типа в отчёте СВОЙ радиус",
          " · ".join("тип %d: %g км" % (t, by[t]["r_km"]) for t in sorted(by)))
    # прямая проверка: датчик типа 3 обязан ловить маршруты своим радиусом 5 км, а не 2
    sample = m._sample_for_sensors()
    if sample and len(by) > 1:
        big_t = max(by, key=lambda t: by[t]["r_km"])
        small_t = min(by, key=lambda t: by[t]["r_km"])
        check(by[big_t]["mean_hits"] > 0 and by[small_t]["mean_hits"] > 0,
              "оба крайних типа что-то ловят",
              "тип %d: %.1f · тип %d: %.1f" % (big_t, by[big_t]["mean_hits"],
                                               small_t, by[small_t]["mean_hits"]))

    print("\n3. СООТНОШЕНИЯ, КОТОРЫЕ ОБЯЗАНЫ ВЫПОЛНЯТЬСЯ ВСЕГДА")
    d1, dk = me.get("detect_frac", 0.0), me.get("detect_k_frac", 0.0)
    check(dk <= d1 + 1e-9, "засечка ≥k не больше засечки ≥1",
          "%.3f ≤ %.3f" % (dk, d1))
    check(0.0 <= me.get("covered_frac", -1) <= 1.0, "покрытая доля веса в пределах 0…1",
          "%.3f" % me.get("covered_frac", -1))
    tot = me.get("mean_hits", 0.0)
    s_by = sum(d["mean_hits"] for d in by.values())
    check(abs(s_by - tot) < 1e-6, "сумма кратностей по типам равна общей",
          "%.2f против %.2f" % (s_by, tot))
    check(all(d["mean_hits"] <= tot + 1e-9 for d in by.values()),
          "кратность каждого типа не больше общей")
    check(all(0.0 <= d["detect_frac"] <= 1.0 for d in by.values()),
          "доли засечки по типам в пределах 0…1")
    check(all(d["idle"] <= d["n"] for d in by.values()),
          "простаивающих не больше, чем датчиков этого типа")

    print("\n4. ЗАДАННЫЕ ЧЕЛОВЕКОМ (условия моделирования)")
    x0, x1, y0, y1 = m.bbox_km
    m.add_manual_sensor(3, True, x0 + 0.31 * (x1 - x0) + 0.17,
                        y0 + 0.44 * (y1 - y0) + 0.23)
    m.place_sensors()
    me2 = m.metrics() or {}
    check(me2.get("n_static", 0) == 1, "отчёт видит заданную позицию",
          "закреплённых %d" % me2.get("n_static", 0))
    check(me2.get("n_sensors", 0) == n_small,
          "общее число не выросло: заданный вошёл в заказанное",
          "%d против %d" % (me2.get("n_sensors", 0), n_small))
    by2 = me2.get("by_type") or {}
    check(sum(d["n"] for d in by2.values()) == me2.get("n_sensors", 0),
          "сумма по типам сходится и с заданными позициями")

    print("\n" + LINE)
    if _fails:
        print("НЕ ПРОШЛО: %d — %s" % (len(_fails), "; ".join(_fails)))
        return 1
    print("ВСЁ ПРОШЛО. Показатели отчёта согласованы между собой и с параметрами.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
