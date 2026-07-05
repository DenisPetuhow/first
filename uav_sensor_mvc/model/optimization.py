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
from .geometry import ellipse_geometry, points_in_ellipse
from .trajectories import corridor_filter


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

    def __init__(self, candidates, R, L_seg, k):
        self.cand = np.asarray(candidates, float)
        self.C = len(self.cand)
        self.R = float(R)
        self.L_seg = int(L_seg)
        self.k = int(k)
        self._hit, self._mask, self._cov = [], [], []
        self._wcov = None                                 # матрица покрытия клеток (вкладка 3)
        self._wpos = None                                 # веса клеток

    @property
    def n_traj(self):
        return len(self._hit)

    def add_trajectory(self, traj):
        if self.C == 0:
            self._hit.append(np.zeros(0, np.uint8))
            self._mask.append(np.zeros(0, np.uint32))
            self._cov.append(np.zeros(0, np.float32))
            return
        diff = traj[:, None, :] - self.cand[None, :, :]
        d = np.linalg.norm(diff, axis=2)                  # (n_points, C)
        within = d <= self.R
        self._hit.append(within.any(axis=0).astype(np.uint8))
        self._cov.append(within.mean(axis=0).astype(np.float32))
        mask_row = np.zeros(self.C, np.uint32)
        idx = np.array_split(np.arange(len(traj)), self.L_seg)
        for j, ix in enumerate(idx):
            if len(ix):
                seg_hit = within[ix].any(axis=0)
                mask_row |= (seg_hit.astype(np.uint32) << np.uint32(j))
        self._mask.append(mask_row)

    def greedy(self, N, weights, anchor_idx=None, anchor_sep=0.0):
        """Жадный выбор N позиций. Всегда возвращает min(N, C) датчиков.

        Основной ключ — субмодулярный прирост phi; при его насыщении —
        вторичный ключ «распределения» (прирост покрытия по маршрутам).

        anchor_idx — индекс ПРИНУДИТЕЛЬНОГО «якорного» датчика (у цели B): ставится
        первым; кандидаты ближе anchor_sep к нему исключаются (не дают «кучи» у B).
        Остальные позиции выбираются обычным жадным алгоритмом.
        """
        T = self.n_traj
        if T == 0 or self.C == 0 or N <= 0:
            return np.empty((0, 2), float)
        a1, a2 = weights
        H = np.asarray(self._hit, dtype=np.int64)         # (T, C)
        M = np.asarray(self._mask, dtype=np.uint32)       # (T, C)
        Fc = np.asarray(self._cov, dtype=np.float64)      # (T, C)
        k, Lseg = self.k, self.L_seg

        n_vec = np.zeros(T, np.int64)
        mask_vec = np.zeros(T, np.uint32)
        cur_min = np.zeros(T)
        cur_pop = np.zeros(T)
        cur_cov = np.zeros(T)                              # накопл. доля покрытия [0..1]
        avail = np.ones(self.C, bool)
        chosen = []

        def take(c):
            nonlocal n_vec, mask_vec, cur_min, cur_pop, cur_cov
            chosen.append(c)
            avail[c] = False
            n_vec = n_vec + H[:, c]
            mask_vec = mask_vec | M[:, c]
            cur_min = np.minimum(n_vec, k).astype(float)
            cur_pop = _popcount(mask_vec).astype(float)
            cur_cov = np.minimum(cur_cov + Fc[:, c], 1.0)

        # якорный датчик у цели B + исключение близких кандидатов
        if anchor_idx is not None and 0 <= int(anchor_idx) < self.C:
            take(int(anchor_idx))
            if anchor_sep > 0:
                d = np.linalg.norm(self.cand - self.cand[int(anchor_idx)], axis=1)
                avail[d < anchor_sep] = False

        while len(chosen) < min(N, self.C):
            new_min = np.minimum(n_vec[:, None] + H, k)
            gain_n = (new_min - cur_min[:, None]) / k
            new_pop = _popcount(mask_vec[:, None] | M)
            gain_m = (new_pop - cur_pop[:, None]) / Lseg
            primary = (a1 * gain_n + a2 * gain_m).mean(axis=0)        # (C,)

            # вторичный критерий: прирост покрытой доли маршрутов (с усечением 1)
            new_cov = np.minimum(cur_cov[:, None] + Fc, 1.0)
            gain_cov = (new_cov - cur_cov[:, None]).mean(axis=0)      # (C,)

            # лексикографика: основной критерий; при его насыщении — вторичный.
            score = primary if primary.max() > 1e-9 else SPREAD_EPS * gain_cov
            score = np.where(avail, score, -np.inf)
            c = int(np.argmax(score))
            if not np.isfinite(score[c]):
                break
            take(c)

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


def greedy_placement(trajectories, candidates, N, R, k, L_segments, weights):
    """Жадное размещение по списку маршрутов (строит CoverageCache внутри)."""
    cache = CoverageCache(candidates, R, L_segments, k)
    for tr in trajectories:
        cache.add_trajectory(tr)
    return cache.greedy(N, weights)
