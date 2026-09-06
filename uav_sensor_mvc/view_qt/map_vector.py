# -*- coding: utf-8 -*-
"""VIEW (Qt) · СВОЯ КАРТА ВЕКТОРОМ (SVG) — план 8, задача 8.5.0.

ЗАЧЕМ. Карта заказчика нарисована в CorelDRAW. Растровый экспорт из неё уже работает
(`map_image.py`), но у растра есть потолок: 17 944 × 13 220 точек — это 8.8 м на пиксель,
712 МБ служебного файла ради резкости, и при сильном приближении всё равно мыло. Вектор
рисуется заново под каждый масштаб: он остаётся острым на любом зуме и весит 1–3 МБ.

ЧТО ЭТО НЕ РЕШАЕТ. ⚠️ **SVG не несёт географических координат.** Это рисунок в своих
единицах, ровно как PNG, и привязывать его надо так же. Путать с векторными ГЕОДАННЫМИ
(`.gpkg`, `.shp`) нельзя: у тех координаты внутри файла и привязка не нужна вовсе
(сравнение — [план 8 §8.5.0б](../теория/планы/8_ПЛАН_КАРТА_И_ИНТЕРФЕЙС.md)).

КАК ПРИВЯЗЫВАЕТСЯ. Тем же механизмом, что растр, и это главная выгода от того, что
привязка живёт отдельным модулем: `MapAnchor` работает с «пикселями картинки», а для SVG
такой «пиксель» — единица его собственной системы координат (`QSvgRenderer.defaultSize`).
Автоподбор по населённым пунктам тоже переиспользуется: SVG рендерится во временный
растр, и дальше идёт та же маска чёрного (`render_for_fit`).

⚠️ ДВЕ ЛОВУШКИ, найденные при первом заходе (план 8 §8.5.0) и подтверждённые здесь:

1. **ось Y смотрит в разные стороны.** У Qt начало вверху слева и Y растёт ВНИЗ, у нашей
   сцены — ВВЕРХ (север). У растра это скрыто переворотом массива, а SVG рисует себя как
   есть, поэтому в матрице привязки множитель по Y отрицателен. Забыть — карта встанет
   вверх ногами;
2. **`QGraphicsSvgItem` держит свой файл** и подменить его в готовом элементе нельзя: при
   смене карты старый элемент снимают со сцены и создают новый (`replace_item`).

ЧЕМ ПЛАТИМ. Вектор пересчитывается при КАЖДОМ кадре: на карте с тысячами объектов
панорама может подтормаживать, и предсказать это нельзя — только замерить на своём файле
(`measure_draw`). Поэтому растровый путь остаётся рядом, а не заменяется.
"""
import os

import numpy as np
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

try:                                     # QtSvg входит в PyQt5, но проверить дёшево
    from PyQt5.QtSvg import QGraphicsSvgItem, QSvgRenderer
    HAVE_SVG = True
except Exception:                        # pragma: no cover — сборка без QtSvg
    QGraphicsSvgItem = QSvgRenderer = None
    HAVE_SVG = False

EXTS = (".svg",)

# Во сколько точек рендерить SVG для ПОДБОРА привязки. Подбору не нужна ни резкость, ни
# цвет — только где на карте чёрное; 3 000 точек по ширине на район 150 км дают 50 м на
# точку, вдвое мельче типичного посёлка (то же число, что у растра — `map_image.read_for_fit`).
FIT_PX = 3000


def is_vector(path):
    """Это векторная карта (по расширению)?"""
    return os.path.splitext(str(path))[1].lower() in EXTS


def svg_size(path):
    """Собственный размер SVG в его единицах -> (ширина, высота).

    ⚠️ ЭТО НЕ ПИКСЕЛИ ФАЙЛА, а система координат рисунка: у SVG может не быть ни
    `width`, ни `height` — только `viewBox`. `defaultSize()` разбирается с обоими
    случаями сам. Все опорные точки привязки задаются именно в этих единицах."""
    if not HAVE_SVG:
        raise RuntimeError("в этой сборке Qt нет QtSvg — векторные карты недоступны")
    r = QSvgRenderer(str(path))
    if not r.isValid():
        raise ValueError("файл не читается как SVG (или пуст)")
    s = r.defaultSize()
    w, h = int(s.width()), int(s.height())
    if w <= 0 or h <= 0:
        vb = r.viewBoxF()
        w, h = int(vb.width()), int(vb.height())
    if w <= 0 or h <= 0:
        raise ValueError("у SVG не задан размер: нет ни width/height, ни viewBox")
    return w, h


# Элементы, по которым видно, что в файле есть НАСТОЯЩАЯ векторная графика, а не одна
# вставленная фотография. `rect` намеренно не в списке: рамку листа рисует сам CorelDRAW.
_VECTOR_TAGS = ("<path", "<polyline", "<polygon", "<line", "<circle", "<ellipse",
                "<text", "<tspan")


def inspect_svg(path):
    """Что лежит внутри SVG -> dict(images, embedded, linked, vector, size_mb).

    ⚠️ ЗАЧЕМ ЭТО НУЖНО. «Сохранить как SVG» ещё не делает карту векторной. Если в
    CorelDRAW подложка — растровое изображение (в панели объектов слой так и называется,
    «Растр»), то SVG будет лишь КОНТЕЙНЕРОМ вокруг той же фотографии: резкость не
    появится, а файл станет больше исходного PNG примерно на треть (base64 — четыре
    байта текста на три байта данных).

    ⚠️ И ВТОРАЯ БЕДА — «Связь изображений» вместо «Встроенные изображения». Тогда внутрь
    попадает не картинка, а ССЫЛКА на файл, и весит такой SVG десятки килобайт вместо
    сотен мегабайт. Замер 05.09.2026 на этом Qt:

        растр встроенный (base64)          — виден
        ссылка вида file:///C:/…           — ПУСТОЙ ЛИСТ (qt.svg: Could not create image)
        ссылка относительная, файл рядом   — виден

    То есть карта, переданная другому человеку, у него просто не откроется. Разобрать это
    заранее дешевле, чем объяснять потом пустой экран."""
    images = embedded = linked = 0
    vector = 0
    tail = ""
    with open(path, "rb") as f:
        while True:
            block = f.read(1 << 20)
            if not block:
                break
            # ⚠️ ПЕРЕКРЫТИЕ между блоками: тег может разорваться на границе чтения, и без
            # хвоста предыдущего блока он бы потерялся.
            text = tail + block.decode("utf-8", "replace")
            tail = text[-200:]
            images += text.count("<image")
            # ⚠️ СЧИТАЕМ ТОЛЬКО `href=`, а не ещё и `xlink:href=`: второе содержит первое
            # как подстроку, и от двойного перебора счётчик показывал «ссылок 2» там, где
            # ссылка одна. Число уходит человеку в предупреждение — оно должно быть честным.
            for piece in text.split('href="')[1:] + text.split("href='")[1:]:
                if piece.startswith("data:"):
                    embedded += 1
                elif not piece.startswith("#"):
                    linked += 1
            for tag in _VECTOR_TAGS:
                vector += text.count(tag)
    return dict(images=images, embedded=embedded, linked=max(0, linked),
                vector=vector, size_mb=os.path.getsize(path) / 1e6)


def describe_content(info):
    """Человеческое предупреждение по разбору `inspect_svg`, либо пустая строка."""
    if info["images"] and info["linked"] and not info["embedded"]:
        return (" ⚠️ Внутри файла не картинка, а ССЫЛКА на неё (в CorelDRAW при экспорте "
                "стояла «Связь изображений»). У вас она может открыться, потому что "
                "исходник лежит рядом, но на другой машине будет пустой лист. "
                "Пересохраните со «Встроенными изображениями».")
    if info["images"] and info["vector"] < 20:
        return (" ⚠️ Внутри SVG лежит РАСТР, а не векторная графика: резкости при "
                "увеличении это не даст — вектор здесь только обёртка. Если в CorelDRAW "
                "карта и есть фотография (слой «Растр»), берите её сразу в .png или "
                ".jpg: тот путь быстрее и умеет резкость по видимой области.")
    return ""


def make_item(path):
    """Элемент сцены для векторной карты.

    Z = −19 — то же место, что у растровой картинки: САМЫЙ НИЖНИЙ слой данных, сразу над
    тайлами и под всем остальным. Картинка заменяет собой ФОН, а расчётные слои ложатся
    на неё (почему именно так — `map_image.make_image_item`)."""
    if not HAVE_SVG:
        raise RuntimeError("в этой сборке Qt нет QtSvg — векторные карты недоступны")
    item = QGraphicsSvgItem(str(path))
    if item.renderer() is None or not item.renderer().isValid():
        raise ValueError("файл не читается как SVG (или пуст)")
    item.setZValue(-19.0)
    # ⚠️ БЕЗ КЭША В ЭКРАННЫХ КООРДИНАТАХ. `DeviceCoordinateCache` ускорил бы панораму, но
    # он держит отрисованную копию и при зуме показывает её растянутой — то самое мыло,
    # ради избавления от которого вектор и берут.
    item.setCacheMode(QtWidgets.QGraphicsItem.NoCache)
    item.setFlag(QtWidgets.QGraphicsItem.ItemClipsToShape, False)
    return item


def place_item(item, coeffs):
    """Поставить векторную карту по привязке (`map_anchor.scene_transform`)."""
    item.setTransform(QtGui.QTransform(*[float(c) for c in coeffs]))
    item.setVisible(True)


def render_for_fit(path, want_px=FIT_PX):
    """Отрисовать SVG в растр для ПОДБОРА привязки.

    -> (RGB (H, W, 3) uint8, шаг «единица SVG на точку растра», (ширина, высота) SVG).

    ⚠️ ШАГ ЗДЕСЬ ДРОБНЫЙ, и это нормально. У растра шаг — целое прореживание (каждый
    пятый пиксель), а тут наоборот: SVG обычно МЕЛЬЧЕ желаемого растра, и на одну его
    единицу приходится несколько точек. `model/map_fit` умножает на этот шаг, чтобы
    вернуть найденные точки в единицы самой карты, и дробное значение ему не мешает."""
    w, h = svg_size(path)
    k = float(want_px) / float(max(w, h))
    rw, rh = max(1, int(round(w * k))), max(1, int(round(h * k)))
    img = QtGui.QImage(rw, rh, QtGui.QImage.Format_RGB888)
    img.fill(QtGui.QColor("white"))       # у SVG фон прозрачный, а нам нужен «лист»
    p = QtGui.QPainter(img)
    try:
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        QSvgRenderer(str(path)).render(p)
    finally:
        p.end()                           # ⚠️ без end() QImage остаётся заблокированным
    ptr = img.constBits()
    ptr.setsize(img.byteCount())
    # ⚠️ У QImage строки выровнены по 4 байта: при ширине не кратной четырём в конце
    # каждой строки лежит мусор, и без учёта bytesPerLine картинка «съезжает» по диагонали.
    arr = np.frombuffer(ptr, np.uint8).reshape(rh, img.bytesPerLine())
    arr = arr[:, : rw * 3].reshape(rh, rw, 3).copy()
    return arr, float(w) / float(rw), (w, h)


def measure_draw(item, view, repeats=3):
    """Сколько миллисекунд занимает отрисовка вектора в текущем виде.

    Нужна не ради красивого числа: у вектора расход растёт с числом объектов, и
    единственный способ узнать, потянет ли КОНКРЕТНЫЙ файл панораму, — замерить его же.
    Вызывается из проверки и из окна карты, чтобы человеку было что сказать."""
    import time
    rect = view.viewRect() if hasattr(view, "viewRect") else item.boundingRect()
    img = QtGui.QImage(600, 400, QtGui.QImage.Format_ARGB32)
    best = None
    for _ in range(max(1, repeats)):
        p = QtGui.QPainter(img)
        t0 = time.perf_counter()
        try:
            item.renderer().render(p, QtCore.QRectF(0, 0, 600, 400))
        finally:
            p.end()
        dt = (time.perf_counter() - t0) * 1000.0
        best = dt if best is None else min(best, dt)
    return best
