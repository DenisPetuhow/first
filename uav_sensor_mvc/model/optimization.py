# -*- coding: utf-8 -*-
"""
MODEL · Дискретизация и жадная субмодулярная оптимизация (метод выборочного
среднего, инкрементальный SAA).

Область эллипса покрывается сеткой кандидатных позиций. Структура покрытия
CoverageCache связывает кандидатные позиции с засечками и сегментами
накопленных маршрутов и позволяет ВЕКТОРИЗОВАННО оценивать предельный прирост
полезности — это ускоряет жадный выбор на каждой итерации.

Целевая функция F_t(S) = (1/t) Σ phi(S, gamma_i) монотонна и субмодулярна,
поэтому жадный алгоритм даёт гарантию (1 - 1/e) ≈ 0,632 от оптимума
(Nemhauser, Wolsey, Fisher, 1978).
"""
import numpy as np

from .geometry import ellipse_geometry, points_in_ellipse
from . import detection


# ----------------------------------------------------------------------
# Векторизованный popcount для битовых масок сегментов (uint32)
# ----------------------------------------------------------------------
def _popcount(a):
    """Число единичных битов поэлементно (uint32). Использует np.bitwise_count
    (numpy >= 2.0); иначе — SWAR-фолбэк для совместимости со старым numpy."""
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
def candidate_grid(A, B, L_max, step):
    """Сетка кандидатных позиций (M, 2), целиком лежащих в эллипсе достижимости."""
    g = ellipse_geometry(A, B, L_max)
    cx, cy = g["center"]
    a, b = g["a"], g["b"]
    xs = np.arange(cx - a, cx + a + step, step)
    ys = np.arange(cy - b, cy + b + step, step)
    # обход x-major (для каждого x — все y): согласует разрешение совпадений
    # жадного алгоритма с эталонной реализацией
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    pts = np.column_stack([gx.ravel(), gy.ravel()])
    mask = points_in_ellipse(pts, A, B, L_max)
    return pts[mask]


# ----------------------------------------------------------------------
# Структура покрытия: связь кандидатов с засечками и сегментами маршрутов
# ----------------------------------------------------------------------
class CoverageCache:
    """Инкрементальная структура покрытия по накопленной выборке маршрутов.

    Для каждого маршрута i и каждого кандидата c хранит:
      * hit[i, c]      — засекает ли датчик в c маршрут i (для кратности n);
      * segmask[i, c]  — битовая маска покрытых сегментов (для равномерности m).

    Маршрут добавляется за один проход; жадный выбор — серия векторных операций.
    """

    def __init__(self, candidates, R, L_seg, k):
        self.cand = np.asarray(candidates, float)
        self.C = len(self.cand)
        self.R = float(R)
        self.L_seg = int(L_seg)
        self.k = int(k)
        self._hit_rows = []       # список (C,) uint8
        self._mask_rows = []      # список (C,) uint32

    @property
    def n_traj(self):
        return len(self._hit_rows)

    def add_trajectory(self, traj):
        """Добавить маршрут в структуру покрытия (один проход)."""
        if self.C == 0:
            self._hit_rows.append(np.zeros(0, np.uint8))
            self._mask_rows.append(np.zeros(0, np.uint32))
            return
        diff = traj[:, None, :] - self.cand[None, :, :]
        d = np.linalg.norm(diff, axis=2)              # (n_points, C)
        within = d <= self.R                          # (n_points, C)
        hit_row = within.any(axis=0).astype(np.uint8)
        mask_row = np.zeros(self.C, np.uint32)
        idx = np.array_split(np.arange(len(traj)), self.L_seg)
        for j, ix in enumerate(idx):
            if len(ix):
                seg_hit = within[ix].any(axis=0)      # (C,) покрыт ли сегмент j
                mask_row |= (seg_hit.astype(np.uint32) << np.uint32(j))
        self._hit_rows.append(hit_row)
        self._mask_rows.append(mask_row)

    def greedy(self, N, weights):
        """Жадный субмодулярный выбор <= N позиций по средней полезности.

        weights = (alpha1, alpha2). Возвращает массив выбранных позиций (<=N, 2).
        """
        T = self.n_traj
        if T == 0 or self.C == 0 or N <= 0:
            return np.empty((0, 2), float)
        a1, a2 = weights
        H = np.asarray(self._hit_rows, dtype=np.int64)      # (T, C)
        M = np.asarray(self._mask_rows, dtype=np.uint32)    # (T, C)
        k, Lseg = self.k, self.L_seg

        n_vec = np.zeros(T, np.int64)        # текущая кратность по маршрутам
        mask_vec = np.zeros(T, np.uint32)    # текущая маска сегментов
        cur_min = np.zeros(T, np.float64)    # min(n_vec, k)
        cur_pop = np.zeros(T, np.float64)    # popcount(mask_vec)
        avail = np.ones(self.C, bool)
        chosen = []

        for _ in range(min(N, self.C)):
            # предельный прирост кратности (с усечением сверху на k)
            new_min = np.minimum(n_vec[:, None] + H, k)          # (T, C)
            gain_n = (new_min - cur_min[:, None]) / k
            # предельный прирост равномерности (покрытие новых сегментов)
            new_pop = _popcount(mask_vec[:, None] | M)           # (T, C)
            gain_m = (new_pop - cur_pop[:, None]) / Lseg
            marginal = a1 * gain_n + a2 * gain_m                 # (T, C)
            score = marginal.mean(axis=0)                        # (C,)
            score[~avail] = -np.inf
            c = int(np.argmax(score))
            if not np.isfinite(score[c]) or score[c] <= 1e-12:
                break                                            # прироста нет
            chosen.append(c)
            avail[c] = False
            n_vec = n_vec + H[:, c]
            mask_vec = mask_vec | M[:, c]
            cur_min = np.minimum(n_vec, k).astype(np.float64)
            cur_pop = _popcount(mask_vec).astype(np.float64)

        return self.cand[chosen] if chosen else np.empty((0, 2), float)


# ----------------------------------------------------------------------
# Удобная обёртка, совместимая с эталонным API (для пакетных расчётов)
# ----------------------------------------------------------------------
def greedy_placement(trajectories, candidates, N, R, k, L_segments, weights):
    """Жадное размещение по списку маршрутов (строит CoverageCache внутри)."""
    cache = CoverageCache(candidates, R, L_segments, k)
    for tr in trajectories:
        cache.add_trajectory(tr)
    return cache.greedy(N, weights)
