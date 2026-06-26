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
import functools
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
    """Дуга A->B по знаковому касательно-хордовому углу theta (градусы).

    Прямая параметризация центральным углом: корректна для ЛЮБОГО theta, включая
    theta > 90° (большие дуги со стрелой > c/2), где формула через стрелу прогиба
    давала вырождение.
    """
    A, B = np.asarray(A, float), np.asarray(B, float)
    chord = B - A
    c_len = float(np.linalg.norm(chord))
    mid = 0.5 * (A + B)
    th = np.radians(theta_deg)
    if abs(th) < 1e-9:
        t = np.linspace(0.0, 1.0, n_points)
        return A[None, :] + t[:, None] * chord[None, :]
    ux = chord / c_len
    un = np.array([-ux[1], ux[0]])
    ath = abs(th)
    R = c_len / (2.0 * np.sin(ath))            # радиус дуги
    yc = -R * np.cos(ath)                       # центр окружности (лок. коорд.)
    alphas = np.linspace(-ath, ath, n_points)   # от A (-ath) к B (+ath)
    xl = R * np.sin(alphas)                      # локальная x в [-c/2, c/2]
    yl = np.sign(theta_deg) * (yc + R * np.cos(alphas))  # сторона по знаку угла
    return mid[None, :] + xl[:, None] * ux[None, :] + yl[:, None] * un[None, :]


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
def _reach_ellipse_halves(A, B, L_max, n_points=160):
    """Верхняя/нижняя половины эллипса достижимости A<->B (для манёвра)."""
    A, B = np.asarray(A, float), np.asarray(B, float)
    mid = 0.5 * (A + B)
    d = B - A
    L = float(np.linalg.norm(d))
    u = d / L if L > 1e-9 else np.array([1.0, 0.0])
    nrm = np.array([-u[1], u[0]])
    a = 0.5 * L_max
    b = ellipse_b(A, B, L_max)
    t = np.linspace(0.0, np.pi, n_points)
    base = mid[None, :] + a * np.cos(t)[:, None] * u[None, :]
    up = base + b * np.sin(t)[:, None] * nrm[None, :]
    lo = base - b * np.sin(t)[:, None] * nrm[None, :]
    return up, lo


def corridor_outline(A, B, L_max, traj_model="arc", n_points=160):
    """Верхняя и нижняя границы коридора движения: (upper, lower) — массивы точек."""
    A, B = np.asarray(A, float), np.asarray(B, float)
    if traj_model == "maneuver":
        return _reach_ellipse_halves(A, B, L_max, n_points)
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


@functools.lru_cache(maxsize=256)
def max_serpentine_amplitude(ab_distance, L_max, n_lobes, n_points=140):
    """Предельная амплитуда петли (для данного числа лепестков), при которой
    длина траектории = L_max. Аналог theta_max для дуг — гарантирует, что
    выбранная амплитуда укладывается в запас хода (никаких откатов в прямую)."""
    A = (0.0, 0.0); B = (float(ab_distance), 0.0)
    b = ellipse_b(A, B, L_max)
    lo, hi = 0.0, b
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        if polyline_length(make_serpentine(A, B, mid, n_lobes, np.pi / 2, n_points)) <= L_max:
            lo = mid
        else:
            hi = mid
    return lo


def sample_serpentine_trajectory(A, B, L_max, rng, sigma_frac=0.45, n_points=140,
                                 theta_max=None, profile="normal"):
    """Случайная петляющая траектория по профилю. Возвращает (traj, feature).

    Амплитуда выбирается в пределах ДОПУСТИМОЙ по запасу хода (max_serpentine_
    amplitude), поэтому все три профиля работают, а «сложный» даёт крупные петли.
    """
    A2, B2 = np.asarray(A, float), np.asarray(B, float)
    ab = float(np.linalg.norm(B2 - A2))
    n_lobes = int(rng.integers(2, 6))
    phase = float(rng.uniform(0.0, np.pi))
    amp_max = max_serpentine_amplitude(ab, float(L_max), n_lobes, n_points)
    if profile == "mixed":
        amp = rng.uniform(0.0, amp_max)
    elif profile == "complex":
        amp = amp_max * (0.7 + 0.3 * rng.random())
    else:  # normal — малые амплитуды чаще
        amp = min(abs(rng.normal(0.0, sigma_frac * amp_max)), amp_max)
    traj = make_serpentine(A2, B2, amp, n_lobes, phase, n_points)
    # страховка на случай влияния фазы на длину
    while polyline_length(traj) > L_max and amp > 1e-6:
        amp *= 0.9
        traj = make_serpentine(A2, B2, amp, n_lobes, phase, n_points)
    return traj, signed_max_lateral(traj, A2, B2)


def signed_max_lateral(traj, A, B):
    A, B = np.asarray(A, float), np.asarray(B, float)
    chord = B - A
    un = np.array([-chord[1], chord[0]]) / np.linalg.norm(chord)
    lateral = (traj - A) @ un
    return float(lateral[int(np.argmax(np.abs(lateral)))])


# ======================================================================
# Вероятные маршруты: «10 частых» (квантили распределения) и «веер по шагу»
# ======================================================================
def _profile_quantiles(profile, lim, sigma, n, seed=0):
    """n квантилей величины (угла/амплитуды) по профилю разброса.

    Детерминированно (фиксированный seed). lim — предел, sigma — ширина «обычного».
    Для амплитуды (одностороннее) знак потом задаётся отдельно.
    """
    rng = np.random.default_rng(seed)
    xs = np.array([_sample_angle(rng, lim, sigma, profile) for _ in range(4000)])
    return np.quantile(xs, (np.arange(n) + 0.5) / n)


def _arc_density(theta, profile, theta_max, sigma):
    if profile == "mixed":
        return 1.0
    if profile == "complex":
        return float(np.exp(-0.5 * ((abs(theta) - theta_max) / (0.30 * theta_max)) ** 2))
    return float(np.exp(-0.5 * (theta / sigma) ** 2))


def frequent_arcs(A, B, L_max, profile, sigma_frac, n=10, n_points=140):
    """10 наиболее вероятных дуг (квантили распределения углов по профилю)."""
    tmax = max_deflection_angle(A, B, L_max)
    sigma = max(sigma_frac * tmax, 1e-3)
    angles = _profile_quantiles(profile, tmax, sigma, n)
    dens = [_arc_density(a, profile, tmax, sigma) for a in angles]
    dmax = max(dens) or 1.0
    return [dict(traj=make_arc_by_angle(A, B, a, n_points), weight=d / dmax,
                 is_extreme=False, label=f"{a:+.0f}°")
            for a, d in zip(angles, dens)]


def frequent_serpentines(A, B, L_max, profile, sigma_frac, n=10, n_points=140):
    """10 представительных петель (квантили амплитуды по профилю)."""
    ab = float(np.linalg.norm(np.asarray(B, float) - np.asarray(A, float)))
    amp_max = max_serpentine_amplitude(ab, float(L_max), 3, n_points)
    qs = _profile_quantiles(profile, amp_max, max(sigma_frac * amp_max, 1e-3), n)
    out = []
    for i, a in enumerate(sorted(np.abs(qs))):
        sign = 1.0 if i % 2 == 0 else -1.0
        lobes = 2 + (i % 3)
        w = a / amp_max if amp_max else 0.0
        out.append(dict(traj=make_serpentine(A, B, sign * a, lobes, np.pi / 2, n_points),
                        weight=float(w), is_extreme=False, label=f"{a:.0f} км"))
    return out


def serpentine_fan(A, B, L_max, k=7, n_points=140):
    """Веер петель: амплитуды от 0 до предельной (крайняя — пунктир)."""
    ab = float(np.linalg.norm(np.asarray(B, float) - np.asarray(A, float)))
    amp_max = max_serpentine_amplitude(ab, float(L_max), 3, n_points)
    amps = np.linspace(0.0, amp_max, k)
    out = []
    for i, a in enumerate(amps):
        sign = 1.0 if i % 2 == 0 else -1.0
        out.append(dict(traj=make_serpentine(A, B, sign * a, 3, np.pi / 2, n_points),
                        weight=a / amp_max if amp_max else 0.0,
                        is_extreme=(i == len(amps) - 1), label=f"{a:.0f} км"))
    return out


# ======================================================================
# Манёвренное движение БПЛА самолётного типа (ломаная)
#   Случайные повороты курса с ОГРАНИЧЕНИЕМ радиуса разворота (rmin); наведение
#   на цель усиливается к концу пути; длина <= L_max. Профиль задаёт извилистость:
#   «обычный» — почти прямо, «смешанный» — зигзаг, «сложный» — максимально извилисто.
# ======================================================================
_MANEUVER_PROFILE = {
    "normal":  dict(lobes=1, amp=0.12),
    "mixed":   dict(lobes=2, amp=0.22),
    "complex": dict(lobes=3, amp=0.36),
}


def _maneuver_lateral(D, rng, profile, rmin_frac):
    """Случайная функция бокового отклонения lateral(t), t in [0,1], нули на концах.

    Боковое отклонение — сумма гармоник sin(jπt); амплитуда каждой ОГРАНИЧЕНА
    радиусом разворота rmin (кривизна ~ A*(jπ/D)^2 <= 1/rmin) — физика БПЛА.
    """
    par = _MANEUVER_PROFILE.get(profile, _MANEUVER_PROFILE["mixed"])
    lobes = par["lobes"] + int(rng.integers(0, 2))          # ±1 «волна» — неопределённость
    comps = []
    for j in range(1, lobes + 1):
        cap = D / (rmin_frac * (j * np.pi) ** 2)            # предел по радиусу разворота
        a = par["amp"] * D / j * rng.uniform(0.45, 1.0)     # случайная амплитуда
        a = min(a, 0.9 * cap) * (1.0 if rng.random() < 0.5 else -1.0)  # случайный знак
        comps.append((j, a))

    def lateral(t):
        tt = np.asarray(t, float)
        s = np.zeros_like(tt)
        for j, a in comps:
            s = s + a * np.sin(j * np.pi * tt)
        return s
    return lateral, lobes


def _fit_to_budget(S0, chord, nrm, lateral, t, L_max):
    """Уменьшать боковое отклонение, пока длина не уложится в запас хода L_max."""
    base = S0[None, :] + t[:, None] * chord[None, :]
    s = 1.0
    traj = base + (s * lateral(t))[:, None] * nrm[None, :]
    while polyline_length(traj) > L_max and s > 0.05:
        s *= 0.85
        traj = base + (s * lateral(t))[:, None] * nrm[None, :]
    return traj


def maneuver_path(S0, B, L_max, rng, profile="mixed", n_points=140, rmin_frac=0.11):
    """ГЛАДКИЙ манёвр БПЛА самолётного типа: плавная кривая (цепочка дуг) S0->B
    с ОГРАНИЧЕНИЕМ радиуса разворота. Концы закреплены, длина <= L_max."""
    S0 = np.asarray(S0, float); B = np.asarray(B, float)
    chord = B - S0; D = float(np.linalg.norm(chord))
    if D < 1e-6:
        return np.repeat(S0[None, :], n_points, axis=0)
    u = chord / D; nrm = np.array([-u[1], u[0]])
    lateral, _ = _maneuver_lateral(D, rng, profile, rmin_frac)
    t = np.linspace(0.0, 1.0, n_points)
    return _fit_to_budget(S0, chord, nrm, lateral, t, L_max)


def _resample_polyline(verts, n_points):
    """Равномерно по длине пересэмплировать ломаную к n_points точкам."""
    seg = np.linalg.norm(np.diff(verts, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    if total < 1e-9:
        return np.repeat(verts[:1], n_points, axis=0)
    s = np.linspace(0.0, total, n_points)
    return np.column_stack([np.interp(s, cum, verts[:, 0]),
                            np.interp(s, cum, verts[:, 1])])


def polyline_path(S0, B, L_max, rng, profile="mixed", n_points=140, rmin_frac=0.11):
    """ЛОМАНАЯ S0->B: прямые отрезки через вершины (угловой манёвр), длина <= L_max."""
    S0 = np.asarray(S0, float); B = np.asarray(B, float)
    chord = B - S0; D = float(np.linalg.norm(chord))
    if D < 1e-6:
        return np.repeat(S0[None, :], n_points, axis=0)
    u = chord / D; nrm = np.array([-u[1], u[0]])
    lateral, lobes = _maneuver_lateral(D, rng, profile, rmin_frac)
    tv = np.linspace(0.0, 1.0, 2 * lobes + 3)               # вершины ломаной
    s = 1.0
    while True:
        verts = (S0[None, :] + tv[:, None] * chord[None, :]
                 + (s * lateral(tv))[:, None] * nrm[None, :])
        if polyline_length(verts) <= L_max or s < 0.05:
            break
        s *= 0.85
    return _resample_polyline(verts, n_points)


def sample_maneuver_trajectory(A, B, L_max, rng, sigma_frac=0.45, n_points=140,
                               theta_max=None, profile="normal"):
    """Случайный гладкий манёвр A->B. Возвращает (traj, feature)."""
    traj = maneuver_path(A, B, L_max, rng, profile, n_points)
    return traj, signed_max_lateral(traj, A, B)


def sample_polyline_trajectory(A, B, L_max, rng, sigma_frac=0.45, n_points=140,
                               theta_max=None, profile="normal"):
    """Случайная ломаная A->B. Возвращает (traj, feature)."""
    traj = polyline_path(A, B, L_max, rng, profile, n_points)
    return traj, signed_max_lateral(traj, A, B)


def frequent_maneuvers(A, B, L_max, profile, sigma_frac=0.45, n=10, n_points=140,
                       kind="maneuver"):
    """n представительных манёвров/ломаных (детерминированно)."""
    fn = polyline_path if kind == "polyline" else maneuver_path
    rng = np.random.default_rng(4321)
    return [dict(traj=fn(A, B, L_max, rng, profile, n_points),
                 weight=0.6, is_extreme=False, label=kind)
            for _ in range(n)]


def maneuver_fan(A, B, L_max, profile="mixed", n_points=140, k=14, kind="maneuver"):
    """Веер манёвров/ломаных (геометрия пространства манёвров)."""
    fn = polyline_path if kind == "polyline" else maneuver_path
    rng = np.random.default_rng(321)
    return [dict(traj=fn(A, B, L_max, rng, profile, n_points),
                 weight=0.5, is_extreme=False, label=kind)
            for _ in range(k)]


SAMPLERS = {
    "arc": sample_arc_trajectory,
    "serpentine": sample_serpentine_trajectory,
    "maneuver": sample_maneuver_trajectory,
    "polyline": sample_polyline_trajectory,
}
