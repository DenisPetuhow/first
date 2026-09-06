# -*- coding: utf-8 -*-
"""ПОДБОР ПРИВЯЗКИ КАРТЫ-КАРТИНКИ по слоям OSM — из командной строки.

    python tools/map_fit.py                      # подобрать для карты из ui_state.json
    python tools/map_fit.py --image путь.png     # для другой картинки
    python tools/map_fit.py --save               # записать опорные точки в ui_state.json

ЗАЧЕМ. Координаты краёв карты вводят на глаз, и промах выходит километровым: на карте
заказчика (Плесецк) — медиана 3 660 м. Утилита находит на картинке населённые пункты по
их пятнам застройки из OSM и решает привязку по ним: 34 пункта, невязка 224 м.

Разбор метода — `model/map_fit.py`, математика привязки — `view_qt/map_anchor.py`.
"""
import argparse
import io
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                     # noqa: E402
from model import map_fit as mf                   # noqa: E402
from model.geo_frame import lonlat_to_km, km_to_lonlat  # noqa: E402
from model.threat_grid import geo_cache_root, layers_cache_path  # noqa: E402
from view_qt import map_image as mi               # только чтение файла, окон не создаёт


def main():
    ap = argparse.ArgumentParser(description="подбор привязки карты-картинки")
    ap.add_argument("--image", help="файл карты (по умолчанию — из ui_state.json)")
    ap.add_argument("--save", action="store_true",
                    help="записать опорные точки в geo_cache/ui_state.json")
    ap.add_argument("--find", action="store_true",
                    help="координат нет вовсе: искать карту по всему участку")
    ap.add_argument("--bbox", nargs=4, type=float,
                    metavar=("ЗАПАД", "ЮГ", "ВОСТОК", "СЕВЕР"),
                    help="начальная рамка в градусах вместо той, что в ui_state.json")
    args = ap.parse_args()

    state_path = os.path.join(geo_cache_root(), "ui_state.json")
    state = {}
    if os.path.exists(state_path):
        state = json.load(io.open(state_path, encoding="utf-8"))
    cur = state.get("map_image") or {}
    path = args.image or cur.get("path")
    if not path or not os.path.exists(path):
        raise SystemExit("не указана карта: --image путь.png")
    need = ("lon_min", "lat_min", "lon_max", "lat_max")
    if args.bbox:
        cur = dict(cur)
        cur["lon_min"], cur["lat_min"], cur["lon_max"], cur["lat_max"] = args.bbox
    if not args.find and not all(cur.get(k) is not None for k in need):
        raise SystemExit("в ui_state.json нет рамки карты — задайте её в окне «Своя карта» "
                         "либо запустите с --find (искать по всему участку)")

    print("карта:", os.path.basename(path))
    rgb, step, (W, H) = mi.read_for_fit(path)
    dark = mf.dark_mask(rgb)
    print("размер %d × %d, для поиска %d × %d (шаг %d), чёрного %.2f %%"
          % (W, H, rgb.shape[1], rgb.shape[0], step, 100.0 * dark.mean()))

    lon0, lat0 = config.THREAT_BBOX_LONLAT[0], config.THREAT_BBOX_LONLAT[1]
    places, built = mf.layers_for_fit(layers_cache_path())
    print("пунктов %d, точек застройки %d" % (len(places), len(built)))

    if args.find:
        # КООРДИНАТ НЕТ ВОВСЕ: перебираем положения по всему участку и выбираем то, где
        # на своих местах нашлось больше всего пунктов (см. locate_and_fit).
        lo_a, la_a, ho_a, ha_a = config.THREAT_BBOX_LONLAT
        wx, wy = lonlat_to_km(ho_a, ha_a, lo_a, la_a)
        print("ищу карту по участку %.0f × %.0f км…" % (float(wx), float(wy)))
        rep = mf.locate_and_fit(dark, step, places, built, (float(wx), float(wy)))
        A0 = rep["A"]
        print("  выбрано положение с %d согласными пунктами (грубая оценка масштаба "
              "%.1f м/пиксель)" % (rep["n_used"], rep.get("m_per_px_guess") or 0.0))
    else:
        # НАЧАЛЬНАЯ привязка — та, что задана сейчас: прямоугольник по четырём числам
        kx0, ky0 = lonlat_to_km(cur["lon_min"], cur["lat_min"], lon0, lat0)
        kx1, ky1 = lonlat_to_km(cur["lon_max"], cur["lat_max"], lon0, lat0)
        A0 = np.array([[(float(kx1) - float(kx0)) / W, 0.0, float(kx0)],
                       [0.0, -(float(ky1) - float(ky0)) / H, float(ky1)]], float)

    rep = mf.fit(dark, step, A0, places, built)
    A = rep["A"]
    print("\nНАЙДЕНО: совпадений %d, согласных %d" % (rep["n_found"], rep["n_used"]))
    print("модель: %s (выбрана проверкой на отложенной точке: %.0f м)"
          % (rep["model"], rep["cv_m"]))
    print("невязка: медиана %.0f м, максимум %.0f м" % (rep["median_m"], rep["max_m"]))
    print("охват опорных точек: %.0f %% ширины, %.0f %% высоты карты"
          % (100 * rep["cover_x"], 100 * rep["cover_y"]))

    # насколько было плохо ДО подбора — по тем же точкам
    P = np.asarray([p[:4] for p in rep["points"]], float)
    M = np.column_stack([P[:, 0], P[:, 1], np.ones(len(P))])
    e0 = np.hypot(M @ A0[0] - P[:, 2], M @ A0[1] - P[:, 3]) * 1000.0
    print("было с нынешней рамкой: медиана %.0f м, максимум %.0f м"
          % (np.median(e0), e0.max()))

    sx = float(np.hypot(A[0, 0], A[1, 0]))
    sy = float(np.hypot(A[0, 1], A[1, 1]))
    rot = np.degrees(np.arctan2(A[1, 0], A[0, 0]))
    rot_y = np.degrees(np.arctan2(-A[0, 1], -A[1, 1]))
    print("\nГЕОМЕТРИЯ КАРТЫ: %.2f м/пиксель по X, %.2f по Y; поворот %+.2f°, перекос %+.2f°"
          % (sx * 1000, sy * 1000, rot, rot - rot_y))
    print("размер по местности: %.2f × %.2f км" % (sx * W, sy * H))

    pts_deg = []
    for xpx, ypx, kx, ky in P:
        lon, lat = km_to_lonlat(kx, ky, lon0, lat0)
        pts_deg.append([float(xpx), float(ypx), float(lon), float(lat)])
    print("опорных точек: %d" % len(pts_deg))

    # ЧЕТЫРЕ ЧИСЛА ДЛЯ ОКНА «Своя карта». Это габариты подобранной привязки: если у карты
    # есть поворот, прямоугольник опишет её ЦЕЛИКОМ и сам по себе точной посадки не даст —
    # точность живёт в опорных точках. Но ввести их надо, они задают начальное положение.
    corners = [(0.0, 0.0), (W, 0.0), (0.0, H), (W, H)]
    kxs = [A[0, 0] * x + A[0, 1] * y + A[0, 2] for x, y in corners]
    kys = [A[1, 0] * x + A[1, 1] * y + A[1, 2] for x, y in corners]
    lo_c, la_c = km_to_lonlat(min(kxs), min(kys), lon0, lat0)
    ho_c, ha_c = km_to_lonlat(max(kxs), max(kys), lon0, lat0)
    print("\nКООРДИНАТЫ ДЛЯ ОКНА «Своя карта»:")
    print("  Долгота, ЗАПАДНЫЙ край   %.6f" % lo_c)
    print("  Долгота, ВОСТОЧНЫЙ край  %.6f" % ho_c)
    print("  Широта,  ЮЖНЫЙ край      %.6f" % la_c)
    print("  Широта,  СЕВЕРНЫЙ край   %.6f" % ha_c)

    if args.save:
        cur = dict(cur)
        cur["path"] = path
        cur["points"] = pts_deg
        cur["w"], cur["h"] = W, H
        cur["model"] = rep["model"]
        cur["lon_min"], cur["lat_min"] = float(lo_c), float(la_c)
        cur["lon_max"], cur["lat_max"] = float(ho_c), float(ha_c)
        state["map_image"] = cur
        with io.open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
        print("\nзаписано в", state_path)
        print("Откройте окно «Своя карта» и нажмите «Показать карту».")
    else:
        print("\n(ничего не записано — добавьте --save, чтобы применить)")


if __name__ == "__main__":
    main()
