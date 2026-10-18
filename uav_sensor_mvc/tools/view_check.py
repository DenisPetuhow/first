# -*- coding: utf-8 -*-
"""
ПРОВЕРКА ОБЗОРА СРЕДСТВА — фактический радиус (план 16: засечка по профилю 16.2, показ 16.4, обзор средств 16.15).

Эталонный расчёт, независимый от кода модели: по 72 направлениям луч от глаза датчика (земля + высота установки) до
БПЛА на высоте из ТТХ над землёй; тень — БПЛА ниже луча через вершину препятствия; фактический радиус r(аз) — дальность
до начала первой тени; R_факт — среднее r по 72 направлениям; остров (снова видно за тенью) в r не входит (В16.25).
Кривизна с рефракцией k = 4/3 — понижение d²/(2·k·R_З) (пособие 6 §10.3).

    python tools/view_check.py                     # самопроверка на случаях с известным ответом (без данных участка)
    python tools/view_check.py --mog               # участок arh: R_факт и острова у всех мест МОГ (как замер М13)
    python tools/view_check.py --place 40.33 62.76 --R 15 --hs 25   # одно место: r по направлениям, R_факт

Когда 16.2 будет в коде, r(аз) модели сверяется с `radius_by_az` в тех же местах: расхождение больше шага луча —
ошибка. Высоты построек пока «3 м × этажи + 1» (как в `sensor_layers`); после 16.5 — из результата заказчика.
Код возврата: 0 — самопроверка сошлась, 1 — расхождение.
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # корень проекта
for _p in (ROOT, os.path.join(ROOT, "src")):           # код программы — в src/ (журнал 364)
    if _p not in sys.path:   # ещё не в путях — добавить
        sys.path.insert(0, _p)
if hasattr(sys.stdout, "reconfigure"):   # печать в файл — UTF-8, иначе знаки «≥», «×» роняют вывод
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

K_REFR, R_EARTH_KM = 4.0 / 3.0, 6371.0                 # рефракция радиоволн, радиус Земли, км
AZ_DEG = np.arange(0.0, 360.0, 5.0)                    # 72 направления, градусы от севера по часовой
H_FOREST_M = 20.0                                      # высота леса, пока нет слоя высот (В16.4), м


def drop_m(d_km):
    """Понижение земли кривизной с рефракцией. Вход: дальность км. Отдаёт: м."""
    return np.asarray(d_km, float) ** 2 / (2.0 * K_REFR * R_EARTH_KM) * 1e3


def visibility(z_eye, ground, obst, dst, hs, hu):
    """Видим ли БПЛА в каждом отсчёте луча. Вход: земля под датчиком м, земля (A, n) м, предметы над землёй (A, n) м,
    дальности (n,) км, высота глаза и БПЛА над землёй м. Отдаёт: (A, n) bool."""
    z0 = float(z_eye) + hs                             # глаз датчика, м
    zz = np.asarray(ground, float) - drop_m(dst)[None]  # земля с учётом кривизны, м
    ang = (zz + obst - z0) / (dst[None] * 1e3)         # угол на верх препятствия
    # БПЛА видим, если его угол выше всех препятствий ближе него (накопленный максимум со сдвигом на отсчёт)
    prev = np.concatenate([np.full((ang.shape[0], 1), -np.inf), np.maximum.accumulate(ang, axis=1)[:, :-1]], axis=1)
    return (zz + hu - z0) / (dst[None] * 1e3) > prev


def radius_by_az(dst, vis):
    """Фактический радиус: до начала первой тени, без тени — R. Вход: дальности (n,) км, видимость (A, n).
    Отдаёт: (A,) км."""
    step = float(dst[1] - dst[0]) if len(dst) > 1 else float(dst[0])   # шаг луча, км
    n_ok = np.logical_and.accumulate(vis, axis=1).sum(axis=1)          # отсчётов подряд видно
    return np.where(n_ok >= len(dst), dst[-1], np.where(n_ok > 0, dst[np.maximum(n_ok - 1, 0)] + 0.5 * step, 0.5 * step))


def visible_by_az(dst, vis):
    """Видимая длина луча: до первой тени плюс острова за ней (R_факт с островами — его среднее, заказчик 18.10).
    Вход: дальности (n,) км, видимость (A, n). Отдаёт: (A,) км; островов нет — как `radius_by_az`."""
    step = float(dst[1] - dst[0]) if len(dst) > 1 else float(dst[0])   # шаг луча, км
    isl = (vis & ~np.logical_and.accumulate(vis, axis=1)).sum(axis=1)   # отсчётов на островах
    return radius_by_az(dst, vis) + isl * step


def island_share(dst, vis):
    """Доля площади круга под островами (видно за тенью). Вход: дальности, видимость (A, n). Отдаёт: доля 0…1."""
    isl = vis & ~np.logical_and.accumulate(vis, axis=1)   # видно, но после тени — остров
    w = dst / dst.sum()                                # вес кольца ∝ дальности
    return float((isl * w[None]).sum(axis=1).mean())


def ray(R_km, step_km):
    """Дальности отсчётов луча. Вход: R км, шаг км. Отдаёт: (n,) км."""
    return np.arange(1, int(round(R_km / step_km)) + 1) * step_km


def selftest():
    """Случаи с известным ответом: равнина, стена, сплошной лес, радиогоризонт, остров за тенью.
    Вход: нет. Отдаёт: число расхождений (печатает строку на случай)."""
    bad = 0   # расхождений
    cases = []                                         # (имя, получено км, ожидается км, допуск км)
    # 1) равнина без предметов, R 1 км: тени нет — r = R по всем направлениям
    d = ray(1.0, 0.015)
    # r по 72 направлениям, км
    r = radius_by_az(d, visibility(0.0, np.zeros((72, len(d))), np.zeros((72, len(d))), d, 0.0, 100.0))
    cases.append(("равнина, R 1 км, БПЛА 100 м: r = R, R_факт = R", float(r.min()), float(d[-1]), 1e-9))   # R — последний отсчёт
    # 2) стена 20 м на 0.3 км, глаз 0, БПЛА 100 м: тень с d·h_БПЛА/h = 1.5 км
    d = ray(3.0, 0.01)
    ob = np.zeros((1, len(d)))   # предметы по лучу, м
    ob[0, int(np.argmin(np.abs(d - 0.3)))] = 20.0
    r = radius_by_az(d, visibility(0.0, np.zeros_like(ob), ob, d, 0.0, 100.0))
    cases.append(("стена 20 м на 0.3 км, БПЛА 100 м: тень с 1.5 км", float(r[0]), 1.5, 0.03))
    # 3) лес 20 м сплошь от первого отсчёта (15 м), глаз 0: r = 0.015·100/20 = 0.075 км
    d = ray(1.0, 0.015)
    r = radius_by_az(d, visibility(0.0, np.zeros((1, len(d))), np.full((1, len(d)), 20.0), d, 0.0, 100.0))
    cases.append(("сплошной лес 20 м, глаз 0, БПЛА 100 м: r ≈ 0.075 км", float(r[0]), 0.075, 0.016))
    # 4) радиогоризонт на равнине: 4.12·(√hs + √h) — 41.2 км (глаз 0, БПЛА 100 м) и 61.8 км (антенна 25 м)
    for hs, want in ((0.0, 41.2), (25.0, 61.8)):
        d = ray(80.0, 0.1)
        r = radius_by_az(d, visibility(0.0, np.zeros((1, len(d))), np.zeros((1, len(d))), d, hs, 100.0))
        cases.append(("радиогоризонт, глаз %.0f м, БПЛА 100 м: %.1f км" % (hs, want), float(r[0]), want, 0.3))
    # 5) остров: стена 20 м на 0.3 км, за 3 км подъём 10 %: БПЛА снова виден за тенью — в r не входит
    d = ray(8.0, 0.05)
    g = np.maximum(d - 3.0, 0.0)[None] * 100.0         # земля поднимается 100 м на км, м
    ob = np.zeros((1, len(d)))
    ob[0, int(np.argmin(np.abs(d - 0.3)))] = 20.0
    vis = visibility(0.0, g, ob, d, 0.0, 100.0)   # видимость по лучу
    cases.append(("остров за тенью: r = 1.5 км, остров не входит", float(radius_by_az(d, vis)[0]), 1.5, 0.06))
    # 6) тот же луч с островами: снова виден с 6 км ((100·(D − 3) + 100)/D > 20/0.3) до конца луча 8 км — 1.5 + 2.0
    cases.append(("с островами: видимая длина 1.5 + 2.0 = 3.5 км", float(visible_by_az(d, vis)[0]), 3.5, 0.1))
    has_isl = island_share(d, vis) > 0                 # остров действительно есть
    for name, got, want, tol in cases:                 # печать и счёт
        ok = abs(got - want) <= tol
        bad += int(not ok)
        print("  %s %-62s получено %.3f · ожидается %.3f ± %.3f" % ("✅" if ok else "❌", name, got, want, tol))
    print("  %s остров в случае 5 найден (доля круга %.1f %%)" % ("✅" if has_isl else "❌", 100 * island_share(d, vis)))
    return bad + int(not has_isl)


def load_area():
    """Участок arh для расчёта по данным (как замер М13): модель, рельеф окна, предметы. Вход: нет.
    Отдаёт: словарь (модель, Z, H м; LEFT, TOP, RES градусы; PX, PY км; места МОГ км)."""
    import rasterio
    from rasterio.windows import from_bounds
    from PIL import Image, ImageDraw
    from config import Params
    from model.threat.geo_frame import KM_PER_DEG_LAT, km_per_deg_lon, km_to_lonlat
    from model.threat.threat_grid import ThreatModel
    from model.placement.sensors import sensor_types
    p = Params()   # параметры программы
    p.threat_N, p.threat_R = 10, 1.0                   # маршрутов мало — нужны только сетка и места
    m = ThreatModel(p)   # модель участка — сетка, кадр, места
    m.build()
    spec = [s for s in sensor_types(m.p) if s.type_id == 1][0]   # МОГ
    places = np.asarray(m._type_places(spec, m.allowed_mask(spec)), float)   # места МОГ, км
    x0, x1, y0, y1 = m.bbox_km   # рамка района, км
    # углы окна растра, градусы
    clo, cla = km_to_lonlat(np.array([x0 - 30.0, x1 + 30.0]), np.array([y0 - 30.0, y1 + 30.0]), m.lon0, m.lat0)
    bb = (float(clo[0]), float(cla[0]), float(clo[1]), float(cla[1]))   # окно: район + 30 км, градусы
    with rasterio.open(os.path.join(ROOT, "geo_cache", "arh", "arh_hh.tif")) as ds:
        win = from_bounds(*bb, transform=ds.transform).round_offsets().round_lengths()   # окно растра
        Z = ds.read(1, window=win).astype(np.float32)   # рельеф окна, м
        wt = ds.window_transform(win)   # привязка окна
    a = dict(m=m, Z=Z, LEFT=wt.c, TOP=wt.f, RES=wt.a, places=places,   # участок для расчёта
             PX=wt.a * km_per_deg_lon(0.5 * (bb[1] + bb[3])), PY=wt.a * KM_PER_DEG_LAT)
    nr, nc = Z.shape   # размер окна, пиксели

    def km_px(xy):
        """Км модели → (строка, столбец) окна. Вход: (P, 2) км. Отдаёт: (P, 2) дробные."""
        lon, lat = km_to_lonlat(xy[:, 0], xy[:, 1], m.lon0, m.lat0)   # градусы точек
        return np.c_[(a["TOP"] - lat) / a["RES"], (lon - a["LEFT"]) / a["RES"]]

    # слои участка: лес, постройки
    d = np.load(os.path.join(ROOT, "geo_cache", "arh", "sensor_layers_arh.npz"), allow_pickle=True)
    img = Image.new("1", (nc, nr), 0)   # маска леса, пиксели окна
    dr = ImageDraw.Draw(img)   # рисовалка маски
    fxy, foff = d["forest@xy"], d["forest@off"]   # кольца леса подряд и их начала
    for i in range(len(foff) - 1):                     # кольцо леса за кольцом
        rc = km_px(np.asarray(fxy[foff[i]:foff[i + 1]], float))   # кольцо в пикселях окна
        if len(rc) >= 3:   # многоугольник — закрасить
            dr.polygon(list(zip(rc[:, 1].tolist(), rc[:, 0].tolist())), fill=1)
    H = np.where(np.array(img, bool), H_FOREST_M, 0.0).astype(np.float32)   # предметы над землёй, м
    bp = d["building_pts@xy"]   # постройки: км, этажи, класс, площадь
    # этажей, класс, площадь м²
    lev, cls, area = bp[:, 2].astype(float), bp[:, 3].astype(float), bp[:, 4].astype(float)
    hgt = np.where(np.isfinite(lev), 3.0 * lev + 1.0, np.where(cls >= 2, 7.0, 4.0))   # высота постройки, м
    half = np.sqrt(np.maximum(area, 1.0)) / 2e3        # полусторона пятна, км
    for (r, c), hh, hf in zip(km_px(bp[:, :2].astype(float)), hgt, half):   # постройка — квадрат своей площади
        r0, r1 = int(np.floor(r - hf / a["PY"])), int(np.floor(r + hf / a["PY"]))   # строки пятна
        c0, c1 = int(np.floor(c - hf / a["PX"])), int(np.floor(c + hf / a["PX"]))   # столбцы пятна
        if r1 >= 0 and c1 >= 0 and r0 < nr and c0 < nc:   # пятно в окне
            sl = H[max(r0, 0):r1 + 1, max(c0, 0):c1 + 1]   # пятно в окне
            np.maximum(sl, hh, out=sl)
    a.update(H=H, km_px=km_px)
    return a


def place_radius(a, rc, R_km, step_km, hs, hu):
    """r(аз) и острова у места по данным участка. Вход: участок, (строка, столбец), R км, шаг км, высота глаза и БПЛА м.
    Отдаёт: (r до первой тени (72,) км, видимая длина с островами (72,) км, доля островов)."""
    Z, H, nr, nc = a["Z"], a["H"], a["Z"].shape[0], a["Z"].shape[1]   # рельеф, предметы, размер окна
    d = ray(R_km, step_km)   # дальности по лучу, км
    az = np.radians(AZ_DEG)   # азимуты, радианы
    # строки отсчётов
    ri = np.clip(np.floor(rc[0] - np.cos(az)[:, None] * d[None] / a["PY"]).astype(np.int64), 0, nr - 1)
    # столбцы отсчётов
    ci = np.clip(np.floor(rc[1] + np.sin(az)[:, None] * d[None] / a["PX"]).astype(np.int64), 0, nc - 1)
    vis = visibility(Z[int(rc[0]), int(rc[1])], Z[ri, ci], H[ri, ci], d, hs, hu)   # видимость по 72 лучам
    return radius_by_az(d, vis), visible_by_az(d, vis), island_share(d, vis)


def main(argv=None):
    """Разбор ключей и запуск. Вход: ключи командной строки. Отдаёт: код возврата."""
    # ключи командной строки
    ap = argparse.ArgumentParser(description="Проверка фактического радиуса обзора (план 16)")
    ap.add_argument("--mog", action="store_true", help="все места МОГ участка arh")
    ap.add_argument("--place", nargs=2, type=float, metavar=("LON", "LAT"), help="одно место, градусы")
    ap.add_argument("--R", type=float, default=1.0, help="радиус средства, км")
    ap.add_argument("--step", type=float, default=0.0, help="шаг луча, км (0 — R/67, не крупнее пикселя)")
    ap.add_argument("--hs", type=float, default=0.0, help="высота установки над землёй, м (по умолчанию 0)")
    ap.add_argument("--hu", type=float, default=100.0, help="высота БПЛА над землёй из ТТХ, м")
    ar = ap.parse_args(argv)   # разобранные ключи
    t0 = time.time()   # начало, с
    print("Самопроверка эталона (случаи с известным ответом):")
    bad = selftest()   # расхождений самопроверки
    if ar.mog or ar.place:                             # расчёт по данным участка
        a = load_area()   # участок arh
        step = ar.step or max(ar.R / 67.0, 0.015)      # шаг луча, км
        if ar.place:                                   # одно место
            from model.threat.geo_frame import lonlat_to_km
            # место в км модели
            x, y = lonlat_to_km(np.array([ar.place[0]]), np.array([ar.place[1]]), a["m"].lon0, a["m"].lat0)
            # r по направлениям и доля островов
            r, rv, isl = place_radius(a, a["km_px"](np.c_[x, y])[0], ar.R, step, ar.hs, ar.hu)
            print("место %.4f, %.4f · R %.1f км, глаз %.0f м, БПЛА %.0f м, шаг %.3f км: R_факт с островами %.2f км "
                  "(%.0f %% R), до первой тени %.2f км; наименьший r %.2f км (аз %.0f°); без тени %d из 72; острова %.1f %% "
                  "круга" % (ar.place[0], ar.place[1], ar.R, ar.hs, ar.hu, step, rv.mean(), 100 * rv.mean() / ar.R,
                            r.mean(), r.min(), AZ_DEG[int(np.argmin(r))], int((r >= ar.R - 1e-9).sum()), 100 * isl))
            print("  r по направлениям через 30°: " + " · ".join("%.0f° %.2f" % (AZ_DEG[i], r[i]) for i in range(0, 72, 6)))
        if ar.mog:                                     # все места МОГ
            rs, rvs, isls = [], [], []   # R_факт до первой тени, с островами и доли островов по местам
            for rc in a["km_px"](a["places"]):         # место за местом
                r, rv, isl = place_radius(a, rc, ar.R, step, ar.hs, ar.hu)
                rs.append(r.mean())
                rvs.append(rv.mean())
                isls.append(isl)
            rs, rvs, isls = np.array(rs), np.array(rvs), np.array(isls)
            print("МОГ, %d мест · R %.1f км, глаз %.0f м, БПЛА %.0f м: R_факт с островами — медиана %.2f км (10-й…90-й "
                  "перцентиль %.2f…%.2f), до первой тени — %.2f км; острова — медиана %.1f %%, наибольшее %.1f %% круга"
                  % (len(rs), ar.R, ar.hs, ar.hu, np.median(rvs), np.percentile(rvs, 10), np.percentile(rvs, 90),
                     np.median(rs), 100 * np.median(isls), 100 * isls.max()))
    print("%s · %.1f с" % ("ВСЁ СОШЛОСЬ" if bad == 0 else "РАСХОЖДЕНИЙ: %d" % bad, time.time() - t0))
    return 1 if bad else 0


if __name__ == "__main__":   # запуск скриптом, а не импортом
    sys.exit(main())
