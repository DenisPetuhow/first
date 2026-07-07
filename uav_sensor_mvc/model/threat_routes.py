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


def plan_routes(grid, entry_km, target_km, n_routes, base_cost, weight_scale,
                urban_cost=200.0):
    """Список маршрутов (каждый — (M,2) км) от входа к цели. Первый — самый «тяжёлый»
    коридор; далее альтернативы (через штраф уже пройденных ячеек).

    Город — не жёсткий запрет, а БОЛЬШАЯ доп. стоимость (urban_cost): маршрут сильно
    обходит населённые пункты, но если вход/цель заданы В городе — путь всё равно
    существует (иначе, окружив старт застройкой, мы получили бы «маршрут не найден»)."""
    w = np.clip(grid.weight, 0.0, None)
    wmax = float(w.max()) if w.size else 0.0
    wnorm = w / wmax if wmax > 0 else np.zeros_like(w)
    base = base_cost + weight_scale * (1.0 - wnorm)            # (ny,nx) базовая стоимость
    base = base + urban_cost * grid.urban_mask()              # мягкий обход городов
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
