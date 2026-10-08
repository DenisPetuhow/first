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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))   # код программы — в src/ (журнал 364)

from config import Params, THREAT_AREA                     # noqa: E402
from model.threat.threat_grid import ThreatModel                  # noqa: E402
from model.placement.sensors import sensor_types                     # noqa: E402

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

    print("\n5. НАГРУЗКА НА СРЕДСТВО (план 13.4)")
    import numpy as np                                            # noqa: E402
    from model.placement.sensor_load import seen_matrix, sort_rows          # noqa: E402
    keep5 = (np.array(m.sensors, copy=True), np.array(m.sensors_big, copy=True))   # расстановка до расчёта
    sample5 = m._sample_for_sensors()                             # единая выборка
    det5 = m._detection(sample5)                                  # засечка «Анализа» на ней же
    ld = m.sensor_load()                                          # нагрузка на средства
    rows5, table5 = ld["rows"], m.sensors_table()                 # строки нагрузки и список средств
    check(len(rows5) == len(m.sensors) + len(m.sensors_big) and [r["n"] for r in rows5] == [t["n"] for t in table5],
          "строка на каждое средство, номера — из списка средств", "%d строк, маршрутов %d" % (len(rows5), ld["n_routes"]))
    check(all(0.0 <= r["load_pct"] <= 100.0 and r["routes"] <= ld["n_routes"] for r in rows5),
          "нагрузка в пределах 0…100 %")
    small5 = [r for r in rows5 if not r["big"]]                   # малые средства
    s5 = sum(r["routes"] for r in small5)                         # Σ маршрутов через малые
    check(abs(s5 - det5["mean_hits"] * det5["n"]) < 1e-6,
          "Σ маршрутов через малые = средняя кратность × M (тождество)",
          "%d = %.3f × %d" % (s5, det5["mean_hits"], det5["n"]))
    seen5 = seen_matrix(sample5, m.sensors, m._sensor_radii())   # маршрут × малое средство
    tids5 = np.asarray(m.sensors_type, int)                       # тип каждого малого
    ok_u = all(abs(float(seen5[:, tids5 == t].any(axis=1).mean()) - d["detect_frac"]) < 1e-12
               for t, d in det5["by_type"].items())                # объединение по типу = засечка типа
    check(ok_u, "объединение маршрутов по типу = доля засечки типа в «Анализе»")
    df5 = sort_rows(rows5)                                        # порядок по умолчанию
    check(all((a["big"], -a["load_pct"], a["n"]) <= (b["big"], -b["load_pct"], b["n"]) for a, b in zip(df5, df5[1:]))
          and all(r["big"] for r in df5[len(small5):]),
          "по умолчанию: малые по убыванию нагрузки, большие в конце")
    check(np.array_equal(keep5[0], m.sensors) and np.array_equal(keep5[1], m.sensors_big)
          and m._detection(sample5)["mean_hits"] == det5["mean_hits"],
          "расчёт нагрузки расстановку и показатели не меняет")
    rad5 = np.r_[m._sensor_radii(), np.full(len(m.sensors_big), float(getattr(m.p, "threat_R_big", 0.0) or 0.0))]   # км
    check(all(r["x_km"] == t["x_km"] and r["y_km"] == t["y_km"] for r, t in zip(rows5, table5))
          and np.allclose([r["r_km"] for r in rows5], rad5),
          "у строки — место и радиус средства (подсветка на карте)")
    # по цели (этап 2 плана 13.4): маршруты делятся между целями без остатка — Σ по целям = вся выборка у каждого средства;
    # вторая цель — на время, у конца последнего маршрута, чтобы деление было не пустым
    keep_t5 = list(m.targets.items)                               # цели до проверки — вернуть в конце
    end5 = np.asarray(sample5[-1], float)[-1] if sample5 else None   # конец последнего маршрута, км
    why5 = "цель не добавлялась"                                  # почему второй цели нет
    if end5 is not None and len(m.targets) < 2:                   # цель одна — завести вторую у конца маршрута
        if not len(m.targets):                                    # стоит цель по умолчанию — поставить её явно первой
            m.add_target(*[float(v) for v in m.target_only_km()])
        why5 = m.add_target(float(end5[0]), float(end5[1])) or "вторая цель у конца маршрута"
    try:
        parts5 = [m.sensor_load(i) for i in range(len(m.target_points_km()))]   # нагрузка по каждой цели
        sum5 = {}                                                 # номер средства -> Σ маршрутов по целям
        for p5 in parts5:                                         # цель — её снимок
            for r in p5["rows"]:                                  # средство в снимке цели
                sum5[r["n"]] = sum5.get(r["n"], 0) + r["routes"]
        check(sum(p5["n_routes"] for p5 in parts5) == ld["n_routes"]
              and all(sum5.get(r["n"], 0) == r["routes"] for r in rows5) and all(p5["target"] == i for i, p5 in enumerate(parts5)),
              "по целям: Σ маршрутов и Σ через каждое средство = вся выборка",
              "целей %d, маршрутов %s (%s)" % (len(parts5), " + ".join(str(p5["n_routes"]) for p5 in parts5), why5))
        far5 = m.sensor_load(99)                                  # номера такой цели нет
        check(far5["target"] is None and far5["n_routes"] == ld["n_routes"], "чужой номер цели — вся выборка")
    finally:
        m.targets.items = keep_t5

    print("\n" + LINE)
    if _fails:
        print("НЕ ПРОШЛО: %d — %s" % (len(_fails), "; ".join(_fails)))
        return 1
    print("ВСЁ ПРОШЛО. Показатели отчёта согласованы между собой и с параметрами.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
