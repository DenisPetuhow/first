# -*- coding: utf-8 -*-
"""
MODEL · Главный класс модели SimulationModel.

Держит состояние задачи (параметры, ГСЧ, накопленную выборку маршрутов,
структуру покрытия, текущее расположение датчиков) и реализует:
  * динамику инкрементального SAA (add_and_replace) — пересчёт расположения
    на каждой итерации по всей накопленной выборке;
  * пакетный расчёт (run_batch) — оптимум сразу по выборке T;
  * сравнение трёх режимов на единой выборке (compare_modes);
  * итоговые показатели (evaluate) и онлайн-показатели пролёта (live_metrics).

Класс не зависит от средств отображения — допускает автономный прогон.
"""
import numpy as np

from config import Params, MODES
from . import detection
from .geometry import ellipse_geometry
from .trajectories import max_sagitta, SAMPLERS, signed_max_lateral
from .optimization import candidate_grid, CoverageCache


class SimulationModel:
    """Вычислительная модель размещения датчиков (этап 1: пучок дуг)."""

    def __init__(self, params: Params):
        params.validate()
        self.p = params
        self.geom = ellipse_geometry(params.A, params.B, params.L_max)
        self.reset()

    # ------------------------------------------------------------------
    # Сброс состояния и пересоздание производных структур
    # ------------------------------------------------------------------
    def reset(self, seed=None):
        """Полный сброс: новая выборка, пустое расположение датчиков."""
        p = self.p
        if seed is not None:
            p.seed = seed
        self.rng = np.random.default_rng(p.seed)
        # кэшируем предельную стрелу прогиба и сетку кандидатов (не меняются)
        self.s_max = max_sagitta(p.A, p.B, p.L_max, p.n_points)
        self.candidates = candidate_grid(p.A, p.B, p.L_max, p.grid_step)
        self.cache = CoverageCache(self.candidates, p.R, p.L_seg, p.k)
        self.trajectories = []        # накопленные маршруты
        self.features = []            # скалярный признак каждого маршрута
        self.sensors = np.empty((0, 2), float)   # текущее расположение S
        self.iteration = 0

    def set_mode(self, mode):
        self.p.mode = mode

    def set_traj_model(self, traj_model):
        self.p.traj_model = traj_model

    @property
    def weights(self):
        return MODES[self.p.mode]

    # ------------------------------------------------------------------
    # Порождение и накопление маршрутов
    # ------------------------------------------------------------------
    def sample_trajectory(self):
        """Случайный маршрут текущей модели движения. Возвращает (traj, feature)."""
        p = self.p
        sampler = SAMPLERS[p.traj_model]
        return sampler(p.A, p.B, p.L_max, self.rng,
                       sigma_frac=p.sigma_frac, n_points=p.n_points,
                       s_max=self.s_max)

    def recompute_placement(self):
        """Заново решить задачу размещения по всей накопленной выборке."""
        self.sensors = self.cache.greedy(self.p.N, self.weights)
        return self.sensors

    def add_and_replace(self, traj, feature):
        """Один шаг динамики SAA: добавить маршрут и пересчитать расположение.

        Реализует принцип «усредняется статистика, а не координаты»: расположение
        выводится заново как оптимум по накопленной выборке.
        """
        self.trajectories.append(traj)
        self.features.append(feature)
        self.cache.add_trajectory(traj)
        self.iteration += 1
        return self.recompute_placement()

    def step(self):
        """Породить маршрут, добавить его и пересчитать расположение."""
        traj, feature = self.sample_trajectory()
        sensors = self.add_and_replace(traj, feature)
        return traj, sensors

    # ------------------------------------------------------------------
    # Пакетный расчёт по всей выборке
    # ------------------------------------------------------------------
    def run_batch(self, T=None, mode=None):
        """Полный расчёт: набрать T маршрутов и разместить датчики один раз.

        Возвращает (sensors, metrics).
        """
        if mode is not None:
            self.p.mode = mode
        T = T or self.p.T
        self.reset()
        for _ in range(T):
            traj, feature = self.sample_trajectory()
            self.trajectories.append(traj)
            self.features.append(feature)
            self.cache.add_trajectory(traj)
        self.iteration = T
        self.recompute_placement()
        return self.sensors, self.evaluate()

    def top_flights(self, top=10):
        """Десять наиболее частых пролётов: гистограмма по скалярному признаку
        маршрута, для каждой частой корзины — представительный реальный маршрут.

        Возвращает список (traj, weight) с weight in [0..1].
        """
        if not self.trajectories:
            return []
        feats = np.asarray(self.features)
        bins = max(top, 12)
        counts, edges = np.histogram(feats, bins=bins)
        order = np.argsort(counts)[::-1]
        order = [b for b in order if counts[b] > 0][:top]
        if not order:
            return []
        centers = 0.5 * (edges[:-1] + edges[1:])
        max_count = counts[order].max()
        result = []
        for b in order:
            lo, hi = edges[b], edges[b + 1]
            in_bin = np.where((feats >= lo) & (feats <= hi))[0]
            if len(in_bin) == 0:
                continue
            # представитель — маршрут, ближайший к центру корзины
            rep = in_bin[np.argmin(np.abs(feats[in_bin] - centers[b]))]
            weight = counts[b] / max_count
            result.append((self.trajectories[rep], float(weight)))
        return result

    # ------------------------------------------------------------------
    # Показатели
    # ------------------------------------------------------------------
    def evaluate(self, sensors=None, trajectories=None):
        """Итоговые показатели по выборке (раздел 6 требований)."""
        S = self.sensors if sensors is None else sensors
        trajs = self.trajectories if trajectories is None else trajectories
        p = self.p
        if not trajs:
            return dict(avg_crossings=0.0, avg_uncovered_distance=0.0,
                        avg_coverage_percent=0.0, share_meeting_k=0.0)
        n_list, cov_list, unc_list = [], [], []
        for t in trajs:
            n_list.append(detection.n_detections(t, S, p.R))
            cov = detection.continuous_coverage(t, S, p.R)
            cov_list.append(cov)
            unc_list.append((1.0 - cov) * _length(t))
        return dict(
            avg_crossings=float(np.mean(n_list)),
            avg_uncovered_distance=float(np.mean(unc_list)),
            avg_coverage_percent=float(np.mean(cov_list) * 100.0),
            share_meeting_k=float(np.mean([x >= p.k for x in n_list]) * 100.0),
        )

    def compare_modes(self):
        """Сравнение трёх режимов на текущей накопленной выборке.

        Возвращает {mode: metrics}. Текущий режим модели восстанавливается.
        """
        saved = self.p.mode
        out = {}
        for m in MODES:
            S = self.cache.greedy(self.p.N, MODES[m])
            out[m] = self.evaluate(sensors=S)
        self.p.mode = saved
        self.recompute_placement()
        return out

    def live_metrics(self, partial_traj):
        """Онлайн-показатели для участка маршрута, пройденного БПЛА:
        процент пути под наблюдением и число пересечений (засечек)."""
        if len(partial_traj) < 2:
            return 0.0, 0
        seen = detection.continuous_coverage(partial_traj, self.sensors, self.p.R) * 100.0
        nd = detection.n_detections(partial_traj, self.sensors, self.p.R)
        return seen, nd


def _length(traj):
    return float(np.sum(np.linalg.norm(np.diff(traj, axis=0), axis=1)))
