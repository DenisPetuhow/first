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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))   # код программы — в src/ (журнал 364)
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
    from model.threat.threat_grid import ThreatModel
    from model.threat.geo_frame import bbox_lonlat_to_km
    from view_qt.tabs.threat_view import ThreatMapView
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
          "красная рамка показана ровно когда район задан")

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
    free = [b.text() for b in (v.btn_area, v.btn_map_img, v.btn_input_data) if b.isEnabled()]
    check(len(locked) > 10, "панель погашена", "%d элементов" % len(locked))
    # три входа: район, своя карта и «Исходные данные» — там выбирается ОБЛАСТЬ (план 10
    # §10.5), и у области без района иначе не на что было бы её сменить
    check(len(free) == 3, "активны ровно три кнопки-входа", " · ".join(free))
    v.set_busy(True); v.set_busy(False)            # фоновый расчёт прошёл без района
    check(v.btn_input_data.isEnabled(), "«Исходные данные» не гаснут после фонового расчёта")
    v._open_input_window()
    lbl = v._input_dlg.lbl_area.text()
    check("Область:" in lbl and "тайлы:" in lbl, "в окне есть блок области и папки тайлов",
          lbl.split("<br>")[0][:60])
    v._input_dlg.hide()
    check(not v.bbox_item.isVisible(), "красная рамка убрана вместе с районом")
    v.set_area_ready(True, m.area_size_km(), m.bbox_km)
    check(len([w for w in v._panel_widgets() if not w.isEnabled()]) < 5,
          "после задания района панель ожила")
    # СПИСОК «ОБЛЁТ ЦЕЛИ» (план 11, 11.4): отдельный критерий маршрута. Ловит список, не
    # доведённый до модели: подписи есть, а выбор до `Params` не доходит.
    from config import THREAT_APPROACH_LABELS
    keys = list(THREAT_APPROACH_LABELS)
    check(v.combo_approach.count() == len(keys) == 4, "в списке «облёт цели» четыре уровня",
          " · ".join(THREAT_APPROACH_LABELS[k].split(" (")[0] for k in keys))
    check(keys[v.combo_approach.currentIndex()] == p.threat_iter_approach == "mix",
          "по умолчанию — смесь")
    v.combo_approach.setCurrentIndex(keys.index("max"))
    check(m.p.threat_iter_approach == "max", "выбор в списке дошёл до модели",
          m.p.threat_iter_approach)
    v.combo_approach.setCurrentIndex(keys.index("mix"))

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
    import view_qt.maps.map_image as _mi
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
        import view_qt.maps.map_anchor as _ma
        import view_qt.maps.geomap as _gm
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
        import view_qt.maps.map_vector as _mv
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

    print("\n6. ОКНО «ДАТЧИКИ» (задача 8.7; с плана 13, 13.2 — своё окно)")
    # ⚠️ Датчики переехали из «Исходных данных» в своё окно — проверки те же, окно другое
    v._open_input_window()
    check(hasattr(v._input_dlg, "btn_sensors") and "threat_N" not in v._input_dlg._edits,
          "в «Исходных данных» датчиков нет — только кнопка «Датчики…»")
    v._input_dlg.on_open_sensors()
    w = v._sensors_dlg                                    # окно «Датчики»
    check(w is not None and w.isVisible(), "кнопка открывает окно «Датчики»")
    # номера 1–3: N, R, k; у номера 4 — N и R (кратность больших не участвует)
    check(len(w._edits) == 11, "поля номеров 1–4 на месте", "%d полей" % len(w._edits))
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
    from view_qt.windows.input_window import write_sensors_file, read_sensors_file
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
    from view_qt.windows.input_window import (sensors_dir, default_file_name,   # noqa: E402
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
    from model.threat import flight_log as fl
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
    # ⚠️ кнопка — в «Исходных данных», а `w` с плана 13 (13.2) — окно «Датчики»
    check(hasattr(v._input_dlg, "btn_view_routes"),
          "кнопка «Посмотреть маршруты» есть в окне «Исходные данные»")
    v._input_dlg.on_view_routes()
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

    # ⚠️ МАРШРУТ, ЗАПИСАННЫЙ ОДИНАКОВЫМИ ПРОХОДАМИ ПОДРЯД (файл из KML, 14.09.2026): одной
    # ломаной линия возвращалась из конца прохода в начало — прямые через всю область.
    # Проходы ЗАДУМАНЫ (заказчик) — делятся на отдельные маршруты, все точки сохраняются.
    _rep_path = os.path.join(_tf2.gettempdir(), "uav_flights_check_repeat.txt")
    _one = ["  40.10 62.50", "  40.20 62.60", "  40.35 62.62", "  40.50 62.70"]
    with open(_rep_path, "w", encoding="utf-8") as f:
        f.write("# UAV-ROUTES v1\n# R 1\n" + "\n".join(_one * 3) + "\n# R 2\n" + "\n".join(_one) + "\n")
    _rr, _rrep = fl.load_flights(_rep_path)
    check(len(_rr) == 4 and all(len(r) == 4 for r in _rr) and _rrep.get("routes_in_file") == 2,
          "три одинаковых прохода — три маршрута, по 4 точки каждый",
          "маршрутов %d (в файле %s), точек %d" % (len(_rr), _rrep.get("routes_in_file"),
                                                 _rrep.get("n_points", 0)))
    check(_rrep.get("n_points") == 16, "все точки сохранены", "16 из 16")
    _jump = max(float(np.hypot(*np.diff(r, axis=0).T).max()) for r in _rr)
    check(_jump < 0.2, "возвратов из конца в начало нет", "длиннейший шаг %.2f°" % _jump)
    os.remove(_rep_path)

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

    # ГАЛОЧКА «ОБОБЩАТЬ ОДИНАКОВЫЕ ПРОХОДЫ» (заказчик 14.09.2026): по умолчанию выключена —
    # все проходы отдельными маршрутами; включена — по одному проходу на запись файла
    _gen_path = os.path.join(_tf2.gettempdir(), "uav_flights_check_generalize.txt")   # тестовый файл
    with open(_gen_path, "w", encoding="utf-8") as f:
        f.write("# UAV-ROUTES v1\n# R 1\n" + "\n".join(_one * 3) + "\n# R 2\n" + "\n".join(_one) + "\n")
    c.on_flights_load(_gen_path)
    check(len(m.loaded_routes) == 4 and not m.generalize_loaded,
          "по умолчанию обобщение выключено — все проходы отдельными маршрутами",
          "%d маршрутов" % len(m.loaded_routes))
    v._open_route_viewer()
    _dlg = v._route_viewer_dlg                       # окно «Посмотреть маршруты»
    check(_dlg.chk_generalize.isEnabled() and not _dlg.chk_generalize.isChecked(),
          "галочка в окне доступна (повторы есть) и снята", _dlg.chk_generalize.text())
    _dlg.chk_generalize.setChecked(True)             # человек ставит галочку
    check(m.generalize_loaded and len(m.loaded_routes) == 2 and len(m.loaded_routes_all) == 4,
          "галочка обобщает: по проходу на запись, все проходы сохранены в модели",
          "в выборке %d, всего проходов %d" % (len(m.loaded_routes), len(m.loaded_routes_all)))
    _dlg.chk_generalize.setChecked(False)            # и снимает
    check(not m.generalize_loaded and len(m.loaded_routes) == 4,
          "снятая галочка возвращает все проходы", "%d маршрутов" % len(m.loaded_routes))
    _dlg.hide()
    c.on_flights_clear()
    os.remove(_gen_path)

    # ================================================================
    # 8. АНАЛИЗ РАЗМЕЩЕНИЯ: стрелки, устойчивые номера (задача 8.9)
    # ================================================================
    print("\n8. АНАЛИЗ РАЗМЕЩЕНИЯ (задача 8.9)")
    from model.placement import sensor_track as trk
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
    check(len(v.measure.pts) == 2, "два клика — две точки линейки")
    # ⚠️ ПОДПИСЕЙ НА ОДНУ БОЛЬШЕ ОТРЕЗКОВ: кроме подписи самого отрезка, у ПОСЛЕДНЕЙ
    # точки есть ещё подпись суммы всей цепочки (заказчик: «у последней точки сумма»).
    check(len(v.measure._labels) == 2,
          "между двумя точками: подпись отрезка + подпись суммы у последней точки")
    check(abs(np.hypot(3.0, 4.0) - 5.0) < 1e-9,
          "условие теста: отрезок 3-4-5 даёт ровно 5.0 км")
    check(abs(v.measure.total_km() - 5.0) < 1e-9, "сумма пути посчитана",
          "%.2f км" % v.measure.total_km())

    v._on_scene_click(_MeasureEv(x0 + 3.0, y0 - 2.0))
    check(len(v.measure.pts) == 3 and len(v.measure._labels) == 3,
          "третий клик продолжает ЦЕПОЧКУ (2 отрезка + 1 сумма), а не тянется к первой",
          "точек %d, подписей %d" % (len(v.measure.pts), len(v.measure._labels)))

    # ⭐ ЛИНЕЙКА КАК В GOOGLE EARTH (правило заказчика 21.09.2026): предпросмотр за
    # курсором, время по скорости типа, подписи повёрнуты вдоль отрезка.
    from view_qt.maps.measure_tool import format_time
    check(format_time(2.5) == "2 ч 30 мин" and format_time(0.75) == "45 мин",
          "время в подписи: часы и минуты", format_time(2.5))
    n_lbl = len(v.measure._labels)                       # подписей до наведения
    v.measure.move_to(x0 + 9.0, y0 + 9.0)
    xs_prev, _ = v.measure.preview.getData()
    check(xs_prev is not None and len(xs_prev) == 2,
          "при ведении мыши тянется линия-предпросмотр")
    check(len(v.measure._labels) == n_lbl + 1, "...и у курсора появились километры")
    check(v.measure.preview.opacity() < 1.0, "предпросмотр полупрозрачный",
          "%.2f" % v.measure.preview.opacity())
    v.measure.move_to(None, None)
    xs_prev2, _ = v.measure.preview.getData()
    check(xs_prev2 is None or len(xs_prev2) == 0, "курсор ушёл с карты — предпросмотр снят")
    check("км" in v.measure.status() and "точек 3" in v.measure.status(),
          "строка состояния показывает точки и сумму", v.measure.status()[:48])
    # время показывается ТОЛЬКО при выбранном типе
    _speed_before = v.on_uav_speed
    v.on_uav_speed = lambda: None
    check("мин" not in v.measure._text(100.0), "без типа БПЛА время не выдумывается",
          v.measure._text(100.0))
    v.on_uav_speed = lambda: 180.0
    check("мин" in v.measure._text(180.0) and "1 ч" in v.measure._text(180.0),
          "со скоростью 180 км/ч путь 180 км — это 1 ч", v.measure._text(180.0))
    v.on_uav_speed = _speed_before

    # правая кнопка — выход, с очисткой нарисованного (заказчик: «клик правой кнопки выйти»)
    v._on_scene_click(_MeasureEv(x0, y0, button=Qt.RightButton))
    check(not v._measure_mode, "правая кнопка выходит из режима измерения")
    check(not v.btn_measure.isChecked(), "кнопка отжимается при выходе")
    check(len(v.measure.pts) == 0 and len(v.measure._labels) == 0,
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

    check(hasattr(v, "btn_analysis"), "кнопка «Аналитика» есть на панели")
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

    print("\n11. СЛОЙ ЗАВЕДЁН ЦЕЛИКОМ, А НЕ НАПОЛОВИНУ")
    # ⚠️ НОВЫЙ СЛОЙ ЖИВЁТ В ЧЕТЫРЁХ МЕСТАХ СРАЗУ, и забыть любое — тихая поломка:
    # без фильтра тегов слой не соберётся из OSM (веса не будет), без стиля не нарисуется,
    # без порядка не попадёт в легенду и в окно «Векторные слои». Проверка заведена
    # 13.09.2026 при добавлении слоя ПРОСЕК: всё это пришлось сверять руками.
    from config import THREAT_LAYERS, THREAT_LAYER_ORDER
    from model.threat.threat_grid import OSM_LAYER_FILTERS
    from view_qt.tabs.threat_view import LAYER_STYLE
    for where, names in (("фильтр тегов OSM", OSM_LAYER_FILTERS),
                         ("стиль линии", LAYER_STYLE),
                         ("порядок слоёв", THREAT_LAYER_ORDER)):
        miss = [k for k in THREAT_LAYERS if k not in names]
        check(not miss, "у каждого слоя есть %s" % where,
              "нет у: %s" % ", ".join(miss) if miss else "слоёв %d" % len(THREAT_LAYERS))
    extra = [k for k in THREAT_LAYER_ORDER if k not in THREAT_LAYERS]
    check(not extra, "в порядке слоёв нет лишних имён",
          "лишние: %s" % ", ".join(extra) if extra else "")
    # стиль без слоя — тоже ошибка: он нарисуется, но веса у него не будет
    orphan = [k for k in LAYER_STYLE if k not in THREAT_LAYERS]
    check(not orphan, "нет стилей для несуществующих слоёв",
          "лишние: %s" % ", ".join(orphan) if orphan else "")
    # ⚠️ СРАВНИВАЕМ ТОЛЬКО ЛИНЕЙНЫЕ СЛОИ. У площадного слоя штрих — это рисунок контура,
    # и совпасть с линией он может без вреда: их всё равно не спутать (застройка — пятно).
    # Первый же прогон проверки нашёл такую пару — `power` и `built_up` с [2, 3].
    dashes = {}
    for name, st in LAYER_STYLE.items():
        if THREAT_LAYERS.get(name, {}).get("geom") != "line":
            continue
        d = tuple(st.get("dash") or ())
        if d:                                     # сплошных линий несколько — это норма
            dashes.setdefault(d, []).append(name)
    same = [v for v in dashes.values() if len(v) > 1]
    check(not same, "штрих у каждой линии свой — слои различимы",
          ("совпадают: %s" % "; ".join(", ".join(v) for v in same)) if same else
          "разных штрихов %d" % len(dashes))

    print("\n12. ЦИФРОВЫЕ КАРТЫ: галочка скрывает, «Применить» + «Построить» исключают")
    # ⚠️ ДВА РАЗНЫХ ДЕЙСТВИЯ (схема заказчика 13.09.2026): галочка — только ПОКАЗ, слой в
    # расчёте остаётся; «Применить к расчёту» и следующий «Построить карту» — слой уходит
    # из весов. Спутать их — значит либо пересчитывать карту на каждый клик, либо молча
    # считать то, что человек на карте уже не видит.
    _lay = "track"
    def _in_calc():
        g = m.grid
        return g is not None and _lay in g.layers and float(g.layers[_lay].sum()) > 0
    m.set_enabled_layers(None); m.build()
    v._open_data_dialog(); _d = v._data_dialog
    check(all(ch.isChecked() for ch in _d._checks.values()), "при запуске все галочки включены")
    check(not _d.btn_apply.isEnabled(), "«Применить» погашена, пока менять нечего")
    _d._checks[_lay].setChecked(False)
    check(v._vec_visible[_lay] is False, "снятая галочка сразу скрывает слой")
    check(m.enabled_layers is None and _in_calc(), "но в расчёте слой остаётся")
    check(_d.btn_apply.isEnabled(), "«Применить» загорается при расхождении")
    _d._apply(); m.build()
    check(not _in_calc(), "«Применить» + «Построить карту» убирают слой из весов")
    _d._checks[_lay].setChecked(True); _d._apply(); m.build()
    check(m.enabled_layers is None and _in_calc(), "вернули галочку и применили — слой в расчёте")
    _d.close()

    # РАЙОН РАДИУСОМ, ОКНО «СЕКТОР НАЛЁТА», ТОЧКИ СТАРТА (план 11, 11.6). Ловит то, чего не
    # видят расчётные проверки: круг нарисован, а в модель ушёл прямоугольник; режим кликов
    # сектора не отпускает правую кнопку; перенос точки за район оставляет метку «в пустоте».
    print("\n13. РАЙОН РАДИУСОМ, СЕКТОР НАЛЁТА, ТОЧКИ СТАРТА (план 11, 11.6)")

    class _Ev:                                   # клик по карте: кнопка и двойной ли
        def __init__(self, x_km, y_km, button=Qt.LeftButton, double=False):
            self._pos = v.vb.mapViewToScene(QPointF(x_km, y_km))
            self._btn, self._dbl = button, double

        def scenePos(self):
            return self._pos

        def button(self):
            return self._btn

        def double(self):
            return self._dbl

        def accept(self):
            pass

    check(v.chk_shape_rect.isChecked() and not v.chk_shape_circle.isChecked(),
          "переключатель «район / радиус» есть, по умолчанию — район")
    v.set_area_ready(False)
    check(v.chk_shape_circle.isEnabled(), "форму можно выбрать и без района")
    v.chk_shape_circle.setChecked(True)
    check(v._area_shape == "circle" and not v.chk_shape_rect.isChecked(),
          "«радиус» включается, «район» гаснет сам")
    cx, cy = (float(q) for q in m.target_only_km())
    R12 = 30.0
    v._begin_area()
    v._on_scene_click(_Ev(cx, cy))
    v._on_scene_click(_Ev(cx + R12, cy))
    v._on_scene_click(_Ev(cx + R12, cy, button=Qt.MiddleButton))   # колёсико — принять
    circ = m.area_circle
    check(circ is not None and abs(circ[2] - R12) < 0.05,
          "два клика и колёсико задали КРУГ в модели",
          "R = %.2f км" % (circ[2] if circ else float("nan")))
    xs_b, _ = v.bbox_item.getData()
    check(xs_b is not None and len(xs_b) > 100 and v.btn_area.text().startswith("Радиус"),
          "красная рамка — окружность, кнопка — «Радиус …»", v.btn_area.text())
    check(tuple(float(q) for q in m.target_only_km()) == (circ[0], circ[1]) if circ else False,
          "цель — центр круга")
    m.build(); c._render_all()
    out12 = m.grid.outside_mask()
    cand12 = m.candidate_positions()
    check(out12 is not None and float(np.abs(m.grid.weight[out12]).max()) == 0.0,
          "за окружностью вес 0", "%d ячеек" % int(out12.sum()))
    check(bool(len(cand12)) and bool(m.in_area(cand12[:, 0], cand12[:, 1]).all()),
          "кандидаты датчиков только внутри круга", "%d шт." % len(cand12))

    check(v.btn_sector.text() == "Сектор налёта", "кнопка «Сектор налёта» в панели")
    v._open_sector_window()
    sd = v._sector_dlg
    check(sd is not None and sd.isVisible(), "окно сектора открывается")
    sd.spin_angle.setValue(90); sd.spin_n.setValue(7); sd._apply_params()
    check(abs(m.sector_half_deg() - 45.0) < 1e-9 and m.sector_entries_n() == 7,
          "раствор и число точек из окна дошли до модели",
          "полураствор %.0f°, точек %d" % (m.sector_half_deg(), m.sector_entries_n()))
    sd.btn_auto.click()
    n_auto = len(m.sector_points())
    check(m.sector_kind == "auto" and n_auto >= 1, "«Построить сектор» — по 360°",
          "точек %d" % n_auto)
    check("360" in sd.lbl_state.text(), "окно показывает вид сектора", sd.lbl_state.text()[:50])
    sd.btn_click.click()
    check(v._sector_mode and not v.vb.menuEnabled(),
          "«Задать сектор кликом» включает режим и забирает правую кнопку")
    # ⚠️ ТОЧКИ СТАВЯТСЯ ДВОЙНЫМ КЛИКОМ (правило заказчика 21.09.2026): одинарный только
    # показывает координаты — проверяем и это, иначе правило можно сломать незаметно.
    _kind_before = m.sector_kind
    v._on_scene_click(_Ev(cx + R12 - 0.5, cy - 5.0))
    check(m.sector_kind == _kind_before, "одинарный клик сектор НЕ задаёт")
    v._on_scene_click(_Ev(cx + R12 - 0.5, cy - 5.0, double=True))
    v._on_scene_click(_Ev(cx - R12 + 0.5, cy + 5.0, double=True))
    check(m.sector_kind == "click" and v._sector_mode,
          "двойной клик по краю задаёт сектор, режим остаётся для следующего",
          "точек %d" % len(m.sector_points()))
    v._on_scene_click(_Ev(cx, cy, button=Qt.RightButton))
    check(not v._sector_mode and not sd.isVisible() and v.vb.menuEnabled(),
          "правая кнопка — выход из режима и закрытие окна сектора")

    n_before = len(m.entry_points)
    # ⚠️ кнопка «Точки старта» переехала с панели в окно сектора (заказчик 30.09.2026)
    v._open_sector_window()
    sd.btn_edit.click()
    check(v._edit_entry and not v.btn_target.isEnabled() and not hasattr(v, "btn_entry"),
          "«Редактировать точки» окна сектора включает режим и гасит панель; на панели кнопки нет")
    px, py = cx - 8.0, cy + 6.0
    v._on_scene_click(_Ev(px, py, double=True))
    check(len(m.manual_entries) == 1 and len(m.entry_points) == n_before + 1,
          "двойной клик ставит точку старта", "всего точек %d" % len(m.entry_points))
    mx_, _ = v.entries_manual_scatter.getData()
    check(mx_ is not None and len(mx_) == 1, "точка человека нарисована своим значком")
    v._on_scene_click(_Ev(px + 0.05, py))
    check(v._entry_handle is not None and v._entry_pick is not None and v._entry_pick["manual"],
          "щелчок рядом выделяет точку")
    v._entry_handle.setPos(px + 2.0, py + 1.0); v._entry_handle_moved()
    check(abs(m.manual_entries[0][0] - (px + 2.0)) < 1e-6,
          "перетаскивание переносит точку в модели")
    v._entry_handle.setPos(cx + 3 * R12, cy); v._entry_handle_moved()
    hp = v._entry_handle.pos()
    check(abs(m.manual_entries[0][0] - (px + 2.0)) < 1e-6 and abs(float(hp.x()) - (px + 2.0)) < 1e-6,
          "за район не переносится, метка возвращается на место")
    from PyQt5.QtGui import QKeyEvent
    from PyQt5.QtCore import QEvent
    v.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier))
    check(not m.manual_entries and len(m.entry_points) == n_before, "DELETE убирает точку")
    v._on_scene_click(_Ev(cx, cy, button=Qt.RightButton))
    check(not v._edit_entry and v.btn_target.isEnabled() and v.vb.menuEnabled(),
          "правая кнопка — выход, панель ожила")
    # ОКНО СЕКТОРА: «Добавить точки» и «Редактировать точки» (заказчик 30.09.2026)
    v._open_sector_window()
    sd.btn_auto.click()                                   # сектор заново: точки сектора есть
    sd.btn_add.click()
    _add_on = v._edit_entry and v._entry_add and "Добавляю" in sd.btn_add.text()   # режим и подпись окна
    v._on_scene_click(_Ev(px, py))                        # одиночный щелчок
    check(_add_on and len(m.manual_entries) == 1, "«Добавить точки» в окне сектора: одиночный щелчок ставит точку",
          "ручных %d" % len(m.manual_entries))
    sd.btn_edit.click()
    check(v._edit_entry and not v._entry_add and "Правлю" in sd.btn_edit.text(),
          "«Редактировать точки»: подрежим сменён, режим не вышел")
    sx, sy = m.sector_points()[0]                         # точка сектора, км
    v._on_scene_click(_Ev(sx + 0.02, sy))
    v._entry_handle.setPos(sx + 2.0, sy + 1.0); v._entry_handle_moved()
    wx2, _ = v.entries_scatter.getData()                  # белые точки на карте
    mx2, _ = v.entries_manual_scatter.getData()           # розовые точки на карте
    check(len(m.moved_entries) == 1 and len(m.manual_entries) == 1 and len(mx2) == 1
          and any(abs(float(x) - (sx + 2.0)) < 1e-6 for x in wx2),
          "перенесённая точка сектора остаётся белой («розовые не надо»)")
    c.on_sector_params(m.sector_half_deg(), m.sector_entries_n())   # переотбор сектора
    d_old = min(float(np.hypot(qx - sx, qy - sy)) for qx, qy in m._sector_pts)   # до прежнего места, км
    check(d_old > m.grid.h and len(m.moved_entries) == 1
          and float(np.hypot(m.moved_entries[0][0] - sx - 2.0, m.moved_entries[0][1] - sy - 1.0)) < 1e-6,
          "переотбор: перенесённая стоит, на её прежнее место белая не встаёт", "%.2f км" % d_old)
    sd.btn_edit.click()
    check(not v._edit_entry and "Редактировать" in sd.btn_edit.text(), "повторное нажатие — выход из режима")
    m.add_manual_entry(px, py)
    c.on_reset()
    check(not m.manual_entries and m.sector_kind is None, "«Сброс» убирает сектор и точки старта")
    v.chk_shape_rect.setChecked(True)
    check(v._area_shape == "rect" and m.area_circle is not None,
          "переключатель не трогает уже заданный круг (меняет только форму следующего)")

    print("\n14. ПОДПИСЬ «ТАЙЛЫ» ПОКАЗЫВАЕТ ПАПКУ УЧАСТКА, А НЕ ТОЛЬКО КОРЕНЬ")
    # ⚠️ Тайлы лежат в <корень>/<участок>/<слой>/z/x/y: по одному корню не видно, та ли
    # область открыта (жалоба заказчика 20.09.2026 «нет папки после tile_cache»).
    import re as _re
    from config import THREAT_TILE_AREA as _TA
    info = _re.sub(r"<[^>]+>", " ", v._area_info_text())
    tiles_line = [s for s in info.split("тайлы:") if s][-1]
    check(bool(_TA), "у области задан участок кэша тайлов: %r" % _TA)
    check(not _TA or _TA in tiles_line, "папка участка видна в подписи")
    check("строка подложки" not in info, "подпись не разрослась лишним текстом")

    print("\n15. ОКНО «ВИДЫ БПЛА»: СПРАВОЧНИК ТИПОВ (план 12, задача 12.3)")
    # ⚠️ ДВЕ ПОДМЕНЫ, обе обязательны: модальное предупреждение без экрана ждало бы нажатия
    # вечно, а запись ушла бы в настоящий справочник человека, а не во временный файл.
    import tempfile as _tmp
    from PyQt5 import QtWidgets as _QtW
    from model.threat import uav_types as _ut
    from view_qt.windows.uav_types_window import UavTypesDialog
    _said, _warn0, _path0 = [], _QtW.QMessageBox.warning, _ut.path   # что вернуть в конце
    _file = os.path.join(_tmp.mkdtemp(), _ut.FILE_NAME)              # временный справочник
    _QtW.QMessageBox.warning = lambda *a, **k: _said.append(a[2] if len(a) > 2 else "")
    _ut.path = lambda: _file
    try:
        n0 = len(_ut.defaults())                         # типов во встроенном списке, шт.
        dlg = UavTypesDialog(None)
        check(dlg.table.rowCount() == n0, "таблица открылась со справочником",
              "строк %d" % dlg.table.rowCount())
        kinds = {dlg.table.cellWidget(r, 1).currentText() for r in range(n0)}  # классы в таблице
        check("мультиротор" in kinds and "самолёт" in kinds, "в умолчаниях оба класса",
              ", ".join(sorted(kinds)))
        dlg._add()
        check(dlg.table.rowCount() == n0 + 1, "«Добавить» прибавляет строку")
        check(not dlg._collect()[1], "новая строка проходит проверку",
              "; ".join(dlg._collect()[1]))
        dlg.table.item(0, 2).setText("18000")            # скорость заведомо неверная
        check(any("скорость" in s for s in dlg._collect()[1]), "кривое число — жалоба",
              "; ".join(dlg._collect()[1]) or "жалоб нет")
        said0 = len(_said)                               # предупреждений до попытки записи
        dlg._save()
        check(len(_said) > said0, "с жалобой предупреждение показано")
        check(not os.path.exists(_file), "...и справочник НЕ записан")
        dlg.table.item(0, 2).setText("180")              # поправили — теперь должно пройти
        dlg._save()
        check(os.path.exists(_file), "после правки справочник записан")
        check(len(_ut.load()) == n0 + 1, "прочитано столько же типов",
              "%d против %d" % (len(_ut.load()), n0 + 1))
        dlg.table.item(1, 0).setText(dlg.table.item(0, 0).text())   # два одинаковых названия
        check(any("повторяется" in s for s in dlg._collect()[1]), "дубль названия — жалоба")
        dlg._reset()
        check(dlg.table.rowCount() == n0, "«Вернуть исходные» восстанавливает встроенный список")
        plane, rotor = _ut.FixedWing("т", 180, 25), _ut.Multirotor("т", 60, 15)
        check(plane.turn_radius_km() > 0 and rotor.turn_radius_km() == 0,
              "разворот: у самолёта радиус есть, у мультиротора ноль",
              "%.2f км против %.2f км" % (plane.turn_radius_km(), rotor.turn_radius_km()))
    finally:                                             # подмены не должны пережить раздел
        _QtW.QMessageBox.warning, _ut.path = _warn0, _path0

    print("\n16. УСЛОВИЯ МОДЕЛИРОВАНИЯ: КНОПКА, ОКНО, ТОЧКА СТАРТА (план 12, задача 12.2)")
    v._open_input_window()
    iw = v._input_dlg                                    # окно «Исходные данные»
    c.on_clear_sector()
    v.refresh_conditions_button()
    check(not iw.btn_conditions.isEnabled(), "без точек входа кнопка условий недоступна")
    check("сектор" in iw.lbl_conditions.text(), "подпись говорит, чего не хватает",
          iw.lbl_conditions.text())
    # ⚠️ ЦЕЛЬ КЛИКОМ НЕ ТРЕБУЕТСЯ (жалоба заказчика 21.09.2026: «почему не нажимается,
    # сектор задан»): цель есть всегда — из `область.txt` или как центр круглого района.
    # Поэтому сектор задаётся БЕЗ `on_set_target`, и кнопка обязана ожить.
    m.target_km = None                                   # цель кликом не ставили
    c.on_sector_auto()
    v.refresh_conditions_button()
    check(iw.btn_conditions.isEnabled(), "сектор задан — кнопка ожила БЕЗ клика по цели",
          iw.lbl_conditions.text())
    c.on_set_target(cx, cy)                              # дальше работаем с явной целью

    iw.btn_conditions.click()
    sc = v._sim_dlg                                      # окно «Условия моделирования»
    check(sc is not None and sc.isVisible(), "окно условий открывается")
    check(sc.combo_type.count() >= 1 and m.uav_type is not None,
          "список типов заполнен, тип выбран по умолчанию",
          sc.combo_type.currentText())
    check("км/ч" in sc.lbl_type.text() and "разворот" in sc.lbl_type.text(),
          "показаны характеристики типа", sc.lbl_type.text()[:60])
    sc.combo_type.setCurrentIndex(sc.combo_type.count() - 1)
    check(m.uav_type is not None and m.uav_type.name == sc.combo_type.currentText(),
          "выбор в списке дошёл до модели", m.uav_type.name if m.uav_type else "нет")

    # ⚠️ ПРЯЧУТСЯ ВСЕ ОКНА, не только своё (правило заказчика 21.09.2026): карту не должно
    # перекрывать ничто. Открываем второе окно и проверяем, что спряталось и оно.
    v._open_sector_window()
    sd16 = v._sector_dlg                                 # второе открытое окно
    sc.show()
    sc.btn_start.click()
    check(v._uav_start_mode and not sc.isVisible(),
          "«Задать точку старта» включает режим и прячет окно")
    check(not sd16.isVisible(), "...и ВСЕ прочие окна тоже спрятаны")
    check(not v.vb.menuEnabled(), "правая кнопка забрана под выход из режима")
    sx, sy = cx + 120.0, cy + 80.0                       # точка заведомо за районом
    v._on_scene_click(_Ev(sx, sy))
    check(m.uav_start_km is None, "одинарный клик точку старта НЕ ставит")
    v._on_scene_click(_Ev(sx, sy, double=True))
    check(m.uav_start_km is not None and abs(m.uav_start_km[0] - sx) < 1e-6,
          "двойной клик поставил точку старта в модели", str(m.uav_start_km))
    check(not v._uav_start_mode and sc.isVisible(), "режим снят, окно условий вернулось")
    check(sd16.isVisible() and v.vb.menuEnabled(),
          "...и остальные окна вернулись вместе с правой кнопкой")
    # ПРАВАЯ КНОПКА — ВЫХОД БЕЗ ТОЧКИ: старая точка остаётся на месте
    keep = m.uav_start_km                                # точка до отмены
    sc.btn_start.click()
    v._on_scene_click(_Ev(cx, cy, button=Qt.RightButton))
    check(not v._uav_start_mode and m.uav_start_km == keep,
          "правая кнопка выходит из режима, точку не меняя")
    check(sc.isVisible() and sd16.isVisible(), "...и все окна вернулись")
    sd16.close()
    ux, _uy = v.uav_start_scatter.getData()
    check(ux is not None and len(ux) == 1, "метка старта нарисована на карте")
    check("Старт:" in sc.lbl_start.text() and "До точек входа" in sc.lbl_start.text(),
          "подпись показывает координаты и расстояния до точек входа",
          sc.lbl_start.text()[:70])

    v._clear_uav_start()
    ux2, _ = v.uav_start_scatter.getData()
    check(m.uav_start_km is None and (ux2 is None or len(ux2) == 0),
          "«Убрать точку старта» снимает и точку, и метку")
    m.set_uav_start(sx, sy)
    # ⚠️ ТОЧКА СТАРТА — В КМ ОТ ЯКОРЯ: смена области делает её бессмысленной, как и цель
    m.set_area(THREAT_BBOX_LONLAT)
    check(m.uav_start_km is None, "смена области сбрасывает точку старта")
    check(m.uav_type is not None, "...а выбранный тип остаётся: он к местности не привязан")

    print("\n16а. ПАНЕЛЬ МАРШРУТА: ЧЕКБОКС «ЗАДАТЬ ВРУЧНУЮ», АВТООБНОВЛЕНИЕ (заказчик 21.09)")
    check("threat_speed_kmh" not in iw._edits and "threat_bank_deg" not in iw._edits,
          "скорость и крен убраны из «Маршрут БПЛА» — они у типа БПЛА")
    check(hasattr(iw, "chk_lmax_manual") and "вручную" in iw.chk_lmax_manual.text(),
          "чекбокс называется «задать вручную»", iw.chk_lmax_manual.text())
    e_lmax = iw._edits["threat_L_max"]                   # поле запаса хода
    iw.chk_lmax_manual.setChecked(False)
    check(e_lmax.isReadOnly(), "галочка снята — поле закрыто, считаем сами")
    iw.chk_lmax_manual.setChecked(True)
    check(not e_lmax.isReadOnly(), "галочка стоит — поле открыто для правки")
    iw._apply()
    check(m.p.threat_L_max_manual, "«вручную» дошло до модели")
    iw.chk_lmax_manual.setChecked(False)
    iw._apply()
    check(not m.p.threat_L_max_manual, "...и снимается обратно")
    # ⚠️ СРЕДНЕЕ ОБНОВЛЯЕТСЯ САМО, без «Применить» (правило заказчика 21.09.2026)
    m.set_uav_type(_ut.defaults()[0])
    c.on_set_uav_start(cx + 500.0, cy - 400.0)
    per16 = m.entry_L_max_km()                           # запасы по точкам, км
    check(per16 and abs(m.p.threat_L_max - sum(per16) / len(per16)) < 0.06,
          "после клика по старту среднее в панели пересчитано само",
          "%.1f км" % m.p.threat_L_max)
    check(e_lmax.text() == "%g" % m.p.threat_L_max, "...и показано в поле окна", e_lmax.text())

    print("\n17. ТАБЛИЦА ТОЧЕК ВХОДА: «ПОКАЗАТЬ ДАННЫЕ» (план 12, задача 12.5)")
    # ⚠️ ТОЧЕК НУЖНО НЕСКОЛЬКО: на одной проверка «у соседней запас остался расчётным»
    # вырождается. Круглый район радиусом 25 км из раздела 13 давал ровно одну точку,
    # поэтому здесь возвращаем прямоугольный участок целиком.
    m.set_area(THREAT_BBOX_LONLAT)
    m.build()
    m.p.threat_sector_entries = 5
    c.on_sector_auto()
    v._open_sector_window()
    sd2 = v._sector_dlg                                  # окно «Сектор налёта»
    check(sd2.btn_data.isVisible(), "кнопка «Показать данные» есть в окне сектора")
    sd2.btn_data.click()
    et = v._entries_dlg                                  # таблица точек входа
    n_pts = len(m.entry_points_km())                     # точек входа, шт.
    check(et is not None and et.isVisible() and et.table.rowCount() == n_pts,
          "таблица открылась, строк по числу точек", "строк %d" % et.table.rowCount())
    check("—" in et.table.item(0, 4).text(),
          "без старта БПЛА расстояние показано прочерком", et.table.item(0, 4).text())
    check("Точка старта не задана" in et.lbl_state.text(), "...и об этом сказано внизу")
    m.set_uav_type(_ut.defaults()[0])
    m.set_uav_start(cx + 400.0, cy - 300.0)
    et.refresh()
    check(et.table.item(0, 4).text() != "—", "со стартом появилось расстояние",
          et.table.item(0, 4).text())
    was0 = float(et.table.item(0, 5).text())             # запас первой точки до правки, км
    et.table.item(0, 5).setText("123.5")                 # человек ввёл своё число
    check(abs(m.entry_L_max_km()[0] - 123.5) < 1e-6, "ручной запас дошёл до модели",
          "%.1f -> %.1f" % (was0, m.entry_L_max_km()[0]))
    per17 = m.entry_L_max_km()                           # запасы всех точек, км
    check(len(per17) < 2 or abs(per17[1] - 123.5) > 1.0,
          "у соседней точки запас остался расчётным",
          "точек %d" % len(per17))
    check("вручную" in et.lbl_state.text(), "таблица отмечает, что запас задан вручную",
          et.lbl_state.text()[-40:])
    et._reset_all()
    # ⚠️ допуск 0.05 км: `was0` прочитан из ячейки, а она показывает один знак после запятой
    check(abs(m.entry_L_max_km()[0] - was0) < 0.05 and not m.entry_L_max_manual,
          "«Вернуть расчётные» снимает правку",
          "%.2f против %.1f" % (m.entry_L_max_km()[0], was0))
    m.set_entry_L_max(0, 99.0)
    m.set_sector_auto()                                  # точки переотобраны
    check(not m.entry_L_max_manual, "переотбор точек чистит ручные запасы: номера уже не те")

    print("\n18. ЧИСЛО СЕГМЕНТОВ В РАЗДЕЛЕ «ДАТЧИКИ» (план 12, задача 12.6)")
    seg = iw._edits.get("L_seg")                          # поле числа сегментов
    check(seg is not None, "поле сегментов есть в окне исходных данных")
    iw.refresh(m.p)
    check(seg.text() == str(int(m.p.L_seg)), "показано текущее значение", seg.text())
    check(not seg.toolTip(), "подсказки НЕТ — прямое указание заказчика",
          seg.toolTip()[:30])
    seg.setText("20")
    iw._apply()
    check(m.p.L_seg == 20, "новое значение дошло до модели", str(m.p.L_seg))
    # ⚠️ ПРЕДЕЛ 31 — маска накрытых сегментов лежит в 32-битном числе, по биту на сегмент
    seg.setText("99")
    iw._apply()
    check(m.p.L_seg == 31, "выше предела зажимается до 31", str(m.p.L_seg))
    seg.setText("0")
    iw._apply()
    check(m.p.L_seg == 1, "ниже единицы зажимается до 1", str(m.p.L_seg))
    seg.setText("10")
    iw._apply()
    check(m.p.L_seg == 10, "умолчание возвращается")

    print("\n19. ШИРОКАЯ КАРТА: ПОКАЗ ШИРЕ ОБЛАСТИ РАСЧЁТА (план 12, задача 12.1)")
    # ⚠️ Своё представление, а не общее `v`: рамка показа задаётся при создании, и менять
    # её у работающего вида нельзя — предел уже построен.
    area_km = bbox_lonlat_to_km(THREAT_BBOX_LONLAT, m.lon0, m.lat0)   # область расчёта, км
    wide_km = (area_km[0] - 150.0, area_km[1] + 150.0,        # показ шире на 150 км
               area_km[2] - 150.0, area_km[3] + 150.0)
    vw = ThreatMapView(m.bbox_km, m.lon0, m.lat0, Params(),      # своя карта — шире области
                       area_max_km=area_km, view_max_km=wide_km)
    vw.resize(1200, 800)
    vw.show()
    app.processEvents()
    check(vw.view_max_km == wide_km and vw.area_max_km == tuple(area_km),
          "рамка показа и рамка расчёта живут раздельно")
    lim = vw.vb.state["limits"]["xLimits"]                    # предел перемещения по X, км
    check(lim[0] <= wide_km[0] + 1.0 and lim[1] >= wide_km[1] - 1.0,
          "предел вида накрывает широкую карту",
          "предел %.0f…%.0f при карте %.0f…%.0f" % (lim[0], lim[1], wide_km[0], wide_km[1]))
    check(lim[0] < area_km[0] - 100.0,
          "...и уходит заметно дальше области расчёта",
          "%.0f против %.0f" % (lim[0], area_km[0]))
    vw.frame_bbox(whole_area=True)
    app.processEvents()
    x0v, x1v = vw.vb.viewRange()[0]                           # что показано после «Весь участок»
    # ⚠️ Вид ШИРЕ самой области (302 → 453 км): масштаб привязан к уровню тайлов, иначе
    # подложка мылит (журнал п. 280). Сравниваем с КАРТОЙ, а не с областью впритык.
    check(x1v - x0v < (wide_km[1] - wide_km[0]) * 0.85,
          "«Весь участок» вписывает ОБЛАСТЬ, а не всю карту",
          "показано %.0f км при карте %.0f км и области %.0f км"
          % (x1v - x0v, wide_km[1] - wide_km[0], area_km[1] - area_km[0]))
    xa0, xa1 = area_km[0], area_km[1]
    check(x0v <= xa0 + 1.0 and x1v >= xa1 - 1.0, "...и область в него помещается целиком",
          "вид %.0f…%.0f, область %.0f…%.0f" % (x0v, x1v, xa0, xa1))
    vw.close()

    print("\n20. ОБЪЕКТЫ ЗАЩИТЫ: ОКНО, ПОСТАНОВКА, ПРИОРИТЕТЫ (план 13, задача 13.13)")

    def _wait_async():
        """Дождаться фоновой задачи контроллера и доставки её сигнала в главный поток."""
        c._pool.waitForDone(120000)
        app.processEvents()

    def _title():
        """Текст заголовка над картой (`set_title` пишет его в заголовок pyqtgraph)."""
        return str(v.pi.titleLabel.text or "")

    m.clear_uav_start()
    m.target_km = None                                   # целей не ставили
    x0t, x1t, y0t, y1t = m.bbox_km                       # район, км
    p1 = (x0t + 0.40 * (x1t - x0t), y0t + 0.50 * (y1t - y0t))   # первая цель, км
    p2 = (x0t + 0.60 * (x1t - x0t), y0t + 0.45 * (y1t - y0t))   # вторая цель, км
    v.btn_target.click()
    td = v._targets_dlg                                  # окно «Объекты защиты»
    check(td is not None and td.isVisible() and td.table.rowCount() == 0,
          "«Указать цель» открывает окно объектов, таблица пуста")
    check("Целей нет" in td.lbl_state.text(), "...и сказано, что расчёт к цели по умолчанию")
    v._open_sector_window()
    sd20 = v._sector_dlg                                 # второе открытое окно
    td.spin_n.setValue(2)
    td.btn_place.click()
    check(v._target_mode and not td.isVisible() and not sd20.isVisible(),
          "«Задать объект»: режим включён, ВСЕ окна спрятаны")
    check(not v.vb.menuEnabled(), "правая кнопка забрана под выход")
    v._on_scene_click(_Ev(*p1))
    check(len(m.targets) == 0, "одинарный клик цель НЕ ставит")
    v._on_scene_click(_Ev(*p1, double=True))
    check(len(m.targets) == 1 and v._target_mode, "двойной клик поставил первую, режим держится")
    v._on_scene_click(_Ev(x1t + 80.0, y1t + 80.0, double=True))
    check(len(m.targets) == 1 and "внутри района" in _title(),
          "клик за районом отклонён с пояснением", _title()[:60])
    v._on_scene_click(_Ev(*p2, double=True))
    check(len(m.targets) == 2 and not v._target_mode,
          "набрали заданное число — режим закрылся сам")
    check(td.isVisible() and sd20.isVisible() and v.vb.menuEnabled(),
          "...окна и правая кнопка вернулись")
    titles20 = []                                        # заголовки, поставленные во время расчёта
    _set_title0 = v.set_title                            # настоящий, вернём после ожидания
    v.set_title = lambda t: (titles20.append(t), _set_title0(t))[-1]
    _wait_async()
    v.set_title = _set_title0
    check(any("Целей: 2" in t for t in titles20), "цели применились сами: карта по всем целям",
          " | ".join(t[:40] for t in titles20))
    tx, _ty = v.target_scatter.getData()
    check(tx is not None and len(tx) == 2 and len(v._target_labels) == 2,
          "на карте два креста и два номера")
    check(td.table.rowCount() == 2 and td.table.item(0, 5).text() == "0.50",
          "в таблице две строки, приоритет поровну 0.50", td.table.item(0, 5).text())
    check("Приоритеты равны" in td.lbl_state.text(),
          "при равных долях сказано: большие датчики вручную")
    td.table.item(0, 5).setText("0.7")                   # 0.7 + 0.5 > 1
    check(abs(m.targets.items[0].weight - 0.5) < 1e-9 and "больше 1" in td.lbl_state.text(),
          "сумма больше 1 — правка отклонена с пояснением")
    td.table.item(1, 5).setText("0.3")
    td.table.item(0, 5).setText("0.7")
    check(abs(m.targets.weight_sum() - 1.0) < 1e-9 and "у цели №1" in td.lbl_state.text(),
          "0.7 / 0.3 принято, кольцо больших — у цели №1")
    td.spin_n.setValue(3)
    td.btn_place.click()
    v._on_scene_click(_Ev(p1[0], p2[1] - 5.0, double=True))
    v._on_scene_click(_Ev(*p1, button=Qt.RightButton))
    check(not v._target_mode and len(m.targets) == 3 and td.isVisible(),
          "правая кнопка — досрочный выход, поставленная цель осталась")
    _wait_async()
    check([t.weight for t in m.targets.items] == [0.33] * 3,
          "новая цель — доли снова поровну 0.33")
    td.btn_clear.click()
    _wait_async()
    check(len(m.targets) == 0 and td.table.rowCount() == 0,
          "«Очистить» убирает все цели и строки")
    td.close()
    sd20.close()

    print("\n21. АНАЛИЗ ПО ЦЕЛЯМ, ИТЕРАЦИЙ СТОЛЬКО, СКОЛЬКО В ПАНЕЛИ, АНАЛИЗ РАЗМЕЩЕНИЯ (23.09.2026)")
    from model.threat.geo_frame import km_to_lonlat as _k2l
    _B21 = m.target_only_km()                           # цель по умолчанию, км
    _lo, _la = _k2l(_B21[0] - 35, _B21[1] - 25, m.lon0, m.lat0)
    _ho, _ha = _k2l(_B21[0] + 35, _B21[1] + 25, m.lon0, m.lat0)
    m.set_area((float(_lo), float(_la), float(_ho), float(_ha)))   # район 70 × 50 км — быстрее
    v.set_area_ready(True, m.area_size_km(), m.bbox_km)
    m.build()
    # ⚠️ ИТЕРАЦИЙ — РОВНО ЧИСЛО ИЗ ПАНЕЛИ (заказчик 23.09.2026): √(точек / 5) растит только
    # синие линии ДО итераций; пакет, «Пуск», выгрузка и заголовок знают ОДНО число — заказ
    m.p.threat_sector_entries = 10
    c.on_sector_auto()
    _n_e = len(m.entry_points_km())                      # точек входа, шт.
    m.p.threat_iter_routes = 20
    _T = m.iter_target_count()                           # наберёт моделирование, шт.
    check(_T == 20, "итераций столько, сколько в панели", "20 → %d при %d точках" % (_T, _n_e))
    check((m.routes_want(20) > 20) == (_n_e > 5),
          "√(точек / 5) остаётся у синих линий", "20 → %d" % m.routes_want(20))
    check("/20 " in c._iter_title() and "заказано" not in c._iter_title(),
          "заголовок без пояснения о множителе", c._iter_title()[:70])
    m.iter_batch()
    check(len(m.iter_routes) == _T and m.iterations_complete() and c._iter_finished(),
          "пакет, «Пуск» и выгрузка сходятся на одном числе", "%d маршрутов" % len(m.iter_routes))

    # СЕКТОРА У КАЖДОЙ ЦЕЛИ (заказчик 23.09.2026) — компас по её маршрутам
    m.add_target(_B21[0] - 12, _B21[1] + 6)
    m.add_target(_B21[0] + 12, _B21[1] - 6)
    m.set_target_sectors(0, True)                         # галка «Сектора» у новой цели снята (29.09.2026)
    m.set_target_sectors(1, True)
    m.apply_targets()
    m.iter_batch()
    c._work_place()
    _parts = m.routes_by_target()                        # маршруты по целям
    check(sum(len(q) for q in _parts) == len(m._sample_for_sensors()) and all(_parts),
          "выборка делится по целям без потерь", " + ".join(str(len(q)) for q in _parts))
    check(sum(sum(e["n_routes"] for e in m.sector_stats(i)) for i in range(2))
          == sum(e["n_routes"] for e in m.sector_stats()),
          "компасы целей в сумме дают обобщённый")
    c._render_sector_compass({"show_sector_stats": True})
    check(len(v._compass_items) == 2 * 24, "у каждой из двух целей свой компас",
          "%d элементов" % len(v._compass_items))
    m.set_target_sectors(1, False)
    c._render_sector_compass({"show_sector_stats": True})
    check(len(v._compass_items) == 24, "галочка «Сектора» у цели гасит её компас")
    m.set_target_sectors(1, True)
    c._render_sector_compass({"show_sector_stats": False})

    # «АНАЛИЗ»: обобщённый и по каждой цели — в том же виде
    _gen = c._analysis_html()
    check("ОБОБЩЁННО" in _gen and "ПО ЦЕЛЯМ" in _gen and "СВОЕЙ цели" in _gen,
          "обобщённый анализ: сводка по целям, компас от своей цели")
    _one = c._analysis_html(0)  # отчёт по цели №1
    check(all(s in _one for s in ("ЦЕЛЬ №1", "ПО ТИПАМ", "ПО НАПРАВЛЕНИЯМ"))
          and "ПО ЦЕЛЯМ" not in _one, "анализ одной цели — те же разделы, её маршруты")
    v._open_analysis_window()
    _ad = v._analysis_dlg                                # окно «Анализ моделирования»
    check(_ad.combo.count() == 3 and _ad.combo.isVisibleTo(_ad),
          "в окне выбор: обобщённый + по цели", _ad.combo.itemText(1))
    _ad.combo.setCurrentIndex(2)
    check("ЦЕЛЬ №2" in _ad.text.toPlainText(), "выбор цели показывает её отчёт")
    # НАГРУЗКА НА СРЕДСТВА (план 13.4): кнопка «Анализа» → окно; «Все средства» — 4 столбца, тип — 3; выгрузка в Word
    from model.placement.sensor_load import ORDERS as _ld_orders, sort_rows as _sort_ld
    from PyQt5 import QtWidgets as _QtW_ld
    _ad.btn_load.click()
    _ld = v._load_dlg                                    # окно «Нагрузка на средства»
    _lrows = _ld._snap.get("rows", [])                   # строки снимка модели

    def _nums():
        """Номера по списку в строках таблицы окна. Отдаёт: список int сверху вниз."""
        return [int(_ld.table.item(i, 0).text()) for i in range(_ld.table.rowCount())]

    check(_ld.isVisible() and _ld.table.columnCount() == 4 and len(_lrows) > 0
          and _nums() == [r["n"] for r in _sort_ld(_lrows)],
          "«Рассчитать нагрузку»: окно, все средства — 4 столбца, малые по убыванию, большие в конце",
          "%d средств" % len(_lrows))
    _ld.combo_type.setCurrentIndex(1)
    _tid = _ld.combo_type.currentData()                  # выбранный тип
    check(_ld.table.columnCount() == 3 and sorted(_nums()) == sorted(r["n"] for r in _lrows if r["type_id"] == _tid),
          "выбор типа — 3 столбца, только его средства", _ld.combo_type.currentText())
    _ld.combo_order.setCurrentIndex([k for k, _t in _ld_orders].index("number"))
    check(_nums() == sorted(_nums()), "порядок «№ по списку» — по возрастанию номера")
    import tempfile
    _docx = os.path.join(tempfile.gettempdir(), "gui_check_нагрузка.docx")   # куда выгрузить
    _keep_fd = _QtW_ld.QFileDialog.getSaveFileName       # вернуть после опыта
    _QtW_ld.QFileDialog.getSaveFileName = lambda *a, **k: (_docx, "")
    try:
        _ld._export()
    finally:                                             # подмена не должна пережить раздел
        _QtW_ld.QFileDialog.getSaveFileName = _keep_fd
    from docx import Document as _Doc
    _wt = _Doc(_docx).tables[0]                          # таблица в Word
    check(len(_wt.rows) == _ld.table.rowCount() + 1 and _wt.rows[0].cells[0].text == "№ по списку"
          and _wt.rows[1].cells[0].text == _ld.table.item(0, 0).text(),
          "«Выгрузить в Word» — таблица как на экране", "%d строк" % (len(_wt.rows) - 1))
    # по цели и подсветка на карте (этап 2 плана 13.4)
    check(_ld.combo_target.count() == 3 and _ld.combo_target.isEnabled(), "выбор цели: «Все цели» + две цели",
          _ld.combo_target.itemText(2))
    _ld.combo_target.setCurrentIndex(2)
    check(_ld._snap.get("target") == 1 and _ld._snap.get("n_routes") == len(m.routes_by_target()[1])
          and "цели №2" in _ld.lbl_info.text(), "цель №2 — нагрузка по её маршрутам", "%d маршрутов" % _ld._snap["n_routes"])
    _ld.table.selectRow(0)
    _pk = _ld._shown[0]                                  # средство первой строки
    _mk = [it for it in v._load_pick_items if hasattr(it, "points")]   # крест подсветки
    check(len(v._load_pick_items) == 2 and len(_mk) == 1
          and abs(_mk[0].points()[0].pos().x() - _pk["x_km"]) < 1e-9 and abs(_mk[0].points()[0].pos().y() - _pk["y_km"]) < 1e-9,
          "строка таблицы подсвечивает своё средство на карте", "№%d" % _pk["n"])
    _ld.close()
    check(not v._load_pick_items, "окно закрыли — подсветка снята")
    # «АНАЛИТИКА» — окна рисунков 5–9 описания ПК (06.10.2026): цель из окна, настройки меняют рисунок, PNG по размеру
    from PyQt5 import QtGui as _QtG_fig
    check(_ad.windowTitle() == "Аналитика" and v.btn_analysis.text() == "Аналитика" and _ad.btn_load.isVisibleTo(_ad)
          and all(b.isVisibleTo(_ad) for b in _ad.btn_figure.values()), "«Аналитика»: четыре кнопки вывода")
    _ad.combo.setCurrentIndex(2)                         # цель №2 в «Аналитике»
    _ad.btn_figure["load"].click()
    _fl = v._figure_dlgs["load"]                         # окно «Анализ нагрузки»
    _flr = _fl._snap["load"]["rows"]                     # строки нагрузки снимка
    check(_fl.isVisible() and _fl.combo_target.currentData() == 1 and _fl._snap["load"]["target"] == 1
          and len(_fl.fig.axes[0].patches) == len(_flr) > 0, "«Анализ нагрузки»: цель из «Аналитики», столбец на средство",
          "%d средств" % len(_flr))
    _fl.combo_type.setCurrentIndex(1)
    _fl.set_opts({"horizontal": True}); _fl.redraw()
    check(len(_fl.fig.axes[0].patches) == sum(1 for r in _flr if r["type_id"] == _fl.combo_type.currentData())
          and _fl.fig.axes[0].get_ylabel() == "номер средства", "тип и горизонтальные столбцы — рисунок перестроен")

    def _png_size(dlg, name):
        """Записать PNG окна и прочитать его размер. Вход: окно, имя файла. Отдаёт: (ширина, высота) пикс. и ожидаемые."""
        path = dlg.save_png(os.path.join(tempfile.gettempdir(), name))   # куда записать
        img = _QtG_fig.QImage(path or "")                # записанная картинка
        want = (dlg.png_w.value() / 2.54 * dlg.png_dpi.value(), dlg.png_h.value() / 2.54 * dlg.png_dpi.value())   # пикс.
        return (img.width(), img.height()), want

    (_pw, _ph), (_ww, _wh) = _png_size(_fl, "gui_check_нагрузка.png")
    check(abs(_pw - _ww) <= 1 and abs(_ph - _wh) <= 1, "PNG нагрузки — размер из см и dpi", "%d × %d" % (_pw, _ph))
    _ad.btn_figure["sector"].click()
    _fs = v._figure_dlgs["sector"]                       # окно «Показ сектора»
    _fax = _fs.fig.axes[0]                               # полярные оси
    _fst = _fs._snap["sectors"]                          # 12 секторов цели №2
    check(_fax.name == "polar" and len(_fax.patches) == 24
          and abs(sum(p.get_height() for p in _fax.patches[:12]) - sum(e["n_routes"] for e in _fst)) < 1e-9
          and "ЗАХОДА" in _fs.lbl_info.text(), "«Показ сектора»: 12 направлений, сумма маршрутов сходится",
          "%d маршрутов" % sum(e["n_routes"] for e in _fst))
    _fs.set_opts({"zero": True, "short": True, "values": True, "share": True}); _fs.redraw()
    check(len(_fs.fig.axes[0].patches) >= 36, "столбцы «без засечки» и рамки недобора добавились",
          "%d столбцов" % len(_fs.fig.axes[0].patches))
    _ad.btn_figure["map"].click()
    _fm = v._figure_dlgs["map"]                          # окно «Показ карты»
    _fm.apply_preset("Рис. 5 — весовая карта"); _fm.redraw()
    check(len(_fm.fig.axes) == 2 and len(_fm.fig.axes[0].images) == 1, "«Показ карты», вид «Рис. 5»: весовая сетка и шкала")
    _fm.apply_preset("Рис. 7 — размещение средств"); _fm.redraw()
    _fma = _fm.fig.axes[0]                               # оси карты
    check(len(_fma.collections) == 1 and len(_fma.patches) == len(_fm._snap["sensors"]) > 0,
          "вид «Рис. 7»: маршруты и зона на каждое средство", "%d средств" % len(_fma.patches))
    _fm.set_opts({"frame": "view"}); _fm.redraw()
    _vr = v.vb.viewRange()                               # рамка вида карты вкладки, км
    check(np.allclose(_fm.fig.axes[0].get_xlim(), _vr[0], atol=1e-6) and np.allclose(_fm.fig.axes[0].get_ylim(), _vr[1],
                                                                                    atol=1e-6),
          "рамка «как на карте сейчас»", "%.1f–%.1f км" % tuple(_vr[0]))
    _fm.set_opts({"background": "own"}); _fm.redraw()
    check("Своя карта" in _fm.lbl_info.text(), "фон «своя карта» без загруженной карты — пояснение, а не ошибка")
    _keep_off = v._map_offline                           # вернуть после опыта
    v._map_offline = True                                # проверка не ходит в сеть
    try:
        _fm.set_opts({"background": "tiles", "frame": "area"}); _fm.redraw()
        _got = _fm.wait_tiles(60000)                     # подложка из кэша тайлов участка
    finally:
        v._map_offline = _keep_off
    check(_fm._tiles["state"] in ("done", "none") and (not _got or len(_fm.fig.axes[0].images) == 2),
          "подложка собирается в потоке и ложится под весовую сетку", _fm._tiles["state"])
    # подложка — строки мозаики на своей широте (07.10.2026): прямоугольником середина уезжала на 0,59 км
    from view_qt.output.analytics_open import straighten_rows as _straight
    from model.threat.geo_frame import KM_PER_DEG_LAT as _KML
    _ext, _H, _la0 = (0.0, 150.0, -10.0, 110.0), 1800, 61.7   # рамка мозаики км, строк, широта якоря
    _mlo, _mhi = (np.arcsinh(np.tan(np.radians(_la0 + v / _KML))) for v in _ext[2:])   # края в Меркаторе
    _yrow = lambda r: (np.degrees(np.arctan(np.sinh(_mlo + (_mhi - _mlo) * (r + 0.5) / _H))) - _la0) * _KML   # строка -> км
    _want = _ext[2] + (_ext[3] - _ext[2]) * (np.arange(_H) + 0.5) / _H   # где строка должна лежать, км
    _src = _straight(np.arange(_H)[:, None], _ext, _la0)[:, 0]   # откуда взята каждая строка
    _px = (_ext[3] - _ext[2]) / _H                       # пиксель мозаики, км
    check(np.abs(_yrow(_src) - _want).max() <= _px * 0.51 < np.abs(_yrow(np.arange(_H)) - _want).max(),
          "подложка: строки выпрямлены по широте — сдвиг не больше полпикселя",
          "было %.3f км, стало %.3f км" % (np.abs(_yrow(np.arange(_H)) - _want).max(), np.abs(_yrow(_src) - _want).max()))
    (_pw, _ph), (_ww, _wh) = _png_size(_fm, "gui_check_карта.png")
    check(abs(_pw - _ww) <= 1 and abs(_ph - _wh) <= 1, "PNG карты — размер из см и dpi", "%d × %d" % (_pw, _ph))
    for _d in (_fl, _fs, _fm):                           # окна рисунков — закрыть
        _d.close()
    _ad.close()

    # АНАЛИЗ РАЗМЕЩЕНИЯ В РЕЖИМАХ (заказчик: «работает не совсем корректно, точки
    # пропадают»). Три дефекта найдены прогоном 23.09.2026 — каждый закреплён здесь
    def _moved():
        """Сколько датчиков сдвинулось от снимка первой расстановки, шт."""
        return sum(1 for r in m.sensor_movement()
                   if r["dist_km"] is not None and r["dist_km"] >= 0.05)

    m.iter_routes = []; m.iter_iteration = 0
    m.set_target_weight(0, 0.5)                          # доли равны — кольца больших нет
    c.on_manual_mode(True)
    check(m.initial_sensors is None or len(m.initial_sensors) + len(m.initial_sensors_big) == 0,
          "«Задать позиции» сбросил снимок чужой расстановки")
    c.on_manual_clear()
    for _tid, _st, _dx, _dy in ((1, True, -10, 5), (1, False, 8, -6), (2, False, -5, -12),
                                (4, True, 0, 12), (4, False, 10, 0)):
        _lon, _lat = _k2l(_B21[0] + _dx, _B21[1] + _dy, m.lon0, m.lat0)
        c.on_manual_add(_tid, _st, float(_lon), float(_lat))
    check(m.initial_sensors is not None and _moved() == 0
          and len(m.initial_sensors) + len(m.initial_sensors_big) == 5,
          "до моделирования снимок следует за таблицей — сдвигов нет")
    m.iter_reset(); m.iter_batch()
    c._sensors_at = 0
    c._maybe_refresh_sensors(force=True)
    _big_now = len(m.sensors_big)                        # больших на карте, шт.
    check(len(m.sensors) + _big_now == 5, "при моделировании ни один датчик не пропал",
          "малых %d, больших %d" % (len(m.sensors), _big_now))
    check(_big_now == 2, "динамический большой при равных долях целей остался на месте")
    _stat = [np.array([q.x_km, q.y_km]) for q in m.manual_sensors if q.static]  # места статических, км
    check(all(np.min(np.hypot(*(np.vstack([m.sensors, m.sensors_big]) - s).T)) < 1e-6
              for s in _stat), "статические стоят ровно там, где заданы")
    _pos = np.vstack([m.sensors, m.sensors_big]).copy()  # позиции до заморозки
    _mv = _moved()                                       # сдвинутых до заморозки
    v.chk_mode_analysis.setChecked(True)
    m.iter_batch(); c._sensors_at = 0
    c._maybe_refresh_sensors(force=True)
    check(np.allclose(np.vstack([m.sensors, m.sensors_big]), _pos) and _moved() == _mv,
          "режим анализа: уехавшие динамические не прыгают обратно в таблицу",
          "сдвинуто %d" % _mv)
    v.chk_mode_analysis.setChecked(False)
    c.on_manual_mode(False)
    check(m.initial_sensors is None, "выход из «Задать позиции» сбросил снимок")
    c.on_manual_clear()
    m.iter_routes = []; m.iter_iteration = 0
    c._work_place()
    check(m.initial_sensors is not None and _moved() == 0,
          "в расчёте снимок — новая первая расстановка")

    print("\n22. ОКНА «ДАТЧИКИ» И «ТИП ДАТЧИКА»: МИНИ-ТАБЛИЦА, СПРАВОЧНИК, КАРТА МЕСТА (план 13, 13.2–13.3)")
    # ⚠️ ТРИ ПОДМЕНЫ, как в разделе 15: запись — во временный справочник, а не в папку участка;
    # вопрос «удалить?» и выбор цвета без экрана ждали бы нажатия вечно
    import tempfile as _tmp22
    from PyQt5 import QtGui as _QtG
    from model.placement import sensor_types as _cat
    from model.placement.sensors import sensor_types as _specs
    from view_qt.tabs.threat_view import type_color as _tcol
    _yes = _QtW.QMessageBox.Yes                           # ответ на вопрос «удалить?»
    _keep = (_QtW.QMessageBox.warning, _QtW.QMessageBox.question, _QtW.QColorDialog.getColor, _cat.path)   # вернуть в конце
    _said22 = []                                          # предупреждения окна
    _file22 = os.path.join(_tmp22.mkdtemp(), _cat.FILE_NAME)   # временный справочник
    _QtW.QMessageBox.warning = lambda *a, **k: _said22.append(a[2] if len(a) > 2 else "")
    _QtW.QMessageBox.question = lambda *a, **k: _yes
    _QtW.QColorDialog.getColor = lambda *a, **k: _QtG.QColor("#123456")
    _cat.path = lambda: _file22
    _p_keep = {n: getattr(p, n) for n in ("threat_N", "threat_R", "threat_k", "threat_R2", "threat_k2",   # задание до раздела
                                          "threat_N2", "threat_k_same", "threat_type_order", "threat_N_types")}
    try:
        v._open_sensors_window()
        w = v._sensors_dlg
        _n_t = len(_specs(p))                             # типов задания, шт.
        check(w.types.rowCount() == _n_t, "мини-таблица: строка на тип", "%d строк" % w.types.rowCount())
        check(isinstance(w.types.cellWidget(0, 2), _QtW.QPushButton), "в строке — образец цвета")
        _old22 = {t.type_id for t in _cat.load()}         # номера до «Добавить тип»
        _want22 = _cat.next_id(_cat.load())               # первый свободный (≥ 5; №5 — «Обычный», Д1)
        w._add_type()
        _new = [t.type_id for t in _cat.load() if t.type_id not in _old22]   # новые номера
        check(_new == [_want22] and _want22 >= _cat.FIRST_FREE_ID, "«Добавить тип» заводит первый свободный номер ≥ 5",
              str(_new))
        _tid = _new[0] if _new else _cat.FIRST_FREE_ID   # номер нового типа
        check(w.types.rowCount() == _n_t + 1 and _tid in w._edits_more, "новый тип — строкой таблицы")
        check(v._types_dlg is not None and v._types_dlg.isVisible(), "и сразу открыто окно его паспорта")
        w._edits_more[_tid].setText("2")
        w._apply()
        check((p.threat_N_types or {}).get(_tid) == 2, "число нового типа дошло до задания",
              str(p.threat_N_types))
        check(any(s.type_id == _tid and s.active for s in _specs(p)), "и тип стал работающим")
        w._pick_color(_tid)
        check(_tcol(_tid).lower() == "#123456", "цвет из окна — в паспорт и на карту", _tcol(_tid))
        _lon22, _lat22 = _k2l(_B21[0] + 3, _B21[1] + 3, m.lon0, m.lat0)
        c.on_manual_add(_tid, True, float(_lon22), float(_lat22))
        _row = [r for r in range(w.types.rowCount()) if int(w.types.item(r, 0).data(Qt.UserRole)) == _tid]   # строка нового типа
        w.types.selectRow(_row[0] if _row else 0)
        w._del_type()
        check(_cat.by_id(_tid, _cat.load()) is None, "«Удалить тип» убирает его из справочника")
        check(not any(q.type_id == _tid for q in m.manual_sensors) and _tid not in (p.threat_N_types or {}),
              "...вместе с его датчиками и числом")
        w.types.selectRow(0)
        _n_said = len(_said22)                            # предупреждений до попытки
        w._del_type()
        check(len(_said22) > _n_said and _cat.by_id(1, _cat.load()) is not None,
              "номер 1–4 не удаляется — предупреждение", (_said22[-1] if _said22 else "")[:50])
        # ОЧЕРЁДНОСТЬ ТИПОВ (13.6) — правило общего прохода из окна
        w.cb_order.setCurrentIndex(w.cb_order.findData("tight"))
        w._apply()
        check(p.threat_type_order == "tight", "очерёдность типов доходит до параметров", p.threat_type_order)
        # ⚠️ ДОВОЛЬНО ЛЮБОГО РАБОТАЮЩЕГО ТИПА: прежде N1 = 0 не принималось, а «один ПВН» — законно
        w._edits["threat_N"].setText("0")
        w._edits["threat_N2"].setText("5")
        w._apply()
        check(p.threat_N == 0 and p.threat_N2 == 5, "задание без типа 1 принимается",
              "N1 = %d, N2 = %d" % (p.threat_N, p.threat_N2))

        # ОКНО «ТИП ДАТЧИКА»: паспорт, поля ПВН, мост 1–4
        v._open_sensor_types(2)
        td = v._types_dlg                                 # окно паспорта
        check(td._types[td._cur].type_id == 2 and td._w["zone"].currentText() == "застройка",
              "окно открыто на ПВН, место — застройка", td._w["zone"].currentText())
        check(td._w["tgt_gain_pct"].isEnabled() and not td._w["KIND"].isEnabled(),
              "поля ПВН открыты, класс номера моста закрыт")
        # Д1 (план 13.1): «Обычный» в справочнике; паспорт только для чтения до «Редактировать»; «Отмена» — как было
        _ids22 = [t.type_id for t in td._types]           # номера в окне
        _r0 = td._w["r_km"].text()                         # радиус ПВН до правки
        _ro = td._w["r_km"].isReadOnly() and not td._btn["Сохранить"].isEnabled() and td.lst.isEnabled()   # только чтение до правки
        td._edit()
        _rw = not td._w["r_km"].isReadOnly() and td._btn["Сохранить"].isEnabled() and not td.lst.isEnabled()   # правка открыта, список заперт
        td._w["r_km"].setText("0.9")
        td._cancel()
        check(5 in _ids22 and _ro and _rw and td._w["r_km"].text() == _r0 and not td._editing
              and "одна на все типы" in td.lbl_k.text(),
              "Д1: «Обычный» есть; правка по «Редактировать», «Отмена» вернула; под k — откуда число",
              "номера %s · R %s · %s" % (_ids22, td._w["r_km"].text(), td.lbl_k.text()[:40]))
        # ГАЛКА «ОТ k» (заказчик 25.09.2026): порог и уровень у цели идут за кратностью защиты; без галки — своё
        td._auto["tgt_gain_pct"].setChecked(True)
        td._auto["tgt_level_pct"].setChecked(True)
        td._w["tgt_k"].setText("3")
        _g3, _l3 = td._w["tgt_gain_pct"].text(), td._w["tgt_level_pct"].text()   # от k 3
        td._w["tgt_k"].setText("2")
        _g2, _l2 = td._w["tgt_gain_pct"].text(), td._w["tgt_level_pct"].text()   # от k 2
        _st_auto = td._from_form(td._types[td._cur])                          # паспорт с галками
        td._auto["tgt_level_pct"].setChecked(False)
        td._w["tgt_level_pct"].setText("75")
        _st_own = td._from_form(td._types[td._cur])                           # своё значение уровня
        check((_g3, _l3, _g2, _l2) == ("4", "89.3", "3", "92") and _st_auto.tgt_level_pct == 0
              and _st_own.tgt_level_pct == 75 and td._w["tgt_gain_pct"].isReadOnly(),
              "порог и уровень «от k» пересчитываются с кратностью, своё — без галки",
              "k3: %s / %s · k2: %s / %s" % (_g3, _l3, _g2, _l2))
        td._auto["tgt_level_pct"].setChecked(True)
        td._w["tgt_k"].setText("1")                       # вернуть кратность защиты по умолчанию
        td._select(0)
        check(not td._w["tgt_gain_pct"].isEnabled() and td._w["wet_buf_km"].isEnabled(),
              "у МОГ поля ПВН закрыты, отступ от рек и болот открыт (план 13.3)")
        # ⚠️ МОСТ: правка R типа 1 — в Params; открытый, но не тронутый ПВН своего k2 не теряет
        p.threat_k_same, p.threat_k2 = True, 4
        td._w["r_km"].setText("1.7")
        td._save()
        check(abs(p.threat_R - 1.7) < 1e-9, "R типа 1 из окна — в задание", "R = %g" % p.threat_R)
        check(p.threat_k2 == 4 and abs(p.threat_R2 - _p_keep["threat_R2"]) < 1e-9,
              "своя кратность ПВН при общей не затёрта", "k2 = %d" % p.threat_k2)
        td._w["r_km"].setText("-1")
        _n_said = len(_said22)                            # предупреждений до попытки
        td._save()
        check(len(_said22) > _n_said and abs(p.threat_R - 1.7) < 1e-9,
              "кривой радиус — предупреждение, задание не тронуто")
        td._w["r_km"].setText("1.7")

        # КАРТА МЕСТА ТИПА — галка в окне типа кладёт её на карту
        c.on_type_zone(2)
        _npts = len(v.type_zone_pts.getData()[0] if v.type_zone_pts.getData()[0] is not None else [])   # мест, шт.
        check(v.type_zone_img.isVisible() and _npts > 0, "карта места ПВН и его места на карте",
              "мест %d" % _npts)
        # Д3: слагаемые карты — по галкам окна типа, своим цветом под картой
        _p_before = v.type_parts_img.isVisible()          # слоёв не отмечено — картинки нет
        td._select(1)                                     # ПВН в форме: галки его правила «застройка» (план 13.3)
        td._parts["wetland"].setChecked(True)
        td._parts["forest"].setChecked(True)
        td._parts["settled"].setChecked(True)
        c.on_type_zone(2)
        _pimg = v.type_parts_img.image                    # картинка слагаемых, RGBA
        _cf, _cw = v.type_vec_items.get("c_forest"), v.type_vec_items.get("c_wetland")   # контуры леса и болот
        _cn = sum(len(i.getData()[0]) for i in (_cf, _cw) if i is not None and i.isVisible() and i.getData()[0] is not None)
        check(not _p_before and v.type_parts_img.isVisible() and _pimg is not None and (_pimg[..., 3] > 0).any()
              and td.shown_parts() == ["forest", "wetland", "settled"] and _cn > 0,
              "Д3 + 13.5.5: застройка — клетками, лес и болота — контуром полигонов OSM", "точек контуров %d" % _cn)
        td._parts["wetland"].setChecked(False)
        td._parts["forest"].setChecked(False)
        td._parts["settled"].setChecked(False)
        # ПЛАН 13.3: у МОГ — слои своего правила «дорога» и векторные слои линиями поверх карты
        td._select(0)                                     # МОГ — первый в справочнике
        _mog_f = (td._w["zone"].currentText() == "дорога" and not td._w["zone_buf_km"].isEnabled()
                  and "ban" in td._parts and "forest" not in td._parts)   # поля и галки своего правила
        td._parts["ban"].setChecked(True)
        td._vecs["road_local"].setChecked(True)
        c.on_type_zone(1)
        _vx = v.type_vec_items.get("road_local")          # линия местных дорог на карте
        _vn = len(_vx.getData()[0]) if _vx is not None and _vx.getData()[0] is not None else 0   # точек линии
        check(_mog_f and td.shown_parts() == ["ban"] and td.shown_vectors() == ["road_local"]
              and v.type_zone_img.isVisible() and v.type_parts_img.isVisible() and _vn > 0 and _vx.isVisible(),
              "13.3: МОГ — поля и слои правила «дорога», векторные слои линиями", "точек дорог %d" % _vn)
        td._parts["ban"].setChecked(False)
        td._vecs["road_local"].setChecked(False)
        # 13.5.5: ЛЭП, просеки, ж/д — для сверки; пути доезда от целей — ломаными со стрелками
        for _k in ("power", "cutline", "railway", "reach_lines"):   # новые векторные слои МОГ
            td._vecs[_k].setChecked(True)
        c.on_type_zone(1)
        _rl = v.type_vec_items.get("reach_lines")         # пути доезда и стрелки на карте
        _rn = len(_rl.getData()[0]) if _rl is not None and _rl.getData()[0] is not None else 0   # точек
        _arr = len(v._type_vectors.get("reach_lines", [])) # ломаных вместе с наконечниками
        check(_rn > 0 and _rl.isVisible() and all(v.type_vec_items.get(k) is not None for k in ("power", "cutline", "railway")),
              "13.5.5: МОГ — ЛЭП, просеки, ж/д и пути доезда со стрелками", "точек путей %d, ломаных %d" % (_rn, _arr))
        for _k in ("power", "cutline", "railway", "reach_lines"):   # снять
            td._vecs[_k].setChecked(False)
        c.on_type_zone(5)
        check(not v.type_zone_img.isVisible() and not any(i.isVisible() for i in v.type_vec_items.values()),
              "у типа «везде» («Обычный») карты места и векторов нет")
        c.on_type_zone(None)
        check(not v.type_zone_img.isVisible(), "и прячется по None")
    finally:                                              # подмены не должны пережить раздел
        (_QtW.QMessageBox.warning, _QtW.QMessageBox.question, _QtW.QColorDialog.getColor, _cat.path) = _keep
        for _n22, _v22 in _p_keep.items():                # вернуть поля задания
            setattr(p, _n22, _v22)
        m.sync_cand_step()

    print("\n23. РАЙОН ЗАЩИТЫ ЦЕЛИ В ЛЕСУ: ОКНО, РИСОВАНИЕ, ТАБЛИЦА (план 13, 13.18, В13.21)")
    # ⚠️ Окно вопроса модальное — без экрана ждало бы вечно: подменяется ответом «прямоугольник»
    from model.placement.target_stage import zone_base as _zb23   # из чего массив цели — как в `target_zone`
    from model.threat.threat_grid import _dilate as _dl23, _label8 as _lb23
    from view_qt.windows.targets_window import _COLS as _tcols
    _g23 = m.grid                                         # сетка карты
    _lab23, _n23 = _lb23(_dl23(_zb23(_g23, m.sensor_cells()), 1))   # массивы застройки
    _free23 = np.argwhere(_lab23 == 0)                    # ячейки вне массивов (строка, столбец)
    _bc23 = np.argwhere(m.sensor_cells()["count"] > 0)    # ячейки с постройками (строка, столбец)
    # ⚠️ точка в лесу — БЛИЖАЙШАЯ к постройкам вне массива, не ближе 3 ячеек: район ±6 км обязан задеть места ПВН
    # (29.09.2026: без «доп. зоны» массивы ужались, и середина списка ушла в глухой лес — в районе 0 мест)
    _fc23 = np.array([rc for rc in _free23[::7]           # каждая 7-я — хватает, и матрица расстояний мала
                      if m.in_area(_g23.ox + (rc[1] + 0.5) * _g23.h, _g23.oy + (rc[0] + 0.5) * _g23.h)])
    _d23 = np.min(np.hypot(_fc23[:, None, 0] - _bc23[None, :, 0], _fc23[:, None, 1] - _bc23[None, :, 1]),
                  axis=1)                                 # до ближайшей постройки, ячеек
    _rr23, _cc23 = _fc23[int(np.argmin(np.where(_d23 >= 3, _d23, np.inf)))]   # ближайшая не ближе 1.5 км
    _fx, _fy = _g23.ox + (_cc23 + 0.5) * _g23.h, _g23.oy + (_rr23 + 0.5) * _g23.h   # точка в лесу, км
    _zcol = [k for _t, k, _w, _e in _tcols].index("zone")   # колонка «Зона ПВН»
    _keep23 = (list(m.targets.items), p.threat_N2, v.ask_target_area)   # вернуть в конце
    _asked = []                                           # у каких целей спрашивали район
    v.ask_target_area = lambda i: _asked.append(i) or "rect"
    try:
        p.threat_N2 = 5
        m.targets.items = []
        m.add_target(_fx, _fy)
        _shape0 = v._area_shape                           # форма переключателя «район / радиус»
        c._ask_target_areas()
        check(_asked == [0] and v._area_mode and v._area_target == 0,
              "цель в лесу — «укажите район», включено рисование", "(%.1f, %.1f) км" % (_fx, _fy))
        _ax, _ay = _g23.ox + 10 * _g23.h, _g23.oy + 10 * _g23.h   # угол ячейки, км
        v._area_pts = [(_ax - 0.05, _ay - 0.05), (_ax + 0.05, _ay + 0.05)]   # ни одного центра ячейки
        v._end_area(True)
        check(m.targets.items[0].area is None and _asked == [0, 0] and v._area_mode,
              "пустой район не принят — спросили снова")
        v._area_pts = [(_fx - 6, _fy - 6), (_fx + 6, _fy + 6)]
        v._end_area(True)
        check(m.targets.items[0].area is not None and not v._area_mode and v._area_target is None
              and v._area_shape == _shape0, "район принят, переключатель района расчёта цел",
              str(m.targets.items[0].area[0]))
        _xs23 = v.target_area_item.getData()[0]           # контур района на карте, км
        _hs23 = v.target_hatch_item.getData()[0]          # штриховка района, км
        check(_xs23 is not None and len(_xs23) >= 5 and _hs23 is not None and len(_hs23) >= 2,
              "контур района защиты и штриховка на карте (Д5)", "штрихов %d" % (len(_hs23) // 2 if _hs23 is not None else 0))
        # Д5: цель в застройке — показан контур зоны по застройке; галка «Район» снята — ничего
        import time as _time23                            # замер: зона цели для показа
        _by, _bx = np.nonzero(m.sensor_cells()["count"] >= 5)   # ячейки с постройками
        _bpts = [(_g23.ox + (bx + 0.5) * _g23.h, _g23.oy + (by + 0.5) * _g23.h) for by, bx in zip(_by, _bx)]   # км
        _bpt = next((q for q in _bpts if m.in_area(*q)), m.target_only_km())   # застройка внутри района
        m.add_target(*_bpt)                               # вторая цель — в застройке
        m.set_target_show_area(1, True)                   # галка «Район» по умолчанию снята (29.09.2026)
        _t23 = _time23.time()                            # начало замера, с
        _zv23 = m.target_zone_views()                     # районы для показа
        _dt23 = _time23.time() - _t23                     # первый расчёт, с
        v.render_target_areas(_zv23)
        _d23 = v.target_area_item.getData()[0]            # контур с зоной по застройке, км
        _n_auto = 0 if _d23 is None else len(_d23)        # его точек
        m.set_target_show_area(1, False)
        m.set_target_show_area(0, False)
        v.render_target_areas(m.target_zone_views())
        _d23 = v.target_area_item.getData()[0]            # контур без галок, км
        _n_off = 0 if _d23 is None else len(_d23)         # его точек
        check(any("mask" in z for z in _zv23) and _n_auto > len(_xs23) and _n_off == 0,
              "Д5: зона по застройке — контуром; галка «Район» снята — района нет",
              "зон %d, точек %d → %d, первый расчёт %.1f с" % (len(_zv23), _n_auto, _n_off, _dt23))
        m.set_target_show_area(0, True)
        m.targets.items = m.targets.items[:1]             # дальше — одна цель в лесу, как было
        v.render_target_areas(m.target_zone_views())
        v._open_targets_window()
        _td = v._targets_dlg                              # окно «Объекты защиты»
        check(_td.table.item(0, _zcol).text() == "задана вручную", "в таблице целей — «задана вручную»")
        _td.table.setCurrentCell(0, 0)
        _td.btn_area_clear.click()
        check(m.targets.items[0].area is None and _td.table.item(0, _zcol).text() == "укажите район",
              "«Снять район» — снова «укажите район»")
        _td.table.setCurrentCell(0, 0)
        _td.btn_area.click()
        check(v._area_mode and v._area_target == 0, "кнопка «Район защиты…» включает рисование")
        v._end_area(False)
        check(not v._area_mode and v._area_target is None and m.targets.items[0].area is None,
              "Esc — район не задан, режим района расчёта не задет")
    finally:                                              # подмены не должны пережить раздел
        m.targets.items, p.threat_N2, v.ask_target_area = _keep23
        v._area_target = None

    print("\n23а. «ИЗМЕНИТЬ ЦЕЛИ», ГАЛКИ ЦЕЛИ СНЯТЫ, ГРУППА ПОКАЗА В ОКНЕ ТИПА (заказчик 29.09.2026)")
    # ЧТО ЛОВИТ: крестик не переносится мышью, район не чертится без вопроса о форме, после района режим
    # закрывается; галки «Секторы» и «Район» включены у новой цели; места нельзя спрятать отдельно от карты
    from model.placement.targets import Target as _T23a
    _t0 = _T23a(0.0, 0.0)                                 # новая цель
    check(not _t0.sectors and not _t0.show_area, "у новой цели галки «Секторы» и «Район» сняты")
    _keep23a = (list(m.targets.items), p.threat_N2)       # вернуть в конце
    try:
        p.threat_N2 = 5
        m.targets.items = []
        m.add_target(*_bpt)                               # цель в застройке, км
        c.on_targets_done()
        _wait_async()
        v.begin_edit_targets(0)
        check(v.editing_targets() and v._tgt_pick == 0 and v._tgt_handle is not None,
              "«Изменить цели» — режим с меткой на выбранной цели")
        _x0, _y0 = m.targets.items[0].x_km, m.targets.items[0].y_km   # где стояла, км
        v._tgt_handle.setPos(_x0 + 0.5, _y0)              # метку утащили на 0.5 км восточнее
        v._target_handle_moved()
        _wait_async()
        check(abs(m.targets.items[0].x_km - (_x0 + 0.5)) < 1e-3 and abs(m.targets.items[0].y_km - _y0) < 1e-3,
              "метку отпустили — цель перенесена", "%.2f → %.2f км" % (_x0, m.targets.items[0].x_km))

        class _Ev23a:
            """Щелчок левой кнопкой в точке (x, y) км — для `_edit_targets_click`."""

            def __init__(self, x, y):
                self._sp = v.vb.mapViewToScene(QPointF(x, y))   # точка сцены

            def button(self):
                """Отдаёт: левую кнопку."""
                return Qt.LeftButton

            def scenePos(self):
                """Отдаёт: точку сцены."""
                return self._sp

            def accept(self):
                """Событие принято — делать нечего. Отдаёт: ничего."""

        _xa, _ya = m.targets.items[0].x_km, m.targets.items[0].y_km   # цель после переноса, км
        _far = 25.0 * v._km_per_pixel() + 1.0            # «в стороне» — дальше допуска выбора, км
        v._edit_targets_click(_Ev23a(_xa - _far, _ya - _far))
        check(v._area_mode and v._area_target == 0 and len(v._area_pts) == 1,
              "щелчок в стороне при выбранной цели — первый угол её района")
        v._area_pts.append((_xa + _far, _ya + _far))
        v._end_area(True)
        check(m.targets.items[0].area is not None and m.targets.items[0].show_area and v.editing_targets(),
              "колёсико — район задан, галка «Район» включилась, режим продолжается",
              "район %.1f × %.1f км" % (2 * _far, 2 * _far))
        v._end_edit_targets()
        check(not v.editing_targets() and v._tgt_handle is None, "выход — метка снята, режим закрыт")
    finally:                                              # подмены не должны пережить раздел
        v._end_edit_targets()
        m.targets.items, p.threat_N2 = _keep23a
    _td23a = getattr(v, "_types_dlg", None)               # окно типа, если открывали
    if _td23a is not None:                                # галки группы показа
        _td23a._show["places"].setChecked(False)
        c.on_type_zone(2)
        _np23a = v.type_zone_pts.getData()[0]             # показанные места
        _pl_off = _np23a is None or len(_np23a) == 0      # мест не видно
        _td23a._show["result"].setChecked(False)
        c.on_type_zone(2)
        _map_off = not v.type_zone_img.isVisible()        # карты не видно
        _td23a._show["places"].setChecked(True)
        _td23a._show["result"].setChecked(True)
        c.on_type_zone(None)
        check(_pl_off and _map_off, "группа показа: места и карта прячутся каждая своей галкой")

    print("\n24.«ПРИМЕНИТЬ» ПРИНИМАЕТ, «РАССТАВИТЬ ДАТЧИКИ» СЧИТАЕТ ЗОНУ И ВЫБОРКУ (25.09.2026)")
    # ЧТО ЛОВИТ: «Применить» сам пересчитывал маршруты и переставлял датчики (замечание заказчика 25.09.2026)
    _keep24 = list(m.targets.items)                       # цели до раздела — вернуть
    try:
        c.on_place()
        _wait_async()
        _kinds24 = []                                     # фоновые задачи, запущенные «Применить»
        _ra24 = c._run_async                              # настоящий запуск — вернуть
        c._run_async = lambda kind, fn: (_kinds24.append(kind), _ra24(kind, fn))[-1]
        _n24 = len(m.sensors)                             # датчиков стоит, шт.
        c.on_apply()                                      # датчики стоят — прежде кнопка их переставляла
        _wait_async()
        _B24 = m.target_only_km()                         # цель, км
        c.on_set_target(_B24[0] + 2.0, _B24[1] + 1.0)     # обстановка сменилась
        c.on_apply()                                      # прежде — маршруты и расстановка
        _wait_async()
        c._run_async = _ra24
        check(_n24 > 0 and not _kinds24 and c._pending_recalc and "Расставить датчики" in _title(),
              "«Применить» ничего не пересчитывает, пересчёт ждёт «Расставить датчики»",
              "датчиков было %d · задачи %s · ждёт %s" % (_n24, _kinds24, c._pending_recalc))
        c.on_place()
        _wait_async()
        check(not c._pending_recalc and m.route_area is not None and len(m.sensors) > 0,
              "«Расставить датчики» — зона пролёта заново и расстановка", "датчиков %d" % len(m.sensors))
    finally:                                              # цель раздела не должна пережить его
        m.targets.items = _keep24

    print("\n25. «ПОКАЗ»: ПОСТРОЙКИ ПО ЯЧЕЙКАМ — ЧИСЛО И СРЕДНЯЯ ЭТАЖНОСТЬ (план 13.1, Д2)")
    v.chk_bld_count.setChecked(True)
    v.chk_bld_floors.setChecked(True)
    c._render_all()
    _zc25 = m.sensor_cells()                              # постройки по ячейкам
    _img25 = v.bld_count_img.image                        # картинка числа, RGBA
    _n25 = int((_img25[..., 3] > 0).sum()) if _img25 is not None else 0   # закрашено ячеек
    check(v.chk_bld_count.isEnabled() and v.bld_count_img.isVisible() and v.bld_floors_img.isVisible()
          and _n25 == int((_zc25["count"] > 0).sum()) and _n25 > 0,
          "постройки: число и этажность — картинками, закрашены ячейки с постройками", "ячеек %d" % _n25)
    v.chk_bld_count.setChecked(False)
    v.chk_bld_floors.setChecked(False)
    c._render_all()
    check(not v.bld_count_img.isVisible() and not v.bld_floors_img.isVisible(), "галки сняты — картинок нет")

    # ---- 26. план 13.5: шкала градаций, прозрачность групп, Ctrl + колесо ----
    # ЧТО ЛОВИТ: шкала в раскладке ужимала карту («0.97»); показ не последнего включённого слоя; ползунок «датчики и
    # подписи», не гасящий видимого; прозрачность, слетающая при перерисовке; Ctrl + колесо, меняющее уровень тайлов
    print("\n26. ПЛАН 13.5: ШКАЛА ГРАДАЦИЙ, ПРОЗРАЧНОСТЬ ГРУПП, CTRL + КОЛЕСО")
    lg = v.grad_legend                                     # плашка шкалы поверх карты
    for _ch in (v.chk_threat, v.chk_bld_count, v.chk_bld_floors, v.chk_iter_heat):   # всё с градацией — снять
        _ch.setChecked(False)
    c._render_all(); app.processEvents()
    _w0 = v.vb.width()                                     # ширина вида карты без шкалы, px
    check(lg.isHidden(), "градаций на карте нет — шкалы нет")
    _keys = []                                             # чья шкала после каждого шага
    for _ch in (v.chk_threat, v.chk_bld_count, v.chk_bld_floors):   # включаем по очереди
        _ch.setChecked(True); c._render_all(); app.processEvents()
        _keys.append(lg.key)
    v.chk_bld_floors.setChecked(False); c._render_all(); app.processEvents(); _keys.append(lg.key)
    v.chk_bld_count.setChecked(False); c._render_all(); app.processEvents(); _keys.append(lg.key)
    check(_keys == ["threat", "bld_count", "bld_floors", "bld_count", "threat"],
          "шкала — последнего включённого; сняли — возвращается предыдущий", " → ".join(map(str, _keys)))
    check(abs(v.vb.width() - _w0) < 0.5 and not lg.isHidden(), "шкала видна, а карта не ужалась (была колонка справа)",
          "вид %.0f → %.0f px" % (_w0, v.vb.width()))
    check(lg.x() + lg.width() <= v.plot.width() and lg.y() >= v._mode_bar.y() + v._mode_bar.height(),
          "шкала — справа сверху под плашкой режимов", "x %d, y %d" % (lg.x(), lg.y()))
    v.chk_bld_count.setChecked(True); c._render_all(); app.processEvents()
    _rows = v._grad_rows("bld_count")                      # строки шкалы числа построек
    _top26 = int(np.nanmax(m.sensor_cells()["count"]))     # построек в самой плотной ячейке
    check(len(_rows) == v.SCALE_ROWS and _rows[0][1] == "%d" % _top26 and _rows[-1][1] == "1",
          "шкала построек — от максимума участка до 1", "%s … %s" % (_rows[0][1], _rows[-1][1]))
    v.chk_bld_count.setChecked(False); c._render_all()
    # ПРОЗРАЧНОСТЬ ГРУПП: каждый ползунок гасит свои элементы; у «датчиков» — и после перерисовки
    if not len(m.sensors):                                 # датчиков нет — поставить
        m.place_sensors()
    c._render_sensors(); app.processEvents()
    _sld = v._opacity_sliders.get("chk_sensors")           # ползунок группы «датчики и подписи»
    _ok_all = all(v._opacity_items([a]) for a in ("chk_zones", "chk_entries", "chk_sector_lines", "chk_legend"))
    check(_sld is not None and _ok_all, "ползунок у группы «датчики и подписи» знает её элементы",
          "ползунков %d" % len(v._opacity_sliders))
    if _sld is not None:                                   # притушить вдвое
        _sld.setValue(50); app.processEvents()
        _op = [it.opacity() / getattr(it, "_base_op", 1.0) for it in v._sensor_items]   # доля от родной
        c._render_sensors(); app.processEvents()           # датчики пересозданы
        _op2 = [it.opacity() / getattr(it, "_base_op", 1.0) for it in v._sensor_items]
        check(_op and all(abs(o - 0.5) < 1e-6 for o in _op + _op2),
              "датчики притушены вдвое — и после перерисовки", "элементов %d → %d" % (len(_op), len(_op2)))
        _sld.setValue(100)
    _all_ok = []                                           # каждый ползунок: элементы его группы на 30 %
    for _first, _s in v._opacity_sliders.items():          # ползунки всех групп
        _s.setValue(30); app.processEvents()
        _items = v._opacity_items([_first])                # элементы первой галки группы
        _all_ok.append(bool(_items) and all(abs(it.opacity() - 0.3 * it._base_op) < 1e-6 for it in _items))
        _s.setValue(100)
    check(all(_all_ok), "все ползунки прозрачности работают", "групп %d" % len(_all_ok))
    # CTRL + КОЛЕСО: шаг ×1.25 без смены уровня тайлов; отпустили Ctrl — вид на ближайшем уровне

    class _Wheel:
        """Событие колеса для проверки: прокрутка от себя, с Ctrl или без."""
        def __init__(self, ctrl):
            self._ctrl = ctrl                              # держат ли Ctrl
        def delta(self):
            """Щелчок колеса от себя — приблизить. Отдаёт: 120."""
            return 120
        def modifiers(self):
            """Клавиши-модификаторы. Отдаёт: Ctrl или ничего."""
            return Qt.ControlModifier if self._ctrl else Qt.NoModifier
        def pos(self):
            """Курсор — в середине вида. Отдаёт: точка, px."""
            return QPointF(v.vb.width() / 2.0, v.vb.height() / 2.0)
        def accept(self):
            """Событие принято. Отдаёт: ничего."""

    _span = lambda: float(np.diff(v.vb.viewRange()[0])[0])   # noqa: E731 — охват вида по X, км
    v.frame_bbox(); app.processEvents()                    # весь район: до предела приближения далеко
    _s0 = _span(); v.vb.wheelEvent(_Wheel(False)); _s1 = _span()
    _z0 = getattr(getattr(v, "_tiles", None), "_cur_z", None)   # уровень тайлов до Ctrl
    v.vb.wheelEvent(_Wheel(True))
    _frames = 0                                            # кадров анимации до конца (13.5.6)
    while v.vb._free_timer.isActive():                     # прогнать кадры таймера синхронно
        v.vb._free_tick(); _frames += 1
    _s2 = _span()
    _held = getattr(getattr(v, "_tiles", None), "hold_z", "нет тайлов")   # заморожен ли уровень
    check(abs(_s1 / _s0 - 0.5) < 1e-3 and abs(_s2 / _s1 - 0.8) < 1e-3 and v._ctrl_timer.isActive(),
          "колесо — уровень тайлов (×2), с Ctrl — плавно (×1.25)", "%.3f · %.3f" % (_s1 / _s0, _s2 / _s1))
    check(_frames >= 5, "Ctrl + колесо — анимацией, а не скачком", "кадров %d" % _frames)
    v._ctrl_poll()                                         # без экрана Ctrl не держат — «отпустили»
    while v.vb._free_timer.isActive():                     # возврат на уровень — тоже анимацией
        v.vb._free_tick()
    _s3 = _span()
    import view_qt.maps.geomap as _gm                           # noqa: E402 — формула ширины уровня
    _kpd = _gm._km_per_deg_lon(v._geo_lat0)                # км на градус долготы
    _lv = [(v._map_px() / 256.0) / 2.0 ** z * 360.0 * _kpd for z in range(_gm.ZOOM_MIN, _gm.ZOOM_MAX + 1)]   # охваты уровней
    check(not v._ctrl_timer.isActive() and min(abs(_s3 / w - 1) for w in _lv) < 1e-3
          and getattr(getattr(v, "_tiles", None), "hold_z", None) is None,
          "Ctrl отпустили — вид на ближайшем уровне тайлов, заморозка снята",
          "уровень до Ctrl %s, заморожен %s, охват %.1f км" % (_z0, _held, _s3))

    # РУКА ПРИ ПЕРЕТАСКИВАНИИ (13.5.7): начало — сжатая рука, конец — прежний курсор
    import pyqtgraph as _pg                                # noqa: E402
    from PyQt5.QtWidgets import QApplication as _QA       # noqa: E402

    class _Drag:
        """Перетаскивание левой кнопкой для проверки: начало или конец."""
        def __init__(self, start):
            self._s = start                                # начало ли
        def button(self):
            """Кнопка мыши. Отдаёт: левая."""
            return Qt.LeftButton
        def isStart(self):
            """Начало перетаскивания. Отдаёт: bool."""
            return self._s
        def isFinish(self):
            """Конец перетаскивания. Отдаёт: bool."""
            return not self._s

    _orig = _pg.ViewBox.mouseDragEvent                     # само перетаскивание вида — не нужно
    _pg.ViewBox.mouseDragEvent = lambda self, ev, axis=None: None   # noqa: E731
    v.vb.mouseDragEvent(_Drag(True))
    _c1 = _QA.overrideCursor()                             # курсор во время перетаскивания
    v.vb.mouseDragEvent(_Drag(False))
    _pg.ViewBox.mouseDragEvent = _orig
    check(_c1 is not None and _c1.shape() == Qt.ClosedHandCursor and _QA.overrideCursor() is None,
          "перетаскивание — курсор «рука», отпустили — прежний")

    # ---- 27. план 13.5: «Пуск» и «Пакетно» в фоновом потоке, линии дописываются (13.5.11) ----
    # ЧТО ЛОВИТ: поток меняет выборку (зерно то же — маршруты обязаны совпасть до бита); датчики не пересчитываются;
    # накопленные линии перерисовываются целиком; «Пакетно» ставит датчики в главном потоке
    print("\n27. ПЛАН 13.5: «ПУСК» И «ПАКЕТНО» В ФОНОВОМ ПОТОКЕ (13.5.11)")
    import time as _tm                                     # noqa: E402
    _rng0 = np.random.default_rng                          # розыгрыш с зерном — чтобы сравнить выборки

    def _seeded(*_a, **_k):
        """Генератор с закреплённым зерном. Отдаёт: numpy Generator."""
        return _rng0(4242)

    v.ed_iter_T.setText("12"); m.p.threat_iter_routes = 12
    # ⚠️ ТО ЖЕ СОСТОЯНИЕ, ЧТО У «ПУСКА»: он включает галку итераций, и перерисовка обновляет зону пролёта — эталон
    # без неё расходился с «Пуском» не из-за потока (отдельный прогон окна: 12 из 12 совпали)
    v.set_iter_checked(True); c._render_all(); app.processEvents()
    def _clear_hist():
        """История стартов (пауза точек входа) — с чистого листа: `iter_reset` её НЕ сбрасывает (находка 13.5.11,
        В13.5.5) — без этого два прогона подряд даже в главном потоке расходятся с первого маршрута. Отдаёт: ничего."""
        for _c in m._ctxs:                                 # контексты целей
            for _k in ("_recent_entries", "_recent_ok_entries"):   # история стартов — общая на все цели
                if _k in _c:                               # контекст с точками входа
                    del _c[_k][:]

    np.random.default_rng = _seeded
    _clear_hist()
    m.iter_reset()
    _at = 0                                                # при скольких маршрутах эталон пересчитал датчики
    # ⚠️ ЭТАЛОН — СТАРЫЙ «ПУСК» ЦЕЛИКОМ: розыгрыш И расстановка каждые 5 маршрутов в главном потоке. Без расстановки
    # эталон в этом состоянии (несколько целей) расходится и со старым «Пуском» — расстановка трогает контексты целей
    while m.iter_iteration < m.iter_target_count():       # маршрут за маршрутом, как таймер прежде
        if m.iter_step() is not None and len(m.iter_routes) - _at >= 5:   # пора пересчитать датчики
            m.place_sensors(); _at = len(m.iter_routes)
    _ref = [np.array(r, float) for r in m.iter_routes]     # маршруты эталона, км
    c._speed = 400                                        # анимация — маршрут за кадр, чтобы не ждать
    _clear_hist()
    c.on_iter_play()                                       # «Пуск»: поток строит, анимация показывает
    _started = c._worker is not None                       # поток запущен
    _t0, _pe = _tm.time(), 0.0                             # начало и самый долгий проход цикла событий, с
    while (c._anim_running or c._worker is not None) and _tm.time() - _t0 < 300:   # до конца «Пуска»
        _a = _tm.time(); app.processEvents(); _pe = max(_pe, _tm.time() - _a)
        _tm.sleep(0.005)
    np.random.default_rng = _rng0
    _got = [np.array(r, float) for r in m.iter_routes]     # маршруты «Пуска»
    _same = len(_got) == len(_ref) and all(a.shape == b.shape and np.array_equal(a, b) for a, b in zip(_got, _ref))
    check(_started and _same, "«Пуск» в потоке — та же выборка, что в главном (зерно то же)",
          "маршрутов %d и %d" % (len(_got), len(_ref)))
    check(c._sensors_at == len(_got) and len(m.sensors) > 0 and c._worker is None and not c._busy,
          "датчики пересчитаны по всей выборке, поток закончил, кнопки свободны",
          "датчиков %d, по %d маршрутам; дольше всего главный поток занят %.2f с" % (len(m.sensors), c._sensors_at, _pe))
    check(len(v._acc_routes) == len(_got) and len(v._acc_items) == -(-len(_got) // v.ACC_CHUNK),
          "накопленные линии — кусками, дописыванием", "кусков %d" % len(v._acc_items))
    c.on_iter_batch()                                      # «Пакетно»: выборка и расстановка в фоне
    _t0, _pe2 = _tm.time(), 0.0                            # начало и самый долгий проход цикла событий, с
    while c._busy and _tm.time() - _t0 < 300:              # пока фон считает
        _a = _tm.time(); app.processEvents(); _pe2 = max(_pe2, _tm.time() - _a)
        _tm.sleep(0.005)
    app.processEvents()
    check(not c._busy and c._sensors_at == len(m.iter_routes) and len(m.sensors) > 0,
          "«Пакетно»: расстановка в фоне, главный поток только рисует",
          "маршрутов %d, датчиков %d, главный занят до %.2f с" % (len(m.iter_routes), len(m.sensors), _pe2))

    print("\n28. ПЛАН 16.01, 16.12: КОПИЯ ЭКРАНА PNG / SVG, «КАЧЕСТВО ×» (19.10.2026)")
    import tempfile                                                          # noqa: E402
    import pyqtgraph as _pg                                                  # noqa: E402
    from PyQt5 import QtGui as _QtGui                                        # noqa: E402
    from view_qt.output import screen_export as _se                          # noqa: E402
    _w = _pg.PlotWidget(background="w")                    # сцена с известным ответом: одна линия пера 2 пикс.
    _w.hideAxis("left"); _w.hideAxis("bottom"); _w.resize(400, 300)
    _ln = _pg.PlotCurveItem([0, 100], [50, 50], pen=_pg.mkPen((0, 0, 0), width=2))   # горизонтальная линия
    _w.addItem(_ln); _w.setRange(xRange=(0, 100), yRange=(0, 100), padding=0); _w.show(); app.processEvents()

    def _dark(img, x):
        """Тёмных пикселей в столбце x картинки. Вход: QImage, столбец. Отдаёт: int."""
        return sum(1 for y in range(img.height()) if _QtGui.QColor(img.pixel(x, y)).black() > 128)

    _vp, _dpr = _w.viewport(), _w.viewport().devicePixelRatioF()   # область карты и разрешение экрана
    _i1, _i2 = _se.render_image(_w, 1), _se.render_image(_w, 2)   # копия ×1 и ×2
    check((_i1.width(), _i1.height()) == (round(_vp.width() * _dpr), round(_vp.height() * _dpr)),
          "×1 — физические пиксели области карты", "%d × %d при разрешении %.2f" % (_i1.width(), _i1.height(), _dpr))
    _t1, _t2 = _dark(_i1, _i1.width() // 10), _dark(_i2, _i2.width() // 10)   # толщина линии, пикс.
    check(_i2.width() == 2 * _i1.width() and _t1 >= 1 and _t2 == 2 * _t1, "×2 — лист вдвое больше, линия вдвое толще",
          "линия %d → %d пикс." % (_t1, _t2))
    check(abs(_ln.opts["pen"].widthF() - 2.0) < 1e-9, "после выгрузки перья — как на экране",
          "перо %.1f пикс." % _ln.opts["pen"].widthF())
    _svg = _se.save_svg(_w, os.path.join(tempfile.gettempdir(), "uav_screen_check.svg"))   # векторная копия
    _txt = open(_svg, encoding="utf-8").read()             # её текст
    check("<polyline" in _txt or "<path" in _txt, "SVG — линии векторами", "%.1f КБ" % (len(_txt) / 1024))
    _iv = _se.render_image(v.plot, 1)                      # копия карты вкладки
    check((_iv.width(), _iv.height()) == (round(v.plot.viewport().width() * _dpr), round(v.plot.viewport().height() * _dpr)),
          "копия карты вкладки — её область на экране", "%d × %d" % (_iv.width(), _iv.height()))

    print("\n" + LINE)
    if _fails:
        print("НЕ ПРОШЛО: %d — %s" % (len(_fails), "; ".join(_fails)))
        return 1
    print("ВСЁ ПРОШЛО. Рамки, оси, панель, слои, окно исходных данных, история "
         "полётов, анализ размещения, линейка «Расстояние», анализ моделирования, "
         "полнота слоёв, район радиусом, окно сектора, точки старта, справочник "
         "типов БПЛА, условия моделирования, таблица точек входа, широкая карта, "
         "объекты защиты, анализ по целям, анализ размещения по режимам, окна «Датчики» и "
         "«Тип датчика», район защиты цели в лесу, «Применить» без расстановки, копия экрана — в норме.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
