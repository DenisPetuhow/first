# -*- coding: utf-8 -*-
"""
MODEL · Этап целей: ПВН у объектов защиты до общего прохода (план 13, задача 13.18).

Решения заказчика 23.09.2026 — план 13 §0.4г–§0.4е, сводка ОГРАНИЧЕНИЯ_ДАТЧИКОВ §8–§9:

* ЗОНА ЦЕЛИ — связный массив «застройка ∪ ячейки с постройками», где стоит цель (разрыв до R —
  тот же массив: город с пригородом, посёлок с дачами), ± R, ∩ карта места типа. У точки массива
  нет (цель в лесу) — зоны нет: район задаёт человек окном «укажите район защиты» (В13.21), и
  зона — этот район ∩ карта места (`area_mask`);
* ВМЕСТИМОСТЬ — сколько мест помещается в зону жадной укладкой с разносом типа 1.6·R; места ближе
  отступа (доля R, R/2) к самой цели не берутся;
* ЧИСЛО У ЦЕЛИ — ПО НАСЫЩЕНИЮ (план 13.1, Д9; решения заказчика 24.09.2026): сначала минимум 3 у
  каждой цели (по убыванию приоритета, пока хватает N — сильнее потолка), затем ОДИН общий жадный по
  всем целям: маршрут весит приоритет своей цели / её число подлётов, так что ПВН уходит туда, где
  «приоритет / средний × прирост» больше — приоритет выделяет средства расчётом, а не константой γ.
  Цель выбывает, набрав УРОВЕНЬ защиты (доля подлёта ≥ k; от k: 99 · 92 · 89.3 … %), или когда никакой
  ПВН не даёт ≥ ПОРОГА прироста (от k: 1 · 3 · 4 · 5 … %). Бюджет — ПОТОЛОК от N (50 % одной цели,
  70 % всем): насыщение раньше потолка — потолок не срабатывает. Было: 10 ПВН при насыщении на 7 (§Д9.2),
  при k 2 первая цель брала 15 из 18 по очереди, а не по пользе;
* критерий — ПОДЛЁТ (точки маршрутов к ЭТОЙ цели внутри её зоны) с кратностью защиты цели;
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


def floors(caps, pri, n_total, st):
    """Минимум у каждой цели: min(tgt_min, вместимость), по убыванию приоритета, пока хватает N (13.1 Д9).
    Вход: вместимости, приоритеты, квота типа N, паспорт. Отдаёт: список int по целям."""
    out, left = [0] * len(caps), int(n_total)        # минимумы; квота, ещё не отложенная
    for i in sorted(range(len(caps)), key=lambda i: (-pri[i], i)):   # старшим — первыми
        out[i] = max(0, min(int(st.tgt_min), int(caps[i]), left))
        left -= out[i]
    return out


def approach_share(tails, pos, R, k):
    """Доля подлёта, засечённого ≥ k датчиками. Вход: куски подлёта (M,2) км, позиции (K,2) км, радиус км,
    кратность. Отдаёт: доля 0…1; подлёта нет — 0."""
    pos = np.asarray(pos, float).reshape(-1, 2)      # позиции датчиков, км
    if not tails or not len(pos):                    # ловить некого или нечем
        return 0.0
    hits = [int((np.linalg.norm(np.asarray(t, float)[:, None, :] - pos[None, :, :], axis=2) <= R).any(axis=0).sum())  # датчиков
            for t in tails]                          # видит каждый подлёт
    return float(np.mean(np.asarray(hits) >= int(k)))


def first_line(routes, pt, pos, rad):
    """Рубеж обнаружения (13.1 Д9): расстояние от цели до первой засечки на каждом маршруте. Вход: маршруты
    (M,2) км от старта к цели, цель (x, y) км, датчики (K,2) км, их радиусы (K,) км. Отдаёт: (маршрутов,) км."""
    pos = np.asarray(pos, float).reshape(-1, 2)      # датчики, км
    rad = np.asarray(rad, float).reshape(-1)         # их радиусы, км
    out = np.zeros(len(routes))                      # не засечён — рубеж 0: дошёл до цели невидимым
    for j, r in enumerate(routes):
        pts = np.asarray(r, float)                   # точки маршрута, км
        seen = (np.linalg.norm(pts[:, None, :] - pos[None, :, :], axis=2) <= rad).any(axis=1)   # под наблюдением
        if seen.any():                               # засечён — где впервые
            out[j] = float(np.hypot(*(pts[int(np.argmax(seen))] - np.asarray(pt, float))))
    return out


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


def gain_pct(st):
    """Порог прироста у цели, %: своё значение паспорта или от кратности (`sensor_types.auto_gain_pct`).
    Вход: паспорт. Отдаёт: float."""
    from .sensor_types import auto_gain_pct
    return float(st.tgt_gain_pct) if float(st.tgt_gain_pct) > 0 else auto_gain_pct(st.tgt_k)   # 0 — от k


def level_pct(st):
    """Уровень защиты цели — доля подлёта ≥ k, %: своё значение или от кратности (`auto_level_pct`).
    Вход: паспорт. Отдаёт: float."""
    from .sensor_types import auto_level_pct
    return float(st.tgt_level_pct) if float(st.tgt_level_pct) > 0 else auto_level_pct(st.tgt_k)   # 0 — от k


def _free(cand, taken, sep):
    """Места, не ближе разноса к занятым. Вход: (M,2) км, (K,2) км, разнос км. Отдаёт: (M',2) км."""
    if not len(taken) or not len(cand):              # сравнивать не с чем
        return cand
    d = np.min(np.linalg.norm(cand[:, None, :] - taken[None, :, :], axis=2), axis=1)   # до ближайшего занятого, км
    return cand[d >= sep]


def protection(tails, places, pos, R, k, sep, L_seg, weights, levels):
    """Защита объекта (план 13.1, Д10): почему кратность мала и сколько ПВН добавить у цели — только счёт.
    Вход: подлёт [(M,2) км], места зоны (M,2) км, ПВН типа (K,2) км, радиус км, кратность, разнос км,
    сегментов, веса жадного, уровни 0…1. Отдаёт: dict — доли, недостижимое, «ещё ПВН» по уровням."""
    from .optimization import CoverageCache
    T = len(tails)                                   # подлётов к цели
    pos = np.asarray(pos, float).reshape(-1, 2)      # ПВН типа, км
    places = np.asarray(places, float).reshape(-1, 2)   # места зоны цели, км
    out = dict(n=T, share1=0.0, share=0.0, lack_places=0, lack_sep=0, need={}, best=0.0, room=0)
    if not T:                                        # ловить некого
        return out

    def seen(pts):
        """Сколько из точек pts (J,2) км видит каждый подлёт. Отдаёт: (T, J) bool."""
        if not len(pts):                             # точек нет
            return np.zeros((T, 0), bool)
        return np.array([(np.linalg.norm(np.asarray(t, float)[:, None, :] - pts[None, :, :], axis=2) <= R).any(axis=0)
                         for t in tails])
    n_now = seen(pos).sum(axis=1)                    # сколько ПВН видит каждый подлёт сейчас
    out.update(share1=float((n_now >= 1).mean()), share=float((n_now >= k).mean()))
    # ПОЧЕМУ НЕ НАБРАНО: у подлёта мест в радиусе R меньше k — «мест мало»; мест хватает, но k штук с разносом
    # не укладываются — «мешает разнос»; остальным не хватает ПВН (замер Д10.2: мест хватает у 99.6 %)
    vis = seen(places)                               # какие места видит каждый подлёт, (T, M)
    for t in np.nonzero(n_now < k)[0]:
        near = places[vis[t]]                        # места, видящие этот подлёт, км
        if len(near) < k:                            # мест в радиусе меньше k
            out["lack_places"] += 1
        elif capacity(near, sep) < k:                # k с разносом не встают
            out["lack_sep"] += 1
    # СКОЛЬКО ДОБАВИТЬ: тот же жадный цели дальше, от стоящих ПВН, до вместимости — расстановку не трогает
    free = _free(places, pos, sep)                   # свободные места зоны, км
    shares = np.zeros(0)                             # доля ≥ k после j-го добавленного
    if len(free):                                    # есть куда добавлять
        cand = np.vstack([pos, free])                # стоящие — первыми
        cache = CoverageCache(cand, R, L_seg, k)     # кэш покрытия подлёта
        for t in tails:
            cache.add_trajectory(np.asarray(t, float))
        cache.greedy(len(cand), weights, anchors=range(len(pos)), anchor_sep=sep, min_sep=sep)
        new = cand[cache.last_idx[len(pos):]]        # добавленные по порядку, км
        cum = n_now[:, None] + np.cumsum(seen(new), axis=1)   # засечки подлёта после j-го
        shares = (cum >= k).mean(axis=0)             # доля ≥ k после каждого добавленного
    out["room"] = int(len(shares))                   # сколько ещё встаёт с разносом
    out["best"] = float(shares[-1]) if len(shares) else out["share"]
    for lv in levels:
        hit = np.nonzero(shares >= lv - 1e-12)[0]    # шаги, где уровень набран
        out["need"][float(lv)] = 0 if out["share"] >= lv - 1e-12 else (int(hit[0]) + 1 if len(hit) else None)
    return out


def stage(grid, zc, place_mask, places, targets, routes_by_t, st, n_total, R, L_seg, weights, areas=None):
    """Этап целей для одного типа. Вход: сетка, сводка, карта места, места типа (M,2) км, цели
    [(x, y, приоритет)], маршруты по целям, паспорт, квота N, радиус км, сегментов, веса жадного,
    районы защиты по целям (None — нет). Отдаёт: (позиции (K,2) км, отчёт по целям — список dict)."""
    from .optimization import CoverageCache
    from .sensor_zones import bonus_vector, cells_of
    sep = 1.6 * float(R)                             # разнос внутри типа, км
    gap = float(st.tgt_gap_r) * float(R)             # отступ от самой цели, км (R/2, В13.26)
    places = np.asarray(places, float).reshape(-1, 2)   # места типа, км
    K = len(targets)                                 # число целей
    areas = list(areas) if areas is not None else [None] * K   # район человека у каждой цели
    zones = [target_zone(grid, zc, place_mask, (x, y), R, a)   # зоны целей
             for (x, y, _p), a in zip(targets, areas)]
    own = [places[cells_of(grid, places, z)] if z is not None else np.empty((0, 2))   # места в зоне цели
           for z in zones]
    own = [o[np.hypot(o[:, 0] - x, o[:, 1] - y) >= gap] for o, (x, y, _p) in zip(own, targets)]   # не у самой цели
    caps = [capacity(o, sep) for o in own]           # вместимость зон
    pri = [max(float(p), 0.0) for _x, _y, p in targets]   # приоритеты
    pri = pri if sum(pri) > 0 else [1.0] * K         # все нули — поровну
    mins = floors(caps, pri, n_total, st)            # минимум у каждой цели
    k = int(st.tgt_k)                                # кратность защиты цели
    thr, lvl = gain_pct(st) / 100.0, level_pct(st) / 100.0   # порог прироста и уровень защиты, доли
    cap_n = int(np.floor((st.tgt_cap_one if K == 1 else st.tgt_cap_all) * n_total))   # потолок от N, шт.
    budget = min(int(n_total), max(sum(mins), cap_n))   # бюджет целей: потолок, но не ниже минимумов
    tails = [approach(routes_by_t[i], grid, zones[i]) if zones[i] is not None and routes_by_t[i] else []  # подлёт
             for i in range(K)]                      # к каждой цели
    report = [dict(zone=z is not None, manual=a is not None, cells=int(z.sum()) if z is not None else 0,  # отчёт
                   capacity=c, n_min=m, placed=0, k=k, share=0.0, why="", budget=budget,
                   gain_pct=100 * thr, level_pct=100 * lvl) for z, a, c, m in zip(zones, areas, caps, mins)]
    # 1) МИНИМУМЫ — по убыванию приоритета, у каждой цели свой жадный: младшим места не мешают старшим
    fixed, owner = np.empty((0, 2)), []              # поставленное и чьё оно
    for i in sorted(range(K), key=lambda i: (-pri[i], i)):
        cand = _free(own[i], fixed, sep)             # места этой цели минус занятые
        if mins[i] <= 0 or not len(cand) or not tails[i]:   # ставить некуда или некого ловить
            continue
        cache = CoverageCache(cand, R, L_seg, k)     # кратность ЦЕЛИ, а не типа
        for t in tails[i]:
            cache.add_trajectory(t)
        got = cache.greedy(mins[i], weights, min_sep=sep, bonus=bonus_vector(grid, zc, cand, st))   # км
        fixed = np.vstack([fixed, got]) if len(got) else fixed   # к поставленным, км
        owner += [i] * len(got)
    # 2) СВЕРХ МИНИМУМОВ — один жадный по всем целям: маршрут весит приоритет цели / её подлётов
    live = [i for i in range(K) if tails[i]]         # цели, у которых есть подлёт
    pools = [_free(own[i], fixed, sep) for i in live]   # свободные места живых целей
    cand = np.vstack([fixed] + pools) if live else fixed   # закреплённые — первыми
    own_of = owner + sum(([i] * len(pl) for i, pl in zip(live, pools)), [])   # цель каждого места
    idx = list(range(len(fixed)))                    # выбранные номера кандидатов
    done, stopped = set(), False                     # цели с набранным уровнем; стоп порогом прироста
    if live and len(cand) > len(fixed) and budget > len(fixed):   # есть куда и на что ставить
        psum = sum(pri[i] for i in live)             # сумма приоритетов живых целей
        w = np.concatenate([np.full(len(tails[i]), pri[i] / (psum * len(tails[i]))) for i in live])   # вес маршрута
        grp = np.concatenate([np.full(len(tails[i]), g) for g, i in enumerate(live)])   # его цель по счёту
        cache = CoverageCache(cand, R, L_seg, k)     # один кэш на все цели
        for i in live:
            for t in tails[i]:
                cache.add_trajectory(t)
        # ⚠️ порог — на (p_i / p̄)·прирост_i: взвешенный прирост (p_i / Σp)·прирост_i сравнивается с порог / K
        cache.greedy(budget, weights, anchors=idx, anchor_sep=sep, min_sep=sep,
                     bonus=bonus_vector(grid, zc, cand, st), stop_gain=thr / len(live), stop_min=len(fixed),
                     traj_w=w, groups=grp, goals=[lvl] * len(live))
        idx = list(cache.last_idx) or idx            # закреплённые и добавленные
        done = {live[g] for g in cache.last_done}    # набрали уровень
        stopped = bool((cache.last_bound or {}).get("stopped"))   # стоп порогом прироста
    pos = cand[idx] if idx else np.empty((0, 2))     # позиции этапа целей, км
    for i in range(K):
        report[i]["placed"] = sum(1 for j in idx if own_of[j] == i)
        report[i]["share"] = approach_share(tails[i], pos, R, k)
        report[i]["why"] = ("" if not tails[i] else "уровень %.0f %%" % (100 * lvl) if i in done
                            else "прирост < %g %%" % (100 * thr) if stopped
                            else ("потолок %d" % budget if budget < int(n_total) else "квота N")
                            if len(idx) >= budget else "места")
    return pos, report
