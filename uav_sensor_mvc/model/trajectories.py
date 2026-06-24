# -*- coding: utf-8 -*-
"""
MODEL · Генераторы случайных маршрутов БПЛА.

Этап 1 — пучок круговых дуг A->B, задаваемых знаковой стрелой прогиба s
(s = 0 — прямая). Стрела берётся из усечённого нормального распределения,
ширина пропорциональна предельной стреле s_max, что концентрирует пучок
у прямой A-B.

Этап 2 (демонстрация расширяемости архитектуры) — петляющее движение:
синусоидальная огибающая с закреплёнными концами A, B, случайной частотой,
амплитудой и фазой; длина ограничена L_max. Метрики, целевая функция и
метод оптимизации при смене генератора НЕ меняются.

Маршруты независимы между собой и от расположения датчиков.
"""
import numpy as np


# ======================================================================
# Этап 1: круговые дуги
# ======================================================================
def make_arc(A, B, sagitta, n_points=120):
    """Круговая дуга от A до B с заданной знаковой стрелой прогиба.

    sagitta = 0 -> прямая. Возвращает массив точек (n_points, 2).
    """
    A, B = np.asarray(A, float), np.asarray(B, float)
    chord = B - A
    c_len = np.linalg.norm(chord)
    midpoint = 0.5 * (A + B)                      # центр хорды — начало лок. коорд.
    t = np.linspace(0.0, 1.0, n_points)
    if abs(sagitta) < 1e-9:
        return A[None, :] + t[:, None] * chord[None, :]
    ux = chord / c_len                            # ось вдоль хорды
    un = np.array([-ux[1], ux[0]])                # нормаль к хорде
    s = float(sagitta)
    R = abs(s) / 2.0 + c_len ** 2 / (8.0 * abs(s))     # радиус дуги
    yc = abs(s) / 2.0 - c_len ** 2 / (8.0 * abs(s))    # центр окружности (лок.)
    th_a = np.arctan2(0.0 - yc, -c_len / 2.0)          # угол к A
    th_b = np.arctan2(0.0 - yc, +c_len / 2.0)          # угол к B
    thetas = np.linspace(th_a, th_b, n_points)
    xl = R * np.cos(thetas)                       # локальная x в [-c/2, c/2]
    yl = np.sign(s) * (yc + R * np.sin(thetas))   # локальная y, знак -> сторона
    pts = midpoint[None, :] + xl[:, None] * ux[None, :] + yl[:, None] * un[None, :]
    return pts


def polyline_length(traj):
    """Длина ломаной (траектории)."""
    return float(np.sum(np.linalg.norm(np.diff(traj, axis=0), axis=1)))


def max_sagitta(A, B, L_max, n_points=120):
    """Предельная стрела прогиба, при которой длина дуги = L_max (бинарный поиск)."""
    lo, hi = 0.0, L_max
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if polyline_length(make_arc(A, B, mid, n_points)) <= L_max:
            lo = mid
        else:
            hi = mid
    return lo


def sample_arc_trajectory(A, B, L_max, rng, sigma_frac=0.35,
                          n_points=120, s_max=None):
    """Случайная дуга: стрела ~ усечённое нормальное в пределах допустимого.

    s_max можно передать заранее (кэш), чтобы не пересчитывать на каждом вызове.
    Возвращает (traj, sagitta).
    """
    if s_max is None:
        s_max = max_sagitta(A, B, L_max, n_points)
    sigma = sigma_frac * s_max
    while True:
        s = rng.normal(0.0, sigma)
        if abs(s) <= s_max:
            traj = make_arc(A, B, s, n_points)
            if polyline_length(traj) <= L_max:
                return traj, s


# ======================================================================
# Этап 2: петляющее (serpentine) движение
# ======================================================================
def make_serpentine(A, B, amplitude, n_lobes, phase, n_points=120):
    """Петляющая траектория A->B: синусоида с огибающей sin(pi*t),
    закрепляющей оба конца (нулевое отклонение в A и B).

    amplitude — амплитуда боковых отклонений; n_lobes — число полупетель.
    """
    A, B = np.asarray(A, float), np.asarray(B, float)
    chord = B - A
    c_len = np.linalg.norm(chord)
    ux = chord / c_len
    un = np.array([-ux[1], ux[0]])
    t = np.linspace(0.0, 1.0, n_points)
    envelope = np.sin(np.pi * t)                  # 0 на концах, 1 в середине
    lateral = amplitude * envelope * np.sin(np.pi * n_lobes * t + phase)
    base = A[None, :] + t[:, None] * chord[None, :]
    return base + lateral[:, None] * un[None, :]


def sample_serpentine_trajectory(A, B, L_max, rng, sigma_frac=0.35,
                                 n_points=120, s_max=None):
    """Случайная петляющая траектория с ограничением длины L_max.

    Сигнатура совместима с sample_arc_trajectory (взаимозаменяемые генераторы).
    Возвращает (traj, feature), где feature — знаковое макс. боковое отклонение.
    """
    if s_max is None:
        s_max = max_sagitta(A, B, L_max, n_points)
    amp_scale = max(s_max, 1e-6)
    for _ in range(200):
        n_lobes = int(rng.integers(2, 6))                    # 2..5 полупетель
        phase = float(rng.uniform(0.0, np.pi))
        amp = abs(rng.normal(0.0, sigma_frac * amp_scale))
        traj = make_serpentine(A, B, amp, n_lobes, phase, n_points)
        if polyline_length(traj) <= L_max:
            feat = signed_max_lateral(traj, A, B)
            return traj, feat
    # запасной вариант: прямая
    return make_arc(A, B, 0.0, n_points), 0.0


# ======================================================================
# Признак маршрута для группировки «частых пролётов» (любой генератор)
# ======================================================================
def signed_max_lateral(traj, A, B):
    """Знаковое максимальное боковое отклонение маршрута от хорды AB.

    Скалярный признак для гистограммы частых пролётов, пригодный как для
    дуг, так и для петель.
    """
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
