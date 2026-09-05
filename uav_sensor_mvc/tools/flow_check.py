# -*- coding: utf-8 -*-
"""
СКВОЗНОЙ ПРОГОН МОДЕЛИ БЕЗ ИНТЕРФЕЙСА: порядок работы, время каждого шага, инварианты.

    python tools/flow_check.py              # полный прогон
    python tools/flow_check.py --quick      # без итераций (быстрее вдвое)

ЧТО ЭТО ЛОВИТ — и почему `reference_run.py` этого не ловит. Тот проверяет ЧИСЛА готового
расчёта: коридоры, датчики, засечку. Этот — ПОРЯДОК и СОСТОЯНИЕ: что считается на каждом
шаге, сколько это стоит и что остаётся в модели после сброса. Именно здесь жили дефекты
04.09.2026, которые числами не ловились вовсе:

* маршруты считались при построении карты, хотя нужны только датчикам и итерациям;
* слои и высоты перечитывались с диска на каждое построение (9.5 с вместо 0.0);
* после снятия района в модели оставались датчики, маршруты и включённый рельеф;
* рельеф при повторном включении ронял программу (растр читался из двух потоков).

ЗАЧЕМ ОТДЕЛЬНЫЙ СКРИПТ. Каждая из этих проверок писалась заново вручную — четыре раза за
один день. Дешевле держать её готовой: прогон занимает полминуты и говорит «да/нет» по
каждому пункту.

⚠️ Скрипт НЕ поднимает Qt: он проверяет модель. Интерфейс проверяется запуском программы.
"""
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Params, THREAT_AREA, THREAT_BBOX_LONLAT, THREAT_CELL_M  # noqa: E402
from model.threat_grid import ThreatModel                                   # noqa: E402
from model import geo_frame as gf                                           # noqa: E402

LINE = "=" * 68
_fails = []


def check(ok, what, detail=""):
    """Записать результат проверки; возвращает ok, чтобы можно было ветвиться."""
    print("   %s %-46s %s" % ("[ ok ]" if ok else "[ НЕТ]", what, detail))
    if not ok:
        _fails.append(what)
    return ok


def step(fn, *a, **kw):
    """Выполнить шаг и вернуть (результат, секунды)."""
    t = time.time()
    res = fn(*a, **kw)
    return res, time.time() - t


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    quick = "--quick" in argv

    p = Params()
    m = ThreatModel(p)
    cell = THREAT_CELL_M / 1000.0
    print(LINE)
    print("СКВОЗНОЙ ПРОГОН МОДЕЛИ · участок %s" % THREAT_AREA)
    print("область показа: %s" % (THREAT_BBOX_LONLAT,))
    print("район при старте: %s (%.1f x %.1f км)"
          % ("задан" if m.area_ready else "НЕ задан", *m.area_size_km()))
    print(LINE)

    if not m.area_ready:                      # район не задан — зададим середину области
        lo, la, ho, ha = m.bbox_lonlat
        dx, dy = (ho - lo) * 0.3, (ha - la) * 0.3
        m.set_area((lo + dx, la + dy, ho - dx, ha - dy))
        print("район задан для проверки: %.1f x %.1f км\n" % m.area_size_km())

    # ---- 1. рамка района кратна ячейке ----
    print("1. РАЙОН")
    w, h = m.area_size_km()
    check(abs(w / cell - round(w / cell)) < 1e-6 and abs(h / cell - round(h / cell)) < 1e-6,
          "стороны кратны ячейке %g км" % cell, "%.0f x %.0f ячеек" % (w / cell, h / cell))
    ax0, ax1, ay0, ay1 = gf.bbox_lonlat_to_km(THREAT_BBOX_LONLAT, m.lon0, m.lat0)
    bx0, bx1, by0, by1 = m.bbox_km
    check(bx0 >= ax0 - 1e-6 and bx1 <= ax1 + 1e-6 and by0 >= ay0 - 1e-6 and by1 <= ay1 + 1e-6,
          "район лежит внутри области показа",
          "x %.1f..%.1f y %.1f..%.1f" % m.bbox_km)

    # ---- 2. построение карты ----
    print("\n2. ПОСТРОЕНИЕ КАРТЫ")
    _, t_build = step(m.build)
    check(m.grid is not None, "карта построена", "%.1f с, сетка %dx%d"
          % (t_build, m.grid.nx, m.grid.ny) if m.grid else "%.1f с" % t_build)
    check(len(m.routes) == 0 and len(m.iter_routes) == 0,
          "маршруты при построении НЕ считаются",
          "их %d" % (len(m.routes) + len(m.iter_routes)))
    _, t_rebuild = step(m.build)
    check(t_rebuild < max(1.0, t_build * 0.25), "повторное построение из кэша",
          "%.1f с против %.1f с" % (t_rebuild, t_build))

    # ---- 3. датчики ----
    print("\n3. ДАТЧИКИ")
    if not m.iter_routes and not m.routes:
        _, t_routes = step(m.plan_routes)
    else:
        t_routes = 0.0
    _, t_place = step(m.place_sensors)
    check(len(m.routes) > 0, "маршруты посчитаны при расстановке",
          "%d за %.1f с" % (len(m.routes), t_routes))
    check(len(m.sensors) == p.threat_N, "малых датчиков сколько заказано",
          "%d из %d, %.1f с" % (len(m.sensors), p.threat_N, t_place))
    check(len(m.sensors_big) == p.threat_N_big, "больших датчиков сколько заказано",
          "%d из %d" % (len(m.sensors_big), p.threat_N_big))
    ll = m.sensors_lonlat()
    check(len(ll) == len(m.sensors) and (len(ll) == 0 or
          (THREAT_BBOX_LONLAT[0] <= ll[:, 0].min() and ll[:, 0].max() <= THREAT_BBOX_LONLAT[2])),
          "координаты датчиков в градусах и в пределах области")

    # ---- 4. итерации ----
    if not quick:
        print("\n4. ИТЕРАЦИИ")
        ok_reset, t_reset = step(m.iter_reset)
        check(bool(ok_reset), "старт моделирования (цель достижима)", "%.1f с" % t_reset)
        _, t_batch = step(m.iter_batch)
        check(len(m.iter_routes) == p.threat_iter_routes, "накоплено маршрутов сколько задано",
              "%d из %d, %.1f с" % (len(m.iter_routes), p.threat_iter_routes, t_batch))

    # ---- 4б. четыре типа датчиков и закреплённые позиции (задача 8.7) ----
    print("\n4б. ТИПЫ ДАТЧИКОВ И ЗАКРЕПЛЁННЫЕ ПОЗИЦИИ")
    from model.sensors import sensor_types, min_small_radius        # noqa: E402
    n1 = p.threat_N
    # ГЛАВНОЕ ТРЕБОВАНИЕ ЗАКАЗЧИКА: закреплённый датчик остаётся ровно там, куда его
    # поставили. Ставим его заведомо НЕ в узел сетки кандидатов — на «некруглые»
    # координаты: привязка к сетке сдвинула бы его на полшага, и проверка это поймает.
    x0, x1, y0, y1 = m.bbox_km
    fx, fy = x0 + 0.37 * (x1 - x0) + 0.137, y0 + 0.41 * (y1 - y0) + 0.211
    m.add_manual_sensor(1, True, fx, fy)
    m.place_sensors()
    d = min(((s[0] - fx) ** 2 + (s[1] - fy) ** 2) ** 0.5 for s in m.sensors) \
        if len(m.sensors) else 9e9
    check(d < 1e-6, "закреплённый датчик стоит ТОЧНО где поставлен",
          "отклонение %.1f м" % (d * 1000.0))
    check(len(m.sensors) == n1, "закреплённый входит в заказанное число",
          "%d из %d" % (len(m.sensors), n1))
    check(int(m.sensors_static.sum()) == 1, "он помечен как закреплённый",
          "помечено %d" % int(m.sensors_static.sum()))
    # пересборка карты и итерации НЕ должны его сдвигать
    m.build(); m.place_sensors()
    d2 = min(((s[0] - fx) ** 2 + (s[1] - fy) ** 2) ** 0.5 for s in m.sensors) \
        if len(m.sensors) else 9e9
    check(d2 < 1e-6, "после пересборки карты он на месте", "отклонение %.1f м" % (d2 * 1000.0))

    # второй тип: число датчиков на карте обязано вырасти ровно на заказанное
    p.threat_N2, p.threat_R2 = 4, 3.0
    check(len(sensor_types(p)) == 4 and len([s for s in sensor_types(p) if s.active]) == 3,
          "работающих типов стало три", "N2 = %d" % p.threat_N2)
    check(abs(min_small_radius(p) - min(p.threat_R, p.threat_R2)) < 1e-9,
          "шаг сетки считается по НАИМЕНЬШЕМУ радиусу", "%g км" % min_small_radius(p))
    m.place_sensors()
    check(len(m.sensors) == n1 + p.threat_N2, "датчиков стало ровно на тип 2 больше",
          "%d = %d + %d" % (len(m.sensors), n1, p.threat_N2))
    got2 = int((m.sensors_type == 2).sum())
    check(got2 == p.threat_N2, "тип каждого датчика проставлен", "типа 2 — %d" % got2)
    d3 = min(((s[0] - fx) ** 2 + (s[1] - fy) ** 2) ** 0.5 for s in m.sensors)
    check(d3 < 1e-6, "закреплённый не сдвинулся и при двух типах",
          "отклонение %.1f м" % (d3 * 1000.0))

    # режим «Задать позиции»: на карте ровно то, что задано, и ничего больше
    m.add_manual_sensor(2, False, fx + 4.0, fy + 4.0)
    m.add_manual_sensor(4, True, fx + 9.0, fy - 6.0)
    p.threat_manual_mode = True
    m.place_sensors()
    check(len(m.sensors) == 2 and len(m.sensors_big) == 1,
          "режим «задать позиции»: только заданные датчики",
          "малых %d, больших %d" % (len(m.sensors), len(m.sensors_big)))
    check(bool(m.sensors_static.all()) and bool(m.sensors_big_static.all()),
          "в этом режиме закреплены все")
    rows = m.sensors_table()
    check(len(rows) == 3 and rows[0]["n"] == 1 and rows[-1]["type_id"] == 4,
          "таблица собрана: номера сквозные, большие последними",
          "строк %d" % len(rows))
    p.threat_manual_mode = False
    p.threat_N2 = 0
    m.clear_manual_sensors()
    m.place_sensors()
    check(len(m.sensors) == n1 and int(m.sensors_static.sum()) == 0,
          "возврат к обычной расстановке", "%d датчиков" % len(m.sensors))

    # ⚠️ ПРАВИЛО ЗАКАЗЧИКА 05.09.2026: в режиме расчёта ЛЮБОЙ поставленный вручную
    # датчик — якорь, даже помеченный «динамическим». Сперва ставятся заданные им, а
    # алгоритм ДОБИРАЕТ недостающие до заказанного числа.
    m.add_manual_sensor(1, False, fx, fy)                    # динамический, не «static»
    m.add_manual_sensor(1, False, fx + 3.7, fy + 2.3)
    m.place_sensors()
    d_a = min(((s[0] - fx) ** 2 + (s[1] - fy) ** 2) ** 0.5 for s in m.sensors)
    d_b = min(((s[0] - fx - 3.7) ** 2 + (s[1] - fy - 2.3) ** 2) ** 0.5
              for s in m.sensors)
    check(d_a < 1e-6 and d_b < 1e-6, "ручной ДИНАМИЧЕСКИЙ тоже стоит где поставлен",
          "отклонения %.1f и %.1f м" % (d_a * 1000, d_b * 1000))
    check(len(m.sensors) == n1, "алгоритм добрал остальные до заказанного числа",
          "%d = 2 заданных + %d подобранных" % (len(m.sensors), len(m.sensors) - 2))
    check(int(m.sensors_static.sum()) == 2, "оба помечены как поставленные человеком",
          "помечено %d" % int(m.sensors_static.sum()))
    m.clear_manual_sensors()

    # ---- 5. рельеф: несколько включений подряд ----
    print("\n5. РЕЛЬЕФ")
    if not m.has_dem():
        print("   файла высот нет — раздел пропущен")
    else:
        ok_all = True
        for i in range(3):
            want = (i % 2 == 0)
            try:
                m.set_relief(want)
                m.build()
                got = (m.grid.relief_k() is not None)
                disp = m.relief_display()
                ok_all &= (got == want) and (disp is not None)
            except Exception as e:                      # noqa: BLE001 — суть проверки
                print("   ПАДЕНИЕ на цикле %d: %s: %s" % (i + 1, type(e).__name__, e))
                ok_all = False
                break
        check(ok_all, "три включения-выключения подряд без сбоя")

    # ---- 6. снятие района обнуляет ВСЁ ----
    print("\n6. СНЯТИЕ РАЙОНА")
    m.clear_area()
    check(m.grid is None, "сетка сброшена")
    check(len(m.layers) == 0, "слои сброшены")
    check(len(m.sensors) == 0 and len(m.sensors_big) == 0, "датчики сброшены")
    check(len(m.routes) == 0 and len(m.iter_routes) == 0, "маршруты сброшены")
    check(m.relief_on is False, "рельеф выключен")
    check(m.area_ready is False, "район помечен как незаданный")

    print("\n" + LINE)
    if _fails:
        print("НЕ ПРОШЛО: %d — %s" % (len(_fails), "; ".join(_fails)))
        return 1
    print("ВСЁ ПРОШЛО. Порядок работы, кэш и сброс — в норме.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
