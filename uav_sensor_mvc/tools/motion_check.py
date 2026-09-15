# -*- coding: utf-8 -*-
"""
ПРОВЕРКА МОДЕЛИ ДВИЖЕНИЯ БПЛА (вкладка 3) — план 11, эталон движения.

Контрольный прогон `reference_run.py` сверяет ЧИСЛА расчёта: коридоры, датчики, среднюю
длину. Как именно летит маршрут, он не видит: петля, разворот «вперёд-назад» и заход на
цель с другой стороны оставляют среднюю длину той же. Этот скрипт мерит ФОРМУ маршрутов.

СЦЕНАРИЙ ЭТАЛОНА (задание заказчика 15.09.2026): участок `arh`, цель — Мирный (цель
участка по умолчанию), сектор появления — с юго-востока (азимут 135°), запас хода =
авто-запас + 50 км, рельеф включён; режимы разброса center / middle / edge / mix, по
15 прогонов на режим, в прогоне 150 маршрутов.

    python tools/motion_check.py                        # эталон: 4 режима × 15 прогонов
    python tools/motion_check.py --runs 3 --spread edge # быстрый замер одного режима
    python tools/motion_check.py --axis weight          # работают ли режимы веса
    python tools/motion_check.py --axis spend           # работают ли профили расхода хода
    python tools/motion_check.py --axis approach        # эталон облёта: уровни min/medium/max/mix
    python tools/motion_check.py --params "threat_iter_approach=max"   # другая ось зафиксирована
    python tools/motion_check.py --save base.json       # сохранить замер для сравнения
    python tools/motion_check.py --compare base.json    # сравнить с сохранённым

Разбор показателей — теория/планы/11_ПЛАН_МОДЕЛЬ_ДВИЖЕНИЯ.md §2.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np

RESAMPLE_KM = 0.5        # шаг, с которым линия маршрута переразбивается перед замером, км
RETURN_LAG_KM = 3.0      # «обратный ход»: точки разнесены по пути не меньше этого, км
RETURN_NEAR_KM = 0.75    # ...а в пространстве ближе этого, км
UTURN_WIN_KM = 5.0       # разворот: курс сменился больше чем на UTURN_DEG на таком участке, км
UTURN_DEG = 150.0        # порог разворота, градусы
CIRCLE_WIN_KM = 8.0      # кружение: суммарный поворот на таком участке пути, км
CIRCLE_DEG = 330.0       # ...больше этого, градусы (почти полный круг)
APPROACH_R_KM = (5.0, 15.0)   # радиусы, на которых меряется сторона захода на цель, км
SECTOR_AZ_DEG = 135.0    # азимут сектора появления от цели (0 — север, 90 — восток), градусы
EXTRA_KM = 50.0          # добавка к авто-запасу хода, км

# ── ЭТАЛОН ДВИЖЕНИЯ ────────────────────────────────────────────────────────────────
# Переснят 16.09.2026 (план 11, 11.5): край пояса облёта — точка входа, доли поясов и повадок
# держатся среди дошедших (`_quota_order`) — они теперь тоже в норме (sector0–4, acc_*). Прежние
# правки — облёт отдельным критерием (11.4), мягкая VIA 3 км, запретный сектор 2 км, профиль
# `late`. Ось разброса — при облёте «смесь», ось облёта — при разбросе «смесь». ⚠️ НОРМА ПО
# ДВУМ НЕЗАВИСИМЫМ ВЫБОРКАМ (зёрна 1000 и 5000, 30 прогонов × 150 маршрутов на режим) и
# проверена на ТРЕТЬЕЙ (9000).
# Снятая на одной выборке норма на второй давала по одному пограничному выходу из 52
# (доля дошедших, радиус облёта): разброс внутри выборки занижает разницу между выборками.
# Норма: (среднее, разброс ОДНОГО прогона σ, пол допуска). Допуск на среднее из N прогонов
# = max(3σ/√N, пол): чем больше прогонов, тем точнее среднее и тем уже допуск.
# ⚠️ Пол долей поясов (`sector0–4`) — 0.04: при облёте «обычный заход» облётов в прогоне ~22, и
# одна цепочка сдвигает долю на 4–5 п.п. Одиночный прогон с полом 0.02 дал ложную тревогу
# (пояс 4: 0.304 при норме 0.265 ± 0.034). Перекосы 11.4 были 10–20 п.п. — пол их ловит.
# ⚠️ Показатели эталона до правок — в плане 11 §2.1: обратный ход mix 0.149, заход
# сбоку-сзади у края 0.033. Вернулись к ним — значит, откатилась одна из правок.
ETALON = {
    "center": {
        "accept": (0.59, 0.03, 0.04), "weight": (8.57, 0.304, 0.3), "coverage": (0.196, 0.0087, 0.01),
        "lateral": (13.4, 0.38, 1.0), "return": (0.032, 0.0154, 0.02), "uturn": (0.145, 0.0251, 0.03),
        "circle": (0.053, 0.0194, 0.02), "appr5_ge90": (0.307, 0.0432, 0.03), "sweep_ge90": (0.432, 0.0414, 0.03),
        "sweep_ge180": (0.138, 0.0295, 0.02), "appr15_ge135": (0.126, 0.0224, 0.02), "orbit_r": (12.6, 0.39, 1.5),
        "appr_entropy": (0.847, 0.0313, 0.03), "acc_direct": (0.592, 0.0214, 0.03), "acc_flank": (0.229, 0.0093, 0.03),
        "acc_rear": (0.179, 0.0137, 0.03), "sector0": (0.102, 0.0055, 0.04), "sector1": (0.267, 0.0063, 0.04),
        "sector2": (0.268, 0.0079, 0.04), "sector3": (0.263, 0.006, 0.04), "sector4": (0.1, 0.0051, 0.04)},
    "middle": {
        "accept": (0.59, 0.029, 0.04), "weight": (8.71, 0.279, 0.3), "coverage": (0.247, 0.0077, 0.01),
        "lateral": (22.2, 0.43, 1.0), "return": (0.044, 0.017, 0.02), "uturn": (0.196, 0.0353, 0.03),
        "circle": (0.076, 0.02, 0.02), "appr5_ge90": (0.372, 0.0317, 0.03), "sweep_ge90": (0.547, 0.0322, 0.03),
        "sweep_ge180": (0.156, 0.0288, 0.02), "appr15_ge135": (0.13, 0.0143, 0.02), "orbit_r": (13.9, 0.75, 1.5),
        "appr_entropy": (0.914, 0.0223, 0.03), "acc_direct": (0.604, 0.0231, 0.03), "acc_flank": (0.225, 0.011, 0.03),
        "acc_rear": (0.171, 0.0131, 0.03), "sector0": (0.104, 0.0057, 0.04), "sector1": (0.269, 0.0066, 0.04),
        "sector2": (0.266, 0.0076, 0.04), "sector3": (0.265, 0.0067, 0.04), "sector4": (0.095, 0.0068, 0.04)},
    "edge": {
        "accept": (0.41, 0.023, 0.04), "weight": (8.32, 0.328, 0.3), "coverage": (0.277, 0.0077, 0.01),
        "lateral": (31.5, 0.5, 1.0), "return": (0.04, 0.0185, 0.02), "uturn": (0.213, 0.0352, 0.03),
        "circle": (0.068, 0.0181, 0.02), "appr5_ge90": (0.281, 0.0333, 0.03), "sweep_ge90": (0.501, 0.0359, 0.03),
        "sweep_ge180": (0.09, 0.02, 0.02), "appr15_ge135": (0.103, 0.0171, 0.02), "orbit_r": (20.7, 0.45, 1.5),
        "appr_entropy": (0.88, 0.0254, 0.03), "acc_direct": (0.604, 0.0173, 0.03), "acc_flank": (0.224, 0.0092, 0.03),
        "acc_rear": (0.171, 0.0101, 0.03), "sector0": (0.103, 0.0054, 0.04), "sector1": (0.27, 0.0069, 0.04),
        "sector2": (0.269, 0.0074, 0.04), "sector3": (0.267, 0.0064, 0.04), "sector4": (0.091, 0.0083, 0.04)},
    "mix": {
        "accept": (0.52, 0.033, 0.04), "weight": (8.66, 0.274, 0.3), "coverage": (0.262, 0.0093, 0.01),
        "lateral": (22.4, 0.78, 1.0), "return": (0.044, 0.0163, 0.02), "uturn": (0.193, 0.0336, 0.03),
        "circle": (0.075, 0.0217, 0.02), "appr5_ge90": (0.359, 0.0354, 0.03), "sweep_ge90": (0.536, 0.0396, 0.03),
        "sweep_ge180": (0.161, 0.0247, 0.02), "appr15_ge135": (0.13, 0.019, 0.02), "orbit_r": (15.4, 1.38, 1.5),
        "appr_entropy": (0.906, 0.022, 0.03), "acc_direct": (0.591, 0.0205, 0.03), "acc_flank": (0.23, 0.0095, 0.03),
        "acc_rear": (0.178, 0.0116, 0.03), "sector0": (0.103, 0.0069, 0.04), "sector1": (0.267, 0.007, 0.04),
        "sector2": (0.269, 0.0063, 0.04), "sector3": (0.265, 0.006, 0.04), "sector4": (0.096, 0.0062, 0.04)},
}

# ЭТАЛОН ОСИ ОБЛЁТА (план 11, 11.4–11.5): уровни min/medium/max/mix при разбросе mix.
ETALON_APPROACH = {
    "min": {
        "accept": (0.58, 0.033, 0.04), "weight": (8.41, 0.232, 0.3), "coverage": (0.245, 0.0069, 0.01),
        "lateral": (21.7, 0.93, 1.0), "return": (0.046, 0.0186, 0.02), "uturn": (0.168, 0.0316, 0.03),
        "circle": (0.072, 0.0151, 0.02), "appr5_ge90": (0.241, 0.0276, 0.03), "sweep_ge90": (0.424, 0.0435, 0.03),
        "sweep_ge180": (0.114, 0.023, 0.02), "appr15_ge135": (0.054, 0.0164, 0.02), "orbit_r": (13.8, 0.87, 1.5),
        "appr_entropy": (0.833, 0.0313, 0.03), "acc_direct": (0.848, 0.0023, 0.03), "acc_flank": (0.1, 0.0012, 0.03),
        "acc_rear": (0.053, 0.002, 0.03), "sector0": (0.112, 0.0213, 0.04), "sector1": (0.267, 0.0173, 0.04),
        "sector2": (0.267, 0.0131, 0.04), "sector3": (0.265, 0.0112, 0.04), "sector4": (0.089, 0.0175, 0.04)},
    "medium": {
        "accept": (0.54, 0.024, 0.04), "weight": (8.62, 0.289, 0.3), "coverage": (0.26, 0.0067, 0.01),
        "lateral": (22.5, 0.59, 1.0), "return": (0.048, 0.0216, 0.02), "uturn": (0.19, 0.0292, 0.03),
        "circle": (0.069, 0.0204, 0.02), "appr5_ge90": (0.349, 0.0329, 0.03), "sweep_ge90": (0.524, 0.0297, 0.03),
        "sweep_ge180": (0.137, 0.0206, 0.02), "appr15_ge135": (0.108, 0.0134, 0.02), "orbit_r": (15.6, 1.4, 1.5),
        "appr_entropy": (0.901, 0.0279, 0.03), "acc_direct": (0.6, 0.0017, 0.03), "acc_flank": (0.25, 0.0033, 0.03),
        "acc_rear": (0.151, 0.0033, 0.03), "sector0": (0.1, 0.003, 0.04), "sector1": (0.267, 0.0032, 0.04),
        "sector2": (0.267, 0.003, 0.04), "sector3": (0.267, 0.0032, 0.04), "sector4": (0.098, 0.005, 0.04)},
    "max": {
        "accept": (0.48, 0.033, 0.04), "weight": (8.98, 0.321, 0.3), "coverage": (0.268, 0.0072, 0.01),
        "lateral": (23.0, 0.52, 1.0), "return": (0.044, 0.014, 0.02), "uturn": (0.22, 0.0378, 0.03),
        "circle": (0.073, 0.0196, 0.02), "appr5_ge90": (0.51, 0.0257, 0.03), "sweep_ge90": (0.681, 0.0297, 0.03),
        "sweep_ge180": (0.213, 0.0307, 0.02), "appr15_ge135": (0.232, 0.0163, 0.02), "orbit_r": (16.1, 0.99, 1.5),
        "appr_entropy": (0.922, 0.0172, 0.03), "acc_direct": (0.3, 0.0017, 0.03), "acc_flank": (0.351, 0.0033, 0.03),
        "acc_rear": (0.35, 0.0033, 0.03), "sector0": (0.105, 0.0002, 0.04), "sector1": (0.266, 0.003, 0.04),
        "sector2": (0.266, 0.0006, 0.04), "sector3": (0.266, 0.0018, 0.04), "sector4": (0.096, 0.0029, 0.04)},
    "mix": {
        "accept": (0.52, 0.033, 0.04), "weight": (8.66, 0.274, 0.3), "coverage": (0.262, 0.0093, 0.01),
        "lateral": (22.4, 0.78, 1.0), "return": (0.044, 0.0163, 0.02), "uturn": (0.193, 0.0336, 0.03),
        "circle": (0.075, 0.0217, 0.02), "appr5_ge90": (0.359, 0.0354, 0.03), "sweep_ge90": (0.536, 0.0396, 0.03),
        "sweep_ge180": (0.161, 0.0247, 0.02), "appr15_ge135": (0.13, 0.019, 0.02), "orbit_r": (15.4, 1.38, 1.5),
        "appr_entropy": (0.906, 0.022, 0.03), "acc_direct": (0.591, 0.0205, 0.03), "acc_flank": (0.23, 0.0095, 0.03),
        "acc_rear": (0.178, 0.0116, 0.03), "sector0": (0.103, 0.0069, 0.04), "sector1": (0.267, 0.007, 0.04),
        "sector2": (0.269, 0.0063, 0.04), "sector3": (0.265, 0.006, 0.04), "sector4": (0.096, 0.0062, 0.04)},
}


ETALONS = {"spread": ETALON, "approach": ETALON_APPROACH}   # оси, у которых есть эталон


def check_etalon(results, etalon):
    """Сверить средние прогонов с эталоном движения. Вход: {режим: [итоги прогонов]}, эталон оси.
    Отдаёт: число расхождений (печатает строку на каждое)."""
    bad = 0
    for v, rs in results.items():
        norms = etalon.get(v)
        if not norms:                                  # для режима эталона нет
            continue
        n = len(rs)
        for key, (mean, sigma, floor) in norms.items():
            got = float(np.nanmean([r.get(key, np.nan) for r in rs]))
            tol = max(3.0 * sigma / np.sqrt(max(n, 1)), floor)   # допуск на среднее из n
            if not abs(got - mean) <= tol:             # вне допуска (или NaN)
                bad += 1
                print(" ⚠ %-7s %-12s %.3f  ← эталон %.3f ± %.3f" % (v, key, got, mean, tol))
    return bad


AXES = {                 # что перебирается: параметр Params и его значения
    "spread": ("threat_iter_spread", ("center", "middle", "edge", "mix")),
    "weight": ("threat_iter_mode", ("max", "medium", "min", "mix")),
    "spend": ("threat_spend", ("late", "early", "even")),
    "approach": ("threat_iter_approach", ("min", "medium", "max", "mix")),
}


def build_scenario(extra_km=EXTRA_KM, az_deg=SECTOR_AZ_DEG, centered=True):
    """Модель по сценарию эталона: arh, цель по умолчанию, сектор по азимуту, запас +extra.
    Вход: extra_km — добавка к авто-запасу хода, км; az_deg — азимут сектора, градусы;
    centered — район размером с район по умолчанию, но с центром в цели. Отдаёт: (модель, авто-запас км, запас км)."""
    from config import Params, THREAT_WORK_LONLAT, THREAT_TARGET
    from model.threat_grid import ThreatModel
    m = ThreatModel(Params())
    m.set_relief(True)                       # как в контрольном прогоне
    # ⚠️ РАЙОН С ЦЕНТРОМ В ЦЕЛИ (задание «центр в Мирном»). В районе по умолчанию Мирный
    # стоит в 23 км от западного края: облетать цель и заходить с обратной стороны там
    # просто некуда, и замер «заходит ли с другой стороны» мерил бы рамку, а не модель.
    if centered:                                       # центрировать район на цели
        lo, la, ho, ha = (float(v) for v in THREAT_WORK_LONLAT)
        tlon, tlat = float(THREAT_TARGET[1]), float(THREAT_TARGET[2])   # цель, градусы
        hw, hh = 0.5 * (ho - lo), 0.5 * (ha - la)      # полуразмеры района, градусы
        m.set_area((tlon - hw, tlat - hh, tlon + hw, tlat + hh))
    m.build()
    B = np.asarray(m.target_only_km(), float)          # цель, км
    a = np.radians(float(az_deg))                      # азимут сектора, радианы
    m.set_sector(B[0] + 200.0 * np.sin(a), B[1] + 200.0 * np.cos(a))   # клик далеко по оси
    m.p.threat_L_max_manual = False
    m._refresh_envelope()                              # авто-запас по пяти точкам входа
    auto = float(m.p.threat_L_max)                     # авто-запас хода, км
    m.p.threat_L_max_manual = True                     # дальше запас задан «руками»
    m.p.threat_L_max = round(auto + float(extra_km), 1)
    m._refresh_envelope()                              # область залёта под новый запас
    return m, auto, float(m.p.threat_L_max)


def _resample(poly, step):
    """Переразбить ломаную равными шагами по длине. Вход: (N,2) км, шаг км.
    Отдаёт: (точки (M,2), пройденный путь до каждой (M,) км)."""
    P = np.asarray(poly, float)
    seg = np.hypot(*np.diff(P, axis=0).T)              # длины звеньев, км
    s = np.concatenate([[0.0], np.cumsum(seg)])        # путь до вершин, км
    if s[-1] <= step:                                  # маршрут короче шага
        return P, s                                    # — переразбивать нечего
    t = np.arange(0.0, s[-1], step)                    # новые отметки пути, км
    return np.column_stack([np.interp(t, s, P[:, 0]), np.interp(t, s, P[:, 1])]), t


def _turns(Q):
    """Курс и повороты вдоль переразбитой линии. Вход: (M,2) км.
    Отдаёт: (курс звеньев (M-1,) радианы, поворот в узлах (M-2,) радианы со знаком)."""
    d = np.diff(Q, axis=0)
    hd = np.arctan2(d[:, 1], d[:, 0])                  # курс звена, радианы
    tr = np.angle(np.exp(1j * np.diff(hd)))            # поворот, приведённый в (-π, π]
    return hd, tr


def route_metrics(poly, B, entry_c, grid):
    """Форма одного маршрута. Вход: линия (N,2) км, цель B км, центр входов км, сетка.
    Отдаёт: словарь показателей (км, градусы, доли, флаги 0/1)."""
    from model.threat_routes import _mean_weight
    P = np.asarray(poly, float)
    Q, s = _resample(P, RESAMPLE_KM)
    out = dict(length=float(np.hypot(*np.diff(P, axis=0).T).sum()),
               weight=_mean_weight(P, grid))
    hd, tr = _turns(Q) if len(Q) >= 3 else (np.zeros(0), np.zeros(0))
    # ОБРАТНЫЙ ХОД: точка далеко по пути, но рядом в пространстве и с встречным курсом —
    # маршрут вернулся по своей же трассе. Встречный курс отличает возврат от пересечения.
    ret = loop = 0
    if len(Q) > 8:
        D = np.hypot(Q[:, None, 0] - Q[None, :, 0], Q[:, None, 1] - Q[None, :, 1])
        lag = s[None, :] - s[:, None]                  # путь от i до j, км
        near = (D <= RETURN_NEAR_KM) & (lag >= RETURN_LAG_KM)
        if near.any():                                 # маршрут подходил к своей трассе
            loop = 1
            ii, jj = np.nonzero(near)
            ii = np.minimum(ii, len(hd) - 1); jj = np.minimum(jj, len(hd) - 1)
            ret = int((np.cos(hd[ii] - hd[jj]) < -0.5).any())   # встречный курс — возврат
    out["loop"] = loop
    out["return"] = ret
    # РАЗВОРОТ И КРУЖЕНИЕ по окну пути: чистая смена курса и суммарный поворот
    uturn = circle = 0
    max_net = 0.0
    if len(tr):
        w_u = max(1, int(round(UTURN_WIN_KM / RESAMPLE_KM)))     # окно разворота, узлов
        w_c = max(1, int(round(CIRCLE_WIN_KM / RESAMPLE_KM)))    # окно кружения, узлов
        cs = np.concatenate([[0.0], np.cumsum(tr)])              # накопленный поворот
        ca = np.concatenate([[0.0], np.cumsum(np.abs(tr))])      # накопленный модуль
        k = min(w_u, len(tr))
        net = np.abs(cs[k:] - cs[:-k]) if len(cs) > k else np.abs(cs[-1:] - cs[:1])
        max_net = float(np.degrees(net.max()))
        uturn = int(max_net > UTURN_DEG)
        if uturn:                                      # где случился худший разворот
            i0 = int(np.argmax(net)) + k // 2          # середина окна, узел
            i0 = min(i0, len(Q) - 1)
            out["uturn_frac"] = float(s[i0] / max(s[-1], 1e-9))          # доля пути
            out["uturn_dgoal"] = float(np.hypot(*(Q[i0] - B)))           # до цели, км
        k = min(w_c, len(tr))
        tot = ca[k:] - ca[:-k] if len(ca) > k else ca[-1:] - ca[:1]
        circle = int(np.degrees(tot.max()) > CIRCLE_DEG)
    out.update(uturn=uturn, circle=circle, max_net_turn=max_net)
    # СТОРОНА ЗАХОДА: где маршрут последний раз вошёл в круг радиуса R вокруг цели —
    # угол между этой точкой и направлением на вход (0° — со стороны входа, 180° — с обратной)
    v = Q - B
    r = np.hypot(v[:, 0], v[:, 1])                     # удаление от цели, км
    ue = np.asarray(entry_c, float) - B
    ue = ue / max(float(np.hypot(*ue)), 1e-9)          # орт «цель → вход»
    for R in APPROACH_R_KM:
        out_idx = np.nonzero(r > R)[0]
        if len(out_idx) == 0:                          # весь маршрут внутри круга
            out["appr%d" % R] = float("nan")
            continue
        p = v[out_idx[-1]]                             # последняя точка вне круга
        c = float((p @ ue) / max(float(np.hypot(*p)), 1e-9))
        out["appr%d" % R] = float(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))))
    # РАДИУС ОБЛЁТА: средняя удалённость от цели там, где маршрут идёт СБОКУ или СЗАДИ (угол к
    # стороне входа ≥ 60°); у маршрута без такого прохода — NaN
    cang = (v @ ue) / np.maximum(r, 1e-9)              # косинус угла к стороне входа
    side_pts = (cang <= 0.5) & (r >= 2.0)
    out["orbit_r"] = float(r[side_pts].mean()) if side_pts.sum() >= 3 else float("nan")
    # ОБЛЁТ ВОКРУГ ЦЕЛИ: размах развёрнутого азимута «цель → точка» по всему маршруту
    far = r >= 2.0                                     # у самой цели азимут шумит
    if far.sum() >= 2:
        az = np.unwrap(np.arctan2(v[far, 1], v[far, 0]))
        out["sweep"] = float(np.degrees(az.max() - az.min()))
    else:
        out["sweep"] = 0.0
    # ОТКЛОНЕНИЕ ОТ ПРЯМОЙ и ЗАХОД ЗА ЦЕЛЬ — по оси «свой старт → цель»
    A = Q[0]
    ab = B - A
    L = max(float(np.hypot(*ab)), 1e-9)
    u = ab / L
    rel = Q - A
    out["lateral"] = float(np.abs(rel[:, 0] * u[1] - rel[:, 1] * u[0]).max())
    out["beyond"] = float(max(0.0, (rel @ u).max() - L))
    # ИЗВИЛИСТОСТЬ ПО ТРЕТЯМ ПУТИ: длина трети / хорда трети (1 — прямая)
    n = len(Q)
    for k, name in enumerate(("sin1", "sin2", "sin3")):
        a0, a1 = k * (n - 1) // 3, (k + 1) * (n - 1) // 3
        seg = Q[a0:a1 + 1]
        chord = float(np.hypot(*(seg[-1] - seg[0]))) if len(seg) > 1 else 0.0
        plen = float(np.hypot(*np.diff(seg, axis=0).T).sum()) if len(seg) > 1 else 0.0
        out[name] = plen / chord if chord > 0.5 else float("nan")
    return out


def run_batch(m, T, seed):
    """Набрать T маршрутов тем же порядком, что `iter_batch`, считая попытки.
    Вход: модель, T шт., зерно. Отдаёт: (маршруты, попыток, секунд)."""
    from model.threat_routes import sample_one_route
    _orig = np.random.default_rng
    np.random.default_rng = lambda *a, **k: _orig(seed)   # воспроизводимый прогон
    try:
        m.iter_reset()
    finally:
        np.random.default_rng = _orig
    m._iter_ctx["_stats"] = {}                         # счётчики модели — заново на прогон
    t0 = time.time()
    routes, seen, tries, since_new = [], set(), 0, 0
    cap, stale = T * 8, max(350, T)                    # те же пределы, что в iter_batch
    rng = m._iter_rng
    while len(routes) < T and tries < cap and since_new < stale:
        tries += 1
        r = sample_one_route(m._iter_ctx, m.p.threat_iter_mode, m.p.threat_L_max,
                             m.p.threat_turn_interval_km, rng,
                             spread=m.p.threat_iter_spread, spend=m._spend(),
                             approach=m._approach())
        if r is None:                                  # не уложился / тупик
            since_new += 1
            continue
        sig = m._route_sig(r)
        if sig in seen:                                # почти такой же уже есть
            since_new += 1
            continue
        seen.add(sig); routes.append(r); since_new = 0
        st = m._iter_ctx["_stats"]
        bh = st.setdefault("behav_acc", {})            # принятых маршрутов по повадке, шт.
        bh[st.get("last_behav", "direct")] = bh.get(st.get("last_behav", "direct"), 0) + 1
        b = st.get("last_belt", -1)                    # пояс облёта принятого маршрута
        if b is not None and b >= 0:                   # маршрут — облёт
            acc = st.setdefault("belt_acc", [0] * 5)
            acc[b] += 1                                # — в выборку попал облёт этого пояса
    return routes, tries, time.time() - t0


def summarize(routes, tries, secs, m):
    """Показатели одного прогона. Вход: маршруты, попыток, секунд, модель.
    Отдаёт: словарь средних и долей."""
    g = m.grid
    B = np.asarray(m.target_only_km(), float)
    entry_c = np.asarray(m.entry_points_km(), float).mean(axis=0)   # центр пяти входов
    rows = [route_metrics(r, B, entry_c, g) for r in routes]
    res = dict(routes=len(routes), tries=tries, accept=len(routes) / max(tries, 1), secs=secs)
    if not rows:                                       # ни одного маршрута
        return res
    col = lambda k: np.asarray([x.get(k, np.nan) for x in rows], float)
    for k in ("length", "weight", "lateral", "beyond", "max_net_turn", "sweep",
              "sin1", "sin2", "sin3"):
        res[k] = float(np.nanmean(col(k)))
    uf, ud = col("uturn_frac"), col("uturn_dgoal")     # место разворотов (NaN — разворота нет)
    if np.isfinite(uf).any():                          # развороты были
        res["uturn_frac"] = float(np.nanmedian(uf))
        res["uturn_near"] = float(np.nanmean(ud[np.isfinite(ud)] <= 10.0))   # доля у цели
    st = (m._iter_ctx or {}).get("_stats", {})
    res["back_relax"] = float(st.get("back_relax", 0)) / max(tries, 1)       # снятий сектора на попытку
    rd = st.get("orbit_rD", [])                        # радиусы дошедших облётов / D
    res["chain_rD"] = float(np.median(rd)) if rd else float("nan")
    # ДОЛЯ ПОЯСА — среди облётов, ПОПАВШИХ В ВЫБОРКУ (11.5): именно их видно на карте, и именно
    # эта доля обязана совпасть с заказанной. Цепочки, построенные в попытках, — отдельно.
    acc = st.get("belt_acc", [0] * 5)                  # принятых облётов по поясам, шт.
    sect = [st.get("orbit_sector_%d" % s, 0) for s in range(5)]   # построенных цепочек по поясам
    for s in range(5):
        res["sector%d" % s] = float(acc[s]) / max(sum(acc), 1)       # доля пояса среди принятых
        res["chain%d" % s] = float(sect[s]) / max(sum(sect), 1)      # доля среди построенных
    bh = st.get("behav_acc", {})                       # принятых по повадке, шт.
    for b in ("direct", "flank", "rear"):              # доля повадки среди принятых маршрутов
        res["acc_" + b] = float(bh.get(b, 0)) / max(len(routes), 1)
    for b in ("flank", "rear"):                        # облёт: доля попыток и доходимость
        tr_, ok_, no_ = (st.get("appr_try_" + b, 0), st.get("appr_ok_" + b, 0),
                         st.get("appr_nohop_" + b, 0))
        res["try_" + b] = float(tr_ + no_) / max(tries, 1)          # разыграно на попытку
        res["nohop_" + b] = float(no_) / max(tr_ + no_, 1)          # из них не по силам
        res["ok_" + b] = float(ok_) / max(tr_, 1)                   # дошли из построенных
    for k in ("loop", "return", "uturn", "circle"):
        res[k] = float(col(k).mean())                  # доля маршрутов с признаком
    for R in APPROACH_R_KM:
        a = col("appr%d" % R)
        res["appr%d_mean" % R] = float(np.nanmean(a))
        res["appr%d_ge90" % R] = float(np.nanmean(a >= 90.0))   # сбоку-сзади
        res["appr%d_ge135" % R] = float(np.nanmean(a >= 135.0))  # с обратной стороны
    orr = col("orbit_r")                               # радиус прохода сбоку/сзади, км
    D = float(np.mean(np.hypot(*(np.asarray(m.entry_points_km(), float) - B).T)))   # вход→цель, км
    res["orbit_r"] = float(np.nanmedian(orr)) if np.isfinite(orr).any() else float("nan")
    res["orbit_rD"] = res["orbit_r"] / D if D > 0 else float("nan")   # доля среднего расстояния
    res["side_pass"] = float(np.isfinite(orr).mean())  # доля маршрутов с проходом сбоку/сзади
    # РАЗНООБРАЗИЕ ЗАХОДА: нормированная энтропия угла захода (r = 5 км) по 6 корзинам 0–180°
    a5 = col("appr5")
    a5 = a5[np.isfinite(a5)]
    if len(a5):                                        # углы захода есть
        h = np.histogram(a5, bins=6, range=(0.0, 180.0))[0].astype(float)
        p = h[h > 0] / h.sum()
        res["appr_entropy"] = float(-(p * np.log(p)).sum() / np.log(6.0))
    sw = col("sweep")
    res["sweep_ge90"] = float((sw >= 90.0).mean())
    res["sweep_ge180"] = float((sw >= 180.0).mean())
    res["len_frac"] = res["length"] / float(m.p.threat_L_max)
    # ПОКРЫТИЕ: доля ячеек области залёта, задетых выборкой
    area = np.asarray(m.route_area, bool) if m.route_area is not None else None
    if area is not None and area.any():                # область залёта посчитана
        hit = np.zeros(area.shape, bool)
        for r in routes:
            ix = np.clip(((r[:, 0] - g.ox) / g.h).astype(int), 0, g.nx - 1)
            iy = np.clip(((r[:, 1] - g.oy) / g.h).astype(int), 0, g.ny - 1)
            hit[iy, ix] = True
        res["coverage"] = float((hit & area).sum() / area.sum())
    return res


def apply_sets(sets):
    """Подставить параметры модели для опыта: «модуль.ИМЯ=значение» через точку с запятой.
    Вход: строка, например "threat_routes.BACK_SECTOR_KM=2;config.THREAT_ORBIT_RADII_KM=(10,)".
    Отдаёт: None. Параметры config ставятся первыми — до импорта модели."""
    import ast
    import importlib
    items = [x for x in (sets or "").split(";") if x.strip()]
    items.sort(key=lambda x: 0 if x.strip().startswith("config.") else 1)   # config — раньше модели
    for item in items:
        name, val = item.split("=", 1)
        mod, attr = name.strip().rsplit(".", 1)
        module = importlib.import_module(mod if mod.startswith(("model.", "config")) else "model." + mod)
        old = getattr(module, attr)
        if isinstance(old, bool):                      # флаг
            new = val.strip() in ("1", "True", "true")
        elif isinstance(old, (int, float, str)):       # число или строка
            new = type(old)(val)
        else:                                          # кортеж, словарь
            new = ast.literal_eval(val)
        setattr(module, attr, new)


def plot_routes(m, routes, path, title):
    """Картинка прогона: весовая карта, область залёта, маршруты, входы и цель.
    Вход: модель, маршруты (км), путь .png, заголовок. Отдаёт: None (пишет файл)."""
    import matplotlib
    matplotlib.use("Agg")                              # без окна
    import matplotlib.pyplot as plt
    g = m.grid
    ext = (g.ox, g.ox + g.nx * g.h, g.oy, g.oy + g.ny * g.h)   # рамка сетки, км
    fig, ax = plt.subplots(figsize=(12, 8), dpi=110)
    W = np.clip(np.asarray(g.weight, float), 0.0, None)
    ax.imshow(np.log1p(W), origin="lower", extent=ext, cmap="Greys", alpha=0.55)
    if m.route_area is not None:                       # область залёта посчитана
        ax.contour(np.asarray(m.route_area, float), levels=[0.5], origin="lower",
                   extent=ext, colors="tab:green", linewidths=0.8)
    for r in routes:
        ax.plot(r[:, 0], r[:, 1], "-", color="tab:blue", lw=0.6, alpha=0.45)
    B = m.target_only_km()
    E = np.asarray(m.entry_points_km(), float)
    ax.plot(E[:, 0], E[:, 1], "^", color="tab:orange", ms=8)
    ax.plot([B[0]], [B[1]], "*", color="tab:red", ms=16)
    for R in APPROACH_R_KM:
        ax.add_patch(plt.Circle(B, R, fill=False, ls="--", color="tab:red", lw=0.7))
    ax.set_aspect("equal"); ax.set_title(title); ax.set_xlabel("км"); ax.set_ylabel("км")
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


def _worker(job):
    """Прогоны одного значения одного параметра в своём процессе.
    Вход: (ось, значение, прогонов, маршрутов, добавка км, азимут, зерно-база, папка картинок).
    Отдаёт: (значение, список итогов прогонов, авто-запас, запас)."""
    axis, value, runs, T, extra, az, seed0, plot_dir, sets, params = job
    apply_sets(sets)
    m, auto, L = build_scenario(extra, az)
    m.p.threat_iter_routes = T
    for item in [x for x in (params or "").split(";") if x.strip()]:   # поля Params для опыта
        k, v = item.split("=", 1)
        setattr(m.p, k.strip(), v.strip())
    setattr(m.p, AXES[axis][0], value)
    out = []
    for i in range(runs):
        routes, tries, secs = run_batch(m, T, seed0 + i)
        out.append(summarize(routes, tries, secs, m))
        if plot_dir and i == 0:                        # картинка первого прогона
            os.makedirs(plot_dir, exist_ok=True)
            plot_routes(m, routes, os.path.join(plot_dir, "%s_%s.png" % (axis, value)),
                        "%s = %s · запас %.1f км · маршрутов %d" % (axis, value, L, len(routes)))
    return value, out, auto, L


SHOW = [("routes", "маршрутов", "%.0f"), ("accept", "доходимость", "%.2f"),
        ("length", "длина, км", "%.1f"), ("len_frac", "длина/запас", "%.2f"),
        ("weight", "вес под линией", "%.2f"), ("coverage", "покрытие", "%.3f"),
        ("lateral", "отклонение, км", "%.1f"), ("beyond", "за цель, км", "%.2f"),
        ("loop", "подход к своей трассе", "%.3f"), ("return", "обратный ход", "%.3f"),
        ("uturn", "разворот >150° за 5 км", "%.3f"), ("circle", "кружение >330°/8 км", "%.3f"),
        ("max_net_turn", "макс. поворот за 5 км, °", "%.0f"),
        ("uturn_frac", "разворот: доля пути (мед.)", "%.2f"),
        ("uturn_near", "разворот у цели ≤10 км", "%.2f"),
        ("back_relax", "снятий сектора / попытку", "%.2f"),
        ("acc_direct", "обычный заход (принято)", "%.3f"), ("acc_flank", "сбоку (принято)", "%.3f"),
        ("acc_rear", "облёт (принято)", "%.3f"),
        ("try_flank", "сбоку: разыграно / попытку", "%.3f"), ("nohop_flank", "сбоку: не по силам", "%.2f"),
        ("ok_flank", "сбоку: дошли", "%.2f"),
        ("try_rear", "облёт: разыграно / попытку", "%.3f"), ("nohop_rear", "облёт: не по силам", "%.2f"),
        ("ok_rear", "облёт: дошли", "%.2f"),
        ("appr5_mean", "угол захода r=5, °", "%.0f"), ("appr5_ge90", "заход сбоку-сзади r=5", "%.3f"),
        ("appr15_mean", "угол захода r=15, °", "%.0f"), ("appr15_ge90", "заход сбоку-сзади r=15", "%.3f"),
        ("appr15_ge135", "заход с обратной r=15", "%.3f"),
        ("appr_entropy", "разнообразие захода (0–1)", "%.3f"),
        ("side_pass", "проход сбоку/сзади", "%.3f"), ("orbit_r", "радиус облёта, км (мед.)", "%.1f"),
        ("orbit_rD", "радиус облёта / D", "%.2f"),
        ("chain_rD", "радиус цепочки облёта / D", "%.2f"),
        ("sector0", "облёт в поясе 0 (принято)", "%.3f"), ("sector1", "облёт в поясе 1 (принято)", "%.3f"),
        ("sector2", "облёт в поясе 2 (принято)", "%.3f"), ("sector3", "облёт в поясе 3 (принято)", "%.3f"),
        ("sector4", "облёт в поясе 4 (принято)", "%.3f"),
        ("chain0", "цепочек в поясе 0", "%.2f"), ("chain4", "цепочек в поясе 4", "%.2f"),
        ("sweep", "облёт вокруг цели, °", "%.0f"), ("sweep_ge90", "облёт ≥90°", "%.3f"),
        ("sweep_ge180", "облёт ≥180°", "%.3f"),
        ("sin1", "извилистость 1/3", "%.3f"), ("sin2", "извилистость 2/3", "%.3f"),
        ("sin3", "извилистость 3/3", "%.3f"), ("secs", "секунд на прогон", "%.1f")]


def print_table(results, ref=None):
    """Таблица «показатель × значение»: среднее ± разброс по прогонам.
    Вход: {значение: [итоги прогонов]}, ref — такой же словарь для сравнения или None."""
    vals = list(results)
    head = "%-28s" % "показатель" + "".join("%-20s" % v for v in vals)
    print(head)
    print("-" * len(head))
    for key, label, fmt in SHOW:
        line = "%-28s" % label
        for v in vals:
            xs = np.asarray([r.get(key, np.nan) for r in results[v]], float)
            cell = (fmt % np.nanmean(xs)) + " ±" + (fmt % np.nanstd(xs))
            if ref and v in ref:                       # есть сохранённый замер
                rx = np.asarray([r.get(key, np.nan) for r in ref[v]], float)
                cell += " (" + (fmt % np.nanmean(rx)) + ")"
            line += "%-20s" % cell
        print(line)


def main(argv=None):
    ap = argparse.ArgumentParser(description="проверка модели движения БПЛА (план 11)")
    ap.add_argument("--axis", choices=sorted(AXES), default="spread")
    ap.add_argument("--values", default="", help="через запятую; по умолчанию все значения оси")
    ap.add_argument("--spread", default="", help="синоним --values для оси spread")
    ap.add_argument("--runs", type=int, default=15)
    ap.add_argument("--routes", type=int, default=150)
    ap.add_argument("--extra-km", type=float, default=EXTRA_KM)
    ap.add_argument("--az", type=float, default=SECTOR_AZ_DEG)
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--save", default="")
    ap.add_argument("--compare", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--plot", default="", help="папка для картинок первого прогона")
    ap.add_argument("--set", default="", help='параметры опыта: "threat_routes.BACK_SECTOR_KM=2"')
    ap.add_argument("--params", default="", help='поля Params: "threat_iter_spread=center"')
    a = ap.parse_args(argv)
    raw = a.values or a.spread
    values = [v for v in raw.split(",") if v] if raw else list(AXES[a.axis][1])
    jobs = [(a.axis, v, a.runs, a.routes, a.extra_km, a.az, a.seed, a.plot, a.set, a.params)
            for v in values]
    t0 = time.time()
    results, auto, L = {}, None, None
    with ProcessPoolExecutor(max_workers=max(1, min(a.jobs, len(jobs)))) as ex:
        for value, out, au, LL in ex.map(_worker, jobs):
            results[value] = out
            auto, L = au, LL
    print("=" * 100)
    print("МОДЕЛЬ ДВИЖЕНИЯ · ось %s · прогонов %d × маршрутов %d · сектор %g° · авто-запас %.1f км,"
          " запас %.1f км (+%g) · %s" % (a.axis, a.runs, a.routes, a.az, auto, L, a.extra_km,
                                         a.note or "без пометки"))
    print("=" * 100)
    ref = None
    if a.compare and os.path.exists(a.compare):        # сравнить с сохранённым
        with open(a.compare, encoding="utf-8") as f:
            ref = json.load(f)["results"]
        print("в скобках — среднее из", a.compare)
    print_table(results, ref)
    print("всего %.0f с" % (time.time() - t0))
    code = 0
    scenario = (a.axis in ETALONS and a.extra_km == EXTRA_KM and a.az == SECTOR_AZ_DEG
                and not a.set and not a.params and a.routes == 150)
    if scenario:                                       # сценарий эталона — сверяем
        print("=" * 100)
        bad = check_etalon(results, ETALONS[a.axis])
        print("ВСЁ СОШЛОСЬ С ЭТАЛОНОМ ДВИЖЕНИЯ." if bad == 0 else
              "РАСХОЖДЕНИЙ С ЭТАЛОНОМ ДВИЖЕНИЯ: %d — разобрать до коммита." % bad)
        code = 1 if bad else 0
    else:                                              # опыт вне сценария
        print("(не сценарий эталона — с эталоном не сверяется)")
    if a.save:                                         # сохранить для сравнения
        with open(a.save, "w", encoding="utf-8") as f:
            json.dump(dict(axis=a.axis, runs=a.runs, routes=a.routes, az=a.az, auto=auto, L=L,
                           extra=a.extra_km, note=a.note, results=results), f,
                      ensure_ascii=False, indent=1)
        print("сохранено:", a.save)
    return code


if __name__ == "__main__":
    sys.exit(main())
