# -*- coding: utf-8 -*-
"""КАРТИНКИ К WORD-ДОКУМЕНТУ «Маршруты и датчики» — как устроен контекст выборки.

Четыре рисунка (`--fig`), все — по РЕАЛЬНОМУ расчёту активного участка, не схемы «от руки»:
    via     — откуда берутся точки обхода: сетка 4x5, что отсеяно и почему;
    field   — что такое «поле расстояний»: карта до цели и карта до одной via;
    routes  — зачем via нужны: маршруты, раскрашенные по своей точке обхода;
    cost    — что считается ОДИН раз, а что на каждый маршрут, и цена ошибки.

ЗАПУСК:
    python docs/make_pictures.py            # все четыре
    python docs/make_pictures.py --fig via

Кладёт в `docs/картинки/`. Оттуда их забирает `make_routes_sensors_docx.py`.
⚠️ Числа на подписях берутся из ТОГО ЖЕ прогона, что и картинка, — не из текста
документа: расхождение между подписью и рисунком заметить почти невозможно.
"""
import argparse
import os
import sys
import time

import numpy as np

import matplotlib
matplotlib.use("Agg")                      # без окна: скрипт запускается в консоли
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))   # папка docs/
ROOT = os.path.dirname(_HERE)              # корень проекта: docs/ лежит в нём
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "src"))   # код программы — в src/ (журнал 364)

PIC_DIR = os.path.join(_HERE, "картинки")   # куда кладём PNG для документа

C_VIA = "#1f4e79"        # точки обхода — синий, как заголовки документа
C_DROP = "#b0b0b0"       # отсеянные места сетки — серый
C_TARGET = "#a61b1b"     # цель — красный
C_ENTRY = "#1e7a1e"      # точки входа — зелёный


def _model():
    """Модель по сценарию эталона движения (`motion_check.build_scenario`).
    Отдаёт: (модель, контекст выборки, время сборки контекста, с)."""
    # ⚠️ Сценарий тот же, что у эталона: иначе точка входа — демо-метка за 160 км от
    # района, и картинка растягивается на пустое поле
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from motion_check import build_scenario
    from model.threat.threat_routes import build_iter_context
    m, _auto, _lmax = build_scenario()
    entry, target = m.entries_target_km()
    t0 = time.time()                            # засекаем сборку контекста
    ctx = build_iter_context(  # контекст выборки: поля Дейкстры, самое дорогое место
        m.grid, entry, target, m._route_gap_km(), slack_km=m._route_slack_km())
    return m, ctx, time.time() - t0


def _cells_km(m, cells):
    """Ячейки (iy, ix) в километры центров. Вход: модель, список ячеек. Отдаёт: (x, y)."""
    g = m.grid                                  # весовая сетка участка
    xs = [g.ox + (c[1] + 0.5) * g.h for c in cells]   # центры ячеек по X, км
    ys = [g.oy + (c[0] + 0.5) * g.h for c in cells]   # то же по Y, км
    return np.asarray(xs), np.asarray(ys)


def _map_background(ax, m, pad_km=4.0):
    """Подложка карты: вес участка серым. Вход: оси, модель, поле у рамки, км. Отдаёт: None.

    ⚠️ Рамка показа = РАЙОН плюс поле: точки входа могут лежать у самого края, и без
    запаса их значки срезаются пополам."""
    g = m.grid                                  # весовая сетка участка
    x0, x1, y0, y1 = m.bbox_km                  # рамка района, км
    w = np.clip(g.weight, 0.0, None)            # вес без минусов — только для показа
    ax.imshow(w, origin="lower", extent=(x0, x1, y0, y1), cmap="Greys",
              alpha=0.75, aspect="equal", zorder=1,
              vmax=float(np.percentile(w[w > 0], 80)) if (w > 0).any() else 1.0)
    ax.set_xlim(x0 - pad_km, x1 + pad_km)
    ax.set_ylim(y0 - pad_km, y1 + pad_km)
    ax.set_xlabel("километры участка, X")
    ax.set_ylabel("километры участка, Y")


def fig_via(m, ctx, out):
    """Рисунок 1: откуда берутся точки обхода и почему часть отсеяна."""
    g = m.grid                                  # весовая сетка участка
    entries = m.entry_points_km()               # точки входа, км
    target = m.target_only_km()                 # цель, км
    via_x, via_y = _cells_km(m, ctx["via_cell"])   # точки обхода, км

    # ПОЛНАЯ сетка 4x5 — те самые места, из которых отбирались via: строится так же,
    # как в build_iter_context, по достижимым проходимым ячейкам
    ys, xs = np.nonzero(ctx["pas"] & np.isfinite(ctx["gt"]))
    gy = np.linspace(ys.min(), ys.max(), 4)     # 4 ряда узлов сетки, номера ячеек
    gx = np.linspace(xs.min(), xs.max(), 5)     # 5 колонок узлов
    grid_x = [g.ox + (cx + 0.5) * g.h for cy in gy for cx in gx]   # узлы по X, км
    grid_y = [g.oy + (cy + 0.5) * g.h for cy in gy for cx in gx]   # узлы по Y, км

    fig, ax = plt.subplots(figsize=(12.5, 7.6), dpi=110)
    _map_background(ax, m)
    ax.plot(grid_x, grid_y, "x", color=C_DROP, ms=11, mew=2.0, zorder=3,
            label="узлы сетки 4 × 5 — места, ГДЕ ИСКАЛИ точку обхода")
    ax.plot(via_x, via_y, "o", color=C_VIA, ms=13, zorder=5, mec="white", mew=1.6,
            label="точки обхода (VIA), оставшиеся после отбора: %d" % len(via_x))
    for i, (vx, vy) in enumerate(zip(via_x, via_y), 1):
        ax.annotate("%d" % i, (vx, vy), color="white", fontsize=8, ha="center",
                    va="center", zorder=6, weight="bold")
    ex = [p[0] for p in entries]                # входы по X, км
    ey = [p[1] for p in entries]                # входы по Y, км
    ax.plot(ex, ey, "^", color=C_ENTRY, ms=12, zorder=6, mec="white", mew=1.2,
            label="точки входа (откуда появляется БПЛА)")
    ax.plot([target[0]], [target[1]], "*", color=C_TARGET, ms=26, zorder=7,
            mec="white", mew=1.2, label="цель")
    # ось «вход → цель»: по ней отсеиваются точки позади старта и далеко за целью
    a = np.asarray(entries, float).mean(axis=0)
    ax.plot([a[0], target[0]], [a[1], target[1]], "--", color="#555555", lw=1.6,
            zorder=4, label="ось вход → цель (по ней идёт отсев)")
    ax.set_title("Рисунок 1. Точки обхода (VIA): из %d мест сетки осталось %d\n"
                 "Отсеиваются те, что позади старта, вплотную к цели или недостижимы"
                 % (len(grid_x), len(via_x)), fontsize=12)
    # легенда ПОД картинкой: внутри она накрывала верхний ряд точек обхода
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2, fontsize=9,
              framealpha=0.95)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return "VIA: %d из %d узлов сетки" % (len(via_x), len(grid_x))


def fig_field(m, ctx, out):
    """Рисунок 2: что такое «поле расстояний» — карта до цели и карта до одной via."""
    g = m.grid                                  # весовая сетка участка
    x0, x1, y0, y1 = m.bbox_km                  # рамка района, км
    target = m.target_only_km()                 # цель, км
    via_x, via_y = _cells_km(m, ctx["via_cell"])   # точки обхода, км
    k = int(np.argmax(np.hypot(via_x - target[0], via_y - target[1])))   # самая дальняя via

    fig, axes = plt.subplots(1, 2, figsize=(14.5, 6.4), dpi=110)
    for ax, fld, ttl, px, py, col in (
            (axes[0], ctx["gt_geo"], "поле ДО ЦЕЛИ", target[0], target[1], C_TARGET),
            (axes[1], ctx["via_gv_geo"][k], "поле ДО ТОЧКИ ОБХОДА № %d" % (k + 1),
             via_x[k], via_y[k], C_VIA)):
        d = np.array(fld, float)
        d[~np.isfinite(d)] = np.nan                 # недостижимое не красим вовсе
        # сама картинка поля; `im` нужен ниже — по нему строится шкала цвета
        im = ax.imshow(d, origin="lower", extent=(x0, x1, y0, y1), cmap="viridis",
                       aspect="equal")
        lv = np.nanpercentile(d, [10, 25, 40, 55, 70, 85])   # уровни изолиний, км
        ax.contour(d, levels=lv, extent=(x0, x1, y0, y1), colors="white",
                   linewidths=0.7, alpha=0.65)
        ax.plot([px], [py], "*" if col == C_TARGET else "o", color=col, ms=20,
                mec="white", mew=1.4)
        ax.set_title("%s\nцвет = сколько километров лететь до неё" % ttl, fontsize=11)
        ax.set_xlabel("километры участка, X")
        fig.colorbar(im, ax=ax, shrink=0.82, label="расстояние по коридорам, км")
    axes[0].set_ylabel("километры участка, Y")
    fig.suptitle("Рисунок 2. «Поле» — это карта расстояний: для КАЖДОЙ из %d ячеек "
                 "заранее посчитано, сколько лететь до точки" % (g.nx * g.ny), fontsize=12)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return "поле %d x %d, %.2f МБ" % (g.ny, g.nx, ctx["gt_geo"].nbytes / 1048576.0)


def fig_routes(m, ctx, out, n_routes=24, seed=1000):
    """Рисунок 3: зачем нужны via — маршруты, раскрашенные по своей точке обхода."""
    from model.threat.threat_routes import sample_one_route
    rng = np.random.default_rng(seed)           # зерно закреплено: картинка повторяема
    via_x, via_y = _cells_km(m, ctx["via_cell"])   # точки обхода, км
    L_max = float(m.p.threat_L_max)             # запас хода, км
    cmap = plt.get_cmap("tab20")                # 20 цветов — по одному на точку обхода

    fig, ax = plt.subplots(figsize=(12.5, 7.6), dpi=110)
    _map_background(ax, m)
    used = {}                                   # номер via -> сколько маршрутов через неё
    for _ in range(n_routes):
        c = dict(ctx)
        r = sample_one_route(c, "mix", L_max, m.p.threat_turn_interval_km, rng,
                             spread="mix", spend="mix", approach="mix")
        if r is None or len(r) < 2:
            continue
        # какая via ближе всего к маршруту — та, через которую он и шёл
        d = [np.min(np.hypot(r[:, 0] - vx, r[:, 1] - vy)) for vx, vy in zip(via_x, via_y)]
        k = int(np.argmin(d))
        used.setdefault(k, 0)
        used[k] += 1
        ax.plot(r[:, 0], r[:, 1], "-", color=cmap(k % 20), lw=1.5, alpha=0.85, zorder=4)
    for k in sorted(used):
        ax.plot([via_x[k]], [via_y[k]], "o", color=cmap(k % 20), ms=13, zorder=6,
                mec="white", mew=1.6)
        ax.annotate("%d" % (k + 1), (via_x[k], via_y[k]), color="white", fontsize=8,
                    ha="center", va="center", zorder=7, weight="bold")
    t = m.target_only_km()
    ax.plot([t[0]], [t[1]], "*", color=C_TARGET, ms=26, zorder=8, mec="white", mew=1.2)
    for p in m.entry_points_km():
        ax.plot([p[0]], [p[1]], "^", color=C_ENTRY, ms=12, zorder=8, mec="white", mew=1.2)
    ax.set_title("Рисунок 3. Зачем точки обхода: %d маршрутов через %d разных VIA\n"
                 "Цвет линии = точка обхода, назначенная этому маршруту. "
                 "Без них все линии легли бы по прямой" % (sum(used.values()), len(used)),
                 fontsize=12)
    ax.legend(handles=[Line2D([], [], color=C_TARGET, marker="*", ls="", ms=15, label="цель"),
                       Line2D([], [], color=C_ENTRY, marker="^", ls="", ms=10,
                              label="точки входа")],
              loc="upper left", fontsize=9, framealpha=0.93)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return "маршрутов %d через %d via" % (sum(used.values()), len(used))


def fig_cost(m, ctx, t_ctx, out, n_routes=40, seed=1000):
    """Рисунок 4: что считается один раз, что на каждый маршрут и цена наивного пути."""
    from model.threat.threat_routes import sample_one_route
    rng = np.random.default_rng(seed)           # зерно закреплено
    L_max = float(m.p.threat_L_max)             # запас хода, км
    t0 = time.time()                            # засекаем розыгрыш маршрутов
    ok = 0                                      # сколько маршрутов дошло, шт.
    for _ in range(n_routes):
        c = dict(ctx)
        if sample_one_route(c, "mix", L_max, m.p.threat_turn_interval_km, rng,
                            spread="mix", spend="mix", approach="mix") is not None:
            ok += 1
    t_one = (time.time() - t0) / max(n_routes, 1)       # секунд на один маршрут
    n_via = len(ctx["via_cell"])                # точек обхода в контексте, шт.
    mb = sum(a.nbytes for a in ctx["via_gv"] + ctx["via_gv_geo"]) / 1048576.0   # поля, МБ
    mb += (ctx["gt"].nbytes + ctx["gt_geo"].nbytes) / 1048576.0

    fig, axes = plt.subplots(1, 2, figsize=(14.5, 6.2), dpi=110)

    # ---- слева: что во что входит ----
    ax = axes[0]
    ax.axis("off")
    ax.set_title("Что считается ОДИН раз, а что — на каждый маршрут", fontsize=12)
    blocks = [                                  # (высота на оси, цвет, заголовок, текст)
        (0.86, "#1f4e79", "КОНТЕКСТ — считается ОДИН раз на цель",
         "%d полей расстояний: %d VIA × 2 + 2 до цели\n%.1f МБ · %.1f секунды"
         % (2 * n_via + 2, n_via, mb, t_ctx)),
        (0.54, "#2e7d32", "МАРШРУТ — берёт готовое",
         "на каждом шаге ЧИТАЕТ число из поля,\nпуть заново не ищет · %.3f с на маршрут"
         % t_one),
        (0.22, "#a61b1b", "ЕСЛИ ЦЕЛЬ МЕНЯЕТСЯ, А КОНТЕКСТ НЕ КЭШИРОВАТЬ",
         "пересчёт %d полей на КАЖДЫЙ маршрут:\n250 маршрутов × %.1f с ≈ %.0f минут"
         % (2 * n_via + 2, t_ctx, 250 * t_ctx / 60.0)),
    ]
    for y, col, head, body in blocks:
        ax.add_patch(plt.Rectangle((0.03, y - 0.17), 0.94, 0.26, transform=ax.transAxes,
                                   facecolor=col, alpha=0.13, edgecolor=col, lw=2.0))
        ax.text(0.06, y + 0.04, head, transform=ax.transAxes, fontsize=11.5,
                weight="bold", color=col, va="center")
        ax.text(0.06, y - 0.09, body, transform=ax.transAxes, fontsize=10,
                color="#333333", va="center")

    # ---- справа: время, логарифмическая шкала ----
    ax = axes[1]
    names = ["контекст\n(один раз)", "250 маршрутов\nпо готовому", "то же, но контекст\n"
             "на каждый маршрут"]
    vals = [t_ctx, 250 * t_one, 250 * t_ctx]    # три столбца, секунды
    cols = ["#1f4e79", "#2e7d32", "#a61b1b"]    # синий — разово, зелёный — дёшево, красный — цена ошибки
    bars = ax.bar(names, vals, color=cols, alpha=0.85)   # сами столбцы — для подписей
    ax.set_yscale("log")
    ax.set_ylabel("секунды (логарифмическая шкала)")
    ax.set_title("Цена вопроса: кэш решает всё", fontsize=12)
    for b, v in zip(bars, vals):
        ax.annotate("%.1f с" % v if v < 60 else "%.0f мин" % (v / 60.0),
                    (b.get_x() + b.get_width() / 2, v), ha="center", va="bottom",
                    fontsize=11, weight="bold")
    ax.grid(axis="y", alpha=0.3)
    fig.suptitle("Рисунок 4. Почему контекст считают заранее и кэшируют", fontsize=13)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return "контекст %.1f с / %.1f МБ, маршрут %.3f с" % (t_ctx, mb, t_one)


FIGS = {"via": fig_via, "field": fig_field,     # имя в --fig -> функция рисунка
        "routes": fig_routes, "cost": fig_cost}


def main():
    ap = argparse.ArgumentParser(description="картинки к Word-документу")   # разбор ключей
    ap.add_argument("--fig", choices=sorted(FIGS) + ["all"], default="all")
    args = ap.parse_args()
    os.makedirs(PIC_DIR, exist_ok=True)
    print("строю карту участка…")
    m, ctx, t_ctx = _model()
    print("контекст собран за %.1f с, VIA %d" % (t_ctx, len(ctx["via_cell"])))
    names = sorted(FIGS) if args.fig == "all" else [args.fig]
    for name in names:
        out = os.path.join(PIC_DIR, "%s.png" % name)
        info = (FIGS[name](m, ctx, t_ctx, out) if name == "cost"   # строка для отчёта
                else FIGS[name](m, ctx, out))
        print("  %-7s -> %s   (%s)" % (name, os.path.relpath(out, ROOT), info))


if __name__ == "__main__":
    main()
