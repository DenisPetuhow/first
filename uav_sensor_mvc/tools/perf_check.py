# -*- coding: utf-8 -*-
"""ПРОИЗВОДИТЕЛЬНОСТЬ И ПАМЯТЬ: куда уходит время при построении маршрутов.

Отвечает на вопросы: сколько стоит подготовка контекста, сколько сам розыгрыш, сколько
сглаживание; сколько попыток отбраковывается; сколько памяти держит выборка.

ЗАПУСК:
    python tools/perf_check.py                 # 150 маршрутов, разбивка по этапам
    python tools/perf_check.py --routes 400    # другой заказ
"""
import argparse
import gc
import os
import sys
import time
import tracemalloc

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LINE = "=" * 70


def measure(routes, seed, centered=False):
    # Прогон с разбивкой по этапам. Вход: маршрутов, зерно, центрировать ли район.
    # Отдаёт: dict чисел. ⚠️ По умолчанию район БЕРЁТСЯ ИЗ `область.txt` участка
    # (требование заказчика 20.09.2026 «замеры делай на районе из arh»), а не
    # центрируется на цели, как в эталоне движения.
    from motion_check import build_scenario
    from model.threat_routes import sample_one_route
    import model.threat_routes as tr

    t0 = time.perf_counter()
    m, _auto, lmax = build_scenario(centered=centered)   # сетка, слои, рельеф
    t_build = time.perf_counter() - t0

    tracemalloc.start()
    t0 = time.perf_counter()
    _orig = np.random.default_rng
    np.random.default_rng = lambda *a, **k: _orig(seed)
    try:
        m.iter_reset()                             # контекст: поля Дейкстры, VIA, пояса
    finally:
        np.random.default_rng = _orig
    t_ctx = time.perf_counter() - t0
    ctx_mem = tracemalloc.get_traced_memory()[0] / 1e6      # после контекста, МБ

    # ЗАМЕР САМОГО РОЗЫГРЫША: считаем попытки, отказы и время внутри сглаживания.
    smooth_time = [0.0]                            # копим время сглаживания, с
    orig_smooth = tr._smooth_safe

    def timed_smooth(*a, **k):                     # обёртка: та же работа плюс секундомер
        t = time.perf_counter()
        try:
            return orig_smooth(*a, **k)
        finally:
            smooth_time[0] += time.perf_counter() - t

    tr._smooth_safe = timed_smooth
    rng, res, seen, tries = m._iter_rng, [], set(), 0
    t0 = time.perf_counter()
    try:
        while len(res) < routes and tries < routes * 8:
            tries += 1
            r = sample_one_route(m._iter_ctx, m.p.threat_iter_mode, lmax,
                                 m.p.threat_turn_interval_km, rng,
                                 spread=m.p.threat_iter_spread, spend=m._spend(),
                                 approach=m.p.threat_iter_approach)
            if r is None:                          # не уложился в запас хода
                continue                           # — попытка впустую
            sig = m._route_sig(r)
            if sig in seen:                        # дубль уже набранного
                continue
            seen.add(sig)
            res.append(r)
    finally:
        tr._smooth_safe = orig_smooth
    t_walk = time.perf_counter() - t0
    peak_mem = tracemalloc.get_traced_memory()[1] / 1e6     # пик, МБ
    tracemalloc.stop()

    pts = [len(r) for r in res]                    # точек в каждом маршруте
    poly_mb = sum(r.nbytes for r in res) / 1e6     # сколько весят сами линии
    return dict(routes=len(res), tries=tries, lmax=lmax,
                t_build=t_build, t_ctx=t_ctx, t_walk=t_walk, t_smooth=smooth_time[0],
                ctx_mem=ctx_mem, peak_mem=peak_mem, poly_mb=poly_mb,
                pts_mean=float(np.mean(pts)) if pts else 0.0,
                pts_max=int(np.max(pts)) if pts else 0,
                km_mean=float(np.mean([_len(r) for r in res])) if res else 0.0)


def _len(poly):
    # Длина ломаной, км. Вход: (N,2) км. Отдаёт: float.
    d = np.diff(np.asarray(poly, float), axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def main(argv=None):
    # Разбор аргументов, прогон, отчёт. Вход: argv. Отдаёт: 0.
    ap = argparse.ArgumentParser(description="Производительность построения маршрутов")
    ap.add_argument("--routes", type=int, default=150, help="сколько маршрутов набрать")
    ap.add_argument("--seed", type=int, default=1000, help="зерно случайности")
    ap.add_argument("--centered", action="store_true",
                    help="район с центром в цели (как эталон движения); по умолчанию — из область.txt")
    a = ap.parse_args(argv)

    print(LINE)
    print("ПРОИЗВОДИТЕЛЬНОСТЬ: заказ %d маршрутов, зерно %d, район %s"
          % (a.routes, a.seed, "с центром в цели" if a.centered else "из область.txt участка"))
    print(LINE)
    gc.collect()
    r = measure(a.routes, a.seed, centered=a.centered)

    total = r["t_ctx"] + r["t_walk"]               # всё, что тратится на выборку
    print("\nВРЕМЯ, с")
    print("   построение сетки и слоёв     %7.2f   (разово, до выборки)" % r["t_build"])
    print("   контекст выборки             %7.2f   %5.1f %% выборки  (поля Дейкстры, VIA, пояса)"
          % (r["t_ctx"], 100.0 * r["t_ctx"] / max(total, 1e-9)))
    print("   розыгрыш %3d маршрутов       %7.2f   %5.1f %%"
          % (r["routes"], r["t_walk"], 100.0 * r["t_walk"] / max(total, 1e-9)))
    print("      из них сглаживание        %7.2f   %5.1f %% розыгрыша"
          % (r["t_smooth"], 100.0 * r["t_smooth"] / max(r["t_walk"], 1e-9)))
    print("   ИТОГО выборка                %7.2f" % total)
    print("   на один маршрут              %7.3f с" % (r["t_walk"] / max(r["routes"], 1)))

    print("\nПОПЫТКИ")
    print("   попыток %d на %d маршрутов — впустую %.0f %%"
          % (r["tries"], r["routes"], 100.0 * (r["tries"] - r["routes"]) / max(r["tries"], 1)))
    print("   ⚠ каждая неудачная попытка — это пройденный путь, брошенный у самой цели")

    print("\nПАМЯТЬ, МБ")
    print("   контекст выборки             %7.1f   (поля расстояний на каждую VIA)" % r["ctx_mem"])
    print("   пик за прогон                %7.1f" % r["peak_mem"])
    print("   сами линии маршрутов         %7.2f   (%d точек в среднем, максимум %d)"
          % (r["poly_mb"], round(r["pts_mean"]), r["pts_max"]))

    print("\nМАРШРУТЫ")
    print("   средняя длина                %7.1f км при запасе %.0f км" % (r["km_mean"], r["lmax"]))
    print("   точек на маршрут             %7.0f  → %.2f км между точками"
          % (r["pts_mean"], r["km_mean"] / max(r["pts_mean"] - 1, 1)))
    print(LINE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
