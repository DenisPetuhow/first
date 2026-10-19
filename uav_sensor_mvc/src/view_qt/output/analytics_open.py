# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Модуль вывода — ОТКРЫТИЕ окон рисунков «Аналитики» и то, что они берут у карты вкладки 3 (06.10.2026).

`open_figure(вид, kind, цель)` — кнопки окна «Аналитика»: «Анализ нагрузки» (load), «Показ сектора» (sector), «Показ
карты» (map). Окно создаётся один раз и при каждом нажатии получает новый снимок (`on_report_data`). Карта вкладки
отдаёт «Показу карты» подложку (тот же слой, участок и офлайн), свою карту (растр с привязкой) и рамку вида; цвета
типов — как на карте (`threat_view.type_color`). Вынесено из `threat_view.py`, чтобы вкладка не росла.
"""
import numpy as np


def type_colors(tids):
    """Цвета типов как на карте вкладки. Вход: номера типов. Отдаёт: {тип: «#rrggbb»}."""
    from view_qt.tabs.threat_view import type_color
    return {int(t): type_color(t) for t in tids}


def straighten_rows(rgba, ext, lat0):
    """Строки мозаики тайлов — из Меркатора в км района: строка встаёт на свою широту. Вход: rgba (строка 0 — юг),
    рамка (x0, x1, y0, y1) км, широта якоря. Отдаёт: новый rgba того же размера."""
    from model.threat.geo_frame import KM_PER_DEG_LAT
    # Строки тайлов равномерны по Меркатору m = asinh(tan φ), а км района — по широте: прямоугольник сдвигал
    # середину на 0,59 км (замер 07.10.2026). Столбцы трогать не нужно — и там, и тут они линейны по долготе.
    h = rgba.shape[0]                                # строк в мозаике
    lat_lo, lat_hi = lat0 + ext[2] / KM_PER_DEG_LAT, lat0 + ext[3] / KM_PER_DEG_LAT   # широты нижнего и верхнего края
    m_lo, m_hi = (np.arcsinh(np.tan(np.radians(v))) for v in (lat_lo, lat_hi))   # они же в Меркаторе
    lat = lat_lo + (lat_hi - lat_lo) * (np.arange(h) + 0.5) / h   # широта центра каждой новой строки
    src = (np.arcsinh(np.tan(np.radians(lat))) - m_lo) / (m_hi - m_lo) * h - 0.5   # её место в мозаике, строки
    return rgba[np.clip(np.rint(src).astype(int), 0, h - 1)]


def basemap(view, box_km, px):
    """Подложка для «Показа карты» — слой, участок и офлайн как на карте вкладки, строки выпрямлены по широте.
    Вход: вид, рамка (x0, x1, y0, y1) км, ширина, пикс. Отдаёт: (rgba — строка 0 юг, рамка км) или None.
    ⚠️ Тяжёлая — звать из потока."""
    from view_qt.maps import geomap as gm
    x0, x1, y0, y1 = box_km                          # рамка, км
    res = gm.build_raster_basemap(x0, x1, y0, y1, layer=view._map_layer, allow_net=not view._map_offline,
                                  target_px=int(px), lon0=view._geo_lon0, lat0=view._geo_lat0, area=view._tile_area)
    if res is None:                                  # тайлов нет — фона не будет
        return None
    return straighten_rows(res[0], res[1], view._geo_lat0), res[1]


def own_map(view):
    """Своя карта вкладки фоном. Вход: вид. Отдаёт: (rgba, (m11, m12, m21, m22, dx, dy) — пиксель -> км),
    «vector» — карта векторная (растра нет), None — не загружена."""
    if getattr(view, "map_svg_item", None) is not None:   # векторная своя карта
        return "vector"
    it = getattr(view, "map_img_item", None)         # растровая своя карта
    if it is None or not it.isVisible() or it.image is None:   # не загружена или скрыта
        return None
    t = it.transform()                               # привязка картинки: пиксель -> км
    return np.asarray(it.image), (t.m11(), t.m12(), t.m21(), t.m22(), t.dx(), t.dy())


def view_box(view):
    """Рамка вида карты вкладки сейчас. Вход: вид. Отдаёт: (x0, x1, y0, y1) км."""
    (x0, x1), (y0, y1) = view.vb.viewRange()        # видимая область, км
    return (float(x0), float(x1), float(y0), float(y1))


def open_figure(view, kind, target=None):
    """Открыть окно рисунка «Аналитики» с новым снимком. Вход: вид вкладки 3, «load» | «sector» | «map», цель
    (номер с нуля, None — все). Отдаёт: окно."""
    from view_qt.output.load_chart_window import LoadChartDialog
    from view_qt.output.map_figure_window import MapFigureDialog
    from view_qt.output.sector_chart_window import SectorChartDialog
    dlgs = view._figure_dlgs                         # окна рисунков вида: вид -> окно
    dlg = dlgs.get(kind)                             # окно уже создано?
    if dlg is None:                                  # первое нажатие — создать
        kw = dict(data=lambda t: view.on_report_data(t), choices=lambda: view.on_analysis_choices(),
                  colors=type_colors)                # снимок, цели, цвета
        if kind == "map":                            # карте нужны ещё фон и рамка вида
            kw.update(basemap=lambda box, px: basemap(view, box, px), own_map=lambda: own_map(view),
                      view_box=lambda: view_box(view),
                      screen=lambda: view.plot)      # виджет карты — для копии экрана (16.12)
        cls = {"load": LoadChartDialog, "sector": SectorChartDialog, "map": MapFigureDialog}[kind]   # класс окна
        dlg = dlgs[kind] = cls(view, **kw)
    dlg.refresh(target)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
