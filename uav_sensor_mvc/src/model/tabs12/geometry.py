# -*- coding: utf-8 -*-
"""
MODEL · Геометрия эллипса достижимости и привязка карты.

Множество всех допустимых траекторий ограничено эллипсом достижимости
    E = { P : |PA| + |PB| <= L_max }
с фокусами A, B, большой полуосью a = L_max/2, фокальным параметром
c = |AB|/2 и малой полуосью b = sqrt(a^2 - c^2).

Любой путь длиной <= L_max целиком лежит в этом эллипсе: для точки P на пути
|PA| <= (длина A->P) и |PB| <= (длина P->B), значит |PA|+|PB| <= длина <= L_max.
Поэтому ограничение по запасу хода автоматически удерживает траекторию внутри
эллипса — отдельной проверки «не выходит за эллипс» не требуется.
"""
import numpy as np


def ellipse_geometry(A, B, L_max):
    """Параметры эллипса достижимости: центр, полуоси, угол поворота."""
    A, B = np.asarray(A, float), np.asarray(B, float)
    center = 0.5 * (A + B)
    c = 0.5 * np.linalg.norm(B - A)
    a = 0.5 * L_max
    if a <= c:
        raise ValueError("Запас хода меньше расстояния AB: траектория невозможна.")
    b = np.sqrt(a * a - c * c)
    angle = np.arctan2(B[1] - A[1], B[0] - A[0])
    return dict(center=center, a=a, b=b, c=c, angle=angle)


def point_in_ellipse(P, A, B, L_max):
    P, A, B = np.asarray(P, float), np.asarray(A, float), np.asarray(B, float)
    return np.linalg.norm(P - A) + np.linalg.norm(P - B) <= L_max + 1e-9


def points_in_ellipse(P, A, B, L_max):
    """Векторизованная проверка принадлежности массива точек (M, 2)."""
    P = np.asarray(P, float)
    A, B = np.asarray(A, float), np.asarray(B, float)
    dA = np.linalg.norm(P - A, axis=1)
    dB = np.linalg.norm(P - B, axis=1)
    return (dA + dB) <= L_max + 1e-9


def auto_axes_limits(geom, margin=0.10):
    """Границы осей карты, автоматически подогнанные под эллипс достижимости.

    Возвращает (xlim, ylim) с равным масштабом по осям и заданным запасом.
    Карта масштабируется автоматически при изменении L_max и |AB|.
    """
    cx, cy = geom["center"]
    a, b = geom["a"], geom["b"]
    pad = margin * max(a, b)
    half = max(a, b) + pad
    return (cx - half, cx + half), (cy - half, cy + half)


# ----------------------------------------------------------------------
# Гео-привязка: широта/долгота -> локальные километры (равнопромежуточная
# проекция вокруг средней точки). Для небольших районов погрешность мала.
# ----------------------------------------------------------------------
def geo_to_local_km(A_geo, B_geo):
    """Перевод двух гео-точек (широта, долгота) в локальные км.

    Возвращает (A_km, B_km, lat0, lon0): A в начале координат, ось X — на восток,
    ось Y — на север. Используется только когда координаты заданы в градусах.
    """
    lat_a, lon_a = A_geo
    lat_b, lon_b = B_geo
    lat0 = 0.5 * (lat_a + lat_b)
    lon0 = 0.5 * (lon_a + lon_b)
    kx = 111.320 * np.cos(np.radians(lat0))      # км на градус долготы
    ky = 110.574                                  # км на градус широты
    A_km = np.array([(lon_a - lon0) * kx, (lat_a - lat0) * ky])
    B_km = np.array([(lon_b - lon0) * kx, (lat_b - lat0) * ky])
    return A_km, B_km, lat0, lon0
