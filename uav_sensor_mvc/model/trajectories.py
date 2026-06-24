# -*- coding: utf-8 -*-
"""
MODEL · Генераторы случайных маршрутов БПЛА.

ЭТАП 1 — пучок круговых дуг, параметризованных УГЛОМ ОТКЛОНЕНИЯ theta
(касательно-хордовый угол в точке A, в градусах):
  * theta = 0       — полёт по прямой A->B;
  * |theta| растёт  — дуга выгибается сильнее, её длина увеличивается;
  * theta_max       — предельный угол, при котором длина дуги = L_max.

Связь величин для хорды c = |AB| и угла theta (рад):
  радиус дуги     Rc = c / (2 sin theta)
  длина дуги      L(theta) = c * theta / sin theta   (монотонна по theta)
  стрела прогиба  s(theta) = c (1 - cos theta) / (2 sin theta)

Пример: |AB| = 100 км, запас хода 150 км. БПЛА может лететь по прямой, либо
отклониться на небольшой угол (малая дуга), либо сильнее — пока хватает запаса
хода. Предельные дуги (+/- theta_max) на карте помечаются ПУНКТИРОМ.

ЭТАП 2 — петляющее движение: синусоида с закреплёнными концами A, B, случайной
частотой, амплитудой и фазой; длина ограничена L_max. Метрики, целевая функция
и метод оптимизации при смене генератора НЕ меняются.

Любой маршрут длиной <= L_max автоматически лежит внутри эллипса достижимости
(см. geometry.py), поэтому отдельная проверка «не выходит за эллипс» не нужна.
"""
import numpy as np


# ======================================================================
# Базовая дуга по стреле прогиба (используется обоими параметризациями)
# ======================================================================
def make_arc(A, B, sagitta, n_points=140):
    """Круговая дуга от A до B с заданной знаковой стрелой прогиба.

    sagitta = 0 -> прямая. Возвращает массив точек (n_points, 2).
    """
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
    """Длина ломаной (траектории)."""
    return float(np.sum(np.linalg.norm(np.diff(traj, axis=0), axis=1)))


# ======================================================================
# Параметризация дуги УГЛОМ отклонения theta (градусы)
# ======================================================================
def _sagitta_from_angle(c_len, theta_rad):
    """Стрела прогиба по касательно-хордовому углу theta (без знака)."""
    th = abs(theta_rad)
    if th < 1e-9:
        return 0.0
    return c_len * (1.0 - np.cos(th)) / (2.0 * np.sin(th))


def arc_length_from_angle(c_len, theta_rad):
    """Длина дуги L(theta) = c * theta / sin theta."""
    th = abs(theta_rad)
    if th < 1e-9:
        return c_len
    return c_len * th / np.sin(th)


def make_arc_by_angle(A, B, theta_deg, n_points=140):
    """Дуга по знаковому углу отклонения theta (в градусах)."""
    A, B = np.asarray(A, float), np.asarray(B, float)
    c_len = float(np.linalg.norm(B - A))
    th = np.radians(theta_deg)
    sag = np.sign(theta_deg) * _sagitta_from_angle(c_len, th)
    return make_arc(A, B, sag, n_points)


def max_deflection_angle(A, B, L_max):
    """Предельный угол отклонения theta_max (градусы), при котором длина дуги
    достигает L_max. Бинарный поиск по монотонной L(theta)."""
    c_len = float(np.linalg.norm(np.asarray(B, float) - np.asarray(A, float)))
    lo, hi = 1e-4, np.pi - 1e-3
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if arc_length_from_angle(c_len, mid) <= L_max:
            lo = mid
        else:
            hi = mid
    return float(np.degrees(lo))


def sample_arc_trajectory(A, B, L_max, rng, sigma_frac=0.45,
                          n_points=140, theta_max=None):
    """Случайная дуга: угол отклонения ~ усечённое нормальное в [-theta_max, theta_max].

    Возвращает (traj, theta_deg). Длина гарантированно <= L_max.
    """
    if theta_max is None:
        theta_max = max_deflection_angle(A, B, L_max)
    sigma = max(sigma_frac * theta_max, 1e-3)
    while True:
        theta = rng.normal(0.0, sigma)
        if abs(theta) <= theta_max:
            return make_arc_by_angle(A, B, theta, n_points), float(theta)


def arc_fan(A, B, L_max, angle_step_deg, sigma_frac=0.45, n_points=140):
    """Веер вероятных дуг с шагом угла angle_step_deg.

    Возвращает список словарей: theta, traj, weight (плотность, max=1),
    is_extreme (крайняя дуга +/- theta_max -> рисуется пунктиром).
    """
    theta_max = max_deflection_angle(A, B, L_max)
    sigma = max(sigma_frac * theta_max, 1e-3)
    thetas = [0.0]
    t = angle_step_deg
    while t < theta_max:
        thetas += [t, -t]
        t += angle_step_deg
    thetas += [theta_max, -theta_max]              # крайние дуги
    thetas = sorted(set(round(x, 4) for x in thetas))
    dens = [np.exp(-0.5 * (x / sigma) ** 2) for x in thetas]
    dmax = max(dens) if dens else 1.0
    fan = []
    for x, d in zip(thetas, dens):
        fan.append(dict(
            theta=x,
            traj=make_arc_by_angle(A, B, x, n_points),
            weight=d / dmax,
            is_extreme=abs(abs(x) - theta_max) < 1e-3,
        ))
    return fan


# ======================================================================
# Этап 2: петляющее (serpentine) движение
# ======================================================================
def make_serpentine(A, B, amplitude, n_lobes, phase, n_points=140):
    """Петляющая траектория A->B: синусоида с огибающей sin(pi*t),
    закрепляющей оба конца (нулевое отклонение в A и B)."""
    A, B = np.asarray(A, float), np.asarray(B, float)
    chord = B - A
    c_len = np.linalg.norm(chord)
    ux = chord / c_len
    un = np.array([-ux[1], ux[0]])
    t = np.linspace(0.0, 1.0, n_points)
    envelope = np.sin(np.pi * t)
    lateral = amplitude * envelope * np.sin(np.pi * n_lobes * t + phase)
    base = A[None, :] + t[:, None] * chord[None, :]
    return base + lateral[:, None] * un[None, :]


def sample_serpentine_trajectory(A, B, L_max, rng, sigma_frac=0.45,
                                 n_points=140, theta_max=None):
    """Случайная петляющая траектория с ограничением длины L_max.

    Сигнатура совместима с sample_arc_trajectory. Возвращает (traj, feature),
    где feature — знаковое макс. боковое отклонение (для группировки пролётов).
    """
    A2, B2 = np.asarray(A, float), np.asarray(B, float)
    c_len = float(np.linalg.norm(B2 - A2))
    g = ellipse_b(A2, B2, L_max)
    amp_scale = max(g, 1e-6)
    for _ in range(300):
        n_lobes = int(rng.integers(2, 6))
        phase = float(rng.uniform(0.0, np.pi))
        amp = abs(rng.normal(0.0, sigma_frac * amp_scale))
        traj = make_serpentine(A2, B2, amp, n_lobes, phase, n_points)
        if polyline_length(traj) <= L_max:
            return traj, signed_max_lateral(traj, A2, B2)
    return make_arc(A2, B2, 0.0, n_points), 0.0


def ellipse_b(A, B, L_max):
    """Малая полуось эллипса (масштаб амплитуды петель)."""
    c = 0.5 * float(np.linalg.norm(np.asarray(B, float) - np.asarray(A, float)))
    a = 0.5 * L_max
    return float(np.sqrt(max(a * a - c * c, 0.0)))


# ======================================================================
# Признак маршрута для группировки «вероятных пролётов» (любой генератор)
# ======================================================================
def signed_max_lateral(traj, A, B):
    """Знаковое максимальное боковое отклонение маршрута от хорды AB."""
    A, B = np.asarray(A, float), np.asarray(B, float)
    chord = B - A
    c_len = np.linalg.norm(chord)
    un = np.array([-chord[1], chord[0]]) / c_len
    lateral = (traj - A) @ un
    j = int(np.argmax(np.abs(lateral)))
    return float(lateral[j])


# Реестр генераторов: ключ модели -> функция выборки маршрута
SAMPLERS = {
    "arc": sample_arc_trajectory,
    "serpentine": sample_serpentine_trajectory,
}
