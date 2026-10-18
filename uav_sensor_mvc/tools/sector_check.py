# -*- coding: utf-8 -*-
"""
ПРОВЕРКА МАРШРУТОВ СЕКТОРА — подлёт до района (план 16: маршруты сектора 16.6, форма самолётного типа 16.9).

Маршрут до района — только самолётного типа (заказчик 17.10.2026): без петель, поворот дугой не круче R_min =
V²/(g·tg φ) (φ — наибольший крен `threat_bank_deg`); маршруты к одному входу — полосами слева направо, плавно, без
перехлёстов; до района не ближе запаса облёта m (15 % площади района, пособие 6 (10.12в)). Маршруты района (вилять
можно) этим скриптом не меряются — для них `motion_check.py`.

    python tools/sector_check.py                           # самопроверка мер на случаях с известным ответом
    python tools/sector_check.py --npz out_test/plan16/m8d_routes.npz   # маршруты из файла (прототип М8г)

Файл: `entries` — имена входов; на вход — «<вход>@xy» (линии подряд, км), «<вход>@off» (начала линий), «<вход>@E»
(точка входа); необязательно — `district` (контур района, км), `margin_km`, `v_kmh`, `bank_deg`, `start`.
Когда 16.6 будет в коде, его маршруты пишутся в такой же файл. Код возврата: 0 — без нарушений, 1 — есть.
"""
import argparse
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # корень проекта
for _p in (ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "tools")):   # код в src/, соседние проверки — tools/
    if _p not in sys.path:   # ещё не в путях — добавить
        sys.path.insert(0, _p)
if hasattr(sys.stdout, "reconfigure"):   # печать в файл — UTF-8, иначе знаки «≥», «×» роняют вывод
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from shapely.geometry import LineString, Polygon

from motion_check import _resample, _turns        # переразбивка линии и повороты — как у маршрутов района

STEP_KM = 0.1             # шаг переразбивки для радиуса по трём точкам: дуга R_min 0.55 км — 6 точек на радиан, км
R_TOL = 0.02              # допуск на радиус: дискретная дуга даёт ошибку меньше 1 %, доля
TRIM = 0.05               # при счёте перехлёстов отрезать долю пути у старта (там полосы расходятся из точки)
CONVERGE = 0.75           # с этой доли пути полосы сходятся к входу (min(1, (1 − t)/0.25), пособие 6 (10.12б))


def r_min_km(v_kmh, bank_deg):
    """Наименьший радиус разворота самолётного типа. Вход: скорость км/ч, наибольший крен °. Отдаёт: км."""
    return (v_kmh / 3.6) ** 2 / (9.81 * math.tan(math.radians(bank_deg))) / 1e3


def min_radius(P):
    """Наименьший радиус кривизны по трём соседним точкам переразбитой линии. Вход: (N, 2) км.
    Отдаёт: (радиус км, точка (2,) км); прямая — inf."""
    Q, _s = _resample(P, STEP_KM)   # линия с равным шагом
    if len(Q) < 3:                                     # нечего мерить
        return float("inf"), Q[0]
    a, b, c = Q[:-2], Q[1:-1], Q[2:]   # тройки соседних точек
    ab, bc, ca = (np.hypot(*(b - a).T), np.hypot(*(c - b).T), np.hypot(*(a - c).T))   # стороны треугольников, км
    cr = np.abs((b - a)[:, 0] * (c - a)[:, 1] - (b - a)[:, 1] * (c - a)[:, 0])   # удвоенная площадь треугольника
    R = np.where(cr > 1e-12, ab * bc * ca / np.maximum(2.0 * cr, 1e-30), np.inf)   # R = abc / (4·S)
    i = int(np.argmin(R))
    return float(R[i]), b[i]


def turns_per_100km(P):
    """Сумма поворотов на 100 км пути. Вход: (N, 2) км. Отдаёт: градусы."""
    Q, s = _resample(P, 0.5)   # линия с шагом 0.5 км и путь, км
    if len(Q) < 3 or s[-1] <= 0:                       # короткая линия
        return 0.0
    return float(np.degrees(np.abs(_turns(Q)[1]).sum()) * 100.0 / s[-1])


def trimmed(P, lo, hi):
    """Часть линии между долями пути. Вход: (N, 2) км, доли от и до. Отдаёт: LineString или None."""
    ls = LineString(P)   # линия маршрута
    L = ls.length   # длина, км
    if L <= 0:                                         # вырожденная
        return None
    Q, s = _resample(P, max(L / 2000.0, 0.05))   # точки с мелким шагом и путь до них, км
    keep = (s >= lo * L) & (s <= hi * L)   # точки внутри доли пути
    return LineString(Q[keep]) if keep.sum() >= 2 else None


def crossings(lines):
    """Перехлёсты полос одного входа до схода: пары, чьи линии пересекаются или сливаются между TRIM и CONVERGE пути.
    Вход: список (N, 2) км. Отдаёт: число пар."""
    tl = [trimmed(P, TRIM, CONVERGE) for P in lines]   # части полос до схода
    n = 0   # пар с перехлёстом
    for i in range(len(tl)):                           # каждая пара один раз
        for j in range(i + 1, len(tl)):   # вторая полоса пары
            # обе части есть и пересекаются — перехлёст
            if tl[i] is not None and tl[j] is not None and tl[i].intersects(tl[j]):
                n += 1
    return n


def lane_order(lines, A, E):
    """Идут ли полосы слева направо в порядке номеров: смещение поперёк оси «старт → вход» на середине пути.
    Вход: линии, старт (2,), вход (2,) км. Отдаёт: (порядок соблюдён, смещения (k,) км)."""
    ax = np.asarray(E, float) - np.asarray(A, float)
    L = float(np.hypot(*ax))   # длина оси, км
    u = ax / max(L, 1e-9)                              # орт оси
    off = []   # смещения полос поперёк оси, км
    for P in lines:                                    # смещение у точки, ближайшей к середине оси
        rel = np.asarray(P, float) - A   # от старта, км
        t = rel @ u                                    # вдоль оси, км
        i = int(np.argmin(np.abs(t - 0.5 * L)))
        off.append(float(rel[i, 0] * u[1] - rel[i, 1] * u[0]))   # поперёк, км (+ — правее оси)
    off = np.array(off)
    d = np.diff(off)   # разности соседних смещений, км
    return bool((d > 0).all() or (d < 0).all()), off


def check_lines(name, lines, A, E, rmin, district=None, margin=0.0, verbose=True):
    """Все меры для маршрутов одного входа. Вход: имя, линии (N, 2) км, старт, вход, R_min км, район, запас км.
    Отдаёт: число нарушений (петли, радиус меньше R_min, ближе запаса к району)."""
    bad = 0   # нарушений
    loops = [not LineString(P).is_simple for P in lines]   # самопересечение — петля
    rad = [min_radius(P) for P in lines]   # (радиус км, где) по маршрутам
    small = [k for k, (r, _pt) in enumerate(rad) if r < rmin * (1.0 - R_TOL)]   # маршруты круче R_min
    tu = np.array([turns_per_100km(P) for P in lines])   # повороты на 100 км, градусы
    Ls = np.array([LineString(P).length for P in lines])   # длины, км
    order, off = lane_order(lines, A, E)   # порядок слева направо, смещения км
    nx = crossings(lines)   # пар с перехлёстом до схода
    near = []   # (маршрут, до района км) ближе запаса
    if district is not None and margin > 0:            # не заходить в запас облёта до точки входа
        for k, P in enumerate(lines):                  # маршрут за маршрутом
            Q, s = _resample(P, 0.5)   # линия с шагом 0.5 км и путь, км
            pre = Q[s <= s[-1] - 3.0 * margin] if len(Q) > 2 else Q   # без последних 3·m пути — подход к входу
            # до входа ближе запаса — нарушение
            if len(pre) >= 2 and LineString(pre).distance(district) < margin * (1.0 - R_TOL):
                near.append((k, float(LineString(pre).distance(district))))
    bad = sum(loops) + len(small) + len(near)
    if verbose:   # печатать разбор
        print("  %-8s маршрутов %d · длина %.0f км (%.0f…%.0f) · поворотов на 100 км %.0f° (%.0f…%.0f) · петель %d · "
              "наименьший радиус %.3f км (R_min %.3f) · пар полос с перехлёстом до схода %d · слева направо: %s · размах на середине "
              "%.0f км%s"
              % (name, len(lines), Ls.mean(), Ls.min(), Ls.max(), tu.mean(), tu.min(), tu.max(), sum(loops),
                 min(r for r, _ in rad), rmin, nx, "да" if order else "НЕТ", off.max() - off.min(),
                 "" if district is None else " · ближе запаса к району: %d" % len(near)))
        for k in small:                                # где радиус меньше
            print("           ❌ маршрут %d: радиус %.3f км у (%.1f, %.1f)" % (k + 1, rad[k][0], *rad[k][1]))
        for k, dd in near:   # маршруты ближе запаса
            print("           ❌ маршрут %d: до района %.2f км при запасе %.2f" % (k + 1, dd, margin))
        for k, lp in enumerate(loops):   # маршруты с петлёй
            if lp:                                     # петля
                print("           ❌ маршрут %d: петля (линия пересекает себя)" % (k + 1))
    return bad, dict(loops=sum(loops), small=len(small), near=len(near), cross=nx, order=order, turns=float(tu.mean()))


def arc(c, r, a0, a1, n=60):
    """Дуга окружности. Вход: центр (2,), радиус км, углы от и до, радианы, точек. Отдаёт: (n, 2) км."""
    t = np.linspace(a0, a1, n)   # углы точек дуги, радианы
    return np.c_[c[0] + r * np.cos(t), c[1] + r * np.sin(t)]


def selftest(rmin):
    """Меры на линиях с известным ответом. Вход: R_min км. Отдаёт: число расхождений (печатает строку на случай)."""
    bad = 0   # расхождений
    straight = np.array([[0.0, 0.0], [0.0, 100.0]])   # прямая 100 км на север
    # поворот 90° дугой радиуса 2·R_min между двумя прямыми по 50 км
    R2 = 2.0 * rmin
    # две прямые и дуга радиуса 2·R_min между ними
    fil = np.vstack([[[0.0, 0.0]], arc((R2, 50.0), R2, math.pi, math.pi / 2), [[50.0 + R2, 50.0 + R2]]])
    corner = np.array([[0.0, 0.0], [0.0, 50.0], [50.0, 50.0]])   # угол 90° без дуги
    # прямая с полным кругом радиуса 3 км посередине
    loop = np.vstack([[[0.0, 0.0]], [[0.0, 50.0]], arc((3.0, 50.0), 3.0, math.pi, -math.pi, 120), [[0.0, 100.0]]])
    cases = [("прямая 100 км: радиус inf, поворотов 0", min_radius(straight)[0] == float("inf")   # (имя, сошлось ли)
              and turns_per_100km(straight) < 1e-6),
             ("дуга 2·R_min: радиус 2·R_min ± 2 %",abs(min_radius(fil)[0] / R2 - 1.0) < R_TOL),
             ("угол без дуги: радиус меньше R_min", min_radius(corner)[0] < rmin),
             ("петля-круг: линия не простая", not LineString(loop).is_simple),
             ("прямая — без петли", LineString(straight).is_simple)]
    A, E = np.zeros(2), np.array([0.0, 100.0])   # старт и вход, км
    # 5 полос веером: смещение на середине −20…20 км
    lanes = [np.array([[0.0, 0.0], [s, 50.0], [0.0, 100.0]]) for s in (-20.0, -10.0, 0.0, 10.0, 20.0)]
    cases.append(("5 полос веером: перехлёстов 0, слева направо", crossings(lanes) == 0 and lane_order(lanes, A, E)[0]))
    # средняя полоса переходит с левого края на правый
    swap = [lanes[0], np.array([[0.0, 0.0], [-20.0, 30.0], [20.0, 70.0], [0.0, 100.0]]), lanes[4]]
    cases.append(("полоса через ось: перехлёстов 2", crossings(swap) == 2))
    for name, ok in cases:                             # печать и счёт
        bad += int(not ok)
        print("  %s %s" % ("✅" if ok else "❌", name))
    return bad


def main(argv=None):
    """Разбор ключей и запуск. Вход: ключи командной строки. Отдаёт: код возврата."""
    from config import Params
    ap = argparse.ArgumentParser(description="Проверка маршрутов сектора (план 16)")   # ключи командной строки
    ap.add_argument("--npz", help="файл маршрутов (формат — в шапке)")
    ar = ap.parse_args(argv)   # разобранные ключи
    t0 = time.time()   # начало, с
    p = Params()   # параметры программы
    rmin = r_min_km(float(p.threat_speed_kmh), float(p.threat_bank_deg))   # из параметров программы, км
    print("Самопроверка мер (R_min %.3f км: %.0f км/ч, крен %.0f°):" % (rmin, p.threat_speed_kmh, p.threat_bank_deg))
    bad = selftest(rmin)   # расхождений самопроверки
    viol = 0   # нарушений в маршрутах из файла
    if ar.npz:                                         # маршруты из файла
        d = np.load(ar.npz, allow_pickle=True)   # маршруты и описание сектора
        if "v_kmh" in d and "bank_deg" in d:           # ТТХ из файла — у маршрутов своего типа
            rmin = r_min_km(float(d["v_kmh"]), float(d["bank_deg"]))
        district = Polygon(d["district"]) if "district" in d else None   # контур района, км
        margin = float(d["margin_km"]) if "margin_km" in d else 0.0   # запас облёта, км
        A = np.asarray(d["start"], float) if "start" in d else np.zeros(2)   # старт, км
        print("Маршруты %s · R_min %.3f км · запас облёта %.2f км:" % (ar.npz, rmin, margin))
        for e in d["entries"]:                         # вход за входом
            xy, off = d[str(e) + "@xy"], d[str(e) + "@off"]   # линии подряд и их начала
            lines = [xy[off[i]:off[i + 1]] for i in range(len(off) - 1)]   # маршруты входа (N, 2) км
            v, _ = check_lines(str(e), lines, A, d[str(e) + "@E"], rmin, district, margin)   # нарушений у входа
            viol += v
    print("%s · %.1f с" % ("ВСЁ СОШЛОСЬ" if bad + viol == 0 else "РАСХОЖДЕНИЙ: самопроверка %d, нарушений %d" % (bad, viol),
                           time.time() - t0))
    return 1 if bad + viol else 0


if __name__ == "__main__":   # запуск скриптом, а не импортом
    sys.exit(main())
