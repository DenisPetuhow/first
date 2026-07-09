# -*- coding: utf-8 -*-
"""
MODEL · Построение маршрутов пролёта БПЛА по весовой карте угроз (вкладка 3).

Идея (запрос пользователя, п. 3в): по готовой весовой карте построить вероятные
линии пролёта от точки ВХОДА (у реки) к ЦЕЛИ, тяготеющие к «тяжёлым» коридорам
(реки/дороги/ЛЭП). Реализация — поиск путей минимальной СТОИМОСТИ по сетке, где
стоимость прохода ячейки тем МЕНЬШЕ, чем БОЛЬШЕ её вес:

    cost(ячейка) = base_cost + weight_scale × (1 − вес_норм)

  * `base_cost` (>0) даёт «мостик через провалы»: даже цепочка НУЛЕВЫХ ячеек между
    двумя тяжёлыми коридорами проходима, поэтому маршрут НЕ рвётся, если пара
    квадратов оказалась с нулевым весом (как и просил пользователь);
  * `weight_scale` задаёт, насколько сильно высокий вес удешевляет путь (притягивает
    маршрут к рекам/дорогам);
  * НАСЕЛЁННЫЕ ПУНКТЫ (urban_mask) для маршрута ЗАБЛОКИРОВАНЫ (над плотной застройкой
    не летят).

Несколько АЛЬТЕРНАТИВНЫХ коридоров получаем итеративно: найдя путь, штрафуем его
ячейки и ищем следующий — так набор путей получается разнообразным (это же — задел
под будущую рандомную генерацию маршрутов, см. методичку, Этап 3).

Алгоритм — Дейкстра на 8-связной сетке (heapq). Сетка ~28k ячеек, путей единицы —
доли секунды; тяжёлый расчёт всё равно идёт в фоновом потоке контроллера.
"""
import heapq
import numpy as np

# 8 соседей: (dy, dx, множитель длины шага)
_NEIGHBORS = [(-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
              (-1, -1, 1.41421356), (-1, 1, 1.41421356),
              (1, -1, 1.41421356), (1, 1, 1.41421356)]


def _cell_of(grid, xy):
    ix = int(np.floor((xy[0] - grid.ox) / grid.h))
    iy = int(np.floor((xy[1] - grid.oy) / grid.h))
    return (min(max(iy, 0), grid.ny - 1), min(max(ix, 0), grid.nx - 1))


def _dijkstra(cost, blocked, start, goal):
    """Кратчайший (по стоимости) путь на 8-связной сетке. Возвращает список индексов
    ячеек (flat) или None, если цель недостижима."""
    ny, nx = cost.shape
    n = ny * nx
    dist = np.full(n, np.inf)
    prev = np.full(n, -1, np.int64)
    si = start[0] * nx + start[1]
    gi = goal[0] * nx + goal[1]
    dist[si] = 0.0
    pq = [(0.0, si)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u]:
            continue
        if u == gi:
            break
        uy, ux = divmod(u, nx)
        for dy, dx, mul in _NEIGHBORS:
            vy, vx = uy + dy, ux + dx
            if vy < 0 or vy >= ny or vx < 0 or vx >= nx or blocked[vy, vx]:
                continue
            nd = d + mul * cost[vy, vx]
            v = vy * nx + vx
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    if not np.isfinite(dist[gi]):
        return None
    path, c = [], gi
    while c != -1:
        path.append(c)
        c = int(prev[c])
    path.reverse()
    return path


def _path_to_km(grid, path):
    xs, ys = [], []
    for c in path:
        iy, ix = divmod(c, grid.nx)
        xs.append(grid.ox + (ix + 0.5) * grid.h)
        ys.append(grid.oy + (iy + 0.5) * grid.h)
    return np.column_stack([xs, ys])


def _step_cost(grid, base_cost, weight_scale, urban_cost):
    """Стоимость прохода ячейки: тем меньше, чем больше вес. Нормировка по 90-му
    ПЕРЦЕНТИЛЮ (а не по максимуму): иначе один сверх-тяжёлый узел делает вес всех
    остальных ≈0, стоимость почти одинаковой -> путь идёт ПО ПРЯМОЙ, игнорируя
    коридоры. По p90 коридоры дёшевы, пустоши дороги -> путь идёт по тепловой карте."""
    w = np.clip(grid.weight, 0.0, None)
    pos = w[w > 0]
    ref = float(np.percentile(pos, 90)) if pos.size else 1.0
    wnorm = np.clip(w / max(ref, 1e-6), 0.0, 1.0)
    return (base_cost + weight_scale * (1.0 - wnorm)
            + urban_cost * grid.urban_mask())


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


def _chaikin(poly, iters=2):
    """Сглаживание углов (Chaikin): острые повороты сетки → плавные довороты. Служит
    приближением манёвра по радиусу разворота R_min (истинная кинематика — задел)."""
    p = np.asarray(poly, float)
    for _ in range(iters):
        if len(p) < 3:
            break
        q = [p[0]]
        for i in range(len(p) - 1):
            a, b = p[i], p[i + 1]
            q.append(0.75 * a + 0.25 * b)
            q.append(0.25 * a + 0.75 * b)
        q.append(p[-1])
        p = np.array(q)
    return p


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
    d = np.diff(np.asarray(poly, float), axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def _cells_yx_to_km(grid, cells_yx):
    """Список ячеек (y,x) -> точки-центры (M,2) в км."""
    a = np.asarray(cells_yx, float)
    xs = grid.ox + (a[:, 1] + 0.5) * grid.h
    ys = grid.oy + (a[:, 0] + 0.5) * grid.h
    return np.column_stack([xs, ys])


def _walk_field(ctx, dfield, start, goal, budget, turn_interval_km, rng,
                heading0=None, wmode="medium"):
    """Стохастический спуск по полю расстояний dfield от start к goal (надёжно приходит):
    выбор коридора — по ВЕСУ соседа (тепловая карта, приоритет по режиму wmode) и развилкам,
    курс держится ≥ turn_interval_km. Годится и для промежуточной цели (via) — отсюда обход.
    Возвращает (список ячеек (y,x), длина_км, курс) или None (тупик / вышли за budget)."""
    grid = ctx["grid"]; pas = ctx["pas"]; wnorm = ctx["wnorm"]
    ny, nx = dfield.shape
    h = grid.h
    if not np.isfinite(dfield[start]):
        return None
    step_cap = int(6 * dfield[start] / h + 80)
    cur = start
    path = [cur]
    heading = heading0
    dist_since_turn = turn_interval_km
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
            if not np.isfinite(dj) or dj > dfield[cur] + 0.8:   # назад — не дальше 0.8 км
                continue
            uy, ux = _NB_UNIT[k]
            if heading is None:
                hf = 1.0
            else:
                dot = uy * heading[0] + ux * heading[1]
                if dot < -0.2:
                    hf = 0.02
                elif dist_since_turn < turn_interval_km:   # держим курс СТРОГО (меньше зигзагов):
                    hf = 12.0 if dot > 0.9 else (0.4 if dot > 0.6 else 0.02)
                else:
                    hf = 1.0 + 2.5 * max(dot, 0.0)      # можно доворачивать, но прямо охотнее
            prog = dfield[cur] - dj
            corr = _mode_corridor_pref(wnorm[vy, vx], wmode)   # приоритет по весу (режим)
            s = float(np.exp(prog / 0.4)) * corr * hf
            cand.append((vy, vx, mul, (uy, ux)))
            score.append(s)
        if not cand:
            return None
        cum = np.cumsum(np.asarray(score, float))       # быстрый выбор ∝ весу (без rng.choice(p=))
        j = int(np.searchsorted(cum, rng.random() * cum[-1]))
        j = min(j, len(cand) - 1)
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
                         turn_interval_km, rng, wmode=wmode)
        if r1 is None:
            return None
        r2 = _walk_field(ctx, ctx["gt"], via, ctx["goal"], L_max - r1[1],
                         turn_interval_km, rng, heading0=r1[2], wmode=wmode)
        if r2 is None:
            return None
        cells = r1[0] + r2[0][1:]                        # склейка без дубля via
    if cells is None or len(cells) < 2:
        return None
    # RDP убирает мелкие зигзаги «на месте», затем Chaikin слегка сглаживает углы
    poly = _chaikin(_rdp(_cells_yx_to_km(grid, cells), eps=grid.h * 1.6), iters=1)
    return poly if _poly_len_km(poly) <= L_max * 1.05 else None


def iterate_routes(grid, entry_km, target_km, n_routes, mode, L_max,
                   turn_interval_km, max_gap_km, seed=None):
    """МНОЖЕСТВО вероятных маршрутов (итерационная модель, методичка §4.5) — пакетно.
    Каждый — стохастический проход вход→цель по коридорам: развилки выбираются случайно
    по весу коридора соседа (режим heavy/balanced/light/mix), курс держится между
    доворотами, длина ограничена запасом хода L_max. Возвращает список (M,2) км."""
    ctx = build_iter_context(grid, entry_km, target_km, max_gap_km)
    if not ctx["reachable"]:
        return []
    rng = np.random.default_rng(seed)
    routes, tries, cap = [], 0, max(1, int(n_routes)) * 6
    while len(routes) < int(n_routes) and tries < cap:
        tries += 1
        r = sample_one_route(ctx, mode, L_max, turn_interval_km, rng)
        if r is not None:
            routes.append(r)
    return routes


def plan_routes(grid, entry_km, target_km, n_routes, base_cost, weight_scale,
                urban_cost=200.0):
    """Список маршрутов (каждый — (M,2) км) от входа к цели. Первый — самый «тяжёлый»
    коридор; далее альтернативы (через штраф уже пройденных ячеек).

    Город — не жёсткий запрет, а БОЛЬШАЯ доп. стоимость (urban_cost): маршрут сильно
    обходит населённые пункты, но если вход/цель заданы В городе — путь всё равно
    существует (иначе, окружив старт застройкой, мы получили бы «маршрут не найден»)."""
    base = _step_cost(grid, base_cost, weight_scale, urban_cost)
    blocked = np.zeros(grid.weight.shape, bool)               # жёстких запретов нет

    start = _cell_of(grid, entry_km)
    goal = _cell_of(grid, target_km)
    penalty = np.ones_like(base)
    routes = []
    for _ in range(max(1, int(n_routes))):
        path = _dijkstra(base * penalty, blocked, start, goal)
        if path is None:
            break
        routes.append(_path_to_km(grid, path))
        for c in path:                                         # штраф коридора -> разнообразие
            iy, ix = divmod(c, grid.nx)
            penalty[max(0, iy - 1):iy + 2, max(0, ix - 1):ix + 2] *= 2.5
    return routes
