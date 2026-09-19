# -*- coding: utf-8 -*-
"""
ЗАМЕР РАССТАНОВКИ ДАТЧИКОВ (вкладка 3): что делают кратность засечки `k` и галка
«распределять между типами».

    python tools/placement_check.py                 # оба опыта, 150 маршрутов
    python tools/placement_check.py --routes 300    # выборка крупнее
    python tools/placement_check.py --exp k         # только опыт по кратности
    python tools/placement_check.py --save out.json # сохранить числа

ЗАЧЕМ ОТДЕЛЬНЫЙ СКРИПТ. `reference_run` сверяет числа одной обстановки с эталоном,
`metrics_check` — согласованность отчёта с самим собой. Ни тот ни другой не отвечают на
вопрос «что изменится в расстановке, если поднять k» — для этого нужно несколько
расстановок по ОДНОЙ выборке маршрутов и сравнение между ними.

⚠️ ВЫБОРКА НАБИРАЕТСЯ ОДИН РАЗ и дальше не меняется: иначе разница между значениями `k`
смешалась бы со стохастикой маршрутов. Каждая расстановка считается по одним и тем же
маршрутам — меняется только параметр опыта.

Результаты замеров и их разбор — теория/замеры_моделирования/.
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np

SECTOR_AZ_DEG = 135.0     # азимут сектора появления от цели (как в motion_check), градусы


def build_model(routes, seed=1000, relief=True):
    """Модель с готовой выборкой маршрутов. Вход: сколько маршрутов, зерно, рельеф.
    Отдаёт: (модель, секунд на выборку)."""
    from config import Params
    from model.threat_grid import ThreatModel
    m = ThreatModel(Params())
    m.set_relief(relief)
    m.build()
    B = np.asarray(m.target_only_km(), float)
    a = np.radians(float(SECTOR_AZ_DEG))
    m.set_sector(B[0] + 200.0 * np.sin(a), B[1] + 200.0 * np.cos(a))   # клик далеко по оси
    m.p.threat_iter_routes = int(routes)
    t0 = time.time()
    # ⚠️ ПАТЧ ДЕРЖИМ ВОКРУГ `iter_batch`, а не вокруг `iter_reset`: пакетный набор САМ
    # зовёт `iter_reset` первой строкой и заводит генератор заново. Патч только на сброс
    # давал разную выборку при одном зерне — замер 16.09.2026: средняя кратность 5.59 и
    # 5.55, ближайшая пара 4.47 и 3.61 км на двух прогонах подряд.
    _orig = np.random.default_rng
    np.random.default_rng = lambda *a_, **k_: _orig(seed)   # повторяемая выборка
    try:
        m.iter_batch()
    finally:
        np.random.default_rng = _orig
    return m, time.time() - t0


def pair_stats(sensors, types=None):
    """Разнос датчиков: ближайшая пара всего и ближайшая пара РАЗНЫХ типов.
    Вход: позиции (N,2) км, типы (N,) или None. Отдаёт: (мин. всего км, мин. между типами км)."""
    S = np.asarray(sensors, float)
    if len(S) < 2:                                     # пары не из чего составить
        return float("nan"), float("nan")
    d = np.linalg.norm(S[:, None, :] - S[None, :, :], axis=2)
    np.fill_diagonal(d, np.inf)
    d_all = float(d.min())
    d_cross = float("nan")
    if types is not None and len(types) == len(S):
        t = np.asarray(types, int)
        cross = t[:, None] != t[None, :]               # пары разных типов
        if cross.any():
            d_cross = float(d[cross].min())
    return d_all, d_cross


def measure(m):
    """Показатели текущей расстановки. Вход: модель. Отдаёт: словарь чисел."""
    me = m.metrics()
    d_all, d_cross = pair_stats(m.sensors, m.sensors_type)
    out = dict(sensors=int(len(m.sensors)), big=int(len(m.sensors_big)),
               detect=float(me.get("detect_frac", float("nan"))),
               detect_k=float(me.get("detect_k_frac", float("nan"))),
               mean_hits=float(me.get("mean_hits", float("nan"))),
               covered=float(me.get("covered_frac", float("nan"))),
               min_pair_km=d_all, min_cross_km=d_cross)
    # разброс расстановки: средний радиус от центра тяжести — «кучно» или «широко»
    S = np.asarray(m.sensors, float)
    if len(S):
        out["spread_km"] = float(np.linalg.norm(S - S.mean(axis=0), axis=1).mean())
    for t, v in sorted(me.get("by_type", {}).items()):
        out["type%d_n" % t] = v["n"]
        out["type%d_detect_k" % t] = v["detect_k_frac"]
        out["type%d_idle" % t] = v["idle"]             # датчиков без единого пролёта
    return out


SHOW_K = [("sensors", "датчиков", "%d"), ("detect", "засечено ≥ 1", "%.3f"),
          ("detect_k", "засечено ≥ k", "%.3f"), ("mean_hits", "средняя кратность", "%.2f"),
          ("covered", "покрытая доля веса", "%.3f"), ("min_pair_km", "ближайшая пара, км", "%.2f"),
          ("spread_km", "разброс от центра, км", "%.1f"), ("secs", "секунд", "%.1f")]
SHOW_S = SHOW_K[:-1] + [("min_cross_km", "ближайшая пара РАЗНЫХ типов, км", "%.2f"),
                        ("threshold_km", "порог правила, км", "%.2f"),
                        ("cand_banned", "кандидатов под запретом", "%d"),
                        ("cand_banned_frac", "их доля", "%.3f"),
                        ("chosen_inside", "выбрано внутри запрета", "%d"),
                        ("type1_detect_k", "тип 1: засечено ≥ k", "%.3f"),
                        ("type2_detect_k", "тип 2: засечено ≥ k", "%.3f"),
                        ("type1_idle", "тип 1: без пролёта", "%d"),
                        ("type2_idle", "тип 2: без пролёта", "%d"),
                        ("secs", "секунд", "%.1f")]


def print_table(rows, cols, show):
    """Таблица «показатель × значение опыта». Вход: {значение: числа}, порядок значений, что печатать."""
    head = "%-34s" % "показатель" + "".join("%-14s" % c for c in cols)
    print(head)
    print("-" * len(head))
    for key, label, fmt in show:
        line = "%-34s" % label
        for c in cols:
            v = rows[c].get(key)
            line += "%-14s" % ("—" if v is None or (isinstance(v, float) and np.isnan(v))
                               else fmt % v)
        print(line)


def exp_k(m, values):
    """Опыт: кратность засечки. Вход: модель с выборкой, значения k. Отдаёт: {k: числа}."""
    out = {}
    for k in values:
        m.p.threat_k = int(k)
        m.p.threat_k2 = m.p.threat_k3 = int(k)         # режим «одна на всех»
        t0 = time.time()
        m.place_sensors()
        r = measure(m)
        r["secs"] = time.time() - t0
        out["k=%d" % k] = r
    return out


def cross_rule_stats(m, r1, r2):
    """Что правило разноса между типами делает с кандидатами. Вход: модель с готовой
    расстановкой двух типов, радиусы типов км. Отдаёт: словарь чисел.

    ⚠️ Нужно, чтобы отличить «правило не работает» от «правило не связывает»: если
    выбранные точки и так дальше порога, одинаковые числа при ВКЛ и ВЫКЛ — не поломка."""
    from config import THREAT_SPREAD_PAIR
    thr = float(THREAT_SPREAD_PAIR) * (float(r1) + float(r2))   # порог разноса, км
    S, T = np.asarray(m.sensors, float), np.asarray(m.sensors_type, int)
    first = S[T == 1] if (T == 1).any() else S[:0]              # позиции типа 1
    second = S[T == 2] if (T == 2).any() else S[:0]
    cand = np.asarray(m.candidates, float)
    out = dict(threshold_km=thr, cand_total=int(len(cand)))
    if len(first) and len(cand):
        d = np.linalg.norm(cand[:, None, :] - first[None, :, :], axis=2)
        banned = (d < thr).any(axis=1)                          # кандидаты под запретом
        out["cand_banned"] = int(banned.sum())
        out["cand_banned_frac"] = float(banned.mean())
    if len(first) and len(second):
        d2 = np.linalg.norm(second[:, None, :] - first[None, :, :], axis=2).min(axis=1)
        out["min_chosen_km"] = float(d2.min())                  # ближайшая выбранная пара типов
        out["chosen_inside"] = int((d2 < thr).sum())            # выбранных внутри запрета
    return out


def exp_spread(m, n1, r1, n2, r2):
    """Опыт: галка «распределять между типами». Вход: модель, числа и радиусы двух типов.
    Отдаёт: {состояние галки: числа}."""
    m.p.threat_N, m.p.threat_R = int(n1), float(r1)
    m.p.threat_N2, m.p.threat_R2 = int(n2), float(r2)
    m.sync_cand_step(force=True)
    out = {}
    for on in (True, False):
        m.p.threat_spread_types = on
        t0 = time.time()
        m.place_sensors()
        r = measure(m)
        r["secs"] = time.time() - t0
        r.update(cross_rule_stats(m, r1, r2))
        out["галка " + ("ВКЛ" if on else "ВЫКЛ")] = r
    return out


def exp_kmode(m, n1, r1, n2, r2, k_common, k1, k2):
    """Опыт: кратность ОДНА НА ВСЕХ против СВОЕЙ у каждого типа (`threat_k_same`).
    Вход: модель, числа и радиусы двух типов, общее k, свои k типов. Отдаёт: {режим: числа}."""
    m.p.threat_N, m.p.threat_R = int(n1), float(r1)
    m.p.threat_N2, m.p.threat_R2 = int(n2), float(r2)
    m.sync_cand_step(force=True)
    out = {}
    for same in (True, False):
        m.p.threat_k_same = same
        if same:                                       # одна кратность на все типы
            m.p.threat_k = m.p.threat_k2 = m.p.threat_k3 = int(k_common)
        else:                                          # у каждого типа своя
            m.p.threat_k, m.p.threat_k2 = int(k1), int(k2)
        t0 = time.time()
        m.place_sensors()
        r = measure(m)
        r["secs"] = time.time() - t0
        out[("одна на всех k=%d" % k_common) if same
            else ("своя: тип1 k=%d, тип2 k=%d" % (k1, k2))] = r
    m.p.threat_k_same = True
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="замер расстановки датчиков (вкладка 3)")
    ap.add_argument("--exp", choices=("k", "spread", "kmode", "all"), default="all")
    ap.add_argument("--routes", type=int, default=150)
    ap.add_argument("--k", default="1,2,3,4,5", help="значения кратности через запятую")
    ap.add_argument("--types", default="12,2,8,3", help="N1,R1,N2,R2 для опыта с галкой")
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--save", default="")
    a = ap.parse_args(argv)

    m, secs = build_model(a.routes, a.seed)
    print("=" * 96)
    print("РАССТАНОВКА · участок %s · маршрутов %d (%.1f с) · запас %.1f км · точек входа %d"
          % (__import__("config").THREAT_AREA, len(m.iter_routes), secs,
             m.p.threat_L_max, len(m.entry_points_km())))
    print("=" * 96)
    res = {}
    if a.exp in ("k", "all"):
        vals = [int(x) for x in a.k.split(",") if x.strip()]
        print("\nОПЫТ 1. КРАТНОСТЬ ЗАСЕЧКИ k — сколько разных датчиков должны увидеть маршрут")
        print("(один тип: %d датчиков R = %.1f км; выборка одна и та же)"
              % (m.p.threat_N, m.p.threat_R))
        rows = exp_k(m, vals)
        print_table(rows, list(rows), SHOW_K)
        res["k"] = rows
        m.p.threat_k = m.p.threat_k2 = m.p.threat_k3 = 3      # вернуть умолчание
    if a.exp in ("spread", "all"):
        n1, r1, n2, r2 = [float(x) for x in a.types.split(",")]
        print("\nОПЫТ 2. ГАЛКА «РАСПРЕДЕЛЯТЬ МЕЖДУ ТИПАМИ» — разнос действует между типами или нет")
        print("(тип 1: %d × %.1f км, тип 2: %d × %.1f км; k = %d; выборка та же)"
              % (n1, r1, n2, r2, m.p.threat_k))
        rows = exp_spread(m, n1, r1, n2, r2)
        print_table(rows, list(rows), SHOW_S)
        res["spread"] = rows
        m.p.threat_spread_types = True                       # вернуть умолчание
    if a.exp in ("kmode", "all"):
        n1, r1, n2, r2 = [float(x) for x in a.types.split(",")]
        print("\nОПЫТ 3. КРАТНОСТЬ ОДНА НА ВСЕХ ИЛИ СВОЯ У КАЖДОГО ТИПА (`threat_k_same`)")
        print("(тип 1: %d × %.1f км, тип 2: %d × %.1f км; выборка та же)" % (n1, r1, n2, r2))
        rows = exp_kmode(m, n1, r1, n2, r2, 3, 2, 5)
        print_table(rows, list(rows), SHOW_S[:-5] + [("type1_detect_k", "тип 1: засечено ≥ своего k", "%.3f"),
                                                     ("type2_detect_k", "тип 2: засечено ≥ своего k", "%.3f"),
                                                     ("secs", "секунд", "%.1f")])
        res["kmode"] = rows
    if a.save:
        with open(a.save, "w", encoding="utf-8") as f:
            json.dump(dict(area=__import__("config").THREAT_AREA, routes=len(m.iter_routes),
                           seed=a.seed, results=res), f, ensure_ascii=False, indent=1)
        print("\nсохранено:", a.save)
    return 0


if __name__ == "__main__":
    sys.exit(main())
