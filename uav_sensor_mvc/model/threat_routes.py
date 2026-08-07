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

from config import (THREAT_LOOKAHEAD_KM, THREAT_LOOKAHEAD_POWER, THREAT_COST_POWER,
                    THREAT_COST_RELIEF, THREAT_COST_MIX_BIAS, THREAT_COST_MIX_STEPS,
                    THREAT_WEIGHT_GAIN,
                    THREAT_DEM_K_MIN, THREAT_DEM_K_MAX)

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


def _cost_mul(grid, wnorm, power, relief_power=0.0):
    """Множитель цены прохода через ячейку — ПРОИЗВЕДЕНИЕ двух независимых сомножителей:

        (1 + power · (1 − вес))  ×  (1 + relief_power · (1 − рельеф))

    Оба в [1, 1+сила]: дёшево там, где есть ориентир И где низко; дорого в пустоте и
    на гребне. Цена шага = длина × этот множитель, поэтому стоимостное поле остаётся
    В КИЛОМЕТРАХ-эквивалентах и «температура» tau сохраняет прежний смысл.

    ПОЧЕМУ НЕ ОДИН МНОЖИТЕЛЬ. Сперва привлекательность считалась как произведение
    `вес × рельеф`, обрезанное единицей, — и обрезка съедала поощрение за низину: река
    в пойме (вес 1.0, рельеф 1.4) давала 1.4 → обрезалось до 1.0, ровно столько же,
    сколько у той же реки на голом плато. Рельеф работал лишь как ослабление веса, хотя
    это НЕЗАВИСИМАЯ физика: вес — «где есть ориентиры», рельеф — «где меня видно».

    Рельеф нормируется по СВОЕМУ диапазону [k_min, k_max] → 0 (гребень) … 1 (низина),
    поэтому `relief_power` не зависит от того, как настроены пороги высот.
    Обе силы 0 -> None, то есть чистая геометрия, как было."""
    if power <= 0.0 and relief_power <= 0.0:
        return None
    mul = np.ones_like(np.asarray(wnorm, float))
    if power > 0.0:
        mul = mul * (1.0 + float(power) * (1.0 - np.clip(np.asarray(wnorm, float), 0.0, 1.0)))
    if relief_power > 0.0:
        k = grid.relief_k() if hasattr(grid, "relief_k") else None
        if k is not None:
            lo, hi = float(THREAT_DEM_K_MIN), float(THREAT_DEM_K_MAX)
            kn = np.clip((np.asarray(k, float) - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
            mul = mul * (1.0 + float(relief_power) * (1.0 - kn))
    return mul


def _dijkstra_cost(passable, start, h, mul):
    """Поле СТОИМОСТИ пути от start (в км-эквивалентах) по проходимым ячейкам.

    Цена шага — его длина, умноженная на среднее значение `mul` в двух ячейках: так
    цена симметрична (из A в B столько же, сколько из B в A) и не зависит от того, с
    какой стороны считали. `mul = None` -> обычные километры."""
    if mul is None:
        return _dijkstra_dist(passable, start, h)
    ny, nx = passable.shape
    m = np.asarray(mul, float).ravel()
    dist = np.full(ny * nx, np.inf)
    si = start[0] * nx + start[1]
    dist[si] = 0.0
    pq = [(0.0, si)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u]:
            continue
        uy, ux = divmod(u, nx)
        for dy, dx, step in _NEIGHBORS:
            vy, vx = uy + dy, ux + dx
            if vy < 0 or vy >= ny or vx < 0 or vx >= nx or not passable[vy, vx]:
                continue
            v = vy * nx + vx
            nd = d + step * h * 0.5 * (m[u] + m[v])
            if nd < dist[v]:
                dist[v] = nd
                heapq.heappush(pq, (nd, v))
    return dist.reshape(ny, nx)


def passable_mask(grid, max_gap_km, slack_km=None):
    """Проходимые ячейки для маршрута: коридоры (вес>0), СШИТЫЕ через провалы ≤ max_gap_km,
    плюс буфер УХОДА ОТ ОРИЕНТИРА slack_km, МИНУС город и МИНУС снятое отсечкой рельефа.

    ДВА ПАРАМЕТРА, А НЕ ОДИН. Раньше одна дилатация делала обе работы сразу: сшивала
    разорванные коридоры И разрешала маршруту отклоняться вбок от реки/дороги (буфер
    получался равен половине разрыва). Настроить одно, не испортив другое, было нельзя —
    чтобы дать маршруту место для обхода холма, приходилось увеличивать перелёты через
    пустоту. Теперь сшивка — это ЗАМЫКАНИЕ (дилатация + эрозия): провалы короче разрыва
    закрываются, а ширина самого коридора не растёт; буфер задаётся отдельно.

    slack_km=None -> прежнее поведение (буфер = разрыв / 2). Провал > max_gap_km не
    перекрывается -> коридор там разорван (маршрут идёт в обход или не строится)."""
    from .threat_grid import _dilate, _erode
    corridor = grid.weight > 0
    r_gap = max(0, int(round(0.5 * max_gap_km / grid.h)))       # мостик ≤ 2·r_gap ячеек
    r_slack = r_gap if slack_km is None else max(0, int(round(slack_km / grid.h)))
    pas = corridor
    if r_gap > 0:
        # `| corridor` — страховка: замыкание по определению не теряет ячеек, но дешевле
        # гарантировать это явно, чем ловить краевой эффект на прижатых к рамке коридорах
        pas = _erode(_dilate(corridor, r_gap), r_gap) | corridor
    if r_slack > 0:
        pas = _dilate(pas, r_slack)
    else:
        pas = pas.copy()
    pas &= ~grid.urban_mask()                                  # город (кроме рек) непроходим
    # ОТСЕЧКА РЕЛЬЕФА — такой же запрет, как город, и вычитается ПОСЛЕ буфера.
    # Иначе правило «очень высокая гора — коридора нет» отменялось само собой: отсечка
    # снимает вес, а идущая выше дилатация возвращает эти ячейки в проходимые, и маршрут
    # спокойно летел поверх горы. Город исключался явно, гора — нет.
    cut = grid.relief_cut() if hasattr(grid, "relief_cut") else None
    if cut is not None:
        pas &= ~np.asarray(cut, bool)
    return pas


def _open_endpoints(pas, cells_yx, grid, radius_km=3.0):
    """Открыть проходимость в окрестности ~radius_km вокруг заданных ячеек (вход/цель),
    чтобы они «дотянулись» до коридорной сети (БПЛА проходит несколько км над полем до
    реки/дороги на входе/выходе). Меняет pas на месте, возвращает его же."""
    rc = max(1, int(round(radius_km / grid.h)))
    for cy, cx in cells_yx:
        pas[max(0, cy - rc):cy + rc + 1, max(0, cx - rc):cx + rc + 1] = True
    return pas


def flight_envelope(grid, entry_km, target_km, L_max, max_gap_km, slack_km=None):
    """ВСЕ ВОЗМОЖНЫЕ МЕСТА ПРОЛЁТА при запасе хода L_max. Ячейка попадает в «возможные
    места», если существует путь вход→ячейка→цель по коридорам (с мостиками ≤ max_gap)
    суммарной длиной ≤ L_max:

        envelope = { ячейка : glen_вход(ячейка) + glen_цель(ячейка) ≤ L_max }

    Так учитываются и ДЛИННЫЕ обходы (зайти с другой стороны) — лишь бы уложиться в
    запас хода. Возвращает (mask_возможных_ячеек, min_длина_вход→цель)."""
    start = _cell_of(grid, entry_km)
    goal = _cell_of(grid, target_km)
    pas = _open_endpoints(passable_mask(grid, max_gap_km, slack_km), (start, goal), grid)
    ge = _dijkstra_dist(pas, start, grid.h)
    gt = _dijkstra_dist(pas, goal, grid.h)
    total = ge + gt
    env = np.isfinite(total) & (total <= L_max)
    dmin = float(ge[goal]) if np.isfinite(ge[goal]) else float("inf")
    return env, dmin


def count_possible_routes(env, gt, entry_cell, goal_cell):
    """СКОЛЬКО ВСЕГО маршрутов вход→цель существует внутри области залёта — точное число,
    БЕЗ их перечисления.

    Маршрут — это путь по дереву развилок: из ячейки можно шагнуть в любого соседа, который
    ближе к цели (в среднем таких ~3 — то самое «ветвление на 3 линии»). Число листьев
    такого дерева считается динамическим программированием от цели наружу:

        N(цель) = 1,   N(ячейка) = Σ N(соседей, которые ближе к цели)

    Порядок обхода — по убыванию расстояния до цели, поэтому каждая ячейка считается один
    раз (граф ациклический: шаг всегда сокращает расстояние). Сложность O(ячеек).

    ЗАЧЕМ: показать, из чего сделана выборка. На реальных данных таких маршрутов ~1e107 —
    их нельзя ни перечислить, ни нарисовать (атомов во Вселенной ~1e80). Поэтому «все
    возможные пролёты» показывает ЗАКРАШЕННАЯ ОБЛАСТЬ (она точна), а маршруты — выборка.
    Возвращает float («бесконечность» тут невозможна, но число огромно — int переполнил бы
    смысл, а float даёт порядок)."""
    ny, nx = gt.shape
    ok = env & np.isfinite(gt)
    ys, xs = np.nonzero(ok)
    if len(ys) == 0 or not ok[goal_cell] or not ok[entry_cell]:
        return 0.0
    order = np.argsort(gt[ys, xs])                     # от цели наружу
    N = np.zeros((ny, nx), float)
    N[goal_cell] = 1.0
    for i in order:
        cy, cx = int(ys[i]), int(xs[i])
        if (cy, cx) == goal_cell:
            continue
        tot = 0.0
        d = gt[cy, cx]
        for dy, dx, _mul in _NEIGHBORS:
            vy, vx = cy + dy, cx + dx
            if 0 <= vy < ny and 0 <= vx < nx and ok[vy, vx] and gt[vy, vx] < d:
                tot += N[vy, vx]
        N[cy, cx] = tot
    return float(N[entry_cell])


# --- ИТЕРАЦИОННАЯ (стохастическая) генерация множества маршрутов ---------------
# 8 единичных векторов направлений (в порядке _NEIGHBORS) — для «держания курса».
_NB_UNIT = [(dy / mul, dx / mul) for dy, dx, mul in _NEIGHBORS]


def _mode_corridor_pref(wnorm, wmode):
    """Предпочтение соседа по ВЕСУ клетки (тепловая карта), зависит от РЕЖИМА. «Пол» у
    каждого режима не даёт вероятности занулиться (маршрут не вырождается в один коридор).

    Размах задаётся `THREAT_WEIGHT_GAIN`: это ТАКТИКА (какую из соседних клеток взять),
    тогда как стоимостное поле — стратегия (куда идти вообще). При прежнем значении 1.6
    размах был ×17 и тонул на фоне удержания курса (×280) и приближения к цели (до
    ×10¹³) — сам по себе, без стоимости, он не влиял вовсе (замер: вес под маршрутом
    11.60 при ×17 и 11.64 при ×101)."""
    g = float(THREAT_WEIGHT_GAIN)
    if wmode == "max":                        # приоритет тяжёлых коридоров (реки/дороги)
        return 0.10 + g * wnorm
    if wmode == "min":                        # приоритет лёгких («пустоши», одинокая река)
        return 0.10 + g * (1.0 - wnorm)
    return 0.30 + 0.5 * g * wnorm             # medium: вероятность ∝ весу


def _lookahead_fields(grid, look_km):
    """Средний множитель РЕЛЬЕФА впереди по каждому из 8 направлений: `ahead[d][y,x]` —
    каково будет дальше, если шагнуть из (y,x) в направлении d.

    ЗАЧЕМ. Горизонт решения в блуждании — одна ячейка (500 м), а тяга к цели считается по
    чистой геометрии (`_dijkstra_dist` не знает ни весов, ни высот). Из-за этого узкий холм
    маршрут обтекал, а перед широким массивом упирался: локальное нежелание лезть вверх
    пересиливалось тягой к цели. Поле `ahead` поднимает горизонт до look_km, и отворот
    начинается заранее.

    СРЕДНЕЕ, А НЕ МИНИМУМ: минимум блокировал бы направление из-за одной плохой клетки,
    среднее даёт плавный отворот. За краем массива клетки не учитываются, пустое
    направление даёт нейтральную единицу.

    Стоимость: 8 направлений × L сдвигов массива 189×151 — доли миллисекунды, считается
    один раз на контекст выборки. None — рельеф не применён, механизм не участвует."""
    k = grid.relief_k() if hasattr(grid, "relief_k") else None
    if k is None or look_km <= 0.0:
        return None
    k = np.asarray(k, float)
    ny, nx = k.shape
    out = []
    for dy, dx, mul in _NEIGHBORS:
        L = max(1, int(round(look_km / (grid.h * mul))))     # диагональный шаг длиннее
        acc = np.zeros((ny, nx)); cnt = np.zeros((ny, nx))
        for i in range(1, L + 1):
            sy, sx = dy * i, dx * i
            y0, y1 = max(0, -sy), min(ny, ny - sy)           # куда пишем
            x0, x1 = max(0, -sx), min(nx, nx - sx)
            if y0 >= y1 or x0 >= x1:
                break
            acc[y0:y1, x0:x1] += k[y0 + sy:y1 + sy, x0 + sx:x1 + sx]
            cnt[y0:y1, x0:x1] += 1.0
        out.append(np.where(cnt > 0.0, acc / np.maximum(cnt, 1.0), 1.0))
    return out


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
#
#  РАСХОД ЗАПАСА ХОДА (реализовано, см. `_spend_tau`): маршрут больше не летит «вслепую».
#  На каждом шаге известен СВОБОДНЫЙ ЗАПАС slack = остаток_хода − кратчайший_путь_до_цели.
#  Профиль (config.THREAT_SPEND_LABELS) решает, когда его тратить: `late` — виляет, пока
#  запас есть, на исходе идёт прямо; `early` — наоборот; `even` — равномерно.
# ────────────────────────────────────────────────────────────────────────────────
_TAU_FREE = 0.40        # «температура» выбора соседа при БОЛЬШОМ свободном запасе (виляет)
_TAU_TIGHT = 0.05       # при исчерпанном запасе (идёт кратчайшим путём к цели)
_SLACK_FULL_KM = 10.0   # запас, при котором маршрут считается полностью свободным

# ФИНАЛЬНЫЙ ПОДХОД: ближе этого расстояния до цели маршрут больше НЕ УДАЛЯЕТСЯ от неё.
# Виляние вбок и обход по дуге при этом разрешены — заход на цель с другой стороны это
# нормальный манёвр. Запрещено именно «отвернул и пошёл назад» в нескольких километрах
# от объекта (замер: 19 % маршрутов, подойдя на 15 км, снова удалялись до 5.3 км).
FINAL_APPROACH_KM = 10.0
# Сколько в зоне подхода разрешено отойти от цели ЗА ОДИН ШАГ. Ноль сделал бы обход
# препятствия у объекта невозможным (маршрут упирался бы в тупик), четверть километра
# при шаге сетки 0.5 км позволяет обогнуть помеху, но не развернуться.
FINAL_DRIFT_KM = 0.25
# ТРАНЗИТ МИМО ЦЕЛИ. Маршрут, идущий к VIA за целью, не должен приближаться к объекту
# ближе этого радиуса: иначе он пролетает цель насквозь, отходит на пару километров и
# возвращается — на карте это «хвост», торчащий из цели. Обход по дуге — то же «зайти с
# другой стороны», но физически осмысленно. Действует ТОЛЬКО на участке, ведущем к такой
# VIA (см. `bypass_km` в `_walk_field`); заход в саму цель, понятно, разрешён.
# Величина: заметно шире «клевка» (в базе маршрут уходил за цель на 2.3 км), но втрое
# меньше запаса хода на крюк. Замер по радиусам 3/4/5/6 км — 4 км даёт лучший вес под
# выборкой при той же доле заходов сзади. 0 — правило выключено.
FINAL_BYPASS_KM = 4.0
# Проверялось и отвергнуто: отдельный лимит «топтания» у цели — сколько пути в зоне
# подхода разрешено пройти, не сокращая расстояние до объекта. Механизм оказался мёртвым:
# значения 0.5, 1.0 и 3.0 км давали побайтово одинаковую выборку. Запрета удаляться
# (FINAL_DRIFT_KM) достаточно — накопить и полкилометра без прогресса маршрут не успевает,
# а «отворот перпендикулярно» с карты заказчика был транзитом мимо цели, а не заходом.
# Насколько маршруту позволено уходить ПОЗАДИ СТАРТА (проекция на ось старт→цель).
# Немного назад в начале — нормально: БПЛА уходит от точки вылета и ищет коридор.
# Дальше — уже «полетел не туда»: на карте это видно как полосу в обратную сторону.
START_BACK_KM = 1.0
VIA_BEHIND_KM = 1.0     # VIA-точка не должна лежать позади старта дальше этого
# За цель заходить МОЖНО: облететь объект и зайти с другой стороны — штатный манёвр.
VIA_BEYOND_KM = 10.0
# VIA, попавшая вплотную ЗА цель, ОТОДВИГАЕТСЯ на рубеж подхода (FINAL_APPROACH_KM), а не
# отбрасывается. На реальной сетке такая точка вставала в 2.5 км за объектом — и, будучи
# ближайшей к прямой вход→цель, выглядела для алгоритма «самой центральной»: через неё шло
# 29 % маршрутов, каждый пролетал цель насквозь и возвращался. Отодвинутая точка даёт тот
# же заход с обратной стороны, но нормальной дугой. False — прежнее поведение.
VIA_BEYOND_PUSH = True


def _spend_tau(slack_km, slack0_km, frac_done, profile):
    """«Температура» выбора соседа: чем она меньше, тем жёстче маршрут идёт к цели.

    slack_km  — СВОБОДНЫЙ ЗАПАС сейчас (км, которые ещё можно потратить на виляние);
    slack0_km — свободный запас в начале маршрута (сколько его было всего);
    frac_done — доля ВСЕГО маршрута, уже пройденная (0..1);
    profile   — из config.THREAT_SPEND_LABELS.

    Свобода вилять = (запас ещё есть) × (профиль разрешает вилять именно сейчас).
    Запас кончился — идём прямо при любом профиле (на остатке топлива не петляют).

      late  — виляет, пока запас есть (профиль не ограничивает);
      early — в начале держит курс, виляет ближе к цели (свобода растёт с frac_done);
      even  — тратит запас РАВНОМЕРНО: сверяется с «планом расхода» (к доле пути f должна
              быть истрачена доля f запаса). Идём с опережением плана — виляем свободнее;
              перерасход — выпрямляемся."""
    by_slack = min(1.0, max(0.0, slack_km) / _SLACK_FULL_KM)
    if profile == "early":              # виляем ближе к цели: в начале держим курс
        by_phase = frac_done
    elif profile == "even":             # держимся плана равномерного расхода
        spent_frac = ((slack0_km - slack_km) / slack0_km) if slack0_km > 1e-6 else 1.0
        by_phase = min(1.0, max(0.0, 0.5 + 3.0 * (frac_done - spent_frac)))
    else:                               # "late" (по умолчанию): виляем, пока запас есть
        by_phase = 1.0
    return _TAU_TIGHT + (_TAU_FREE - _TAU_TIGHT) * by_slack * by_phase


def _walk_field(ctx, dfield, start, goal, budget, turn_interval_km, rng,
                heading0=None, wmode="medium", visited=None, spend="late",
                done0_km=0.0, tail_km=0.0, dgeo=None, fscale=1.0, bypass_km=0.0):
    """Стохастический спуск по полю расстояний dfield от start к goal (надёжно приходит):
    выбор коридора — по ВЕСУ соседа (тепловая карта, приоритет по режиму wmode) и развилкам.

    ЗАПАС ХОДА расходуется осмысленно (`spend`, см. `_spend_tau`): пока свободный запас
    (остаток − кратчайший путь до цели) велик, маршрут виляет по коридорам; когда тает —
    выпрямляется и идёт к цели. Если дойти уже нельзя (остаток < кратчайшего пути) —
    обрываемся сразу, не тратя шаги. Раньше маршрут вилял «вслепую», проедал весь запас и
    отбраковывался у самой цели: на реальных данных доходило ~5 % попыток, стало ~90 %.

    ПРЯМОЛИНЕЙНОСТЬ: `turn_interval_km` — ЦЕЛЕВАЯ длина ПРЯМОГО участка: маршрут держит курс,
    пока не пролетит ~столько км, и только потом доворачивает. БЕЗ «КРУЖЕНИЯ»: посещённые
    ячейки штрафуются, «назад» по полю разрешено чуть-чуть — петли уходят.

    `visited` — общий набор пройденных ячеек (flat-индексы); для маршрута через VIA
    передаётся сквозь оба участка, чтобы они не наматывались друг на друга.

    `done0_km` / `tail_km` — участок знает своё место во ВСЁМ маршруте: сколько км уже
    пройдено ДО него и сколько минимум предстоит ПОСЛЕ (для второго участка через VIA это
    0). Без этого профиль расхода считал бы фазу пути от начала КАЖДОГО участка, и маршрут
    через VIA дважды «начинал сначала».

    Возвращает (список ячеек (y,x), длина_км, курс) или None (тупик / не хватило хода)."""
    grid = ctx["grid"]; pas = ctx["pas"]; wnorm = ctx["wnorm"]
    axis_s = ctx.get("axis_s")          # проекция ячеек на ось старт→цель (км)
    dgoal = ctx.get("dgoal")            # ПРЯМОЕ расстояние ячеек до цели (км)
    ahead = ctx.get("ahead")            # средний рельеф впереди по каждому направлению
    look_p = float(THREAT_LOOKAHEAD_POWER)   # 0 -> предвидение выключено
    ny, nx = dfield.shape
    h = grid.h
    # СПУСК идёт по `dfield` (стоимость, если она включена), а ЗАПАС ХОДА считается по
    # `dgeo` — честным километрам. Смешивать нельзя: иначе «дорогой» обход выглядел бы
    # как нехватка топлива и маршрут обрывался бы там, где физически проходит.
    if dgeo is None:
        dgeo = dfield
    d_start = dgeo[start]
    if not np.isfinite(d_start) or d_start > budget:    # цель недостижима / не хватает хода
        return None
    if not np.isfinite(dfield[start]):                  # по стоимости пути тоже нет
        return None
    # свободный запас ВСЕГО маршрута в его начале (сколько км можно потратить на виляние)
    slack0 = max(0.0, (budget + done0_km) - (d_start + tail_km + done0_km))
    # ЦЕЛЕВАЯ длина прямого участка задаётся пользователем, но в самом блуждании «держим
    # курс» лишь мягко и не дольше L_hold (жёсткая длинная прямая роняет доходимость до цели,
    # особенно на маршрутах через VIA). Итоговую ПРЯМОЛИНЕЙНОСТЬ даёт пост-обработка
    # (упрощение линии с допуском ∝ длине участка + сглаживание), см. sample_one_route.
    L_hold = min(max(1.0, float(turn_interval_km)), 6.0)
    # МАСШТАБ ПОЛЯ. `dfield` может быть стоимостным, а величины ниже заданы в КИЛОМЕТРАХ:
    # «сколько можно назад», порог «цель достигнута» и «температура» tau. Стоимостное
    # поле крупнее геометрического в среднем в `fscale` раз, и без пересчёта эти пороги
    # молча ужимались во столько же раз — стохастика падала, и вся выборка сваливалась
    # в один оптимум (см. журнал п. 154). Приводим их к единицам поля.
    fscale = max(float(fscale), 1e-6)
    back_allow = 0.8 * fscale                          # «назад» по полю (иначе тупики у стенок)
    reach_eps = h * fscale                             # порог «дошли до цели» в единицах поля
    if visited is None:
        visited = set()
    step_cap = int(6 * d_start / h + 80)
    cur = start
    visited.add(cur[0] * nx + cur[1])
    path = [cur]
    heading = heading0
    dist_since_turn = L_hold                            # на старте курс можно выбрать свободно
    route_len = 0.0
    # ТРАНЗИТ МИМО ЦЕЛИ: участок ведёт к VIA ЗА целью, и приближаться к объекту ближе
    # радиуса обхода ему незачем — иначе маршрут протыкает цель и возвращается. Радиус
    # задаёт вызывающий (`bypass_km`), потому что правило нужно только на таких участках;
    # поля до этих VIA считаются по той же урезанной маске (см. `pas_via`). Если участок
    # стартует внутри круга, правило не включаем — первый же шаг был бы в тупик.
    bypass = 0.0
    if (bypass_km > 0.0 and tail_km > 1e-9 and dgoal is not None
            and dgoal[start] >= bypass_km and dgoal[goal] >= bypass_km):
        bypass = float(bypass_km)
    for _ in range(step_cap):
        if cur == goal or dfield[cur] <= reach_eps:
            break
        # СВОБОДНЫЙ ЗАПАС: сколько км ещё можно потратить на виляние, оставшись в бюджете.
        # Отрицательный — дойти уже нельзя, обрываемся сразу (не жжём шаги впустую).
        # Считается по КИЛОМЕТРАМ (`dgeo`), а не по стоимости: это про топливо.
        slack = (budget - route_len) - dgeo[cur]
        if slack < 0.0:
            return None
        # доля ВСЕГО маршрута, уже пройденная (а не только текущего участка)
        done = done0_km + route_len
        remain = dgeo[cur] + tail_km
        frac_done = done / (done + remain) if (done + remain) > 1e-9 else 1.0
        # «температура» задана в км/шаг — переводим в единицы поля тем же масштабом,
        # иначе на стоимостном поле она означала бы вчетверо меньшую свободу выбора
        tau = _spend_tau(slack, slack0, frac_done, spend) * fscale
        # ФИНАЛЬНЫЙ ПОДХОД: рядом с целью запрещаем шаг ОТ неё (см. FINAL_APPROACH_KM).
        # Идти вбок и обходить по дуге по-прежнему можно — заход с другой стороны это
        # нормальный манёвр; нельзя именно «отвернуть и уйти назад» у самого объекта.
        # tail_km — остаток пути ПОСЛЕ участка: на первом участке через VIA цель ещё
        # далеко, там подход включать рано.
        in_final = (tail_km <= 1e-9 and dgoal is not None
                    and dgoal[cur] <= FINAL_APPROACH_KM)
        allow_back = 0.0 if in_final else back_allow
        cy, cx = cur
        cand, score = [], []
        for k, (dy, dx, mul) in enumerate(_NEIGHBORS):
            vy, vx = cy + dy, cx + dx
            if vy < 0 or vy >= ny or vx < 0 or vx >= nx or not pas[vy, vx]:
                continue
            dj = dfield[vy, vx]
            if not np.isfinite(dj) or dj > dfield[cur] + allow_back:   # к цели, почти без «назад»
                continue
            if axis_s is not None and axis_s[vy, vx] < -START_BACK_KM:
                continue                           # не уходить назад за точку вылета
            if in_final and dgoal[vy, vx] > dgoal[cur] + FINAL_DRIFT_KM:
                continue                           # у цели не отворачиваем от неё
            if bypass > 0.0 and dgoal[vy, vx] < bypass:
                continue                           # мимо цели идём в обход, а не сквозь неё
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
            if ahead is not None and look_p > 0.0:
                # ЧТО ЖДЁТ ДАЛЬШЕ по этому курсу: холм впереди снижает привлекательность
                # направления заранее, за километры, а не за одну клетку до подножия
                corr *= float(ahead[k][cy, cx]) ** look_p
            # tau мал (запас на исходе) -> exp резко выделяет соседа с макс. прогрессом к цели
            s = float(np.exp(prog / tau)) * corr * hf
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
        sc = np.asarray(score, float)
        if not np.isfinite(sc).all() or sc.sum() <= 0.0:
            j = int(np.argmax(np.nan_to_num(sc, posinf=np.finfo(float).max)))   # exp переполнился
        else:                                           # при tau->0: берём лучшего соседа
            cum = np.cumsum(sc)
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
    return (path, route_len, heading) if (cur == goal or dfield[cur] <= reach_eps) else None


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


def build_iter_context(grid, entry_km, target_km, max_gap_km, n_grid=(4, 5),
                       slack_km=None):
    """Подготовить (один раз, кэшируется) контекст выборки. VIA-точки — СЕТКА по всей
    проходимой области (все стороны, включая юг/восток и «за целью» — заход в Б с ЛЮБОГО
    направления, если хватает хода), с предвычисленными полями расстояний. Каждой via
    сопоставлены:
      * `via_segd`  — «дальность обхода» (расстояние до отрезка вход→цель), для разброса;
      * `via_minlen` — МИНИМАЛЬНАЯ длина пути вход→via→цель. Маршрут через via физически
        возможен, только если via_minlen ≤ запаса хода. Раньше это не проверялось, и
        половина via была заведомо недостижима — каждая попытка через них гарантированно
        проваливалась (см. `_pick_via`);
      * `via_togoal` — кратчайший путь via→цель: столько хода надо ОСТАВИТЬ на второй участок.

    Порог мостика через провал = `max_gap_km` (разрыв между весовыми секторами, из
    интерфейса). Контекст зависит от разрыва и от цели, но НЕ от запаса хода L_max —
    поэтому кэшируется и переиспользуется при смене L_max."""
    start = _cell_of(grid, entry_km)
    goal = _cell_of(grid, target_km)
    pas = _open_endpoints(passable_mask(grid, max_gap_km, slack_km), (start, goal), grid)
    ahead = _lookahead_fields(grid, THREAT_LOOKAHEAD_KM)   # что ждёт впереди по курсу
    w = np.clip(grid.weight, 0.0, None)                 # нормировка веса по p90 (как в стоимости)
    pos = w[w > 0]
    ref = float(np.percentile(pos, 90)) if pos.size else 1.0
    wnorm = np.clip(w / max(ref, 1e-6), 0.0, 1.0)
    # ДВА ПОЛЯ РАССТОЯНИЙ ДО ЦЕЛИ, потому что вопросов два:
    #   gt_geo — честные километры: «хватит ли запаса хода» (физику подменять нельзя);
    #   gt     — стоимость в км-эквивалентах: «куда выгоднее идти» (по ней идёт спуск).
    # При THREAT_COST_POWER = 0 второе совпадает с первым — прежнее поведение.
    mul = _cost_mul(grid, wnorm, THREAT_COST_POWER, THREAT_COST_RELIEF)
    gt_geo = _dijkstra_dist(pas, goal, grid.h)
    gt = gt_geo if mul is None else _dijkstra_cost(pas, goal, grid.h, mul)
    # ВО СКОЛЬКО РАЗ стоимостное поле крупнее геометрического. По нему приводятся к
    # единицам поля пороги, заданные в километрах («температура» tau, допуск «назад»,
    # порог «дошли»). Считаем по ПРОХОДИМЫМ ячейкам — остальные к делу не относятся.
    if mul is None:
        cost_scale = 1.0
    else:
        m_ok = np.asarray(mul, float)[pas]
        cost_scale = float(m_ok.mean()) if m_ok.size else 1.0
    # НАБОР ПОЛЕЙ «жадности»: от чистой геометрии до полной стоимости. Каждое считается
    # СВОЕЙ Дейкстрой по множителю `1 + a·(mul − 1)`, поэтому остаётся корректным полем
    # расстояний — в отличие от линейной смеси готовых полей, где появляются локальные
    # минимумы-ловушки и маршрут не доходит.
    gt_mix = []
    if mul is not None and THREAT_COST_MIX_BIAS > 0.0:
        m = np.asarray(mul, float)
        for a in THREAT_COST_MIX_STEPS:
            if a <= 0.0:
                fld, sc = gt_geo, 1.0
            elif a >= 1.0:
                fld, sc = gt, cost_scale
            else:
                ma = 1.0 + a * (m - 1.0)
                fld = _dijkstra_cost(pas, goal, grid.h, ma)
                sc = float(ma[pas].mean()) if pas.any() else 1.0
            gt_mix.append(dict(a=float(a), gt=fld, scale=sc, via=[]))
    A = np.asarray(entry_km, float); B = np.asarray(target_km, float)
    ab = float(np.hypot(*(B - A))) or 1.0
    # Проекция каждой ячейки на ось старт→цель (км): 0 у старта, ab у цели, отрицательная —
    # ПОЗАДИ старта; и ПРЯМОЕ расстояние ячейки до цели. Именно последнее видно на карте:
    # расстояние по коридорам (`gt`) может быть вдвое больше, и по нему зона подхода
    # срабатывала не там, где кажется глазу.
    gxc, gyc = grid.cell_centers_km()
    axis_s = ((gxc - A[0]) * (B[0] - A[0]) + (gyc - A[1]) * (B[1] - A[1])) / ab
    dgoal = np.hypot(gxc - B[0], gyc - B[1])
    # МАСКА ТРАНЗИТА — только для VIA ЗА ЦЕЛЬЮ. По ней считаются поля до таких точек: путь
    # к ним ведёт мимо объекта, и приближаться к нему вплотную незачем (то же правило, что
    # в `_walk_field`). Считать по ней поля ДО ВСЕХ VIA нельзя: обычным маршрутам она
    # закрывает коридоры у самой цели, и вес под выборкой падает на 12 %.
    pas_via = pas
    push_km = FINAL_APPROACH_KM if VIA_BEYOND_PUSH else 0.0   # 0 — не отодвигать (как было)
    if FINAL_BYPASS_KM > 0.0 and float(dgoal[start]) >= FINAL_BYPASS_KM:
        pas_via = pas & (dgoal >= FINAL_BYPASS_KM)
        # VIA за целью ставится ВНЕ круга обхода, с зазором на дискретизацию сетки: точка
        # ровно на границе попадала бы в ячейку, куда шаг уже запрещён.
        if VIA_BEYOND_PUSH:
            push_km = FINAL_BYPASS_KM + 2.0 * grid.h
    via_cell, via_gv, via_gv_geo = [], [], []
    via_segd, via_minlen, via_togoal, via_back = [], [], [], []
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
                    # VIA не должна лежать ПОЗАДИ старта или ЗА целью: маршрут через
                    # такую точку сперва уходит в сторону, обратную цели, либо пролетает
                    # мимо цели и возвращается — на карте это выглядит как «полетел не
                    # туда». Небольшой заступ допустим (обход препятствия у самой точки).
                    s_axis = float((np.array([vxk, vyk]) - A) @ (B - A)) / ab
                    if s_axis < -VIA_BEHIND_KM or s_axis > ab + VIA_BEYOND_KM:
                        continue
                    # VIA рядом с целью, но НЕ за ней, заставляет маршрут отвернуть у
                    # самого объекта и вернуться — на карте это «дёрнулся в сторону в
                    # пяти километрах от цели». Заход ЗА цель — штатный манёвр, но и он
                    # должен быть ОБХОДОМ: точка вплотную за объектом (на реальной сетке
                    # 2.5 км) заставляет маршрут пролететь цель насквозь и вернуться.
                    # Такую VIA отодвигаем по лучу от цели на рубеж подхода — заход
                    # остаётся, «клевок» пропадает (см. VIA_BEYOND_PUSH).
                    d_via_goal = float(np.hypot(vxk - B[0], vyk - B[1]))
                    if d_via_goal < FINAL_APPROACH_KM and s_axis <= ab:
                        continue
                    if d_via_goal < push_km and s_axis > ab:
                        # Точка ЗА целью, но вплотную к ней: отодвигаем по лучу от цели за
                        # круг обхода (с зазором на дискретизацию сетки), чтобы маршрут
                        # огибал объект, а не протыкал его.
                        d = np.array([vxk - B[0], vyk - B[1]], float)
                        n = float(np.hypot(*d))
                        d = d / n if n > 1e-6 else (B - A) / ab   # точно на оси — вдоль неё
                        out = B + d * push_km
                        vc2 = _nearest_passable_cell(pas_via, grid, out[1], out[0], max_km=12.0)
                        if vc2 is None or vc2 in via_cell or vc2 == goal or vc2 == start:
                            continue
                        vc = vc2
                        vxk = grid.ox + (vc[1] + 0.5) * grid.h
                        vyk = grid.oy + (vc[0] + 0.5) * grid.h
                        s_axis = float((np.array([vxk, vyk]) - A) @ (B - A)) / ab
                    # два поля ОТ этой via: геометрия — для бюджета, стоимость — для спуска.
                    # Для точки ЗА ЦЕЛЬЮ — по маске транзита (обход объекта), для остальных
                    # по обычной: им закрывать коридоры у цели незачем.
                    back = bool(s_axis > ab and pas_via is not pas)
                    pmask = pas_via if back else pas
                    if not pmask[vc]:
                        continue                       # сама точка внутри круга обхода
                    gv_geo = _dijkstra_dist(pmask, vc, grid.h)
                    gv = gv_geo if mul is None else _dijkstra_cost(pmask, vc, grid.h, mul)
                    if not np.isfinite(gv_geo[start]):
                        continue                       # в обход цели до неё не добраться
                    via_cell.append(vc)
                    via_back.append(back)
                    via_gv.append(gv)
                    via_gv_geo.append(gv_geo)
                    # то же для каждой «жадности» — иначе первый участок через via шёл бы
                    # по другой логике, чем второй, и повадка маршрута ломалась посередине
                    for f in gt_mix:
                        if f["a"] <= 0.0:
                            f["via"].append(gv_geo)
                        elif f["a"] >= 1.0:
                            f["via"].append(gv)
                        else:
                            f["via"].append(_dijkstra_cost(
                                pmask, vc, grid.h,
                                1.0 + f["a"] * (np.asarray(mul, float) - 1.0)))
                    via_segd.append(_seg_dist_km((vxk, vyk), A, B))
                    # «сколько хода оставить на второй участок» и «уложимся ли вообще» —
                    # это КИЛОМЕТРЫ. Путь via→цель берём из поля ОТ ЦЕЛИ (`gt_geo`): оно
                    # считано по полной маске, а поле от via — по маске транзита, где сама
                    # цель вырезана кругом обхода и расстояние до неё было бы бесконечным.
                    via_togoal.append(float(gt_geo[vc]))
                    via_minlen.append(float(gv_geo[start] + gt_geo[vc]))
    return dict(grid=grid, pas=pas, gt=gt, gt_geo=gt_geo, wnorm=wnorm,
                cost_scale=cost_scale, gt_mix=gt_mix, start=start, goal=goal, ab=ab,
                axis_s=axis_s, dgoal=dgoal, ahead=ahead,
                via_cell=via_cell, via_gv=via_gv, via_gv_geo=via_gv_geo,
                via_back=np.asarray(via_back, bool), via_segd=np.asarray(via_segd),
                via_minlen=np.asarray(via_minlen), via_togoal=np.asarray(via_togoal),
                gt_start=float(gt_geo[start]),
                reachable=bool(np.isfinite(gt_geo[start])))


def _pick_via(ctx, spread, rng, L_max):
    """Выбрать индекс VIA по режиму РАЗБРОСА (по дальности обхода segd): center — близкие к
    прямой, edge — дальние (юг/восток/за целью), middle — средние, mix — любая. None = прямо.

    Сначала отбрасываем VIA, ЧЕРЕЗ КОТОРЫЕ НЕ ХВАТИТ ХОДА: путь вход→via→цель не короче
    `via_minlen`, и если это больше L_max — маршрут через неё невозможен, сколько ни
    пробуй. Раньше такие via разыгрывались наравне с остальными (на реальных данных —
    половина всех точек), и каждая попытка через них была гарантированным отказом."""
    segd = ctx["via_segd"]
    if len(segd) == 0:
        return None
    minlen = ctx["via_minlen"]
    ok = np.nonzero(np.isfinite(minlen) & (minlen <= L_max))[0]   # достижимые при этом ходе
    if len(ok) == 0:
        return None                                    # ни одна via не по силам — только прямой
    sp = ("center", "middle", "edge")[rng.integers(3)] if spread == "mix" else spread
    order = ok[np.argsort(segd[ok])]                   # от близких к прямой к дальним
    n = len(order)
    if sp == "center":
        pool = order[:max(1, n // 3)]
    elif sp == "edge":
        pool = order[-max(1, n // 2):]                 # дальние (включая юг/восток/за B)
    else:                                              # middle
        lo, hi = n // 4, max(n // 4 + 1, 3 * n // 4)
        pool = order[lo:hi]
        if len(pool) == 0:                             # мало via — берём что есть
            pool = order
    return int(pool[rng.integers(len(pool))])


def _off_corridor(poly, grid, pas):
    """Сколько точек линии вышло за ПРОХОДИМУЮ зону (город / провал шире разрыва)."""
    ix = np.clip(((poly[:, 0] - grid.ox) / grid.h).astype(np.int64), 0, grid.nx - 1)
    iy = np.clip(((poly[:, 1] - grid.oy) / grid.h).astype(np.int64), 0, grid.ny - 1)
    return int((~pas[iy, ix]).sum())


def _smooth_safe(raw, grid, pas, eps0):
    """Сгладить ломаную, НЕ ВЫВОДЯ её из проходимого коридора.

    Упрощение (RDP) с большим допуском спрямляет маршрут, но срезает углы — и линия
    может уйти туда, куда БПЛА не летит (плотная застройка, провал шире разрыва).
    Раньше так и было: сама ломаная по ячейкам всегда лежала в коридоре, а СГЛАЖЕННАЯ
    кривая — та, что рисуется и по которой считаются датчики, — срезала углы через город.

    Поэтому допуск подбирается: пробуем заданный, и пока кривая режет запретное —
    уменьшаем вдвое (углы срезаются меньше, линия жмётся к расчётной). В пределе
    возвращаем сглаживание по опорным точкам сетки — оно из коридора не выходит.

    Порядок проверок выбран из соображений скорости: упрощение (RDP) дёшево, сплайн —
    дорого, поэтому сначала отбраковываем по упрощённой ломаной и только уцелевшую
    доводим сплайном."""
    eps = float(eps0)
    for _ in range(4):
        simp = _rdp(raw, eps=eps)
        if _off_corridor(simp, grid, pas) == 0:    # дёшево: ломаная уже режет запретное?
            poly = _catmull_rom(simp)
            if _off_corridor(poly, grid, pas) == 0:
                return poly
        eps *= 0.5
    poly = _catmull_rom(raw)                       # без упрощения: кривая идёт по ячейкам
    return poly if _off_corridor(poly, grid, pas) == 0 else raw


def sample_one_route(ctx, mode, L_max, turn_interval_km, rng, spread="mix", spend="late"):
    """ОДИН стохастический маршрут вход→цель. mode = приоритет клетки по ВЕСУ
    (max/medium/min/mix). spread = РАЗБРОС по карте (center/middle/edge/mix) — через какую
    VIA-точку строить (center — вдоль прямой, edge — дальний обход/заход с любой стороны).
    spend = профиль РАСХОДА ЗАПАСА ХОДА (late/early/even, см. `_spend_tau`).
    None, если не уложились в запас хода."""
    if not ctx["reachable"]:
        return None
    wmode = ("max", "medium", "min")[rng.integers(3)] if mode == "mix" else mode
    if spend == "mix":                                  # веер всех повадок (для «возможных путей»)
        spend = ("late", "early", "even")[rng.integers(3)]
    # «ЖАДНОСТЬ» ЭТОГО маршрута: какое из предрассчитанных полей взять — от чисто
    # геометрического (летит напрямик) до полностью стоимостного (жмётся к коридорам и
    # низинам). Одного поля мало: если путь A дешевле B на десятки единиц, локальная
    # стохастика туда не уведёт, и вся выборка сваливается в один оптимум.
    fields = ctx.get("gt_mix")
    if fields:
        u = float(rng.random()) ** float(THREAT_COST_MIX_BIAS)
        fi = min(int(u * len(fields)), len(fields) - 1)
        fmix, gt_use = fields[fi]["scale"], fields[fi]["gt"]
        via_use = fields[fi]["via"]
    else:
        fmix, gt_use, via_use = ctx.get("cost_scale", 1.0), ctx["gt"], ctx["via_gv"]
    grid = ctx["grid"]
    j = (None if (spread == "center" and rng.random() < 0.5)
         else _pick_via(ctx, spread, rng, L_max))
    if j is None:                                       # прямой маршрут (через центр)
        r = _walk_field(ctx, gt_use, ctx["start"], ctx["goal"], L_max,
                        turn_interval_km, rng, wmode=wmode, spend=spend,
                        dgeo=ctx.get("gt_geo"), fscale=fmix)
        cells = r[0] if r else None
    else:                                               # через VIA (обход/заход со стороны)
        via = ctx["via_cell"][j]
        togoal = float(ctx["via_togoal"][j])
        # На участок via→цель надо ОСТАВИТЬ хода не меньше кратчайшего пути оттуда, иначе
        # первый участок проест весь запас и второй гарантированно не дойдёт.
        budget1 = L_max - togoal
        if budget1 <= 0.0:
            return None
        # tail_km/done0_km: участки знают своё место во всём маршруте — иначе профиль
        # расхода отсчитывал бы фазу пути заново от каждого из них.
        gv_geo = ctx.get("via_gv_geo")
        g1 = gv_geo[j] if gv_geo else None
        f1 = via_use[j] if (via_use and j < len(via_use)) else ctx["via_gv"][j]
        # VIA ЗА ЦЕЛЬЮ: участок к ней обходит объект по дуге (тот же радиус, по которому
        # считалось поле), а не протыкает его насквозь. Для остальных точек правило не
        # нужно — им у цели делать нечего и без запрета.
        vb = ctx.get("via_back")
        back = bool(vb is not None and len(vb) > j and vb[j])
        r1 = _walk_field(ctx, f1, ctx["start"], via, budget1,
                         turn_interval_km, rng, wmode=wmode, spend=spend,
                         done0_km=0.0, tail_km=togoal, dgeo=g1, fscale=fmix,
                         bypass_km=(FINAL_BYPASS_KM if back else 0.0))
        if r1 is None:                                  # каждый участок — свой набор visited
            return None
        r2 = _walk_field(ctx, gt_use, via, ctx["goal"], L_max - r1[1],
                         turn_interval_km, rng, heading0=r1[2], wmode=wmode, spend=spend,
                         done0_km=r1[1], tail_km=0.0, dgeo=ctx.get("gt_geo"), fscale=fmix)
        if r2 is None:
            return None
        cells = r1[0] + r2[0][1:]                        # склейка без дубля via
    if cells is None or len(cells) < 2:
        return None
    # ПРЯМОЛИНЕЙНОСТЬ ∝ «длине прямого участка»: чем она больше, тем крупнее допуск упрощения
    # (RDP) -> тем длиннее прямые звенья и тем прямее маршрут. Затем интерполяция Катмулла–Рома
    # делает повороты плавными ДУГАМИ, а прямые оставляет прямыми (плавно «дугами и прямыми»).
    # Допуск подбирается так, чтобы сглаженная кривая НЕ вышла из коридора (см. _smooth_safe).
    eps = float(np.clip(turn_interval_km * 0.18, grid.h * 1.2, 8.0))
    poly = _smooth_safe(_cells_yx_to_km(grid, cells), grid, ctx["pas"], eps)
    # Запас хода — строго: длина маршрута не может его превышать (сглаживание маршрут
    # только СПРЯМЛЯЕТ, т.е. укорачивает, поэтому прежний допуск ×1.15 был лишним).
    return poly if _poly_len_km(poly) <= L_max else None
