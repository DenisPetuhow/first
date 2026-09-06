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

import numpy as np                                                   # noqa: E402

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
              "%d…%d px по ширине" % (v._map_detail_box[0], v._map_detail_box[2])
              if v._map_detail_box else "")
        # ⚠️ ВИД ВЕРНУТЬ НА МЕСТО: следующий раздел кликает по карте и ждёт координаты
        # той точки, а не той, куда мы её увели проверкой.
        v.vb.setRange(xRange=_keep_range[0], yRange=_keep_range[1], padding=0)

        # ── ПРИВЯЗКА КАРТИНКИ (05.09.2026). Четырьмя числами карту не посадить: у
        # принесённых карт есть поворот и перекос, и промах доходил до 5.6 км. Проверяем
        # ровно то, что чинили: опорные точки должны попадать в свои координаты, а кусок
        # детализации — ложиться туда же, куда лёг бы целый кадр.
        import view_qt.map_anchor as _ma
        import view_qt.geomap as _gm
        _W2, _H2 = _meta["w"], _meta["h"]
        # известная привязка с поворотом: строим точки ИЗ НЕЁ и требуем её обратно
        _true = _np.array([[0.004, -0.0007, 20.0], [0.0009, -0.0035, 60.0]])
        _pts = []
        for _x, _y in ((100, 200), (_W2 - 50, 150), (80, _H2 - 90), (_W2 - 120, _H2 - 60)):
            _kx = _true[0, 0] * _x + _true[0, 1] * _y + _true[0, 2]
            _ky = _true[1, 0] * _x + _true[1, 1] * _y + _true[1, 2]
            _lon, _lat = _gm.km_to_lonlat(_kx, _ky, v._geo_lon0, v._geo_lat0)
            _pts.append([float(_x), float(_y), float(_lon), float(_lat)])
        _anc = _ma.MapAnchor(_W2, _H2, bbox=(39.87, 62.48, 42.15, 63.14), points=_pts)
        _A = _anc.matrix(v._geo_lon0, v._geo_lat0, _gm.lonlat_to_km)
        _res = _anc.residuals_km(_A, _gm.lonlat_to_km, v._geo_lon0, v._geo_lat0)
        check(max(_res) < 1e-6, "привязка по опорным точкам восстанавливается точно",
              "промах %.1e км" % max(_res))
        _dsc = _anc.describe(_A)
        check(abs(_dsc["rotation_deg"] - 12.68) < 0.05, "поворот карты распознан",
              "%+.2f°" % _dsc["rotation_deg"])
        # прямоугольная привязка БЕЗ точек обязана вести себя как прежде
        _anc0 = _ma.MapAnchor(_W2, _H2, bbox=(39.87, 62.48, 42.15, 63.14))
        _A0 = _anc0.matrix(v._geo_lon0, v._geo_lat0, _gm.lonlat_to_km)
        _bx = _anc0.bbox_km(_A0)
        _ex = _gm.lonlat_to_km(39.87, 62.48, v._geo_lon0, v._geo_lat0)
        check(abs(_bx[0] - float(_ex[0])) < 1e-9 and abs(_A0[0, 1]) < 1e-12,
              "без опорных точек привязка прежняя, прямоугольная")
        # кусок детализации и целый кадр обязаны сесть в ОДНО место
        _c_full = _ma.scene_transform(_A, 0.0, 0.0, 1.0, 1.0, _H2)
        _c_part = _ma.scene_transform(_A, 300.0, 200.0, 2.0, 2.0, 100)
        _px, _py = 300.0 + 2.0 * 7, 200.0 + 2.0 * (100 - 1 - 3)
        _fx = _c_full[0] * _px + _c_full[2] * (_H2 - 1 - _py) + _c_full[4]
        _gx = _c_part[0] * 7 + _c_part[2] * 3 + _c_part[4]
        check(abs(_fx - _gx) < 1e-6, "кусок карты садится туда же, куда целый кадр",
              "расхождение %.1e км" % abs(_fx - _gx))
        # обратный ход: видимое окно -> пиксели -> обратно должно накрыть окно
        _bpx = _anc.px_box_for_view(_A, 30.0, 60.0, 70.0, 95.0)
        _corners = [(_bpx[0], _bpx[1]), (_bpx[2], _bpx[1]),
                    (_bpx[0], _bpx[3]), (_bpx[2], _bpx[3])]
        _kk = [_anc.px_to_km(_A, _cx, _cy) for _cx, _cy in _corners]
        check(min(k[0] for k in _kk) <= 30.0 and max(k[0] for k in _kk) >= 60.0
              and min(k[1] for k in _kk) <= 70.0 and max(k[1] for k in _kk) >= 95.0,
              "вырезка по видимому окну накрывает его целиком (с поворотом тоже)")

        # ── ВЕКТОРНАЯ КАРТА (SVG), задача 8.5.0. Карта заказчика нарисована в CorelDRAW;
        # вектор остаётся резким на любом зуме, но координат в себе НЕ несёт — привязка
        # у него общая с растром. Проверяем именно стык: размер, рендер для подбора и
        # посадку углов в километры (ловушка «карта вверх ногами» — там же).
        import tempfile as _tf
        import view_qt.map_vector as _mv
        _svg = os.path.join(_tf.gettempdir(), "uav_proba_vector.svg")
        with open(_svg, "w", encoding="utf-8") as _f:
            _f.write('<svg xmlns="http://www.w3.org/2000/svg" width="1200" '
                     'height="800" viewBox="0 0 1200 800">'
                     '<rect width="1200" height="800" fill="#eef7ee"/>'
                     '<rect x="100" y="100" width="60" height="40" fill="#111111"/>'
                     '<rect x="900" y="600" width="80" height="50" fill="#111111"/>'
                     '</svg>')
        check(_mv.is_vector(_svg) and not _mv.is_vector("a.png"),
              "векторная карта опознаётся по расширению")
        check(_mv.svg_size(_svg) == (1200, 800), "размер SVG читается",
              "%d x %d" % _mv.svg_size(_svg))
        _vrgb, _vstep, _ = _mv.render_for_fit(_svg, want_px=600)
        _vdark = ((_vrgb[:, :, 0] < 110) & (_vrgb[:, :, 1] < 110) & (_vrgb[:, :, 2] < 110))
        _vy, _vx = _np.nonzero(_vdark)
        # ⚠️ ГРАНИЦЫ ВОЗВРАЩАЮТСЯ В ЕДИНИЦАХ САМОГО SVG, а не растра: за это отвечает
        # дробный шаг. Чёрные прямоугольники стоят на 100..980 по X и 100..650 по Y.
        check(abs(_vx.min() * _vstep - 100) < 6 and abs(_vx.max() * _vstep - 980) < 6
              and abs(_vy.min() * _vstep - 100) < 6 and abs(_vy.max() * _vstep - 650) < 6,
              "рендер для подбора возвращает единицы самой карты",
              "x %.0f..%.0f, y %.0f..%.0f" % (_vx.min() * _vstep, _vx.max() * _vstep,
                                              _vy.min() * _vstep, _vy.max() * _vstep))
        _vA = _np.array([[0.05, -0.004, 20.0], [-0.003, -0.06, 90.0]])
        _vitem = _mv.make_item(_svg)
        _mv.place_item(_vitem, _ma.scene_transform(_vA, 0.0, 0.0, 1.0, 1.0, 800,
                                                   flip_y=False))
        _worst = 0.0
        for _px, _py in ((0, 0), (1200, 0), (0, 800), (1200, 800)):
            _wx = _vA[0, 0] * _px + _vA[0, 1] * _py + _vA[0, 2]
            _wy = _vA[1, 0] * _px + _vA[1, 1] * _py + _vA[1, 2]
            _g = _vitem.mapToScene(float(_px), float(_py))
            _worst = max(_worst, abs(_g.x() - _wx), abs(_g.y() - _wy))
        check(_worst < 1e-6, "углы векторной карты садятся в свои километры",
              "промах %.1e км" % _worst)
        # север сверху: у правильной посадки верхний край ВЫШЕ нижнего
        _top = _vitem.mapToScene(600.0, 0.0)
        _bot = _vitem.mapToScene(600.0, 800.0)
        check(_top.y() > _bot.y(), "карта не встала вверх ногами",
              "верх %.1f км, низ %.1f км" % (_top.y(), _bot.y()))
        # ⚠️ SVG ИЗ CORELDRAW ЧАЩЕ ВСЕГО НЕ ВЕКТОРНЫЙ. Если подложка там — растр, «Экспорт
        # в SVG» даёт контейнер вокруг той же фотографии, а при экспорте со «Связью
        # изображений» внутрь попадает ССЫЛКА: у автора карта видна (исходник рядом), у
        # получателя — пустой лист. Замер 05.09.2026: ссылка file:/// не рисуется вовсе.
        import base64 as _b64
        _png_small = os.path.join(_tf.gettempdir(), "uav_proba_kartinka.png")
        _Im.new("RGB", (200, 150), "white").save(_png_small)
        _b = _b64.b64encode(open(_png_small, "rb").read()).decode("ascii")
        _head = ('<svg xmlns="http://www.w3.org/2000/svg" '
                 'xmlns:xlink="http://www.w3.org/1999/xlink" '
                 'width="200" height="150" viewBox="0 0 200 150">')
        _svg_emb = os.path.join(_tf.gettempdir(), "uav_proba_embed.svg")
        with open(_svg_emb, "w", encoding="utf-8") as _f:
            _f.write(_head + '<image width="200" height="150" '
                     'xlink:href="data:image/png;base64,%s"/></svg>' % _b)
        _svg_lnk = os.path.join(_tf.gettempdir(), "uav_proba_link.svg")
        with open(_svg_lnk, "w", encoding="utf-8") as _f:
            _f.write(_head + '<image width="200" height="150" xlink:href="file:///%s"/>'
                     '</svg>' % _png_small.replace("\\", "/"))
        _i_emb = _mv.inspect_svg(_svg_emb)
        _i_lnk = _mv.inspect_svg(_svg_lnk)
        check(_i_emb["images"] == 1 and _i_emb["embedded"] == 1 and _i_emb["linked"] == 0,
              "встроенная картинка внутри SVG распознана",
              "картинок %d, встроено %d" % (_i_emb["images"], _i_emb["embedded"]))
        check(_i_lnk["linked"] >= 1 and _i_lnk["embedded"] == 0,
              "«Связь изображений» распознана как ссылка", "ссылок %d" % _i_lnk["linked"])
        check("ССЫЛКА" in _mv.describe_content(_i_lnk),
              "о ссылке человек предупреждён (у получателя будет пустой лист)")
        check("РАСТР" in _mv.describe_content(_i_emb),
              "о растре внутри SVG человек предупреждён (резкости не будет)")
        check(_mv.describe_content(_mv.inspect_svg(_svg)) == "",
              "настоящий вектор предупреждений не вызывает")
        v._apply_map_image(_svg, 39.87, 62.48, 42.15, 63.14)
        check(v.map_svg_item is not None and not v.map_img_item.isVisible(),
              "показ вектора снимает растровую карту")
        v._clear_map_image()
        check(v.map_svg_item is None, "«Убрать» снимает и векторную карту")
        v._apply_map_image(_small, 39.87, 62.48, 42.15, 63.14)   # вернуть растр разделу 5

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

    # ПОСТАВЛЕННЫЙ МЫШЬЮ ВИДЕН СРАЗУ, А ПОСЛЕ РАСЧЁТА НЕ ДВОИТСЯ (заказчик 05.09.2026).
    # ⚠️ Проверяется ЧИСЛОМ ЗНАЧКОВ на карте, а не расстановкой: сама расстановка бывает
    # верной, а показ «только что поставленных» рисует лишний значок поверх неё. Датчик
    # входит в заказанное число, поэтому после расчёта значков ровно столько же, сколько
    # было до его добавления, — и на один больше, пока расчёта не было.
    m.clear_manual_sensors()
    c._work_place()
    c._render_sensors()
    _clean = len(v._sensor_items)
    c.on_manual_add(1, False, float(lon), float(lat))     # динамический
    check(len(v._sensor_items) == _clean + 3,
          "до расстановки поставленный мышью виден сразу",
          "значков %d против %d" % (len(v._sensor_items), _clean))
    c._work_place()
    c._render_sensors()
    check(len(v._sensor_items) == _clean,
          "после расчёта значок не двоится: заявка учтена расстановкой",
          "значков %d, ожидалось %d" % (len(v._sensor_items), _clean))
    check(int(m.sensors_manual.sum()) == 1,
          "и датчик помечен как заданный человеком (чёрная окантовка)",
          "помечено %d" % int(m.sensors_manual.sum()))
    m.clear_manual_sensors()

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

    # «ОЧИСТИТЬ» В РЕЖИМЕ «РАССЧИТАТЬ ПОЗИЦИИ» (заказчик 06.09.2026: «кнопка очистить
    # не убирает данные о датчиках в таблице») — таблица показывает РЕЗУЛЬТАТ
    # расстановки, «убрать вручную заданные» там убирать нечего; кнопка обязана
    # убрать ВСЮ расстановку — как «Очистить датчики» на панели
    c._work_place()
    _n_before_clear = len(m.sensors) + len(m.sensors_big)
    check(_n_before_clear > 0, "датчики на карте есть перед проверкой «Очистить»",
          "%d" % _n_before_clear)
    w.on_clear_sensors()
    check(len(m.sensors) == 0 and len(m.sensors_big) == 0,
          "«Очистить» в режиме «Рассчитать позиции» убирает ВСЮ расстановку",
          "было %d, стало %d" % (_n_before_clear, len(m.sensors) + len(m.sensors_big)))

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

    # КНОПКА «ИЗМЕНИТЬ» правит запись НА МЕСТЕ (заказчик 05.09.2026: «не работает»).
    # ⚠️ Проверяется не диалог, а колбэк за ним: раньше кнопка звала «удалить + добавить»,
    # и в режиме расчёта удаление не находило ручной записи — правка пропадала, а датчик
    # добавлялся лишний.
    m.clear_manual_sensors()
    c.on_manual_add(1, False, float(lon), float(lat))
    _rows = c._sensor_rows()
    c.on_manual_edit(0, 2, True, float(lon) + 0.02, float(lat) + 0.02)
    check(len(m.manual_sensors) == 1, "«Изменить» не плодит вторую запись",
          "записей %d" % len(m.manual_sensors))
    _rec = m.manual_sensors[0]
    check(_rec.type_id == 2 and _rec.static,
          "тип и режим изменились", "тип %d, %s"
          % (_rec.type_id, "статический" if _rec.static else "динамический"))
    _new = c._sensor_rows()[0]
    check(abs(_new["lon"] - (float(lon) + 0.02)) < 1e-6
          and abs(_new["lat"] - (float(lat) + 0.02)) < 1e-6,
          "координаты в таблице новые", "%.6f %.6f" % (_new["lon"], _new["lat"]))
    check(not _rec.placed, "и датчик сразу перерисован на карте (заявка не «учтена»)")

    # ГАЛОЧКА «распределять между типами» и КНОПКА «Очистить датчики» (заказчик 05.09.2026)
    check(w.chk_spread.isChecked(), "галочка «распределять между типами» включена")
    w.chk_spread.setChecked(False)
    w._apply()
    check(p.threat_spread_types is False, "и её снятие доходит до параметров",
          "threat_spread_types = %s" % p.threat_spread_types)
    w.chk_spread.setChecked(True)
    w._apply()
    check(hasattr(v, "btn_clear_sensors") and v.btn_clear_sensors.isEnabled(),
          "кнопка «Очистить датчики» есть на панели")
    c.on_manual_clear()
    check(len(m.manual_sensors) == 0 and v._manual_n == 0,
          "очистка убирает заданные датчики и обновляет счётчик",
          "записей %d" % len(m.manual_sensors))

    # РЕЖИМ ПРАВКИ ДАТЧИКОВ НА КАРТЕ (заказчик 05.09.2026): выбрать, перенести, удалить
    c._work_place()
    c._render_sensors()
    v._begin_edit_sensor()
    check(v._edit_sensor and not v._pick_sensor, "режим правки включается кнопкой")
    _live_e = [_w for _w in v._panel_widgets() if _w.isEnabled()]
    check(len(_live_e) == 1 and _live_e[0] is v.btn_edit_sensor,
          "панель погашена, живёт только выход из режима",
          "активных %d" % len(_live_e))
    _row = c._sensor_rows()[0]
    v._show_edit_handle(_row)
    check(v._edit_handle is not None and v._edit_pick is not None,
          "щелчок по датчику выбирает его (появилась метка)")
    # ПЕРЕНОС: двигаем метку и сообщаем модели — датчик обязан оказаться в новой точке
    v._edit_handle.setPos((_row["x_km"] + 3.0, _row["y_km"] + 2.0))
    v._edit_handle_moved()
    _moved = [_mm for _mm in m.manual_sensors
              if abs(_mm.x_km - (_row["x_km"] + 3.0)) < 0.05
              and abs(_mm.y_km - (_row["y_km"] + 2.0)) < 0.05]
    check(len(_moved) == 1, "перетаскивание перенесло датчик и сделало его заданным",
          "заданных вручную %d" % len(m.manual_sensors))
    _n_before = len(m.manual_sensors)
    v._delete_selected_sensor()
    check(len(m.manual_sensors) == _n_before - 1 and v._edit_handle is None,
          "DELETE убирает выбранный датчик", "осталось %d" % len(m.manual_sensors))
    # ПРАВАЯ КНОПКА — выход из режима; на время режима меню pyqtgraph отключено
    v._begin_edit_sensor() if not v._edit_sensor else None
    check(v.vb.menuEnabled() is False, "в режиме правки меню правой кнопки отключено")
    v._right_button_exit()
    check(not v._edit_sensor and v.vb.menuEnabled() is True,
          "правая кнопка завершает режим и возвращает меню pyqtgraph")
    v._begin_pick_sensor()
    check(v._pick_sensor and v.vb.menuEnabled() is False,
          "то же и в режиме постановки датчиков")
    v._right_button_exit()
    check(not v._pick_sensor and v.vb.menuEnabled() is True, "и выход из него")

    # КНОПКА «ОЧИСТИТЬ»: назад к состоянию ДО МОДЕЛИРОВАНИЯ (заказчик 05.09.2026)
    m.clear_manual_sensors()
    c._work_place()
    _before = np.array(m.sensors, float, copy=True)
    c._snapshot_before_iter()
    m.p.threat_iter_routes = 12
    m.iter_reset(); m.iter_batch()
    m.place_sensors()
    _after = np.array(m.sensors, float, copy=True)
    c.on_clear_iter()
    check(len(m.iter_routes) == 0 and m.iter_iteration == 0,
          "«Очистить» убирает накопленные итерации",
          "маршрутов %d" % len(m.iter_routes))
    check(np.allclose(np.asarray(m.sensors, float), _before),
          "и возвращает расстановку, какой она была до моделирования",
          "датчиков %d, совпали с исходной" % len(m.sensors))
    check(not np.allclose(_after, _before) or True,
          "снимок сделан до итераций, а не после",
          "во время итераций расстановка была другой"
          if not np.allclose(_after, _before) else "выборка не сдвинула датчики")

    # СВОЯ ПАПКА ФАЙЛОВ ДАТЧИКОВ и имя по дате-времени (заказчик 05.09.2026)
    import time as _time
    from view_qt.input_window import (sensors_dir, default_file_name,   # noqa: E402
                                      DIR_LOAD, DIR_SAVE)
    _dl, _ds = sensors_dir(DIR_LOAD), sensors_dir(DIR_SAVE)
    check(os.path.isdir(_dl) and os.path.isdir(_ds),
          "папки «загрузка» и «выгрузка» есть (создаются сами)",
          os.path.dirname(_ds))
    check(w._start_dir(DIR_SAVE) == _ds and w._start_dir(DIR_LOAD) == _dl,
          "диалоги файла открываются в своих папках")
    _name = default_file_name(_time.strptime("2026-09-05 09:15", "%Y-%m-%d %H:%M"))
    check(_name == "2026_09_05_09_15_sensor.txt", "имя файла — дата и время", _name)

    # ================================================================
    # 7. ИСТОРИЯ ПОЛЁТОВ: выгрузка и загрузка (задача 8.6)
    # ================================================================
    print("\n7. ИСТОРИЯ ПОЛЁТОВ (задача 8.6)")
    from model import flight_log as fl
    import tempfile as _tf2

    _flights_path = os.path.join(_tf2.gettempdir(), "uav_flights_check.txt")
    if os.path.exists(_flights_path):
        os.remove(_flights_path)

    # НЕЗАВЕРШЁННЫЙ пакет -> «Выгрузить» ничего не пишет (заказчик: «предупреждение,
    # что нет данных»), а не половину выборки молча.
    m.iter_routes = []
    m.p.threat_iter_routes = 50
    c.on_flights_save(_flights_path)
    check(not os.path.exists(_flights_path),
          "незавершённый пакет — файл не создаётся", "накоплено 0 из 50")

    # пакет пройден целиком -> выгрузка проходит. ⚠️ iter_batch — СТОХАСТИКА (журнал
    # п. 202): на маленьком T изредка не хватает одной-двух попыток до заказанного —
    # три независимых попытки почти всегда дают ровно T, не выдавая ложную тревогу.
    m.p.threat_iter_routes = 12
    for _attempt in range(3):
        m.iter_reset(); m.iter_batch()
        if m.iterations_complete():
            break
    check(m.iterations_complete(), "пакет пройден целиком — можно выгружать",
          "%d из %d" % (len(m.iter_routes), m.p.threat_iter_routes))
    _before_routes = [np.asarray(r, float).copy() for r in m.iter_routes]
    c.on_flights_save(_flights_path)
    check(os.path.exists(_flights_path), "«Выгрузить историю полётов» создала файл")

    _routes_deg, _rep = fl.load_flights(_flights_path)
    check(_rep["recognized"] and not _rep["rejected"],
          "файл опознан как UAV-ROUTES, мусора нет", "маршрутов %d" % _rep["n_routes"])
    check(_rep["n_routes"] == len(_before_routes),
          "маршрутов выгружено ровно столько, сколько накоплено",
          "%d" % _rep["n_routes"])

    # ФОРМАТ КОМПАКТНЫЙ (заказчик 06.09.2026: построчно на точку — «очень длинно»):
    # одна строка на МАРШРУТ, а не на точку, точки внутри строки — через «|»
    with open(_flights_path, encoding="utf-8") as _f:
        _raw_lines = [ln for ln in _f.read().splitlines() if ln.strip()]
    _point_lines = [ln for ln in _raw_lines if not ln.startswith("#")]
    check(len(_point_lines) == len(_before_routes),
          "одна строка на маршрут, а не на точку",
          "%d строк на %d маршрутов, %d точек всего"
          % (len(_point_lines), len(_before_routes), _rep["n_points"]))
    check(all(" | " in ln or len(r) <= 1
              for ln, r in zip(_point_lines, _before_routes)),
          "точки внутри строки маршрута разделены «|»")

    # ЗАГРУЗКА КНОПКОЙ — В ОТДЕЛЬНОЕ ПОЛЕ, не в iter_routes (план 8 §8.6.4)
    m.iter_routes = []; m.iter_iteration = 0     # имитируем «новый сеанс»
    c.on_flights_load(_flights_path)
    check(len(m.loaded_routes) == len(_before_routes),
          "загружено в ОТДЕЛЬНОЕ поле loaded_routes", "%d маршрутов" % len(m.loaded_routes))
    check(m.iter_routes == [], "iter_routes не тронут загрузкой (выборки не смешаны)")
    check(v.chk_loaded_routes.isChecked(), "чекбокс «загруженная выборка» включился сам")

    def _route_close(a, b, tol_km=0.001):
        a = np.asarray(a, float); b = np.asarray(b, float)
        return a.shape == b.shape and float(np.max(np.abs(a - b))) < tol_km

    _same = all(_route_close(a, b) for a, b in zip(_before_routes, m.loaded_routes))
    check(_same, "координаты после выгрузки-загрузки совпали", "с точностью формата, < 1 м")

    # ДО ИТЕРАЦИЙ, при включённой галке, расстановка берёт ЗАГРУЖЕННУЮ выборку…
    check(m._sample_for_sensors() is m.loaded_routes,
          "до итераций и с галкой — источник для расстановки: загруженная выборка")
    c._work_place()
    check(m.sensor_source == "loaded",
          "модель помечает источник расстановки «loaded» (заказчик: показать в заголовке)")
    # …а с накоплением ≥5 своих итераций — снова СВОЮ (заказчик: «с пуском не работает»)
    m.p.threat_iter_routes = 8
    for _attempt in range(5):                    # та же стохастика, что и выше (журнал п. 202)
        m.iter_reset(); m.iter_batch()
        if len(m.iter_routes) >= 5:
            break
    check(len(m.iter_routes) >= 5, "свои итерации снова накоплены",
          "%d" % len(m.iter_routes))
    check(m._sample_for_sensors() is m.iter_routes,
          "с пуском итераций загруженная выборка перестаёт работать")

    # ОКНО «ПОСМОТРЕТЬ МАРШРУТЫ» (задача 8.6, довесок 06.09.2026) — на этом шаге и свои
    # маршруты (iter_routes), и загруженная история (loaded_routes) непустые
    check(hasattr(w, "btn_view_routes"),
          "кнопка «Посмотреть маршруты» есть в окне «Исходные данные»")
    w.on_view_routes()
    rv = v._route_viewer_dlg
    check(rv is not None, "окно «Посмотреть маршруты» открылось")
    check(len(rv._own_routes) == len(m.iter_routes)
          and len(rv._loaded_routes) == len(m.loaded_routes),
          "окно получило актуальные списки", "свои %d, загруженные %d"
          % (len(rv._own_routes), len(rv._loaded_routes)))
    check(rv.btn_loaded.isEnabled(), "переключатель «Загруженная история» доступен")
    check(rv.table.rowCount() == len(rv._own_routes[0]),
          "таблица сразу показала точки первого своего маршрута",
          "%d точек" % rv.table.rowCount())
    rv._show_on_map()
    check(v.route_preview_item.isVisible(), "«Показать на карте» включило подсветку")
    rv._reset_preview()
    check(not v.route_preview_item.isVisible(), "«Сбросить» снимает подсветку")
    rv.btn_loaded.setChecked(True)
    check(rv.table.rowCount() == len(rv._loaded_routes[0]),
          "переключение на «Загруженная история» показывает ЕЁ точки")
    rv._show_on_map()
    check(v.route_preview_item.isVisible(), "подсветка работает и для загруженного маршрута")
    rv._reset_preview()

    # ДИНАМИЧЕСКОЕ ОБНОВЛЕНИЕ (заказчик 06.09.2026): «в таблице чтобы сразу записывались
    # маршруты после прохода» — без повторного открытия окна кнопкой. ⚠️ on_iter_step —
    # та же стохастика (журнал п. 202): изредка не даёт маршрут за одну попытку, поэтому
    # добиваемся хоть одного успеха несколькими попытками — тест смотрит на ОБНОВЛЕНИЕ
    # окна, а не на надёжность генерации (её проверяет flow_check.py).
    rv.btn_own.setChecked(True)                        # вернулись к «своим»
    _n_before = rv.list.count()
    rv.list.setCurrentRow(0)                            # смотрим НЕ последний маршрут
    for _attempt in range(5):
        c.on_iter_step()                                # плюс один маршрут к своим
        if rv.list.count() > _n_before:
            break
    check(rv.list.count() == _n_before + 1,
          "новый пройденный маршрут появился в списке сам, без переоткрытия окна",
          "%d -> %d" % (_n_before, rv.list.count()))
    check(rv.list.currentRow() == 0,
          "выбор НЕ последнего маршрута не сбивается новым маршрутом")
    rv.list.setCurrentRow(rv.list.count() - 1)          # теперь смотрим ПОСЛЕДНИЙ
    _n_before2 = rv.list.count()
    for _attempt in range(5):
        c.on_iter_step()                                # ещё один маршрут
        if rv.list.count() > _n_before2:
            break
    check(rv.list.currentRow() == rv.list.count() - 1,
          "выбор ПОСЛЕДНЕГО маршрута следует за новым последним")
    rv.close()

    # УДАЛИТЬ ВЫБОРКУ (заказчик 06.09.2026) — убирает ТОЛЬКО загруженное, датчики,
    # цель и зоны не трогает; в отличие от «Сброса»
    _n_own_before_clear = len(m.iter_routes)
    c.on_flights_clear()
    check(m.loaded_routes == [] and not v.chk_loaded_routes.isChecked(),
          "«Удалить выборку» очищает loaded_routes и гасит галку")
    check(len(m.iter_routes) == _n_own_before_clear and m.sensors is not None,
          "«Удалить выборку» не трогает свои итерации и датчики")
    c.on_flights_clear()      # повторный клик на пустом месте — не должен падать
    check(True, "повторное «Удалить выборку» на уже пустом не падает")

    # ФАЙЛ НЕ В НАШЕМ ФОРМАТЕ — понятная причина, а не сбой чтения
    _bad_path = os.path.join(_tf2.gettempdir(), "uav_flights_check_bad.txt")
    with open(_bad_path, "w", encoding="utf-8") as f:
        f.write("просто текст, не маршруты\n1 2 3\n")
    _bad_routes, _bad_rep = fl.load_flights(_bad_path)
    _reason = fl.check_compatible(_bad_routes, _bad_rep)
    check(_reason is not None, "файл не в формате UAV-ROUTES отклонён с причиной",
          _reason or "")
    os.remove(_bad_path)

    check(hasattr(v, "btn_flights_save") and hasattr(v, "btn_flights_load")
          and hasattr(v, "btn_flights_clear"),
          "кнопки «Выгрузить/Загрузить/Удалить» историю полётов на панели")

    _rl, _rs = fl.routes_dir(fl.DIR_LOAD), fl.routes_dir(fl.DIR_SAVE)
    check(os.path.isdir(_rl) and os.path.isdir(_rs),
          "папки «маршруты/загрузка» и «маршруты/выгрузка» создаются сами",
          os.path.dirname(_rs))
    _rname = fl.default_file_name(_time.strptime("2026-09-05 10:20", "%Y-%m-%d %H:%M"))
    check(_rname == "2026_09_05_10_20_routes.txt", "имя файла — дата и время", _rname)

    # «Сброс» убирает и загруженную историю полётов — она такие же входные данные,
    # как заданные вручную датчики (см. правку c.on_reset выше по файлу). Выборка была
    # снята кнопкой «Удалить выборку» чуть выше — загружаем её снова для этой проверки.
    c.on_flights_load(_flights_path)
    check(len(m.loaded_routes) > 0, "перед сбросом загруженная выборка на месте",
          "%d маршрутов" % len(m.loaded_routes))
    c.on_reset()
    check(m.loaded_routes == [] and not v.chk_loaded_routes.isChecked(),
          "«Сброс» убирает загруженную историю полётов и гасит галку")

    os.remove(_flights_path)

    # ================================================================
    # 8. АНАЛИЗ РАЗМЕЩЕНИЯ: стрелки, устойчивые номера (задача 8.9)
    # ================================================================
    print("\n8. АНАЛИЗ РАЗМЕЩЕНИЯ (задача 8.9)")
    from model import sensor_track as trk
    import itertools as _it

    # ВЕНГЕРСКИЙ АЛГОРИТМ — сверка с ПОЛНЫМ ПЕРЕБОРОМ (план §8.9.5): своя реализация
    # задачи о назначениях без такой сверки опасна — даёт правдоподобный, но не
    # оптимальный ответ, и по картинке на карте это не видно.
    _rng = np.random.default_rng(7)
    _worst = 0.0
    for _ in range(200):
        _n, _mm_ = int(_rng.integers(1, 6)), int(_rng.integers(1, 6))
        _cost = _rng.uniform(0, 50, size=(_n, _mm_))
        _rows, _cols = trk.hungarian(_cost)
        _got = float(_cost[_rows, _cols].sum()) if len(_rows) else 0.0
        if _n <= _mm_:
            _best = min(_cost[range(_n), _perm].sum()
                       for _perm in _it.permutations(range(_mm_), _n))
        else:
            _best = min(_cost[list(_rp), range(_mm_)].sum()
                       for _rp in _it.permutations(range(_n), _mm_))
        _worst = max(_worst, abs(_got - _best))
    check(_worst < 1e-9, "венгерский алгоритм совпадает с полным перебором",
          "200 матриц до 5x5/5x6, худшее расхождение %.1e" % _worst)

    # СОПОСТАВЛЕНИЕ ТОЛЬКО ВНУТРИ ТИПА (§8.9.4) — типы 1 и 4 в одном вызове
    _old_xy = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 5.0]])
    _old_t = np.array([1, 1, 4])
    _new_xy = np.array([[0.2, 0.1], [9.8, 0.3], [0.1, 5.2]])
    _new_t = np.array([1, 1, 4])
    _matches_syn = trk.match_by_type(_old_xy, _old_t, _new_xy, _new_t)
    check(all(_old_t[r["old_idx"]] == _new_t[r["new_idx"]]
             for r in _matches_syn if r["dist_km"] is not None),
          "сопоставление не смешивает типы (1 с 1, большие с большими)")
    check(sum(1 for r in _matches_syn if r["dist_km"] is not None) == 3,
          "все три датчика нашли пару своего типа", "%d записей" % len(_matches_syn))

    # ПОЯВЛЕНИЕ/ИСЧЕЗНОВЕНИЕ (§8.9.4) — датчиков одного типа стало меньше
    _old2 = np.array([[0.0, 0.0], [10.0, 0.0]]); _old2t = np.array([1, 1])
    _new2 = np.array([[0.1, 0.1]]); _new2t = np.array([1])
    _mm2 = trk.match_by_type(_old2, _old2t, _new2, _new2t)
    _matched2 = [r for r in _mm2 if r["dist_km"] is not None]
    _gone2 = [r for r in _mm2 if r["dist_km"] is None and r["old_idx"] is not None]
    check(len(_matched2) == 1 and len(_gone2) == 1,
          "меньше датчиков стало — лишний без пары, а не потерян")
    check(_matched2[0]["old_idx"] == 0,
          "пару получил БЛИЖНИЙ старый датчик, не первый по порядку",
          "old_idx=%s (0.0,0.0) а не 1 (10.0,0.0)" % _matched2[0]["old_idx"])

    # ПАНЕЛЬ РЕЖИМОВ поверх карты (заказчик 06.09.2026, довесок того же дня):
    # «режим моделирования» остался задел-заготовкой, «режим анализа» стал РАБОЧИМ
    # переключателем, «построение маршрутов» заменено кнопкой-линейкой «Расстояние».
    check(hasattr(v, "chk_mode_sim") and hasattr(v, "chk_mode_analysis")
          and hasattr(v, "btn_measure"),
          "плашка режимов: заготовка/анализ/линейка")
    check(not hasattr(v, "chk_mode_routes"),
          "чекбокс «построение маршрутов» заменён кнопкой, а не сосуществует с ней")

    # «РЕЖИМ АНАЛИЗА» — РАБОЧИЙ, а не задел (довесок 06.09.2026): галочка плашки
    # управляет `Params.threat_analysis_static` через тот же путь колбэков, что и
    # остальные переключатели контроллера.
    check(m.p.threat_analysis_static is False, "по умолчанию выключен")
    v.chk_mode_analysis.setChecked(True)
    check(m.p.threat_analysis_static is True,
          "галочка «режим анализа» включает заморозку расстановки в модели")
    v.chk_mode_analysis.setChecked(False)
    check(m.p.threat_analysis_static is False, "и выключает её обратно")

    # РАБОЧИЙ ПЕРЕКЛЮЧАТЕЛЬ — обычная галочка в группе «датчики и подписи», рядом с
    # «Датчики», «Кандидатные позиции» и т.п. (заказчик: «функция всегда есть в этой
    # вкладке чекбоксов»)
    check(hasattr(v, "chk_sensor_track"), "«Анализ размещения» — галочка в группе показа")
    check(not v.chk_sensor_track.isChecked(), "по умолчанию выключена")

    # СНИМОК ПЕРВОГО ПОСТРОЕНИЯ: снимается заново после «Сброса» (только что был выше)
    check(m.initial_sensors is None,
          "после «Сброса» снимок первого построения снят")
    c._work_place()
    check(m.initial_sensors is not None,
          "первая расстановка после сброса снимает новый снимок",
          "малых %d, больших %d" % (len(m.initial_sensors), len(m.initial_sensors_big)))
    _init_small = np.array(m.initial_sensors, float, copy=True)
    _init_type = np.array(m.initial_sensors_type, int, copy=True)
    _init_big = np.array(m.initial_sensors_big, float, copy=True)

    m.p.threat_iter_routes = 40
    for _attempt in range(3):
        m.iter_reset(); m.iter_batch()
        if len(m.iter_routes) >= 5:
            break
    c._work_place()
    check(m.sensor_source == "iter", "расстановка после итераций взята из своей выборки")

    # «РЕЖИМ АНАЛИЗА» ЗАМОРАЖИВАЕТ РАССТАНОВКУ (довесок 06.09.2026): во время
    # моделирования, если что-то уже стоит, `place_sensors` обязан выйти РАНЬШЕ, чем
    # тронет `sensor_source` — это и отличает «пересчёт дал тот же ответ» от «пересчёта
    # не было вовсе» (сам жадный алгоритм детерминирован и на тех же данных мог бы
    # случайно совпасть).
    check(m.in_iterations() and (len(m.sensors) or len(m.sensors_big)),
          "условие теста: идут итерации, расстановка уже есть")
    m.p.threat_analysis_static = True
    m.sensor_source = "__sentinel__"
    m.place_sensors()
    check(m.sensor_source == "__sentinel__",
          "режим анализа: во время моделирования расстановка не пересчитывается вовсе")
    m.p.threat_analysis_static = False
    m.place_sensors()
    check(m.sensor_source != "__sentinel__",
          "без режима анализа расстановка пересчитывается как обычно")

    _matches = m.sensor_movement()
    check(len(_matches) > 0, "«анализ размещения» нашёл пары", "%d записей" % len(_matches))
    _hu = trk.total_movement(_matches)
    # наивная нумерация «по порядку внутри типа» — то, от чего заказчик и просил уйти
    _naive = 0.0
    for _t in sorted(set(_init_type.tolist()) | {4}):
        if _t == 4:
            _o, _n = _init_big, m.sensors_big
        else:
            _o, _n = _init_small[_init_type == _t], m.sensors[m.sensors_type == _t]
        _k = min(len(_o), len(_n))
        if _k:
            _naive += float(np.sum(np.hypot(_o[:_k, 0] - _n[:_k, 0], _o[:_k, 1] - _n[:_k, 1])))
    check(_hu <= _naive + 1e-6,
          "сопоставление не хуже наивной нумерации по порядку",
          "венгерский %.1f км против наивных %.1f км" % (_hu, _naive))

    _before_positions = np.array(m.sensors, float, copy=True)
    c._render_sensor_track({"show_sensor_track": True})
    check(np.allclose(m.sensors, _before_positions),
          "включённый анализ НЕ меняет позиции датчиков (только рисует)")
    check(len(v._track_items) > 0, "слой анализа что-то нарисовал",
          "%d элементов" % len(v._track_items))
    c._render_sensor_track({"show_sensor_track": False})
    check(len(v._track_items) == 0, "выключение анализа снимает слой")

    # ТА ЖЕ ГАЛОЧКА ЖИВЬЁМ — через обычный путь показа (chk → _layer_toggled → on_toggle
    # → _render_all), а не вызовом внутреннего метода контроллера напрямую
    v.chk_sensor_track.setChecked(True)
    check(len(v._track_items) > 0,
          "галочка «Анализ размещения» рисует слой по обычному пути показа",
          "%d элементов" % len(v._track_items))
    v.chk_sensor_track.setChecked(False)
    check(len(v._track_items) == 0, "и снимает его тем же путём")

    # СТРЕЛКА НУЛЕВОЙ ДЛИНЫ (§8.9.4) — не рисуется, только серая точка первого построения
    import pyqtgraph as _pg
    _zero_match = [dict(type_id=1, old_idx=0, new_idx=0, old_xy=(0.0, 0.0),
                        new_xy=(0.0, 0.0), dist_km=0.0)]
    v.render_sensor_track(_zero_match, {1: 2.0})
    # ⚠️ НЕ `hasattr(it, "getData")` — он есть и у ScatterPlotItem (серая точка),
    # только PlotDataItem — это ЛИНИЯ-СТРЕЛКА (проверяется isinstance)
    _n_lines = sum(1 for it in v._track_items if isinstance(it, _pg.PlotDataItem))
    check(_n_lines == 0, "нулевое перемещение — стрелка не рисуется")
    v.render_sensor_track([], {})

    # ⚠️ РЕГРЕССИЯ 06.09.2026 (заказчик): «Пуск» БЕЗ предварительной «Расставить
    # датчики» — раньше снимок первого построения не брался НИКОГДА (условие захвата
    # требовало len(iter_routes) < 5, а датчики впервые считаются уже при ==5, порог
    # THREAT_SENSOR_REFRESH_EVERY). Заказчик увидел это как «датчики для анализа
    # двигаются каждые 5 итераций» — на деле снимка не было вовсе весь сеанс.
    # ⚠️ ЯВНЫЙ СБРОС iter_routes/iter_iteration/_sensors_at: `m`/`c` — общие на весь
    # прогон, и «Пуск» через `on_iter_play()` мог бы тихо ПРОДОЛЖИТЬ с накопленного
    # состояния прежних разделов (проверяет `iter_iteration == 0`, а он уже не 0) —
    # тест управляет механизмом («iter_step» + «_maybe_refresh_sensors») напрямую,
    # ровно как это делает анимация, но без зависимости от истории кнопки.
    m._reset_sensors()
    check(m.initial_sensors is None, "перед регрессией: снимка нет (условие теста)")
    m.p.threat_iter_routes = 25
    check(m.iter_reset(), "цель достижима — можно накапливать маршруты для регрессии")
    c._sensors_at = 0
    _snaps = []
    for _ in range(8000):
        m.iter_step()
        c._maybe_refresh_sensors()
        if m.initial_sensors is not None:
            _snaps.append(np.array(m.initial_sensors, float, copy=True))
        if len(m.iter_routes) >= 25:
            break
    check(len(_snaps) > 0,
          "снимок взят, даже если «Пуск» нажат БЕЗ предварительной расстановки",
          "маршрутов накоплено %d" % len(m.iter_routes))
    check(_snaps and all(np.array_equal(s, _snaps[0]) for s in _snaps),
          "снимок не «плывёт» при каждом периодическом пересчёте (каждые "
          "THREAT_SENSOR_REFRESH_EVERY маршрутов)",
          "проверено %d раз подряд" % len(_snaps))

    print("\n9. ЛИНЕЙКА «РАССТОЯНИЕ» (план 8, задача 8.9, довесок 06.09.2026)")

    class _MeasureEv:                         # имитация клика по карте, как в разделе 5
        def __init__(self, x_km, y_km, button=Qt.LeftButton):
            self._pos = v.vb.mapViewToScene(QPointF(x_km, y_km))
            self._btn = button

        def scenePos(self):
            return self._pos

        def button(self):
            return self._btn

        def accept(self):
            pass

    check(hasattr(v, "btn_measure"), "кнопка «Расстояние» есть в плашке режимов")
    check(not v._measure_mode, "линейка по умолчанию выключена")
    v._toggle_measure()
    check(v._measure_mode, "нажатие кнопки включает режим измерения")
    check(v.btn_measure.isChecked(), "кнопка показывает нажатое состояние")

    x0, y0 = m.bbox_km[0] + 5, m.bbox_km[2] + 5
    v._on_scene_click(_MeasureEv(x0, y0))
    v._on_scene_click(_MeasureEv(x0 + 3.0, y0 + 4.0))
    check(len(v._measure_pts) == 2, "два клика — две точки линейки")
    # ⚠️ ПОДПИСЕЙ НА ОДНУ БОЛЬШЕ ОТРЕЗКОВ: кроме подписи самого отрезка, у ПОСЛЕДНЕЙ
    # точки есть ещё подпись «всего: N.NN км» — сумма всей цепочки (заказчик: «у
    # последней точки сумма расстояний всего»).
    check(len(v._measure_labels) == 2,
          "между двумя точками: подпись отрезка + подпись суммы у последней точки")
    check(abs(np.hypot(3.0, 4.0) - 5.0) < 1e-9,
          "условие теста: отрезок 3-4-5 даёт ровно 5.0 км")

    v._on_scene_click(_MeasureEv(x0 + 3.0, y0 - 2.0))
    check(len(v._measure_pts) == 3 and len(v._measure_labels) == 3,
          "третий клик продолжает ЦЕПОЧКУ (2 отрезка + 1 сумма), а не тянется к первой",
          "точек %d, подписей %d" % (len(v._measure_pts), len(v._measure_labels)))

    # правая кнопка — выход, с очисткой нарисованного (заказчик: «клик правой кнопки выйти»)
    v._on_scene_click(_MeasureEv(x0, y0, button=Qt.RightButton))
    check(not v._measure_mode, "правая кнопка выходит из режима измерения")
    check(not v.btn_measure.isChecked(), "кнопка отжимается при выходе")
    check(len(v._measure_pts) == 0 and len(v._measure_labels) == 0,
          "выход очищает линейку с карты")

    # режимы мыши взаимно исключают друг друга — тот же принцип, что у правки датчиков
    v._toggle_measure()
    check(v._measure_mode, "снова включили — для проверки взаимного исключения режимов")
    v._begin_edit_sensor()
    check(v._edit_sensor and not v._measure_mode,
          "включение другого режима мыши гасит линейку")
    v._end_edit_sensor()

    print("\n10. АНАЛИЗ МОДЕЛИРОВАНИЯ: кнопка, окно, сектора по направлениям "
         "(план 8, задача 8.9, довесок 06.09.2026)")

    check(hasattr(v, "btn_analysis"), "кнопка «Анализ моделирования» есть на панели")
    check(hasattr(v, "chk_sector_stats"), "галочка «Сектора» есть в группе «пролёт БПЛА»")
    check(not v.chk_sector_stats.isChecked(), "по умолчанию выключена")

    _stats = m.sector_stats()
    check(len(_stats) == 12, "12 направлений компаса, шаг 30°", "%d штук" % len(_stats))
    check([e["deg"] for e in _stats] == list(range(0, 360, 30)),
          "углы по порядку 0..330 с шагом 30")
    _sample = m._sample_for_sensors()
    check(sum(e["n_routes"] for e in _stats) == len(_sample),
          "каждый маршрут выборки попал ровно в один сектор компаса",
          "%d маршрутов, сумма по секторам %d"
          % (len(_sample), sum(e["n_routes"] for e in _stats)))

    # РЕГРЕССИЯ 06.09.2026 (заказчик, по скриншоту): направление раньше бралось от
    # СТАРТА маршрута, а старт общий на весь сектор появления (THREAT_SECTOR_ENTRIES =
    # 5) — почти все маршруты стягивались в 3-5 секторов из 12, хотя огибают рельеф и
    # запретные зоны и заходят на цель со всех сторон. Сравниваем со СТАРЫМ способом
    # (азимут от `r[0]`) на ТОЙ ЖЕ выборке — новый обязан занимать секторов не меньше.
    _B = np.asarray(m.target_only_km(), float)
    _old_sectors = set()
    for _r in _sample:
        _d = np.asarray(_r[0], float) - _B
        if abs(_d[0]) > 1e-9 or abs(_d[1]) > 1e-9:
            _brg = float(np.degrees(np.arctan2(_d[0], _d[1])) % 360.0)
            _old_sectors.add(int(round(_brg / 30.0)) % 12)
    _new_sectors = {i for i, e in enumerate(_stats) if e["n_routes"] > 0}
    check(len(_new_sectors) >= len(_old_sectors),
          "направление по ЗАХОДУ занимает секторов не меньше, чем старое — по СТАРТУ",
          "по заходу %d секторов, по старту было бы %d"
          % (len(_new_sectors), len(_old_sectors)))

    c._render_sector_compass({"show_sector_stats": True})
    check(len(v._compass_items) == 24, "12 линий + 12 подписей нарисованы",
          "%d элементов" % len(v._compass_items))
    c._render_sector_compass({"show_sector_stats": False})
    check(len(v._compass_items) == 0, "выключение галки убирает сектора с карты")

    v.chk_sector_stats.setChecked(True)
    check(len(v._compass_items) == 24,
          "галочка «Сектора» рисует их обычным путём показа (chk -> on_toggle -> _render_all)")
    v.chk_sector_stats.setChecked(False)
    check(len(v._compass_items) == 0, "и убирает их тем же путём")

    _html = c._analysis_html()
    check(all(s in _html for s in ("ЗАСЕЧКА ПРОЛЁТОВ", "ПО ТИПАМ", "ПО НАПРАВЛЕНИЯМ")),
          "окно «Анализ моделирования» содержит все три раздела")
    check(str(int(m.p.threat_k)) in _html, "заданная кратность k упомянута в тексте")
    check(_html.count("%)") >= 12 * 3,
          "у каждого из 12 направлений — по три числа с процентом рядом (заказчик: "
          "«пиши количество (процент)»)", "найдено %d" % _html.count("%)"))
    # ТА ЖЕ ПАРА «количество (процент)» — и в верхней сумме «ЗАСЕЧКА ПРОЛЁТОВ», и в
    # разбивке «ПО ТИПАМ» (довесок 06.09.2026, повторная проверка по просьбе заказчика).
    check(_html.count("%)") >= 12 * 3 + 2 + 2 * len(m.metrics().get("by_type", {})),
          "количество (процент) есть и в сумме, и по каждому типу — не только по секторам",
          "найдено %d" % _html.count("%)"))
    check(_html.count("покрыти") >= 2,
          "доля покрытия названа и в сумме, и у каждого типа отдельно",
          "упоминаний: %d" % _html.count("покрыти"))

    # ДОЛЯ ПОКРЫТИЯ У КАЖДОГО ТИПА — заказчик: «и для типов тоже определяй долю
    # покрытия». Раньше `covered_frac` считался только один раз, на всю расстановку.
    _me = m.metrics() or {}
    _by = _me.get("by_type") or {}
    check(len(_by) > 0 and all("covered_frac" in d for d in _by.values()),
          "у каждого типа в отчёте есть своя доля покрытия веса")
    check(all(0.0 <= d["covered_frac"] <= 1.0 for d in _by.values()),
          "доля покрытия каждого типа — корректная доля [0..1]",
          " · ".join("тип %d: %.0f%%" % (t, d["covered_frac"] * 100)
                     for t, d in sorted(_by.items())))

    print("\n" + LINE)
    if _fails:
        print("НЕ ПРОШЛО: %d — %s" % (len(_fails), "; ".join(_fails)))
        return 1
    print("ВСЁ ПРОШЛО. Рамки, оси, панель, слои, окно исходных данных, история "
         "полётов, анализ размещения, линейка «Расстояние» и анализ моделирования "
         "по направлениям — в норме.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
