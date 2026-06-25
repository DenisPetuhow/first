# -*- coding: utf-8 -*-
"""
MODEL · Главный класс модели SimulationModel.

Держит состояние задачи (параметры, ГСЧ, накопленную выборку, структуру
покрытия, текущее расположение датчиков) и реализует:
  * динамику инкрементального SAA (step / add_and_replace);
  * пакетный расчёт (run_batch) и сравнение режимов (compare_modes);
  * «вероятные пути» (probable_paths): веер дуг с шагом угла (этап 1) либо
    кластеры частых пролётов (этап 2), с пометкой крайних путей;
  * поле плотности маршрутов (density_field) для тепловой карты;
  * итоговые и онлайн-показатели.

Класс не зависит от средств отображения.
"""
import numpy as np

from config import Params, MODES
from . import detection
from .geometry import ellipse_geometry, geo_to_local_km
from .trajectories import (max_deflection_angle, SAMPLERS, arc_fan,
                           signed_max_lateral, corridor_bbox, corridor_outline,
                           frequent_arcs, frequent_serpentines, serpentine_fan)
from .optimization import candidate_grid, CoverageCache


class SimulationModel:
    """Вычислительная модель размещения датчиков обнаружения БПЛА."""

    def __init__(self, params: Params):
        params.validate()
        self.p = params
        self._geo_info = None
        self.geom = ellipse_geometry(params.A, params.B, params.L_max)
        if params.geo:
            _, _, lat0, lon0 = geo_to_local_km(params.A_geo, params.B_geo)
            self._geo_info = (lat0, lon0)
        self.reset()

    # ------------------------------------------------------------------
    def reset(self, seed=None):
        """Полный сброс: НОВАЯ случайная выборка, пустое расположение датчиков.

        Если seed не задан явно (p.seed=None), генератор инициализируется от
        системной энтропии — каждый прогон даёт другой случайный результат.
        Передайте seed (или задайте p.seed) для воспроизводимости.
        """
        p = self.p
        if seed is not None:
            p.seed = seed
        self.rng = np.random.default_rng(p.seed)
        self.geom = ellipse_geometry(p.A, p.B, p.L_max)
        self.theta_max = max_deflection_angle(p.A, p.B, p.L_max)
        self.candidates = candidate_grid(p.A, p.B, p.L_max, p.grid_step,
                                         traj_model=p.traj_model)
        self.cache = CoverageCache(self.candidates, p.R, p.L_seg, p.k)
        self.trajectories = []
        self.features = []
        self.sensors = np.empty((0, 2), float)
        self.iteration = 0
        # кэш геометрии отображения (пересчитывается при смене параметров)
        self._corr_outline = corridor_outline(p.A, p.B, p.L_max, p.traj_model)
        self._corr_bbox = corridor_bbox(p.A, p.B, p.L_max, p.traj_model)
        self._view_bbox = self._compute_view_bbox()

    def _compute_view_bbox(self, n=200, margin=0.06):
        """Габариты ЗОНЫ МАРШРУТОВ: где реально лежат ~96% траекторий выбранного
        профиля (детерминированная пред-выборка, не зависит от живого прогона).
        Используется для зума карты вместо полного коридора."""
        p = self.p
        sampler = SAMPLERS[p.traj_model]
        rng = np.random.default_rng(12345)
        pts = []
        for _ in range(n):
            tr, _ = sampler(p.A, p.B, p.L_max, rng, sigma_frac=p.sigma_frac,
                            n_points=p.n_points, theta_max=self.theta_max,
                            profile=p.motion_profile)
            pts.append(tr)
        P = np.vstack(pts)
        x0, x1 = np.percentile(P[:, 0], [1, 99])
        y0, y1 = np.percentile(P[:, 1], [2, 98])
        x0 = min(x0, p.A[0]); x1 = max(x1, p.B[0])      # концы A, B всегда в кадре
        dx, dy = (x1 - x0) * margin, (y1 - y0) * margin + 1e-6
        return (float(x0 - dx), float(x1 + dx), float(y0 - dy), float(y1 + dy))

    def set_mode(self, mode):
        self.p.mode = mode

    def set_traj_model(self, traj_model):
        self.p.traj_model = traj_model

    def set_profile(self, profile):
        self.p.motion_profile = profile

    @property
    def weights(self):
        return MODES[self.p.mode]

    def corridor_bbox(self):
        """Габариты полного коридора движения (все возможные маршруты)."""
        return self._corr_bbox

    def corridor_outline(self):
        """Границы коридора движения (upper, lower) — для контура на карте."""
        return self._corr_outline

    def view_bbox(self):
        """Габариты зоны типичных маршрутов (зум карты, профиль-зависимо)."""
        return self._view_bbox

    # ------------------------------------------------------------------
    # Порождение и накопление маршрутов
    # ------------------------------------------------------------------
    def sample_trajectory(self):
        p = self.p
        sampler = SAMPLERS[p.traj_model]
        return sampler(p.A, p.B, p.L_max, self.rng,
                       sigma_frac=p.sigma_frac, n_points=p.n_points,
                       theta_max=self.theta_max, profile=p.motion_profile)

    def recompute_placement(self):
        self.sensors = self.cache.greedy(self.p.N, self.weights)
        return self.sensors

    def add_and_replace(self, traj, feature):
        self.trajectories.append(traj)
        self.features.append(feature)
        self.cache.add_trajectory(traj)
        self.iteration += 1
        return self.recompute_placement()

    def step(self):
        traj, feature = self.sample_trajectory()
        sensors = self.add_and_replace(traj, feature)
        return traj, sensors

    def run_batch(self, T=None, mode=None):
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

    # ------------------------------------------------------------------
    # Слои вероятных путей (аналитические, данные не требуются)
    # ------------------------------------------------------------------
    def frequent_paths(self, n=10):
        """10 наиболее вероятных маршрутов (квантили распределения по профилю):
        дуги — 10 дуг, петли — 10 представительных петель. Сплошные линии,
        вес = относительная частота (без предельных пунктирных)."""
        p = self.p
        if p.traj_model == "arc":
            return frequent_arcs(p.A, p.B, p.L_max, p.motion_profile,
                                 p.sigma_frac, n=n, n_points=p.n_points)
        return frequent_serpentines(p.A, p.B, p.L_max, p.motion_profile,
                                    p.sigma_frac, n=n, n_points=p.n_points)

    def fan_paths(self):
        """Веер ВОЗМОЖНЫХ маршрутов по шагу (геометрия пространства маршрутов):
        дуги — по шагу угла (предельные пунктиром), петли — по шагу амплитуды."""
        p = self.p
        if p.traj_model == "arc":
            return arc_fan(p.A, p.B, p.L_max, p.angle_step_deg,
                           sigma_frac=p.sigma_frac, n_points=p.n_points,
                           profile=p.motion_profile)
        return serpentine_fan(p.A, p.B, p.L_max, n_points=p.n_points)

    def density_field(self, nbins=160):
        """2D-плотность точек накопленных маршрутов для тепловой карты.

        Возвращает (H, extent) либо (None, None), если выборка пуста.
        """
        if not self.trajectories:
            return None, None
        pts = np.vstack(self.trajectories)
        x0, x1, y0, y1 = self.corridor_bbox()
        H, xe, ye = np.histogram2d(pts[:, 0], pts[:, 1], bins=nbins,
                                   range=[[x0, x1], [y0, y1]])
        H = H.T
        if H.max() > 0:
            H = H / H.max()
        return H, (x0, x1, y0, y1)

    # ------------------------------------------------------------------
    # Показатели
    # ------------------------------------------------------------------
    def evaluate(self, sensors=None, trajectories=None):
        S = self.sensors if sensors is None else sensors
        trajs = self.trajectories if trajectories is None else trajectories
        p = self.p
        if not trajs:
            return dict(avg_crossings=0.0, avg_uncovered_distance=0.0,
                        avg_coverage_percent=0.0, share_meeting_k=0.0,
                        n_sensors=len(S))
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
            n_sensors=len(S),
        )

    def compare_modes(self):
        saved = self.p.mode
        out = {}
        for m in MODES:
            S = self.cache.greedy(self.p.N, MODES[m])
            out[m] = self.evaluate(sensors=S)
        self.p.mode = saved
        self.recompute_placement()
        return out

    def live_metrics(self, partial_traj):
        if len(partial_traj) < 2:
            return 0.0, 0
        seen = detection.continuous_coverage(partial_traj, self.sensors, self.p.R) * 100.0
        nd = detection.n_detections(partial_traj, self.sensors, self.p.R)
        return seen, nd


def _length(traj):
    return float(np.sum(np.linalg.norm(np.diff(traj, axis=0), axis=1)))
