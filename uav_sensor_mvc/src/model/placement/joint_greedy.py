# -*- coding: utf-8 -*-
"""
MODEL · Общий жадный проход по типам датчиков (план 13, задача 13.6).

Раньше малые типы ставились по очереди: тип 1 забирал лучшие места, тип 2 добирал остатки
(замер §0.3в плана 13: второй тип набирал свою кратность у 0.8 % маршрутов). Здесь на каждом
шаге среди всех типов с неисчерпанной квотой берётся пара «тип + позиция» с наибольшим
приростом. Квоты по типам — матроид разбиения, и у такого жадного есть граница 1/2 (Fisher,
Nemhauser, Wolsey, 1978). ⚠️ Строго — только при разносе ячейками (план 6, задача 1): попарный
разнос её ломает, писать «1/2» без оговорки нельзя (ОГРАНИЧЕНИЯ 1.14).

Критерии у каждого типа — те же, что в `CoverageCache.greedy`, в том же лексикографическом
порядке: кратность и равномерность → доля пути под наблюдением → сколько маршрутов накрывает.
Между типами сравниваются приросты ОДНОГО уровня; при равенстве ходит самый стеснённый — у кого
«осталось поставить / доступно мест» больше.

Состояние покрытия: при «одной на всех» кратности — ОБЩЕЕ (засечка датчика любого типа идёт в
счёт всем), при своей — у каждого типа своё (В13.13). ⚠️ `CoverageCache.greedy` не тронут: путь
одного типа и контрольный прогон считаются им, как раньше. Разбор — план 13 §6.
"""
import numpy as np

from config import SPREAD_EPS
from model.placement.optimization import _popcount, top_sum

# ПРАВИЛА ВЫБОРА ТИПА на шаге (`Params.threat_type_order`). Замер 24.09.2026 (план 13 §13.6.5): при
# разносе между типами выравнивание покупается общей засечкой, без разноса все правила равны.
RULES = ("gain",    # по наибольшему приросту, равные — стеснённому (решение заказчика, §0.5)
         "tight",   # самый стеснённый ходит первым: типы ровнее, общая засечка ниже
         "round",   # по кругу 1-2-3-1-2-3 (идея заказчика §0.4б)
         "types")   # по очереди номеров — как до 13.6; держится для сравнения


class _Cover:
    """Состояние покрытия выборки: засечек на маршрут, покрытые сегменты, доля пути под наблюдением.
    Вход: число маршрутов. Общее для всех типов при «одной на всех», иначе у каждого своё."""

    def __init__(self, T):
        self.n = np.zeros(T, np.int64)               # засечек у каждого маршрута
        self.mask = np.zeros(T, np.uint32)           # покрытые сегменты маршрута, битами
        self.cov = np.zeros(T)                       # доля пути под наблюдением, 0…1


class _Kind:
    """Один тип в общем проходе: матрицы его кэша, квота, разнос, доступные места, выбранные."""

    def __init__(self, cache, n, sep, anchors, cover, bonus=None):
        self.cand = cache.cand                       # кандидаты типа, км
        self.R = float(cache.R)                      # радиус типа, км
        # ⚠️ матрицы — в том виде, в каком хранятся (13.1 Д6): uint8 и float32 дают те же суммы, что копии int64 / float64
        self.H = np.asarray(cache._hit, np.uint8)    # засекает ли кандидат маршрут: маршрут × кандидат
        self.M = np.asarray(cache._mask, np.uint32)  # покрытые сегменты: маршрут × кандидат
        self.Fc = np.asarray(cache._cov, np.float32)  # доля пути в зоне: маршрут × кандидат
        self.covsum = self.H.sum(axis=0, dtype=np.int64).astype(float)   # маршрутов на кандидата — третичный ключ
        self.k, self.L = int(cache.k), int(cache.L_seg)  # кратность типа, сегментов в маршруте
        self.n_quota = int(n)                        # сколько поставить, с закреплёнными
        self.sep = float(sep)                        # разнос внутри типа, км
        # закреплённые человеком номера кандидатов; номера вне сетки отброшены
        self.anchors = [int(a) for a in (anchors or []) if 0 <= int(a) < len(self.cand)]
        self.cover = cover                           # состояние покрытия (общее или своё)
        self.avail = np.ones(len(self.cand), bool)   # кандидат ещё можно выбрать
        self.chosen = []                             # номера выбранных кандидатов
        self.ub = np.inf                             # граница Минью: наименьшая по шагам
        self.f0 = 0.0                                # F до датчиков прохода — база доли
        self.bonus = None if bonus is None else np.asarray(bonus, float)   # бонус места (13.7) или None
        self.scale = None                            # масштаб бонуса — лучший прирост первого шага

    def phi(self, a1, a2):
        """F типа на текущем покрытии — основной критерий в среднем по маршрутам. Вход: веса a1, a2. Отдаёт: float."""
        c = self.cover                               # покрытие маршрутов: засечки, сегменты, доля пути
        return float((a1 * np.minimum(c.n, self.k) / self.k + a2 * _popcount(c.mask) / self.L).mean())

    def gains(self, a1, a2):
        """Основной прирост всех кандидатов на текущем покрытии — кратность и равномерность. Вход: веса a1, a2.
        Отдаёт: (C,) — прирост F в среднем по маршрутам. ⚠️ на месте, без копий (13.1 Д6); порядок действий прежний."""
        return _primary_gain(self.H, self.M, self.cover.n, self.cover.mask, self.k, self.L, a1, a2)

    def cov_gains(self):
        """Прирост доли пути под наблюдением (усечение 1) у всех кандидатов — второй ключ. Вход: нет — `self.cover`.
        Отдаёт: (C,). ⚠️ только после насыщения основного: прежде считался на каждом шаге впустую (13.1 Д6)."""
        c = self.cover                               # покрытие маршрутов: засечки, сегменты, доля пути
        g = c.cov[:, None] + self.Fc                 # доля с кандидатом, (T, C); float64 + float32 — те же числа
        np.minimum(g, 1.0, out=g)                    # больше всего пути не накрыть
        g -= c.cov[:, None]                          # прирост доли
        return g.mean(axis=0)

    def note_bound(self, primary, a1, a2):
        """Граница Минью на этом шаге (как в `CoverageCache.greedy`, 13.8). Вход: основной прирост (C,), веса a1, a2.
        Отдаёт: ничего — копит наименьшую границу в `self.ub`."""
        if min(self.n_quota, len(primary)) <= 0:     # квоты нет — оценивать нечего
            return
        self.ub = min(self.ub, self.phi(a1, a2) + top_sum(primary, self.n_quota))   # F + N лучших приростов

    def bound(self, a1, a2):
        """Оценка зазора прохода, как `CoverageCache.last_bound`. Вход: веса a1, a2. Отдаёт: dict(F, F0, UB, ratio)."""
        f_s = self.phi(a1, a2)                       # F итогового набора
        room = self.ub - self.f0                     # сколько вообще можно было добавить
        return dict(F=f_s, F0=self.f0, UB=self.ub, ratio=((f_s - self.f0) / room if room > 1e-12 else 1.0))


def top_gain(cache, n, mask, weights, top):
    """Сумма `top` лучших приростов F по кандидатам кэша на покрытии S (граница Минью, план 13.1 Д4). Вход:
    кэш, засечек у маршрута (T,), покрытые сегменты (T,) uint32, веса (a1, a2), сколько складывать. Отдаёт: float."""
    a1, a2 = weights                                 # вес кратности, вес равномерности
    H = np.asarray(cache._hit, np.uint8)             # засекает ли кандидат маршрут: маршрут × кандидат (Д6 — без копии)
    if H.size == 0 or top <= 0:                      # кандидатов или квоты нет
        return 0.0
    M = np.asarray(cache._mask, np.uint32)           # покрытые сегменты: маршрут × кандидат
    g = _primary_gain(H, M, n, mask, int(cache.k), int(cache.L_seg), a1, a2)   # прирост F у каждого кандидата
    return top_sum(g, top)


def _primary_gain(H, M, n, mask, k, L, a1, a2):
    """Основной прирост F у кандидатов — кратность и равномерность, в среднем по маршрутам; на месте, без копий
    (13.1 Д6). Вход: засечки и сегменты (T, C), засечек у маршрута (T,) int64, сегменты (T,) uint32, k, L, веса.
    Отдаёт: (C,). ⚠️ порядок действий — целые разности, деление, вес: так эталон до бита."""
    t = n[:, None] + H                               # засечек с кандидатом, int64
    np.minimum(t, k, out=t)                          # потолок k
    t -= np.minimum(n, k)[:, None]                   # прирост кратности, целый
    gn = t / k                                       # в долях k, float64
    del t
    p = _popcount(mask[:, None] | M)                 # покрытых сегментов с кандидатом
    p -= _popcount(mask)[:, None]                    # прирост сегментов, целый
    gm = p / L                                       # в долях сегментов
    del p
    gn *= a1; gm *= a2; gn += gm                     # a1·Δn + a2·Δm
    return gn.mean(axis=0)


def _take(kinds, t, c):
    """Поставить кандидата `c` типа `t` и обновить покрытие. Вход: типы, номер типа, номер кандидата.
    Отдаёт: ничего — меняет `chosen`, `avail` и покрытие типа (общее при «одной на всех», иначе своё).
    ⚠️ При общем покрытии засечки считаются МАТРИЦЕЙ поставившего типа — его радиусом."""
    kd = kinds[t]                                    # тип, который ставит
    kd.chosen.append(c)
    kd.avail[c] = False
    cov = kd.cover                                   # общее при «одной на всех», иначе своё
    cov.n = cov.n + kd.H[:, c]
    cov.mask = cov.mask | kd.M[:, c]
    cov.cov = np.minimum(cov.cov + kd.Fc[:, c], 1.0)


def _exclude(kinds, t, c, pair):
    """Разнос после выбора: внутри типа — `sep`, между типами — pair·(R₁ + R₂). Вход: типы, номер типа,
    номер кандидата, доля пары (0 — между типами не разносить). Отдаёт: ничего."""
    kd = kinds[t]                                    # тип, который поставил
    xy = kd.cand[c]                                  # поставленная позиция, км
    if kd.sep > 0:                                   # разнос внутри типа
        kd.avail[np.linalg.norm(kd.cand - xy, axis=1) < kd.sep] = False
    if pair <= 0:                                    # галка «распределять между типами» снята
        return
    for u, other in enumerate(kinds):                # остальные типы — разнос от поставленного
        if u == t:                                   # сам тип
            continue                                 # — его разнос `sep`, выше
        block = np.linalg.norm(other.cand - xy, axis=1) < pair * (kd.R + other.R)   # слишком близко
        # ⚠️ как в прежнем проходе: если разнос закрыл бы типу ВСЕ места — не терять тип совсем
        if (other.avail & ~block).any():             # после разноса у типа места остаются — разносим
            other.avail &= ~block


def joint_greedy(caches, quotas, weights, seps, anchors, pair=0.0, joint=True, rule="gain", bonuses=None):
    """Общий жадный по типам. Вход: кэши типов (без `placed`), квоты, веса (a1, a2), разнос внутри типа,
    закреплённые номера, доля пары (0 — нет), общее ли покрытие, правило (RULES), бонусы мест по типам
    (None — нет; 13.7). Отдаёт: (номера выбранных по типам, оценки зазора по типам)."""
    a1, a2 = weights                                 # вес кратности, вес равномерности
    T = caches[0].n_traj if caches else 0            # маршрутов в выборке — у всех кэшей одна
    shared = _Cover(T)                               # общее покрытие («одна на всех»)
    bonuses = bonuses or [None] * len(caches)        # бонусы мест по типам
    kinds = [_Kind(cache, n, s, anc, shared if joint else _Cover(T), bn)   # типы прохода
             for cache, n, s, anc, bn in zip(caches, quotas, seps, anchors, bonuses)]
    for kd in kinds:                                 # F до прохода — от него считается доля зазора
        kd.f0 = kd.phi(a1, a2)
    # ЗАКРЕПЛЁННЫЕ ЧЕЛОВЕКОМ — сперва все, потом разнос от них: поставил два рядом — оба остаются
    for t, kd in enumerate(kinds):                   # каждый тип
        for a in kd.anchors:                         # — его закреплённые
            if a not in kd.chosen:                   # номер повторён — ставим один раз
                _take(kinds, t, a)
    for t, kd in enumerate(kinds):                   # разнос — когда закреплённые уже стоят все
        for a in kd.anchors:                         # от каждого закреплённого
            _exclude(kinds, t, a, pair)
    turn = 0                                         # чей ход при «по кругу»
    while True:                                      # шаг: каждый тип предлагает лучшее место, ставится одно
        offers = []                                  # (уровень, счёт, стеснённость, тип, кандидат)
        for t, kd in enumerate(kinds):               # предложение каждого типа
            if len(kd.chosen) >= min(kd.n_quota, len(kd.cand)):   # квота типа исчерпана
                continue
            primary = kd.gains(a1, a2)               # основной прирост кандидатов типа
            if not joint:                            # своё покрытие — граница по типу на каждом шаге
                kd.note_bound(primary, a1, a2)
            if primary.max() > 1e-9:                 # кратность ещё растёт
                level, score = 0, primary            # уровень ключа 0 — основной; счёт — прирост
                if kd.bonus is not None:             # модульный бонус места (13.7) — только на этом уровне
                    kd.scale = float(primary.max()) if kd.scale is None else kd.scale
                    score = primary + kd.scale * kd.bonus   # прирост плюс бонус места
            else:                                    # насыщена — второй ключ, с тем же весом, что у одного типа
                secondary = SPREAD_EPS * kd.cov_gains()   # равномерность пути
                if secondary.max() > 1e-12:          # доля пути ещё растёт
                    level, score = 1, secondary      # уровень 1 — доля пути
                else:                                # насыщено и это — резерв кратности
                    level, score = 2, kd.covsum      # уровень 2 — сколько маршрутов накрывает
            s = np.where(kd.avail, score, -np.inf)   # только доступные места
            c = int(np.argmax(s))                    # лучшее место типа
            if not np.isfinite(s[c]) or s[c] <= 0.0:   # полезных мест у типа не осталось
                continue
            tight = (kd.n_quota - len(kd.chosen)) / max(1, int(kd.avail.sum()))   # осталось / доступно
            offers.append((level, float(s[c]), tight, t, c))
        if not offers:                               # ни одному типу ставить некуда
            break
        if rule == "types":                          # по очереди, как до 13.6: младший номер типа
            _lev, _s, _tg, t, c = min(offers, key=lambda o: o[3])
        elif rule == "round":                        # по кругу 1-2-3-1-2-3: первый с предложением
            order = sorted(offers, key=lambda o: (o[3] - turn) % len(kinds))   # очередь от того, чей ход
            _lev, _s, _tg, t, c = order[0]
            turn = (t + 1) % len(kinds)              # следующий ход — следующему типу
        elif rule == "tight":                        # самый стеснённый ходит первым всегда
            _lev, _s, _tg, t, c = max(offers, key=lambda o: (-o[0], o[2], o[1], -o[3]))
        else:                                        # «gain» — по приросту (В13.22)
            level = min(o[0] for o in offers)        # уровень критерия: основной важнее
            top = max(o[1] for o in offers if o[0] == level)   # лучший прирост этого уровня
            # равные приросты — ходит самый стеснённый; при равенстве и в этом — меньший номер типа
            near = [o for o in offers if o[0] == level and o[1] >= top - 1e-12 * max(1.0, abs(top))]
            _lev, _s, _tg, t, c = max(near, key=lambda o: (o[2], -o[3]))
        _take(kinds, t, c)
        _exclude(kinds, t, c, pair)
    picks = [list(kd.chosen) for kd in kinds]        # выбранные номера по типам
    if not kinds:                                    # ставить было некому
        return picks, []
    if not joint:                                    # покрытие у каждого своё — граница по типу
        for kd in kinds:                             # и после последнего шага — обычно самая тесная
            kd.note_bound(kd.gains(a1, a2), a1, a2)
        return picks, [kd.bound(a1, a2) for kd in kinds]
    # ⚠️ ПОКРЫТИЕ ОБЩЕЕ — граница ОДНА на весь проход: OPT ≤ F(S) + Σ по типам N_t лучших приростов
    # типа (квоты — матроид разбиения). Граница «по типу» здесь неверна: чужие датчики, поставленные
    # после последнего хода типа, подняли бы F выше неё
    f_s = kinds[0].phi(a1, a2)                       # F общего набора (k у всех одна)
    extra = 0.0                                      # сумма лучших приростов по типам
    for kd in kinds:                                 # по типам — N_t лучших приростов каждого
        extra += top_sum(kd.gains(a1, a2), kd.n_quota)   # N_t лучших приростов на итоговом покрытии
    f0 = kinds[0].f0                                 # F до прохода
    room = f_s + extra - f0                          # сколько вообще можно было добавить
    one = dict(F=f_s, F0=f0, UB=f_s + extra,          # одна оценка на весь проход
               ratio=((f_s - f0) / room if room > 1e-12 else 1.0))
    return picks, [dict(one) for _kd in kinds]
