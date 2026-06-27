# -*- coding: utf-8 -*-
"""
MODEL · Режим «зона старта -> цель» (вкладка 2).

Отличия от базовой модели (A фиксирована):
  * старт БПЛА — не одна точка, а СЛУЧАЙНАЯ точка из ЗОНЫ СТАРТА (эллипс вокруг A:
    полуось вдоль A->B = глубина/2, поперёк = ширина/2);
  * цель — точка B; направление движения у каждого маршрута своё (от своей точки
    старта к B);
  * две модели движения:
      - "area_arc"  — дуга из точки старта в B (угол меняется от итерации к итерации);
      - "maneuver"  — манёвренная ЛОМАНАЯ как у БПЛА самолётного типа: случайные
        повороты с ОГРАНИЧЕНИЕМ РАДИУСА РАЗВОРОТА (прямо / зигзаг / извилисто по
        профилю разброса), длина <= L_max, идеальные условия (одна высота, без
        препятствий).
Оптимизация (жадный субмодулярный выбор по накопленной выборке) и метрики —
переиспользуются из optimization.py / detection.py: размещение учитывает и
вероятностные линии маршрута, и разброс точек старта.
"""
import numpy as np

from config import MODES
from . import detection
from .geometry import ellipse_geometry
from .trajectories import (make_arc_by_angle, max_deflection_angle, _sample_angle,
                           polyline_length, arc_fan, maneuver_path, polyline_path,
                           turn_radius_km)
from .optimization import CoverageCache, filter_not_past_target


# ----------------------------------------------------------------------
# Векторная геометрия
# ----------------------------------------------------------------------
def _frame(A, B):
    """Орт оси A->B (u) и нормали (n)."""
    A, B = np.asarray(A, float), np.asarray(B, float)
    d = B - A
    L = float(np.linalg.norm(d))
    u = d / L if L > 1e-9 else np.array([1.0, 0.0])
    n = np.array([-u[1], u[0]])
    return u, n, L


def start_zone_outline(A, B, depth, width, n=180):
    """Контур зоны старта (эллипс с центром в A): глубина вдоль оси, ширина поперёк."""
    A = np.asarray(A, float)
    u, nrm, _ = _frame(A, B)
    t = np.linspace(0, 2 * np.pi, n)
    return (A[None, :] + (0.5 * depth) * np.cos(t)[:, None] * u[None, :]
            + (0.5 * width) * np.sin(t)[:, None] * nrm[None, :])


def reach_ellipse_outline(A, B, L_max, n=220):
    """Граница эллипса достижимости A<->B (предел движения по запасу хода)."""
    g = ellipse_geometry(A, B, L_max)
    cx, cy = g["center"]; a, b, ang = g["a"], g["b"], g["angle"]
    t = np.linspace(0, 2 * np.pi, n)
    ca, sa = np.cos(ang), np.sin(ang)
    x = a * np.cos(t); y = b * np.sin(t)
    return np.column_stack([cx + x * ca - y * sa, cy + x * sa + y * ca])


def sample_start_point(A, B, depth, width, rng):
    """Случайная точка ВНУТРИ зоны старта (равномерно в эллипсе)."""
    A = np.asarray(A, float)
    u, nrm, _ = _frame(A, B)
    r = np.sqrt(rng.random()); th = rng.uniform(0, 2 * np.pi)
    du = r * np.cos(th) * (0.5 * depth)
    dv = r * np.sin(th) * (0.5 * width)
    return A + du * u + dv * nrm


def _zone_exit_frac(S0, A, B, depth, width):
    """Доля пути S0->B, ЗА которой путевые точки гарантированно ВНЕ ЗОНЫ СТАРТА.

    Берётся опорная проекция эллипса зоны (центр A, полуоси depth/2 вдоль оси A->B
    и width/2 поперёк) на ось S0->B: точка, отстоящая дальше максимальной проекции
    эллипса, лежит за его опорной плоскостью, поэтому её боковой вынос (перпендикуляр
    к оси S0->B сохраняет проекцию) уже не может вернуть точку внутрь зоны. Так точки
    не спавнятся внутри зоны возможного старта — только за её пределами.
    """
    S0 = np.asarray(S0, float); A = np.asarray(A, float); B = np.asarray(B, float)
    u, nrm, _ = _frame(A, B)                          # оси эллипса зоны
    a = 0.5 * depth; b = 0.5 * width
    chord = B - S0; D = float(np.linalg.norm(chord))
    if D < 1e-9:
        return 0.05
    e_par = chord / D
    # max по эллипсу от (P - S0)·e_par  (опорная функция эллипса вдоль оси S0->B)
    base = float((A - S0) @ e_par)
    reach = float(np.hypot(a * (u @ e_par), b * (nrm @ e_par)))
    frac = (base + reach) / D
    return float(np.clip(frac + 0.02, 0.05, 0.6))     # +небольшой запас наружу


# ----------------------------------------------------------------------
# Генераторы маршрутов (старт из зоны -> цель B)
# ----------------------------------------------------------------------
def sample_area_arc(A, B, depth, width, L_max, rng, sigma_frac, n_points, profile):
    """Дуга из случайной точки старта в B (угол по профилю разброса)."""
    S0 = sample_start_point(A, B, depth, width, rng)
    c = float(np.linalg.norm(np.asarray(B, float) - S0))
    if c >= L_max * 0.999 or c < 1e-6:                 # запаса нет -> прямая
        t = np.linspace(0, 1, n_points)
        return S0[None, :] + t[:, None] * (np.asarray(B, float) - S0)[None, :], S0
    tmax = max_deflection_angle(S0, B, L_max)
    theta = _sample_angle(rng, tmax, sigma_frac * tmax, profile)
    return make_arc_by_angle(S0, B, theta, n_points), S0


def sample_maneuver(A, B, depth, width, L_max, rng, n_points, profile, r_min=0.5,
                    nrange=(2, 5), law="points"):
    """Манёвр из СЛУЧАЙНОЙ точки старта зоны в B (по точкам или по синусу).
    Путевые точки ставятся только ЗА зоной старта (s_min = доля выхода из зоны)."""
    S0 = sample_start_point(A, B, depth, width, rng)
    s_min = _zone_exit_frac(S0, A, B, depth, width)
    return maneuver_path(S0, B, L_max, rng, profile, n_points, r_min, nrange, law,
                         s_min), S0


def sample_polyline(A, B, depth, width, L_max, rng, n_points, profile, r_min=0.5,
                    nrange=(2, 5), law="points"):
    """Ломаная из СЛУЧАЙНОЙ точки старта зоны в B (общее ядро polyline_path).
    Точки — только ЗА зоной старта."""
    S0 = sample_start_point(A, B, depth, width, rng)
    s_min = _zone_exit_frac(S0, A, B, depth, width)
    return polyline_path(S0, B, L_max, rng, profile, n_points, r_min, nrange, law,
                         s_min), S0


# ----------------------------------------------------------------------
# Модель режима «зона старта -> цель»
# ----------------------------------------------------------------------
class AreaStartModel:
    """Размещение датчиков при старте из зоны и движении к цели B."""

    def __init__(self, params):
        self.p = params
        self.movement = "area_arc"
        self.A = np.asarray(params.A, float)
        self.B = np.asarray(params.B, float)
        self.has_route = False
        self.theta_max = 0.0
        self.r_min = turn_radius_km(params.speed_kmh / 3.6, params.bank_deg)
        self.rng = np.random.default_rng(params.seed)
        self.trajectories, self.features = [], []
        self.sensors = np.empty((0, 2), float)
        self.candidates = np.empty((0, 2), float)
        self.cache = None
        self.iteration = 0
        self._corr_outline = (np.empty((0, 2)), np.empty((0, 2)))
        self._corr_bbox = (-1.0, 1.0, -1.0, 1.0)
        self._view_bbox = self._corr_bbox

    # ---- настройка ----
    def set_mode(self, mode):
        self.p.mode = mode

    def set_profile(self, profile):
        self.p.motion_profile = profile

    def set_movement(self, movement):
        self.movement = movement

    @property
    def weights(self):
        return MODES[self.p.mode]

    def set_route(self, A, B):
        """Зафиксировать маршрут (центр зоны старта A и цель B) и сбросить выборку."""
        self.A = np.asarray(A, float)
        self.B = np.asarray(B, float)
        self.has_route = True
        self.reset()

    # ---- геометрия ----
    def corridor_outline(self):
        return self._corr_outline

    def corridor_bbox(self):
        return self._corr_bbox

    def view_bbox(self):
        return self._view_bbox

    def start_zone(self):
        return start_zone_outline(self.A, self.B, self.p.corridor_depth,
                                  self.p.corridor_width)

    def _reachable_mask(self, P):
        """Точки, достижимые из зоны старта при движении в B за <= L_max."""
        A, B = self.A, self.B
        u, nrm, _ = _frame(A, B)
        dep, wid = 0.5 * self.p.corridor_depth, 0.5 * self.p.corridor_width
        rel = P - A
        du = rel @ u; dv = rel @ nrm
        rad = np.hypot(du, dv)
        psi = np.arctan2(dv, du)
        rdir = 1.0 / np.sqrt((np.cos(psi) / max(dep, 1e-6)) ** 2
                             + (np.sin(psi) / max(wid, 1e-6)) ** 2)
        mindist = np.maximum(0.0, rad - rdir)           # расстояние P до зоны старта
        return (mindist + np.linalg.norm(P - B, axis=1)) <= self.p.L_max

    def _build_candidates(self):
        cx, cy = 0.5 * (self.A + self.B)
        half = 0.5 * self.p.L_max + 0.5 * max(self.p.corridor_depth,
                                              self.p.corridor_width) + 2 * self.p.grid_step
        step = self.p.grid_step
        xs = np.arange(cx - half, cx + half + step, step)
        ys = np.arange(cy - half, cy + half + step, step)
        gx, gy = np.meshgrid(xs, ys, indexing="ij")
        pts = np.column_stack([gx.ravel(), gy.ravel()])
        return pts[self._reachable_mask(pts)]

    def reset(self, seed=None):
        if seed is not None:
            self.p.seed = seed
        self.rng = np.random.default_rng(self.p.seed)
        self.r_min = turn_radius_km(self.p.speed_kmh / 3.6, self.p.bank_deg)
        self.trajectories, self.features = [], []
        self.sensors = np.empty((0, 2), float)
        self.iteration = 0
        if not self.has_route:
            return
        self.candidates = filter_not_past_target(self._build_candidates(), self.A, self.B)
        self.cache = CoverageCache(self.candidates, self.p.R, self.p.L_seg, self.p.k)
        zone = self.start_zone()
        reach = reach_ellipse_outline(self.A, self.B, self.p.L_max)
        self._corr_outline = (reach, zone)
        allpts = np.vstack([zone, reach, self.A[None, :], self.B[None, :]])
        self._corr_bbox = (float(allpts[:, 0].min()), float(allpts[:, 0].max()),
                           float(allpts[:, 1].min()), float(allpts[:, 1].max()))
        self._view_bbox = self._compute_view_bbox()

    def _compute_view_bbox(self, n=160, margin=0.08):
        rng = np.random.default_rng(777)
        pts = [self.start_zone(), self.A[None, :], self.B[None, :]]
        for _ in range(n):
            tr, _f = self._sample(rng)
            pts.append(tr)
        P = np.vstack(pts)
        x0, x1 = np.percentile(P[:, 0], [1, 99])
        y0, y1 = np.percentile(P[:, 1], [1, 99])
        dx, dy = (x1 - x0) * margin, (y1 - y0) * margin + 1e-6
        return (float(x0 - dx), float(x1 + dx), float(y0 - dy), float(y1 + dy))

    # ---- порождение и накопление ----
    def _sample(self, rng):
        p = self.p
        if self.movement == "maneuver":
            return sample_maneuver(self.A, self.B, p.corridor_depth, p.corridor_width,
                                   p.L_max, rng, p.n_points, p.motion_profile,
                                   self.r_min, (p.n_min, p.n_max), p.maneuver_law)
        if self.movement == "polyline":
            return sample_polyline(self.A, self.B, p.corridor_depth, p.corridor_width,
                                   p.L_max, rng, p.n_points, p.motion_profile,
                                   self.r_min, (p.n_min, p.n_max), p.maneuver_law)
        return sample_area_arc(self.A, self.B, p.corridor_depth, p.corridor_width,
                               p.L_max, rng, p.sigma_frac, p.n_points, p.motion_profile)

    def sample_trajectory(self):
        return self._sample(self.rng)

    def _anchor_idx(self):
        """Индекс кандидата для «якорного» датчика у цели B (1/3 R заходит за B)."""
        if self.cache is None or len(self.candidates) == 0:
            return None
        d = self.B - self.A; L = float(np.linalg.norm(d))
        if L < 1e-6:
            return None
        P = self.B - (2.0 / 3.0) * self.p.R * (d / L)
        return int(np.argmin(np.linalg.norm(self.candidates - P, axis=1)))

    def recompute_placement(self):
        self.sensors = self.cache.greedy(self.p.N, self.weights,
                                         anchor_idx=self._anchor_idx(),
                                         anchor_sep=1.35 * self.p.R)
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

    # ---- слои ----
    def frequent_paths(self, n=10):
        if not self.has_route:
            return []
        rng = np.random.default_rng(2024)
        out = []
        for _ in range(n):
            tr, _f = self._sample(rng)
            out.append(dict(traj=tr, weight=0.6, is_extreme=False))
        return out

    def fan_paths(self):
        if not self.has_route:
            return []
        if self.movement == "area_arc":
            return arc_fan(self.A, self.B, self.p.L_max, self.p.angle_step_deg,
                           sigma_frac=self.p.sigma_frac, n_points=self.p.n_points,
                           profile=self.p.motion_profile)
        rng = np.random.default_rng(99)
        return [dict(traj=self._sample(rng)[0], weight=0.5, is_extreme=False)
                for _ in range(14)]

    def density_field(self, nbins=160):
        if not self.trajectories:
            return None, None
        pts = np.vstack(self.trajectories)
        x0, x1, y0, y1 = self.corridor_bbox()
        H, _xe, _ye = np.histogram2d(pts[:, 0], pts[:, 1], bins=nbins,
                                     range=[[x0, x1], [y0, y1]])
        H = H.T
        if H.max() > 0:
            H = H / H.max()
        return H, (x0, x1, y0, y1)

    # ---- показатели ----
    def evaluate(self, sensors=None, trajectories=None):
        S = self.sensors if sensors is None else sensors
        trajs = self.trajectories if trajectories is None else trajectories
        p = self.p
        if not trajs:
            return dict(avg_crossings=0.0, avg_uncovered_distance=0.0,
                        avg_coverage_percent=0.0, share_meeting_k=0.0, n_sensors=len(S))
        n_list, cov_list, unc_list = [], [], []
        for t in trajs:
            n_list.append(detection.n_detections(t, S, p.R))
            cov = detection.continuous_coverage(t, S, p.R)
            cov_list.append(cov)
            unc_list.append((1.0 - cov) * polyline_length(t))
        return dict(
            avg_crossings=float(np.mean(n_list)),
            avg_uncovered_distance=float(np.mean(unc_list)),
            avg_coverage_percent=float(np.mean(cov_list) * 100.0),
            share_meeting_k=float(np.mean([x >= p.k for x in n_list]) * 100.0),
            n_sensors=len(S))

    def compare_modes(self):
        saved = self.p.mode
        out = {}
        ai, asep = self._anchor_idx(), 1.35 * self.p.R
        for m in MODES:
            S = self.cache.greedy(self.p.N, MODES[m], anchor_idx=ai, anchor_sep=asep)
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
