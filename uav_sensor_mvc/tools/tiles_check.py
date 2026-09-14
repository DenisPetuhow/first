# -*- coding: utf-8 -*-
"""
ПРОВЕРКА ПОКАЗА ТАЙЛОВ: совпадают ли подложка и векторные слои, живёт ли слой тайлов.

    python tools/tiles_check.py

ЗАЧЕМ ОТДЕЛЬНАЯ ПРОВЕРКА. `gui_check.py` смотрит виджеты вкладки — рамки, галочки, слои;
`reference_run.py` считает числа модели. Ни тот, ни другой не видят ГЛАВНОГО обещания
задачи 10.10: что тайл лежит ровно там же, где рисует ту же реку векторный слой. Ошибка
здесь не роняет программу и не меняет ни одного числа расчёта — карта просто чуть-чуть
врёт, и заметить это глазами нельзя: речь о единицах пикселей.

⚠️ ЭТА ПРОВЕРКА УЖЕ ПОЙМАЛА ДЕФЕКТ. Первый вариант `tile_layer.py` отбрасывал доехавший
тайл, если вид успел сдвинуться, и заново его никто не заказывал — подложка оставалась
пустой до следующего движения мыши. На сцену легло 0 тайлов вместо 48.

КАК ЗАПУСКАЕТСЯ БЕЗ ЭКРАНА. Кириллица в `C:\\Users\\Денис` ломает автоопределение плагинов
Qt, поэтому путь задаётся готовой строкой от Windows (`%APPDATA%`) строго ДО импорта
QtWidgets — приём взят из `tools/gui_check.py`.
"""
import math
import os
import sys

_PLUGINS = os.path.join(os.environ.get("APPDATA", ""), "Python", "Python314",
                        "site-packages", "PyQt5", "Qt5", "plugins")
if os.path.isdir(_PLUGINS):
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", os.path.join(_PLUGINS, "platforms"))
    os.environ.setdefault("QT_PLUGIN_PATH", _PLUGINS)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LINE = "=" * 68
_fails = []

# ПОРОГ СОВПАДЕНИЯ: расхождение подложки и слоёв должно быть меньше ПИКСЕЛЯ экрана.
# Не в метрах: в метрах «много» и «мало» зависят от масштаба, а человек смотрит в
# пиксели. Мозаикой было 11.2 px — линия слоя отходила от реки на свою толщину.
MAX_SHIFT_PX = 1.0
WIN_W, WIN_H = 1560, 960          # окно замера; от него зависит выбранный зум


def check(ok, what, detail=""):
    print("   %s %-46s %s" % ("[ ok ]" if ok else "[ НЕТ]", what, detail))
    if not ok:
        _fails.append(what)
    return ok


def _merc_lat_of_row(lon, lat, z):
    """Куда попадёт точка (lon, lat), если её тайл положен СВОИМ прямоугольником.

    Принимает: координаты точки (град) и зум. Отдаёт: километр по Y на сцене."""
    from view_qt import geomap as gm
    xt, yt = gm.deg2num(lon, lat, z)
    tx, ty = int(math.floor(xt)), int(math.floor(yt))
    _ex0, _ex1, ey0, ey1 = gm.tile_bounds_km(z, tx, ty, _LON0, _LAT0)
    return ey1 - (yt - ty) * (ey1 - ey0)          # yt растёт на юг, ey1 — верхний край


def _mosaic_y(lon, lat, z, view):
    """То же, но при СКЛЕЙКЕ окна в одну картинку — как было до задачи 10.10."""
    from view_qt import geomap as gm
    tx0, tx1, ty0, ty1 = gm.tiles_for_box(*view, z=z, lon0=_LON0, lat0=_LAT0)
    ny = ty1 - ty0 + 1
    tl_lon, tl_lat = gm.num2deg(tx0, ty0, z)
    br_lon, br_lat = gm.num2deg(tx1 + 1, ty1 + 1, z)
    _x, ey1 = gm.lonlat_to_km(tl_lon, tl_lat, _LON0, _LAT0)
    _x2, ey0 = gm.lonlat_to_km(br_lon, br_lat, _LON0, _LAT0)
    _xt, yt = gm.deg2num(lon, lat, z)
    return float(ey1) - (yt - ty0) / ny * float(ey1 - ey0)


def main():
    global _LON0, _LAT0
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QThreadPool, QTime, QEventLoop
    from config import Params, THREAT_AREAS, THREAT_AREA
    from model.geo_frame import bbox_lonlat_to_km
    from view_qt import geomap as gm
    from view_qt.threat_view import ThreatMapView
    from view_qt.basemap_mixin import TILES_MOSAIC

    area_cfg = THREAT_AREAS[THREAT_AREA]
    lo, la, ho, ha = area_cfg["bbox"]
    _LON0, _LAT0 = lo, la
    area_km = bbox_lonlat_to_km(area_cfg["bbox"], lo, la)
    work_km = bbox_lonlat_to_km(area_cfg.get("work") or area_cfg["bbox"], lo, la)

    print(LINE)
    print("ПОКАЗ ТАЙЛОВ · участок %s (%.0f × %.0f км)"
          % (THREAT_AREA, area_km[1] - area_km[0], area_km[3] - area_km[2]))
    print(LINE)

    app = QApplication.instance() or QApplication([])
    v = ThreatMapView(work_km, lo, la, Params(), area_max_km=area_km)
    v.resize(WIN_W, WIN_H)
    v.show()
    app.processEvents()
    tiles = getattr(v, "_tiles", None)

    print("\n1. СЛОЙ ТАЙЛОВ ЖИВ")
    if TILES_MOSAIC:
        print("   ⚠️ UAV_TILES_MOSAIC=1 — включён СТАРЫЙ путь (одна картинка).")
        print("   Проверка совпадения пропущена: она про поштучные тайлы.")
        return 0
    check(tiles is not None, "слой поштучных тайлов заведён")
    if tiles is None:
        return 1

    def settle(ms=8000):
        # ⚠️ Ждём по ОЧЕРЕДИ ЗАКАЗОВ, а не по activeThreadCount: сразу после start()
        # пул ещё не подхватил задачи, счётчик равен нулю — ожидание кончалось, не
        # начавшись, и проверка видела пустую сцену.
        t = QTime.currentTime()
        while t.msecsTo(QTime.currentTime()) < ms:
            app.processEvents(QEventLoop.AllEvents, 50)
            if not tiles._pending and QThreadPool.globalInstance().activeThreadCount() == 0:
                app.processEvents(QEventLoop.AllEvents, 50)
                if not tiles._pending:
                    break

    v._refresh_basemap(force=True)
    settle()
    n1 = len(tiles._items)
    z = tiles._cur_z
    check(n1 > 0, "тайлы легли на сцену", "элементов: %d, зум %s" % (n1, z))
    if n1 == 0:
        print("   (кэш тайлов пуст? проверьте tile_cache/ — без тайлов замер невозможен)")
        return 1

    print("\n2. КАЖДЫЙ ТАЙЛ — В СВОИХ КИЛОМЕТРАХ")
    bad = 0
    for (tz, tx, ty), it in tiles._items.items():
        ex0, ex1, _ey0, _ey1 = gm.tile_bounds_km(tz, tx, ty, lo, la)
        got = it.mapRectToView(it.boundingRect())
        if abs(got.left() - ex0) > 1e-6 or abs(got.width() - (ex1 - ex0)) > 1e-6:
            bad += 1
    check(bad == 0, "прямоугольник тайла = его границы в км",
          "проверено %d, расходится %d" % (n1, bad))

    print("\n3. ⭐ ПОДЛОЖКА СОВПАДАЕТ С ВЕКТОРНЫМИ СЛОЯМИ")
    (vx0, vx1), (vy0, vy1) = v.vb.viewRange()
    km_per_px = (vy1 - vy0) / float(WIN_H)
    view = (area_km[0], area_km[1], area_km[2], area_km[3])
    worst_t = worst_m = 0.0
    for i in range(41):                                    # точки по всей высоте участка
        lat = la + (ha - la) * i / 40.0
        lon = (lo + ho) / 2.0
        _kx, ky = gm.lonlat_to_km(lon, lat, lo, la)        # ВЕКТОРНЫЙ слой — истина
        worst_t = max(worst_t, abs(_merc_lat_of_row(lon, lat, z) - float(ky)))
        worst_m = max(worst_m, abs(_mosaic_y(lon, lat, z, view) - float(ky)))
    px_t, px_m = worst_t / km_per_px, worst_m / km_per_px
    print("      было (одной картинкой): %7.0f м = %5.2f px" % (worst_m * 1000, px_m))
    print("      стало (поштучно):       %7.1f м = %5.2f px" % (worst_t * 1000, px_t))
    check(px_t < MAX_SHIFT_PX, "расхождение меньше пикселя экрана",
          "%.2f px при пороге %.1f" % (px_t, MAX_SHIFT_PX))
    check(px_t < px_m, "поштучно точнее, чем одной картинкой",
          "в %.0f раз" % (px_m / px_t if px_t else 0))

    print("\n4. ПАНОРАМА НЕ ПЛОДИТ ЭЛЕМЕНТЫ")
    dx = (vx1 - vx0) * 0.25
    v.vb.setRange(xRange=(vx0 + dx, vx1 + dx), padding=0.0)
    v._refresh_basemap(force=True)
    settle()
    from view_qt.tile_layer import MAX_ITEMS
    check(len(tiles._items) <= MAX_ITEMS, "элементов не больше предела",
          "%d при пределе %d" % (len(tiles._items), MAX_ITEMS))
    check(len(tiles._free) > 0 or len(tiles._items) >= n1,
          "элементы переиспользуются, а не создаются заново",
          "в пуле свободных: %d" % len(tiles._free))

    print("\n5. СМЕНА ЗУМА НЕ ОСТАВЛЯЕТ ПУСТОТЫ")
    v.vb.setRange(xRange=(vx0 + dx, vx0 + dx + (vx1 - vx0) * 0.3), padding=0.0)
    v._refresh_basemap(force=True)
    settle()
    zs = sorted({k[0] for k in tiles._items})
    check(len(tiles._items) > 0, "после смены зума подложка на месте",
          "зумы на сцене: %s" % zs)

    print("\n6. ПОДЛОЖКУ МОЖНО ВЫКЛЮЧИТЬ И ВЕРНУТЬ")
    tiles.set_visible(False)
    check(not any(it.isVisible() for it in tiles._items.values()), "все тайлы спрятаны")
    tiles.set_visible(True)
    check(all(it.isVisible() for it in tiles._items.values()), "и вернулись обратно")
    tiles.clear()
    check(len(tiles._items) == 0, "clear() убирает всё со сцены")

    print("\n7. ВПИСЫВАНИЕ ОБЛАСТИ НЕ СЪЕДАЕТСЯ ПРИВЯЗКОЙ К ЗУМУ")
    # ⚠️ Поймано 13.09.2026: привязка к уровню тайлов СУЖАЛА вписанный вид (302 км -> 165),
    # и чёрная рамка при запуске обрезалась. Разделы 1–6 этого не видят: там вид ставится
    # руками, а не через `frame_bbox`.
    v.frame_bbox(whole_area=True)
    app.processEvents()
    (fx0, fx1), (fy0, fy1) = v.vb.viewRange()
    ax0, ax1, ay0, ay1 = area_km
    fits = fx0 <= ax0 + 1e-6 and fx1 >= ax1 - 1e-6 and fy0 <= ay0 + 1e-6 and fy1 >= ay1 - 1e-6
    check(fits, "вся область в кадре после вписывания",
          "кадр %.0f × %.0f км, область %.0f × %.0f" % (fx1 - fx0, fy1 - fy0, ax1 - ax0, ay1 - ay0))
    from view_qt.basemap_mixin import ZOOM_SNAP
    if ZOOM_SNAP:                                          # привязка включена — масштаб на уровне
        kpd = gm._km_per_deg_lon(la)
        levels = [(max(v.plot.width(), 256) / 256.0) / (2.0 ** zz) * 360.0 * kpd
                  for zz in range(gm.ZOOM_MIN, gm.ZOOM_MAX + 1)]
        near = min(abs((fx1 - fx0) / L - 1.0) for L in levels)
        # ⚠️ Не «ровно»: уровень бывает чуть шире ПРЕДЕЛА вида (область + поле 4 %), и кадр
        # упирается в предел. На arh 331 км против 326: подложка растянута на 2.7 %, область
        # при этом цела. Порог 3 % — ровно этот зазор, а не допуск «на всякий случай».
        check(near < 0.03, "ширина кадра — уровень тайлов с точностью до предела вида",
              "отклонение %.1f %% (порог 3)" % (near * 100))
        check((fx1 - fx0) <= 2.2 * (ax1 - ax0), "кадр не шире области больше чем вдвое",
              "%.0f км при области %.0f" % (fx1 - fx0, ax1 - ax0))

    v.close()
    print("\n" + LINE)
    if _fails:
        print("НЕ ПРОШЛО (%d): %s" % (len(_fails), "; ".join(_fails)))
        return 1
    print("ВСЁ ПРОШЛО. Подложка совпадает со слоями, элементы переиспользуются.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
