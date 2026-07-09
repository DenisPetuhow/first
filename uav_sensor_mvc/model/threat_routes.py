# -*- coding: utf-8 -*-
"""
MODEL · Построение маршрутов пролёта БПЛА по весовой карте угроз (вкладка 3).

НАЗНАЧЕНИЕ. По готовой весовой (тепловой) карте построить: (1) ОГИБАЮЩУЮ — все места,
куда БПЛА в принципе может залететь по пути вход→цель в пределах запаса хода; (2) набор
ВЕРОЯТНЫХ маршрутов вход→цель, тяготеющих к «тяжёлым» коридорам (реки/дороги/ЛЭП) и
обходящих города.

ДВА РАЗНЫХ ВОПРОСА — ДВА РАЗНЫХ МЕТОДА:

  * «Куда ВООБЩЕ можно долететь» — детерминированно, точно. Через два поля расстояний
    (алгоритм Дейкстры от входа и от цели): ячейка достижима, если
    `путь_от_входа + путь_до_цели ≤ запас_хода`  (см. `flight_envelope`).
  * «Как ВЕРОЯТНО летит» — случайные прогоны (метод Монте-Карло). Стохастическое
    блуждание по полю расстояний к цели: на каждом шаге сосед выбирается с вероятностью
    ∝ (приближение к цели × привлекательность по весу × удержание курса), развилка
    разыгрывается «рулеткой» (см. `_walk_field`, `sample_one_route`).

ПОСТ-ОБРАБОТКА линии: упрощение (RDP) убирает лишние точки, интерполяция Катмулла–Рома
делает повороты плавными дугами, а прямые — прямыми («дугами и прямыми»).

ЕДИНИЦЫ. Всё в километрах (локальный км-фрейм сетки). Ячейки адресуются как (iy, ix) или
плоским индексом `iy*nx + ix`. Тяжёлый расчёт идёт в фоновом потоке контроллера.

ПУБЛИЧНЫЙ API (используется в model/threat_grid.py):
  flight_envelope · build_iter_context · sample_one_route · passable_mask
"""
import heapq                      # двоичная куча для Дейкстры (_dijkstra_dist)
import numpy as np

# 8 соседей ячейки: (dy, dx, множитель длины шага). Орт. сосед — шаг h, диагональный — h·√2.
_NEIGHBORS = [(-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
              (-1, -1, 1.41421356), (-1, 1, 1.41421356),
              (1, -1, 1.41421356), (1, 1, 1.41421356)]


def _cell_of(grid, xy):
    """Точка (x, y) в км -> индекс ячейки (iy, ix), обрезанный по границам сетки."""
    ix = int(np.floor((xy[0] - grid.ox) / grid.h))
    iy = int(np.floor((xy[1] - grid.oy) / grid.h))
    return (min(max(iy, 0), grid.ny - 1), min(max(ix, 0), grid.nx - 1))


def _dijkstra_dist(passable, start, h):
    """Поле ГЕОМЕТРИЧЕСКИХ расстояний (км) от start по проходимым ячейкам (8-связно).
    Шаг = h (орт.) или h·√2 (диаг.). Непроходимые (passable=False) не посещаются."""
    ny, nx = passable.shape
    dist = np.full(ny * nx, np.inf)
    si = start[0] * nx + start[1]
    dist[si] = 0.0
    pq = [(0.0, si)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u]:
            continue
        uy, ux = divmod(u, nx)
        for dy, dx, mul in _NEIGHBORS:
            vy, vx = uy + dy, ux + dx
            if vy < 0 or vy >= ny or vx < 0 or vx >= nx or not passable[vy, vx]:
                continue
            nd = d + mul * h
            v = vy * nx + vx
            if nd < dist[v]:
                dist[v] = nd
                heapq.heappush(pq, (nd, v))
    return dist.reshape(ny, nx)


def passable_mask(grid, max_gap_km):
    """Проходимые ячейки для маршрута: коридоры (вес>0) + короткие ПРЯМЫЕ мостики через
    провалы ≤ max_gap_km (дилатация коридоров на пол-провала), МИНУС город (кроме рек).
    Провал > max_gap_km не перекрывается -> коридор там разорван (маршрут невозможен
    напрямую, идёт в обход или не строится)."""
    from .threat_grid import _dilate
    corridor = grid.weight > 0
    r = max(0, int(round(0.5 * max_gap_km / grid.h)))          # мостик ≤ 2r ячеек
    pas = _dilate(corridor, r) if r > 0 else corridor.copy()
    pas &= ~grid.urban_mask()                                  # город (кроме рек) непроходим
    return pas


def _open_endpoints(pas, cells_yx, grid, radius_km=3.0):
    """Открыть проходимость в окрестности ~radius_km вокруг заданных ячеек (вход/цель),
    чтобы они «дотянулись» до коридорной сети (БПЛА проходит несколько км над полем до
    реки/дороги на входе/выходе). Меняет pas на месте, возвращает его же."""
    rc = max(1, int(round(radius_km / grid.h)))
    for cy, cx in cells_yx:
        pas[max(0, cy - rc):cy + rc + 1, max(0, cx - rc):cx + rc + 1] = True
    return pas


def flight_envelope(grid, entry_km, target_km, L_max, max_gap_km):
    """ВСЕ ВОЗМОЖНЫЕ МЕСТА ПРОЛЁТА при запасе хода L_max. Ячейка попадает в «возможные
    места», если существует путь вход→ячейка→цель по коридорам (с мостиками ≤ max_gap)
    суммарной длиной ≤ L_max:

        envelope = { ячейка : glen_вход(ячейка) + glen_цель(ячейка) ≤ L_max }

    Так учитываются и ДЛИННЫЕ обходы (зайти с другой стороны) — лишь бы уложиться в
    запас хода. Возвращает (mask_возможных_ячеек, min_длина_вход→цель)."""
    start = _cell_of(grid, entry_km)
    goal = _cell_of(grid, target_km)
    pas = _open_endpoints(passable_mask(grid, max_gap_km), (start, goal), grid)
    ge = _dijkstra_dist(pas, start, grid.h)
    gt = _dijkstra_dist(pas, goal, grid.h)
    total = ge + gt
    env = np.isfinite(total) & (total <= L_max)
    dmin = float(ge[goal]) if np.isfinite(ge[goal]) else float("inf")
    return env, dmin


# --- ИТЕРАЦИОННАЯ (стохастическая) генерация множества маршрутов ---------------
# 8 единичных векторов направлений (в порядке _NEIGHBORS) — для «держания курса».
_NB_UNIT = [(dy / mul, dx / mul) for dy, dx, mul in _NEIGHBORS]


def _mode_corridor_pref(wnorm, wmode):
    """Предпочтение соседа по ВЕСУ клетки (тепловая карта), зависит от РЕЖИМА. «Пол» у
    каждого режима не даёт вероятности занулиться (маршрут не вырождается в один коридор)."""
    if wmode == "max":                        # приоритет тяжёлых коридоров (реки/дороги)
        return 0.10 + 1.6 * wnorm
    if wmode == "min":                        # приоритет лёгких («пустоши», одинокая река)
        return 0.10 + 1.6 * (1.0 - wnorm)
    return 0.30 + 0.8 * wnorm                 # medium: вероятность ∝ весу


def _catmull_rom(points, seg_km=1.5, alpha=0.5):
    """ИНТЕРПОЛЯЦИЯ ломаной гладкой кривой (центростремительный сплайн Катмулла–Рома):
    кривая ПРОХОДИТ через опорные точки, острые углы сетки становятся плавными ДУГАМИ, а
    прямые участки (коллинеарные точки) остаются ПРЯМЫМИ. Центростремительная параметризация
    (alpha=0.5) не даёт «петель»/выбросов на резких поворотах — то, что нужно для трасс БПЛА
    (плавно «дугами и прямыми», приближение манёвра по R_min)."""
    P = np.asarray(points, float)
    if len(P) < 3:
        return P
    pts = np.vstack([P[0], P, P[-1]])                # дублируем концы (для крайних сегментов)
    out = [P[0]]
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]

        def _t(ti, a, b):
            return ti + max(float(np.hypot(*(b - a))), 1e-6) ** alpha

        t0 = 0.0; t1 = _t(t0, p0, p1); t2 = _t(t1, p1, p2); t3 = _t(t2, p2, p3)
        seg_len = float(np.hypot(*(p2 - p1)))
        n = int(np.clip(seg_len / seg_km, 2, 24))    # число точек на сегмент ∝ его длине
        for t in np.linspace(t1, t2, n, endpoint=False):
            a1 = (t1 - t) / (t1 - t0) * p0 + (t - t0) / (t1 - t0) * p1
            a2 = (t2 - t) / (t2 - t1) * p1 + (t - t1) / (t2 - t1) * p2
            a3 = (t3 - t) / (t3 - t2) * p2 + (t - t2) / (t3 - t2) * p3
            b1 = (t2 - t) / (t2 - t0) * a1 + (t - t0) / (t2 - t0) * a2
            b2 = (t3 - t) / (t3 - t1) * a2 + (t - t1) / (t3 - t1) * a3
            out.append((t2 - t) / (t2 - t1) * b1 + (t - t1) / (t2 - t1) * b2)
    out.append(P[-1])
    return np.asarray(out)


def _rdp(points, eps):
    """Упрощение ломаной (Ramer–Douglas–Peucker): убирает точки ближе eps к хорде — снимает
    мелкие зигзаги «на месте». Итеративно (без рекурсии), на numpy."""
    pts = np.asarray(points, float)
    n = len(pts)
    if n < 3:
        return pts
    keep = np.zeros(n, bool); keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        a = pts[i0]; b = pts[i1]; ab = b - a; L2 = float(ab @ ab)
        seg = pts[i0 + 1:i1]
        if L2 < 1e-9:
            dd = np.hypot(seg[:, 0] - a[0], seg[:, 1] - a[1])
        else:
            t = np.clip(((seg - a) @ ab) / L2, 0.0, 1.0)
            proj = a + t[:, None] * ab
            dd = np.hypot(seg[:, 0] - proj[:, 0], seg[:, 1] - proj[:, 1])
        k = int(np.argmax(dd))
        if dd[k] > eps:
            idx = i0 + 1 + k; keep[idx] = True
            stack.append((i0, idx)); stack.append((idx, i1))
    return pts[keep]


def _poly_len_km(poly):
    """Длина ломаной (км) — сумма длин звеньев. Нужна для проверки «уложились в запас хода»."""
    d = np.diff(np.asarray(poly, float), axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def _cells_yx_to_km(grid, cells_yx):
    """Список ячеек (y,x) -> точки-центры (M,2) в км."""
    a = np.asarray(cells_yx, float)
    xs = grid.ox + (a[:, 1] + 0.5) * grid.h
    ys = grid.oy + (a[:, 0] + 0.5) * grid.h
    return np.column_stack([xs, ys])


# ─── ИЗВЕСТНЫЕ ОГРАНИЧЕНИЯ / ЗАДЕЛ НА БУДУЩЕЕ (движение маршрута) ────────────────
#  Модель ходит ПО ЯЧЕЙКАМ 8-связной сетки, поэтому маршрут по своей природе не вполне
#  «бпла-подобный»: остаются мелкие зигзаги (8 фиксированных направлений), возможны
#  локальные возвраты назад (back_allow) и не учитывается настоящая кинематика (радиус
#  разворота R_min, скорость, инерция). Пост-обработка (RDP + Катмулл-Ром) это сглаживает
#  ВИЗУАЛЬНО, но не физически.
#  Направление развития (не реализовано): планирование сразу в непрерывном пространстве
#  с учётом R_min — кривые Дубинса (Dubins) / деревья RRT* по costmap, либо сглаживание
#  с жёстким ограничением кривизны. Тогда «длина прямого участка» и радиус разворота
#  станут настоящими кинематическими ограничениями, а не приближением.
# ────────────────────────────────────────────────────────────────────────────────
def _walk_field(ctx, dfield, start, goal, budget, turn_interval_km, rng,
                heading0=None, wmode="medium", visited=None):
    """Стохастический спуск по полю расстояний dfield от start к goal (надёжно приходит):
    выбор коридора — по ВЕСУ соседа (тепловая карта, приоритет по режиму wmode) и развилкам.

    ПРЯМОЛИНЕЙНОСТЬ: `turn_interval_km` теперь трактуется как ЦЕЛЕВАЯ длина ПРЯМОГО участка —
    маршрут держит курс, пока не пролетит ~столько км, и только потом доворачивает (БПЛА
    стремится лететь прямыми отрезками этой длины). БЕЗ «КРУЖЕНИЯ»: посещённые ячейки не
    выбираются повторно (кроме тупика), «назад» по полю разрешено чуть-чуть — петли уходят.

    `visited` — общий набор пройденных ячеек (flat-индексы); для маршрута через VIA
    передаётся сквозь оба участка, чтобы они не наматывались друг на друга.
    Возвращает (список ячеек (y,x), длина_км, курс) или None (тупик / вышли за budget)."""
    grid = ctx["grid"]; pas = ctx["pas"]; wnorm = ctx["wnorm"]
    ny, nx = dfield.shape
    h = grid.h
    if not np.isfinite(dfield[start]):
        return None
    # ЦЕЛЕВАЯ длина прямого участка задаётся пользователем, но в самом блуждании «держим
    # курс» лишь мягко и не дольше L_hold (жёсткая длинная прямая роняет доходимость до цели,
    # особенно на маршрутах через VIA). Итоговую ПРЯМОЛИНЕЙНОСТЬ даёт пост-обработка
    # (упрощение линии с допуском ∝ длине участка + сглаживание), см. sample_one_route.
    L_hold = min(max(1.0, float(turn_interval_km)), 6.0)
    back_allow = 0.8                                   # км «назад» по полю (иначе тупики у стенок)
    if visited is None:
        visited = set()
    step_cap = int(6 * dfield[start] / h + 80)
    cur = start
    visited.add(cur[0] * nx + cur[1])
    path = [cur]
    heading = heading0
    dist_since_turn = L_hold                            # на старте курс можно выбрать свободно
    route_len = 0.0
    for _ in range(step_cap):
        if cur == goal or dfield[cur] <= h:
            break
        cy, cx = cur
        cand, score = [], []
        for k, (dy, dx, mul) in enumerate(_NEIGHBORS):
            vy, vx = cy + dy, cx + dx
            if vy < 0 or vy >= ny or vx < 0 or vx >= nx or not pas[vy, vx]:
                continue
            dj = dfield[vy, vx]
            if not np.isfinite(dj) or dj > dfield[cur] + back_allow:   # к цели, почти без «назад»
                continue
            uy, ux = _NB_UNIT[k]
            if heading is None:
                hf = 1.0
            else:
                dot = uy * heading[0] + ux * heading[1]
                if dist_since_turn < L_hold:            # держим курс (мягко — меньше зигзагов)
                    if dot > 0.9:    hf = 14.0          # прямо — сильнее всего
                    elif dot > 0.7:  hf = 2.5           # небольшой доворот — можно
                    elif dot > 0.4:  hf = 0.5           # 45° — если нужно объехать
                    else:            hf = 0.05          # резкий поворот — редко
                else:                                   # прошли прямой участок — можно менять курс
                    hf = (0.05 if dot < -0.2            # назад почти не разворачиваемся
                          else 1.0 + 2.5 * max(dot, 0.0))   # но прямо всё равно охотнее
            prog = dfield[cur] - dj
            corr = _mode_corridor_pref(wnorm[vy, vx], wmode)   # приоритет по весу (режим)
            s = float(np.exp(prog / 0.4)) * corr * hf
            if vy * nx + vx in visited:                 # штраф за повторный заход — гасит петли,
                s *= 0.12                               # но не запрещает (иначе тупики)
            if s <= 0.0:
                continue
            cand.append((vy, vx, mul, (uy, ux))); score.append(s)
        if not cand:
            return None
        # РАЗВИЛКА — «метод рулетки»: вероятность выбрать соседа ∝ его оценке score.
        # cumsum даёт нарастающие границы секторов, searchsorted находит сектор, в который
        # попала случайная точка на [0, сумма). Быстрее и без аллокаций rng.choice(p=...).
        cum = np.cumsum(np.asarray(score, float))
        j = min(int(np.searchsorted(cum, rng.random() * cum[-1])), len(cand) - 1)
        vy, vx, mul, unit = cand[j]
        step = mul * h
        route_len += step
        if route_len > budget:
            return None
        new_head = (round(unit[0], 3), round(unit[1], 3))
        dist_since_turn = (dist_since_turn + step
                           if heading is not None and new_head == heading else step)
        heading = new_head
        cur = (vy, vx)
        visited.add(vy * nx + vx)
        path.append(cur)
    return (path, route_len, heading) if (cur == goal or dfield[cur] <= h) else None


def _nearest_passable_cell(pas, grid, y_km, x_km, max_km=12.0):
    """Ближайшая проходимая ячейка к точке (км). None, если ничего в радиусе max_km."""
    iy = min(max(int(np.floor((y_km - grid.oy) / grid.h)), 0), grid.ny - 1)
    ix = min(max(int(np.floor((x_km - grid.ox) / grid.h)), 0), grid.nx - 1)
    if pas[iy, ix]:
        return (iy, ix)
    ys, xs = np.nonzero(pas)
    if len(ys) == 0:
        return None
    d2 = (ys - iy) ** 2 + (xs - ix) ** 2
    j = int(np.argmin(d2))
    r = int(round(max_km / grid.h))
    return (int(ys[j]), int(xs[j])) if d2[j] <= r * r else None


def _seg_dist_km(pt, a, b):
    """Расстояние от точки pt до ОТРЕЗКА a-b (км). Для сортировки via по «дальности обхода»."""
    p = np.asarray(pt, float); a = np.asarray(a, float); b = np.asarray(b, float)
    ab = b - a; L2 = float(ab @ ab)
    t = 0.0 if L2 < 1e-9 else float(np.clip(((p - a) @ ab) / L2, 0.0, 1.0))
    return float(np.hypot(*(p - (a + t * ab))))


def build_iter_context(grid, entry_km, target_km, max_gap_km, n_grid=(4, 5)):
    """Подготовить (один раз, кэшируется) контекст выборки. VIA-точки — СЕТКА по всей
    проходимой области (все стороны, включая юг/восток и «за целью» — заход в Б с ЛЮБОГО
    направления, если хватает хода), с предвычисленными полями расстояний. Каждой via
    сопоставлена «дальность обхода» (расстояние до отрезка вход→цель) — для режима разброса.
    Порог мостика через провал = ШАГ СЕТКИ ДАТЧИКА (passable_mask). Не зависит от L_max."""
    start = _cell_of(grid, entry_km)
    goal = _cell_of(grid, target_km)
    pas = _open_endpoints(passable_mask(grid, max_gap_km), (start, goal), grid)
    gt = _dijkstra_dist(pas, goal, grid.h)              # поле расстояний до цели (тяга)
    w = np.clip(grid.weight, 0.0, None)                 # нормировка веса по p90 (как в стоимости)
    pos = w[w > 0]
    ref = float(np.percentile(pos, 90)) if pos.size else 1.0
    wnorm = np.clip(w / max(ref, 1e-6), 0.0, 1.0)
    A = np.asarray(entry_km, float); B = np.asarray(target_km, float)
    ab = float(np.hypot(*(B - A))) or 1.0
    via_cell, via_gv, via_segd = [], [], []
    if np.isfinite(gt[start]):
        ys, xs = np.nonzero(pas & np.isfinite(gt))     # достижимые проходимые ячейки
        if len(ys):
            gy = np.linspace(ys.min(), ys.max(), n_grid[0])
            gx = np.linspace(xs.min(), xs.max(), n_grid[1])
            for cy in gy:
                for cx in gx:
                    vc = _nearest_passable_cell(pas, grid,
                                                grid.oy + (cy + 0.5) * grid.h,
                                                grid.ox + (cx + 0.5) * grid.h, max_km=18.0)
                    if vc is None or vc in via_cell or vc == goal or vc == start:
                        continue
                    vxk = grid.ox + (vc[1] + 0.5) * grid.h
                    vyk = grid.oy + (vc[0] + 0.5) * grid.h
                    via_cell.append(vc)
                    via_segd.append(_seg_dist_km((vxk, vyk), A, B))
                    via_gv.append(_dijkstra_dist(pas, vc, grid.h))
    return dict(grid=grid, pas=pas, gt=gt, wnorm=wnorm, start=start, goal=goal, ab=ab,
                via_cell=via_cell, via_gv=via_gv, via_segd=np.asarray(via_segd),
                gt_start=float(gt[start]), reachable=bool(np.isfinite(gt[start])))


def _pick_via(ctx, spread, rng):
    """Выбрать индекс VIA по режиму РАЗБРОСА (по дальности обхода segd): center — близкие к
    прямой, edge — дальние (юг/восток/за целью), middle — средние, mix — любая. None = прямо."""
    segd = ctx["via_segd"]
    n = len(segd)
    if n == 0:
        return None
    sp = ("center", "middle", "edge")[rng.integers(3)] if spread == "mix" else spread
    order = np.argsort(segd)                           # от близких к прямой к дальним
    if sp == "center":
        pool = order[:max(1, n // 3)]
    elif sp == "edge":
        pool = order[-max(1, n // 2):]                 # дальние (включая юг/восток/за B)
    else:                                              # middle
        lo, hi = n // 4, max(n // 4 + 1, 3 * n // 4)
        pool = order[lo:hi]
    return int(pool[rng.integers(len(pool))])


def sample_one_route(ctx, mode, L_max, turn_interval_km, rng, spread="mix"):
    """ОДИН стохастический маршрут вход→цель. mode = приоритет клетки по ВЕСУ
    (max/medium/min/mix). spread = РАЗБРОС по карте (center/middle/edge/mix) — через какую
    VIA-точку строить (center — вдоль прямой, edge — дальний обход/заход с любой стороны).
    None, если не уложились в запас хода."""
    if not ctx["reachable"]:
        return None
    wmode = ("max", "medium", "min")[rng.integers(3)] if mode == "mix" else mode
    grid = ctx["grid"]
    j = None if (spread == "center" and rng.random() < 0.5) else _pick_via(ctx, spread, rng)
    if j is None:                                       # прямой маршрут (через центр)
        r = _walk_field(ctx, ctx["gt"], ctx["start"], ctx["goal"], L_max,
                        turn_interval_km, rng, wmode=wmode)
        cells = r[0] if r else None
    else:                                               # через VIA (обход/заход со стороны)
        via = ctx["via_cell"][j]
        r1 = _walk_field(ctx, ctx["via_gv"][j], ctx["start"], via, L_max,
                         turn_interval_km, rng, wmode=wmode)   # каждый участок — свой набор visited
        if r1 is None:
            return None
        r2 = _walk_field(ctx, ctx["gt"], via, ctx["goal"], L_max - r1[1],
                         turn_interval_km, rng, heading0=r1[2], wmode=wmode)
        if r2 is None:
            return None
        cells = r1[0] + r2[0][1:]                        # склейка без дубля via
    if cells is None or len(cells) < 2:
        return None
    # ПРЯМОЛИНЕЙНОСТЬ ∝ «длине прямого участка»: чем она больше, тем крупнее допуск упрощения
    # (RDP) -> тем длиннее прямые звенья и тем прямее маршрут. Затем интерполяция Катмулла–Рома
    # делает повороты плавными ДУГАМИ, а прямые оставляет прямыми (плавно «дугами и прямыми»).
    eps = float(np.clip(turn_interval_km * 0.18, grid.h * 1.2, 8.0))
    poly = _catmull_rom(_rdp(_cells_yx_to_km(grid, cells), eps=eps))
    return poly if _poly_len_km(poly) <= L_max * 1.15 else None
