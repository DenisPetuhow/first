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

import numpy as np

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

    # ---- 4б. четыре типа датчиков и заданные человеком позиции (задача 8.7) ----
    print("\n4б. ТИПЫ ДАТЧИКОВ И ЗАДАННЫЕ ПОЗИЦИИ · фаза ДО ИТЕРАЦИЙ")
    from model.sensors import sensor_types, min_small_radius        # noqa: E402
    n1 = p.threat_N
    # ⚠️ ФАЗА ЗДЕСЬ РЕШАЕТ ВСЁ (правило заказчика 05.09.2026). ДО итераций закреплены
    # ВСЕ заданные человеком датчики — и статические, и динамические: человек описывает
    # обстановку, алгоритм доставляет недостающие. Различие режимов проявляется ПРИ
    # итерациях, и оно проверяется ниже, отдельным блоком. Поэтому накопленную выборку
    # сбрасываем: раздел 4 её уже проверил, а здесь она перевела бы модель в другую фазу.
    m.iter_reset()
    # Позицию берём заведомо НЕ в узле сетки кандидатов — «некруглые» координаты:
    # привязка к сетке сдвинула бы датчик на полшага, и проверка это поймает.
    x0, x1, y0, y1 = m.bbox_km
    fx, fy = x0 + 0.37 * (x1 - x0) + 0.137, y0 + 0.41 * (y1 - y0) + 0.211
    m.add_manual_sensor(1, True, fx, fy)
    m.place_sensors()
    check(not m.in_iterations(), "фаза: итерации не идут — закреплены все заданные")
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
    # ⚠️ РЕЖИМ КАЖДОГО — ИЗ ЕГО ЗАПИСИ, а не «в ручном режиме закреплены все». Стоят
    # здесь и правда все заданные (подбора нет вовсе), но это про МЕСТО, а не про режим:
    # тип 1 добавлен статическим, тип 2 — динамическим, и таблица обязана это показать.
    check(list(m.sensors_static) == [True, False] and bool(m.sensors_big_static.all()),
          "режим взят из записи, а не «всем статический»",
          "малые %s, большие %s" % (list(map(bool, m.sensors_static)),
                                    list(map(bool, m.sensors_big_static))))
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

    # ДО ИТЕРАЦИЙ ДИНАМИЧЕСКИЙ ТОЖЕ СТОИТ ТАМ, ГДЕ ЗАДАН (правило заказчика 05.09.2026):
    # «поставленные датчики не перемещаются, а остальные доставляются». Различие режимов
    # начинается только с моделирования — проверка этого сразу следом.
    m.add_manual_sensor(1, False, fx, fy)                    # динамический, не «static»
    m.add_manual_sensor(1, False, fx + 3.7, fy + 2.3)
    m.place_sensors()
    d_a = min(((s[0] - fx) ** 2 + (s[1] - fy) ** 2) ** 0.5 for s in m.sensors)
    d_b = min(((s[0] - fx - 3.7) ** 2 + (s[1] - fy - 2.3) ** 2) ** 0.5
              for s in m.sensors)
    check(d_a < 1e-6 and d_b < 1e-6, "до итераций ДИНАМИЧЕСКИЙ стоит где поставлен",
          "отклонения %.1f и %.1f м" % (d_a * 1000, d_b * 1000))
    check(len(m.sensors) == n1, "алгоритм добрал остальные до заказанного числа",
          "%d = 2 заданных + %d доставленных" % (len(m.sensors), len(m.sensors) - 2))
    check(int(m.sensors_manual.sum()) == 2, "оба помечены как заданные человеком",
          "помечено %d" % int(m.sensors_manual.sum()))
    check(int(m.sensors_static.sum()) == 0, "но статическими НЕ помечены — режим другой",
          "статических %d" % int(m.sensors_static.sum()))

    # ---- 4в. фаза МОДЕЛИРОВАНИЯ: статический стоит, динамический перемещается ----
    print("\n4в. ПРИ ИТЕРАЦИЯХ: статический стоит, динамический перемещается")
    m.add_manual_sensor(1, True, fx + 7.3, fy - 5.1)         # к двум динамическим
    keep_iters = p.threat_iter_routes
    p.threat_iter_routes = 40            # для проверки хватает: важна ФАЗА, а не объём
    m.iter_reset()
    m.iter_batch()
    p.threat_iter_routes = keep_iters
    m.place_sensors()
    check(m.in_iterations(), "фаза: идут итерации", "маршрутов %d" % len(m.iter_routes))
    d_s = min(((s[0] - fx - 7.3) ** 2 + (s[1] - fy + 5.1) ** 2) ** 0.5 for s in m.sensors)
    check(d_s < 1e-6, "СТАТИЧЕСКИЙ остался ровно на месте",
          "отклонение %.1f м" % (d_s * 1000.0))
    d_a2 = min(((s[0] - fx) ** 2 + (s[1] - fy) ** 2) ** 0.5 for s in m.sensors)
    d_b2 = min(((s[0] - fx - 3.7) ** 2 + (s[1] - fy - 2.3) ** 2) ** 0.5
               for s in m.sensors)
    # Координаты заведомо НЕ узел сетки кандидатов: подобранный датчик не может встать
    # в них случайно, поэтому «не на месте» — надёжный признак, что подбор был.
    check(d_a2 > 1e-6 and d_b2 > 1e-6, "ДИНАМИЧЕСКИЕ перемещены алгоритмом",
          "ушли на %.0f и %.0f м" % (d_a2 * 1000, d_b2 * 1000))
    check(int(m.sensors_static.sum()) == 1 and len(m.sensors) == n1,
          "статический ровно один, число датчиков не выросло",
          "статических %d, всего %d" % (int(m.sensors_static.sum()), len(m.sensors)))
    check(int(m.sensors_manual.sum()) == 1,
          "«задано человеком» осталось только у статического",
          "помечено %d" % int(m.sensors_manual.sum()))

    # ---- 4г. режим «Задать позиции» ВО ВРЕМЯ МОДЕЛИРОВАНИЯ ----
    # ⚠️ Заказчик 05.09.2026 (по снимкам экрана): «не работает режим, когда мы сами
    # задаём датчики после начала моделирования — алгоритм не работает и они не
    # перераспределяются». Раньше `_place_manual` не смотрел на фазу вовсе: и до
    # итераций, и после 150 итераций датчики стояли ровно там, где их поставили.
    print("\n4г. «ЗАДАТЬ ПОЗИЦИИ» ПРИ ИТЕРАЦИЯХ: динамические перераспределяются")
    m.clear_manual_sensors()
    m.add_manual_sensor(1, True, fx, fy)                     # статический
    m.add_manual_sensor(1, False, fx + 6.3, fy + 4.7)        # динамические
    m.add_manual_sensor(1, False, fx - 5.9, fy + 3.1)
    p.threat_manual_mode = True
    m.place_sensors()
    check(len(m.sensors) == 3, "число датчиков берётся из таблицы, а не из поля N",
          "%d из 3 заданных (поле N = %d)" % (len(m.sensors), p.threat_N))
    d_fix = min(((s[0] - fx) ** 2 + (s[1] - fy) ** 2) ** 0.5 for s in m.sensors)
    check(d_fix < 1e-6, "статический стоит на месте и в этом режиме",
          "отклонение %.1f м" % (d_fix * 1000.0))
    d_dyn = min(((s[0] - fx - 6.3) ** 2 + (s[1] - fy - 4.7) ** 2) ** 0.5
                for s in m.sensors)
    check(d_dyn > 1e-6, "ДИНАМИЧЕСКИЙ перераспределён алгоритмом",
          "ушёл на %.0f м" % (d_dyn * 1000.0))
    p.threat_manual_mode = False
    m.clear_manual_sensors()

    # ---- 4д. РАСПРЕДЕЛЕНИЕ МЕЖДУ ТИПАМИ: разнос и кратность ----
    # ⚠️ Заказчик 05.09.2026 (по снимкам): «распределение работает внутри группы, но не
    # между — и они слипаются». Разнос между типами был 0.5·(R₁+R₂) против 1.6·R внутри
    # типа: при R = 1 и 3 км это 2 км против 4.8. Теперь правило общее.
    print("\n4д. РАСПРЕДЕЛЕНИЕ МЕЖДУ ТИПАМИ (разнос и кратность)")
    from config import THREAT_SPREAD_PAIR                        # noqa: E402
    p.threat_N, p.threat_R = 6, 1.0
    p.threat_N2, p.threat_R2 = 6, 3.0
    p.threat_N3 = 0
    p.threat_spread_types = True
    m.place_sensors()
    S, T = np.asarray(m.sensors, float), np.asarray(m.sensors_type, int)
    want_sep = THREAT_SPREAD_PAIR * (p.threat_R + p.threat_R2)
    pairs = [(i, j) for i in range(len(S)) for j in range(len(S))
             if i < j and T[i] != T[j]]
    d_min = min((float(np.hypot(*(S[i] - S[j]))) for i, j in pairs), default=9e9)
    check(d_min >= want_sep - 1e-6, "датчики РАЗНЫХ типов не ближе %.1f км" % want_sep,
          "ближайшая пара %.2f км" % d_min)
    p.threat_spread_types = False
    m.place_sensors()
    S2, T2 = np.asarray(m.sensors, float), np.asarray(m.sensors_type, int)
    pairs2 = [(i, j) for i in range(len(S2)) for j in range(len(S2))
              if i < j and T2[i] != T2[j]]
    d_min2 = min((float(np.hypot(*(S2[i] - S2[j]))) for i, j in pairs2), default=9e9)
    # ⚠️ ПРОВЕРЯЕМ НЕ «СТАЛО БЛИЖЕ», А «НЕ СТАЛО ДАЛЬШЕ». Выключение ограничения не
    # обязано сближать датчики: жадный отбор и сам разносит их по разным коридорам, и
    # на этом участке пары разных типов стоят в 9.2 км при пороге 3.2 — правило просто
    # не было ограничивающим. А вот вырасти разнос от СНЯТИЯ ограничения не может: если
    # вырос — логика галочки перевёрнута.
    check(d_min2 <= d_min + 1e-6 and len(S2) == len(S),
          "галочка выключена — ограничение снято, датчиков столько же",
          "ближайшая пара %.2f км против %.2f, датчиков %d"
          % (d_min2, d_min, len(S2)))
    p.threat_spread_types = True
    # КРАТНОСТЬ МЕЖДУ ТИПАМИ: кэш второго типа обязан видеть покрытие первого
    from model.optimization import CoverageCache                 # noqa: E402
    sample = m._sample_for_sensors()[:20]
    c1 = CoverageCache(S[:1], p.threat_R2, p.L_seg, 3)
    c2 = CoverageCache(S[:1], p.threat_R2, p.L_seg, 3,
                       placed=[(float(S[i, 0]), float(S[i, 1]), float(p.threat_R))
                               for i in range(len(S))])
    for r in sample:
        c1.add_trajectory(r); c2.add_trajectory(r)
    check(sum(c1._pre_n) == 0 and sum(c2._pre_n) > 0,
          "кэш видит покрытие датчиков ДРУГИХ типов",
          "засечек чужими: %d против %d" % (sum(c2._pre_n), sum(c1._pre_n)))
    p.threat_N, p.threat_R, p.threat_N2 = n1, 2.0, 0
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
