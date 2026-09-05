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
    c = ThreatController(m, v)

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
    # ОГРОМНАЯ КАРТА (05.09.2026). Экспорт из CorelDRAW дал 237 млн пикселей, и Pillow
    # отказался открывать файл: «could be decompression bomb DOS attack». Проверяем обе
    # стороны решения: свой предел поднят выше штатного, а сверх него человек получает
    # понятный текст, а не системную ошибку.
    import view_qt.map_image as _mi
    from PIL import Image as _Im
    _small = os.path.join("geo_cache", "карты_картинки", "15.jpg")
    check(_mi.MAX_PIXELS_ALLOWED > 237219680,
          "свой предел выше того, что даёт CorelDRAW",
          "%.0f млн против 237" % (_mi.MAX_PIXELS_ALLOWED / 1e6))
    if os.path.exists(_small):
        keep = _mi.MAX_PIXELS_ALLOWED
        _mi.MAX_PIXELS_ALLOWED = 1000                       # заведомо ниже любой картинки
        try:
            _mi.load_image_rgba(_small)
            check(False, "предел сообщает человеку, что делать")
        except ValueError as e:
            check("Пересохраните" in str(e), "предел сообщает человеку, что делать",
                  str(e).split(".")[0])
        finally:
            _mi.MAX_PIXELS_ALLOWED = keep
        check((_Im.MAX_IMAGE_PIXELS or 0) >= keep, "штатный порог Pillow поднят",
              "%.0f млн" % ((_Im.MAX_IMAGE_PIXELS or 0) / 1e6))
        # ДЕТАЛИЗАЦИЯ ПО ВИДИМОЙ ОБЛАСТИ (05.09.2026): при приближении карта обязана
        # показываться в ПОЛНОМ разрешении, иначе подписи топокарты не читаются.
        # ⚠️ ИМЕНА ЗДЕСЬ С ПОДЧЁРКИВАНИЕМ НЕ ЗРЯ: короткие `m`, `n`, `d` заняты моделью и
        # прочим выше по функции, и обычное `n, m = ...` уже затёрло модель — раздел 6
        # падал с «'int' object has no attribute 'manual_sensors'».
        import numpy as _np
        _mi.build_detail(_small)
        _meta = _mi.read_detail_meta(_small)
        check(_meta is not None, "кэш детализации строится и опознаётся",
              "%d x %d" % (_meta["w"], _meta["h"]) if _meta else "")
        _rgba, _got = _mi.read_detail_window(_meta, (0.4, 0.4, 0.6, 0.6), max_px=4000)
        with _Im.open(_small) as _im:
            _W, _H = _im.size
            _ref = _np.asarray(_im.convert("RGB").crop(
                (int(0.4 * _W), int(0.4 * _H), int(0.6 * _W), int(0.6 * _H))))[::-1]
        _r = min(_ref.shape[0], _rgba.shape[0])
        _c = min(_ref.shape[1], _rgba.shape[1])
        _d = int(_np.abs(_ref[:_r, :_c].astype(int)
                         - _rgba[:_r, :_c, :3].astype(int)).max())
        check(_d == 0, "видимый кусок читается ПИКСЕЛЬ В ПИКСЕЛЬ", "расхождение %d" % _d)
        # карта показана -> вид приблизили -> кусок обязан подтянуться сам
        v._apply_map_image(_small, 39.87, 62.48, 42.15, 63.14)
        check(v._map_detail is not None, "показ подхватил готовый кэш")
        _keep_range = v.vb.viewRange()
        v.vb.setRange(xRange=(v._map_full_km[0] + 5, v._map_full_km[0] + 25), padding=0)
        v._refresh_map_detail()
        check(v._map_detail_box is not None, "при зуме подгружается свой кусок",
              "%.2f…%.2f по ширине" % (v._map_detail_box[0], v._map_detail_box[2])
              if v._map_detail_box else "")
        # ⚠️ ВИД ВЕРНУТЬ НА МЕСТО: следующий раздел кликает по карте и ждёт координаты
        # той точки, а не той, куда мы её увели проверкой.
        v.vb.setRange(xRange=_keep_range[0], yRange=_keep_range[1], padding=0)

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

    print("\n6. ОКНО «ИСХОДНЫЕ ДАННЫЕ» (задача 8.7)")
    v._open_input_window()
    w = v._input_dlg
    check(w is not None, "окно открывается")
    check(len(w._edits) >= 16, "поля четырёх типов и маршрута на месте",
          "%d полей" % len(w._edits))
    # кратность «одна на всех»: правка в типе 1 обязана появиться в типах 2–3
    w.rb_k_same.setChecked(True)
    w._edits["threat_k"].setText("5")
    w._k_edited("5")
    same = all(w._edits[n].text() == "5" for n in ("threat_k2", "threat_k3"))
    check(same, "кратность продублировалась во все типы",
          " · ".join(w._edits[n].text() for n in ("threat_k", "threat_k2", "threat_k3")))
    check(w._edits["threat_k2"].isReadOnly(), "чужие поля кратности закрыты для правки")
    w.rb_k_own.setChecked(True)
    check(not w._edits["threat_k2"].isReadOnly(), "«у каждого своя» открывает поля")
    w.rb_k_same.setChecked(True)
    w._edits["threat_k"].setText("3"); w._k_edited("3")

    # таблица датчиков: добавить, показать, удалить
    n0 = len(m.manual_sensors)
    lon, lat = m.km_to_lonlat_arr([[m.bbox_km[0] + 5, m.bbox_km[2] + 5]])[0]
    c.on_manual_add(1, True, float(lon), float(lat))
    c.on_manual_add(4, False, float(lon) + 0.05, float(lat) + 0.05)
    check(len(m.manual_sensors) == n0 + 2, "датчики добавились в модель",
          "%d шт." % len(m.manual_sensors))
    check(w.table.rowCount() == len(m.manual_sensors), "таблица показывает все строки",
          "%d строк" % w.table.rowCount())
    check(w.table.item(0, 2).text() == "статический", "режим виден в таблице",
          w.table.item(0, 2).text())
    c.on_manual_remove(0)
    check(len(m.manual_sensors) == n0 + 1, "удаление убрало ровно одну строку",
          "%d шт." % len(m.manual_sensors))

    # режим постановки мышью: интерфейс гаснет, живой остаётся одна кнопка выхода
    _live_before = sum(1 for w in v._panel_widgets() if w.isEnabled())
    v._begin_pick_sensor()
    check(v._pick_sensor, "режим постановки включается кнопкой")
    _live = [w for w in v._panel_widgets() if w.isEnabled()]
    check(len(_live) == 1 and _live[0] is v.btn_pick_sensor,
          "панель погашена, живёт только выход из режима",
          "активных %d" % len(_live))
    v._end_pick_sensor()
    check(not v._pick_sensor, "и выключается")
    check(sum(1 for w in v._panel_widgets() if w.isEnabled()) == _live_before,
          "после выхода панель вернулась как была",
          "было %d, стало %d" % (_live_before,
                                 sum(1 for w in v._panel_widgets() if w.isEnabled())))

    # ГАЛОЧКА «ДАТЧИКИ» прячет их, не теряя расстановки (заказчик 05.09.2026)
    c.on_manual_add(1, True, float(lon), float(lat))
    v.chk_sensors.setChecked(True)
    c._render_sensors()
    _with = len(v._sensor_items)
    v.chk_sensors.setChecked(False)
    c._render_sensors()
    check(_with > 0 and len(v._sensor_items) == 0, "галочка «Датчики» прячет их с карты",
          "было %d значков, стало %d" % (_with, len(v._sensor_items)))
    check(len(m.manual_sensors) > 0, "расстановка при этом цела",
          "заданных %d" % len(m.manual_sensors))
    v.chk_sensors.setChecked(True)
    c._render_sensors()
    check(len(v._sensor_items) == _with, "и возвращает обратно")

    # КНОПКА «СБРОС» убирает датчики ВСЕХ типов, включая большие (заказчик 05.09.2026)
    m.sensors_big = __import__("numpy").array([[m.bbox_km[0] + 9, m.bbox_km[2] + 9]], float)
    c.on_reset()
    check(len(m.sensors) == 0 and len(m.sensors_big) == 0
          and len(m.manual_sensors) == 0,
          "«Сброс» убирает малые, большие и заданные вручную",
          "малых %d, больших %d, ручных %d"
          % (len(m.sensors), len(m.sensors_big), len(m.manual_sensors)))

    # в режиме «Задать позиции» количество берётся из таблицы и не редактируется
    w.rb_manual.setChecked(True)
    _n1 = w._edits["threat_N"]
    check(_n1.isReadOnly(), "в режиме «задать позиции» количество закрыто для правки")
    check(_n1.text() == "%d" % sum(1 for r in w._rows if r["type_id"] == 1),
          "и подставлено по таблице", "N = %s" % _n1.text())
    check(not w._edits["threat_R"].isReadOnly(), "радиус остался редактируемым")
    w.rb_calc.setChecked(True)
    check(not _n1.isReadOnly(), "в режиме расчёта количество снова задаётся руками")

    # ⚠️ ДАТЧИКИ ЗАНОВО: проверка «Сброса» выше очистила всё, и файл записывался бы из
    # пустой таблицы — «принято 0 из 0» проходит, но ничего не доказывает.
    c.on_manual_add(1, True, float(lon), float(lat))
    c.on_manual_add(2, False, float(lon) + 0.03, float(lat) + 0.02)
    c.on_manual_add(4, True, float(lon) + 0.07, float(lat) - 0.04)

    # файл датчиков: запись → чтение, позиции обязаны совпасть до знака
    import tempfile
    from view_qt.input_window import write_sensors_file, read_sensors_file
    rows = c._sensor_rows()
    path = os.path.join(tempfile.gettempdir(), "uav_sensors_check.txt")
    write_sensors_file(path, rows, p)
    back, rep = read_sensors_file(path)
    check(len(back) == len(rows), "файл прочитан обратно целиком",
          "принято %d из %d" % (len(back), len(rows)))
    same_xy = all(abs(b[2] - r["lon"]) < 1e-6 and abs(b[3] - r["lat"]) < 1e-6
                  and b[0] == r["type_id"] and b[1] == bool(r["static"])
                  for b, r in zip(back, rows))
    check(same_xy and not rep["rejected"], "координаты, типы и режимы совпали")
    # строка, набранная руками, без номера и по-английски — тоже обязана читаться
    with open(path, "a", encoding="utf-8") as f:
        f.write("2 s 40.5 62.8\n3 d 40.6 62.9\nмусор в строке\n")
    back2, rep2 = read_sensors_file(path)
    check(len(back2) == len(back) + 2, "ручные строки без номера приняты",
          "принято %d" % rep2["accepted"])
    check(len(rep2["rejected"]) == 1, "мусорная строка отброшена, чтение не упало",
          rep2["rejected"][0][1] if rep2["rejected"] else "")
    os.remove(path)

    print("\n" + LINE)
    if _fails:
        print("НЕ ПРОШЛО: %d — %s" % (len(_fails), "; ".join(_fails)))
        return 1
    print("ВСЁ ПРОШЛО. Рамки, оси, панель, слои и окно исходных данных — в норме.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
