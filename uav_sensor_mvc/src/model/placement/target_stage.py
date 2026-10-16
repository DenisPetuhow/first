# -*- coding: utf-8 -*-
"""
MODEL · Этап целей: ПВН у объектов защиты до общего прохода (план 13, задача 13.18).

Решения заказчика 23.09.2026 — план 13 §0.4г–§0.4е, сводка ОГРАНИЧЕНИЯ_ДАТЧИКОВ §8–§9:

* ЗОНА ЦЕЛИ — связный массив «застройка ∪ ячейки с постройками», где стоит цель (разрыв до R —
  тот же массив: город с пригородом, посёлок с дачами), ± R, ∩ карта места типа. ⚠️ Застройка здесь —
  СЫРАЯ (`zone_base`), без буфера города и зоны штрафа деревень: через них Мирный сцеплялся с Плесецком
  в 6.5 км (замер 29.09.2026: массив 211 ячеек → 72). У точки массива
  нет (цель в лесу) — зоны нет: район задаёт человек окном «укажите район защиты» (В13.21), и
  зона — этот район ∩ карта места (`area_mask`);
* ВМЕСТИМОСТЬ — сколько мест помещается в зону жадной укладкой с разносом типа 1.6·R; места ближе
  отступа (доля R, R/2) к самой цели не берутся;
* ЧИСЛО У ЦЕЛИ — ДОЛЯ ПО ПРИОРИТЕТУ (поручение заказчика 30.09.2026; разбор — датчики/ЗАЩИТА_ОБЪЕКТОВ):
  ПОТРЕБНОСТЬ цели — по её кривой: до УРОВНЯ защиты (доля подлёта ≥ k; от k: 99 · 92 · 89.3 … %), а
  недостижим — до насыщения (k ПВН дают < k ПОРОГОВ прироста; от k: 1 · 3 · 4 … %); ДОЛЯ — бюджет целей
  λ·приоритет, не больше потребности и не меньше max(k, `tgt_min`); порядок — минимум у всех по
  приоритету → у старшей до доли → у следующей → остаток бюджета целям ниже уровня. Бюджет — ПОТОЛОК от N
  (50 % одной цели, 70 % всем), минимумы сильнее его. Было (Д9, 24.09): общий жадный с весом p / подлётов —
  90/5/5 при k 2 давали 8 / 3 / 3, а стоп по одному ПВН бросал цель ниже уровня (снимок 6: 81.6 % < 92 %);
* критерий — ПОДЛЁТ (точки маршрутов к ЭТОЙ цели внутри её зоны) с кратностью защиты цели;
* поставленное здесь идёт в общий проход закреплёнными позициями типа: их засечки считаются в
  кратности типа, другие типы обходят их разносом (§0.4г: иначе засечка ПВН 11.2 → 8.4 %).

⚠️ Гарантии для решения целиком нет — у каждого прохода своя (ОГРАНИЧЕНИЯ 1.14); цена видна числом.
"""
import numpy as np

SEP_R = 1.6        # разнос ПВН у цели внутри типа, доля R: перекрытие зон ≤ ~10 % (как желаемый в `_sep_range`)


def area_mask(grid, area):
    """Ячейки района защиты — центр ячейки внутри. Вход: сетка, район ("rect", x0, y0, x1, y1) или
    ("circle", cx, cy, r), км. Отдаёт: маска (ny,nx) bool."""
    xs = grid.ox + (np.arange(grid.nx) + 0.5) * grid.h   # центры столбцов, км
    ys = grid.oy + (np.arange(grid.ny) + 0.5) * grid.h   # центры строк, км
    X, Y = np.meshgrid(xs, ys)                           # центры ячеек (ny,nx), км
    if area[0] == "circle":                              # круг: центр и радиус
        _k, cx, cy, r = area                         # центр и радиус круга, км
        return np.hypot(X - cx, Y - cy) <= r
    _k, x0, y0, x1, y1 = area                            # прямоугольник: противоположные углы
    return (X >= min(x0, x1)) & (X <= max(x0, x1)) & (Y >= min(y0, y1)) & (Y <= max(y0, y1))


def zone_base(grid, zc):
    """Из чего складывается массив цели: сырая застройка ∪ ячейки с постройками. Вход: сетка, сводка `cells`.
    Отдаёт: (ny,nx) bool. ⚠️ Без «доп. зоны» (буфер города, штраф деревень) — решение заказчика 29.09.2026."""
    return np.asarray(grid._built_cells, bool) | (zc["count"] > 0)


def target_zone(grid, zc, place_mask, pt, R, area=None):
    """Зона цели. Вход: ThreatGrid, сводка `sensor_zones.cells`, карта места типа (ny,nx), цель (x, y) км,
    радиус типа км, район защиты человека или None. Отдаёт: маска (ny,nx) bool; None — у точки нет
    массива застройки (цель в лесу), а района нет."""
    from model.threat.threat_grid import _cell_index, _dilate, _label8
    if area is not None:                             # район задал человек (В13.21) — он и есть зона
        return area_mask(grid, area) & np.asarray(place_mask, bool)   # ∩ карта места: без леса, болот, воды
    r = max(1, int(round(float(R) / grid.h)))        # разрыв и полоса, ячеек (R 0.5 км — одна)
    built = zone_base(grid, zc)                      # сырая застройка ∪ ячейки с постройками
    lab, _n = _label8(_dilate(built, r))             # массивы: разрыв до R — один массив
    (ix,), (iy,), (inside,) = _cell_index(grid, np.asarray([pt], float))   # ячейка цели: столбец, строка, в сетке ли
    if not inside or lab[iy, ix] == 0:               # цель вне сетки или в лесу
        return None                                  # — зону задаёт человек
    return _dilate((lab == lab[iy, ix]) & built, r) & np.asarray(place_mask, bool)


def own_places(grid, places, zone, pt, gap):
    """Места цели: в её зоне и не ближе отступа к самой цели. Вход: сетка, места типа (M,2) км, зона (ny,nx) или
    None, цель (x, y) км, отступ км. Отдаёт: (M',2) км; зоны нет — пусто."""
    from model.placement.sensor_zones import cells_of
    if zone is None:                                 # зоны нет — и мест нет
        return np.empty((0, 2))
    o = places[cells_of(grid, places, zone)]         # места в зоне цели, км
    return o[np.hypot(o[:, 0] - pt[0], o[:, 1] - pt[1]) >= gap]


def capacity(places, sep):
    """Сколько мест помещается в зону жадной укладкой. Вход: места (M,2) км, разнос км. Отдаёт: int.
    Порядок обхода — по x, затем по y: так укладка воспроизводима от прогона к прогону."""
    pts = np.asarray(places, float).reshape(-1, 2)   # места зоны, км
    taken = []                                       # уложенные места, км
    for p in pts[np.lexsort((pts[:, 1], pts[:, 0]))]:   # места по x, затем по y — укладка воспроизводима
        if not taken or np.min(np.hypot(*(np.asarray(taken) - p).T)) >= sep:   # не ближе разноса
            taken.append(p)
    return len(taken)


def need(shares, lvl, thr, k):
    """Потребность цели по её кривой: до уровня защиты, недостижим — до насыщения. Вход: доля подлёта ≥ k после
    1…L ПВН, уровень и порог (доли), кратность. Отдаёт: (шт., достижим ли уровень, потолок кривой 0…1)."""
    S = np.concatenate([[0.0], np.asarray(shares, float)])   # доля после 0…L ПВН
    L = len(S) - 1                                   # сколько встаёт в зону
    hit = np.nonzero(S >= lvl - 1e-12)[0]            # где уровень набран
    if len(hit):                                     # достижим — до него
        return int(hit[0]), True, float(S[L])
    # ⚠️ НАСЫЩЕНИЕ — ПО ОСТАТКУ ДО ПОТОЛКА КРИВОЙ, а не по приросту шага: при k 2 первый ПВН пары прироста доли
    # ≥ 2 не даёт, при k 3 первые три — 0 · 0 · 6 %, и стоп «ПВН дал < порога» бросал цель ниже уровня (снимок 6:
    # Мирный 81.6 % при 92 %). Здесь цель добирает, пока до потолка остаётся ≥ k порогов (замер — ЗАЩИТА_ОБЪЕКТОВ §4)
    w = max(1, int(k))                               # сколько порогов остатка ещё стоят ПВН
    n = int(np.nonzero(S[L] - S < w * thr - 1e-12)[0][0]) if thr > 0 else L   # первое n с малым остатком
    return (max(n, min(w, L)) if n else 0), False, float(S[L])   # меньше k — засечки ≥ k нет


def least(want, st):
    """Наименьшее осмысленное у цели: min(потребность, max(k, `tgt_min`)) — меньше k засечки ≥ k нет. Вход:
    потребности шт., паспорт. Отдаёт: список int."""
    lo = max(int(st.tgt_k), int(st.tgt_min))         # меньше не ставить, шт.
    return [min(int(w), lo) for w in want]


def _water(budget, pri, lo, hi):
    """Деление «водой»: доля цели — λ·приоритет, не меньше lo и не больше hi; Σ = бюджет; целыми. Вход: бюджет шт.,
    приоритеты, нижние и верхние границы шт. (Σlo ≤ бюджет < Σhi). Отдаёт: список int."""
    K = len(pri)                                     # целей

    def share(lam):
        """Доли при множителе λ. Вход: λ, шт. на единицу приоритета. Отдаёт: список float, шт."""
        return [min(h, max(l, lam * p)) for l, h, p in zip(lo, hi, pri)]
    # ⚠️ у кого верхняя граница меньше λ·p, та получает её, излишек идёт остальным по приоритету — старшая не
    # забирает долю младших («3 3 3 → 10 3 3» невозможно), а 90/5/5 отдаёт 90 % старшей
    lo_l, hi_l = 0.0, max([h / p for h, p in zip(hi, pri) if p > 0] or [float(budget)])   # границы λ
    for _ in range(100):                             # деление пополам: Σ долей = бюджет
        mid = 0.5 * (lo_l + hi_l)                    # пробный λ
        if sum(share(mid)) < budget:                 # мало — λ больше
            lo_l = mid
        else:                                        # хватает — λ меньше
            hi_l = mid
    xs = share(hi_l)                                 # доли, дробные, шт.
    out = [min(h, int(np.floor(v + 1e-9))) for v, h in zip(xs, hi)]   # целые части, шт.
    rest = budget - sum(out)                         # остаток от округления, шт.
    for i in sorted(range(K), key=lambda i: (-(xs[i] - out[i]), -pri[i], i)):   # большая дробь — первой
        if rest <= 0:                                # роздано
            break
        if out[i] < hi[i]:                           # ещё можно — единицу ей
            out[i], rest = out[i] + 1, rest - 1
    return out


def allot(budget, pri, want, low, k=1):
    """Доли целей: бюджет по приоритету (λ·p), не больше потребности, не меньше наименьшего; целыми. Вход: бюджет
    шт., приоритеты, потребности шт., наименьшее шт., кратность. Отдаёт: список int по целям."""
    K = len(pri)                                     # целей
    want = [max(0, int(w)) for w in want]            # потребность, шт.
    low = [min(int(lo), w) for lo, w in zip(low, want)]   # наименьшее — не больше потребности, шт.
    budget = max(0, int(budget))                     # бюджет целей, шт.
    if sum(want) <= budget:                          # хватает всем — каждой её потребность
        return want
    if sum(low) <= budget:                           # наименьшее влезает всем — сверх него по приоритету
        return _water(budget, pri, low, want)
    # НЕ ХВАТАЕТ НА НАИМЕНЬШЕЕ ВСЕМ: сперва по k каждой, старшим первыми (меньше k засечки ≥ k нет), остаток — по
    # приоритету до наименьшего. Было «по 3 старшим»: 5 ПВН на три цели давали 3 / 2 / 0, теперь 3 / 1 / 1
    base, left = [0] * K, budget                     # по k; не розданное, шт.
    for i in sorted(range(K), key=lambda i: (-pri[i], i)):   # старшим — первыми
        give = min(int(k), low[i])                   # k, если столько нужно, шт.
        if 0 < give <= left:                         # влезает — дать
            base[i], left = give, left - give
    top = [lo if b else 0 for lo, b in zip(low, base)]   # без k — и без доли
    return _water(budget, pri, base, top) if sum(top) > budget else top


def _sees(track, pts, R):
    """Какие точки видят кусок маршрута — хоть одна его точка не дальше R. Вход: кусок (M,2) км, точки (J,2) км,
    радиус км. Отдаёт: (J,) bool."""
    t = np.asarray(track, float)                     # точки куска, км
    return (np.linalg.norm(t[:, None, :] - pts[None, :, :], axis=2) <= R).any(axis=0)


def approach_share(tails, pos, R, k):
    """Доля подлёта, засечённого ≥ k датчиками. Вход: куски подлёта (M,2) км, позиции (K,2) км, радиус км,
    кратность. Отдаёт: доля 0…1; подлёта нет — 0."""
    pos = np.asarray(pos, float).reshape(-1, 2)      # позиции датчиков, км
    if not tails or not len(pos):                    # ловить некого или нечем
        return 0.0
    hits = [int(_sees(t, pos, R).sum()) for t in tails]   # сколько датчиков видит каждый подлёт
    return float(np.mean(np.asarray(hits) >= int(k)))


def first_line(routes, pt, pos, rad):
    """Рубеж обнаружения (13.1 Д9): расстояние от цели до первой засечки на каждом маршруте. Вход: маршруты
    (M,2) км от старта к цели, цель (x, y) км, датчики (K,2) км, их радиусы (K,) км. Отдаёт: (маршрутов,) км."""
    pos = np.asarray(pos, float).reshape(-1, 2)      # датчики, км
    rad = np.asarray(rad, float).reshape(-1)         # их радиусы, км
    pt = np.asarray(pt, float)                       # цель, км
    out = np.zeros(len(routes))                      # не засечён — рубеж 0: дошёл до цели невидимым
    for j, r in enumerate(routes):                   # каждый маршрут к цели
        pts = np.asarray(r, float)                   # точки маршрута, км
        seen = (np.linalg.norm(pts[:, None, :] - pos[None, :, :], axis=2) <= rad).any(axis=1)   # под наблюдением
        if seen.any():                               # засечён — где впервые
            out[j] = float(np.hypot(*(pts[int(np.argmax(seen))] - pt)))
    return out


def approach(routes, grid, zone):
    """Подлёт к цели: точки каждого маршрута внутри её зоны. Вход: маршруты (M,2) км, сетка, зона.
    Отдаёт: список кусков; в зоне меньше двух точек — последние две (иначе подлёт пуст)."""
    from model.placement.sensor_zones import cells_of
    out = []
    for r in routes:                                 # каждый маршрут к цели — его кусок в зоне
        pts = np.asarray(r, float)                   # точки маршрута, км
        tail = pts[cells_of(grid, pts, zone)]        # точки маршрута в зоне цели
        out.append(tail if len(tail) >= 2 else pts[-2:])
    return out


def gain_pct(st):
    """Порог прироста у цели, %: своё значение паспорта или от кратности (`sensor_types.auto_gain_pct`).
    Вход: паспорт. Отдаёт: float."""
    from model.placement.sensor_types import auto_gain_pct
    return float(st.tgt_gain_pct) if float(st.tgt_gain_pct) > 0 else auto_gain_pct(st.tgt_k)   # 0 — от k


def level_pct(st):
    """Уровень защиты цели — доля подлёта ≥ k, %: своё значение или от кратности (`auto_level_pct`).
    Вход: паспорт. Отдаёт: float."""
    from model.placement.sensor_types import auto_level_pct
    return float(st.tgt_level_pct) if float(st.tgt_level_pct) > 0 else auto_level_pct(st.tgt_k)   # 0 — от k


def _free(cand, taken, sep):
    """Места, не ближе разноса к занятым. Вход: (M,2) км, (K,2) км, разнос км. Отдаёт: (M',2) км."""
    if not len(taken) or not len(cand):              # сравнивать не с чем
        return cand
    d = np.min(np.linalg.norm(cand[:, None, :] - taken[None, :, :], axis=2), axis=1)   # до ближайшего занятого, км
    return cand[d >= sep]


def _useful_left(tails, places, pos, R, k, sep):
    """Осталось ли у цели свободное место, видящее её подлёт с засечками меньше k. Вход: подлёт [(M,2) км], места
    зоны (M,2) км, поставленные ПВН (K,2) км, радиус км, кратность, разнос км. Отдаёт: bool."""
    pos = np.asarray(pos, float).reshape(-1, 2)      # поставленные, км
    free = _free(np.asarray(places, float).reshape(-1, 2), pos, sep)   # места не ближе разноса к поставленным, км
    if not tails or not len(free):                   # ловить некого или ставить некуда
        return False
    for t in tails:                                  # каждый подлёт цели
        if _sees(t, pos, R).sum() < k and _sees(t, free, R).any():   # засечек меньше k, и свободное место его видит
            return True                              # — ставить есть куда
    return False


def protection(tails, places, pos, R, k, sep, L_seg, weights, levels):
    """Защита объекта (план 13.1, Д10): почему кратность мала и сколько ПВН добавить у цели — только счёт.
    Вход: подлёт [(M,2) км], места зоны (M,2) км, ПВН типа (K,2) км, радиус км, кратность, разнос км,
    сегментов, веса жадного, уровни 0…1. Отдаёт: dict — доли, недостижимое, «ещё ПВН» по уровням."""
    from model.placement.optimization import CoverageCache
    T = len(tails)                                   # подлётов к цели
    pos = np.asarray(pos, float).reshape(-1, 2)      # ПВН типа, км
    places = np.asarray(places, float).reshape(-1, 2)   # места зоны цели, км
    out = dict(n=T, share1=0.0, share=0.0, lack_places=0, lack_sep=0, need={}, best=0.0, room=0)
    if not T:                                        # ловить некого
        return out

    def seen(pts):
        """Какие из точек видят каждый подлёт. Вход: точки pts (J,2) км. Отдаёт: (T, J) bool."""
        return np.array([_sees(t, pts, R) for t in tails]).reshape(T, len(pts))
    n_now = seen(pos).sum(axis=1)                    # сколько ПВН видит каждый подлёт сейчас
    out.update(share1=float((n_now >= 1).mean()), share=float((n_now >= k).mean()))
    # ПОЧЕМУ НЕ НАБРАНО: у подлёта мест в радиусе R меньше k — «мест мало»; мест хватает, но k штук с разносом
    # не укладываются — «мешает разнос»; остальным не хватает ПВН (замер Д10.2: мест хватает у 99.6 %)
    vis = seen(places)                               # какие места видит каждый подлёт, (T, M)
    for t in np.nonzero(n_now < k)[0]:               # подлёты, не набравшие k, — почему
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
        for t in tails:                              # строка кэша на каждый подлёт
            cache.add_trajectory(np.asarray(t, float))
        cache.greedy(len(cand), weights, anchors=range(len(pos)), anchor_sep=sep, min_sep=sep)
        new = cand[cache.last_idx[len(pos):]]        # добавленные по порядку, км
        cum = n_now[:, None] + np.cumsum(seen(new), axis=1)   # засечки подлёта после j-го
        shares = (cum >= k).mean(axis=0)             # доля ≥ k после каждого добавленного
    out["room"] = int(len(shares))                   # сколько ещё встаёт с разносом
    out["best"] = float(shares[-1]) if len(shares) else out["share"]
    for lv in levels:                                # каждый уровень — сколько ПВН до него добавить
        hit = np.nonzero(shares >= lv - 1e-12)[0]    # шаги, где уровень набран
        out["need"][float(lv)] = 0 if out["share"] >= lv - 1e-12 else (int(hit[0]) + 1 if len(hit) else None)
    return out


def _fill(grid, zc, st, own, tails, fixed, n_max, R, L_seg, k, sep, weights):
    """Жадный одной цели поверх стоящих ПВН (их засечки в счёт). Вход: сетка, сводка, паспорт, места цели (M,2) км,
    подлёт, стоящие (F,2) км, сколько добавить, радиус км, сегментов, кратность, разнос км, веса.
    Отдаёт: (добавленные по порядку (n,2) км, доля подлёта ≥ k после каждого (n,))."""
    from model.placement.optimization import CoverageCache
    from model.placement.sensor_zones import bonus_vector
    free = _free(own, fixed, sep)                    # места цели не ближе разноса к стоящим, км
    if n_max <= 0 or not len(free) or not tails:     # ставить нечего, некуда или некого ловить
        return np.empty((0, 2)), np.zeros(0)
    nf = len(fixed)                                  # стоящих, шт.
    cand = np.vstack([fixed, free]) if nf else free  # стоящие — первыми, закреплёнными
    cache = CoverageCache(cand, R, L_seg, k)         # кратность ЦЕЛИ, а не типа
    for t in tails:                                  # подлёты цели — строки кэша
        cache.add_trajectory(t)
    cache.greedy(nf + n_max, weights, anchors=range(nf), anchor_sep=sep, min_sep=sep,
                 bonus=bonus_vector(grid, zc, cand, st))
    new = list(cache.last_idx[nf:])                  # добавленные по порядку — номера кандидатов
    H = np.asarray(cache._hit, np.uint8)             # засекает ли место подлёт, (подлётов, мест)
    base = H[:, :nf].sum(axis=1, dtype=np.int64)     # засечки подлёта стоящими
    cum = base[:, None] + np.cumsum(H[:, new], axis=1, dtype=np.int64)   # после каждого добавленного
    return cand[new], (cum >= int(k)).mean(axis=0)


def stage(grid, zc, place_mask, places, targets, routes_by_t, st, n_total, R, L_seg, weights, areas=None):
    """Этап целей для одного типа. Вход: сетка, сводка, карта места, места типа (M,2) км, цели
    [(x, y, приоритет)], маршруты по целям, паспорт, квота N, радиус км, сегментов, веса жадного,
    районы защиты по целям (None — нет). Отдаёт: (позиции (K,2) км, отчёт по целям — список dict)."""
    sep = SEP_R * float(R)                           # разнос внутри типа, км
    gap = float(st.tgt_gap_r) * float(R)             # отступ от самой цели, км (R/2, В13.26)
    places = np.asarray(places, float).reshape(-1, 2)   # места типа, км
    K = len(targets)                                 # число целей
    areas = list(areas) if areas is not None else [None] * K   # район человека у каждой цели
    zones = [target_zone(grid, zc, place_mask, (x, y), R, a)   # зоны целей
             for (x, y, _p), a in zip(targets, areas)]
    own = [own_places(grid, places, z, (x, y), gap) for z, (x, y, _p) in zip(zones, targets)]   # места цели, км
    caps = [capacity(o, sep) for o in own]           # вместимость зон
    pri = [max(float(p), 0.0) for _x, _y, p in targets]   # приоритеты
    pri = pri if sum(pri) > 0 else [1.0] * K         # все нули — поровну
    k = int(st.tgt_k)                                # кратность защиты цели
    thr, lvl = gain_pct(st) / 100.0, level_pct(st) / 100.0   # порог прироста и уровень защиты, доли
    tails = [approach(routes_by_t[i], grid, zones[i]) if zones[i] is not None and routes_by_t[i] else []  # подлёт
             for i in range(K)]                      # к каждой цели
    empty = np.empty((0, 2))                         # ни одного стоящего

    def fill(i, fixed, n):
        """Жадный цели i поверх `fixed`. Вход: номер цели, стоящие (F,2) км, сколько добавить. Отдаёт: как `_fill`."""
        return _fill(grid, zc, st, own[i], tails[i], fixed, n, R, L_seg, k, sep, weights)
    # 1) ПОТРЕБНОСТЬ — каждой цели одной: её жадный до вместимости, по кривой — до уровня или до насыщения
    got = [need(fill(i, empty, len(own[i]))[1], lvl, thr, k) for i in range(K)]   # (шт., достижим ли, потолок)
    want = [g[0] for g in got]                       # потребность, шт.
    reach = [g[1] for g in got]                      # уровень достижим одной цели
    top = [g[2] for g in got]                        # потолок кривой — доля при всех местах зоны
    low = least(want, st)                            # меньше не ставить, шт.
    cap_n = int(np.floor((st.tgt_cap_one if K == 1 else st.tgt_cap_all) * n_total))   # потолок от N, шт.
    budget = min(int(n_total), max(sum(low), cap_n))   # бюджет целей: потолок, но не ниже наименьших
    # 2) ДОЛЯ — бюджет по приоритету; МИНИМУМ — первая очередь каждой цели (не больше её доли)
    part = allot(budget, pri, want, low, k)          # доля цели, шт.
    mins = [min(a, lo) for a, lo in zip(part, low)]  # минимум, шт.
    report = [dict(zone=z is not None, manual=a is not None, cells=int(z.sum()) if z is not None else 0,  # отчёт
                   capacity=c, n_min=m, want=w, reach=r, top=t, allot=p, placed=0, k=k, share=0.0, why="",
                   budget=budget, gain_pct=100 * thr, level_pct=100 * lvl)
              for z, a, c, m, w, r, t, p in zip(zones, areas, caps, mins, want, reach, top, part)]
    fixed, owner = empty, []                         # поставленное, км, и чьё оно

    def put(i, n):
        """Поставить у цели i до n ПВН, стоп на уровне защиты. Вход: номер цели, шт. Отдаёт: ничего."""
        nonlocal fixed, owner
        n = min(int(n), budget - len(owner))         # не сверх бюджета
        if n <= 0:                                   # ставить нечего
            return
        add, sh = fill(i, fixed, n)                  # добавленные, км, и доля после каждого
        hit = np.nonzero(sh >= lvl - 1e-12)[0]       # где уровень набран
        m = int(hit[0]) + 1 if len(hit) else len(add)   # сколько взять, шт.
        if m:                                        # что-то встало
            fixed = np.vstack([fixed, add[:m]])      # к поставленным этапа, км
            owner += [i] * m

    def low_now(i):
        """Ниже ли цель i уровня защиты сейчас. Вход: номер цели. Отдаёт: bool."""
        return bool(tails[i]) and approach_share(tails[i], fixed, R, k) < lvl - 1e-12
    # 3) ПОСТАНОВКА: а) минимум у всех — старшим первыми, чтобы младшим хватило мест; б) до доли — у старшей до
    # уровня, потом у следующей; в) остаток бюджета (набрали раньше доли, лишнее от округления) — целям ниже
    # уровня, старшим первыми: достижим — до уровня, недостижим — до насыщения
    order = sorted(range(K), key=lambda i: (-pri[i], i))   # цели по убыванию приоритета
    for i in order:                                  # а) минимум
        put(i, mins[i])
    for i in order:                                  # б) до доли
        if low_now(i):                               # уровень не набран — добрать
            put(i, part[i] - owner.count(i))
    for i in order:                                  # в) остаток бюджета
        if low_now(i):                               # всё ещё ниже уровня
            put(i, (len(own[i]) if reach[i] else want[i]) - owner.count(i))
    for i in range(K):                               # отчёт по каждой цели
        rp = report[i]                               # строка отчёта цели
        rp["placed"] = owner.count(i)
        rp["share"] = approach_share(tails[i], fixed, R, k)
        # ⚠️ «мест нет» — раньше «потолка»: маленькой зоне бюджет не поможет, а отчёт писал «потолок 14»
        # (замер 29.09.2026: площадка 24 ячейки, вместимость 4, с подлётом 2 места — встало 2 при минимуме 3)
        if not tails[i]:                             # подлёта нет — и причины нет
            rp["why"] = ""
        elif rp["share"] >= lvl - 1e-12:             # защищена
            rp["why"] = "уровень %.0f %%" % (100 * lvl)
        elif not _useful_left(tails[i], own[i], fixed, R, k, sep):   # места, видящие подлёт, кончились
            rp["why"] = "мест с подлётом нет"
        elif not reach[i] and rp["placed"] >= want[i]:   # уровень недостижим — насыщена
            rp["why"] = "насыщение: максимум %.0f %%" % (100 * top[i])
        elif not rp["placed"]:                       # бюджета не хватило даже на наименьшее
            rp["why"] = "ПВН не хватило"
        else:                                        # бюджет кончился раньше уровня
            rp["why"] = "доля %d, %s" % (part[i], "потолок %d" % budget if budget < int(n_total) else "квота N")
    return fixed, report
