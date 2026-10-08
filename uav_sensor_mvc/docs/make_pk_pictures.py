# -*- coding: utf-8 -*-
"""РИСУНКИ И ЧИСЛА к «Описанию ПК» (вкладка 3) — по РЕАЛЬНОМУ расчёту участка, как контрольный прогон (журнал 366).

Схемы модулей и этапов, весовая карта, маршруты, размещение средств, нагрузка, засечка по направлениям, снимок окна.
Рисунки карты, нагрузки и направлений рисует модуль вывода программы (`src/view_qt/output/figures.py`) — те же
функции, что окна «Аналитики», поэтому рисунок документа и окно программы совпадают (06.10.2026).
Числа — в `docs/картинки/пк/числа.json`: их берёт `make_pk_description_docx.py`, подпись и рисунок из одного прогона.
ЗАПУСК: `python docs/make_pk_pictures.py` (≈ 1–2 мин) · `--no-shot` — без снимков окон.
"""
import json
import os
import sys
import time

import numpy as np

import matplotlib
matplotlib.use("Agg")                                # без окна: скрипт консольный
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

if hasattr(sys.stdout, "reconfigure"):               # консоль Windows — кириллица без порчи
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_HERE = os.path.dirname(os.path.abspath(__file__))   # папка docs/
ROOT = os.path.dirname(_HERE)                        # корень проекта
sys.path.insert(0, os.path.join(ROOT, "src"))        # код программы — в src/ (журнал 364)
OUT = os.path.join(_HERE, "картинки", "пк")          # куда класть рисунки и числа
from model.threat.report_data import report_data   # noqa: E402 — снимок для рисунков, как у окон «Аналитики»
from view_qt.output import figures as F            # noqa: E402 — рисунки 5–9 рисует модуль вывода программы
plt.rcParams.update({"font.family": "Times New Roman", "font.size": 11})


def _save(fig, name):
    """Сохранить рисунок. Вход: фигура, имя файла. Отдаёт: путь."""
    p = os.path.join(OUT, name)                      # путь рисунка
    fig.savefig(p, dpi=170, bbox_inches="tight")
    plt.close(fig)
    return p


def _box(ax, x, y, w, h, text, fc):
    """Прямоугольник схемы с текстом по центру. Вход: оси, угол, размеры (доли), текст, цвет. Отдаёт: ничего."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.008", fc=fc, ec="#333333", lw=1.2))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=10, wrap=True)


def _arrow(ax, x0, y0, x1, y1, text=""):
    """Стрелка потока данных с подписью. Вход: оси, начало и конец (доли), подпись. Отдаёт: ничего."""
    ax.annotate("", (x1, y1), (x0, y0), arrowprops=dict(arrowstyle="-|>", lw=1.4, color="#333333"))
    if text:                                         # подпись у середины стрелки
        ax.text((x0 + x1) / 2, (y0 + y1) / 2 + 0.018, text, ha="center", fontsize=8.5, style="italic")


def fig_structure():
    """Рисунок: структурная схема ПК и потоки данных. Вход: нет. Отдаёт: путь."""
    fig, ax = plt.subplots(figsize=(11, 5.6))
    ax.set_axis_off(); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    _box(ax, 0.01, 0.70, 0.20, 0.22, "Геоданные\nOSM (.osm.pbf → .npz)\nматрица высот (GeoTIFF)", "#eef3e8")
    _box(ax, 0.01, 0.40, 0.20, 0.22, "Справочники\nтипы средств\nтипы БпЛА (ТТХ)", "#eef3e8")
    _box(ax, 0.01, 0.10, 0.20, 0.22, "Задание\nрайон, точки входа,\nобъекты защиты,\nсостав средств, T", "#eef3e8")
    _box(ax, 0.29, 0.40, 0.15, 0.22, "1. Ввод\nданных", "#dde8f5")
    _box(ax, 0.50, 0.40, 0.15, 0.22, "2. Выбор\nрежима\nрасчёта", "#dde8f5")
    _box(ax, 0.71, 0.30, 0.15, 0.42, "3. Расчёт\n\nвесовая карта\nмаршруты\nразмещение\nнагрузка,\nанализ", "#f5e6d6")
    _box(ax, 0.71, 0.02, 0.15, 0.16, "4. Вывод\nрезультатов", "#dde8f5")
    _box(ax, 0.50, 0.78, 0.32, 0.16, "5. Отображение данных (карта)", "#dde8f5")
    for y in (0.81, 0.51, 0.21):                     # три источника — в модуль ввода
        _arrow(ax, 0.21, y, 0.29, 0.51)
    _arrow(ax, 0.44, 0.51, 0.50, 0.51, "набор данных")
    _arrow(ax, 0.65, 0.51, 0.71, 0.51, "задание")
    _arrow(ax, 0.785, 0.30, 0.785, 0.18, "результаты")
    _arrow(ax, 0.86, 0.10, 0.90, 0.10)
    ax.text(0.905, 0.10, "таблицы,\nWord, файлы", va="center", fontsize=9)
    _arrow(ax, 0.785, 0.72, 0.785, 0.78)
    ax.text(0.80, 0.745, "слои, линии, позиции", fontsize=8.5, style="italic")
    ax.annotate("", (0.52, 0.78), (0.365, 0.62), arrowprops=dict(arrowstyle="<|-|>", lw=1.4, color="#333333"))
    ax.text(0.30, 0.71, "исходные данные /\nкорректировка", fontsize=8.5, style="italic")
    return _save(fig, "01_схема_модулей.png")


def fig_stages():
    """Рисунок: три этапа расчёта и их результаты. Вход: нет. Отдаёт: путь."""
    fig, ax = plt.subplots(figsize=(11, 3.4))
    ax.set_axis_off(); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    st = [("Этап 1. Сеть маршрутов", "весовая карта 500 м по OSM\nи рельефу → поля стоимости →\n"
           "стохастические маршруты с манёвром\n(R_min, запас хода L_max)", "тепловая карта пролётов,\nугрожаемые направления"),
          ("Этап 2. Размещение средств", "ПВН у объектов защиты по приоритету →\nобщий жадный проход по типам\n"
           "с правилами места; заданные\nпозиции неподвижны", "позиции МОГ, ПВН, ЗРК,\nтаблица координат"),
          ("Этап 3. Оценка", "засечка ≥ 1 и ≥ k, по направлениям,\nзащита объектов, нагрузка\nна каждое средство,"
           "\nвремя подлёта", "«Анализ», «Нагрузка»,\nвыгрузка в Word")]
    for i, (h, body, res) in enumerate(st):          # этап за этапом слева направо
        x = 0.02 + i * 0.33                          # левый край этапа, доля
        _box(ax, x, 0.42, 0.29, 0.50, h + "\n\n" + body, "#f5e6d6" if i == 1 else "#dde8f5")
        _box(ax, x + 0.03, 0.04, 0.23, 0.24, res, "#eef3e8")
        _arrow(ax, x + 0.145, 0.42, x + 0.145, 0.28)
        if i < 2:                                    # стрелка к следующему этапу
            _arrow(ax, x + 0.29, 0.67, x + 0.33, 0.67)
    return _save(fig, "02_этапы_расчёта.png")


def _fig_map(snap, view, name):
    """Рисунок карты района модулем вывода — как окно «Показ карты». Вход: снимок `report_data`, готовый вид
    (`figures.MAP_PRESETS`), имя файла. Отдаёт: путь."""
    fig = plt.figure(figsize=(10, 6.4))
    F.draw_map(fig, snap, F.MAP_PRESETS[view])
    return _save(fig, name)


def fig_weight(snap):
    """Рисунок: весовая карта угроз района. Вход: снимок `report_data`. Отдаёт: путь."""
    return _fig_map(snap, "Рис. 5 — весовая карта", "03_весовая_карта.png")


def fig_routes(snap):
    """Рисунок: выборка маршрутов пролёта (400 линий) поверх весовой карты. Вход: снимок. Отдаёт: путь."""
    return _fig_map(snap, "Рис. 6 — маршруты", "04_маршруты.png")


def fig_place(snap):
    """Рисунок: рассчитанные позиции средств с зонами. Вход: снимок. Отдаёт: путь."""
    return _fig_map(snap, "Рис. 7 — размещение средств", "05_размещение.png")


def fig_load(snap):
    """Рисунок: нагрузка на каждое средство — как окно «Анализ нагрузки». Вход: снимок. Отдаёт: путь."""
    fig = plt.figure(figsize=(10, 3.8))
    F.draw_load(fig, snap["load"])
    return _save(fig, "06_нагрузка.png")


def fig_compass(snap):
    """Рисунок: засечка по 12 направлениям подлёта — как окно «Показ сектора». Вход: снимок. Отдаёт: путь."""
    fig = plt.figure(figsize=(6, 6))
    F.draw_sectors(fig, snap["sectors"])
    return _save(fig, "07_направления.png")


def calc():
    """Эталонный расчёт участка (как `reference_run`). Вход: нет. Отдаёт: (модель, числа dict)."""
    from config import Params, THREAT_AREA
    from model.threat.threat_grid import ThreatModel
    from model.placement.sensors import sensor_types
    from model.threat.approach_time import approach_times
    p = Params()                                     # состав — как в примере применения: 13 МОГ, 18 ПВН, 4 ЗРК
    p.threat_N, p.threat_N2, p.threat_N_big, p.threat_iter_routes = 13, 18, 4, 1000
    t0 = time.time(); m = ThreatModel(p); m.set_relief(True); g = m.build(); t_b = time.time() - t0   # весовая карта, с
    m.set_sector_params(180.0, 8); m.set_sector_auto()   # 8 точек входа по кругу 360°
    t0 = time.time(); m.iter_batch(); t_r = time.time() - t0   # маршруты, с
    t0 = time.time(); m.place_sensors(); t_p = time.time() - t0   # размещение, с
    names = {s.type_id: (s.name, float(s.r_km)) for s in sensor_types(p)}   # тип -> (имя, R)
    me = m.metrics() or {}                           # засечка
    ld = m.sensor_load()                             # нагрузка
    tab = m.sensors_table()                          # позиции
    v = float(p.threat_speed_kmh)                    # скорость, км/ч
    num = dict(area=THREAT_AREA, nx=int(g.nx), ny=int(g.ny), h_km=float(g.h), w_km=float(g.nx * g.h), hgt_km=float(g.ny * g.h),
               T=int(m.iter_iteration), L_max=float(p.threat_L_max), v_kmh=v, bank=float(p.threat_bank_deg),
               R_min_km=float((v / 3.6) ** 2 / (9.81 * np.tan(np.radians(p.threat_bank_deg))) / 1000.0),
               targets=len(m.target_points_km()), entries=len(m.entry_points_km()),
               t_build=t_b, t_routes=t_r, t_place=t_p,
               det1=float(me.get("detect_frac", 0)), detk=float(me.get("detect_k_frac", 0)),
               mean_hits=float(me.get("mean_hits", 0)), k=int(p.threat_k),
               gap={str(t): float(r) for t, r in (me.get("gap") or {}).items()},   # граница Минью по типам
               lmax_auto=float(m.p.threat_L_max),
               types={str(t): dict(name=names[t][0], r=names[t][1], n=sum(1 for r in tab if int(r["type_id"]) == t))
                      for t in names if any(int(r["type_id"]) == t for r in tab)},
               load=[dict(n=r["n"], type_id=r["type_id"], name=names.get(r["type_id"], ("?",))[0], pct=r["load_pct"],
                          routes=r["routes"]) for r in ld["rows"]],
               table=[dict(n=r["n"], type_id=int(r["type_id"]), name=names.get(int(r["type_id"]), ("?",))[0],
                           lon=float(r.get("lon", np.nan)), lat=float(r.get("lat", np.nan))) for r in tab],
               approach=approach_times(m.iter_routes, v),
               sectors=[dict(deg=e["deg"], n=e["n_routes"], k=e["n_ge_k"]) for e in m.sector_stats()])
    return m, num, names


# Снимки окон для описания ПК: файл -> (метод вида, поле с окном), два окна — рядом на одном рисунке
WINDOWS = [("09_окно_исходные_данные.png", ("_open_input_window", "_input_dlg")),
           ("10_окно_сектор.png", ("_open_sector_window", "_sector_dlg"), ("_open_entries_table", "_entries_dlg")),
           ("11_окно_объекты.png", ("_open_targets_window", "_targets_dlg")),
           ("12_окно_датчики.png", ("_open_sensors_window", "_sensors_dlg"), ("_open_sensor_types", "_types_dlg")),
           ("13_окно_анализ.png", ("_open_analysis_window", "_analysis_dlg")),
           ("14_окно_нагрузка.png", ("_open_load_window", "_load_dlg"))]


def _shot_windows(app, v):
    """Снимки немодальных окон вкладки 3: открыть методом вида, снять, два окна склеить рядом.
    Вход: QApplication, вид вкладки 3. Отдаёт: dict файл -> путь (не снялся — нет в словаре)."""
    from PyQt5.QtGui import QColor, QPainter, QPixmap
    out = {}                                         # файл -> путь снимка
    for fname, *wins in WINDOWS:                     # рисунок за рисунком
        pms = []                                     # снимки окон этого рисунка
        for meth, attr in wins:                      # окно за окном
            try:
                getattr(v, meth)()
            except Exception as e:                   # окно не открылось — рисунок без него
                print("окно %s не открылось: %s" % (meth, e))
                continue
            for _ in range(25):                      # дорисовать окно
                app.processEvents(); time.sleep(0.02)
            dlg = getattr(v, attr, None)             # открытое окно
            if dlg is not None:                      # окно есть — снять
                pms.append(dlg.grab())
        if not pms:                                  # ни одно окно не снялось
            continue
        gap = 16                                     # зазор между окнами, px
        canvas = QPixmap(sum(pm.width() for pm in pms) + gap * (len(pms) - 1), max(pm.height() for pm in pms))   # общий снимок
        canvas.fill(QColor("white"))
        qp = QPainter(canvas); x = 0                 # рисовальщик и левый край очередного окна, px
        for pm in pms:                               # окна слева направо
            qp.drawPixmap(x, 0, pm); x += pm.width() + gap
        qp.end()
        path = os.path.join(OUT, fname)              # путь снимка
        if canvas.save(path):                        # записался — в отчёт
            out[fname] = path
        for _meth, attr in wins:                     # закрыть окна рисунка
            if getattr(v, attr, None) is not None:   # окно создавали
                getattr(v, attr).hide()
    return out


def shot(m):
    """Снимки главного окна вкладки 3 и окон ввода и анализа на модели из `calc` — без экрана и без второго расчёта.
    Вход: посчитанная модель (карта, маршруты, датчики). Отдаёт: dict файл -> путь."""
    import PyQt5
    pl = os.path.join(os.path.dirname(PyQt5.__file__), "Qt5", "plugins")   # плагины Qt
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", os.path.join(pl, "platforms"))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")   # без него offscreen рисует без букв
    from PyQt5.QtWidgets import QApplication
    from config import THREAT_BBOX_LONLAT
    from model.threat.geo_frame import bbox_lonlat_to_km
    from view_qt.tabs.threat_view import ThreatMapView
    from controller_qt.threat_controller import ThreatController
    app = QApplication.instance() or QApplication([])   # держит Qt живым
    v = ThreatMapView(m.bbox_km, m.lon0, m.lat0, m.p, area_max_km=bbox_lonlat_to_km(THREAT_BBOX_LONLAT, m.lon0, m.lat0))   # окно вкладки 3
    c = ThreatController(m, v)                       # связь: кнопки окна
    v.resize(1700, 1000); v.show()
    v.set_iter_checked(True)
    # ⚠️ отрисовка — как по окончании «Пакетно»: только рисует и считает показатели; ветку «build» не звать — при
    # включённых итерациях она перезапускает маршруты, и снимки разойдутся с числами текста
    c._on_task_done(("iter", None))
    if hasattr(v, "chk_iter_heat"):                  # тепловая карта маршрутов — результат «Пакетно»
        v.chk_iter_heat.setChecked(True)
    v.frame_bbox()                                   # вид — по району расчёта
    for _ in range(60):                              # дорисовать
        app.processEvents(); time.sleep(0.02)
    out = {}                                         # файл -> путь снимка
    p_out = os.path.join(OUT, "08_главное_окно.png")   # путь снимка главного окна
    if v.grab().save(p_out):                         # снимок виджета записался
        out["08_главное_окно.png"] = p_out
    out.update(_shot_windows(app, v))
    return out


def main():
    """Расчёт, рисунки, числа в JSON. Вход: argv. Отдаёт: код возврата."""
    os.makedirs(OUT, exist_ok=True)
    fig_structure(); fig_stages()
    m, num, names = calc()   # модель после расчёта и числа прогона
    snap = report_data(m)                            # снимок для рисунков — как у окон «Аналитики»
    fig_weight(snap); fig_routes(snap); fig_place(snap); fig_load(snap); fig_compass(snap)
    num["shot"] = None if "--no-shot" in sys.argv else shot(m)
    with open(os.path.join(OUT, "числа.json"), "w", encoding="utf-8") as f:
        json.dump(num, f, ensure_ascii=False, indent=1, default=float)
    print("готово: %s; район %.1f x %.1f км, T %d, средств %d, засечка >=k %.0f %%, расчёт %.1f + %.1f + %.1f с"
          % (OUT, num["w_km"], num["hgt_km"], num["T"], len(num["table"]), 100 * num["detk"],
             num["t_build"], num["t_routes"], num["t_place"]))
    return 0


if __name__ == "__main__":                           # запуск скриптом, не импорт
    sys.exit(main())
