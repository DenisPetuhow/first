# -*- coding: utf-8 -*-
"""
MODEL · Этап целей: ПВН у объектов защиты до общего прохода (план 13, задача 13.18).

Решения заказчика 23.09.2026 — план 13 §0.4г–§0.4е, сводка ОГРАНИЧЕНИЯ_ДАТЧИКОВ §8–§9:

* ЗОНА ЦЕЛИ — связный массив «застройка ∪ ячейки с постройками», где стоит цель (разрыв до R —
  тот же массив: город с пригородом, посёлок с дачами), ± R, ∩ карта места типа. У точки массива
  нет (цель в лесу) — зоны нет: район задаёт человек окном «укажите район защиты» (В13.21), и
  зона — этот район ∩ карта места (`area_mask`);
* ВМЕСТИМОСТЬ — сколько мест помещается в зону жадной укладкой с разносом типа 1.6·R;
* ЧИСЛО У ЦЕЛИ — ⌊доля · вместимость⌋ × (1 + γ·(p − p̄)/p̄), не меньше min(минимум, вместимость), не
  больше вместимости; сумма выше потолка (одна цель — cap_one·N, несколько — cap_all·N) — минимумы,
  а остаток бюджета делится в пропорции желаемому. ⚠️ Потолок — ПОСЛЕ приоритета, иначе он стирает
  приоритет (замер §0.4е: 4 / 4 / 4 вместо 6 / 4 / 4);
* ПО УБЫВАНИЮ ПРИОРИТЕТА у каждой цели свой жадный: места — зона минус занятые целями выше,
  критерий — ПОДЛЁТ (точки маршрутов к ЭТОЙ цели внутри её зоны) с кратностью цели;
* поставленное здесь идёт в общий проход закреплёнными позициями типа: их засечки считаются в
  кратности типа, другие типы обходят их разносом (§0.4г: иначе засечка ПВН 11.2 → 8.4 %).

⚠️ Гарантии для решения целиком нет — у каждого прохода своя (ОГРАНИЧЕНИЯ 1.14); цена видна числом.
"""
import numpy as np


def area_mask(grid, area):
    """Ячейки района защиты — центр ячейки внутри. Вход: сетка, район ("rect", x0, y0, x1, y1) или
    ("circle", cx, cy, r), км. Отдаёт: маска (ny,nx) bool."""
    xs = grid.ox + (np.arange(grid.nx) + 0.5) * grid.h   # центры столбцов, км
    ys = grid.oy + (np.arange(grid.ny) + 0.5) * grid.h   # центры строк, км
    X, Y = np.meshgrid(xs, ys)                           # центры ячеек (ny,nx), км
    if area[0] == "circle":                              # круг: центр и радиус
        _k, cx, cy, r = area
        return np.hypot(X - cx, Y - cy) <= r
    _k, x0, y0, x1, y1 = area                            # прямоугольник: противоположные углы
    return (X >= min(x0, x1)) & (X <= max(x0, x1)) & (Y >= min(y0, y1)) & (Y <= max(y0, y1))


def target_zone(grid, zc, place_mask, pt, R, area=None):
    """Зона цели. Вход: ThreatGrid, сводка `sensor_zones.cells`, карта места типа (ny,nx), цель (x, y) км,
    радиус типа км, район защиты человека или None. Отдаёт: маска (ny,nx) bool; None — у точки нет
    массива застройки (цель в лесу), а района нет."""
    from .sensor_zones import settled_mask
    from .threat_grid import _dilate, _label8
    if area is not None:                             # район задал человек (В13.21) — он и есть зона
        return area_mask(grid, area) & np.asarray(place_mask, bool)   # ∩ карта места: без леса, болот, воды
    r = max(1, int(round(float(R) / grid.h)))        # разрыв и полоса, ячеек (R 0.5 км — одна)
    built = settled_mask(grid) | (zc["count"] > 0)   # застройка ∪ ячейки с постройками
    lab, _n = _label8(_dilate(built, r))             # массивы: разрыв до R — один массив
    ix = int(np.floor((pt[0] - grid.ox) / grid.h))   # ячейка цели: столбец
    iy = int(np.floor((pt[1] - grid.oy) / grid.h))   # и строка
    if not (0 <= ix < grid.nx and 0 <= iy < grid.ny) or lab[iy, ix] == 0:   # цель вне сетки или в лесу
        return None                                  # — зону задаёт человек
    return _dilate((lab == lab[iy, ix]) & built, r) & np.asarray(place_mask, bool)


def capacity(places, sep):
    """Сколько мест помещается в зону жадной укладкой. Вход: места (M,2) км, разнос км. Отдаёт: int.
    Порядок обхода — по x, затем по y: так укладка воспроизводима от прогона к прогону."""
    pts = np.asarray(places, float).reshape(-1, 2)   # места зоны, км
    taken = []                                       # уложенные места, км
    for p in pts[np.lexsort((pts[:, 1], pts[:, 0]))]:
        if not taken or np.min(np.hypot(*(np.asarray(taken) - p).T)) >= sep:   # не ближе разноса
            taken.append(p)
    return len(taken)


def allocate(caps, pri, n_total, st):
    """Сколько ПВН у каждой цели (формула §0.4е). Вход: вместимости, приоритеты, квота типа N, паспорт
    (tgt_share, tgt_gamma, tgt_min, tgt_cap_one, tgt_cap_all). Отдаёт: список int по целям."""
    K = len(caps)                                    # число целей
    if K == 0:                                       # целей нет
        return []
    pbar = 1.0 / K                                   # средний приоритет
    floor_ = [min(int(st.tgt_min), int(c)) for c in caps]   # минимум у цели, если помещается
    want = [min(max(int(np.floor(c * st.tgt_share))   # желаемое по вместимости и приоритету
                    * (1.0 + st.tgt_gamma * (p - pbar) / pbar), f), c)
            for c, p, f in zip(caps, pri, floor_)]
    budget = int(np.floor((st.tgt_cap_one if K == 1 else st.tgt_cap_all) * n_total))   # потолок от N
    if sum(want) <= budget:                          # всё желаемое помещается под потолок
        return [int(round(w)) for w in want]
    order = sorted(range(K), key=lambda i: (-pri[i], i))   # цели по убыванию приоритета
    if sum(floor_) >= budget:                        # даже минимумы не влезают — старшим по приоритету
        out, left = [0] * K, budget
        for i in order:
            out[i] = min(floor_[i], left)
            left -= out[i]
        return out
    room = budget - sum(floor_)                      # бюджет сверх минимумов
    extra = [w - f for w, f in zip(want, floor_)]    # желаемое сверх минимума
    share = [e * room / (sum(extra) or 1.0) for e in extra]   # доля бюджета — в пропорции желаемому
    out = [f + int(np.floor(s)) for f, s in zip(floor_, share)]
    rest = budget - sum(out)                         # недоданное округлением
    for i in sorted(range(K), key=lambda i: (-(share[i] - np.floor(share[i])), i))[:max(0, rest)]:
        out[i] += 1                                  # — наибольшим дробным частям
    return [min(o, int(c)) for o, c in zip(out, caps)]


def approach(routes, grid, zone):
    """Подлёт к цели: точки каждого маршрута внутри её зоны. Вход: маршруты (M,2) км, сетка, зона.
    Отдаёт: список кусков; в зоне меньше двух точек — последние две (иначе подлёт пуст)."""
    from .sensor_zones import cells_of
    out = []
    for r in routes:
        pts = np.asarray(r, float)                   # точки маршрута, км
        tail = pts[cells_of(grid, pts, zone)]        # точки маршрута в зоне цели
        out.append(tail if len(tail) >= 2 else pts[-2:])
    return out


def stage(grid, zc, place_mask, places, targets, routes_by_t, st, n_total, R, L_seg, weights, areas=None):
    """Этап целей для одного типа. Вход: сетка, сводка, карта места, места типа (M,2) км, цели
    [(x, y, приоритет)], маршруты по целям, паспорт, квота N, радиус км, сегментов, веса жадного,
    районы защиты по целям (None — нет). Отдаёт: (позиции (K,2) км, отчёт по целям — список dict)."""
    from .optimization import CoverageCache
    from .sensor_zones import bonus_vector, cells_of
    sep = 1.6 * float(R)                             # разнос внутри типа, км
    places = np.asarray(places, float).reshape(-1, 2)   # места типа, км
    areas = list(areas) if areas is not None else [None] * len(targets)   # район человека у каждой цели
    zones = [target_zone(grid, zc, place_mask, (x, y), R, a)   # зоны целей
             for (x, y, _p), a in zip(targets, areas)]
    own = [places[cells_of(grid, places, z)] if z is not None else np.empty((0, 2))   # места в зоне цели
           for z in zones]
    caps = [capacity(o, sep) for o in own]           # вместимость зон
    want = allocate(caps, [p for _x, _y, p in targets], n_total, st)   # число у каждой цели
    report = [dict(zone=z is not None, manual=a is not None,   # отчёт: зона, район ли человека, вместимость,
                    cells=int(z.sum()) if z is not None else 0, capacity=c, n=n, placed=0)   # заказано, поставлено
              for z, a, c, n in zip(zones, areas, caps, want)]
    chosen = np.empty((0, 2))                        # поставлено у целей выше по приоритету, км
    for i in sorted(range(len(targets)), key=lambda i: (-targets[i][2], i)):
        cand = own[i]                                # места этой цели
        if len(chosen) and len(cand):                # занятое целями выше — не место
            d = np.min(np.linalg.norm(cand[:, None, :] - chosen[None, :, :], axis=2), axis=1)
            cand = cand[d >= sep]                    # не ближе разноса к занятым
        if want[i] <= 0 or not len(cand) or not routes_by_t[i]:   # ставить некуда или некого ловить
            continue
        cache = CoverageCache(cand, R, L_seg, int(st.tgt_k))   # кратность ЦЕЛИ, а не типа
        for tail in approach(routes_by_t[i], grid, zones[i]):
            cache.add_trajectory(tail)
        bonus = bonus_vector(grid, zc, cand, st)     # высота крыши и размер пункта (13.7) или None
        got = cache.greedy(want[i], weights, min_sep=sep, bonus=bonus)   # позиции у цели, км
        report[i]["placed"] = len(got)
        chosen = np.vstack([chosen, got]) if len(got) else chosen   # к занятым целями выше
    return chosen, report
