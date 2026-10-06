# -*- coding: utf-8 -*-
"""
MODEL · Доезд по дорогам — правило места «дорога» (МОГ, план 13.3).

Место типа — только там, куда он доезжает по дорогам от объектов защиты, и не у рек и болот. Всё по
ячейкам сетки: дороги — растр слоёв (`grid._present`), доезд — Дейкстра, запрет — реки и болота ± отступ.
Разбор — `теория/код/ДОСТУП_ПО_ДОРОГАМ.md`; решения и замеры — план 13.3 §2, §3.

⚠️ РЕКИ И БОЛОТА ЗАПРЕЩАЮТ СТОЯТЬ, А НЕ ЕХАТЬ: дорога через реку в OSM — мост или брод. Рви полоса
«± отступ» и проезд — каждая река делила бы сеть дорог надвое.
"""
import heapq
import math

import numpy as np

DRIVE_LAYERS = ("road_major", "road_local", "track")   # по чему едет МОГ: дороги и лесные (В13.3.1)
RIVER_LAYERS = ("river",)                               # у чего стоять нельзя: реки и каналы, без ручьёв (В13.3.2)
# шаги к 8 соседям: строка, столбец, длина в ячейках (1 по стороне, √2 по диагонали)
_STEPS = tuple((dy, dx, math.hypot(dy, dx)) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)


def road_cells(grid, layers=DRIVE_LAYERS):
    """Ячейки, где есть линия слоя. Вход: ThreatGrid, имена слоёв. Отдаёт: (ny,nx) bool; слоя нет — нет и ячеек."""
    out = np.zeros((grid.ny, grid.nx), bool)         # есть ли линия в ячейке
    for name in layers:                              # слои по очереди
        if name in grid._present:                    # слой есть на карте
            out |= grid._present[name]
    return out


def sources(grid, roads, pts):
    """Откуда едет МОГ: ближайшая к каждой цели ячейка с дорогой. Вход: ThreatGrid, дороги (ny,nx), цели [(x,y)] км.
    Отдаёт: (истоки [(строка, столбец)], от цели до дороги [км]); дорог нет — два пустых списка."""
    ry, rx = np.nonzero(roads)                       # ячейки с дорогой: строки, столбцы
    if not len(ry):                                  # дорог на карте нет
        return [], []                                # — ехать не по чему
    cx = grid.ox + (rx + 0.5) * grid.h               # центры ячеек дорог по x, км
    cy = grid.oy + (ry + 0.5) * grid.h               # и по y, км
    src, gap = [], []                                # истоки и сколько до них от цели, км
    for x, y in pts:                                 # объекты защиты
        j = int(np.argmin((cx - x) ** 2 + (cy - y) ** 2))   # номер ближайшей ячейки дороги
        src.append((int(ry[j]), int(rx[j])))
        gap.append(float(math.hypot(cx[j] - x, cy[j] - y)))
    return src, gap


def road_distance(roads, src, h):
    """Карта доступности — Дейкстра по ячейкам дорог, 8 соседей. Вход: дороги (ny,nx), истоки [(строка, столбец)],
    ячейка км. Отдаёт: (ny,nx) км по дорогам от ближайшего истока; не доехать — inf."""
    ny, nx = roads.shape                             # размер сетки: строк, столбцов
    road = roads.tolist()                            # списком: в цикле быстрее, чем индекс numpy
    best = [[math.inf] * nx for _ in range(ny)]      # км по дорогам; inf — не доехать
    heap = []                                        # очередь: (км, строка, столбец)
    for iy, ix in src:                               # истоки — ячейки дорог у целей
        best[iy][ix] = 0.0
        heap.append((0.0, iy, ix))
    heapq.heapify(heap)
    # ⚠️ диагональ — без запрета «среза угла»: растр кладёт точки линии шагом 0.4·h, и дорога у угла ячейки
    # перескакивает по диагонали; с запретом от цели доезжало 37 % дорог arh, без него — 97 % (план 13.3 §3)
    while heap:                                      # ближайшая ещё не закрытая ячейка
        d, y, x = heapq.heappop(heap)                # км до неё, строка, столбец
        if d > best[y][x]:                           # устаревшая запись очереди
            continue                                 # — ячейку уже закрыли короче
        for dy, dx, k in _STEPS:                     # 8 соседей
            yy, xx = y + dy, x + dx                  # сосед: строка, столбец
            if 0 <= yy < ny and 0 <= xx < nx and road[yy][xx]:   # сосед в сетке и с дорогой
                nd = d + k * h                       # км до соседа через эту ячейку
                if nd < best[yy][xx]:                # путь короче известного
                    best[yy][xx] = nd
                    heapq.heappush(heap, (nd, yy, xx))
    return np.asarray(best, float)


def access_parts(grid, zc, st, passable, r_km, targets):
    """Слагаемые карты места «дорога» и итог `result`. Вход: ThreatGrid, сводка `sensor_zones.cells`, паспорт, проходимость
    (ny,nx), R км, цели [(x,y)] км. Отдаёт: dict (ny,nx) bool — roads, reach, river, wetland, ban, nopass, result; dist км; gap_km."""
    from model.threat_grid import _dilate
    roads = road_cells(grid)                         # по чему едет МОГ
    src, gap = sources(grid, roads, targets)         # ячейки дорог у целей и сколько до них, км
    dist = road_distance(roads, src, grid.h)         # км по дорогам от целей
    lim = float(getattr(st, "drive_km", 0.0) or 0.0)   # предел выезда, км; 0 — без предела
    reach = np.isfinite(dist)                        # куда доезжает
    if lim > 0:                                      # предел задан
        reach &= dist <= lim                         # — дальше него не едем
    river = road_cells(grid, RIVER_LAYERS)           # ячейки рек
    # ⚠️ болота — только свой слой `wetland`: в старом файле мест их нет отдельно, а `rough` (кустарник, пески) МОГ не мешает
    wet = zc.get("wetland", np.zeros_like(roads))    # болота
    nb = max(0, int(round(float(getattr(st, "wet_buf_km", 0.0) or 0.0) / grid.h)))   # отступ, ячеек
    ban = _dilate(river | wet, nb)                   # реки и болота ± отступ — стоять нельзя
    pas = np.asarray(passable, bool)                 # куда БПЛА может лететь
    nr = max(0, int(round(float(r_km or 0.0) / grid.h)))   # на сколько ячеек видно с места, R
    seen = _dilate(pas, nr) if nr > 0 else pas       # проходимое и не дальше R от него
    return dict(roads=roads, reach=reach, river=river, wetland=wet, ban=ban, nopass=~pas,
                result=reach & ~ban & seen, dist=dist, gap_km=gap)


def place_nodes(grid, step_km, mask):
    """Места типа «дорога»: в каждом блоке s×s ячеек (s = шаг / ячейка) — центр разрешённой ячейки, ближайшей к центру
    блока. Вход: ThreatGrid, шаг км, карта (ny,nx). Отдаёт: (M,2) км."""
    s = max(1, int(round(float(step_km) / grid.h)))  # сторона блока, ячеек
    iy, ix = np.nonzero(np.asarray(mask, bool))      # разрешённые ячейки: строки, столбцы
    if not len(iy):                                  # карта пуста
        return np.empty((0, 2))                      # — мест нет
    # ⚠️ блоками, а не узлами решётки, как у ПВН: узел шагом 1 км попадает в каждую 4-ю ячейку, и дорога
    # шириной в ячейку проходит между рядами узлов целиком
    by, bx = iy // s, ix // s                        # блок ячейки: строка, столбец
    off = (s - 1) / 2.0                              # центр блока от его угла, ячеек
    far = (iy - by * s - off) ** 2 + (ix - bx * s - off) ** 2   # удаление ячейки от центра блока, ячеек²
    key = by * (grid.nx // s + 1) + bx               # номер блока одним числом
    order = np.lexsort((far, key))                   # по блоку, внутри — от центра
    first = np.r_[True, key[order][1:] != key[order][:-1]]   # первая в блоке — ближайшая к центру
    pick = order[first]                              # выбранные ячейки
    return np.column_stack([grid.ox + (ix[pick] + 0.5) * grid.h, grid.oy + (iy[pick] + 0.5) * grid.h])


def road_graph(layers, names=DRIVE_LAYERS, tol_km=0.001):
    """Граф дорог по ВЕКТОРНЫМ линиям (план 13.5, 13.5.9): узел — вершина ломаной (совпавшие до `tol_km` — один узел,
    так сходятся перекрёстки OSM), ребро — отрезок. Вход: dict слой -> ломаные (M,2) км, имена слоёв, допуск км.
    Отдаёт: (узлы (N,2) км, CSR: начала (N+1,), соседи (E,), длины (E,) км); дорог нет — пустые массивы."""
    lines = [np.asarray(a, float)[:, :2] for n in names for a in layers.get(n, []) if len(a) >= 2]   # ломаные дорог, км
    if not lines:                                    # дорог на карте нет
        return np.zeros((0, 2)), np.zeros(1, np.int64), np.zeros(0, np.int64), np.zeros(0)
    pts = np.vstack(lines)                           # все вершины подряд, км
    own = np.repeat(np.arange(len(lines)), [len(a) for a in lines])   # номер ломаной у каждой вершины
    # ⚠️ ОДИН УЗЕЛ НА ТОЧКУ: на перекрёстке OSM у дорог общая вершина — округлив до метра, склеиваем её в один узел.
    # Без склейки граф распался бы на 12 тыс. отдельных ломаных, и от цели нельзя было бы уехать дальше своей дороги
    key = np.round(pts / tol_km).astype(np.int64)    # координаты в метрах (при допуске 1 м), целые
    _u, node = np.unique(key[:, 0] * 2_000_003 + key[:, 1], return_inverse=True)   # номер узла у каждой вершины
    nodes = np.zeros((len(_u), 2))                   # координаты узлов, км
    nodes[node] = pts
    same = own[1:] == own[:-1]                       # соседние вершины — из одной ломаной (отрезок)
    a, b = node[:-1][same], node[1:][same]           # концы отрезков — номера узлов
    ln = np.hypot(*(pts[1:][same] - pts[:-1][same]).T)   # длины отрезков, км
    ok = a != b                                      # отрезок короче допуска — петля в узле, выбросить
    src = np.concatenate([a[ok], b[ok]])             # ребро в обе стороны: откуда
    dst = np.concatenate([b[ok], a[ok]])             # куда
    w = np.concatenate([ln[ok], ln[ok]])             # длина, км
    order = np.argsort(src, kind="stable")           # рёбра — подряд по узлу-началу (CSR)
    start = np.searchsorted(src[order], np.arange(len(nodes) + 1))   # где начинаются рёбра узла
    return nodes, start, dst[order], w[order]


def reach_lines(layers, targets, lim_km=0.0, step_km=5.0, names=DRIVE_LAYERS):
    """Пути доезда МОГ от целей по ВЕКТОРНЫМ дорогам (13.5.9): дерево кратчайших путей и стрелки по ходу движения.
    Вход: dict слой -> ломаные км, цели [(x, y)] км, предел км (0 — без), шаг стрелок км, слои дорог.
    Отдаёт: (ломаные [(M,2) км] — от конца ветки к развилке, стрелки [(x, y, dx, dy)] км)."""
    nodes, start, nbr, w = road_graph(layers, names)   # граф дорог по линиям
    if not len(nodes) or not targets:                # ехать не по чему или не откуда
        return [], []
    best = np.full(len(nodes), np.inf)               # км по дорогам от ближайшей цели
    par = np.full(len(nodes), -1, np.int64)          # узел-родитель в дереве кратчайших путей
    heap = []                                        # очередь: (км, узел)
    for x, y in targets:                             # от каждой цели — ближайший узел дороги
        i = int(np.argmin(np.hypot(nodes[:, 0] - x, nodes[:, 1] - y)))   # исток у цели
        best[i] = 0.0
        heap.append((0.0, i))
    heapq.heapify(heap)
    st, nb, wl = start.tolist(), nbr.tolist(), w.tolist()   # списками: в цикле быстрее, чем индекс numpy
    while heap:                                      # ближайший ещё не закрытый узел
        d, u = heapq.heappop(heap)                   # км до него, узел
        if d > best[u]:                              # устаревшая запись очереди
            continue
        for e in range(st[u], st[u + 1]):            # рёбра узла
            v, nd = nb[e], d + wl[e]                 # сосед и км до него через u
            if nd < best[v] and (lim_km <= 0 or nd <= lim_km):   # короче известного и в пределе выезда
                best[v], par[v] = nd, u
                heapq.heappush(heap, (nd, v))
    lines, seen = [], np.zeros(len(nodes), bool)     # ломаные; узлы, уже вошедшие в ломаную
    for u in np.argsort(-best):                      # от дальних узлов: каждая ветка — с конца
        u = int(u)                                   # узел
        if not np.isfinite(best[u]) or par[u] < 0 or seen[u]:   # не доезжает, исток или уже нарисован
            continue
        path = [u]                                   # узлы ветки от конца к развилке
        while par[u] >= 0 and not seen[u]:           # вверх по дереву до цели или до нарисованного
            seen[u] = True
            u = int(par[u])
            path.append(u)
        lines.append(nodes[path])
    # СТРЕЛКИ — где путь пересекает очередные step км, по ходу движения от цели; ближе 0.6·step к уже поставленной —
    # не ставить (на развилках ветки пересекают отметку рядом)
    arrows, taken = [], {}                           # стрелки; занятые клетки решётки прореживания
    cell = 0.6 * float(step_km)                      # клетка прореживания, км
    for v in np.argsort(best):                       # от цели наружу
        v = int(v)                                   # узел
        u = int(par[v])                              # его родитель
        if u < 0 or not np.isfinite(best[v]) or np.floor(best[v] / step_km) <= np.floor(best[u] / step_km):   # нет отметки
            continue
        ax, ay = nodes[v]                            # место стрелки, км
        k = (int(ax // cell), int(ay // cell))       # клетка решётки
        near = [taken.get((k[0] + i, k[1] + j)) for i in (-1, 0, 1) for j in (-1, 0, 1)]   # соседние стрелки
        if any(q is not None and math.hypot(q[0] - ax, q[1] - ay) < cell for q in near):   # рядом уже есть
            continue
        taken[k] = (ax, ay)
        arrows.append((float(ax), float(ay), float(ax - nodes[u, 0]), float(ay - nodes[u, 1])))
    return lines, arrows
