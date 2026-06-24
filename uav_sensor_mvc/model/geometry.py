# -*- coding: utf-8 -*-
"""
MODEL · Геометрия эллипса достижимости.

Множество всех допустимых траекторий ограничено эллипсом достижимости
    E = { P : |PA| + |PB| <= L_max }
с фокусами A, B, большой полуосью a = L_max/2, фокальным параметром
c = |AB|/2 и малой полуосью b = sqrt(a^2 - c^2).
"""
import numpy as np


def ellipse_geometry(A, B, L_max):
    """Параметры эллипса достижимости: центр, полуоси, угол поворота.

    Возвращает словарь: center (2,), a, b, c, angle (рад).
    """
    A, B = np.asarray(A, float), np.asarray(B, float)
    center = 0.5 * (A + B)
    c = 0.5 * np.linalg.norm(B - A)          # полу-межфокусное расстояние
    a = 0.5 * L_max                          # большая полуось
    if a <= c:
        raise ValueError("Запас хода меньше расстояния AB: траектория невозможна.")
    b = np.sqrt(a * a - c * c)               # малая полуось
    angle = np.arctan2(B[1] - A[1], B[0] - A[0])
    return dict(center=center, a=a, b=b, c=c, angle=angle)


def point_in_ellipse(P, A, B, L_max):
    """Принадлежность точки эллипсу достижимости (скаляр)."""
    P, A, B = np.asarray(P, float), np.asarray(A, float), np.asarray(B, float)
    return np.linalg.norm(P - A) + np.linalg.norm(P - B) <= L_max + 1e-9


def points_in_ellipse(P, A, B, L_max):
    """Векторизованная проверка принадлежности массива точек (M, 2)."""
    P = np.asarray(P, float)
    A, B = np.asarray(A, float), np.asarray(B, float)
    dA = np.linalg.norm(P - A, axis=1)
    dB = np.linalg.norm(P - B, axis=1)
    return (dA + dB) <= L_max + 1e-9
