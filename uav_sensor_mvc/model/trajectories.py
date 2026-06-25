# -*- coding: utf-8 -*-
"""
MODEL · Генераторы случайных маршрутов БПЛА и коридор движения.

ЭТАП 1 — пучок круговых дуг по УГЛУ отклонения theta (касательно-хордовый угол
в A, в градусах). theta=0 — прямая; |theta| растёт — дуга длиннее; theta_max —
предел, при котором длина дуги = L_max.

Профили разброса (как выбирается угол):
  * normal  — усечённое нормальное (короткий путь самый частый);
  * mixed   — равномерно по всему диапазону [-theta_max, theta_max];
  * complex — тяготеет к предельным (сильно выгнутым) дугам.

ЭТАП 2 — петляющее движение (синусоида с закреплёнными концами); профиль задаёт
амплитуду петель аналогично.

КОРИДОР ДВИЖЕНИЯ — область между предельными дугами +/- theta_max. Любой
возможный маршрут лежит в нём, поэтому кандидатные позиции датчиков и масштаб
карты ограничиваются именно коридором (а не всем эллипсом достижимости).
"""
import numpy as np


# ======================================================================
# Базовая дуга по стреле прогиба
# ======================================================================
def make_arc(A, B, sagitta, n_points=140):
    A, B = np.asarray(A, float), np.asarray(B, float)
    chord = B - A
    c_len = np.linalg.norm(chord)
    midpoint = 0.5 * (A + B)
    t = np.linspace(0.0, 1.0, n_points)
    if abs(sagitta) < 1e-9:
        return A[None, :] + t[:, None] * chord[None, :]
    ux = chord / c_len
    un = np.array([-ux[1], ux[0]])
    s = float(sagitta)
    R = abs(s) / 2.0 + c_len ** 2 / (8.0 * abs(s))
    yc = abs(s) / 2.0 - c_len ** 2 / (8.0 * abs(s))
    th_a = np.arctan2(0.0 - yc, -c_len / 2.0)
    th_b = np.arctan2(0.0 - yc, +c_len / 2.0)
    thetas = np.linspace(th_a, th_b, n_points)
    xl = R * np.cos(thetas)
    yl = np.sign(s) * (yc + R * np.sin(thetas))
    return midpoint[None, :] + xl[:, None] * ux[None, :] + yl[:, None] * un[None, :]


def polyline_length(traj):
    return float(np.sum(np.linalg.norm(np.diff(traj, axis=0), axis=1)))


# ======================================================================
# Дуга по углу отклонения theta (градусы)
# ======================================================================
def _sagitta_from_angle(c_len, theta_rad):
    th = abs(theta_rad)
    if th < 1e-9:
        return 0.0
    return c_len * (1.0 - np.cos(th)) / (2.0 * np.sin(th))


def arc_length_from_angle(c_len, theta_rad):
    th = abs(theta_rad)
    if th < 1e-9:
        return c_len
    return c_len * th / np.sin(th)


def make_arc_by_angle(A, B, theta_deg, n_points=140):
    A, B = np.asarray(A, float), np.asarray(B, float)
    c_len = float(np.linalg.norm(B - A))
    sag = np.sign(theta_deg) * _sagitta_from_angle(c_len, np.radians(theta_deg))
    return make_arc(A, B, sag, n_points)


def max_deflection_angle(A, B, L_max):
    """Предельный угол theta_max (градусы): длина дуги = L_max (бинарный поиск)."""
    c_len = float(np.linalg.norm(np.asarray(B, float) - np.asarray(A, float)))
    lo, hi = 1e-4, np.pi - 1e-3
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if arc_length_from_angle(c_len, mid) <= L_max:
            lo = mid
        else:
            hi = mid
    return float(np.degrees(lo))


def _sample_angle(rng, theta_max, sigma, profile):
    """Случайный угол отклонения по профилю разброса."""
    if profile == "mixed":
        return float(rng.uniform(-theta_max, theta_max))
    if profile == "complex":
        mag = theta_max - abs(rng.normal(0.0, 0.30 * theta_max))
        mag = min(max(mag, 0.0), theta_max)
        return float(mag if rng.random() < 0.5 else -mag)
    # normal
    for _ in range(100):
        t = rng.normal(0.0, sigma)
        if abs(t) <= theta_max:
            return float(t)
    return float(np.clip(rng.normal(0.0, sigma), -theta_max, theta_max))


def sample_arc_trajectory(A, B, L_max, rng, sigma_frac=0.45, n_points=140,
                          theta_max=None, profile="normal"):
    """Случайная дуга по профилю. Возвращает (traj, theta_deg)."""
    if theta_max is None:
        theta_max = max_deflection_angle(A, B, L_max)
    sigma = max(sigma_frac * theta_max, 1e-3)
    theta = _sample_angle(rng, theta_max, sigma, profile)
    return make_arc_by_angle(A, B, theta, n_points), theta


def arc_fan(A, B, L_max, angle_step_deg, sigma_frac=0.45, n_points=140,
            profile="normal"):
    """Веер вероятных дуг с шагом угла. Вес = относительная частота при профиле.
    Предельные дуги +/- theta_max помечены is_extreme (рисуются пунктиром)."""
    theta_max = max_deflection_angle(A, B, L_max)
    sigma = max(sigma_frac * theta_max, 1e-3)
    thetas = [0.0]
    t = angle_step_deg
    while t < theta_max:
        thetas += [t, -t]; t += angle_step_deg
    thetas += [theta_max, -theta_max]
    thetas = sorted(set(round(x, 4) for x in thetas))

    def density(x):
        if profile == "mixed":
            return 1.0
        if profile == "complex":
            return np.exp(-0.5 * ((abs(x) - theta_max) / (0.30 * theta_max)) ** 2)
        return np.exp(-0.5 * (x / sigma) ** 2)

    dens = [density(x) for x in thetas]
    dmax = max(dens) if dens else 1.0
    return [dict(theta=x, traj=make_arc_by_angle(A, B, x, n_points),
                 weight=d / dmax,
                 is_extreme=abs(abs(x) - theta_max) < 1e-3,
                 label=f"{x:+.0f}°")
            for x, d in zip(thetas, dens)]


# ======================================================================
# Коридор движения (зона, в которой лежат все возможные маршруты)
#   arc        — область между предельными дугами +/- theta_max;
#   serpentine — область под огибающей петель |y| <= b*sin(pi*x/d).
# Кандидатные позиции датчиков и масштаб карты ограничиваются коридором.
# ======================================================================
def corridor_outline(A, B, L_max, traj_model="arc", n_points=160):
    """Верхняя и нижняя границы коридора движения: (upper, lower) — массивы точек."""
    A, B = np.asarray(A, float), np.asarray(B, float)
    if traj_model == "serpentine":
        d = float(np.linalg.norm(B - A))
        b = ellipse_b(A, B, L_max)
        x = np.linspace(0.0, d, n_points)
        y = b * np.sin(np.pi * x / d)
        up = np.column_stack([A[0] + x, A[1] + y])
        lo = np.column_stack([A[0] + x, A[1] - y])
        return up, lo
    tmax = max_deflection_angle(A, B, L_max)
    return (make_arc_by_angle(A, B, +tmax, n_points),
            make_arc_by_angle(A, B, -tmax, n_points))


def corridor_polygon(A, B, L_max, traj_model="arc", n_points=160):
    """Замкнутый многоугольник коридора движения."""
    up, lo = corridor_outline(A, B, L_max, traj_model, n_points)
    return np.vstack([up, lo[::-1]])


def _points_in_polygon(pts, poly):
    """Векторизованный точечно-в-многоугольнике (алгоритм лучевого пересечения)."""
    x = pts[:, 0]; y = pts[:, 1]
    inside = np.zeros(len(pts), bool)
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]; xj, yj = poly[j]
        cond = (yi > y) != (yj > y)
        denom = np.where(np.abs(yj - yi) < 1e-12, 1e-12, yj - yi)
        xint = (xj - xi) * (y - yi) / denom + xi
        inside ^= cond & (x < xint)
        j = i
    return inside


def corridor_filter(pts, A, B, L_max, traj_model="arc", n_points=160):
    """Маска точек, попадающих в коридор движения."""
    poly = corridor_polygon(A, B, L_max, traj_model, n_points)
    return _points_in_polygon(np.asarray(pts, float), poly)


def corridor_bbox(A, B, L_max, traj_model="arc", n_points=160):
    """Габариты коридора (x0, x1, y0, y1) для подгонки масштаба карты."""
    poly = corridor_polygon(A, B, L_max, traj_model, n_points)
    return (float(poly[:, 0].min()), float(poly[:, 0].max()),
            float(poly[:, 1].min()), float(poly[:, 1].max()))


# ======================================================================
# Этап 2: петляющее движение
# ======================================================================
def ellipse_b(A, B, L_max):
    c = 0.5 * float(np.linalg.norm(np.asarray(B, float) - np.asarray(A, float)))
    a = 0.5 * L_max
    return float(np.sqrt(max(a * a - c * c, 0.0)))


def make_serpentine(A, B, amplitude, n_lobes, phase, n_points=140):
    A, B = np.asarray(A, float), np.asarray(B, float)
    chord = B - A
    c_len = np.linalg.norm(chord)
    ux = chord / c_len
    un = np.array([-ux[1], ux[0]])
    t = np.linspace(0.0, 1.0, n_points)
    lateral = amplitude * np.sin(np.pi * t) * np.sin(np.pi * n_lobes * t + phase)
    base = A[None, :] + t[:, None] * chord[None, :]
    return base + lateral[:, None] * un[None, :]


def sample_serpentine_trajectory(A, B, L_max, rng, sigma_frac=0.45, n_points=140,
                                 theta_max=None, profile="normal"):
    """Случайная петляющая траектория по профилю. Возвращает (traj, feature)."""
    A2, B2 = np.asarray(A, float), np.asarray(B, float)
    b = ellipse_b(A2, B2, L_max)
    for _ in range(300):
        n_lobes = int(rng.integers(2, 6))
        phase = float(rng.uniform(0.0, np.pi))
        if profile == "mixed":
            amp = rng.uniform(0.0, b)
        elif profile == "complex":
            amp = b * (0.7 + 0.3 * rng.random())
        else:
            amp = abs(rng.normal(0.0, sigma_frac * b))
        traj = make_serpentine(A2, B2, amp, n_lobes, phase, n_points)
        if polyline_length(traj) <= L_max:
            return traj, signed_max_lateral(traj, A2, B2)
    return make_arc(A2, B2, 0.0, n_points), 0.0


def signed_max_lateral(traj, A, B):
    A, B = np.asarray(A, float), np.asarray(B, float)
    chord = B - A
    un = np.array([-chord[1], chord[0]]) / np.linalg.norm(chord)
    lateral = (traj - A) @ un
    return float(lateral[int(np.argmax(np.abs(lateral)))])


SAMPLERS = {
    "arc": sample_arc_trajectory,
    "serpentine": sample_serpentine_trajectory,
}
