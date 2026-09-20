# -*- coding: utf-8 -*-
"""
VIEW (Qt) · ПОДЛОЖКА ПОШТУЧНЫМИ ТАЙЛАМИ (план 10, задача 10.10).

ЧЕМ ОТЛИЧАЕТСЯ ОТ ПРЕЖНЕГО ПУТИ. Раньше все видимые тайлы склеивались в ОДНУ картинку
(`geomap.build_raster_basemap`) и клались на сцену одним `ImageItem.setRect`. Отсюда три
беды разом, и все три лечит поштучная укладка:

  * РАСХОЖДЕНИЕ СО СЛОЯМИ. Картинка натягивается на прямоугольник линейно, а содержимое
    тайлов распределено по Меркатору — нелинейно по широте. Разница копится к середине
    мозаики и тем больше, чем та выше. Замер: на виде «вся область» — 3 094 м, то есть
    11.07 пикселя. Уложив КАЖДЫЙ тайл в свой прямоугольник, оставляем нелинейность
    внутри одного тайла: на зуме 8 его высота 71.6 км, а расхождение 198 м = 0.71 px;
  * МИГАНИЕ. Любое движение мыши пересобирало мозаику целиком (дебаунс 180 мс). Теперь
    уже показанные тайлы остаются на месте, доезжают только недостающие;
  * РАЗМЫТИЕ. Мало класть тайлы поштучно — надо ещё рисовать их ПИКСЕЛЬ В ПИКСЕЛЬ, иначе
    подписи рассыпаются. Этим заняты `SteppedViewBox` (колесо ходит по уровням тайлов) и
    `set_span_x`; плюс включено сглаживание картинок (`basemap_mixin`).

ЧТО БЕРЁТСЯ ИЗ `geomap.py`, А НЕ ПИШЕТСЯ ЗАНОВО: адресация тайлов, кэш на диске и в
памяти, раскладки XYZ/TMS, запасной поиск в общей папке кэша (там 13 273 старых тайла),
подбор зума. Здесь — только показ: какие тайлы нужны, где они лежат на сцене и как
переиспользуются элементы сцены.

⚠️ ГАШЕНИЕ ОСТАЁТСЯ ПРАВКОЙ ПИКСЕЛЕЙ, а не прозрачностью элемента: подложка гасится, чтобы
не спорить с нашими слоями, и полупрозрачная картинка на тёмном фоне приложения ушла бы в
грязно-тёмное вместо светло-бледного (см. `basemap_mixin._fade_basemap`).
"""
from collections import OrderedDict

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui

from . import geomap as gm

# Сколько элементов сцены держим. Больше — растёт память и время обхода сцены;
# меньше — при панораме тайлы начинают выбрасываться и грузиться заново.
# 400 = экран 4К целиком (примерно 8×5 тайлов) плюс запас на предыдущий зум.
MAX_ITEMS = 400
# Сколько ГОТОВЫХ (уже погашенных) тайлов держим в памяти. Гашение — numpy по 256×256,
# дешёвое, но при панораме туда-сюда пересчитывать его незачем.
FADED_CACHE = 256


def set_span_x(vb, want, y_center=None):
    """Выставить диапазон по X ФАКТИЧЕСКИ, а не «попросить».

    Принимает: ViewBox, пару краёв по X (км), необязательный центр по Y (км).
    Отдаёт: ничего.

    ⚠️ ПОЧЕМУ НЕЛЬЗЯ ПРОСТО `setRange(xRange=…)`. Масштаб осей заблокирован, и ViewBox
    выводит одну ось из другой по форме окна. Просишь X вдвое меньше, а Y остаётся
    прежним — и X растягивается обратно под него: замер 13.09.2026 показал ×0.56 вместо
    ×0.5, кратность уровню тайлов ломалась на первом же щелчке. Поэтому обе оси задаются
    вместе и ровно в том соотношении, какое у окна: тогда подгонять нечего."""
    lo, hi = float(want[0]), float(want[1])
    span = hi - lo
    if span <= 0:
        return
    try:
        w_px, h_px = float(vb.width()), float(vb.height())
    except Exception:                              # виджет ещё не разложен
        w_px = h_px = 0.0
    (_x0, _x1), (y0, y1) = vb.viewRange()
    cy = (y0 + y1) * 0.5 if y_center is None else float(y_center)
    if w_px > 1.0 and h_px > 1.0:
        span_y = span * h_px / w_px                # высота, отвечающая ширине при том же масштабе
    else:                                          # размеров нет — сохраняем нынешнюю высоту
        span_y = (y1 - y0)
    vb._resetTarget()
    vb.setRange(xRange=(lo, hi), yRange=(cy - span_y * 0.5, cy + span_y * 0.5),
                padding=0.0)


class SteppedViewBox(pg.ViewBox):
    """ViewBox, у которого колесо мыши меняет масштаб ровно ВДВОЕ — на один уровень тайлов.

    ⚠️ ЗАЧЕМ ЛОМАТЬ ПРИВЫЧНЫЙ ПЛАВНЫЙ ЗУМ. Тайлы существуют только на целых уровнях, и
    чёткими они выглядят, лишь когда рисуются пиксель в пиксель. При плавном зуме масштаб
    отрисовки — любое число: замер 13.09.2026 дал 1:1 всего в 23 % случаев, а в остальных
    от 0.71 до 1.41, и подписи на карте рассыпаются.

    Обычное колесо pyqtgraph даёт шаг ≈ ×0.74 за щелчок — это 2.3 щелчка на уровень.
    Подтягивать вид к уровню после каждого щелчка нельзя: половину щелчков вид отскакивал
    бы назад (замечание заказчика «после 10 прокруток дёргается»). Поэтому шаг колеса и
    делается равным уровню — ровно так же ведут себя слиппи-карты в браузере."""

    def wheelEvent(self, ev, axis=None):
        d = ev.delta() if hasattr(ev, "delta") else ev.angleDelta().y()
        if not d:                                  # горизонтальная прокрутка или пустое событие
            return                                 # масштаб не трогаем
        factor = 0.5 if d > 0 else 2.0             # от себя — приблизить: охват вдвое меньше
        # точка под курсором остаётся на месте — как при обычном зуме pyqtgraph
        center = pg.Point(pg.functions.invertQTransform(
            self.childGroup.transform()).map(ev.pos()))
        # ⚠️ НЕ ЧЕРЕЗ scaleBy: он умножает ЦЕЛЕВОЙ диапазон, а фактический потом тянется
        # под форму окна — щелчок давал ×1.78 вместо ×2 (замер 13.09.2026), и кратность
        # уровню ломалась на первом же щелчке. Диапазон выставляется явно и по обеим осям
        # сразу — см. `set_span_x`.
        (x0, x1), (y0, y1) = self.viewRange()
        ny0, ny1 = self._zoom_axis(y0, y1, center.y(), factor)   # куда уйдёт Y при зуме к курсору
        set_span_x(self, self._zoom_axis(x0, x1, center.x(), factor),
                   y_center=(ny0 + ny1) * 0.5)
        ev.accept()

    @staticmethod
    def _zoom_axis(lo, hi, at, factor):
        # Новый диапазон одной оси при зуме «вокруг точки».
        # Принимает: края (км), точку под курсором, множитель охвата. Отдаёт: пару краёв.
        span = (hi - lo) * factor                  # новый охват оси, км
        frac = (at - lo) / (hi - lo) if hi > lo else 0.5   # где курсор внутри вида, доля
        lo2 = at - span * frac                     # держим ту же долю: точка не убегает
        return (lo2, lo2 + span)


class _TileImage(pg.ImageItem):
    """Картинка тайла: рисуется БЕЗ сглаживания, пиксель в пиксель.

    ⚠️ Сглаживание убрано 19.09.2026 («не сглаживай»): оно не лечило мыло, а размазывало
    его — причина была в виде между уровнями (растяжение 1.145). ⚠️ Класс отдельный:
    у весовой карты ячейки 500 м обязаны читаться квадратиками, её сглаживать нельзя."""

    def paint(self, p, *args):
        p.save()                                   # не протечь настройкой на соседние элементы
        # False, а не «не трогать»: pyqtgraph мог включить сглаживание у всей сцены
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, False)
        try:
            pg.ImageItem.paint(self, p, *args)
        finally:
            p.restore()


class _TileSignals(QtCore.QObject):
    ready = QtCore.pyqtSignal(object)      # (layer, z, x, y, rgba|None)


class _TileTask(QtCore.QRunnable):
    """Чтение и декод ОДНОГО тайла в фоновом потоке.

    Принимает слой, зум и координаты тайла. Отдаёт картинку сигналом; годится она ещё
    или нет — решает сам слой, когда она доедет."""

    def __init__(self, layer, z, x, y, allow_net, area, signals):
        super().__init__()
        self._layer = layer
        self._z = z; self._x = x; self._y = y
        self._net = allow_net; self._area = area; self._sig = signals

    def run(self):
        try:
            rgba = gm.tile_rgba(self._layer, self._z, self._x, self._y,
                                self._net, area=self._area)
        except Exception:                       # битый файл, нет прав, обрыв сети
            rgba = None                         # тайла просто не будет, показ не падает
        try:
            self._sig.ready.emit((self._layer, self._z, self._x, self._y, rgba))
        except RuntimeError:
            pass                                # окно закрыли, пока тайл ехал


class TileLayer(QtCore.QObject):
    """Подложка из отдельных тайлов поверх `PlotItem`.

    Принимает: `pi` — PlotItem сцены, якорь км-фрейма, колбэки текущего слоя и офлайна.
    Отдаёт: ничего; сам добавляет и убирает элементы по мере движения вида."""

    def __init__(self, pi, lon0, lat0, get_layer, get_offline,
                 area=None, fade_fn=None, z_value=-20, parent=None):
        super().__init__(parent)
        self._pi = pi
        self._lon0 = float(lon0); self._lat0 = float(lat0)
        self._get_layer = get_layer            # callable -> ключ слоя ("osm", …)
        self._get_offline = get_offline        # callable -> bool, «не ходить в сеть»
        self._area = area                      # папка участка в кэше (ОГРАНИЧЕНИЯ 5.1д)
        self._fade = fade_fn                   # callable(rgba) -> rgba, гашение подложки
        self._z = float(z_value)               # глубина слоя: ниже всех наших слоёв

        self._items = {}                       # (z,x,y) -> ImageItem, что сейчас на сцене
        self._free = []                        # снятые элементы — очередь на переиспользование
        self._pending = set()                  # (z,x,y), которые уже заказаны в поток
        self._faded = OrderedDict()            # LRU готовых картинок, ключ тот же
        # ⚠️ ЧТО НУЖНО ПРЯМО СЕЙЧАС — по этому набору решается судьба доехавшего тайла.
        # Раньше здесь стоял номер поколения, и тайл, заказанный до движения мыши,
        # выбрасывался по приходе. Заново его не заказывали (ключ уже ушёл из `_pending`),
        # и подложка оставалась пустой до следующего жеста — на проверке вышло 0 тайлов
        # вместо 42. Картинка тайла не устаревает: устаревает только надобность в ней.
        self._need = set()
        self._cur_z = None                     # зум, на котором сейчас показываем
        self._visible = True

        self._pool = QtCore.QThreadPool.globalInstance()
        self._sig = _TileSignals()
        self._sig.ready.connect(self._on_tile)

    # ------------------------------------------------------------------
    def set_view(self, box_km, width_px):
        """Пересчитать, какие тайлы нужны для видимого окна.

        Принимает: (kx0, kx1, ky0, ky1) в километрах и ширину виджета в пикселях.
        Отдаёт: выбранный зум либо None, если показывать нечего."""
        if not self._visible:
            return None
        kx0, kx1, ky0, ky1 = box_km
        if not (kx1 > kx0 and ky1 > ky0):          # вырожденное окно (виджет ещё не разложен)
            return None                            # считать нечего — ждём следующего вызова
        layer = self._get_layer()
        z = gm.pick_zoom_for_box(kx0, kx1, ky0, ky1, width_px, self._lon0, self._lat0)
        x0, x1, y0, y1 = gm.tiles_for_box(kx0, kx1, ky0, ky1, z, self._lon0, self._lat0)
        # ⚠️ ПОЛЕ В ОДИН ТАЙЛ ВОКРУГ ОКНА: при панораме следующий тайл уже загружен, и
        # край экрана не догоняет мышь пустотой. Стоит это ровно одного ряда тайлов.
        n = int(2 ** z) - 1
        x0, x1 = max(0, x0 - 1), min(n, x1 + 1)
        y0, y1 = max(0, y0 - 1), min(n, y1 + 1)

        allow_net = not self._get_offline()
        need = set()
        for tx in range(x0, x1 + 1):
            for ty in range(y0, y1 + 1):
                key = (z, tx, ty)
                need.add(key)
                if key in self._items or key in self._pending:
                    continue                       # уже на сцене или уже едет — не дублируем
                got = self._faded.get(key)
                if got is not None:                # был показан недавно — ставим сразу
                    self._faded.move_to_end(key)
                    self._place(key, got)
                    continue
                self._pending.add(key)
                self._pool.start(_TileTask(layer, z, tx, ty, allow_net,
                                           self._area, self._sig))
        self._need = need
        self._cur_z = z                            # до _restack: глубина считается от него
        self._restack()                            # текущий зум — наверх, прежний — под него
        self._drop_extra(need, z)
        return z

    def set_visible(self, on):
        """Показать или спрятать всю подложку (режим «только своя карта»)."""
        self._visible = bool(on)
        for it in self._items.values():
            it.setVisible(self._visible)

    def clear(self):
        """Убрать все тайлы со сцены — при смене слоя или участка."""
        for key in list(self._items):
            self._recycle(key)
        self._need = set()                         # ничего не нужно: доехавшие не ставим
        self._pending.clear()
        self._faded.clear()                        # картинки чужого слоя не пригодятся
        self._cur_z = None

    # ------------------------------------------------------------------
    def _on_tile(self, payload):
        """Тайл доехал из потока: положить в кэш и на сцену, если он ещё нужен."""
        layer, z, x, y, rgba = payload
        key = (z, x, y)
        self._pending.discard(key)
        if layer != self._get_layer():             # слой переключили, пока тайл ехал
            return                                 # чужая картинка, выбрасываем
        if rgba is None:                           # тайла нет ни в кэше, ни в сети
            return                                 # дырка в подложке, это не ошибка
        img = self._fade(rgba) if self._fade else rgba
        # ⚠️ строка 0 картинки — СЕВЕР, а ось Y сцены смотрит вверх: переворачиваем, иначе
        # каждый тайл ляжет вверх ногами — и это выглядит правдоподобно, пока не приглядишься
        img = img[::-1]
        self._faded[key] = img                     # в кэш кладём ВСЕГДА: вид мог вернуться
        if len(self._faded) > FADED_CACHE:
            self._faded.popitem(last=False)
        if key in self._need:                      # тайл всё ещё в кадре
            self._place(key, img)                  # ставим; иначе он просто ждёт в кэше

    def _place(self, key, img):
        """Положить готовую картинку тайла в его километры."""
        z, x, y = key
        it = self._items.get(key)
        if it is None:
            it = self._free.pop() if self._free else self._new_item()
            self._items[key] = it
        it.setImage(img, autoLevels=False)
        ex0, ex1, ey0, ey1 = gm.tile_bounds_km(z, x, y, self._lon0, self._lat0)
        it.setRect(QtCore.QRectF(ex0, ey0, ex1 - ex0, ey1 - ey0))
        it.setZValue(self._z_for(z))
        it.setVisible(self._visible)

    def _z_for(self, z):
        # Глубина тайла на сцене. Принимает: его зум. Отдаёт: Z-координату.
        # ⚠️ СВЕРХУ — ТЕКУЩИЙ ЗУМ, А НЕ САМЫЙ ПОДРОБНЫЙ. Раньше Z рос вместе с номером
        # зума, и при ОТДАЛЕНИИ новые тайлы ложились ПОД старые подробные: карта меняла
        # масштаб, а подписи оставались прежними, пока вид не сдвинут (замечание
        # заказчика 13.09.2026). Прежний уровень нужен лишь как заплатка на время
        # загрузки — ему место снизу.
        return self._z + (0.5 if z == self._cur_z else 0.0)

    def _restack(self):
        """Переставить глубину всех тайлов после смены зума: текущий — наверх."""
        for (z, _x, _y), it in self._items.items():
            it.setZValue(self._z_for(z))

    def _new_item(self):
        """Новый элемент сцены под один тайл."""
        it = _TileImage()
        it.setOpts(axisOrder="row-major")
        it.setZValue(self._z)
        self._pi.addItem(it)
        return it

    def _drop_extra(self, need, z):
        """Убрать то, что уже не нужно: чужой зум и уехавшие за край тайлы.

        ⚠️ Тайлы ПРЕЖНЕГО зума держим, пока новый не доехал, — иначе при каждом повороте
        колеса кадр на мгновение пустеет. Это и есть «плавность»."""
        # ⚠️ «ДОЕХАЛ» — ЗНАЧИТ НИЧЕГО НЕ ЖДЁМ, а не «все тайлы на сцене». Отсутствующий в
        # кэше тайл на сцену не попадёт никогда, и проверка «все на месте» держала бы
        # прежний уровень вечно — поверх нового.
        fresh_ready = not (need & self._pending)
        for key in list(self._items):
            if key in need:
                continue
            if key[0] != z and not fresh_ready:
                continue                           # чужой зум ещё нужен как подложка
            self._recycle(key)
        # предохранитель: при быстрой панораме элементы копятся быстрее, чем уходят
        while len(self._items) > MAX_ITEMS:
            self._recycle(next(iter(self._items)))

    def _recycle(self, key):
        """Снять тайл со сцены, элемент оставить в пуле.

        ⚠️ Не удаляем `ImageItem` насовсем: создание элемента сцены дороже, чем очистка,
        а при панораме их сменяются десятки в секунду."""
        it = self._items.pop(key, None)
        if it is None:
            return
        it.setVisible(False)
        if len(self._free) < 64:                   # пул не резиновый: 64 хватает на кадр
            self._free.append(it)
        else:
            self._pi.removeItem(it)
