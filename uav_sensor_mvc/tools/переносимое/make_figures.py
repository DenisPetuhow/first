# -*- coding: utf-8 -*-
"""Рисунки для учебного пособия (теория/пособие/рис/*.svg).

ЗАЧЕМ. Пособие объясняет вещи, которые словами объясняются плохо: убывающую
отдачу, насыщение критерия, эллипс достижимости, предел радиуса разворота.
Каждый рисунок здесь СЧИТАЕТСЯ по той же формуле, что записана в тексте, —
поэтому картинка не может разойтись с формулой, как разошлась бы нарисованная
руками.

ЗАПУСК:
    python tools/переносимое/make_figures.py            # пересобрать все рисунки
    python tools/переносимое/make_figures.py --list     # какие рисунки есть и куда пишутся

ФОРМАТ. SVG: векторный, читается GitHub и VS Code, масштабируется без потерь.
Шрифт DejaVu Sans — стандартный для matplotlib и с кириллицей.
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")                      # без окна: скрипт запускается в консоли
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Ellipse, Circle, Arc, FancyArrowPatch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def _find_root(markers=("CLAUDE.md", "config.py")):
    """Корень проекта — ближайшая папка ВВЕРХ, содержащая любой из маркеров.

    Не «посчитать уровни»: `dirname` N раз означает «лежу ровно на N уровней ниже
    корня» и врёт при каждом переносе папки, причём МОЛЧА — скрипт запускается и
    просто смотрит не туда. Так уже было при переезде в tools/docs.
    При переносе в другой проект менять только список маркеров.
    """
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        if any(os.path.exists(os.path.join(d, m)) for m in markers):
            return d
        up = os.path.dirname(d)
        if up == d:
            raise SystemExit("не найден корень проекта: вверх от %s нет ни одного из %s"
                             % (os.path.dirname(os.path.abspath(__file__)), list(markers)))
        d = up


ROOT = _find_root()
OUT = os.path.join(ROOT, "теория", "пособие", "рис")

# Единый стиль: светлый фон учебника, спокойные цвета, читаемая сетка.
plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 10,
    "axes.titlesize": 11,
    "axes.labelsize": 10,
    "axes.edgecolor": "#5a6472",
    "axes.linewidth": 0.9,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
    "grid.color": "#dde3ea",
    "grid.linewidth": 0.8,
})

C_MAIN = "#1f5fa8"      # основная линия
C_ACC = "#c0392b"       # акцент / предел
C_OK = "#1e8449"        # «хорошо» / достигнуто
C_GREY = "#7f8c8d"      # вспомогательное
C_FILL = "#dce9f7"      # заливка области


def save(fig, name):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)
    print("  %-26s -> %s" % (name, os.path.relpath(path, ROOT).replace("\\", "/")))


# ─────────────────────────────────────────────────────────────────────────────
# 1. УБЫВАЮЩАЯ ОТДАЧА (субмодулярность) — главный рисунок пособия
# ─────────────────────────────────────────────────────────────────────────────
def fig_diminishing():
    """Прирост от каждого следующего датчика падает, накопленное покрытие растёт.

    Модель рисунка честная: считается покрытие площади N кругами, брошенными
    жадно на случайное поле точек, — та самая функция, что в задаче.
    """
    rng = np.random.default_rng(7)
    # точки собраны в кластеры — как реальные маршруты, идущие пучками, а не
    # размазанные равномерно: только тогда видно выход кривой на «полку»
    pts = np.concatenate([
        rng.normal([28, 60], 13, size=(1400, 2)),
        rng.normal([64, 38], 15, size=(1600, 2)),
        rng.normal([82, 78], 11, size=(1000, 2)),
    ])
    cand = rng.random((300, 2)) * 100                    # кандидатные позиции
    R = 19.0
    covered = np.zeros(len(pts), dtype=bool)
    gains, total = [], []
    for _ in range(10):
        best, best_gain, best_mask = None, -1, None
        for c in cand:
            m = (np.hypot(pts[:, 0] - c[0], pts[:, 1] - c[1]) <= R) & ~covered
            g = int(m.sum())
            if g > best_gain:
                best, best_gain, best_mask = c, g, m
        covered |= best_mask
        gains.append(best_gain / len(pts) * 100)
        total.append(covered.sum() / len(pts) * 100)

    x = np.arange(1, len(gains) + 1)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 3.9))

    ax1.bar(x, gains, color=C_MAIN, width=0.62)
    ax1.set_title("Прирост от каждого следующего датчика\n(предельная полезность Δ)")
    ax1.set_xlabel("номер добавляемого датчика")
    ax1.set_ylabel("прирост покрытия, %")
    ax1.set_xticks(x)
    ax1.grid(axis="y", zorder=0)
    ax1.set_axisbelow(True)
    ax1.set_ylim(0, max(gains) * 1.32)
    for i, g in enumerate(gains):
        ax1.text(x[i], g + max(gains) * 0.02, "%.1f" % g, ha="center",
                 fontsize=8, color="#34495e")
    ax1.annotate("каждый следующий\nдаёт меньше предыдущего",
                 xy=(x[6], gains[6]), xytext=(x[3] + 0.2, max(gains) * 1.13),
                 fontsize=9, color=C_ACC,
                 arrowprops=dict(arrowstyle="->", color=C_ACC, lw=1.1))

    ax2.plot(x, total, "o-", color=C_OK, lw=2, ms=5)
    ax2.set_title("Накопленное покрытие F(S)\n(монотонно растёт, но всё медленнее)")
    ax2.set_xlabel("число размещённых датчиков  |S|")
    ax2.set_ylabel("покрыто, %")
    ax2.set_xticks(x)
    ax2.grid(True)
    ax2.set_axisbelow(True)
    ax2.set_ylim(0, 100)
    ax2.annotate("«полка» насыщения",
                 xy=(x[-1], total[-1]), xytext=(x[-4], total[-1] - 26),
                 fontsize=9, color=C_GREY,
                 arrowprops=dict(arrowstyle="->", color=C_GREY, lw=1.0))

    fig.tight_layout()
    save(fig, "diminishing_returns.svg")


# ─────────────────────────────────────────────────────────────────────────────
# 2. НАСЫЩЕНИЕ min(n, k)/k — почему засечки сверх k не вознаграждаются
# ─────────────────────────────────────────────────────────────────────────────
def fig_saturation():
    k = 3
    n = np.arange(0, 9)
    y = np.minimum(n, k) / k
    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    ax.step(n, y, where="post", color=C_MAIN, lw=2.2, label="min(n, k) / k   при k = 3")
    ax.plot(n, n / k, "--", color=C_GREY, lw=1.4, label="n / k   (без усечения)")
    ax.axhline(1.0, color=C_ACC, lw=1.0, ls=":")
    ax.axvline(k, color=C_ACC, lw=1.0, ls=":")
    ax.text(k + 0.12, 0.12, "k = 3", color=C_ACC, fontsize=9)
    ax.fill_between([k, 8], 0, 1.0, color="#fdeaea", zorder=0)
    ax.text(5.4, 0.5, "здесь прирост = 0:\nдальше ловить\nэтот маршрут незачем",
            fontsize=9, color=C_ACC, ha="center")
    ax.set_xlabel("n — сколько РАЗНЫХ датчиков засекли маршрут")
    ax.set_ylabel("вклад в полезность")
    ax.set_title("Насыщение по кратности: усечение оператором min")
    ax.set_ylim(0, 1.75)
    ax.set_xlim(0, 8)
    ax.grid(True)
    ax.set_axisbelow(True)
    ax.legend(loc="upper left", fontsize=9, framealpha=0.95)
    fig.tight_layout()
    save(fig, "saturation.svg")


# ─────────────────────────────────────────────────────────────────────────────
# 3. ГАРАНТИЯ (1 − 1/e): откуда берётся число 0.632
# ─────────────────────────────────────────────────────────────────────────────
def fig_guarantee():
    N = np.arange(1, 31)
    bound = 1 - (1 - 1 / N) ** N
    fig, ax = plt.subplots(figsize=(6.4, 3.7))
    ax.plot(N, bound, "o-", color=C_MAIN, lw=1.8, ms=4,
            label=r"нижняя оценка  $1-(1-1/N)^N$")
    ax.axhline(1 - 1 / np.e, color=C_ACC, lw=1.6, ls="--",
               label=r"предел  $1-1/e \approx 0{,}632$")
    ax.set_xlabel("N — сколько датчиков размещаем")
    ax.set_ylabel("доля от оптимума, гарантированная жадному выбору")
    ax.set_title("Гарантия Немхаузера–Уолси–Фишера (1978):\nхуже 63,2 % от оптимума жадный алгоритм быть не может")
    ax.set_ylim(0.6, 1.02)
    ax.grid(True)
    ax.set_axisbelow(True)
    ax.legend(fontsize=9, loc="upper right", framealpha=0.95)
    ax.annotate("гарантия ХУДШЕГО случая;\nна практике 90–95 %",
                xy=(20, 1 - 1 / np.e), xytext=(11, 0.80),
                fontsize=9, color=C_GREY,
                arrowprops=dict(arrowstyle="->", color=C_GREY, lw=1.0))
    fig.tight_layout()
    save(fig, "greedy_guarantee.svg")


# ─────────────────────────────────────────────────────────────────────────────
# 4. СХОДИМОСТЬ SAA: оценка по выборке приближается к истинному среднему
# ─────────────────────────────────────────────────────────────────────────────
def fig_saa():
    rng = np.random.default_rng(3)
    true = 0.78
    t = np.arange(1, 601)
    fig, ax = plt.subplots(figsize=(6.8, 3.7))
    for _ in range(5):                                   # пять независимых прогонов
        draws = np.clip(rng.normal(true, 0.22, size=t.size), 0, 1)
        ax.plot(t, np.cumsum(draws) / t, lw=1.1, alpha=0.85)
    ax.axhline(true, color=C_ACC, lw=1.8, ls="--",
               label=r"истинное  $F(S)=\mathbb{E}[\varphi]$")
    band = 0.22 / np.sqrt(t)
    ax.fill_between(t, true - 2 * band, true + 2 * band, color=C_FILL, zorder=0,
                    label=r"коридор $\pm 2\sigma/\sqrt{t}$")
    ax.set_xlabel("t — сколько маршрутов накоплено в выборке")
    ax.set_ylabel(r"оценка  $F_t(S)$")
    ax.set_title("Метод выборочного среднего (SAA): пять прогонов\nразброс сужается как $1/\\sqrt{t}$")
    ax.set_xscale("log")
    ax.set_ylim(0.4, 1.05)
    ax.grid(True, which="both")
    ax.set_axisbelow(True)
    ax.legend(fontsize=9, loc="lower right", framealpha=0.95)
    fig.tight_layout()
    save(fig, "saa_convergence.svg")


# ─────────────────────────────────────────────────────────────────────────────
# 5. ЛОКАЛЬНЫЙ И ГЛОБАЛЬНЫЙ ОПТИМУМ
# ─────────────────────────────────────────────────────────────────────────────
def fig_extrema():
    x = np.linspace(0, 10, 600)
    y = np.sin(x) * np.exp(-0.12 * x) + 0.55 * np.sin(2.6 * x + 0.7) * np.exp(-0.05 * x)
    fig, ax = plt.subplots(figsize=(7.0, 3.4))
    ax.plot(x, y, color=C_MAIN, lw=2)
    gi = int(np.argmax(y))
    # локальный максимум: наибольший из остальных горбов
    loc_mask = (x > 5.0)
    li = int(np.argmax(np.where(loc_mask, y, -9)))
    ax.plot(x[gi], y[gi], "o", color=C_OK, ms=10, zorder=5)
    ax.plot(x[li], y[li], "o", color=C_ACC, ms=10, zorder=5)
    ax.annotate("ГЛОБАЛЬНЫЙ оптимум\n(лучший во всём допустимом множестве)",
                xy=(x[gi], y[gi]), xytext=(x[gi] - 0.3, y[gi] + 0.55),
                fontsize=9, color=C_OK, ha="center",
                arrowprops=dict(arrowstyle="->", color=C_OK, lw=1.1))
    ax.annotate("ЛОКАЛЬНЫЙ оптимум\n(лучший среди соседних —\nздесь застревает наивный поиск)",
                xy=(x[li], y[li]), xytext=(x[li] + 0.2, y[li] + 0.62),
                fontsize=9, color=C_ACC, ha="center",
                arrowprops=dict(arrowstyle="->", color=C_ACC, lw=1.1))
    ax.set_xlabel("переменная решения x")
    ax.set_ylabel("целевая функция F(x)")
    ax.set_title("Оптимум глобальный и локальный")
    ax.set_ylim(min(y) - 0.3, max(y) + 1.15)
    ax.grid(True)
    ax.set_axisbelow(True)
    fig.tight_layout()
    save(fig, "extrema.svg")


# ─────────────────────────────────────────────────────────────────────────────
# 6. ВОГНУТОСТЬ: что значит «вогнутая неубывающая функция»
# ─────────────────────────────────────────────────────────────────────────────
def fig_concavity():
    x = np.linspace(0, 6, 400)
    y = np.sqrt(x)
    fig, ax = plt.subplots(figsize=(6.2, 3.5))
    ax.plot(x, y, color=C_MAIN, lw=2.2, label=r"вогнутая неубывающая $g(x)=\sqrt{x}$")
    x1, x2 = 0.7, 5.2
    ax.plot([x1, x2], [np.sqrt(x1), np.sqrt(x2)], "--", color=C_ACC, lw=1.6,
            label="хорда между двумя точками")
    ax.plot([x1, x2], [np.sqrt(x1), np.sqrt(x2)], "o", color=C_ACC, ms=6)
    xm = 0.5 * (x1 + x2)
    ax.plot([xm, xm], [0.5 * (np.sqrt(x1) + np.sqrt(x2)), np.sqrt(xm)],
            color=C_OK, lw=2.4)
    ax.annotate("график ВЫШЕ хорды —\nэто и есть вогнутость",
                xy=(xm, 0.5 * (np.sqrt(xm) + 0.5 * (np.sqrt(x1) + np.sqrt(x2)))),
                xytext=(2.3, 0.62), fontsize=9, color=C_OK,
                arrowprops=dict(arrowstyle="->", color=C_OK, lw=1.1))
    ax.text(4.3, 2.35, "растёт всегда (неубывающая),\nно всё медленнее",
            fontsize=9, color=C_GREY, ha="center")
    ax.set_xlabel("аргумент")
    ax.set_ylabel("значение")
    ax.set_title("Вогнутая неубывающая функция —\nнепрерывный прообраз «убывающей отдачи»")
    ax.grid(True)
    ax.set_axisbelow(True)
    ax.legend(fontsize=9, loc="lower right", framealpha=0.95)
    fig.tight_layout()
    save(fig, "concavity.svg")


# ─────────────────────────────────────────────────────────────────────────────
# 7. ЭЛЛИПС ДОСТИЖИМОСТИ
# ─────────────────────────────────────────────────────────────────────────────
def fig_ellipse():
    AB, L = 100.0, 150.0
    a, c = L / 2, AB / 2
    b = np.sqrt(a * a - c * c)
    fig, ax = plt.subplots(figsize=(7.4, 4.3))
    ax.add_patch(Ellipse((0, 0), 2 * a, 2 * b, facecolor=C_FILL,
                         edgecolor=C_MAIN, lw=1.8, zorder=1))
    A, B = (-c, 0.0), (c, 0.0)
    ax.plot(*A, "o", color=C_ACC, ms=9, zorder=4)
    ax.plot(*B, "o", color=C_ACC, ms=9, zorder=4)
    ax.text(A[0] - 3, -8.5, "A (старт)", ha="right", fontsize=10, color=C_ACC)
    ax.text(B[0] + 3, -8.5, "B (цель)", ha="left", fontsize=10, color=C_ACC)

    P = (12.0, 45.0)
    ax.plot(*P, "o", color=C_OK, ms=8, zorder=4)
    ax.plot([A[0], P[0]], [A[1], P[1]], color=C_OK, lw=1.5)
    ax.plot([P[0], B[0]], [P[1], B[1]], color=C_OK, lw=1.5)
    ax.text(P[0] + 2, P[1] + 3, "P", fontsize=11, color=C_OK)
    ax.text(-28, 27, "|PA|", fontsize=10, color=C_OK, rotation=52)
    ax.text(35, 27, "|PB|", fontsize=10, color=C_OK, rotation=-62)

    # размерные линии уведены ВНИЗ, в пустую половину: наверху идут |PA| и |PB|
    # подписи ставятся ПОВЕРХ своих размерных линий на белой подложке — так они не
    # налезают ни на стрелку, ни на соседнюю надпись
    box = dict(boxstyle="round,pad=0.2", fc="white", ec="none")
    # большая полуось вынесена ПОД эллипс: внутри для неё нет свободной полосы
    ya = -b - 12
    ax.annotate("", xy=(-a, ya), xytext=(0, ya),
                arrowprops=dict(arrowstyle="<->", color="#34495e", lw=1.3))
    ax.plot([-a, -a], [ya - 3, 0], ":", color="#aab3bd", lw=1.0)
    ax.plot([0, 0], [ya - 3, -b], ":", color="#aab3bd", lw=1.0)
    ax.text(-a / 2, ya, "a = L_max / 2 = 75 км", ha="center", va="center",
            fontsize=9.5, color="#34495e", bbox=box)
    ax.annotate("", xy=(0, -b), xytext=(0, 0),
                arrowprops=dict(arrowstyle="<->", color="#34495e", lw=1.3))
    ax.text(0, -b / 2, "b = √(a² − c²) ≈ %.1f км" % b, ha="center", va="center",
            fontsize=9.5, color="#34495e", bbox=box)
    ax.annotate("", xy=(c, 6), xytext=(0, 6),
                arrowprops=dict(arrowstyle="<->", color=C_GREY, lw=1.2))
    ax.text(c / 2, 6, "c = |AB| / 2 = 50 км", ha="center", va="center",
            fontsize=9.5, color=C_GREY, bbox=box)

    ax.text(0, b + 9, "Любая точка маршрута длиной ≤ L_max лежит внутри:  |PA| + |PB| ≤ L_max",
            ha="center", fontsize=10.5, color=C_MAIN)
    ax.set_title("Эллипс достижимости при |AB| = 100 км и L_max = 150 км")
    ax.set_aspect("equal")
    ax.set_xlim(-a - 12, a + 12)
    ax.set_ylim(-b - 24, b + 22)
    ax.axis("off")
    fig.tight_layout()
    save(fig, "ellipse.svg")


# ─────────────────────────────────────────────────────────────────────────────
# 8. ДУГА ПО УГЛУ ОТКЛОНЕНИЯ θ
# ─────────────────────────────────────────────────────────────────────────────
def fig_arc():
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.0, 4.0),
                                   gridspec_kw={"width_ratios": [1.15, 1]})
    ch = 100.0
    A, B = np.array([0.0, 0.0]), np.array([ch, 0.0])
    ax1.plot([A[0], B[0]], [A[1], B[1]], "--", color=C_GREY, lw=1.3)
    ax1.text(ch / 2, -6, "хорда  c = |AB|", ha="center", fontsize=9.5, color=C_GREY)
    for th_deg, col, lw in [(15, "#8fb8e0", 1.4), (35, C_MAIN, 2.0), (60, C_ACC, 2.0)]:
        th = np.radians(th_deg)
        R = ch / (2 * np.sin(th))
        cx, cy = ch / 2, -R * np.cos(th)
        a0 = np.arctan2(A[1] - cy, A[0] - cx)
        a1 = np.arctan2(B[1] - cy, B[0] - cx)
        tt = np.linspace(a0, a1, 200)
        ax1.plot(cx + R * np.cos(tt), cy + R * np.sin(tt), color=col, lw=lw,
                 label="θ = %d°   длина %.0f км" % (th_deg, ch * th / np.sin(th)))
    ax1.plot(*A, "o", color="#2c3e50", ms=8)
    ax1.plot(*B, "o", color="#2c3e50", ms=8)
    ax1.text(A[0], -6, "A", ha="center", fontsize=11)
    ax1.text(B[0], -6, "B", ha="center", fontsize=11)
    # стрела прогиба показана размерной линией у самой выпуклой дуги, а не подписью в пустоте
    th60 = np.radians(60)
    s60 = ch * (1 - np.cos(th60)) / (2 * np.sin(th60))
    ax1.annotate("", xy=(ch / 2, s60), xytext=(ch / 2, 0),
                 arrowprops=dict(arrowstyle="<->", color=C_ACC, lw=1.2))
    ax1.text(ch / 2, s60 / 2, "стрела\nпрогиба s(θ)", fontsize=9, color=C_ACC,
             ha="center", va="center",
             bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none"))
    ax1.set_title("Дуга задаётся ОДНИМ числом — углом θ\n(θ = 0 — полёт по прямой)")
    ax1.legend(fontsize=8.5, loc="lower center", framealpha=0.95,
               bbox_to_anchor=(0.5, -0.16))
    ax1.set_aspect("equal")
    ax1.set_ylim(-14, s60 + 12)
    ax1.axis("off")

    th = np.radians(np.linspace(0.6, 110, 500))
    ax2.plot(np.degrees(th), ch * th / np.sin(th), color=C_MAIN, lw=2.1)
    ax2.axhline(150, color=C_ACC, lw=1.5, ls="--", label="запас хода L_max = 150 км")
    # θ_max — корень уравнения ℓ(θ) = L_max (бинарный поиск, как в коде)
    lo, hi = 1e-6, np.radians(179)
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if ch * mid / np.sin(mid) < 150:
            lo = mid
        else:
            hi = mid
    th_max = np.degrees(0.5 * (lo + hi))
    ax2.axvline(th_max, color=C_OK, lw=1.5, ls=":")
    ax2.plot([th_max], [150], "o", color=C_OK, ms=8)
    ax2.text(th_max - 2, 168, "θ_max ≈ %.1f°" % th_max, color=C_OK, fontsize=10, ha="right")
    ax2.set_xlabel("угол отклонения θ, °")
    ax2.set_ylabel("длина дуги ℓ(θ), км")
    ax2.set_title("ℓ(θ) = c · θ / sin θ  монотонно растёт;\nθ_max — где длина упирается в запас хода")
    ax2.set_xlim(0, 110)
    ax2.set_ylim(90, 230)
    ax2.grid(True)
    ax2.set_axisbelow(True)
    ax2.legend(fontsize=9, loc="upper left", framealpha=0.95)
    fig.tight_layout()
    save(fig, "arc_angle.svg")


# ─────────────────────────────────────────────────────────────────────────────
# 9. КОРИДОР ДВИЖЕНИЯ против эллипса: где датчик бесполезен
# ─────────────────────────────────────────────────────────────────────────────
def fig_corridor():
    ch, L = 100.0, 150.0
    a, c = L / 2, ch / 2
    b = np.sqrt(a * a - c * c)
    lo, hi = 1e-6, np.radians(179)
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if ch * mid / np.sin(mid) < L:
            lo = mid
        else:
            hi = mid
    th_max = 0.5 * (lo + hi)

    def arc_xy(th_deg, n=300):
        th = np.radians(th_deg)
        R = ch / (2 * np.sin(th))
        cx, cy = 0.0, -R * np.cos(th)
        a0 = np.arctan2(0 - cy, -c - cx)
        a1 = np.arctan2(0 - cy, c - cx)
        tt = np.linspace(a0, a1, n)
        return cx + R * np.cos(tt), cy + R * np.sin(tt)

    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    ax.add_patch(Ellipse((0, 0), 2 * a, 2 * b, facecolor="#f2f4f7",
                         edgecolor=C_GREY, lw=1.4, ls="--", zorder=1))
    xu, yu = arc_xy(np.degrees(th_max))
    ax.fill_between(xu, -yu, yu, color=C_FILL, zorder=2)
    ax.plot(xu, yu, color=C_ACC, lw=2, ls="--", zorder=3)
    ax.plot(xu, -yu, color=C_ACC, lw=2, ls="--", zorder=3)
    ax.text(0, -np.max(yu) - 5.5, "предельные маршруты ±θ_max — граница коридора",
            ha="center", fontsize=9.5, color=C_ACC)
    for th in (12, 28, 45):
        x1, y1 = arc_xy(th)
        ax.plot(x1, y1, color=C_MAIN, lw=1.1, alpha=0.8, zorder=4)
        ax.plot(x1, -y1, color=C_MAIN, lw=1.1, alpha=0.8, zorder=4)
    ax.plot([-c, c], [0, 0], color=C_MAIN, lw=1.1, alpha=0.8, zorder=4)
    ax.plot([-c, c], [0, 0], "o", color="#2c3e50", ms=8, zorder=5)
    ax.text(-c, -7.5, "A", ha="center", fontsize=11)
    ax.text(c, -7.5, "B", ha="center", fontsize=11)

    # МЁРТВЫЕ ЗОНЫ — то, ради чего рисунок и нужен: эллипс их включает, коридор нет
    for sx, lbl in [(-1, "за спиной у A"), (1, "за целью B")]:
        ax.plot(sx * (a - 9), 0, "x", color=C_ACC, ms=12, mew=2.6, zorder=6)
        ax.annotate("МЁРТВАЯ ЗОНА\n%s:\nдатчик бесполезен" % lbl,
                    xy=(sx * (a - 9), 0), xytext=(sx * (a - 4), sx * 0 + b * 0.62),
                    fontsize=8.5, color=C_ACC, ha="center",
                    arrowprops=dict(arrowstyle="->", color=C_ACC, lw=1.1))
    ax.text(0, 9, "КОРИДОР\nкандидаты под датчики — только здесь",
            ha="center", fontsize=10, color=C_MAIN)
    ax.text(0, -b - 14, "серым штрихом — эллипс достижимости: он длиннее коридора на "
                        "%.0f км с каждой стороны" % (a - c),
            ha="center", fontsize=9, color=C_GREY)
    ax.set_title("Коридор движения ⊂ эллипс достижимости")
    ax.set_aspect("equal")
    ax.set_xlim(-a - 22, a + 22)
    ax.set_ylim(-b - 24, b + 16)
    ax.axis("off")
    fig.tight_layout()
    save(fig, "corridor.svg")


# ─────────────────────────────────────────────────────────────────────────────
# 10. ЗОНА СТАРТА (вкладка 2) и огибающая достижимости
# ─────────────────────────────────────────────────────────────────────────────
def fig_start_zone():
    depth, width, L = 60.0, 120.0, 150.0
    A, B = np.array([0.0, 0.0]), np.array([100.0, 0.0])
    fig, ax = plt.subplots(figsize=(7.8, 4.3))
    ax.add_patch(Ellipse(A, depth, width, facecolor="#fbe9e7",
                         edgecolor=C_ACC, lw=1.7, zorder=3))
    ax.text(A[0] - 34, width / 2 + 4, "ЗОНА СТАРТА\nглубина %g × ширина %g км" % (depth, width),
            ha="center", fontsize=9.5, color=C_ACC)

    # огибающая: max по S0 из границы зоны от эллипсов (S0, B)
    ths = np.linspace(0, 2 * np.pi, 361)
    phis = np.linspace(0, 2 * np.pi, 240)
    S0 = np.stack([A[0] + (depth / 2) * np.cos(phis),
                   A[1] + (width / 2) * np.sin(phis)], axis=1)
    rmax = np.zeros_like(ths)
    for i, th in enumerate(ths):
        d = np.array([np.cos(th), np.sin(th)])
        v = B - S0                                       # вектор от старта к цели
        num = L ** 2 - np.einsum("ij,ij->i", v, v)
        den = 2 * (L + v @ d)
        rmax[i] = np.max(num / np.maximum(den, 1e-9))
    ax.fill(B[0] + rmax * np.cos(ths), B[1] + rmax * np.sin(ths),
            color=C_FILL, zorder=1)
    ax.plot(B[0] + rmax * np.cos(ths), B[1] + rmax * np.sin(ths),
            color=C_MAIN, lw=1.8, zorder=2,
            label="огибающая достижимости: dist(P, зона) + |P−B| ≤ L_max")

    rng = np.random.default_rng(11)
    for _ in range(7):
        ph = rng.uniform(0, 2 * np.pi)
        r = np.sqrt(rng.uniform(0, 1))
        s0 = A + np.array([r * (depth / 2) * np.cos(ph), r * (width / 2) * np.sin(ph)])
        tt = np.linspace(0, 1, 60)
        amp = rng.uniform(-38, 38)
        pth = np.outer(1 - tt, s0) + np.outer(tt, B)
        pth[:, 1] += amp * np.sin(np.pi * tt)
        ax.plot(pth[:, 0], pth[:, 1], color="#5b8fd0", lw=1.0, alpha=0.85, zorder=4)
    ax.plot(*B, "o", color=C_ACC, ms=9, zorder=5)
    ax.text(B[0], -12, "B (цель)", ha="center", fontsize=10, color=C_ACC)
    ax.set_title("Вкладка 2: старт — не точка, а ЗОНА неопределённости.\n"
                 "Показывается огибающая всех эллипсов (S₀, B), а не эллипс (A, B)")
    ax.set_aspect("equal")
    ax.axis("off")
    ax.legend(fontsize=9, loc="lower center", framealpha=0.95)
    fig.tight_layout()
    save(fig, "start_zone.svg")


# ─────────────────────────────────────────────────────────────────────────────
# 11. РАДИУС РАЗВОРОТА: физика ограничивает геометрию
# ─────────────────────────────────────────────────────────────────────────────
def fig_turn_radius():
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.0, 4.7))
    g = 9.81
    V = np.linspace(80, 400, 400) / 3.6                  # км/ч -> м/с
    for bank, col in [(15, "#9fc3e8"), (25, C_MAIN), (40, C_OK)]:
        R = V ** 2 / (g * np.tan(np.radians(bank))) / 1000.0
        ax1.plot(V * 3.6, R, color=col, lw=2, label="крен φ = %d°" % bank)
    V0 = 180 / 3.6
    R0 = V0 ** 2 / (g * np.tan(np.radians(25))) / 1000.0
    ax1.plot([180], [R0], "o", color=C_ACC, ms=9, zorder=5)
    ax1.annotate("рабочая точка модели:\n180 км/ч, 25° → R_min ≈ %.2f км" % R0,
                 xy=(180, R0), xytext=(205, R0 + 1.9), fontsize=9, color=C_ACC,
                 arrowprops=dict(arrowstyle="->", color=C_ACC, lw=1.1))
    ax1.set_xlabel("скорость V, км/ч")
    ax1.set_ylabel("R_min, км")
    ax1.set_title("R_min = V² / (g · tan φ)\nрастёт как КВАДРАТ скорости")
    ax1.grid(True)
    ax1.set_axisbelow(True)
    ax1.legend(fontsize=9, framealpha=0.95)

    # поворот на 70°: при таком довороте дуга скругления хорошо видна
    p0, p1, p2 = np.array([0.0, 0.0]), np.array([7.0, 0.0]), np.array([11.1, 7.7])
    ax2.plot([p0[0], p1[0], p2[0]], [p0[1], p1[1], p2[1]], "--",
             color=C_GREY, lw=1.6, label="ломаная: излом на месте — так аппарат не может")
    v_in = (p1 - p0) / np.linalg.norm(p1 - p0)
    v_out = (p2 - p1) / np.linalg.norm(p2 - p1)
    dpsi = np.arccos(np.clip(v_in @ v_out, -1, 1))
    R = 2.6
    T = R * np.tan(dpsi / 2)
    t_in, t_out = p1 - v_in * T, p1 + v_out * T
    nrm = np.array([-v_in[1], v_in[0]])
    ctr = t_in + nrm * R
    a0 = np.arctan2(t_in[1] - ctr[1], t_in[0] - ctr[0])
    a1 = np.arctan2(t_out[1] - ctr[1], t_out[0] - ctr[0])
    tt = np.linspace(a0, a1, 120)
    ax2.plot([p0[0], t_in[0]], [p0[1], t_in[1]], color=C_MAIN, lw=2.6)
    ax2.plot(ctr[0] + R * np.cos(tt), ctr[1] + R * np.sin(tt), color=C_MAIN, lw=2.6)
    ax2.plot([t_out[0], p2[0]], [t_out[1], p2[1]], color=C_MAIN, lw=2.6,
             label="реальный путь: угол скруглён дугой радиуса R ≥ R_min")
    ax2.add_patch(Circle(ctr, R, fill=False, color=C_OK, lw=1.1, ls=":"))
    ax2.plot(*ctr, "+", color=C_OK, ms=11, mew=2)
    ax2.plot([t_in[0], t_out[0]], [t_in[1], t_out[1]], "o", color=C_OK, ms=6, zorder=5)
    ax2.annotate("", xy=t_in, xytext=ctr,
                 arrowprops=dict(arrowstyle="->", color=C_OK, lw=1.3))
    ax2.text(ctr[0] - 1.3, ctr[1] + 0.9, "R", color=C_OK, fontsize=13)
    ax2.text(t_in[0], t_in[1] - 1.1, "точки касания", fontsize=9, color=C_OK, ha="center")
    ax2.add_patch(Arc(p1, 2.2, 2.2, angle=0, theta1=180 - np.degrees(dpsi),
                      theta2=180, color=C_GREY, lw=1.2))
    ax2.text(p1[0] + 1.9, p1[1] - 2.3, "Δψ = %.0f° — доворот" % np.degrees(dpsi),
             fontsize=9, color=C_GREY, ha="center")
    ax2.set_title("Скругление угла: кривизна пути ≤ 1 / R_min")
    ax2.set_aspect("equal")
    ax2.set_xlim(-1.0, 14.0)
    ax2.set_ylim(-5.0, 11.0)
    ax2.axis("off")
    ax2.legend(fontsize=8.5, loc="lower right", framealpha=0.95)
    fig.tight_layout()
    save(fig, "turn_radius.svg")


# ─────────────────────────────────────────────────────────────────────────────
# 12. СЕГМЕНТНОЕ ПОКРЫТИЕ: как считается равномерность
# ─────────────────────────────────────────────────────────────────────────────
def fig_segments():
    t = np.linspace(0, 1, 400)
    x = t * 100
    y = 18 * np.sin(np.pi * t) * np.sin(2.1 * np.pi * t + 0.5)
    L_seg = 10
    sensors = [(16, 6), (33, -6), (52, 9), (88, 2)]
    R = 11.0
    fig, ax = plt.subplots(figsize=(9.6, 3.6))
    for sx, sy in sensors:
        ax.add_patch(Circle((sx, sy), R, facecolor=C_FILL, edgecolor=C_MAIN,
                            lw=1.2, alpha=0.75, zorder=1))
        ax.plot(sx, sy, "^", color=C_MAIN, ms=9, zorder=5)
    idx = np.array_split(np.arange(len(t)), L_seg)
    covered = []
    for j, ii in enumerate(idx):
        seg = np.stack([x[ii], y[ii]], axis=1)
        hit = any(np.min(np.hypot(seg[:, 0] - sx, seg[:, 1] - sy)) <= R
                  for sx, sy in sensors)
        covered.append(hit)
        ax.plot(x[ii], y[ii], color=(C_OK if hit else C_ACC), lw=3.0, zorder=4)
        ax.text(x[ii].mean(), -26, str(j + 1), ha="center", fontsize=8.5,
                color=(C_OK if hit else C_ACC))
        ax.axvline(x[ii][0], color="#e5e9ee", lw=0.9, zorder=0)
    m = sum(covered)
    ax.text(50, 30, "m = %d покрытых сегментов из L_seg = %d   →   m / L_seg = %.1f"
            % (m, L_seg, m / L_seg), ha="center", fontsize=10.5, color="#2c3e50")
    ax.text(50, -33, "зелёный — сегмент задевает зону хотя бы одного датчика ХОТЯ БЫ ОДНОЙ точкой;\n"
                     "красный — «слепой» участок, на нём аппарат не наблюдается никем",
            ha="center", fontsize=9, color=C_GREY)
    ax.set_title("Равномерность сопровождения: маршрут делится на L_seg равных частей")
    ax.set_aspect("equal")
    ax.set_xlim(-14, 114)
    ax.set_ylim(-38, 36)
    ax.axis("off")
    fig.tight_layout()
    save(fig, "segments.svg")


# ─────────────────────────────────────────────────────────────────────────────
# 13. МНОЖИТЕЛЬ РЕЛЬЕФА relief_k (вкладка 3)
# ─────────────────────────────────────────────────────────────────────────────
def fig_relief():
    """relief_k = 1 − 0.75·f(A/60) − 0.45·f(B/35), зажат в [0.25, 1.40].

    f — насыщающая функция отклика: вверх круче, вниз слабее. Здесь показан
    характер зависимости от превышения по «району» при нулевой окрестности.
    """
    A = np.linspace(-60, 120, 500)

    def f(u):                                            # отклик с асимметрией
        return np.where(u >= 0, np.tanh(u), 0.45 * np.tanh(u))

    k = 1 - 0.75 * f(A / 60.0)
    k = np.clip(k, 0.25, 1.40)
    fig, ax = plt.subplots(figsize=(7.0, 3.8))
    ax.plot(A, k, color=C_MAIN, lw=2.3)
    ax.axhline(1.0, color=C_GREY, lw=1.0, ls="--")
    ax.axhline(1.40, color=C_OK, lw=1.2, ls=":", label="потолок K_MAX = 1.40")
    ax.axhline(0.25, color=C_ACC, lw=1.2, ls=":", label="пол K_MIN = 0.25")
    ax.axvline(0, color=C_GREY, lw=0.9)
    ax.axvline(80, color=C_ACC, lw=1.4, ls="--")
    ax.text(82, 1.05, "отсечка 80 м:\nвес = 0", fontsize=9, color=C_ACC)
    ax.fill_between(A, 1.0, k, where=(k > 1.0), color="#e8f6ee", zorder=0)
    ax.fill_between(A, k, 1.0, where=(k < 1.0), color="#fdeaea", zorder=0)
    ax.text(-42, 1.14, "НИЗИНА\nаппарат укрыт →\nвес растёт", fontsize=9, color=C_OK)
    ax.text(45, 0.45, "ГРЕБЕНЬ\nаппарат виден →\nвес падает", fontsize=9, color=C_ACC)
    ax.set_xlabel("превышение над районом A, м  (радиус усреднения 15 км)")
    ax.set_ylabel("множитель relief_k")
    ax.set_title("Рельеф — МНОЖИТЕЛЬ к весу, а не слагаемое:\nноль остаётся нулём (нет ориентира — нет коридора)")
    ax.set_ylim(0.1, 1.55)
    ax.grid(True)
    ax.set_axisbelow(True)
    ax.legend(fontsize=9, loc="upper right", framealpha=0.95)
    fig.tight_layout()
    save(fig, "relief_k.svg")


# ─────────────────────────────────────────────────────────────────────────────
# 14. ЖАДНЫЙ ВЫБОР ПО ШАГАМ: почему изолированно лучший — не всегда следующий
# ─────────────────────────────────────────────────────────────────────────────
def fig_greedy_steps():
    fig, axes = plt.subplots(1, 3, figsize=(11.4, 3.6))
    rng = np.random.default_rng(5)
    pts = np.concatenate([
        rng.normal([30, 55], 11, size=(220, 2)),
        rng.normal([62, 42], 13, size=(260, 2)),
        rng.normal([84, 72], 9, size=(140, 2)),
    ])
    cand = np.array([[30, 55], [44, 49], [62, 42], [84, 72]])
    names = ["c₁", "c₂", "c₃", "c₄"]
    R = 16.0
    chosen, covered = [], np.zeros(len(pts), dtype=bool)
    for step, ax in enumerate(axes):
        gains = []
        for c in cand:
            m = (np.hypot(pts[:, 0] - c[0], pts[:, 1] - c[1]) <= R) & ~covered
            gains.append(int(m.sum()))
        pick = int(np.argmax(gains))
        ax.plot(pts[~covered, 0], pts[~covered, 1], ".", color="#b6c2d1", ms=2.2)
        ax.plot(pts[covered, 0], pts[covered, 1], ".", color="#cfe3d6", ms=2.2)
        for j, c in enumerate(cand):
            done = j in chosen
            ax.add_patch(Circle(c, R, fill=False, lw=(2.0 if done else 1.0),
                                color=(C_OK if done else "#aebac9"),
                                ls=("-" if done else ":")))
            ax.plot(*c, "^" if done else "o",
                    color=(C_OK if done else ("#e67e22" if j == pick else C_GREY)),
                    ms=(10 if done else 7))
            lbl = "%s ✔" % names[j] if done else "%s: +%d" % (names[j], gains[j])
            # подпись уводится ПОД круг, если сверху уже есть чужая: иначе они
            # наезжают друг на друга у близко стоящих кандидатов
            dy = (R + 4.5) if c[1] > 50 else -(R + 8.0)
            ax.text(c[0], c[1] + dy, lbl, ha="center", fontsize=9,
                    color=(C_OK if done else ("#e67e22" if j == pick else C_GREY)),
                    bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none", alpha=0.85))
        m = (np.hypot(pts[:, 0] - cand[pick][0], pts[:, 1] - cand[pick][1]) <= R) & ~covered
        covered |= m
        chosen.append(pick)
        ax.set_title("Шаг %d: берём %s (прирост %d)" % (step + 1, names[pick], gains[pick]),
                     fontsize=10)
        ax.set_aspect("equal")
        ax.set_xlim(-2, 112)
        ax.set_ylim(8, 104)
        ax.axis("off")
    fig.suptitle("Жадный алгоритм: на каждом шаге — кандидат с наибольшим ПРЕДЕЛЬНЫМ приростом "
                 "(прирост считается заново, с учётом уже покрытого)", fontsize=10.5, y=1.04)
    fig.tight_layout()
    save(fig, "greedy_steps.svg")


# ═════════════════════════════════════════════════════════════════════════════
# ОБЩАЯ ЧАСТЬ: вероятность и язык математической записи.
# Эти рисунки не про датчики — они поясняют понятия, встречающиеся в любой
# оптимизационной задаче, и переносятся в другой проект вместе со скриптом.
# ═════════════════════════════════════════════════════════════════════════════

def fig_distributions():
    """Три закона распределения, которыми задаётся случайность в модели."""
    x = np.linspace(-4, 4, 800)
    fig, axes = plt.subplots(1, 3, figsize=(11.6, 3.4))

    # равномерное
    ax = axes[0]
    y = np.where(np.abs(x) <= 2, 1 / 4.0, 0.0)
    ax.fill_between(x, 0, y, color=C_FILL)
    ax.plot(x, y, color=C_MAIN, lw=2.2)
    ax.set_title("Равномерное  U(a, b)\nвсе значения равновероятны")
    ax.text(0, 0.28, "E[X] = (a+b)/2", ha="center", fontsize=9, color="#34495e")
    ax.text(-2, -0.035, "a", ha="center", fontsize=10, color=C_ACC)
    ax.text(2, -0.035, "b", ha="center", fontsize=10, color=C_ACC)
    ax.set_ylim(-0.06, 0.42)

    # нормальное
    ax = axes[1]
    y = np.exp(-x ** 2 / 2) / np.sqrt(2 * np.pi)
    ax.fill_between(x, 0, y, color=C_FILL)
    ax.plot(x, y, color=C_MAIN, lw=2.2)
    for k, al in ((1, 0.30), (2, 0.16)):
        ax.fill_between(x, 0, y, where=(np.abs(x) <= k), color=C_MAIN, alpha=al)
    ax.axvline(0, color=C_ACC, lw=1.3, ls="--")
    ax.text(0.12, 0.30, "μ", color=C_ACC, fontsize=11)
    ax.annotate("", xy=(1, 0.11), xytext=(0, 0.11),
                arrowprops=dict(arrowstyle="<->", color="#34495e", lw=1.2))
    ax.text(0.5, 0.13, "σ", ha="center", fontsize=10, color="#34495e")
    ax.set_title("Нормальное  N(μ, σ²)\nсредние значения чаще крайних")
    ax.text(0, -0.055, "±1σ — 68 %,  ±2σ — 95 %", ha="center", fontsize=8.5, color=C_GREY)
    ax.set_ylim(-0.09, 0.47)

    # усечённое нормальное — то, чем выбирается угол отклонения дуги
    ax = axes[2]
    y = np.exp(-x ** 2 / 2) / np.sqrt(2 * np.pi)
    y = np.where(np.abs(x) <= 2, y, 0.0)
    y = y / (np.trapezoid(y, x) if hasattr(np, "trapezoid") else np.trapz(y, x))
    ax.fill_between(x, 0, y, color="#e8f6ee")
    ax.plot(x, y, color=C_OK, lw=2.2)
    ax.axvline(-2, color=C_ACC, lw=1.3, ls=":")
    ax.axvline(2, color=C_ACC, lw=1.3, ls=":")
    ax.text(-2.05, 0.30, "−θ_max", ha="right", fontsize=9, color=C_ACC)
    ax.text(2.05, 0.30, "+θ_max", ha="left", fontsize=9, color=C_ACC)
    ax.set_title("Усечённое нормальное\nколокол, обрезанный пределом")
    ax.text(0, -0.082, "так выбирается угол дуги:\nпрямая чаще, предельная реже",
            ha="center", fontsize=8.5, color=C_GREY)
    ax.set_ylim(-0.13, 0.47)

    for ax in axes:
        ax.set_xlabel("значение случайной величины")
        ax.grid(True)
        ax.set_axisbelow(True)
        ax.set_yticks([])
    axes[0].set_ylabel("плотность вероятности")
    fig.tight_layout()
    save(fig, "distributions.svg")


def fig_expectation():
    """Матожидание — центр тяжести; дисперсия — разброс вокруг него."""
    rng = np.random.default_rng(4)
    a = rng.normal(0.62, 0.06, 4000)
    b = rng.normal(0.62, 0.17, 4000)
    fig, ax = plt.subplots(figsize=(7.6, 3.9))
    bins = np.linspace(0.05, 1.2, 70)
    ax.hist(a, bins=bins, color=C_MAIN, alpha=0.65, label="σ = 0.06 — узкий разброс")
    ax.hist(b, bins=bins, color=C_ACC, alpha=0.45, label="σ = 0.17 — широкий разброс")
    ax.axvline(0.62, color="#2c3e50", lw=2.0)
    ax.text(0.633, ax.get_ylim()[1] * 0.93, "𝔼[X] = 0.62\nодно и то же",
            fontsize=9.5, color="#2c3e50")
    ax.annotate("", xy=(0.62 + 0.17, 60), xytext=(0.62, 60),
                arrowprops=dict(arrowstyle="<->", color=C_ACC, lw=1.4))
    ax.text(0.62 + 0.085, 78, "σ", ha="center", fontsize=11, color=C_ACC)
    ax.set_xlabel("полезность расстановки на одном маршруте")
    ax.set_ylabel("сколько маршрутов")
    ax.set_title("Матожидание говорит ГДЕ центр, дисперсия — НАСКОЛЬКО широко\n"
                 "оптимизация по среднему обе картины считает одинаковыми")
    ax.legend(fontsize=9, framealpha=0.95)
    ax.grid(True)
    ax.set_axisbelow(True)
    fig.tight_layout()
    save(fig, "expectation_variance.svg")


def fig_risk_criteria():
    """Три разных критерия на одном распределении: среднее, квантиль, худший случай."""
    rng = np.random.default_rng(9)
    v = np.clip(rng.normal(0.72, 0.14, 200000), 0, 1)
    fig, ax = plt.subplots(figsize=(8.4, 4.0))
    ax.hist(v, bins=120, color=C_FILL, edgecolor="#c3d6ea", lw=0.4)
    mean = v.mean()
    q10 = np.quantile(v, 0.10)
    cvar = v[v <= q10].mean()
    worst = v.min()
    top = ax.get_ylim()[1]
    for val, col, name, dy in ((mean, C_MAIN, "𝔼[X] = %.2f\nсреднее" % mean, 0.92),
                               (q10, C_OK, "квантиль 10 %% = %.2f\nVaR" % q10, 0.70),
                               (cvar, "#e67e22", "CVaR = %.2f\nсреднее худших 10 %%" % cvar, 0.48),
                               (worst, C_ACC, "минимум = %.2f\nхудший случай" % worst, 0.26)):
        ax.axvline(val, color=col, lw=2.0)
        ax.text(val + 0.006, top * dy, name, fontsize=9, color=col)
    ax.fill_between([0, q10], 0, top, color="#fdeaea", zorder=0)
    ax.set_xlim(0.1, 1.02)
    ax.set_xlabel("качество решения на случайном сценарии")
    ax.set_ylabel("сколько сценариев")
    ax.set_title("Одно распределение — четыре разных ответа на вопрос «насколько хорошо»\n"
                 "выбор критерия важнее выбора алгоритма")
    ax.grid(True)
    ax.set_axisbelow(True)
    fig.tight_layout()
    save(fig, "risk_criteria.svg")


def fig_chance_constraint():
    """Вероятностное ограничение: не «в среднем хорошо», а «плохо — редко»."""
    rng = np.random.default_rng(2)
    good = np.clip(rng.normal(0.70, 0.09, 60000), 0, 1)
    risky = np.clip(rng.normal(0.76, 0.20, 60000), 0, 1)
    thr = 0.5
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.6, 3.8), sharey=True)
    for ax, data, name in ((ax1, risky, "Вариант A"), (ax2, good, "Вариант B")):
        ax.hist(data, bins=90, color=C_FILL, edgecolor="#c3d6ea", lw=0.4)
        bad = float((data < thr).mean())
        ax.axvline(thr, color=C_ACC, lw=2.0, ls="--")
        top = ax.get_ylim()[1]
        ax.fill_between([0, thr], 0, top, color="#fdeaea", zorder=0)
        ok = bad <= 0.05
        ax.set_title("%s:  𝔼 = %.2f,  ниже порога %.0f %%\n%s"
                     % (name, data.mean(), bad * 100,
                        "ограничение выполнено ✔" if ok else "ограничение НАРУШЕНО ✘"),
                     color=(C_OK if ok else C_ACC), fontsize=10)
        ax.set_xlabel("качество на сценарии")
        ax.set_xlim(0.1, 1.02)
        ax.grid(True)
        ax.set_axisbelow(True)
    ax1.set_ylabel("сколько сценариев")
    ax1.text(thr - 0.02, ax1.get_ylim()[1] * 0.55, "порог q", ha="right",
             fontsize=9.5, color=C_ACC)
    fig.suptitle("Вероятностное ограничение  ℙ( φ ≥ q ) ≥ 0.95:  у A среднее ВЫШЕ, "
                 "но провалов больше нормы", fontsize=10.5, y=1.05)
    fig.tight_layout()
    save(fig, "chance_constraint.svg")


def fig_sets():
    """Операции над множествами — язык, на котором записаны все ограничения."""
    fig, axes = plt.subplots(1, 4, figsize=(11.6, 2.9))
    ops = [("A ∪ B", "объединение:\nхотя бы в одном"),
           ("A ∩ B", "пересечение:\nв обоих сразу"),
           ("A \\ B", "разность:\nв A, но не в B"),
           ("A ⊆ B", "подмножество:\nвсё A лежит в B")]
    for ax, (sym, name) in zip(axes, ops):
        if sym == "A ⊆ B":
            ax.add_patch(Circle((0.55, 0.5), 0.42, facecolor=C_FILL,
                                edgecolor=C_MAIN, lw=1.6))
            ax.add_patch(Circle((0.45, 0.5), 0.20, facecolor="#9dc3e6",
                                edgecolor=C_MAIN, lw=1.6))
            ax.text(0.45, 0.5, "A", ha="center", va="center", fontsize=11)
            ax.text(0.85, 0.5, "B", ha="center", va="center", fontsize=11)
        else:
            ca, cb = (0.38, 0.5), (0.64, 0.5)
            r = 0.28
            if sym == "A ∪ B":
                ax.add_patch(Circle(ca, r, facecolor=C_MAIN, alpha=0.55, lw=0))
                ax.add_patch(Circle(cb, r, facecolor=C_MAIN, alpha=0.55, lw=0))
            elif sym == "A ∩ B":
                th = np.linspace(0, 2 * np.pi, 400)
                ax.add_patch(Circle(ca, r, facecolor=C_FILL, lw=0))
                ax.add_patch(Circle(cb, r, facecolor=C_FILL, lw=0))
                xs = np.linspace(0.36, 0.66, 300)
                ya = np.sqrt(np.maximum(r ** 2 - (xs - ca[0]) ** 2, 0)) + ca[1]
                yb = np.sqrt(np.maximum(r ** 2 - (xs - cb[0]) ** 2, 0)) + cb[1]
                up = np.minimum(ya, yb)
                ax.fill_between(xs, 2 * ca[1] - up, up, color=C_MAIN, alpha=0.65, lw=0)
            else:
                ax.add_patch(Circle(ca, r, facecolor=C_MAIN, alpha=0.55, lw=0))
                ax.add_patch(Circle(cb, r, facecolor="white", lw=0))
            ax.add_patch(Circle(ca, r, fill=False, edgecolor=C_MAIN, lw=1.6))
            ax.add_patch(Circle(cb, r, fill=False, edgecolor=C_MAIN, lw=1.6))
            ax.text(0.20, 0.5, "A", ha="center", va="center", fontsize=11)
            ax.text(0.82, 0.5, "B", ha="center", va="center", fontsize=11)
        ax.set_title(sym, fontsize=13)
        ax.text(0.5, -0.14, name, ha="center", fontsize=9, color=C_GREY)
        ax.set_xlim(0, 1.05)
        ax.set_ylim(-0.26, 1)
        ax.set_aspect("equal")
        ax.axis("off")
    fig.tight_layout()
    save(fig, "sets_venn.svg")


def fig_integral():
    """Сумма и интеграл — одно и то же действие при разном шаге."""
    f = lambda t: 0.6 + 0.5 * np.sin(1.5 * t) + 0.12 * t
    x = np.linspace(0, 5, 500)
    fig, axes = plt.subplots(1, 3, figsize=(11.6, 3.5), sharey=True)
    for ax, n, name in ((axes[0], 5, "n = 5 столбиков"),
                        (axes[1], 15, "n = 15"),
                        (axes[2], 200, "n → ∞:  это и есть интеграл")):
        xs = np.linspace(0, 5, n + 1)
        w = xs[1] - xs[0]
        ax.bar(xs[:-1], f(xs[:-1]), width=w, align="edge",
               color=C_FILL, edgecolor="#8fb8e0", lw=0.7)
        ax.plot(x, f(x), color=C_MAIN, lw=2.2)
        approx = float(np.sum(f(xs[:-1]) * w))
        ax.set_title("%s\nсумма ≈ %.3f" % (name, approx), fontsize=10)
        ax.set_xlabel("x")
        ax.grid(True)
        ax.set_axisbelow(True)
    axes[0].set_ylabel("f(x)")
    axes[0].text(0.15, 1.72, "Σ f(xᵢ)·Δx", fontsize=11, color="#34495e")
    axes[2].text(0.15, 1.72, "∫ f(x) dx", fontsize=12, color=C_ACC)
    fig.suptitle("Интеграл — предел суммы: «сложить много узких столбиков». "
                 "Матожидание непрерывной величины считается ровно так",
                 fontsize=10.5, y=1.04)
    fig.tight_layout()
    save(fig, "integral_area.svg")


def fig_convex():
    """Выпуклое множество: отрезок между любыми точками не выходит наружу."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.0, 3.5))
    th = np.linspace(0, 2 * np.pi, 300)

    ax1.fill(0.5 + 0.42 * np.cos(th), 0.5 + 0.33 * np.sin(th),
             color=C_FILL, ec=C_MAIN, lw=1.8)
    p, q = (0.2, 0.42), (0.85, 0.6)
    ax1.plot(*zip(p, q), color=C_OK, lw=2.4)
    ax1.plot(*zip(p, q), "o", color=C_OK, ms=7)
    ax1.set_title("ВЫПУКЛОЕ множество\nотрезок целиком внутри — всегда", color=C_OK)

    r = 0.34 + 0.16 * np.cos(3 * th)
    ax2.fill(0.5 + r * np.cos(th), 0.5 + r * np.sin(th),
             color="#fdeaea", ec=C_ACC, lw=1.8)
    p, q = (0.19, 0.66), (0.80, 0.66)
    ax2.plot(*zip(p, q), color=C_ACC, lw=2.4, ls="--")
    ax2.plot(*zip(p, q), "o", color=C_ACC, ms=7)
    ax2.text(0.5, 0.72, "отрезок вышел наружу", ha="center", fontsize=9, color=C_ACC)
    ax2.set_title("НЕвыпуклое множество\nздесь локальный оптимум ≠ глобальному",
                  color=C_ACC)

    for ax in (ax1, ax2):
        ax.set_xlim(0, 1)
        ax.set_ylim(0.05, 0.95)
        ax.set_aspect("equal")
        ax.axis("off")
    fig.tight_layout()
    save(fig, "convex_set.svg")


def fig_gradient():
    """Линии уровня и градиент: как выглядит «идти вверх» в непрерывной задаче."""
    x = np.linspace(-3, 3, 300)
    y = np.linspace(-2.2, 2.2, 300)
    X, Y = np.meshgrid(x, y)
    Z = np.exp(-((X - 0.8) ** 2 + (Y - 0.4) ** 2) / 1.6) \
        + 0.65 * np.exp(-((X + 1.4) ** 2 + (Y + 0.9) ** 2) / 0.9)
    fig, ax = plt.subplots(figsize=(7.4, 4.0))
    cs = ax.contour(X, Y, Z, levels=12, cmap="Blues", linewidths=1.1)
    ax.clabel(cs, inline=True, fontsize=7, fmt="%.2f")
    for p in ((-0.6, -0.3), (1.9, 1.2), (-2.1, 0.6)):
        i = np.argmin(np.abs(x - p[0]))
        j = np.argmin(np.abs(y - p[1]))
        gy, gx = np.gradient(Z, y, x)
        g = np.array([gx[j, i], gy[j, i]])
        g = g / (np.linalg.norm(g) + 1e-9) * 0.6
        ax.arrow(p[0], p[1], g[0], g[1], head_width=0.11, color=C_ACC, lw=1.8)
        ax.plot(*p, "o", color=C_ACC, ms=6)
    ax.plot(0.8, 0.4, "*", color=C_OK, ms=18)
    ax.text(0.95, 0.5, "максимум", color=C_OK, fontsize=10)
    ax.set_title("Линии уровня и градиент ∇F\nстрелка — направление наибольшего роста, "
                 "она перпендикулярна линии уровня")
    ax.set_xlabel("x₁")
    ax.set_ylabel("x₂")
    ax.text(-2.9, -2.0, "⚠️ для ДИСКРЕТНОГО выбора позиций такой картины нет:\n"
                        "функция ступенчатая, градиент равен нулю почти всюду",
            fontsize=8.5, color=C_GREY)
    fig.tight_layout()
    save(fig, "gradient_contours.svg")


def fig_scenario_tree():
    """Откуда берётся выборка сценариев и что с ней делают."""
    fig, ax = plt.subplots(figsize=(9.2, 3.6))
    ax.axis("off")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 4)
    rng = np.random.default_rng(1)
    ax.text(0.6, 3.5, "распределение ℙ\n(задано моделью,\nформулы нет)",
            ha="center", fontsize=9.5, color=C_MAIN)
    xs = np.linspace(0.1, 1.1, 100)
    ax.plot(xs, 2.1 + 0.8 * np.exp(-((xs - 0.6) / 0.22) ** 2), color=C_MAIN, lw=1.8)
    for k in range(6):
        y = 2.7 - k * 0.42
        ax.annotate("", xy=(3.3, y), xytext=(1.3, 2.2),
                    arrowprops=dict(arrowstyle="->", color="#9db3c8", lw=1.0))
        t = np.linspace(0, 1, 40)
        amp = rng.uniform(-0.16, 0.16)
        ax.plot(3.4 + t * 1.9, y + amp * np.sin(np.pi * t) * 3,
                color="#5b8fd0", lw=1.2)
        ax.text(5.5, y, "γ%d" % (k + 1), fontsize=8.5, color=C_GREY, va="center")
    ax.text(4.4, 3.35, "выборка сценариев γ₁ … γₜ\n(розыгрыш случайных маршрутов)",
            ha="center", fontsize=9.5, color="#34495e")
    ax.annotate("", xy=(7.1, 2.0), xytext=(6.0, 2.0),
                arrowprops=dict(arrowstyle="->", color="#34495e", lw=1.6))
    ax.text(8.6, 2.55, "F_t(S) = (1/t)·Σ φ(S, γᵢ)", ha="center", fontsize=11,
            color=C_OK)
    ax.text(8.6, 2.05, "усреднить ПОЛЕЗНОСТЬ по сценариям", ha="center",
            fontsize=9, color=C_GREY)
    ax.text(8.6, 1.35, "⚠️ усредняется КРИТЕРИЙ,\nа не сами сценарии\nи не координаты решения",
            ha="center", fontsize=9, color=C_ACC)
    ax.set_title("Схема метода выборочного среднего (SAA)")
    fig.tight_layout()
    save(fig, "saa_scheme.svg")


FIGURES = [
    ("diminishing_returns.svg", "убывающая отдача: прирост Δ и накопленное F(S)", fig_diminishing),
    ("saturation.svg", "насыщение min(n,k)/k — почему засечки сверх k не нужны", fig_saturation),
    ("greedy_guarantee.svg", "гарантия (1 − 1/e) ≈ 0.632 и откуда она берётся", fig_guarantee),
    ("saa_convergence.svg", "сходимость выборочного среднего F_t → F", fig_saa),
    ("extrema.svg", "локальный и глобальный оптимум", fig_extrema),
    ("concavity.svg", "вогнутая неубывающая функция и хорда", fig_concavity),
    ("ellipse.svg", "эллипс достижимости, полуоси a, b, c", fig_ellipse),
    ("arc_angle.svg", "дуга по углу θ и предельный угол θ_max", fig_arc),
    ("corridor.svg", "коридор движения внутри эллипса", fig_corridor),
    ("start_zone.svg", "зона старта и огибающая достижимости (вкладка 2)", fig_start_zone),
    ("turn_radius.svg", "R_min от скорости и крена; скругление угла", fig_turn_radius),
    ("segments.svg", "сегментное покрытие маршрута (равномерность)", fig_segments),
    ("relief_k.svg", "множитель рельефа relief_k (вкладка 3)", fig_relief),
    ("greedy_steps.svg", "жадный выбор по шагам", fig_greedy_steps),
    # общая часть: вероятность и язык записи (не про датчики)
    ("distributions.svg", "равномерное, нормальное, усечённое нормальное", fig_distributions),
    ("expectation_variance.svg", "матожидание и дисперсия: центр и разброс", fig_expectation),
    ("risk_criteria.svg", "среднее / квантиль / CVaR / худший случай", fig_risk_criteria),
    ("chance_constraint.svg", "вероятностное ограничение ℙ(φ ≥ q) ≥ 1−ε", fig_chance_constraint),
    ("saa_scheme.svg", "схема метода выборочного среднего", fig_scenario_tree),
    ("sets_venn.svg", "операции над множествами", fig_sets),
    ("integral_area.svg", "интеграл как предел суммы", fig_integral),
    ("convex_set.svg", "выпуклое и невыпуклое множество", fig_convex),
    ("gradient_contours.svg", "линии уровня и градиент", fig_gradient),
]


def main():
    if "--list" in sys.argv:
        print("Рисунки пособия -> %s\n" % os.path.relpath(OUT, ROOT).replace("\\", "/"))
        for name, descr, _ in FIGURES:
            print("  %-26s %s" % (name, descr))
        return 0
    print("Собираю рисунки пособия -> %s\n" % os.path.relpath(OUT, ROOT).replace("\\", "/"))
    for _, _, fn in FIGURES:
        fn()
    print("\nГотово: %d рисунков." % len(FIGURES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
