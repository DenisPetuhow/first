# -*- coding: utf-8 -*-
"""КАРТИНКИ ПРОГОНА — проверка ГЛАЗАМИ того, что числа показать не могут.

ЗАЧЕМ ОТДЕЛЬНЫЙ ИНСТРУМЕНТ. `motion_check.py` отвечает на вопрос «сошлось ли с эталоном»
одним числом на показатель, и этого хватает, пока известно, ЧТО мерить. Когда механизм
новый (облёт цели, пояса, сектор налёта), вопрос другой: «а что вообще получилось?» —
и тут одно число врёт по-крупному. Доли поясов могут сойтись до десятых, а облёты при
этом лечь в одну сторону от цели: средняя доля не знает про углы. Такое видно только
на картинке — и только поэтому инструмент есть.

⚠️ ЧТО ЭТО НЕ ЗАМЕНЯЕТ. Картинка не доказывает, а ПОКАЗЫВАЕТ: глаз не отличит 26.7 % от
24.9 %. Порядок такой: сначала `motion_check.py` (числа против эталона), и только если
числа сошлись, а поведение всё равно кажется странным, — сюда. Обратный порядок («на
картинке вроде нормально») уже подводил: доли поясов держались, а облёт в 11.4 сваливался
в ближний пояс, и увидели это по ЧИСЛАМ разбивки, а не по линиям.

ТРИ ВИДА КАРТИНОК (`--fig`):
    belts   — ⭐ ОБЛЁТ ЦЕЛИ: маршруты по повадкам, пояса окружностями, рядом столбцы
              «заказано → вышло» по поясам и по уровням облёта. Главный вид: сразу видно
              и куда легли линии, и держится ли заказанная доля;
    spread  — РАЗБРОС: один режим (`center/middle/edge/mix`) на весовой карте, с областью
              залёта и рубежами подхода. Отвечает на «широко ли расходятся маршруты»;
    shape   — ФОРМА: только маршруты с дефектом — развороты (точкой отмечено место) и
              возвраты по своей трассе. Отвечает на «где именно ломается движение».

ЗАПУСК:
    python tools/plot_run.py                        # belts, уровень max, 150 маршрутов
    python tools/plot_run.py --fig spread --spread edge
    python tools/plot_run.py --fig shape --seed 1000
    python tools/plot_run.py --fig belts --set THREAT_ORBIT_DIRS=24 --out проба.png

КУДА КЛАДЁТ. `теория/замеры_моделирования/картинки/` — рядом с описаниями замеров, чтобы
картинка и её разбор не разъезжались. Имя по умолчанию — из вида, уровня и зерна.

СЦЕНАРИЙ берётся из `motion_check.build_scenario` (участок `arh`, район с центром в цели,
сектор на азимут 135°, запас авто +50 км) — тот же, что у эталона движения. Иначе картинку
нельзя было бы сравнить с числами проверки: разные условия — разные линии.

⚠️ ЗЕРНО ЗАКРЕПЛЯЕТСЯ, и картинка повторяется дословно. Без этого нельзя сравнить «до» и
«после» правки: любое расхождение списывалось бы на случайность.
"""
import argparse
import os
import sys

import numpy as np

import matplotlib
matplotlib.use("Agg")                      # без окна: скрипт запускается в консоли
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _find_root(markers=("CLAUDE.md", "config.py")):
    """Корень проекта — ближайшая папка ВВЕРХ, где лежит любой маркер.
    Вход: имена файлов-маркеров. Отдаёт: путь к корню (строка).

    Не «подняться на N уровней»: это врёт молча при каждом переносе файла.
    """
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        if any(os.path.exists(os.path.join(d, m)) for m in markers):
            return d
        up = os.path.dirname(d)
        if up == d:                        # добрались до корня диска
            raise RuntimeError("не найден корень проекта: нет CLAUDE.md / config.py")
        d = up


ROOT = _find_root()
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_TOOLS = os.path.join(ROOT, "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

PIC_DIR = os.path.join(ROOT, "теория", "замеры_моделирования", "картинки")

# Цвета повадок захода — те же смыслы, что в легенде программы: серый «как обычно»,
# синий «сбоку», красный «зашёл с обратной стороны».
BEHAV_COLOR = {"direct": ("#8a8a8a", 0.35, "обычный заход"),
               "flank":  ("#1f77b4", 0.60, "сбоку"),
               "rear":   ("#d62728", 0.70, "облёт, заход с обратной стороны")}
BELT_COLOR = ("#9467bd", "#2ca02c", "#17becf", "#ff7f0e", "#8c564b")   # пояса 1…5
LEVEL_RU = {"min": "обычный", "medium": "умеренный", "max": "максимальный"}


def collect(m, level, seed, routes):
    """Набрать маршруты с их повадкой и поясом. Вход: модель, уровень облёта, зерно,
    сколько маршрутов. Отдаёт: список (линия (N,2) км, повадка, пояс или −1).

    Почему не `run_batch` из motion_check: там нужны только линии, а здесь — ещё и чем
    маршрут оказался (повадка, пояс). Эти метки модель кладёт в `_stats` при каждом
    розыгрыше, поэтому читать их надо сразу после вызова, а не потом.
    """
    from model.threat_routes import sample_one_route
    m.p.threat_iter_approach = level
    # ⚠️ ЗЕРНО ПАТЧИМ ВОКРУГ `iter_reset`: он заводит генератор выборки заново, и без
    # подмены картинка не повторилась бы (ловушка уже стоила замера — журнал п. 275).
    _orig = np.random.default_rng
    np.random.default_rng = lambda *a, **k: _orig(seed)
    try:
        m.iter_reset()
    finally:
        np.random.default_rng = _orig
    m._iter_ctx["_stats"] = {}
    rng, res, seen, tries = m._iter_rng, [], set(), 0
    while len(res) < routes and tries < routes * 8:     # предел попыток: часть не доходит
        tries += 1
        r = sample_one_route(m._iter_ctx, m.p.threat_iter_mode, m.p.threat_L_max,
                             m.p.threat_turn_interval_km, rng, spread=m.p.threat_iter_spread,
                             spend=m._spend(), approach=level)
        if r is None:                                   # не уложился в запас хода
            continue                                    # — просто следующая попытка
        sig = m._route_sig(r)
        if sig in seen:                                 # дубль уже набранного
            continue                                    # — в выборке он ничего не добавит
        seen.add(sig)
        st = m._iter_ctx["_stats"]
        res.append((r, st.get("last_behav", "direct"), st.get("last_belt", -1)))
    return res


def _map_base(ax, m):
    """Подложка карты: вес местности серым. Вход: оси, модель. Отдаёт: рамку сетки (км)."""
    g = m.grid
    ext = (g.ox, g.ox + g.nx * g.h, g.oy, g.oy + g.ny * g.h)
    W = np.clip(np.asarray(g.weight, float), 0.0, None)
    # log1p, а не сам вес: коридоры отличаются от фона в десятки раз, и на линейной шкале
    # карта выглядела бы белым листом с несколькими чёрными нитями.
    ax.imshow(np.log1p(W), origin="lower", extent=ext, cmap="Greys", alpha=0.45)
    ax.set_xlim(ext[0], ext[1])
    ax.set_ylim(ext[2], ext[3])
    ax.set_aspect("equal")                              # километр по X = километр по Y
    ax.set_xlabel("км")
    ax.set_ylabel("км")
    return ext


def _marks(ax, m, entry_label=True):
    """Точки входа и цель. Вход: оси, модель, ставить ли подпись входам. Отдаёт: (входы, цель)."""
    B = np.asarray(m.target_only_km(), float)
    E = np.asarray(m.entry_points_km(), float)
    ax.plot(E[:, 0], E[:, 1], "^", color="tab:orange", ms=10, mec="k",
            label="точки входа" if entry_label else None)
    ax.plot([B[0]], [B[1]], "*", color="red", ms=18, mec="k", label="цель")
    return E, B


def fig_belts(m, level, seed, routes, out):
    """⭐ Облёт цели: маршруты по повадкам, пояса, столбцы «заказано → вышло».
    Вход: модель, уровень облёта, зерно, число маршрутов, путь .png. Отдаёт: None."""
    import config as cfg
    from model.threat_routes import FINAL_BYPASS_KM
    levels = ("min", "medium", "max")
    # Столбцы по уровням честны только тогда, когда КАЖДЫЙ уровень набран отдельно: доли
    # заданы внутри уровня, и смешанная выборка показала бы среднее по трём заказам.
    data = {lv: collect(m, lv, seed + i, routes) for i, lv in enumerate(levels)}
    orb = m._iter_ctx["orbit"]
    D, bounds = orb["D"], orb["bounds"]
    L = float(m.p.threat_L_max)

    fig = plt.figure(figsize=(17, 9.5), dpi=100)
    gs = fig.add_gridspec(2, 2, width_ratios=[1.45, 1.0], hspace=0.35, wspace=0.12)
    ax = fig.add_subplot(gs[:, 0])
    _map_base(ax, m)

    for bh in ("direct", "flank", "rear"):              # порядок: серые снизу, красные сверху
        first = True                                    # подпись в легенду — один раз на повадку
        c, a, lab = BEHAV_COLOR[bh]
        for r, b, _s in data[level]:
            if b != bh:
                continue
            ax.plot(r[:, 0], r[:, 1], "-", color=c, lw=0.8, alpha=a,
                    label=lab if first else None)
            first = False

    r_min = FINAL_BYPASS_KM + 2.0 * m.grid.h            # ближе круга обхода точек нет
    names = ["r0 = %d %%" % round(f * 100) for f in cfg.THREAT_ORBIT_RADII_FRAC]
    names = [n.replace("r0", "r%d" % i) for i, n in enumerate(names)]
    names[-1] = "край = 100 %"
    lines = ["Границы поясов, доли D:"]
    for i, rb in enumerate(bounds):
        ax.add_patch(plt.Circle((m.target_only_km()[0], m.target_only_km()[1]), rb,
                                fill=False, ls="--", lw=1.2, color="k", alpha=0.7))
        extra = (" (точки не ближе %.0f км)" % r_min if i == 0 else
                 " — точка входа" if i == len(bounds) - 1 else "")
        lines.append("%s · %.1f км%s" % (names[i], rb, extra))
    ax.text(0.01, 0.99, "\n".join(lines), transform=ax.transAxes, fontsize=8.5,
            va="top", ha="left",
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="0.5", alpha=0.92))

    B = np.asarray(m.target_only_km(), float)
    for s in range(5):                                  # подпись пояса — в его середине
        rr = 0.5 * (max(bounds[s], 6.0) + bounds[s + 1])
        ang = np.radians(-20)                           # чуть ниже оси: там реже линии
        ax.text(B[0] + rr * np.cos(ang), B[1] + rr * np.sin(ang), "пояс %d" % (s + 1),
                fontsize=9, color=BELT_COLOR[s], weight="bold", ha="center", va="center",
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec=BELT_COLOR[s], alpha=0.85))
    _marks(ax, m)
    # Легенда ПОД картой: внутри она закрывала бы юго-восток, откуда как раз идут маршруты.
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.07), ncol=3, fontsize=9, framealpha=0.9)
    ax.set_title("%s облёт · %d маршрутов · запас %.0f км · D = %.1f км\n"
                 "пояса облёта — доли среднего расстояния D от точек входа до цели"
                 % (LEVEL_RU[level].capitalize(), len(data[level]), L, D), fontsize=11)

    # --- доли поясов: заказано → вышло (облёты всех трёх уровней вместе) --------------
    ax2 = fig.add_subplot(gs[0, 1])
    share = np.asarray(cfg.THREAT_ORBIT_SECTOR_SHARE, float) * 100
    x = np.arange(5)
    cnt = np.zeros(5)
    for lv in levels:
        for _r, _b, s in data[lv]:
            if s is not None and s >= 0:                # −1 это «обычный заход», не облёт
                cnt[s] += 1
    got = cnt / max(cnt.sum(), 1) * 100
    ax2.bar(x - 0.2, share, 0.4, color="#cccccc", edgecolor="k", label="заказано")
    ax2.bar(x + 0.2, got, 0.4, color=BELT_COLOR, edgecolor="k", label="вышло (дошедшие облёты)")
    for i in range(5):                                  # число над столбцом: глаз не мерит высоту
        ax2.text(i - 0.2, share[i] + 0.8, "%.0f" % share[i], ha="center", fontsize=9)
        ax2.text(i + 0.2, got[i] + 0.8, "%.1f" % got[i], ha="center", fontsize=9, weight="bold")
    ax2.set_xticks(x)
    ax2.set_xticklabels(["пояс %d\n%d–%d %% D"
                         % (i + 1, round(cfg.THREAT_ORBIT_RADII_FRAC[i] * 100),
                            round(cfg.THREAT_ORBIT_RADII_FRAC[i + 1] * 100)) for i in range(5)],
                        fontsize=9)
    ax2.set_ylabel("% облётов")
    ax2.set_ylim(0, max(36.0, got.max() * 1.15))
    ax2.set_title("Доли поясов: заказано → вышло (облётов %d)" % cnt.sum(), fontsize=11)
    ax2.legend(fontsize=9, loc="upper right")

    # --- доли повадок по уровням: заказано → вышло -----------------------------------
    ax3 = fig.add_subplot(gs[1, 1])
    BH = ("direct", "flank", "rear")
    BHC = tuple(BEHAV_COLOR[b][0] for b in BH)
    w = 0.13
    for j, lv in enumerate(levels):
        want = np.asarray(cfg.THREAT_APPROACH_MIX[lv], float) * 100
        n = len(data[lv])
        have = np.asarray([sum(1 for _r, b, _s in data[lv] if b == bh) for bh in BH],
                          float) / max(n, 1) * 100
        for k in range(3):
            xo = j + (k - 1) * 2.1 * w
            # Штриховка = заказ, заливка = что вышло: пара стоит рядом, и расхождение
            # видно без чтения чисел.
            ax3.bar(xo - w / 2, want[k], w, color="white", edgecolor=BHC[k], hatch="//")
            ax3.bar(xo + w / 2, have[k], w, color=BHC[k], edgecolor="k")
            ax3.text(xo + w / 2, have[k] + 1, "%.0f" % have[k], ha="center", fontsize=8, weight="bold")
            ax3.text(xo - w / 2, want[k] + 1, "%.0f" % want[k], ha="center", fontsize=8, color="#555")
    ax3.set_xticks(range(3))
    ax3.set_xticklabels([LEVEL_RU[lv] for lv in levels])
    ax3.set_ylabel("% маршрутов")
    ax3.set_ylim(0, 100)
    ax3.legend(handles=[Patch(fc="white", ec="k", hatch="//", label="заказано"),
                        Patch(fc=BHC[0], label="вышло: обычный"),
                        Patch(fc=BHC[1], label="вышло: сбоку"),
                        Patch(fc=BHC[2], label="вышло: облёт")],
               fontsize=8, loc="upper right", ncol=2)
    ax3.set_title("Уровни облёта (обычный / сбоку / облёт): заказано → вышло", fontsize=11)

    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print("  облётов по поясам %s, доли %s" % (cnt.astype(int), np.round(got, 1)))


def fig_spread(m, level, seed, routes, out):
    """Разброс: один режим на весовой карте, с областью залёта и рубежами подхода.
    Вход: модель, уровень облёта, зерно, число маршрутов, путь .png. Отдаёт: None."""
    from motion_check import APPROACH_R_KM
    sp = m.p.threat_iter_spread
    data = collect(m, level, seed, routes)
    fig, ax = plt.subplots(figsize=(13, 8.5), dpi=100)
    ext = _map_base(ax, m)
    if m.route_area is not None:                        # область залёта посчитана
        ax.contour(np.asarray(m.route_area, float), levels=[0.5], origin="lower",
                   extent=ext, colors="tab:green", linewidths=0.8)
    for r, _b, _s in data:
        ax.plot(r[:, 0], r[:, 1], "-", color="tab:blue", lw=0.6, alpha=0.45)
    _E, B = _marks(ax, m, entry_label=False)
    for R in APPROACH_R_KM:                             # рубежи, на которых мерят угол захода
        ax.add_patch(plt.Circle(B, R, fill=False, ls="--", color="tab:red", lw=0.7))
    ax.set_title("spread = %s · запас %.1f км · маршрутов %d"
                 % (sp, float(m.p.threat_L_max), len(data)), fontsize=12)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print("  маршрутов на картинке %d" % len(data))


def fig_shape(m, level, seed, routes, out):
    """Форма: отдельно развороты (место — точкой) и возвраты по своей трассе.
    Вход: модель, уровень облёта, зерно, число маршрутов, путь .png. Отдаёт: None."""
    import motion_check as mc
    data = collect(m, level, seed, routes)
    lines = [r for r, _b, _s in data]
    B = np.asarray(m.target_only_km(), float)
    E = np.asarray(m.entry_points_km(), float).mean(axis=0)   # центр сектора появления
    fig, axs = plt.subplots(1, 2, figsize=(18, 8), dpi=100)
    titles = {"uturn": "развороты (смена курса > 150° на 5 км)",
              "return": "возвраты по своей трассе"}
    for ax, want in zip(axs, ("uturn", "return")):
        _map_base(ax, m)
        n = 0
        for r in lines:
            met = mc.route_metrics(r, B, E, m.grid)
            if not met[want]:                           # у этого маршрута дефекта нет
                continue                                # — на картинку не выносим
            n += 1
            ax.plot(r[:, 0], r[:, 1], "-", lw=0.8, alpha=0.7)
            if want == "uturn":                         # место разворота — красной точкой
                Q, _s = mc._resample(r, mc.RESAMPLE_KM)
                i0 = int(round(met["uturn_frac"] * (len(Q) - 1)))
                ax.plot(Q[i0, 0], Q[i0, 1], "o", color="red", ms=5)
        ax.plot([B[0]], [B[1]], "*", color="red", ms=15, mec="k")
        ax.set_title("%s: %d из %d" % (titles[want], n, len(lines)), fontsize=11)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


FIGS = {"belts": fig_belts, "spread": fig_spread, "shape": fig_shape}


def main(argv=None):
    """Разбор аргументов, сценарий, картинка. Вход: argv или None. Отдаёт: код возврата."""
    ap = argparse.ArgumentParser(description="Картинки прогона: облёт, разброс, форма маршрутов")
    ap.add_argument("--fig", choices=sorted(FIGS), default="belts", help="какой вид картинки")
    ap.add_argument("--level", choices=("min", "medium", "max", "mix"), default="max",
                    help="уровень облёта цели")
    ap.add_argument("--spread", choices=("center", "middle", "edge", "mix"), default="mix",
                    help="режим разброса")
    ap.add_argument("--seed", type=int, default=2024, help="зерно случайности (картинка повторяема)")
    ap.add_argument("--routes", type=int, default=150, help="сколько маршрутов набрать")
    ap.add_argument("--set", default="", help="параметры через запятую, как в motion_check")
    ap.add_argument("--out", default="", help="имя файла; по умолчанию — из вида, уровня и зерна")
    a = ap.parse_args(argv)

    import motion_check as mc
    if a.set:                                           # опыт с изменённым параметром
        mc.apply_sets(a.set)                            # — правим config до построения
    m, auto, L = mc.build_scenario()
    m.p.threat_iter_spread = a.spread
    os.makedirs(PIC_DIR, exist_ok=True)
    out = a.out or "%s_%s_seed%d.png" % (a.fig, a.level, a.seed)
    if not os.path.isabs(out):                          # имя без папки — кладём к замерам
        out = os.path.join(PIC_DIR, out)
    print("сценарий: участок arh, центр района в цели, сектор 135°, авто-запас %.1f → запас %.1f км"
          % (auto, L))
    print("вид %s · уровень облёта %s · разброс %s · зерно %d" % (a.fig, a.level, a.spread, a.seed))
    FIGS[a.fig](m, a.level if a.level != "mix" else "max", a.seed, a.routes, out)
    print("сохранено: %s" % os.path.relpath(out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
