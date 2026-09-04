# -*- coding: utf-8 -*-
"""
ПРОВЕРКА ИНТЕРФЕЙСА ВКЛАДКИ 3 БЕЗ ЭКРАНА: рамки, оси, блокировка панели, своя карта.

    python tools/gui_check.py

ЗАЧЕМ. `flow_check.py` проверяет модель, `reference_run.py` — числа расчёта, а вид карты
до сих пор проверялся только глазами заказчика. Между тем половина правок 03–04.09.2026
была именно про вид, и ошибки там ровно того же сорта, что в модели: рамка обещает район,
которого нет; галочка гаснет и не возвращается; слой лёг поверх результата расчёта.

⚠️ КАК ЭТО ВООБЩЕ ЗАПУСКАЕТСЯ БЕЗ ЭКРАНА. Qt ищет свои плагины по пути, который строит
сам, и **кириллица в имени пользователя** (`C:\\Users\\Денис`) ломает автоопределение:
получается «Could not find the Qt platform plugin». Путь задаётся готовой строкой от
Windows (`%APPDATA%`) и строго ДО импорта QtWidgets — приём взят из `check_env.py`,
где он был найден раньше.

ЧТО ЭТО НЕ ЗАМЕНЯЕТ. Скрипт видит состояние виджетов, а не картинку: он подтвердит, что
слой включён и лежит на своём Z, но не скажет, красиво ли это выглядит. Цвета, читаемость
и «не сливается ли» по-прежнему смотрят глазами.
"""
import os
import sys

# --- пути к плагинам Qt: строго ДО импорта QtWidgets (см. шапку) ---
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


def check(ok, what, detail=""):
    print("   %s %-44s %s" % ("[ ok ]" if ok else "[ НЕТ]", what, detail))
    if not ok:
        _fails.append(what)
    return ok


def main():
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QPointF, Qt
    from config import Params, THREAT_BBOX_LONLAT
    from model.threat_grid import ThreatModel
    from model.geo_frame import bbox_lonlat_to_km
    from view_qt.threat_view import ThreatMapView
    from controller_qt.threat_controller import ThreatController

    app = QApplication([])                      # noqa: F841 — держит Qt живым
    p = Params()
    m = ThreatModel(p)
    v = ThreatMapView(m.bbox_km, m.lon0, m.lat0, p,
                      area_max_km=bbox_lonlat_to_km(THREAT_BBOX_LONLAT, m.lon0, m.lat0))
    ThreatController(m, v)

    print(LINE)
    print("ПРОВЕРКА ИНТЕРФЕЙСА · район при старте: %s" %
          ("задан" if m.area_ready else "не задан"))
    print(LINE)

    print("1. РАМКИ")
    check(v.area_max_item.isVisible(), "чёрная рамка области видна всегда")
    check(v.bbox_item.isVisible() == m.area_ready,
          "синяя рамка показана ровно когда район задан")

    print("\n2. ОСИ (ноль в углу района или области)")
    if m.area_ready:
        check(v.chk_axes_area.isChecked(), "галочка включилась вместе с районом")
        at_area = v._axis_x.tickStrings([m.bbox_km[0], m.bbox_km[0] + 20], 1, 10.0)
        check(at_area and at_area[0] == "0", "ноль осей в углу района", str(at_area))
        v.chk_axes_area.setChecked(False)
        v._apply_axis_origin()
        at_all = v._axis_x.tickStrings([m.bbox_km[0]], 1, 10.0)
        check(v._axis_x.origin == 0.0, "снятая галочка вернула ноль в угол области",
              str(at_all))
        v.chk_axes_area.setChecked(True)
        v._apply_axis_origin()

    print("\n3. БЛОКИРОВКА ПАНЕЛИ, пока район не задан")
    v.set_area_ready(False)
    locked = [w for w in v._panel_widgets() if not w.isEnabled()]
    free = [b.text() for b in (v.btn_area, v.btn_map_img) if b.isEnabled()]
    check(len(locked) > 10, "панель погашена", "%d элементов" % len(locked))
    check(len(free) == 2, "активны ровно две кнопки", " · ".join(free))
    check(not v.bbox_item.isVisible(), "синяя рамка убрана вместе с районом")
    v.set_area_ready(True, m.area_size_km(), m.bbox_km)
    check(len([w for w in v._panel_widgets() if not w.isEnabled()]) < 5,
          "после задания района панель ожила")

    print("\n4. СВОЯ КАРТА КАРТИНКОЙ")
    check(v.map_img_item.zValue() < -9.0, "слой лежит ПОД всеми слоями карты",
          "Z = %g" % v.map_img_item.zValue())
    img = os.path.join("geo_cache", "карты_картинки", "15.jpg")
    if os.path.exists(img):
        msg = v._apply_map_image(img, 39.87, 62.48, 42.15, 63.14)
        check(v.map_img_item.isVisible(), "картинка показана", msg.split(".")[0])
    else:
        print("   картинки нет — раздел пропущен (%s)" % img)

    print("\n5. КООРДИНАТЫ ПО КЛИКУ")

    class _Click:                                # имитация клика по карте
        def scenePos(self):
            return v.vb.mapViewToScene(QPointF(m.bbox_km[0] + 10, m.bbox_km[2] + 10))

        def button(self):
            return Qt.LeftButton

    v.lbl_coords.setText("")
    v._show_click_coords(_Click())
    txt = v.lbl_coords.text()
    check("°" in txt and "шир" in txt, "показаны градусы, Г/М/С, радианы и км")
    print("      %s" % txt[:100])

    print("\n" + LINE)
    if _fails:
        print("НЕ ПРОШЛО: %d — %s" % (len(_fails), "; ".join(_fails)))
        return 1
    print("ВСЁ ПРОШЛО. Рамки, оси, блокировка панели и слои — в норме.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
