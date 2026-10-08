# -*- coding: utf-8 -*-
"""MODEL · Время подлёта к цели по ТТХ типа БПЛА — ПОКАЗАТЕЛЬ «Анализа», расстановка его не видит (журнал 365).

Маршрут выборки — ломаная от точки входа до цели, км; время подлёта t = L / v, где L — длина ломаной, v — крейсерская
скорость типа БПЛА. Пример: маршрут 145 км при 180 км/ч — 48 мин. Без Qt.
"""
import numpy as np


def route_length_km(route):
    """Длина ломаной маршрута. Вход: точки (M, 2), км. Отдаёт: длина, км (меньше двух точек — 0)."""
    r = np.asarray(route, float)                      # точки маршрута, км
    if len(r) < 2:                                    # отрезков нет
        return 0.0
    return float(np.hypot(*np.diff(r[:, :2], axis=0).T).sum())


def approach_times(routes, v_kmh):
    """Время подлёта по выборке. Вход: маршруты [(M, 2) км], скорость, км/ч.
    Отдаёт: dict n, v_kmh, L_med_km, t_min, t_med, t_max (минуты); маршрутов нет или скорость ≤ 0 — None."""
    v = float(v_kmh or 0.0)                           # крейсерская скорость, км/ч
    L = np.array([route_length_km(r) for r in routes or []], float)   # длины маршрутов, км
    if not len(L) or v <= 0:                          # считать не по чему
        return None
    t = L / v * 60.0                                  # время подлёта каждого маршрута, мин
    return dict(n=int(len(L)), v_kmh=v, L_med_km=float(np.median(L)), t_min=float(t.min()),
                t_med=float(np.median(t)), t_max=float(t.max()))
