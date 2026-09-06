# -*- coding: utf-8 -*-
"""
MODEL · Сопоставление датчиков между ДВУМЯ построениями — «анализ размещения»
(задача 8.9, план 8). Новый механизм — свой модуль (правило проекта, CLAUDE.md).

⚠️ АЛГОРИТМ РАССТАНОВКИ ЭТИМ МОДУЛЕМ НЕ ЗАТРАГИВАЕТСЯ. Жадный выбор позиций как был,
так и остался: если заставить его тяготеть к прежним местам, это добавит задаче
ограничение, которого в ней нет, и снимет гарантию (1 − 1/e) — план 7, §2.1·0. Здесь
только ПОСЛЕ расстановки решается задача о НАЗНАЧЕНИЯХ: какой датчик НОВОГО построения
считать «тем же самым», что датчик СТАРОГО, — чтобы суммарное перемещение было
минимальным. Позиции не двигаются и не меняются, только рисуется стрелка старое→новое.

Почему это вообще нужно (план 8, §8.9.2). Номер датчика — просто порядок жадного
выбора: при пересчёте порядок меняется целиком, и позиция, бывшая первой, может стать
седьмой. Без сопоставления анализ «куда переехал датчик №1» показывал бы случайные,
не связанные по смыслу пары точек через всю карту.

⚠️ СОПОСТАВЛЕНИЕ — ТОЛЬКО ВНУТРИ ОДНОГО ТИПА (§8.9.4): тип 1 с типом 1, большие
с большими. Смешивать типы бессмысленно — это разные датчики с разными радиусами,
и «минимальное перемещение» между ними не имеет физического смысла.

Венгерский алгоритм — СВОЯ РЕАЛИЗАЦИЯ (решение заказчика 03.09.2026, §8.9.3/8.9.6):
`scipy` ради одной функции `linear_sum_assignment` в проект не ставим (см. задел 8.9.6:
риск для сборки .exe перевешивает выгоду одной функции). Матрицы здесь малые — обычно
меньше полусотни на полусотни, считается за доли миллисекунды.
"""
import numpy as np


def hungarian(cost):
    """Задача о назначениях: минимизировать сумму `cost[i, col[i]]` по паросочетанию.

    `cost` — матрица (n, m), необязательно квадратная. Возвращает `(rows, cols)` —
    индексы ОПТИМАЛЬНОГО паросочетания, длиной `min(n, m)`, отсортированные по `rows`.
    Тот же контракт, что у `scipy.optimize.linear_sum_assignment` — понадобится scipy
    (задел 8.9.6), заменить нужно будет только эту функцию.

    Реализация — классический O(n³) венгерский алгоритм (метод потенциалов, поиск
    кратчайших дополняющих путей). Прямоугольная матрица дополняется до квадратной
    большой константой на «фиктивных» клетках — реальная пара им предпочтительнее
    всегда, а сама допонительная клетка ничего не стоит паре из двух фиктивных."""
    cost = np.asarray(cost, dtype=float)
    n, m = cost.shape
    if n == 0 or m == 0:
        return np.empty(0, dtype=int), np.empty(0, dtype=int)
    size = max(n, m)
    if n == m:
        sq = cost
    else:
        # БОЛЬШАЯ КОНСТАНТА — заведомо больше любой настоящей стоимости (здесь это
        # километры внутри одного участка, ≤ пары сотен), но конечная: потенциалы
        # венгерского алгоритма с бесконечностью считать нельзя.
        big = 1.0e6
        sq = np.zeros((size, size), dtype=float)
        sq[:n, :m] = cost
        if m < size:
            sq[:n, m:] = big
        if n < size:
            sq[n:, :m] = big
        # sq[n:, m:] остаётся 0 — «фиктивный к фиктивному» ничего не стоит
    row_to_col = _hungarian_square(sq)
    rows, cols = [], []
    for i in range(n):
        j = row_to_col[i]
        if j < m:
            rows.append(i); cols.append(j)
    return np.asarray(rows, dtype=int), np.asarray(cols, dtype=int)


def _hungarian_square(a):
    """Венгерский алгоритм на КВАДРАТНОЙ матрице (n×n), метод потенциалов (истоки —
    классическая e-maxx реализация). 1-индексация внутри — так короче запись через
    массивы потенциалов `u`/`v`; наружу отдаём обычный 0-индексный список.

    Возвращает список длины n: `row_to_col[i]` — столбец, назначенный строке `i`."""
    n = a.shape[0]
    INF = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)          # p[j] = номер строки (1..n), занявшей столбец j
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
            j1 = -1
            for j in range(1, n + 1):
                if used[j]:
                    continue
                cur = a[i0 - 1, j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    row_to_col = [0] * n
    for j in range(1, n + 1):
        row_to_col[p[j] - 1] = j - 1
    return row_to_col


def match_by_type(old_xy, old_types, new_xy, new_types):
    """Сопоставить старые и новые позиции ОТДЕЛЬНО ПО ТИПАМ (§8.9.4).

    `old_xy`/`new_xy` — (N,2)/(M,2) км; `old_types`/`new_types` — код типа той же
    длины (1–3 малые, 4 большие). Возвращает список записей-словарей:
    `type_id`, `old_idx`, `new_idx`, `old_xy`, `new_xy`, `dist_km` — по одной на КАЖДУЮ
    старую и каждую новую позицию. Появление/исчезновение (§8.9.4, «появление и
    исчезновение»): датчик без пары получает `new_idx=None` или `old_idx=None`,
    `dist_km=None` — стрелку для него не рисуют (не с чем соединять)."""
    old_xy = np.asarray(old_xy, float).reshape(-1, 2)
    new_xy = np.asarray(new_xy, float).reshape(-1, 2)
    old_types = np.asarray(old_types).reshape(-1)
    new_types = np.asarray(new_types).reshape(-1)
    out = []
    types = sorted(set(old_types.tolist()) | set(new_types.tolist()))
    for t in types:
        oi = np.where(old_types == t)[0]
        ni = np.where(new_types == t)[0]
        if len(oi) == 0 or len(ni) == 0:
            for i in oi:
                out.append(dict(type_id=t, old_idx=int(i), new_idx=None,
                                old_xy=tuple(old_xy[i]), new_xy=None, dist_km=None))
            for j in ni:
                out.append(dict(type_id=t, old_idx=None, new_idx=int(j),
                                old_xy=None, new_xy=tuple(new_xy[j]), dist_km=None))
            continue
        op, npnts = old_xy[oi], new_xy[ni]
        cost = np.hypot(op[:, 0][:, None] - npnts[:, 0][None, :],
                        op[:, 1][:, None] - npnts[:, 1][None, :])
        rows, cols = hungarian(cost)
        matched_old, matched_new = set(rows.tolist()), set(cols.tolist())
        for r, c in zip(rows, cols):
            out.append(dict(type_id=t, old_idx=int(oi[r]), new_idx=int(ni[c]),
                            old_xy=tuple(op[r]), new_xy=tuple(npnts[c]),
                            dist_km=float(cost[r, c])))
        for i, idx in enumerate(oi):
            if i not in matched_old:
                out.append(dict(type_id=t, old_idx=int(idx), new_idx=None,
                                old_xy=tuple(op[i]), new_xy=None, dist_km=None))
        for j, idx in enumerate(ni):
            if j not in matched_new:
                out.append(dict(type_id=t, old_idx=None, new_idx=int(idx),
                                old_xy=None, new_xy=tuple(npnts[j]), dist_km=None))
    return out


def total_movement(matches):
    """Суммарное перемещение по списку от `match_by_type` — только у пар с известным
    расстоянием (не появившихся/не исчезнувших). Используется для проверки: у
    сопоставления по венгерскому алгоритму сумма обязана быть МЕНЬШЕ ИЛИ РАВНА, чем у
    наивной нумерации «по порядку» (план 8, §8.9.5)."""
    return float(sum(m["dist_km"] for m in matches if m["dist_km"] is not None))
