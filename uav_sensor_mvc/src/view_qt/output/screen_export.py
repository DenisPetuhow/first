# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Выгрузка карты КОПИЕЙ ЭКРАНА (план 16.01, задача 16.12; решение заказчика 19.10.2026).

Заказчик 17.10: не KML и не перерисовка, а «картинка того, что на экране»: PNG высокого качества и SVG. 19.10: размер —
область карты на экране с учётом разрешения экрана, множитель качества — настройкой, по умолчанию 1.

КАК. Видимая часть сцены карты рисуется в картинку с плотностью пикселей k = разрешение экрана × качество
(`QImage.setDevicePixelRatio`) — так Qt рисует на экране с масштабом 150–200 %: значки, подписи и растры растут сами.
⚠️ «Косметические» перья линий (pyqtgraph рисует ими всё) от плотности НЕ зависят — замер 19.10: перо 2 пикс. при
k = 1, 2, 3 — всё те же 2 пикс. Поэтому на время выгрузки они толще в «качество» раз и потом возвращаются. При ×1 ничего
не трогается — картинка совпадает с экраном. SVG — векторами (`pyqtgraph.exporters.SVGExporter`), растры — вставками.
Разбор — теория/код/КОПИЯ_ЭКРАНА.
"""
from contextlib import contextmanager

import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets


def _scaled(pen, q):
    """Копия пера толще в q раз. Вход: QPen, множитель. Отдаёт: QPen; волосяное (0) считается за 1 пикс."""
    p = QtGui.QPen(pen)                              # копия — исходное перо не трогаем
    p.setWidthF((p.widthF() or 1.0) * q)
    return p


@contextmanager
def thick_pens(scene, q):
    """На время выгрузки — косметические перья линий и контуров толще в q раз, потом прежние.
    Вход: сцена карты, качество. Отдаёт: ничего (менеджер контекста); q = 1 — ничего не трогает."""
    saved = []                                       # (элемент, его перо) — вернуть после выгрузки
    if q != 1:                                       # ×1 — копия экрана как есть
        for it in scene.items():                     # каждый элемент сцены
            if isinstance(it, pg.PlotCurveItem):     # линия pyqtgraph — перо в opts
                pen = QtGui.QPen(it.opts.get("pen") or QtGui.QPen())   # её перо
                if pen.isCosmetic():                 # толщина в пикселях — плотность её не растит
                    saved.append((it, pen))
                    it.setPen(_scaled(pen, q))
            elif isinstance(it, QtWidgets.QAbstractGraphicsShapeItem):   # круги, многоугольники, пути Qt
                pen = QtGui.QPen(it.pen())           # перо контура
                if pen.isCosmetic():                 # то же — только косметические
                    saved.append((it, pen))
                    it.setPen(_scaled(pen, q))
    try:
        yield
    finally:
        for it, pen in saved:                        # вернуть перья, как были на экране
            it.setPen(pen)


def render_image(widget, quality=1):
    """Видимая часть карты картинкой. Вход: виджет карты (`pg.PlotWidget`), качество ≥ 1.
    Отдаёт: QImage — физические пиксели области карты × качество (с плотностью пикселей)."""
    q = max(1.0, float(quality))                     # множитель качества
    vp = widget.viewport()                           # область карты на экране
    dpr = float(vp.devicePixelRatioF())              # разрешение экрана: 1.5 при масштабе Windows 150 %
    k = dpr * q                                      # пикселей листа на логический пиксель экрана
    img = QtGui.QImage(max(1, round(vp.width() * k)), max(1, round(vp.height() * k)),   # лист выгрузки
                       QtGui.QImage.Format_ARGB32)
    img.setDevicePixelRatio(k)
    img.fill(widget.backgroundBrush().color())       # фон вида: сцена его не рисует
    src = widget.mapToScene(vp.rect()).boundingRect()   # что видно на экране — в координатах сцены
    with thick_pens(widget.scene(), q):
        p = QtGui.QPainter(img)                      # кисть на лист
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        widget.scene().render(p, QtCore.QRectF(0, 0, vp.width(), vp.height()), src)
        p.end()
    return img


def save_png(widget, path, quality=1):
    """PNG копией экрана. Вход: виджет карты, путь, качество. Отдаёт: (путь, ширина, высота) или None — не записался."""
    if not path.lower().endswith(".png"):            # расширение не указали
        path += ".png"
    img = render_image(widget, quality)              # картинка того, что на экране
    return (path, img.width(), img.height()) if img.save(path) else None


def save_svg(widget, path):
    """SVG копией экрана: линии, круги, многоугольники — векторами, растры — вставками PNG. Вход: виджет карты, путь.
    Отдаёт: путь. ⚠️ Размер растёт с числом линий: 150 маршрутов — 5.2 МБ (замер 18.10, план 16.01 §16.12)."""
    import pyqtgraph.exporters as ex
    if not path.lower().endswith(".svg"):            # расширение не указали
        path += ".svg"
    ex.SVGExporter(widget.getPlotItem()).export(path)
    return path
