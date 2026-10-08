# -*- coding: utf-8 -*-
"""
MODEL · Дискретизация и жадная субмодулярная оптимизация (инкрементальный SAA).

Сетка кандидатных позиций внутри эллипса; структура покрытия CoverageCache
связывает кандидатов с засечками, сегментами и долей покрытия маршрутов и
позволяет ВЕКТОРИЗОВАННО оценивать предельный прирост полезности.

Целевая функция F_t(S) = (1/t) Σ phi(S, gamma_i) монотонна и субмодулярна ->
жадный алгоритм даёт гарантию (1 - 1/e) ≈ 0,632 (Nemhauser, Wolsey, Fisher, 1978).

Размещаются ВСЕ N датчиков. Когда основной критерий (кратность + равномерность)
насыщается на малой выборке, в дело вступает вторичный критерий «распределения»
по плотности маршрутов — он разносит оставшиеся датчики по наиболее частым путям,
вместо того чтобы останавливать выбор досрочно.
"""
import numpy as np

from config import SPREAD_EPS
from model.tabs12.geometry import ellipse_geometry, points_in_ellipse
from model.tabs12.trajectories import corridor_filter


# ----------------------------------------------------------------------
# Векторизованный popcount для битовых масок сегментов (uint32)
# ----------------------------------------------------------------------
def _popcount(a):
    a = a.astype(np.uint32, copy=False)
    bc = getattr(np, "bitwise_count", None)
    if bc is not None:
        return bc(a).astype(np.int64)
    a = a - ((a >> 1) & np.uint32(0x55555555))
    a = (a & np.uint32(0x33333333)) + ((a >> 2) & np.uint32(0x33333333))
    a = (a + (a >> 4)) & np.uint32(0x0F0F0F0F)
    return ((a * np.uint32(0x01010101)) >> 24).astype(np.int64)


# ----------------------------------------------------------------------
# Сетка кандидатных позиций датчиков внутри эллипса достижимости
# ----------------------------------------------------------------------
def filter_not_past_target(pts, A, B, max_s=1.0):
    """Убрать кандидатов ЗА целью B (проекция на ось A->B больше max_s·|AB|).

    Маршруты заканчиваются в B, поэтому датчики за B бесполезны и дают «кучу»
    позади цели. Оставляем только позиции до B.
    """
    A = np.asarray(A, float); B = np.asarray(B, float)
    d = B - A; D2 = float(d @ d)
    if D2 < 1e-9 or len(pts) == 0:
        return pts
    s = ((np.asarray(pts, float) - A) @ d) / D2
    return pts[s <= max_s]


def candidate_grid(A, B, L_max, step, corridor=True, traj_model="arc"):
    """Сетка кандидатных позиций (M, 2).

    По умолчанию ограничена КОРИДОРОМ движения (зоной всех возможных маршрутов)
    — датчики не попадают в недостижимые «мёртвые» зоны эллипса.
    corridor=False оставляет весь эллипс достижимости.
    """
    g = ellipse_geometry(A, B, L_max)
    cx, cy = g["center"]
    a, b = g["a"], g["b"]
    xs = np.arange(cx - a, cx + a + step, step)
    ys = np.arange(cy - b, cy + b + step, step)
    # обход x-major: стабильное разрешение совпадений жадного алгоритма
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    pts = np.column_stack([gx.ravel(), gy.ravel()])
    pts = pts[points_in_ellipse(pts, A, B, L_max)]
    if corridor and len(pts):
        pts = pts[corridor_filter(pts, A, B, L_max, traj_model)]
    return pts


# ----------------------------------------------------------------------
# Структура покрытия
# ----------------------------------------------------------------------
class CoverageCache:
    """Инкрементальная структура покрытия по накопленной выборке маршрутов.

    Для каждого маршрута i и кандидата c хранит:
      * hit[i, c]      — засекает ли датчик в c маршрут i (кратность n);
      * segmask[i, c]  — битовая маска покрытых сегментов (равномерность m);
      * covfrac[i, c]  — доля точек маршрута в зоне датчика (распределение).
    """

    def __init__(self, candidates, R, L_seg, k, placed=None):
        self.cand = np.asarray(candidates, float)
        self.C = len(self.cand)
        self.R = float(R)
        self.L_seg = int(L_seg)
        self.k = int(k)
        self._hit, self._mask, self._cov = [], [], []
        self._wcov = None                                 # матрица покрытия клеток (вкладка 3)
        self._wpos = None                                 # веса клеток
        # ⚠️ УЖЕ ПОСТАВЛЕННЫЕ ДАТЧИКИ ДРУГИХ ТИПОВ — `[(x, y, R), …]`. Кэш строится под
        # ОДИН радиус, поэтому датчики с другим радиусом кандидатами быть не могут; но их
        # ПОКРЫТИЕ обязано учитываться, иначе следующий тип не знает, что этот кусок
        # маршрута уже засечён нужное число раз, и садится поверх (заказчик 05.09.2026:
        # «кратность и распределение должны работать и между группами»).
        self._placed = [(float(x), float(y), float(r)) for x, y, r in (placed or [])]
        self._pre_n, self._pre_mask, self._pre_cov = [], [], []
        # оценка зазора последнего `greedy` (план 13, 13.8): F набора, верхняя граница OPT, их отношение
        self.last_bound = None
        self.last_idx = []                 # номера кандидатов, выбранных последним `greedy`
        self.last_done = set()             # группы маршрутов, набравшие уровень в последнем `greedy`

    @property
    def n_traj(self):
        return len(self._hit)

    def _add_placed_row(self, traj):
        """Вклад УЖЕ ПОСТАВЛЕННЫХ датчиков (других типов) в покрытие этой траектории.

        Считается по радиусу КАЖДОГО из них, а не по радиусу кэша: у типов радиусы
        разные, и мерить чужой датчик своей меркой значило бы завышать или занижать его
        зону. Результат — стартовое состояние для `greedy`: сколько раз маршрут уже
        засечён, какие его участки закрыты и какая доля пройдена под наблюдением."""
        n = 0
        mask = np.uint32(0)
        cov = 0.0
        if self._placed:
            idx = np.array_split(np.arange(len(traj)), self.L_seg)
            for x, y, r in self._placed:
                within = np.hypot(traj[:, 0] - x, traj[:, 1] - y) <= r
                if not within.any():
                    continue
                n += 1
                cov += float(within.mean())
                for j, ix in enumerate(idx):
                    if len(ix) and within[ix].any():
                        mask |= np.uint32(1) << np.uint32(j)
        self._pre_n.append(n)
        self._pre_mask.append(mask)
        self._pre_cov.append(min(cov, 1.0))

    def add_trajectory(self, traj, block=1 << 19):
        """Строка кэша по маршруту: засечка, сегменты, доля пути у каждого кандидата. Вход: точки маршрута (P, 2)
        км; block — пар «точка × кандидат» в куске (13.1 Д6). Отдаёт: ничего — строка дописывается в _hit, _mask, _cov."""
        self._add_placed_row(np.asarray(traj, float))
        if self.C == 0:
            self._hit.append(np.zeros(0, np.uint8))
            self._mask.append(np.zeros(0, np.uint32))
            self._cov.append(np.zeros(0, np.float32))
            return
        # ⚠️ КУСКАМИ ПО КАНДИДАТАМ: матрица расстояний целиком стоила ~48 байт на пару (разность, её копия и квадраты
        # в `norm`), до 620 МБ на маршрут при малом радиусе. √(dx² + dy²) — ровно то, что `norm` считает по оси из
        # двух чисел (одно сложение), поэтому эталон до бита; `hypot` — функция C-библиотеки, совпадает лишь по замеру
        hit = np.empty(self.C, np.uint8)                  # засекает ли кандидат маршрут
        cov = np.empty(self.C, np.float32)                # доля точек маршрута в зоне кандидата
        mask_row = np.zeros(self.C, np.uint32)            # покрытые сегменты, битами
        idx = np.array_split(np.arange(len(traj)), self.L_seg)   # номера точек по L_seg равным сегментам
        step = max(1, int(block) // max(1, len(traj)))   # кандидатов в куске
        for a in range(0, self.C, step):                  # куски кандидатов: в куске не больше block пар
            b = min(a + step, self.C)                     # конец куска
            d = traj[:, 0, None] - self.cand[None, a:b, 0]    # dx, потом расстояние, (точек, кусок), км
            dy = traj[:, 1, None] - self.cand[None, a:b, 1]   # dy, км
            d *= d; dy *= dy; d += dy                      # dx² + dy²
            np.sqrt(d, out=d)                              # расстояние точка — кандидат, км
            within = d <= self.R                          # точка в зоне кандидата, (точек, кусок)
            hit[a:b] = within.any(axis=0)                 # хоть одна точка в зоне — засёк
            cov[a:b] = within.mean(axis=0)                # доля точек маршрута в зоне
            for j, ix in enumerate(idx):                  # сегменты маршрута: бит j — сегмент задет
                if len(ix):                               # в сегменте есть точки (маршрут короче L_seg — нет)
                    seg_hit = within[ix].any(axis=0)      # сегмент j задет кандидатом
                    mask_row[a:b] |= (seg_hit.astype(np.uint32) << np.uint32(j))
        self._hit.append(hit)
        self._cov.append(cov)
        self._mask.append(mask_row)

    def routes_covered_by_candidate(self):
        """Сколько маршрутов выборки засекает КАЖДЫЙ кандидат — вектор (C,).
        Нужен для выбора «якорных» датчиков у входа и цели (вкладка 3): среди ближних
        кандидатов берётся тот, что накрывает больше всего маршрутов."""
        if len(self._hit) == 0:                        # строк нет; `not` не годится — строки бывают матрицей (шаг 9)
            return np.zeros(self.C, np.int64)
        return np.asarray(self._hit, dtype=np.uint8).sum(axis=0, dtype=np.int64)   # int64 — без копии в int64

    def greedy(self, N, weights, anchors=None, anchor_sep=0.0, min_sep=0.0, bonus=None, stop_gain=None, stop_min=0,
               traj_w=None, groups=None, goals=None):
        """Жадный выбор до min(N, C) позиций; ключи: кратность и равномерность → доля пути → маршрутов. Вход: N, веса
        (a1, a2), закреплённые номера и их разнос км, разнос км, бонус (C,) | None (13.7), стоп по приросту доли и минимум
        до него, веса маршрутов (T,), группа маршрута (T,), уровни групп 0…1 (13.1 Д9). Отдаёт: позиции (n, 2), км."""
        # ⚠️ ТРЕТИЙ КЛЮЧ «сколько маршрутов накрывает кандидат» (резерв кратности) — не украшение: без него
        # при насыщении двух первых счёт нулевой у всех, `argmax` брал ПЕРВОГО по порядку, и датчики уходили
        # в угол карты (4 из 12 не ловили ни одного пролёта). Нулевой и он — расстановка прекращается.
        # ⚠️ `anchors` — имя историческое, это лишь способ передать ПРИНУДИТЕЛЬНЫЕ позиции: прежде — якоря
        # самого алгоритма (отключены 29.08.2026), теперь — позиции, ЗАДАННЫЕ ЧЕЛОВЕКОМ (8.7). Для гарантии
        # (1 − 1/e) разница существенна: самоограничение алгоритма против сокращения задачи.
        # `min_sep` > 0 — кандидаты ближе него к ЛЮБОМУ выбранному исключаются: зоны не кучкуются.
        T = self.n_traj
        if T == 0 or self.C == 0 or N <= 0:
            return np.empty((0, 2), float)
        a1, a2 = weights
        # ⚠️ матрицы — в том виде, в каком хранятся (13.1 Д6): копии в int64 / float64 стоили +20 байт на пару, а
        # int64 + uint8 и float64 + float32 дают те же числа — эталон до бита
        H = np.asarray(self._hit, dtype=np.uint8)         # (T, C) засекает ли
        M = np.asarray(self._mask, dtype=np.uint32)       # (T, C) покрытые сегменты
        Fc = np.asarray(self._cov, dtype=np.float32)      # (T, C) доля пути в зоне
        k, Lseg = self.k, self.L_seg
        # ВЕСА МАРШРУТОВ (13.1 Д9): у этапа целей маршрут весит приоритет своей цели / её число подлётов.
        # ⚠️ без весов — прежнее `.mean`, а не `W @`: иначе округление сдвинет выбор, и эталон уйдёт
        if traj_w is None:                                # все маршруты равны
            avg = lambda X: X.mean(axis=0)                # noqa: E731 — среднее по маршрутам, (C,)
        else:                                             # вклад маршрута — его вес
            W = np.asarray(traj_w, float) / float(np.sum(traj_w))   # веса маршрутов, сумма 1
            avg = lambda X: W @ X                         # noqa: E731 — взвешенное среднее, (C,)
        grp = None if groups is None or traj_w is None else np.asarray(groups, int)   # цель маршрута или None
        done = set()                                      # группы, набравшие свой уровень

        covsum = H.sum(axis=0, dtype=np.int64).astype(float)   # маршрутов на кандидата (третичный ключ)
        # ⚠️ СТАРТУЕМ НЕ С НУЛЯ, ЕСЛИ ДАТЧИКИ ДРУГИХ ТИПОВ УЖЕ СТОЯТ. Их покрытие —
        # часть решения, и жадный прирост обязан считаться поверх него: иначе тип 2
        # «не видит» кратности, набранной типом 1, и ставит датчики в уже закрытые места.
        pre_n = (np.asarray(self._pre_n, np.int64) if len(self._pre_n) == T
                 else np.zeros(T, np.int64))
        pre_mask = (np.asarray(self._pre_mask, np.uint32) if len(self._pre_mask) == T
                    else np.zeros(T, np.uint32))
        pre_cov = (np.asarray(self._pre_cov, float) if len(self._pre_cov) == T
                   else np.zeros(T))
        n_vec = pre_n.copy()
        mask_vec = pre_mask.copy()
        cur_min = np.minimum(n_vec, k).astype(float)
        cur_pop = _popcount(mask_vec).astype(float)
        cur_cov = np.minimum(pre_cov, 1.0)                 # накопл. доля покрытия [0..1]
        avail = np.ones(self.C, bool)
        chosen = []
        # ГРАНИЦА МИНЬЮ (план 13, 13.8): для монотонной субмодулярной F при любом наборе S
        # OPT ≤ F(S) + сумма N наибольших приростов относительно S. Берём минимум по шагам.
        # ⚠️ Приросты — по ВСЕМ кандидатам, и занятым разносом тоже: граница тогда верна и для
        # задачи без разноса, а значит, и для нашей, более тесной. Выбор жадного она не трогает.
        ub = [np.inf]                                     # лучшая (наименьшая) верхняя граница OPT
        scale = [None]                                    # масштаб бонуса места — лучший прирост первого шага

        def phi_now():
            """F текущего набора — основной критерий в среднем по маршрутам. Отдаёт: float."""
            return float(avg(a1 * cur_min / k + a2 * cur_pop / Lseg))

        def note_bound(primary):
            """Учесть границу на этом шаге. Вход: основной прирост кандидатов (C,). Отдаёт: ничего."""
            top = np.partition(primary, -min(N, len(primary)))[-min(N, len(primary)):]   # N лучших приростов
            ub[0] = min(ub[0], phi_now() + float(np.maximum(top, 0.0).sum()))

        # ⚠️ доля — по СВОЕМУ вкладу типа: покрытие чужих датчиков («одна на всех») — общая база, иначе
        # отношение F / граница завышалось бы за её счёт
        f0 = phi_now()                                    # F до датчиков этого прохода

        def take(c):
            nonlocal n_vec, mask_vec, cur_min, cur_pop, cur_cov
            chosen.append(c)
            avail[c] = False
            n_vec = n_vec + H[:, c]
            mask_vec = mask_vec | M[:, c]
            cur_min = np.minimum(n_vec, k).astype(float)
            cur_pop = _popcount(mask_vec).astype(float)
            cur_cov = np.minimum(cur_cov + Fc[:, c], 1.0)

        def gain_rows(late=False):
            """Прирост основного критерия у пар маршрут × кандидат, на месте (13.1 Д6). Вход: late — порядок границы
            после цикла (a1·Δ)/k, иначе шага a1·(Δ/k): ⚠️ округление разное, эталон до бита. Отдаёт: (T, C) float64."""
            t = n_vec[:, None] + H                         # засечек с кандидатом, int64
            np.minimum(t, k, out=t)                        # не больше k
            g = t - cur_min[:, None]                       # прирост кратности, float64
            del t
            p = _popcount(mask_vec[:, None] | M)           # покрытых сегментов с кандидатом, int64
            gm = p - cur_pop[:, None]                      # прирост сегментов, float64
            del p
            if late:                                       # граница после цикла — сперва вес, потом деление
                g *= a1; g /= k; gm *= a2; gm /= Lseg
            else:                                          # шаг цикла — сперва деление, потом вес
                g /= k; g *= a1; gm /= Lseg; gm *= a2
            g += gm                                        # a1·Δn/k + a2·Δm/L
            return g

        def cov_rows():
            """Прирост доли пути под наблюдением (усечение 1) у пар маршрут × кандидат, на месте. Вход: нет — текущее
            покрытие `cur_cov`. Отдаёт: (T, C) float64."""
            g = cur_cov[:, None] + Fc                      # доля с кандидатом; float64 + float32 — те же числа, что с копией
            np.minimum(g, 1.0, out=g)                      # больше всего пути не накрыть
            g -= cur_cov[:, None]                          # прирост доли
            return g

        def close_groups():
            """Группа набрала уровень — её маршруты больше не весят (цель защищена). Отдаёт: ничего."""
            if grp is None or goals is None:              # групп нет — выбывать некому
                return
            for g in set(grp.tolist()) - done:             # группы, ещё не набравшие уровень
                rows = grp == g                           # маршруты этой группы
                if float((n_vec[rows] >= k).mean()) >= float(goals[g]) - 1e-12:   # уровень набран
                    W[rows] = 0.0                         # — вклада больше нет, веса не пересчитываем
                    done.add(g)

        def exclude_near(idx, sep):
            if sep > 0:
                dd = np.linalg.norm(self.cand - self.cand[idx], axis=1)
                avail[dd < sep] = False

        # ПРИНУДИТЕЛЬНЫЕ ПОЗИЦИИ. ⚠️ Механизм один, а источники РАЗНЫЕ, и путать их
        # нельзя (уточнение заказчика 05.09.2026):
        #   * ЯКОРЬ — позиция, которую алгоритм назначает себе сам (у входа и цели);
        #     такие отключены 29.08.2026, потому что принуждение снимало гарантию;
        #   * УСЛОВИЯ МОДЕЛИРОВАНИЯ — позиции, заданные человеком: входные данные задачи,
        #     а не решение алгоритма. Ими и пользуется вкладка 3 (задача 8.7).
        #
        # ⚠️ СНАЧАЛА СТАВИМ ВСЕ, ПОТОМ ИСКЛЮЧАЕМ СОСЕДЕЙ. Раньше это шло одним циклом, и
        # позиция, попавшая в зону разноса предыдущей, молча терялась: `avail` к её
        # очереди уже был снят. Для двух якорей у входа и цели это было незаметно (они
        # далеко друг от друга), но заданную человеком позицию ронять нельзя: поставил
        # два датчика рядом — программа обязана оставить оба. Разнос применяется только
        # к тому, что алгоритм выбирает сам.
        for aidx in (anchors or []):
            i = int(aidx)
            if 0 <= i < self.C and i not in chosen:
                take(i)
        for aidx in (anchors or []):
            i = int(aidx)
            if 0 <= i < self.C:
                exclude_near(i, max(anchor_sep, min_sep))

        stopped = False                                    # остановлен ли правилом прироста (13.1 Д9)
        close_groups()                                     # закреплённые могли уже набрать уровень
        while len(chosen) < min(N, self.C):
            primary = avg(gain_rows())                                # основной прирост кандидатов, (C,)
            note_bound(primary)                                       # граница OPT на этом шаге

            # лексикографика: основной критерий → вторичный → третичный (резерв кратности)
            if primary.max() > 1e-9:
                score = primary
                if bonus is not None:                      # модульный бонус места (13.7) — только
                    if scale[0] is None:                   # на первом уровне, пока кратность растёт
                        scale[0] = float(primary.max())    # масштаб — лучший прирост первого шага
                    score = primary + scale[0] * np.asarray(bonus, float)   # прирост плюс бонус места
            else:                                          # основной насыщен — вторичный: доля пути (с усечением 1)
                # ⚠️ считается только здесь: прежде — на каждом шаге и впустую, пока основной растёт (13.1 Д6)
                secondary = SPREAD_EPS * avg(cov_rows())                  # (C,)
                score = secondary if secondary.max() > 1e-12 else covsum   # иначе — хоть сколько-то маршрутов
            score = np.where(avail, score, -np.inf)
            # ⚠️ стоп — по доле маршрутов, засечённых ≥ k, а не по ключу выбора: после насыщения жадный уходит
            # на вторичный ключ и ставит «туда же» (план 13.1 Д9: 10 ПВН у цели, насыщение уже на 7). Смотрятся
            # ВСЕ кандидаты, а не выбранный: иначе в общем жадном по целям насыщенная цель глушила бы соседнюю
            if stop_gain is not None and len(chosen) >= stop_min:   # минимум поставлен — считаем прирост
                have = n_vec >= k                          # маршруты, уже засечённые k раз
                gain_k = avg((n_vec[:, None] + H >= k) & ~have[:, None])   # прирост доли с кратностью k, (C,)
                if np.isfinite(score).any() and not np.isfinite(score[gain_k >= stop_gain]).any():   # пользы нет
                    stopped = True                         # насыщение — отчёт скажет «прирост < …»
                    break                                  # — остальное не здесь
                score = np.where(gain_k >= stop_gain, score, -np.inf)   # ставить только полезные
            c = int(np.argmax(score))
            if not np.isfinite(score[c]) or score[c] <= 0.0:
                break                                      # полезных позиций не осталось
            take(c)
            exclude_near(c, min_sep)                       # датчики не ближе min_sep друг к другу
            close_groups()

        # граница и после последнего шага: к концу набор полнее, и она обычно самая тесная
        last = avg(gain_rows(late=True))                   # приросты в среднем по маршрутам, (C,)
        note_bound(last)
        f_s = phi_now()                                    # F итогового набора
        room = ub[0] - f0                                  # сколько вообще можно было добавить
        self.last_bound = dict(F=f_s, F0=f0, UB=ub[0], stopped=stopped,
                               ratio=((f_s - f0) / room if room > 1e-12 else 1.0))   # не хуже этой доли от OPT
        self.last_idx = list(chosen)                       # номера выбранных кандидатов — чей датчик (13.1 Д9)
        self.last_done = set(done)                         # группы, набравшие уровень
        return self.cand[chosen] if chosen else np.empty((0, 2), float)


    # ==================================================================
    # ВЕСОВАЯ карта (вкладка 3): единица покрытия — не маршрут, а КЛЕТКА с весом.
    # Это обобщение существующего алгоритма (п. 1.4/5 методички «карта угроз»):
    # тот же жадный субмодулярный выбор с гарантией (1−1/e), только «попадание»
    # датчика считается по клеткам весовой сетки, а не по точкам траектории. Никаких
    # T итераций накопления — вес статичен и известен целиком заранее (T=1).
    # ==================================================================
    def set_weighted_cells(self, cells_xy, cells_w, block=512):
        """Задать взвешенные клетки-цели. Строит булеву матрицу покрытия
        cov[c, j] = (|cand_c − cell_j| ≤ R) блоками (чтобы не держать (C×M) float).
        Считаем КВАДРАТ расстояния (без sqrt) и сравниваем с R² — быстрее."""
        P = np.asarray(cells_xy, float)
        w = np.asarray(cells_w, float)
        C = self.cand
        r2 = self.R * self.R
        cov = np.zeros((len(C), len(P)), bool)
        for i in range(0, len(C), block):
            diff = C[i:i + block, None, :] - P[None, :, :]
            d2 = np.einsum("ijk,ijk->ij", diff, diff)      # |Δ|², без квадратного корня
            cov[i:i + block] = d2 <= r2
        self._wcov = cov
        self._wpos = w

    def greedy_weighted(self, N, weights, min_sep=0.0):
        """Жадная weighted-max-coverage по клеткам. weights=(a1,a2): a1 — кратность
        (насыщение до k, «пересечение»), a2 — распределённость (охват РАЗНЫХ клеток).

        Аттракторы (w>0) поощряют покрытие (кратно до k и вширь); репеллеры (w<0)
        штрафуют — датчики от них отталкиваются. min_sep>0 убирает кандидатов ближе
        min_sep к уже выбранному (жёсткая распределённость по площади)."""
        cov = self._wcov
        if cov is None or self.C == 0 or N <= 0 or cov.shape[1] == 0:
            return np.empty((0, 2), float)
        a1, a2 = weights
        w = self._wpos
        wpos = np.clip(w, 0.0, None)                       # величина аттрактора
        wneg = np.clip(-w, 0.0, None)                      # величина репеллера
        k = max(1, int(self.k))
        cov_f = cov.astype(np.float32)                     # (C, M) — float32 экономит память
        cnt = np.zeros(cov.shape[1], np.float64)           # сколько датчиков видят клетку
        avail = np.ones(self.C, bool)
        chosen = []
        while len(chosen) < min(N, self.C):
            lt_k = cnt < k                                 # ещё не насыщено по кратности
            lt_1 = cnt < 1                                 # ещё не покрыта вовсе
            # предельная ценность клетки при добавлении ещё одного покрытия:
            g_mult = wpos * lt_k / k - wneg * lt_1         # кратность (до k) − штраф
            g_spread = wpos * lt_1 - wneg * lt_1           # охват новых клеток − штраф
            gcell = a1 * g_mult + a2 * g_spread            # (M,)
            gain = cov_f @ gcell                           # (C,) прирост по кандидатам
            gain = np.where(avail, gain, -np.inf)
            c = int(np.argmax(gain))
            if not np.isfinite(gain[c]):
                break
            chosen.append(c)
            avail[c] = False
            cnt = cnt + cov_f[c]
            if min_sep > 0:
                d = np.linalg.norm(self.cand - self.cand[c], axis=1)
                avail[d < min_sep] = False
        return self.cand[chosen] if chosen else np.empty((0, 2), float)


# ----------------------------------------------------------------------
# Строки кэша нарастающим итогом (оптимизация, шаг 9)
# ----------------------------------------------------------------------
class RowStore:
    """Строки кэша покрытия нарастающим итогом: выборка при итерациях растёт с хвоста, и строки старых маршрутов по
    тем же кандидатам не пересчитываются. Вход: постоянные кандидаты (C,2) км, радиус км, сегментов. Разбор —
    теория/код/ОПТИМИЗАЦИЯ_В_КОДЕ §9."""

    def __init__(self, cand, R, L_seg):
        self.base = CoverageCache(cand, R, L_seg, 1)   # строки по постоянным кандидатам; k строкам не нужен
        self.routes = []                               # маршруты, чьи строки уже есть, — ссылки, по порядку
        self.added = 0                                 # сколько строк досчитано в последнем `cache` — для замера

    def fits(self, cand, R, L_seg):
        """Те же ли кандидаты, радиус и сегменты. Вход: (C,2) км, км, шт. Отдаёт: bool."""
        c = np.asarray(cand, float)                    # кандидаты запроса, км
        b = self.base                                  # хранимое
        return (b.R == float(R) and b.L_seg == int(L_seg) and c.shape == b.cand.shape
                and bool(np.array_equal(c, b.cand)))

    def cache(self, routes, extra, k):
        """Кэш на кандидатах + `extra` по выборке: общее начало выборки — из памяти, новые маршруты и столбцы `extra`
        — счётом. Вход: маршруты, доп. кандидаты (E,2) км, кратность. Отдаёт: CoverageCache (строки — матрицами)."""
        b = self.base                                  # хранимые строки
        n = len(self.routes)                           # маршрутов в памяти
        # ⚠️ ВЫБОРКА РОСЛА С ХВОСТА — ТЕ ЖЕ ОБЪЕКТЫ по порядку (`is`, не сравнение чисел): новую выборку, сброс
        # итераций, загруженную историю строки прежней не описывают — тогда всё заново
        if n > len(routes) or any(a is not r for a, r in zip(self.routes, routes)):   # выборку сменили
            b = self.base = CoverageCache(b.cand, b.R, b.L_seg, 1)
            self.routes, n = [], 0
        for r in routes[n:]:                           # новые маршруты — строки счётом
            b.add_trajectory(r)
        self.routes = list(routes)
        self.added = len(routes) - n
        T = len(routes)                                # маршрутов всего
        extra = np.asarray(extra, float).reshape(-1, 2)   # закреплённые и позиции у целей, км
        full = CoverageCache(np.vstack([b.cand, extra]), b.R, b.L_seg, k)   # кэш запроса
        H = np.asarray(b._hit, np.uint8).reshape(T, -1)      # засечки, (T, C)
        M = np.asarray(b._mask, np.uint32).reshape(T, -1)    # сегменты, (T, C)
        F = np.asarray(b._cov, np.float32).reshape(T, -1)    # доля пути, (T, C)
        if len(extra):                                 # столбцы доп. кандидатов — счётом по всей выборке
            ex = CoverageCache(extra, b.R, b.L_seg, k)   # их строки
            for r in routes:                           # строка на маршрут
                ex.add_trajectory(r)
            # ⚠️ столбец считается независимо от соседей (расстояние, `any`, `mean` по точкам), поэтому склейка даёт
            # ровно те числа, что один кэш на всех кандидатах, — эталон до бита
            H = np.hstack([H, np.asarray(ex._hit, np.uint8).reshape(T, -1)])
            M = np.hstack([M, np.asarray(ex._mask, np.uint32).reshape(T, -1)])
            F = np.hstack([F, np.asarray(ex._cov, np.float32).reshape(T, -1)])
        full._hit, full._mask, full._cov = H, M, F     # готовые матрицы: `greedy`, `joint_greedy` берут их без копии
        return full
