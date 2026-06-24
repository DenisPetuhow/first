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
def candidate_grid(A, B, L_max, step):
    """Сетка кандидатных позиций (M, 2), целиком лежащих в эллипсе достижимости."""
    g = ellipse_geometry(A, B, L_max)
    cx, cy = g["center"]
    a, b = g["a"], g["b"]
    xs = np.arange(cx - a, cx + a + step, step)
    ys = np.arange(cy - b, cy + b + step, step)
    # обход x-major: согласует разрешение совпадений с эталонной реализацией
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    pts = np.column_stack([gx.ravel(), gy.ravel()])
    return pts[points_in_ellipse(pts, A, B, L_max)]


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

    def greedy(self, N, weights):
        """Жадный выбор N позиций. Всегда возвращает min(N, C) датчиков.

        Основной ключ — субмодулярный прирост phi; при его насыщении —
        вторичный ключ «распределения» (прирост покрытия по маршрутам).
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

        for _ in range(min(N, self.C)):
            new_min = np.minimum(n_vec[:, None] + H, k)
            gain_n = (new_min - cur_min[:, None]) / k
            new_pop = _popcount(mask_vec[:, None] | M)
            gain_m = (new_pop - cur_pop[:, None]) / Lseg
            primary = (a1 * gain_n + a2 * gain_m).mean(axis=0)        # (C,)

            # вторичный критерий: прирост покрытой доли маршрутов (с усечением 1)
            new_cov = np.minimum(cur_cov[:, None] + Fc, 1.0)
            gain_cov = (new_cov - cur_cov[:, None]).mean(axis=0)      # (C,)

            # лексикографика: основной критерий; при его насыщении (всюду ~0) —
            # вторичный критерий распределения. Сохраняет валидированное поведение
            # при положительном основном приросте и гарантирует размещение всех N.
            score = primary if primary.max() > 1e-9 else SPREAD_EPS * gain_cov
            score = np.where(avail, score, -np.inf)
            c = int(np.argmax(score))
            if not np.isfinite(score[c]):
                break
            chosen.append(c)
            avail[c] = False
            n_vec = n_vec + H[:, c]
            mask_vec = mask_vec | M[:, c]
            cur_min = np.minimum(n_vec, k).astype(float)
            cur_pop = _popcount(mask_vec).astype(float)
            cur_cov = np.minimum(cur_cov + Fc[:, c], 1.0)

        return self.cand[chosen] if chosen else np.empty((0, 2), float)


def greedy_placement(trajectories, candidates, N, R, k, L_segments, weights):
    """Жадное размещение по списку маршрутов (строит CoverageCache внутри)."""
    cache = CoverageCache(candidates, R, L_segments, k)
    for tr in trajectories:
        cache.add_trajectory(tr)
    return cache.greedy(N, weights)
