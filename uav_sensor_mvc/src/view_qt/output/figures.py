# -*- coding: utf-8 -*-
"""
VIEW · Модуль вывода — РИСУНКИ ОТЧЁТА без Qt (рисунки 5–9 «Описания ПК», заказчик 06.10.2026): карта района,
нагрузка на средства, засечка по направлениям подлёта.

Одни и те же функции рисуют окна «Аналитики» («Показ карты», «Анализ нагрузки», «Показ сектора») и рисунки Word
(`docs/make_pk_pictures.py`) — поэтому окно и документ совпадают. Функция получает рисунок matplotlib (`Figure`),
снимок данных (`model/threat/report_data.py :: report_data`) и настройки — словарь; чего в нём нет, берётся из
умолчаний `*_DEFAULTS`, а они повторяют рисунки Word.
⚠️ НИЧЕГО НЕ СЧИТАЕТ и не знает Qt: холст даёт окно (`figure_base.py`) или генератор; `pyplot` не нужен.
"""
import numpy as np
from matplotlib import colormaps, rc_context
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.patches import Circle
from matplotlib.ticker import FuncFormatter
from matplotlib.transforms import Affine2D

from model.placement.sensor_load import sort_rows

WORD_COLORS = {1: "#1f77b4", 2: "#d62728", 4: "#2ca02c"}   # цвета типов в рисунках Word: МОГ, ПВН, большой
OTHER_COLOR = "#9467bd"                              # тип без своего цвета
FAMILIES = ("Times New Roman", "Arial", "DejaVu Sans")   # шрифты на выбор; первый — как в Word

# Рисунок 8 — нагрузка. type — номер типа или None (все средства); order — ключ `sensor_load.ORDERS`
LOAD_DEFAULTS = dict(type=None, order="number", horizontal=False, values=False, mean=False, full_scale=False,
                     grid=True, legend=True, title="", font=11, family=FAMILIES[0])
# Рисунок 9 — направления. routes/ge1/gek/zero — какие столбцы; share — доля от выборки, %; short — недобор кратности
SECTOR_DEFAULTS = dict(routes=True, ge1=False, gek=True, zero=False, share=False, values=False, short=False,
                       width=28.0, legend=True, title="", font=11, family=FAMILIES[0])
# Рисунки 5–7 — карта. background: none | tiles (подложка) | own (своя карта); frame: auto | area | view
MAP_DEFAULTS = dict(background="none", bg_alpha=1.0,
                    weights=True, weights_alpha=0.55, cmap="Greys", hide_low=False, weights_cut=0.0, colorbar=False,
                    routes=True, routes_n=250, routes_color="#c0392b", routes_lw=0.4, routes_alpha=0.15,
                    sensors=True, zones=True, zones_alpha=0.18, numbers=True, targets=True, entries=True,
                    legend=True, frame="auto", axes=True, title="", font=11, family=FAMILIES[0])
# Готовые виды «Показа карты»: рисунки 5, 6, 7 Word и «поверх подложки» — веса без пустых ячеек на карте местности
MAP_PRESETS = {
    "Рис. 5 — весовая карта": dict(background="none", weights=True, weights_alpha=1.0, cmap="turbo", hide_low=False,
                                    colorbar=True, routes=False, sensors=False, legend=False),
    "Рис. 6 — маршруты": dict(background="none", weights=True, weights_alpha=0.75, cmap="Greys", hide_low=False,
                               colorbar=False, routes=True, routes_n=400, routes_lw=0.6, routes_alpha=0.35,
                               sensors=False, legend=False),
    "Рис. 7 — размещение средств": dict(MAP_DEFAULTS),
    "Поверх подложки": dict(background="tiles", bg_alpha=1.0, weights=True, weights_alpha=0.6, cmap="turbo",
                            hide_low=True, weights_cut=10.0, colorbar=False, routes=True, routes_n=400, routes_lw=0.6,
                            routes_alpha=0.35, sensors=True, zones=True, zones_alpha=0.18, legend=True),
}


def _opts(defaults, opts):
    """Настройки рисунка: умолчания, поверх — заданные. Вход: умолчания, dict или None. Отдаёт: новый dict."""
    o = dict(defaults)                               # копия умолчаний — их не портим
    o.update(opts or {})
    return o


def _rc(o):
    """Шрифт рисунка на время построения. Вход: настройки (font, family). Отдаёт: контекст matplotlib."""
    return rc_context({"font.size": float(o["font"]), "font.family": [o["family"], "DejaVu Sans"]})


def _ticks(ax, o):
    """Подписи делений тем же шрифтом — и у делений, созданных позже. Вход: оси, настройки. Отдаёт: ничего."""
    try:                                             # labelfontfamily — matplotlib ≥ 3.8
        ax.tick_params(labelsize=float(o["font"]), labelfontfamily=o["family"])
    except (TypeError, ValueError):                  # старый matplotlib — только размер
        ax.tick_params(labelsize=float(o["font"]))


def _color(colors, tid):
    """Цвет типа. Вход: {тип: цвет} или None (цвета Word), номер типа. Отдаёт: строку цвета."""
    return (colors or WORD_COLORS).get(int(tid), OTHER_COLOR)


def _empty(ax, text):
    """Пустой рисунок с пояснением, почему пусто. Вход: оси, текст. Отдаёт: ничего."""
    ax.set_axis_off()
    ax.text(0.5, 0.5, text, ha="center", va="center", transform=ax.transAxes, wrap=True)


def draw_load(fig, load, opts=None, colors=None):
    """Рисунок 8 — нагрузка на каждое средство столбцами. Вход: рисунок, снимок `sensor_load` (rows, names),
    настройки (`LOAD_DEFAULTS`), {тип: цвет} или None — цвета Word. Отдаёт: оси."""
    o = _opts(LOAD_DEFAULTS, opts)                   # настройки рисунка
    fig.clear()
    with _rc(o):
        ax = fig.add_subplot()                       # оси рисунка
        rows = list((load or {}).get("rows", []))    # строки нагрузки
        if o["type"] is not None:                    # выбран тип — только его средства
            rows = [r for r in rows if r["type_id"] == int(o["type"])]
        rows = sort_rows(rows, o["order"])
        if not rows:                                 # рисовать нечего
            _empty(ax, "Нагрузки нет: нет маршрутов или средств на карте.")
            return ax
        pos = np.arange(len(rows))                   # места столбцов
        val = np.array([r["load_pct"] for r in rows], float)   # нагрузка, %
        col = [_color(colors, r["type_id"]) for r in rows]     # цвет столбца — по типу
        labels = [str(r["n"]) for r in rows]         # подписи — номера по списку
        if o["horizontal"]:                          # горизонтальные столбцы — длинный список читается сверху вниз
            bars = ax.barh(pos, val, color=col)      # столбцы — для подписей чисел
            ax.set_yticks(pos)
            ax.set_yticklabels(labels)
            ax.invert_yaxis()
            ax.set_ylabel("номер средства"); ax.set_xlabel("нагрузка, %")
        else:                                        # вертикальные — как рисунок Word
            bars = ax.bar(pos, val, color=col)       # столбцы — для подписей чисел
            ax.set_xticks(pos)
            ax.set_xticklabels(labels)
            ax.set_xlabel("номер средства"); ax.set_ylabel("нагрузка, %")
        if o["full_scale"]:                          # шкала всегда до 100 % — рисунки сравнимы между собой
            (ax.set_xlim if o["horizontal"] else ax.set_ylim)(0, 105)
        if o["values"]:                              # числа у концов столбцов
            ax.bar_label(bars, fmt="%.0f", padding=2, fontsize=max(6.0, float(o["font"]) - 3))
        if o["mean"]:                                # средняя нагрузка пунктиром
            mv = float(val.mean())                   # средняя нагрузка, %
            line = ax.axvline if o["horizontal"] else ax.axhline   # линия поперёк столбцов
            line(mv, color="#555555", ls="--", lw=1.0, label="средняя %.1f %%" % mv)
        if o["grid"]:                                # сетка вдоль шкалы нагрузки
            ax.grid(axis="x" if o["horizontal"] else "y", alpha=0.3)
            ax.set_axisbelow(True)
        if o["legend"]:                              # легенда — по типам на рисунке
            names = (load or {}).get("names", {})    # номер типа -> название
            hs = [Line2D([], [], marker="s", ls="", color=_color(colors, t), label=names.get(t, "тип %d" % t))
                  for t in sorted({r["type_id"] for r in rows})]   # значок типа
            if o["mean"]:                            # и пунктир средней
                hs.append(Line2D([], [], color="#555555", ls="--", label="средняя %.1f %%" % float(val.mean())))
            ax.legend(handles=hs, fontsize=max(6.0, float(o["font"]) - 2))
        if o["title"]:                               # заголовок задан
            ax.set_title(o["title"])
        _ticks(ax, o)
    return ax


def draw_sectors(fig, stats, opts=None):
    """Рисунок 9 — маршруты и засечка по 12 направлениям подлёта (полярная диаграмма, север сверху, по часовой).
    Вход: рисунок, строки `sector_stats`, настройки (`SECTOR_DEFAULTS`). Отдаёт: оси."""
    o = _opts(SECTOR_DEFAULTS, opts)                 # настройки рисунка
    fig.clear()
    with _rc(o):
        ax = fig.add_subplot(projection="polar")     # полярные оси
        ax.set_theta_zero_location("N"); ax.set_theta_direction(-1)
        stats = list(stats or [])                    # строки секторов
        n = np.array([e["n_routes"] for e in stats], float)   # маршрутов в секторе
        if not len(n) or n.sum() <= 0:               # рисовать нечего
            _empty(ax, "Маршрутов по направлениям нет: нет выборки или средств на карте.")
            return ax
        th = np.radians([e["deg"] for e in stats])   # азимуты секторов, рад
        g1 = np.array([e["n_ge1"] for e in stats], float)    # засечено хоть раз
        gk = np.array([e["n_ge_k"] for e in stats], float)   # засечено с кратностью k
        sc = 100.0 / n.sum() if o["share"] else 1.0  # множитель шкалы: доля от всей выборки, % — или штуки
        w = np.radians(float(o["width"]))            # ширина столбца, рад
        if o["routes"]:                              # все маршруты сектора — серым
            ax.bar(th, n * sc, width=w, color="#cccccc", label="маршрутов")
        if o["ge1"]:                                 # засечено хоть раз — светло-зелёным
            ax.bar(th, g1 * sc, width=w, color="#a6d96a", label="засечено ≥ 1")
        if o["gek"]:                                 # засечено с кратностью — зелёным
            ax.bar(th, gk * sc, width=w, color="#2ca02c", label="засечено ≥ k")
        if o["zero"]:                                # не засечено ни разу — красным, у края столбца
            ax.bar(th, (n - g1) * sc, bottom=g1 * sc, width=w, color="#d62728", label="без засечки")
        if o["short"]:                               # сектора, где кратности не хватило, — красной рамкой
            bad = np.array([not e.get("ok", True) for e in stats])   # сектор с недобором
            if bad.any():                            # есть такие сектора
                ax.bar(th[bad], n[bad] * sc, width=w, fill=False, edgecolor="#d62728", lw=1.6,
                       label="кратности не хватает")
        if o["values"]:                              # «засечено ≥ k / всего» у конца столбца
            pad = 0.06 * float(n.max() * sc)         # отступ подписи от столбца
            for a, nn, kk in zip(th, n, gk):         # сектор за сектором
                if nn > 0:                           # пустые сектора без подписи
                    ax.text(a, nn * sc + pad, "%d/%d" % (kk, nn), ha="center", va="center",
                            fontsize=max(6.0, float(o["font"]) - 3))
        if o["share"]:                               # шкала в процентах
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: "%g %%" % v))
        if o["legend"]:                              # легенда под диаграммой слева — как в Word
            ax.legend(loc="lower left", bbox_to_anchor=(-0.1, -0.12), fontsize=max(6.0, float(o["font"]) - 2))
        if o["title"]:                               # заголовок задан
            ax.set_title(o["title"], pad=18)
        _ticks(ax, o)
    return ax


def _grid_ext(g):
    """Рамка весовой сетки. Вход: dict сетки (ox, oy, h, nx, ny). Отдаёт: (x0, x1, y0, y1) км."""
    return (g["ox"], g["ox"] + g["nx"] * g["h"], g["oy"], g["oy"] + g["ny"] * g["h"])


def _weights(ax, g, o):
    """Весовая сетка: шкала до 97-го перцентиля, слабые ячейки по выбору прозрачны. Вход: оси, сетка, настройки.
    Отдаёт: растр (для шкалы цвета)."""
    w = np.clip(np.asarray(g["weight"], float), 0.0, None)   # вес без минусов — для показа
    vmax = float(np.percentile(w[w > 0], 97)) if (w > 0).any() else 1.0   # верх шкалы — 97-й перцентиль, как в Word
    if o["hide_low"]:                                # слабые ячейки — насквозь: под ними видна карта
        w = np.ma.masked_less_equal(w, vmax * float(o["weights_cut"]) / 100.0)
    cmap = colormaps[o["cmap"]].copy()               # палитра весов
    cmap.set_bad(alpha=0.0)                          # спрятанные ячейки — прозрачны
    return ax.imshow(w, origin="lower", extent=_grid_ext(g), cmap=cmap, alpha=float(o["weights_alpha"]),
                     vmin=0, vmax=vmax, zorder=1)


def _background(ax, snap, o):
    """Фон под рисунком: подложка-тайлы или своя карта (с поворотом и перекосом). Вход: оси, снимок (basemap —
    (rgba, рамка км); own_map — (rgba, коэф. QTransform)), настройки. Отдаёт: True — фон положен."""
    a = float(o["bg_alpha"])                         # непрозрачность фона
    if o["background"] == "tiles" and snap.get("basemap") is not None:   # подложка: строка 0 — юг
        rgba, ext = snap["basemap"]                  # картинка и её рамка, км
        ax.imshow(rgba, origin="lower", extent=ext, alpha=a, zorder=0)
        return True
    if o["background"] == "own" and snap.get("own_map") is not None:   # своя карта по аффинной привязке
        rgba, (m11, m12, m21, m22, dx, dy) = snap["own_map"]   # картинка и привязка: пиксель -> км
        h, w = rgba.shape[:2]                        # размер картинки, пикс.
        tr = Affine2D.from_values(m11, m12, m21, m22, dx, dy) + ax.transData   # x' = m11·x + m21·y + dx (Qt)
        ax.imshow(rgba, origin="lower", extent=(0, w, 0, h), alpha=a, zorder=0, transform=tr)
        return True
    return False


def draw_map(fig, snap, opts=None, colors=None):
    """Рисунки 5–7 — карта района: фон, весовая сетка, маршруты, средства с зонами, цели, точки входа.
    Вход: рисунок, снимок `report_data` (+ basemap, own_map, view_box от окна), настройки (`MAP_DEFAULTS`),
    {тип: цвет} или None — цвета Word. Отдаёт: оси."""
    o = _opts(MAP_DEFAULTS, opts)                    # настройки рисунка
    snap = snap or {}                                # снимок данных
    fig.clear()
    with _rc(o):
        ax = fig.add_subplot()                       # оси рисунка
        g = snap.get("grid")                         # весовая сетка или None
        if g is None and not snap.get("routes"):     # ни карты, ни маршрутов
            _empty(ax, "Карта не построена: задайте район и нажмите «Построить».")
            return ax
        im = _weights(ax, g, o) if o["weights"] and g is not None else None   # растр весов
        R = snap.get("routes") or []                 # маршруты выборки, км
        if o["routes"] and R:                        # маршруты — одной коллекцией линий (быстрее, чем по одной)
            k = int(o["routes_n"])                   # сколько показать; 0 — все
            segs = [np.asarray(r, float).reshape(-1, 2) for r in (R[:k] if k > 0 else R)]   # линии, км
            ax.add_collection(LineCollection(segs, colors=o["routes_color"], linewidths=float(o["routes_lw"]),
                                             alpha=float(o["routes_alpha"]), zorder=2))
        S = snap.get("sensors") or []                # средства на карте
        types = snap.get("types") or {}              # номер типа -> имя, радиус
        small = max(6.0, float(o["font"]) - 4)       # шрифт номеров средств
        if o["sensors"] and S:                       # средства: зона, точка, номер
            for s in S:                              # средство за средством
                c = _color(colors, s["type_id"])     # цвет типа
                if o["zones"]:                       # круг зоны обнаружения
                    ax.add_patch(Circle((s["x_km"], s["y_km"]), s["r_km"], fc=c, ec=c,
                                        alpha=float(o["zones_alpha"]), lw=1.0, zorder=3))
                ax.plot(s["x_km"], s["y_km"], "o", ms=5, color=c, mec="black", mew=0.5, zorder=8)
                if o["numbers"]:                     # номер по списку
                    ax.annotate(str(s["n"]), (s["x_km"], s["y_km"]), xytext=(3, 3), textcoords="offset points",
                                fontsize=small, zorder=8)
        if o["targets"]:                             # объекты защиты — звёзды
            for i, t in enumerate(snap.get("targets") or []):   # цель за целью
                ax.plot(t[0], t[1], "*", ms=17, color="#ffd400", mec="black", mew=0.8, zorder=9)
                ax.annotate("О%d" % (i + 1), (t[0], t[1]), xytext=(6, 6), textcoords="offset points",
                            fontsize=max(6.0, float(o["font"]) - 2), weight="bold", zorder=9)
        e = np.asarray(snap.get("entries") or [], float).reshape(-1, 2)   # точки входа, км
        if o["entries"] and len(e):                  # точки входа — зелёные треугольники
            ax.plot(e[:, 0], e[:, 1], "^", ms=9, color="#1e7a1e", mec="white", zorder=9, ls="")
        ax.autoscale_view()
        lim = ax.get_xlim() + ax.get_ylim()          # рамка «как в Word» — по всему нарисованному
        if o["frame"] == "area" and g is not None:   # рамка весовой сетки
            lim = _grid_ext(g)
        elif o["frame"] == "view" and snap.get("view_box"):   # как на карте программы сейчас
            lim = tuple(snap["view_box"])
        _background(ax, snap, o)                     # фон — после рамки, иначе мозаика тайлов раздвинет её
        ax.set_xlim(lim[0], lim[1]); ax.set_ylim(lim[2], lim[3])
        ax.set_aspect("equal")
        if o["legend"] and o["sensors"] and S:       # легенда — по типам на карте
            hs = [Line2D([], [], marker="o", ls="", color=_color(colors, t),
                         label="%s (R %.1f км)" % (types.get(t, {}).get("name", "тип %d" % t),
                                                   types.get(t, {}).get("r_km", 0.0)))
                  for t in sorted({int(s["type_id"]) for s in S})]   # значок типа
            ax.legend(handles=hs, loc="upper left", fontsize=max(6.0, float(o["font"]) - 2), framealpha=0.9)
        if o["colorbar"] and im is not None:         # шкала весов справа
            fig.colorbar(im, ax=ax, shrink=0.8, label="вес ячейки (предпочтительность пролёта)")
        if o["axes"]:                                # оси в километрах
            ax.set_xlabel("X, км"); ax.set_ylabel("Y, км")
        else:                                        # картинка без рамки и осей
            ax.set_axis_off()
        if o["title"]:                               # заголовок задан
            ax.set_title(o["title"])
        _ticks(ax, o)
    return ax
