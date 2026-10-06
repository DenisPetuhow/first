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
    # ⚠️ СПРАВОЧНИК ТИПОВ — ВСТРОЕННЫЙ, а не файл участка: заказчик правит свой (24.09.2026 — кратность защиты 2,
    # k ПВН 3), и проверка считала бы его числами
    import tempfile                                                # noqa: E402
    from model import sensor_types as _cat0                        # noqa: E402
    _empty = tempfile.mkdtemp()                                    # пустая папка — файла справочника нет
    _cat0.path = lambda: os.path.join(_empty, _cat0.FILE_NAME)

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
    # ⚠️ считаются РАБОТАЮЩИЕ: в справочнике ещё «Обычный» №5 (Д1) с N 0 — он в расчёт не идёт
    check(len([s for s in sensor_types(p) if s.active]) == 3,
          "работающих типов стало три", "N2 = %d" % p.threat_N2)
    # ⚠️ во встроенном справочнике МОГ и ПВН оба с картой места (план 13.3): общей сеткой не ставится никто — шаг по R типа 1
    check(abs(min_small_radius(p) - p.threat_R) < 1e-9,
          "шаг общей сетки — по типам «везде»; МОГ и ПВН со своей картой его не дробят", "%g км" % min_small_radius(p))
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
    # ⚠️ СВОЯ КРАТНОСТЬ — СВОИМИ ДАТЧИКАМИ (заказчик 23.09.2026, В13.13): галка «одна на
    # всех» снята и разнос выключен — тип 2 обязан встать одинаково при 6 и при 3 датчиках типа 1
    k_old = (p.threat_k, p.threat_k2)                            # вернуть после опыта
    p.threat_k_same, p.threat_spread_types = False, False
    p.threat_k, p.threat_k2 = 3, 2
    pos2 = []                                                    # позиции типа 2 в двух расстановках
    for n_first in (6, 3):
        p.threat_N = n_first
        m.place_sensors()
        pos2.append(np.asarray(m.sensors, float)[np.asarray(m.sensors_type, int) == 2])
    same_pos = (pos2[0].shape == pos2[1].shape and len(pos2[0]) > 0   # совпали ли позиции типа 2
                and np.allclose(pos2[0], pos2[1]))
    check(same_pos, "своя кратность: тип 2 не зависит от датчиков типа 1",
          "типа 2: %d и %d датчиков" % (len(pos2[0]), len(pos2[1])))
    me_own = m.metrics()                                         # отчёт при своей кратности
    by_own = me_own.get("by_type", {})                           # засечка по типам
    any_own = max((v["detect_k_frac"] for v in by_own.values()), default=0.0)   # лучший из типов, доля
    check(me_own["detect_k_frac"] >= any_own - 1e-9,
          "«засечено своей кратностью» не меньше лучшего из типов",
          "%.3f против %.3f" % (me_own["detect_k_frac"], any_own))
    p.threat_k_same, p.threat_spread_types = True, True
    p.threat_k, p.threat_k2 = k_old
    p.threat_N, p.threat_R, p.threat_N2 = n1, 2.0, 0
    m.clear_manual_sensors()

    # ---- 4е. справочник типов датчиков (план 13, задача 13.1) ----
    print("\n4е. СПРАВОЧНИК ТИПОВ ДАТЧИКОВ (план 13, 13.1)")
    from model import sensor_types as st                         # noqa: E402
    base = st.defaults()                                         # встроенный справочник
    check([(t.type_id, t.name) for t in base] == [(1, "МОГ"), (2, "ПВН"), (4, "Большой"), (5, "Обычный")],
          "умолчания: МОГ №1, ПВН №2, большой №4, Обычный №5 (Д1)",
          ", ".join("%d %s" % (t.type_id, t.name) for t in base))
    pvn = st.by_name("ПВН", base)                                # паспорт ПВН
    check(pvn.zone == "застройка" and pvn.r_km == 0.5 and pvn.k == 4 and pvn.targets,
          "ПВН: застройка, R 0.5 км, k 4, ставится у целей",
          "место %s, полоса %g км" % (pvn.zone, pvn.zone_buf_km))
    check([st.from_row(st.to_row(t)) for t in base] == base and not any(t.validate() for t in base),
          "паспорт переживает запись в файл и чтение")
    kept = st._parse([st.to_row(base[0]), dict(st.to_row(base[1]), номер=1),   # что осталось из
                      {"название": "без номера"}])                            # записей с изъяном
    check(len(kept) == 1, "повтор номера и запись без номера отброшены", "осталось %d" % len(kept))
    forced = st.from_row(dict(st.to_row(base[0]), номер=4))     # «малый» под номером большого
    check(isinstance(forced, st.Big) and st.by_id(4, base).SPEC_KIND == "big",
          "номер 4 — всегда большой: за ним поля threat_N_big / threat_R_big")
    check(st.next_id(base) == 6, "новый тип — первый свободный номер ≥ 5 (№5 — «Обычный»), а не свободный 3",
          "номер %d" % st.next_id(base))
    specs = sensor_types(p, base)                                # задание по встроенному справочнику
    check([s.name for s in specs] == ["МОГ", "ПВН", "Тип 3", "Большой", "Обычный"]
          and specs[0].r_km == p.threat_R and specs[3].r_km == p.threat_R_big and not specs[4].active,
          "номера 1–4: название из паспорта, числа из полей Params",
          " · ".join("%s R %g" % (s.name, s.r_km) for s in specs))
    # тип №5: R и k — из паспорта, N — из задания; ставится наравне с прочими малыми
    extra = [t for t in base if t.type_id != 5] + [st.Small(type_id=5, name="Проба", r_km=1.5, k=2, color="sensor_t3")]   # №5 — проба
    real_load = st.load                                          # вернуть после опыта
    st.load = lambda: list(extra)                                # справочник без записи на диск
    p.threat_N_types = {"5": 3}                                  # ключ строкой — как из настроек
    try:
        spec5 = [s for s in sensor_types(p) if s.type_id == 5]   # тип №5 в задании
        check(len(spec5) == 1 and spec5[0].n == 3 and spec5[0].r_km == 1.5,
              "тип №5: R из паспорта, N из задания",
              "N %d, R %g км" % (spec5[0].n, spec5[0].r_km) if spec5 else "нет в задании")
        m.place_sensors()
        got5 = int((np.asarray(m.sensors_type, int) == 5).sum())   # поставлено типа №5
        check(got5 == 3 and len(m.sensors) == n1 + 3, "тип №5 расставлен наравне с прочими",
              "типа №5 — %d, всего %d" % (got5, len(m.sensors)))
        m.add_manual_sensor(5, True, fx, fy)
        check(m.manual_sensors[-1].type_id == 5, "датчик типа №5 можно задать руками")
    finally:
        st.load = real_load
        p.threat_N_types = {}
        m.clear_manual_sensors()
    m.place_sensors()
    check(len(m.sensors) == n1, "без типа №5 — прежнее число датчиков", "%d" % len(m.sensors))

    # ---- 4ж. места датчиков: постройки, лес, неудобья (план 13, задача 13.17) ----
    print("\n4ж. МЕСТА ДАТЧИКОВ: ПОСТРОЙКИ, ЛЕС, НЕУДОБЬЯ (план 13, 13.17)")
    from model import sensor_zones as sz                         # noqa: E402
    check(sz.file_name("threat_layers_arh.npz") == "sensor_layers_arh.npz",
          "файл мест датчиков лежит рядом с кэшем слоёв", sz.file_name("threat_layers_arh.npz"))
    w_before = m.grid.weight.copy()                              # вес до сводки
    zc = m.sensor_cells()                                        # сводка по ячейкам
    check(np.array_equal(w_before, m.grid.weight), "сводка мест датчиков вес карты не трогает")
    check(m.sensor_cells() is zc, "сводка считается раз на сетку")
    # ранг «кто выше»: этажность → тип → площадь (В13.19). Строки: x, y, этажей, класс, м²
    probe = np.array([[0, 0, np.nan, 1, 5000], [0, 0, 2, 0, 50],             # «не ясно» 5000 м²; дом в 2 этажа
                      [0, 0, np.nan, 2, 100], [0, 0, np.nan, 0, 900]], float)  # многоквартирный; гараж 900 м²
    check(list(np.argsort(-sz.building_rank(probe))) == [2, 1, 0, 3],
          "кто выше: этажность → тип → площадь", "многоквартирный, 2 эт., большое «не ясно», гараж")
    if not sz.load():                                            # участок без файла мест датчиков
        print("   файла %s нет — числа по участку пропущены" % os.path.basename(sz.layers_path()))
    else:
        cnt = zc["count"]                                        # построек в ячейке
        iy, ix = np.nonzero(cnt > 0)                             # ячейки с постройками: строки, столбцы
        xy = zc["top_xy"][iy, ix]                                # самые высокие постройки, км
        own = ((xy[:, 0] >= m.grid.ox + ix * m.grid.h)             # постройка в своей ячейке & (xy[:, 0] < m.grid.ox + (ix + 1) * m.grid.h)
               & (xy[:, 1] >= m.grid.oy + iy * m.grid.h) & (xy[:, 1] < m.grid.oy + (iy + 1) * m.grid.h))
        check(len(own) > 0 and bool(own.all()), "самая высокая постройка лежит в своей ячейке",
              "%d построек в %d ячейках" % (int(cnt.sum()), len(own)))
        check(0.0 < zc["forest"].mean() < 1.0 and not np.any(zc["top_floors"][cnt == 0] > 0),
              "лес найден, у пустых ячеек высоты нет",
              "лес %.1f %%, неудобья %.1f %% района" % (100 * zc["forest"].mean(), 100 * zc["rough"].mean()))

    # ---- 4з. карта места типа: ПВН только в застройке (план 13, задача 13.4) ----
    print("\n4з. КАРТА МЕСТА ТИПА: ПВН ТОЛЬКО В ЗАСТРОЙКЕ (план 13, 13.4)")
    specs = {s.type_id: s for s in sensor_types(p)}              # типы задания по номеру
    check(m.allowed_mask(specs[1]) is not None and m.allowed_mask(specs[5]) is None,
          "МОГ — доезд по дорогам (план 13.3), тип «везде» — прежний расчёт, карты нет")
    zmap = m.allowed_mask(specs[2])                              # карта места ПВН
    if zmap is None or not zmap.any():                           # без построек карта пуста
        print("   карта ПВН пуста (нет файла мест датчиков?) — проверки расстановки пропущены")
    else:
        check(m.allowed_mask(specs[2]) is zmap, "карта считается раз на сетку",
              "%.1f км²" % (zmap.sum() * m.grid.h ** 2))
        # Д3 (план 13.1): слагаемые по отдельности — итог обязан быть той же картой; болота — свой слой
        parts = m.allowed_parts(specs[2])                        # слагаемые карты места ПВН
        # ⚠️ в непроходимом места есть, но только в R от проходимого: с крыши у края города видно маршрут (29.09.2026)
        from model.threat_grid import _dilate as _dl4z           # noqa: E402
        _nr = max(0, int(round(float(specs[2].r_km) / m.grid.h)))   # R в ячейках
        _edge = _dl4z(~parts["nopass"], _nr) if _nr else ~parts["nopass"]   # проходимое ± R
        check(np.array_equal(parts["result"], zmap) and not (parts["result"] & ~_edge).any(),
              "слагаемые карты места: итог — та же карта, в непроходимом — только в R от проходимого",
              "болота %d, неудобья %d, у края запрета %d ячеек" % (int(parts["wetland"].sum()), int(parts["rough"].sum()),
                                                                  int((parts["result"] & parts["nopass"]).sum())))
        # ⚠️ одиночный ПВН при N1 = 0 раньше шёл путём «один тип» и вставал НОЛЬ раз
        keep_n = (p.threat_N, p.threat_N2, p.threat_R2)          # вернуть после опыта
        p.threat_N, p.threat_N2, p.threat_R2 = 0, 6, 0.5
        m.place_sensors()
        on_map = sz.cells_of(m.grid, m.sensors, zmap)            # стоит ли ПВН на своей карте
        check(len(m.sensors) == 6 and set(m.sensors_type.tolist()) == {2},
              "одиночный ПВН ставится, сколько заказано", "%d из 6" % len(m.sensors))
        check(len(on_map) > 0 and bool(on_map.all()), "все ПВН — на карте места",
              "%d из %d" % (int(on_map.sum()), len(on_map)))
        places = m.metrics().get("zone_places", {}).get(2)       # (разрешено, кандидатов)
        check(bool(places) and places[0] > 0, "число мест ПВН видно в отчёте",
              "%s из %s" % places if places else "нет")
        # ---- 4и. своя сетка типа (13.5): на крыше самой высокой постройки либо в центре ячейки ----
        print("\n4и. СВОЯ СЕТКА ТИПА: ПВН НА КРЫШЕ ИЛИ В ЦЕНТРЕ ЯЧЕЙКИ (план 13, 13.5)")
        g, zc = m.grid, m.sensor_cells()                         # сетка и постройки по ячейкам
        S = np.asarray(m.sensors, float)                         # позиции ПВН, км
        ix = np.floor((S[:, 0] - g.ox) / g.h).astype(int)        # их ячейки: столбцы
        iy = np.floor((S[:, 1] - g.oy) / g.h).astype(int)        # и строки
        has_b = zc["count"][iy, ix] > 0                          # в ячейке есть постройки
        at_top = np.hypot(*(zc["top_xy"][iy, ix] - S).T) < 1e-6  # стоит на самой высокой
        at_mid = np.hypot(S[:, 0] - (g.ox + (ix + 0.5) * g.h),                # стоит в центре
                          S[:, 1] - (g.oy + (iy + 0.5) * g.h)) < 1e-6         # своей ячейки
        check(bool(np.where(has_b, at_top, at_mid).all()),
              "ПВН — на самой высокой постройке ячейки или в её центре",
              "на постройках %d из %d" % (int((has_b & at_top).sum()), len(S)))
        p.threat_N, p.threat_N2 = n1, 6                           # МОГ R 2 км рядом с ПВН 0.5 км
        check(abs(min_small_radius(p) - p.threat_R) < 1e-9, "ПВН со своей сеткой общий шаг не дробит",
              "шаг по R = %g км" % min_small_radius(p))
        p.threat_N, p.threat_N2, p.threat_R2 = keep_n
        m.place_sensors()

    # ---- 4к. оценка зазора до оптимума: граница Минью (план 13, задача 13.8) ----
    print("\n4к. ОЦЕНКА ЗАЗОРА: ГРАНИЦА МИНЬЮ (план 13, 13.8)")
    from itertools import combinations                           # noqa: E402
    from config import MODES                                     # noqa: E402
    from model.optimization import _popcount                     # noqa: E402
    w = MODES["balanced"]                                        # веса критериев жадного
    few = m._sample_for_sensors()[:40]                           # маршрутов в малой задаче
    cand_all = np.asarray(m.candidates, float)                   # общая сетка кандидатов
    big = CoverageCache(cand_all, p.threat_R, p.L_seg, p.threat_k)   # кэш всей сетки — отобрать лучших
    for r in few:                                                # строка кэша на маршрут
        big.add_trajectory(r)
    # каждый 5-й из 60 лучших: 12 самых «густых» подряд насыщают критерий тремя датчиками, и
    # проверка выходит вырожденной (F = OPT = граница); вразнобой граница строго выше — замер 0.54 < 0.63
    top = np.argsort(-big.routes_covered_by_candidate())[0:60:5]   # 12 кандидатов вразнобой по рейтингу
    small = CoverageCache(cand_all[top], p.threat_R, p.L_seg, p.threat_k)   # задача, решаемая перебором
    for r in few:                                                # строка кэша на маршрут
        small.add_trajectory(r)
    small.greedy(3, w)
    b = small.last_bound                                         # F жадного, граница, доля
    Hs = np.asarray(small._hit, np.int64)                        # засечки: маршрут × кандидат
    Ms = np.asarray(small._mask, np.uint32)                      # покрытые сегменты: маршрут × кандидат

    def f_of(idx):
        """F набора кандидатов — тот же основной критерий, что у жадного. Вход: номера. Отдаёт: float."""
        n = Hs[:, list(idx)].sum(axis=1)                         # засечек у каждого маршрута
        seg = np.bitwise_or.reduce(Ms[:, list(idx)], axis=1)     # его покрытые сегменты
        return float((w[0] * np.minimum(n, p.threat_k) / p.threat_k
                      + w[1] * _popcount(seg) / p.L_seg).mean())

    opt = max(f_of(c) for c in combinations(range(len(top)), 3))   # настоящий оптимум — перебором
    check(b["F"] <= opt + 1e-9 and opt <= b["UB"] + 1e-9 and b["UB"] > b["F"] + 1e-6,
          "граница Минью не ниже настоящего оптимума",
          "F %.4f ≤ OPT %.4f ≤ граница %.4f" % (b["F"], opt, b["UB"]))
    gap = m.metrics().get("gap", {})                             # оценка зазора в отчёте
    check(1 in gap and 0.0 < gap[1] <= 1.0 + 1e-9, "оценка зазора видна в отчёте",
          "тип 1: не хуже %.0f %% от лучшего" % (100 * gap.get(1, 0.0)))

    # ---- 4л. общий проход по типам вместо очереди (план 13, задача 13.6) ----
    print("\n4л. ОБЩИЙ ПРОХОД ПО ТИПАМ (план 13, 13.6)")
    from model.joint_greedy import RULES                         # noqa: E402
    check(p.threat_type_order == "gain" and "types" in RULES, "по умолчанию — по наибольшему приросту",
          "правила: %s" % ", ".join(RULES))
    keep_j = (p.threat_N, p.threat_R, p.threat_N2, p.threat_R2,   # вернуть после опыта
              p.threat_k_same, p.threat_spread_types, p.threat_type_order)
    p.threat_N, p.threat_R, p.threat_N2, p.threat_R2 = 6, 1.0, 6, 0.5
    # ⚠️ без разноса и при своей k типы друг другу не мешают — очерёдность не решает НИЧЕГО (замер §13.6.5)
    p.threat_k_same, p.threat_spread_types = False, False
    runs = {}                                                    # правило -> (позиции, типы)
    for rule in RULES:                                           # каждое правило очереди — свой проход
        p.threat_type_order = rule
        m.place_sensors()
        runs[rule] = (np.round(np.asarray(m.sensors, float), 6), np.asarray(m.sensors_type, int).copy())
    same = all(np.array_equal(runs[r][0], runs["gain"][0])   # позиции у всех правил совпали
               and np.array_equal(runs[r][1], runs["gain"][1]) for r in RULES)
    check(same, "без разноса при своей k правила дают одно и то же",
          "%d датчиков, правил %d" % (len(runs["gain"][0]), len(RULES)))
    p.threat_k_same, p.threat_spread_types, p.threat_type_order = True, True, "gain"
    m.place_sensors()
    gap_j = m.metrics().get("gap", {})                           # оценка при общем покрытии
    check(bool(gap_j) and all(0.0 < v <= 1.0 + 1e-9 for v in gap_j.values()),
          "одна на всех: оценка общего прохода в пределах 0…1",
          " · ".join("тип %d: %.0f %%" % (t, 100 * v) for t, v in sorted(gap_j.items())))
    (p.threat_N, p.threat_R, p.threat_N2, p.threat_R2,
     p.threat_k_same, p.threat_spread_types, p.threat_type_order) = keep_j
    m.place_sensors()

    # ---- 4м. ПВН у объектов защиты: этап целей (план 13, задача 13.18) ----
    print("\n4м. ПВН У ОБЪЕКТОВ ЗАЩИТЫ: ЭТАП ЦЕЛЕЙ (план 13, 13.18)")
    from model import target_stage as tsg                        # noqa: E402
    pvn_st = st.by_id(2, st.defaults())                          # паспорт ПВН: правила у целей
    # ДОЛИ ПО ПРИОРИТЕТУ (заказчик 30.09.2026): бюджет λ·p, не больше потребности, не меньше наименьшего; нужно
    # младшей мало — излишек старшей («10 + 3»), 90/5/5 — всё сверх наименьших старшей, а не «3 3 3 → 10 3 3»;
    # не хватает на наименьшее — по k каждой, остаток по приоритету (было 3 / 2 / 0)
    table = [(14, [0.5, 0.5], [14, 3], [3, 3], 2, [11, 3]), (14, [1, 1, 1], [20, 20, 20], [3, 3, 3], 1, [5, 5, 4]),
             (14, [0.9, 0.05, 0.05], [12, 8, 8], [3, 3, 3], 2, [8, 3, 3]),
             (14, [0.5, 0.3, 0.2], [14, 7, 5], [3, 3, 3], 2, [7, 4, 3]),
             (5, [0.5, 0.3, 0.2], [4, 2, 2], [3, 2, 2], 1, [3, 1, 1]), (5, [0.5, 0.3, 0.2], [14, 7, 5], [3, 3, 3], 2, [3, 2, 0]),
             (20, [0.5, 0.5], [4, 2], [3, 2], 1, [4, 2])]
    got_tab = [tsg.allot(b, pr, w, lo, kk) for b, pr, w, lo, kk, _o in table]   # доли по правилу
    check(all(g == o for g, (*_x, o) in zip(got_tab, table)),
          "доли целей — по приоритету, до потребности; излишек младших — старшей",
          " · ".join("/".join(map(str, g)) for g in got_tab))
    # ПОТРЕБНОСТЬ: уровень достижим — до него; нет — пока до потолка кривой остаётся ≥ k порогов (при k 3 первые ПВН
    # дают 0 · 0 · 6 %, стоп по шагу обнулял бы потребность)
    nd = [tsg.need([0.49, 0.76, 0.86, 0.96, 0.98], 0.95, 0.01, 1),
          tsg.need([0, .25, .33, .45, .57, .65, .69, .72, .76, .79, .81, .83, .84, .86, .87, .89, .89, .91], 0.95, 0.03, 2),
          tsg.need([0, 0, .06, .07, .17, .25, .30, .37, .40, .44, .48, .50, .53, .55, .58, .59, .60], 0.95, 0.04, 3)]
    check([(n, r) for n, r, _t in nd] == [(4, True), (14, False), (12, False)],
          "потребность: до уровня, иначе до остатка < k порогов", " · ".join("%d%s" % (n, "" if r else "*") for n, r, _t in nd))
    # ПОРОГ И УРОВЕНЬ ОТ КРАТНОСТИ (заказчик 24–25.09.2026): порог 1 · 3 · 4 · 5 %, уровень 99 · 92 · 89.3 · 86.7 %
    import dataclasses                                           # noqa: E402
    thr_lvl = [(tsg.gain_pct(dataclasses.replace(pvn_st, tgt_k=kk)), tsg.level_pct(dataclasses.replace(pvn_st, tgt_k=kk)))  # %
               for kk in (1, 2, 3, 4, 6)]                        # (порог, уровень) по кратности
    own_v = (tsg.gain_pct(dataclasses.replace(pvn_st, tgt_gain_pct=2.5)),   # своё значение сильнее умолчания
             tsg.level_pct(dataclasses.replace(pvn_st, tgt_level_pct=75.0)))
    check([t for t, _l in thr_lvl] == [1, 3, 4, 5, 7] and [round(lv) for _t, lv in thr_lvl] == [99, 92, 89, 87, 81]
          and own_v == (2.5, 75.0), "порог и уровень у цели — от кратности, своё значение сильнее",
          " · ".join("k%d: %g / %.0f %%" % (kk, t, lv) for kk, (t, lv) in zip((1, 2, 3, 4, 6), thr_lvl)))
    # ПРИОРИТЕТ РАСЧЁТОМ: две цели, у каждой свои маршруты и своё место — ставится место весомой цели;
    # цель, набравшая уровень, выбывает (её маршруты больше не весят)
    from model.optimization import CoverageCache                 # noqa: E402
    two = CoverageCache(np.array([[0.05, 0.0], [10.05, 0.0]]), 0.5, 4, 1)   # место у цели А и у цели Б, км
    for x0 in (0.0,) * 4 + (10.0,) * 4:                          # по четыре подлёта к каждой
        two.add_trajectory(np.array([[x0, 0.0], [x0 + 0.1, 0.0]]))
    pa = two.greedy(1, (1.0, 0.0), traj_w=[0.7] * 4 + [0.3] * 4)   # А весомее
    pb = two.greedy(1, (1.0, 0.0), traj_w=[0.3] * 4 + [0.7] * 4)   # Б весомее
    two.greedy(2, (1.0, 0.0), traj_w=[1.0] * 8, groups=[0] * 4 + [1] * 4, goals=[1.0, 1.0], stop_gain=0.01)
    check(pa[0][0] < 5 < pb[0][0] and two.last_done == {0, 1}, "приоритет — вес маршрутов цели; набравшая уровень выбывает",
          "А весомее → x %.2f км, Б весомее → x %.2f км" % (pa[0][0], pb[0][0]))
    # ЧТО ЛОВИТ: число у цели задаёт насыщение подлёта, а не доля N (было 10 при насыщении на 7, §Д9.2);
    # при малом N минимум 3 сильнее потолка (было: 5 ПВН → у цели 2, §Д9.4б); потолок — бюджет целей
    keep_t = (p.threat_N2, p.threat_R2)                          # вернуть после опыта
    got_n = {}                                                   # квота ПВН -> (у цели, доля подлёта, почему)
    for n2 in (20, 5):                                           # квота ПВН: хватает и не хватает
        p.threat_N2, p.threat_R2 = n2, 0.5
        m.place_sensors()
        rep_t = m.metrics().get("targets_stage", {}).get(2, [])  # отчёт этапа по целям
        got_n[n2] = (sum(r["placed"] for r in rep_t), max([r["share"] for r in rep_t] or [0.0]),
                     ",".join(r["why"] for r in rep_t))
        if n2 == 20:                                             # рубеж первой засечки (Д9) — строка на цель
            lines_t = m.metrics().get("targets_line", [])        # медиана и худшие 10 %, км
            check(len(lines_t) == len(m.target_points_km()) and all(0 <= r["p10"] <= r["med"] for r in lines_t),
                  "рубеж обнаружения — по строке на цель, худшие 10 % не дальше медианы",
                  " · ".join("%.1f · %.1f км" % (r["med"], r["p10"]) for r in lines_t))
            # Д10: «Анализ» у цели — доля не ниже этапа (там только ПВН цели), «ещё ПВН» не убывает с уровнем
            pr = m.target_protection(0) or {}                    # защита объекта №1
            need = [v for _lv, v in sorted((pr.get("need") or {}).items())]   # ещё ПВН по уровням
            mono = (all(a is None or (b is None or a <= b) for a, b in zip(need, need[1:]))   # не убывает,
                    and (None not in need or need[need.index(None):] == [None] * (len(need) - need.index(None))))
            check(pr.get("zone") and pr.get("n", 0) > 0 and pr["share"] >= rep_t[0]["share"] - 1e-9 and mono,
                  "Д10: защита объекта согласована с этапом, «сколько добавить» растёт с уровнем",
                  "подлёт ≥k %.1f %%, ещё ПВН %s" % (100 * pr.get("share", 0), need))
    put20, share20, why20 = got_n[20]                            # квота 20: стоп по насыщению
    # ⚠️ «мест с подлётом нет» (журнал 349) — тоже насыщение, а не потолок: выборка без зерна изредка кончает места
    # раньше уровня (1 прогон из 3, 29.09.2026)
    check(len(m.target_points_km()) == 1 and 3 <= put20 <= 10
          and why20.split()[0] in ("уровень", "мест", "насыщение:", "доля") and share20 >= 0.9,
          "одна цель, 20 ПВН: у цели по насыщению, не больше потолка 10",
          "у цели %d из 20, подлёт %.1f %%, стоп: %s" % (put20, 100 * share20, why20))
    check(got_n[5][0] == 3, "5 ПВН: минимум 3 у цели сильнее потолка (50 % × 5 = 2)",
          "у цели %d из 5, стоп: %s" % (got_n[5][0], got_n[5][2]))
    p.threat_N2, p.threat_R2 = keep_t
    m.place_sensors()

    # ---- 4н. бонус места: высота крыши и размер пункта (план 13, задача 13.7) ----
    print("\n4н. БОНУС МЕСТА: ВЫСОТА КРЫШИ И РАЗМЕР ПУНКТА (план 13, 13.7)")
    mog_st = st.by_id(1, st.defaults())                          # паспорт МОГ
    check(pvn_st.bonus_height > 0 and mog_st.bonus_height == 0 and mog_st.bonus_city == 0,
          "бонус — только у ПВН, МОГ считается как прежде",
          "ПВН: высоте %g, городу %g" % (pvn_st.bonus_height, pvn_st.bonus_city))
    plain = small.greedy(1, w)                                   # первый выбор без бонуса
    c0 = int(np.argmin(np.hypot(*(small.cand - plain[0]).T)))    # его номер
    rank = np.argsort(-small.routes_covered_by_candidate())      # кандидаты по числу маршрутов
    j = int(next(i for i in rank if i != c0))                    # другой полезный кандидат
    boost = np.zeros(small.C)                                    # бонус только ему
    boost[j] = 10.0
    pick = small.greedy(1, w, bonus=boost)                       # выбор с бонусом
    same = np.allclose(small.greedy(1, w, bonus=None), plain)    # без бонуса — прежний выбор
    check(np.allclose(pick[0], small.cand[j]) and same, "бонус меняет выбор, без него — прежний",
          "кандидат %d вместо %d" % (j, c0))

    # ---- 4о. район защиты цели в лесу (план 13, 13.18, В13.21) ----
    # ЧТО ЛОВИТ: у точки нет массива построек — зону задаёт человек; пустой район не принимается
    # (ПВН у цели не встал бы), район идёт в этап целей, перенос цели его снимает
    print("\n4о. РАЙОН ЗАЩИТЫ ЦЕЛИ В ЛЕСУ (план 13, 13.18, В13.21)")
    g4 = m.grid                                                  # сетка карты
    ax, ay = g4.ox + 10 * g4.h, g4.oy + 10 * g4.h                # угол ячейки (10, 10), км
    n_rect = int(tsg.area_mask(g4, ("rect", ax, ay, ax + 3 * g4.h, ay + 2 * g4.h)).sum())   # 3 × 2 ячейки
    n_circ = int(tsg.area_mask(g4, ("circle", ax + 0.5 * g4.h, ay + 0.5 * g4.h, 0.49 * g4.h)).sum())   # одна
    check(n_rect == 6 and n_circ == 1, "маска района: прямоугольник 3 × 2 ячейки, круг — одна",
          "%d и %d" % (n_rect, n_circ))
    keep_o = (list(m.targets.items), p.threat_N2, p.threat_R2)   # вернуть после опыта
    p.threat_N2, p.threat_R2 = 6, 0.5
    from model.threat_grid import _dilate, _label8               # noqa: E402
    # ⚠️ разметка массивов — ОДИН раз и из того же, что `target_zone` (`zone_base`, без буфера города и штрафа
    # деревень): перебор точек через неё шёл бы минутами
    lab4, _n4 = _label8(_dilate(tsg.zone_base(g4, m.sensor_cells()), 1))   # массивы застройки
    free = np.argwhere(lab4 == 0)                                # ячейки вне массивов (строка, столбец)
    centers = [(g4.ox + (c + 0.5) * g4.h, g4.oy + (r + 0.5) * g4.h) for r, c in free[len(free) // 2:]]   # км
    forest = next(((x, y) for x, y in centers if m.in_area(x, y)), None)   # точка в лесу внутри района, км
    if forest is None:                                           # точки вне застройки в районе нет
        print("   точки вне застройки в районе нет — раздел пропущен")
    else:
        m.targets.items = []
        m.add_target(*forest)
        check(m.target_needs_area(0) and m.target_table_rows()[0]["zone"] == "укажите район",
              "цель в лесу: зоны нет — просим район", "(%.1f, %.1f) км" % forest)
        why0, _n0 = m.set_target_area(0, ("rect", ax - 0.05, ay - 0.05, ax + 0.05, ay + 0.05))   # ни одной ячейки
        check(bool(why0) and m.targets.items[0].area is None, "пустой район не принят", why0[:48])
        fx, fy = forest                                          # место цели, км
        # ⚠️ в глухом лесу разрешённых мест может не быть и в 12 × 12 км (замер: 0 из 625 ячеек) —
        # район тянется до ближайшей ячейки карты ПВН, чтобы проверять приём, а не местность
        ok_c = np.argwhere(m.allowed_mask(m._targets_type()))    # разрешённые ячейки (строка, столбец)
        near = ok_c[np.argmin(np.hypot(g4.ox + (ok_c[:, 1] + 0.5) * g4.h - fx, g4.oy + (ok_c[:, 0] + 0.5) * g4.h - fy))]   # ближайшая к цели ячейка
        nx_, ny_ = g4.ox + (near[1] + 0.5) * g4.h, g4.oy + (near[0] + 0.5) * g4.h   # её центр, км
        box = ("rect", min(fx, nx_) - 2, min(fy, ny_) - 2, max(fx, nx_) + 2, max(fy, ny_) + 2)   # район, км
        why1, n1 = m.set_target_area(0, box)                     # причина отказа и мест ПВН в районе
        check(not why1 and n1 > 0 and not m.target_needs_area(0)
              and m.target_table_rows()[0]["zone"] == "задана вручную",
              "район до ближайшего места ПВН принят, зона — вручную",
              "мест ПВН %d, район %.1f × %.1f км%s" % (n1, box[3] - box[1], box[4] - box[2],
                                                    " · " + why1 if why1 else ""))
        m.place_sensors()
        rep_o = (m.metrics().get("targets_stage", {}).get(2) or [{}])[0]   # отчёт этапа по цели
        check(bool(rep_o.get("manual")) and rep_o.get("zone") and rep_o.get("capacity", 0) > 0,
              "этап целей берёт район человека", "вместимость %s" % rep_o.get("capacity"))
        m.move_target(0, fx + g4.h, fy)
        check(m.targets.items[0].area is None, "перенос цели снимает её район")
    m.targets.items, p.threat_N2, p.threat_R2 = keep_o
    m.place_sensors()

    # ---- 4п. доезд МОГ по дорогам (план 13.3) ----
    print("\n4п. ДОЕЗД МОГ ПО ДОРОГАМ: ДЕЙКСТРА, РЕКИ И БОЛОТА ± ОТСТУП (план 13.3)")
    from model import road_access as ra                          # noqa: E402

    class _Grid4p:
        """Сетка-макет 5 × 12 ячеек по 0.5 км: только то, что читает `road_access`."""
        ny, nx, h, ox, oy = 5, 12, 0.5, 0.0, 0.0                 # строк, столбцов, ячейка км, начало км
        _present = {"road_local": np.zeros((5, 12), bool), "river": np.zeros((5, 12), bool)}   # дорога и река

    g4p = _Grid4p()                                              # сетка-макет
    g4p._present["road_local"][2, :8] = True                     # дорога по строке 2, разрыв в столбце 8
    g4p._present["road_local"][2, 9:] = True                     # — кусок за разрывом
    g4p._present["road_local"][3, 1] = g4p._present["road_local"][4, 2] = True   # съезд вниз по диагонали
    g4p._present["river"][:, 3] = True                           # река поперёк, дорога идёт по мосту
    d4p = ra.road_distance(ra.road_cells(g4p), [(2, 0)], 0.5)    # км по дорогам от начала дороги
    check(d4p[2, 7] == 3.5 and np.isinf(d4p[2, 9]) and abs(d4p[4, 2] - 2 ** 0.5) < 1e-12,
          "Дейкстра: 7 ячеек — 3.5 км, за разрывом не доехать, две диагонали — 2·0.5·√2 км",
          "%.2f · %s · %.4f км" % (d4p[2, 7], d4p[2, 9], d4p[4, 2]))
    st4p = st.Small(type_id=1, name="МОГ", zone="дорога", wet_buf_km=0.5)   # паспорт МОГ
    pa4p = ra.access_parts(g4p, {}, st4p, np.ones((5, 12), bool), 0.0, [(0.25, 1.25)])   # слагаемые, цель у начала
    check(pa4p["reach"][2, 3] and pa4p["result"][2, 5] and not pa4p["result"][2, 3:5].any()
          and not pa4p["reach"][2, 10],
          "мост проезжаем, стоять у реки ± 0.5 км нельзя, за разрывом дороги мест нет",
          "мест %d ячеек" % int(pa4p["result"].sum()))
    lim4p = ra.access_parts(g4p, {}, st.Small(zone="дорога", drive_km=1.0), np.ones((5, 12), bool), 0.0,
                            [(0.25, 1.25)])                      # то же с пределом выезда 1 км
    check(int(lim4p["reach"].sum()) == 4, "дальность по дорогам 1 км — доезжает только ближнее",
          "%d ячеек" % int(lim4p["reach"].sum()))
    n4p = ra.place_nodes(g4p, 1.0, pa4p["result"])               # места блоками 2 × 2 ячейки
    check(len(n4p) > 0 and bool(sz.cells_of(g4p, n4p, pa4p["result"]).all()),
          "места блоками: каждое — в разрешённой ячейке", "%d мест" % len(n4p))
    mog4p = m.allowed_parts(specs[1])                            # слагаемые карты МОГ на участке
    if mog4p is None or not mog4p["result"].any():               # дорог нет — проверять нечего
        print("   карта МОГ пуста — проверки на участке пропущены")
    else:
        c4p = m.type_candidates(specs[1], mog4p["result"])       # места МОГ, км
        ic = np.floor((c4p - [m.grid.ox, m.grid.oy]) / m.grid.h).astype(int)   # их ячейки: столбец, строка
        check(not mog4p["ban"][ic[:, 1], ic[:, 0]].any() and mog4p["reach"][ic[:, 1], ic[:, 0]].all(),
              "места МОГ: все доезжают, ни одного у рек и болот ± отступ",
              "мест %d, доезжает %.0f %% дорог" % (len(c4p), 100 * mog4p["reach"].sum() / mog4p["roads"].sum()))
        m.place_sensors()
        s4p = np.asarray(m.sensors, float)[np.asarray(m.sensors_type, int) == 1]   # поставленные МОГ, км
        is4p = np.floor((s4p - [m.grid.ox, m.grid.oy]) / m.grid.h).astype(int)     # их ячейки
        check(len(s4p) > 0 and mog4p["result"][is4p[:, 1], is4p[:, 0]].all(),
              "расстановка: каждый МОГ — на разрешённой карте", "МОГ %d" % len(s4p))

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

    # ---- 5а. точки входа и круглый район (план 11, 11.6) ----
    # ЧТО ЛОВИТ: точки старта человека — входные данные, как его датчики, и обязаны пережить
    # пересборку, смену цели и снятие сектора; а ключ контекста выборки обязан их видеть,
    # иначе маршруты строились бы из прежних точек молча.
    print("\n5а. ТОЧКИ ВХОДА И КРУГЛЫЙ РАЙОН (план 11, 11.6)")
    B5 = np.asarray(m.target_only_km(), float)
    m.set_sector(B5[0] + 200.0, B5[1] - 200.0)
    n_sec = len(m.sector_points())
    key0 = m._ctx_key()
    x0_, x1_, y0_, y1_ = m.bbox_km
    added = m.add_manual_entry(0.5 * (x0_ + x1_) + 3.0, 0.5 * (y0_ + y1_) + 3.0)
    check(added and len(m.entry_points) == n_sec + 1, "точка старта человека добавилась к сектору",
          "сектора %d + ручных %d" % (n_sec, len(m.manual_entries)))
    check(m._ctx_key() != key0, "ключ контекста выборки видит новую точку")
    m.build()
    check(len(m.manual_entries) == 1, "пересборка карты точку старта сохраняет")
    m.set_target(float(B5[0]), float(B5[1]))
    check(len(m.manual_entries) == 1 and m.sector_kind == "click",
          "смена цели: сектор переотобран, точка человека на месте")
    m.clear_sector()
    check(m.entry_points == m.manual_entries, "«Убрать сектор» оставляет точки человека")
    m.set_area_circle(float(B5[0]), float(B5[1]), 25.0)
    check(not m.manual_entries and m.sector_kind is None and m.area_circle is not None,
          "круглый район сбрасывает сектор и точки старта, как смена рамки")
    m.build()
    area5, _ = m.grid.outside_mask(), None
    m._refresh_envelope()
    check(area5 is not None and not bool((np.asarray(m.route_area, bool) & area5).any()),
          "область залёта не выходит за окружность",
          "ячеек залёта %d" % int(np.asarray(m.route_area, bool).sum()))
    m.set_sector_auto()
    check(len(m.sector_points()) >= 1
          and bool(m.in_area(*np.asarray(m.sector_points(), float).T).all()),
          "сектор по 360°: точки входа внутри круга", "точек %d" % len(m.sector_points()))

    print("\n5в. РЕЖИМЫ ПАНЕЛИ ДОХОДЯТ ДО «ВОЗМОЖНЫХ МАРШРУТОВ» (жалоба заказчика 21.09.2026)")
    # ⚠️ Раньше из панели брался только ВЕС, а разброс, расход и заход стояли жёстким "mix":
    # человек менял режим захода, нажимал «Применить», а синие линии не менялись вовсе.
    # Замер: средняя длина при min/max была 130.6 и 131.8 км (шум), стала 122.0 и 134.2.
    import model.threat_routes as _tr
    seen = []                                        # режимы, с которыми звали розыгрыш
    _orig_sample = _tr.sample_one_route               # настоящий розыгрыш, вернём в конце

    def _spy(ctx, mode, lmax, turn, rng, spread="mix", spend="late", approach="mix"):
        """Подменыш розыгрыша: запомнить режимы и ничего не строить."""
        seen.append((mode, spread, spend, approach))
        return None                                  # маршрут не нужен — важны аргументы

    m.p.threat_iter_mode, m.p.threat_iter_spread = "max", "edge"
    m.p.threat_spend, m.p.threat_iter_approach = "early", "medium"
    _tr.sample_one_route = _spy
    try:
        m.plan_routes()
    except Exception:                                # маршрутов нет — отбор может ругнуться
        pass                                         # нам хватило записанных аргументов
    finally:
        _tr.sample_one_route = _orig_sample
    check(bool(seen), "розыгрыш вызывался", "вызовов %d" % len(seen))
    got = set(seen)                                  # все наборы режимов, что ушли в розыгрыш
    check(got == {("max", "edge", "early", "medium")},
          "ВСЕ ЧЕТЫРЕ режима панели дошли до выборки", str(sorted(got)[:2]))

    print("\n5б. СВОЙ ЗАПАС ХОДА У КАЖДОЙ ТОЧКИ ВХОДА (план 12, задача 12.4)")
    from model import uav_types as _ut
    # ⚠️ ВЫКЛЮЧАЕМОСТЬ В НОЛЬ — главное здесь: пока старт не задан, расчёт обязан идти
    # ровно как раньше, одним числом (правило проверок, «механизм выключается в ноль»).
    check(np.isscalar(m.route_L_max()) and m.entry_L_max_km() is None,
          "без старта БПЛА — один общий запас, как было", str(m.route_L_max()))
    m.set_uav_type(_ut.defaults()[0])
    check(np.isscalar(m.route_L_max()),
          "выбран ТОЛЬКО тип, без старта — по-прежнему один запас")
    bx5, by5 = m.bbox_km[1], m.bbox_km[2]           # юго-восточный угол области
    m.set_uav_start(bx5 + 300.0, by5 - 200.0)
    per5 = m.entry_L_max_km()                        # запасы по точкам, км
    d5 = m.uav_start_dists_km()                      # расстояния старт→вход, км
    full5 = float(m.uav_type.L_max_km)               # полный запас аппарата, км
    check(per5 is not None and len(per5) == len(m.entry_points_km()),
          "со стартом запас считается по каждой точке", "точек %d" % len(per5 or []))
    check(per5 and all(abs((full5 - d) - v) < 1e-6 for d, v in zip(d5, per5)),
          "запас = запас аппарата − прямая от старта")
    check(per5 and abs(m.p.threat_L_max - sum(per5) / len(per5)) < 0.06,
          "в панель ушло СРЕДНЕЕ по точкам",
          "%.1f против %.1f" % (m.p.threat_L_max, sum(per5) / len(per5)))
    check(per5 and max(per5) - min(per5) > 1.0, "у точек запасы РАЗНЫЕ",
          "разброс %.1f км" % (max(per5) - min(per5)))
    m.p.threat_L_max_manual = True                   # человек ввёл своё число
    m.uav_lmax_per_entry = False                     # (это делает контроллер на «Применить»)
    check(np.isscalar(m.route_L_max()), "ручной запас отменяет расчёт по точкам")
    m.clear_uav_start()
    check(m.entry_L_max_km() is None and np.isscalar(m.route_L_max()),
          "«Убрать точку старта» возвращает один общий запас")
    m.p.threat_L_max_manual = False

    print("\n5г. ОБЪЕКТЫ ЗАЩИТЫ: НЕСКОЛЬКО ЦЕЛЕЙ, РОЗЫГРЫШ БЕЗ ПОВТОРОВ (план 13, 13.12–13.14)")
    from model.targets import TargetBag, route_target_index
    from model.threat_routes import flight_envelope
    # ⚠️ ВЫКЛЮЧАЕМОСТЬ В НОЛЬ: мешок из одной цели не трогает ГПСЧ — иначе поток случайных
    # чисел сместился бы, и эталон §6 разъехался бы без единой настоящей правки
    rng5 = np.random.default_rng(5)
    st5 = rng5.bit_generator.state                   # состояние ГПСЧ до розыгрыша
    one5 = TargetBag([0])                            # мешок из одной цели
    for _ in range(20):
        one5.peek(rng5); one5.commit()
    check(rng5.bit_generator.state == st5, "одна цель: ГПСЧ не тронут")
    m._refresh_envelope()
    m._ensure_ctx(m.entries_target_km()[0])
    check(len(m._ctxs) == 1 and m.target_km is None, "целей не ставили — один контекст, цель по умолчанию")
    cx5, cy5, r5 = m.area_circle                     # круглый район, км
    pts5 = [(cx5, cy5), (cx5 + 0.5 * r5, cy5 + 0.3 * r5),   # три цели внутри круга, км
            (cx5 - 0.4 * r5, cy5 - 0.5 * r5)]
    for q in pts5:
        m.add_target(*q)
    check(len(m.targets) == 3 and [t.weight for t in m.targets.items] == [0.33] * 3,
          "три цели, приоритеты поровну по 0.33")
    check(m.add_target(cx5 + 2 * r5, cy5) != "", "цель за районом не ставится")
    _, t_apply = step(m.apply_targets)               # карта и зона по всем целям, с
    env5 = np.asarray(m.route_area, bool)            # зона по всем целям
    singles = [flight_envelope(m.grid, m.entries_target_km()[0], q, m.route_L_max(),   # маски
                               m._route_gap_km(), slack_km=m._route_slack_km())[0]
               for q in pts5]                       # зона к каждой цели отдельно
    check(all(not bool((np.asarray(s, bool) & ~env5).any()) for s in singles),
          "зона пролёта — объединение по целям",
          "ячеек %d, по целям %s" % (int(env5.sum()), [int(np.asarray(s).sum()) for s in singles]))
    check(m.big_ring_target_km() is None, "доли равны — кольца больших нет (В13.8)")
    m.set_target_weight(1, 0.2); m.set_target_weight(2, 0.2); m.set_target_weight(0, 0.6)
    check(m.big_ring_target_km() == pts5[0], "0.6 / 0.2 / 0.2 — кольцо у приоритетной цели")
    check(m.set_target_weight(1, 0.5) != "", "сумма приоритетов больше 1 не принимается")
    if not quick:
        m.p.threat_iter_routes = 30
        rr5, t_it5 = step(m.iter_batch)
        own5 = [route_target_index(r, pts5) for r in rr5]   # цель каждого маршрута, по порядку
        rep5 = sum(1 for a, b in zip(own5, own5[1:]) if a == b)   # повторов подряд, шт.
        rounds5 = all(sorted(own5[k:k + 3]) == [0, 1, 2]           # каждый круг из трёх —
                      for k in range(0, len(own5) - 2, 3))         # все три цели по разу
        share5 = [own5.count(i) / max(len(own5), 1) for i in range(3)]   # доли целей
        check(len(m._ctxs) == 3, "у каждой цели свой контекст", "%.1f с на выборку" % t_it5)
        check(len(rr5) > 0 and rep5 == 0, "подряд одна цель не выпадает",
              "маршрутов %d, повторов %d" % (len(rr5), rep5))
        check(rounds5, "за круг каждая цель ровно по разу")
        check(max(share5) - min(share5) < 0.05, "доли целей поровну",
              " / ".join("%.0f %%" % (100 * s) for s in share5))
    m.target_km = None
    check(not len(m.targets) and len(m.target_points_km()) == 1,
          "`target_km = None` убирает все цели", "применение %.1f с" % t_apply)

    # ---- 6. снятие района обнуляет ВСЁ ----
    print("\n6. СНЯТИЕ РАЙОНА")
    m.clear_area()
    check(m.area_circle is None and not m.entry_points and m.sector_kind is None,
          "круг, сектор и точки входа сброшены")
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
    print("ВСЁ ПРОШЛО. Порядок работы, кэш, сброс, запас хода по точкам и розыгрыш целей — в норме.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
