# -*- coding: utf-8 -*-
"""
MODEL · Модель обнаружения и метрики.

Обнаружение — однократный факт входа траектории в зону радиусом R хотя бы
одного датчика; повторный вход той же траектории в зону того же датчика не
учитывается. Число обнаружений n(S, gamma) равно числу РАЗЛИЧНЫХ засёкших
датчиков.

Здесь — эталонные (прямые) реализации метрик. Для быстрой оптимизации служит
векторизованная структура покрытия в optimization.py; настоящий модуль
используется для точного расчёта итоговых показателей и как опорная проверка.
"""
import numpy as np


def _min_dist_point_to_sensors(traj, sensors):
    """Матрица расстояний (n_points, n_sensors)."""
    if len(sensors) == 0:
        return np.full((len(traj), 1), np.inf)
    S = np.asarray(sensors, float)
    diff = traj[:, None, :] - S[None, :, :]
    return np.linalg.norm(diff, axis=2)


def n_detections(traj, sensors, R):
    """Число РАЗЛИЧНЫХ датчиков, в зону которых траектория входит хотя бы раз."""
    if len(sensors) == 0:
        return 0
    d = _min_dist_point_to_sensors(traj, sensors)      # (точки, датчики)
    hit_per_sensor = np.any(d <= R, axis=0)            # засёк ли датчик
    return int(np.sum(hit_per_sensor))


def continuous_coverage(traj, sensors, R):
    """Доля длины пути под наблюдением [0..1] — процент сопровождения.

    Отличается от дискретного счёта засечек: отвечает на вопрос
    «какую часть пути БПЛА ведётся».
    """
    if len(sensors) == 0:
        return 0.0
    d = _min_dist_point_to_sensors(traj, sensors)
    covered_pt = np.any(d <= R, axis=1)
    seg_len = np.linalg.norm(np.diff(traj, axis=0), axis=1)
    seg_cov = covered_pt[:-1] & covered_pt[1:]         # сегмент покрыт, если оба конца
    total = np.sum(seg_len)
    if total <= 0:
        return 0.0
    return float(np.sum(seg_len[seg_cov]) / total)


def segment_coverage(traj, sensors, R, L_segments):
    """Доля покрытых сегментов равной длины [0..1] — мера равномерности m/L_seg."""
    if len(sensors) == 0:
        return 0.0
    d = _min_dist_point_to_sensors(traj, sensors)
    covered_pt = np.any(d <= R, axis=1)
    idx = np.array_split(np.arange(len(traj)), L_segments)
    covered_seg = sum(1 for ix in idx if len(ix) and covered_pt[ix].any())
    return covered_seg / L_segments


def utility(traj, sensors, R, k, L_segments, weights):
    """Полезность расположения на одном маршруте phi(S, gamma) in [0..1].

    weights = (alpha1, alpha2).
    """
    a1, a2 = weights
    n = n_detections(traj, sensors, R)
    m = segment_coverage(traj, sensors, R, L_segments)
    return a1 * min(n, k) / k + a2 * m


def avg_utility(trajectories, sensors, R, k, L_segments, weights):
    """Эмпирическое среднее полезности по выборке F_t(S) = (1/t) Σ phi."""
    if not trajectories:
        return 0.0
    return float(np.mean([utility(t, sensors, R, k, L_segments, weights)
                          for t in trajectories]))
