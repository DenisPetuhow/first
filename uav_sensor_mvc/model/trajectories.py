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
                          theta_max=None, profile="normal", r_min=0.5, nrange=(2, 5),
                          law="points"):
    """Случайная дуга по профилю. Возвращает (traj, theta_deg)."""
    if theta_max is None:
        theta_max = max_deflection_angle(A, B, L_max)
    profile = _pick_mode(profile, rng)               # «сбалансированный» -> случайный режим
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
                                 theta_max=None, profile="normal", r_min=0.5,
                                 nrange=(2, 5), law="points"):
    """Случайная петляющая траектория по профилю. Возвращает (traj, feature).

    Амплитуда выбирается в пределах ДОПУСТИМОЙ по запасу хода (max_serpentine_
    amplitude), поэтому все три профиля работают, а «сложный» даёт крупные петли.
    """
    profile = _pick_mode(profile, rng)               # «сбалансированный» -> случайный режим
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
# Манёвренное движение БПЛА самолётного типа (по ПУТЕВЫМ ТОЧКАМ)
#   Физика: минимальный радиус разворота R_min = V²/(g·tan φ) (абсолютный, км).
#   Боковой вынос ограничен ЛИНЗОЙ h_max(s)=ρ·D·4·(s/D)(1−s/D) — ноль на концах,
#   максимум в центре. Путь: S0 → n путевых точек → B прямыми отрезками; в «манёвре»
#   углы скруглены дугами радиуса R_min (кривизна ≤ 1/R_min), в «ломаной» — нет.
#   Четыре режима задают число точек n, ширину линзы ρ и рисунок знаков;
#   «сбалансированный» — случайный выбор одного из первых трёх каждую итерацию
#   (поэтому встречаются и прямые маршруты).
# ======================================================================
GRAVITY = 9.81  # м/с²


def turn_radius_km(speed_ms, bank_deg):
    """Минимальный радиус координированного разворота R_min = V²/(g·tan φ), км."""
    tan = np.tan(np.radians(max(float(bank_deg), 1.0)))
    return float(speed_ms ** 2 / (GRAVITY * tan) / 1000.0)


# Режимы разброса: амплитуда выноса ρ, чередование знаков (зигзаг), мин. доля u_min,
# пологость дуги turn. РАЗБРОС = АМПЛИТУДА отклонения от прямой (НЕ число точек!).
#   rho  — амплитуда выноса как ДОЛЯ ОТ b (макс. достижимого бокового выноса —
#          полу-малой оси эллипса достижимости): малый ~0.1·b (жмётся к прямой),
#          сложный ~b (достаёт до краёв эллипса). База — b, а не хорда D, поэтому
#          вынос укладывается в запас хода и НЕ схлопывается фитом в прямую.
#   umin — мин. доля выноса (ближе к 1 -> точки у края, заход с разных углов);
#   turn — пологость дуги (доля отрезка под скругление);
#   alt  — чередование знаков (для сложного: уход в разные стороны эллипса).
# Число точек задаётся ОТДЕЛЬНО диапазоном nrange (поля пользователя).
_WP_MODE = {
    "normal":  dict(rho=(0.08, 0.25), turn=0.40, alt=False, umin=0.45),
    "mixed":   dict(rho=(0.32, 0.62), turn=0.30, alt=False, umin=0.65),
    "complex": dict(rho=(0.70, 1.05), turn=0.22, alt=True,  umin=0.90),
}
# «смешанный»: сложный (к краям) преобладает, иначе маршруты жмутся к центру,
# т.к. малый+средний (оба ближе к оси) суммарно перевешивали бы.
_BALANCED_P = [("normal", 0.25), ("mixed", 0.25), ("complex", 0.50)]


def _pick_mode(profile, rng):
    """«сбалансированный» = случайный выбор режима каждую итерацию (есть и прямые)."""
    if profile == "balanced":
        keys = [k for k, _ in _BALANCED_P]
        return keys[int(rng.choice(len(keys), p=[p for _, p in _BALANCED_P]))]
    return profile if profile in _WP_MODE else "mixed"


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


def _waypoints(S0, B, L_max, rng, mode, r_min, nrange, s_min=0.05):
    """Путевые точки. Число точек — СЛУЧАЙНО в пользовательском диапазоне nrange
    (со случайным расстоянием между ними); РЕЖИМ задаёт АМПЛИТУДУ отклонения, а не
    число точек. Амплитуда отсчитывается от b — макс. достижимого бокового выноса
    (полу-малой оси эллипса достижимости), поэтому сложный достаёт до краёв эллипса.

    s_min — минимальная доля пути S0->B, начиная с которой допускаются точки
    (во вкладке 2 = доля выхода из зоны старта; точки не спавнятся внутри неё).

    Возвращает (centers, offs, turn).
    """
    chord = B - S0; D = float(np.linalg.norm(chord))
    e_par = chord / D; e_perp = np.array([-e_par[1], e_par[0]])
    b_lat = ellipse_b(S0, B, L_max)          # макс. боковой вынос (полу-малая ось)
    par = _WP_MODE[mode]
    n_lo = max(0, int(nrange[0])); n_hi = max(n_lo, int(nrange[1]))
    n_phys = max(n_lo, int(D / max(4.0 * r_min, 1e-6)))           # физический предел
    n_hi = min(n_hi, n_phys)
    n = int(rng.integers(n_lo, n_hi + 1)) if n_hi >= n_lo else n_lo
    rho = rng.uniform(*par["rho"])
    if par["alt"]:
        # сложный = МАКС. отклонение: 1–2 КРУПНЫХ лепестка к краю эллипса. Точки
        # одной стороны образуют большой вынос ~b (а не узкий зигзаг, который при
        # многих точках не успевает уйти от центра в пределах L_max). Разные маршруты
        # выбирают разную сторону -> огибающая заполняет эллипс с разных направлений.
        side = 1.0 if rng.random() < 0.5 else -1.0
        if n <= 1 or rng.random() < 0.6:
            signs = [side] * n                                    # один крупный лепесток
        else:
            cut = int(rng.integers(1, n))                        # смена стороны (S-образно)
            signs = [side if i < cut else -side for i in range(n)]
    else:
        signs = [1.0 if rng.random() < 0.5 else -1.0 for _ in range(n)]
    # доступный диапазон долей пути: [s_lo, 0.95]. s_lo>0.05 -> точки только ЗА
    # зоной старта (режим "outside"); s_lo=0.05 -> как раньше (точки по всему пути).
    s_lo = max(0.05, min(float(s_min), 0.6))
    span = max(0.95 - s_lo, 1e-3)
    denom = max(1.0 - s_lo, 1e-6)
    centers, offs = [], []
    for i in range(n):
        # стратификация в доступном диапазоне [s_lo, 0.95] со случайным сдвигом
        frac = float(np.clip(s_lo + span * (i + 1 + rng.uniform(-0.35, 0.35)) / (n + 1),
                             s_lo, 0.95))
        s = frac * D
        # ЛИНЗА якорится на ДОСТУПНОМ диапазоне [s_lo, 1] (а не на всём пути): вынос
        # = 0 у границы зоны и у B, максимум — посередине доступного диапазона. Это
        # сохраняет ПОЛНУЮ амплитуду «разброса» даже когда точки сдвинуты за зону.
        x = (frac - s_lo) / denom
        u = rng.uniform(par["umin"], 1.0)
        h = signs[i] * u * (rho * b_lat * 4.0 * x * (1.0 - x))   # база — b (см. _WP_MODE)
        centers.append(S0 + s * e_par); offs.append(h * e_perp)
    return (np.array(centers).reshape(-1, 2), np.array(offs).reshape(-1, 2), par["turn"])


def _round_corners(verts, r_min, turn_frac, n_points):
    """Скруглить углы ВИДИМЫМИ дугами: радиус = доля отрезка (turn_frac, пологость),
    но НЕ меньше физического r_min. Между дугами остаются прямые."""
    pieces = [np.atleast_2d(verts[0])]
    for k in range(1, len(verts) - 1):
        P = verts[k]
        d_in = P - verts[k - 1]; li = np.linalg.norm(d_in)
        d_out = verts[k + 1] - P; lo = np.linalg.norm(d_out)
        if li < 1e-6 or lo < 1e-6:
            continue
        d_in = d_in / li; d_out = d_out / lo
        dpsi = np.arccos(float(np.clip(d_in @ d_out, -1.0, 1.0)))
        if dpsi < 1e-3:
            continue
        half = np.tan(dpsi / 2.0)
        T = turn_frac * min(li, lo)              # пологость: доля отрезка под дугу
        R = T / half
        if R < r_min:                            # острее физического предела нельзя
            R = r_min; T = R * half
        T = min(T, 0.49 * li, 0.49 * lo)         # не выходить за отрезки
        R = T / half
        entry = P - T * d_in; exit_ = P + T * d_out
        sgn = 1.0 if (d_in[0] * d_out[1] - d_in[1] * d_out[0]) >= 0 else -1.0
        C = entry + R * sgn * np.array([-d_in[1], d_in[0]])
        a0 = np.arctan2(entry[1] - C[1], entry[0] - C[0])
        m = max(4, int(dpsi / 0.15))
        angs = np.linspace(a0, a0 + sgn * dpsi, m)
        arc = np.column_stack([C[0] + R * np.cos(angs), C[1] + R * np.sin(angs)])
        pieces += [np.atleast_2d(entry), arc, np.atleast_2d(exit_)]
    pieces.append(np.atleast_2d(verts[-1]))
    return _resample_polyline(np.vstack(pieces), n_points)


def _waypoint_path(S0, B, L_max, rng, profile, n_points, r_min, nrange, rounded,
                   s_min=0.05):
    """Путь по точкам с укладкой в запас хода (уменьшение выноса до прямой).

    При большом отклонении (сложный) и нехватке хода вынос уменьшается — получается
    «большой манёвр, затем по остатку хода кратчайшим к B». s_min ограничивает точки
    зоной ЗА стартовым эллипсом (вкладка 2).
    """
    S0 = np.asarray(S0, float); B = np.asarray(B, float)
    if float(np.linalg.norm(B - S0)) < 1e-6:
        return np.repeat(S0[None, :], n_points, axis=0)
    centers, offs, turn_frac = _waypoints(S0, B, L_max, rng, _pick_mode(profile, rng),
                                          r_min, nrange, s_min)
    for scale in (1.0, 0.85, 0.7, 0.55, 0.4, 0.25, 0.1, 0.0):
        if len(centers) == 0 or scale == 0.0:
            verts = np.vstack([S0, B]) if len(centers) == 0 else \
                np.vstack([S0, centers, B])
        else:
            verts = np.vstack([S0, centers + scale * offs, B])
        path = _round_corners(verts, r_min, turn_frac, n_points) \
            if (rounded and len(verts) > 2) else _resample_polyline(verts, n_points)
        if polyline_length(path) <= L_max:
            return path
    return _resample_polyline(np.vstack([S0, B]), n_points)


_SINE_LOBES = {"normal": (1, 1), "mixed": (1, 2), "complex": (2, 4)}


def _sine_path(S0, B, L_max, rng, profile, n_points, r_min):
    """Манёвр по СИНУСОИДАЛЬНОМУ закону: боковое отклонение = сумма гармоник
    sin(jπt) (гладкая волна без прямых участков). Амплитуда по режиму (rho),
    кривизна ограничена R_min, длина <= L_max. (Как было до перехода на точки.)"""
    S0 = np.asarray(S0, float); B = np.asarray(B, float)
    chord = B - S0; D = float(np.linalg.norm(chord))
    if D < 1e-6:
        return np.repeat(S0[None, :], n_points, axis=0)
    mode = _pick_mode(profile, rng)
    par = _WP_MODE[mode]
    b_lat = ellipse_b(S0, B, L_max)          # макс. боковой вынос (полу-малая ось)
    u = chord / D; nrm = np.array([-u[1], u[0]])
    lo, hi = _SINE_LOBES.get(mode, (1, 2))
    lobes = int(rng.integers(lo, hi + 1))
    rho = rng.uniform(*par["rho"])
    comps = []
    for j in range(1, lobes + 1):
        a = rho * b_lat / j * rng.uniform(0.5, 1.0)   # база — b (доля от полу-малой оси)
        a = a * (1.0 if rng.random() < 0.5 else -1.0)
        comps.append((j, a))
    # ГАРАНТИЯ радиуса разворота: суммарная кривизна волны <= 1/R_min
    # (кривизна гармоники a·sin(jπx/D) ~ |a|·(jπ/D)^2; крен/скорость -> R_min).
    curv = sum(abs(a) * (j * np.pi / D) ** 2 for j, a in comps)
    kmax = 1.0 / max(r_min, 1e-6)
    if curv > kmax:
        f = kmax / curv
        comps = [(j, a * f) for j, a in comps]
    t = np.linspace(0.0, 1.0, n_points)
    lat = np.zeros_like(t)
    for j, a in comps:
        lat = lat + a * np.sin(j * np.pi * t)
    base = S0[None, :] + t[:, None] * chord[None, :]
    s = 1.0
    traj = base + (s * lat)[:, None] * nrm[None, :]
    while polyline_length(traj) > L_max and s > 0.05:
        s *= 0.85
        traj = base + (s * lat)[:, None] * nrm[None, :]
    return traj


def maneuver_path(S0, B, L_max, rng, profile="mixed", n_points=140, r_min=0.5,
                  nrange=(2, 5), law="points", s_min=0.05):
    """Манёвр БПЛА. law="points" — путь по ТОЧКАМ со скруглением углов (дуги
    радиуса >= R_min); law="sine" — по СИНУСОИДАЛЬНОМУ закону. s_min — мин. доля
    пути S0->B с точками (точки не спавнятся внутри зоны старта; вкладка 1 = 0.05)."""
    if law == "sine":
        return _sine_path(S0, B, L_max, rng, profile, n_points, r_min)
    return _waypoint_path(S0, B, L_max, rng, profile, n_points, r_min, nrange, True, s_min)


def polyline_path(S0, B, L_max, rng, profile="mixed", n_points=140, r_min=0.5,
                  nrange=(2, 5), law="points", s_min=0.05):
    """Ломаная БПЛА: путь по точкам БЕЗ скругления (угловой манёвр; law не влияет).
    s_min исключает точки внутри зоны старта (вкладка 2)."""
    return _waypoint_path(S0, B, L_max, rng, profile, n_points, r_min, nrange, False, s_min)


def sample_maneuver_trajectory(A, B, L_max, rng, sigma_frac=0.45, n_points=140,
                               theta_max=None, profile="normal", r_min=0.5,
                               nrange=(2, 5), law="points"):
    """Случайный манёвр A->B (по точкам или по синусу). Возвращает (traj, feature)."""
    traj = maneuver_path(A, B, L_max, rng, profile, n_points, r_min, nrange, law)
    return traj, signed_max_lateral(traj, A, B)


def sample_polyline_trajectory(A, B, L_max, rng, sigma_frac=0.45, n_points=140,
                               theta_max=None, profile="normal", r_min=0.5,
                               nrange=(2, 5), law="points"):
    """Случайная ломаная A->B. Возвращает (traj, feature)."""
    traj = polyline_path(A, B, L_max, rng, profile, n_points, r_min, nrange, law)
    return traj, signed_max_lateral(traj, A, B)


def frequent_maneuvers(A, B, L_max, profile, sigma_frac=0.45, n=10, n_points=140,
                       kind="maneuver", r_min=0.5, nrange=(2, 5), law="points"):
    """n представительных манёвров/ломаных, РАВНОМЕРНО по боковому отклонению (обе
    стороны), чтобы выборка немногих маршрутов не оказалась односторонней."""
    fn = polyline_path if kind == "polyline" else maneuver_path
    rng = np.random.default_rng(4321)
    cand = []
    for _ in range(max(n * 6, 60)):
        tr = fn(A, B, L_max, rng, profile, n_points, r_min, nrange, law)
        cand.append((signed_max_lateral(tr, A, B), tr))
    cand.sort(key=lambda t: t[0])
    idx = np.linspace(0, len(cand) - 1, n).round().astype(int)
    return [dict(traj=cand[int(i)][1], weight=0.6, is_extreme=False, label=kind)
            for i in idx]


def maneuver_fan(A, B, L_max, profile="mixed", n_points=140, k=14, kind="maneuver",
                 r_min=0.5, nrange=(2, 5), law="points"):
    """Веер манёвров/ломаных (геометрия пространства манёвров)."""
    fn = polyline_path if kind == "polyline" else maneuver_path
    rng = np.random.default_rng(321)
    return [dict(traj=fn(A, B, L_max, rng, profile, n_points, r_min, nrange, law),
                 weight=0.5, is_extreme=False, label=kind)
            for _ in range(k)]


# ======================================================================
# Предиктивный слой «10 наиболее вероятных маршрутов»:
#   НЕ случайная выборка, а обобщение ВСЕЙ накопленной выборки —
#   кластеризация маршрутов (k-means по форме) и показ медоида каждого
#   кластера (реальный маршрут-представитель) с весом = доля кластера
#   (= вероятность такого пролёта). Чем крупнее кластер, тем вероятнее путь.
# ======================================================================
def _kmeans(X, k, iters=30, seed=0):
    """Простой k-means (numpy, без внешних зависимостей). Возвращает (labels, C)."""
    rng = np.random.default_rng(seed)
    n = len(X)
    C = X[rng.choice(n, size=k, replace=False)].copy()
    labels = np.zeros(n, int)
    for _ in range(iters):
        d = np.linalg.norm(X[:, None, :] - C[None, :, :], axis=2)   # (n, k)
        new = d.argmin(axis=1)
        if np.array_equal(new, labels) and _ > 0:
            break
        labels = new
        for j in range(k):
            m = labels == j
            if np.any(m):
                C[j] = X[m].mean(axis=0)
    return labels, C


def representative_trajectories(trajectories, n=10, m=80, seed=0):
    """10 наиболее вероятных маршрутов как ОБОБЩЕНИЕ накопленной выборки.

    Каждый маршрут пересэмплируется к m точкам и кластеризуется (k-means) по форме;
    для каждого кластера берётся медоид (реальный маршрут, ближайший к центру
    кластера), вес = доля выборки в кластере (вероятность). Веса нормируются к [0,1].
    """
    T = len(trajectories)
    if T == 0:
        return []
    if T <= n:
        return [dict(traj=t, weight=1.0, is_extreme=False) for t in trajectories]
    X = np.array([_resample_polyline(np.asarray(t, float), m) for t in trajectories])
    Xf = X.reshape(T, -1)
    labels, C = _kmeans(Xf, n, seed=seed)
    out = []
    for j in range(len(C)):
        ids = np.where(labels == j)[0]
        if len(ids) == 0:
            continue
        dd = np.linalg.norm(Xf[ids] - C[j], axis=1)
        medoid = int(ids[int(dd.argmin())])
        out.append(dict(traj=np.asarray(trajectories[medoid], float),
                        weight=len(ids) / T, is_extreme=False))
    wmax = max((o["weight"] for o in out), default=1.0) or 1.0
    for o in out:
        o["weight"] = o["weight"] / wmax               # нормировка к [0,1] для отрисовки
    out.sort(key=lambda o: o["weight"], reverse=True)
    return out


SAMPLERS = {
    "arc": sample_arc_trajectory,
    "serpentine": sample_serpentine_trajectory,
    "maneuver": sample_maneuver_trajectory,
    "polyline": sample_polyline_trajectory,
}
