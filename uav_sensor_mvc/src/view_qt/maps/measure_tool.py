# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Линейка «Расстояние» — как в Google Earth (правило заказчика 21.09.2026).

Ведём мышь — за курсором тянется полупрозрачная линия с километрами. Щёлкнули — отрезок
закреплён: его длина и время уходят НА СЕРЕДИНУ сегмента, а сумма пути остаётся у
последней точки, вынесенная за неё как продолжение линии и повёрнутая вдоль неё.

⚠️ ВРЕМЯ СЧИТАЕТСЯ ПО СКОРОСТИ ВЫБРАННОГО ТИПА БПЛА. Тип не выбран — время не
показывается вовсе, а не подставляется «по умолчанию»: выдуманное число хуже пустого.

⚠️ НОВЫЙ МЕХАНИЗМ — СВОЙ ФАЙЛ (правило проекта). Представление отдаёт сюда клики и
движения мыши, а рисование целиком здесь: в `threat_view` оставалась бы четверть файла.
"""
import numpy as np
import pyqtgraph as pg


def format_time(hours):
    """Время в подпись: «2 ч 35 мин», «47 мин». Вход: часы. Отдаёт: строку."""
    total = int(round(float(hours) * 60.0))          # минут всего, шт.
    if total < 60:
        return "%d мин" % total
    return "%d ч %02d мин" % (total // 60, total % 60)


class MeasureTool:
    """Линейка карты. Вход: PlotItem, палитра цветов, `speed_fn()` — скорость км/ч или
    None. Отдаёт: ничего — рисует сама, состояние держит внутри."""

    PREVIEW_ALPHA = 110      # прозрачность линии-предпросмотра, 0…255
    TOTAL_OFFSET_KM = 0.04   # вынос подписи суммы за последнюю точку, доля длины кадра

    def __init__(self, plot_item, colors, speed_fn=None):
        # colors — готовые QColor: line (ломаная), point и point_edge (точки), label_bg (фон подписи)
        self.pi = plot_item
        self.colors = colors
        self._speed_fn = speed_fn or (lambda: None)
        self.pts = []                                # закреплённые точки, км
        self._cursor = None                          # где курсор сейчас, км; None — вне карты
        self._labels = []                            # подписи, пересобираются целиком
        self.line = pg.PlotDataItem(pen=pg.mkPen(colors["line"], width=2.6, dash=[6, 3]),
                                    connect="all", antialias=True)
        self.line.setZValue(29)
        self.dots = pg.ScatterPlotItem(size=9, symbol="o",
                                       brush=pg.mkBrush(colors["point"]),
                                       pen=pg.mkPen(colors["point_edge"], width=1.6))
        self.dots.setZValue(30)
        # ЛИНИЯ-ПРЕДПРОСМОТР: полупрозрачная — видно, что отрезок ещё не закреплён
        self.preview = pg.PlotDataItem(pen=pg.mkPen(colors["line"], width=1.8, dash=[4, 6]),
                                       connect="all", antialias=True)
        self.preview.setZValue(28)
        self.preview.setOpacity(self.PREVIEW_ALPHA / 255.0)
        for it in (self.line, self.dots, self.preview):
            self.pi.addItem(it, ignoreBounds=True)

    # ---------- работа ----------
    def reset(self):
        """Убрать всё нарисованное и начать заново."""
        self.pts = []
        self._cursor = None
        self.redraw()

    def add_point(self, x_km, y_km):
        """Закрепить точку. Вход: км. Отдаёт: ничего."""
        self.pts.append((float(x_km), float(y_km)))
        self.redraw()

    def move_to(self, x_km, y_km):
        """Курсор переехал — обновить предпросмотр. Вход: км (None, None — курсор ушёл)."""
        self._cursor = None if x_km is None else (float(x_km), float(y_km))
        self.redraw()

    def status(self):
        """Строка для заголовка: сколько точек, длина отрезка и сумма. Отдаёт: строку."""
        if not self.pts:
            return ("ЛИНЕЙКА: щёлкните первую точку. Правая кнопка мыши или Esc — выход.")
        total = self.total_km()                      # сумма отрезков, км
        text = "ЛИНЕЙКА: точек %d, всего %.2f км" % (len(self.pts), total)
        t = self._hours(total)
        if t is not None:                            # тип выбран — время известно
            text += " (%s)" % format_time(t)
        return text + ". Правая кнопка мыши или Esc — выход."

    def total_km(self):
        """Сумма всех закреплённых отрезков, км."""
        return float(sum(np.hypot(b[0] - a[0], b[1] - a[1])
                         for a, b in zip(self.pts, self.pts[1:])))

    # ---------- внутреннее ----------
    def _hours(self, km):
        """Время на путь по скорости выбранного типа. Отдаёт: часы или None (типа нет)."""
        v = self._speed_fn()                         # скорость, км/ч; None — тип не выбран
        if not v or float(v) <= 0.0:                 # типа нет либо скорость нулевая
            return None                              # — время не выдумываем
        return float(km) / float(v)

    def _text(self, km, with_time=True):
        """Подпись: километры и, если известно, время. Отдаёт: строку."""
        s = "%.2f км" % float(km)
        if with_time:
            t = self._hours(km)
            if t is not None:
                s += "\n%s" % format_time(t)
        return s

    def _label(self, text, x, y, angle=None, anchor=(0.5, 0.5), bold=False):
        """Подпись на карте. Вход: текст, км, угол поворота °, якорь. Отдаёт: TextItem."""
        # подпись в HTML: перенос строки и жирность для суммы
        html = "<b>%s</b>" % text.replace("\n", "<br>") if bold else text.replace("\n", "<br>")
        lbl = pg.TextItem(   # сама подпись на сцене
            html=("<div style='text-align:center'>%s</div>" % html), anchor=anchor,
            fill=pg.mkBrush(self.colors["label_bg"]))
        if angle is not None:
            lbl.setAngle(float(angle))
        lbl.setPos(float(x), float(y))
        lbl.setZValue(31)
        self.pi.addItem(lbl, ignoreBounds=True)
        self._labels.append(lbl)
        return lbl

    @staticmethod
    def _angle_deg(ax, ay, bx, by):
        """Угол отрезка для поворота подписи, градусы. ⚠️ Держим текст читаемым: если
        отрезок идёт влево, разворачиваем на 180°, иначе надпись встанет вверх ногами."""
        ang = float(np.degrees(np.arctan2(by - ay, bx - ax)))   # наклон отрезка, °
        if ang > 90.0:
            ang -= 180.0
        elif ang < -90.0:
            ang += 180.0
        return ang

    def redraw(self):
        """Перерисовать линейку целиком: ломаную, точки, подписи и предпросмотр."""
        for lbl in self._labels:
            self.pi.removeItem(lbl)
        self._labels = []
        if not self.pts:
            for it in (self.line, self.dots, self.preview):
                it.setData([], [])
            return
        a = np.asarray(self.pts, float)               # точки ломаной, км
        self.line.setData(a[:, 0], a[:, 1])
        self.dots.setData(a[:, 0], a[:, 1])

        # ПОДПИСЬ КАЖДОГО ЗАКРЕПЛЁННОГО СЕГМЕНТА — ПОСЕРЕДИНЕ (заказчик: при новом клике
        # число уходит с точки на середину отрезка), повёрнута вдоль него
        for (ax, ay), (bx, by) in zip(self.pts, self.pts[1:]):
            seg = float(np.hypot(bx - ax, by - ay))   # длина отрезка, км
            self._label(self._text(seg), 0.5 * (ax + bx), 0.5 * (ay + by),
                        angle=self._angle_deg(ax, ay, bx, by))

        # СУММА — У ПОСЛЕДНЕЙ ТОЧКИ, ВЫНЕСЕНА ЗА НЕЁ как продолжение линии
        if len(self.pts) >= 2:
            self._total_label(self.pts[-2], self.pts[-1], self.total_km())

        # ПРЕДПРОСМОТР: линия за курсором и километры у самого курсора
        if self._cursor is not None:
            (lx, ly), (cx, cy) = self.pts[-1], self._cursor
            self.preview.setData([lx, cx], [ly, cy])
            live = float(np.hypot(cx - lx, cy - ly))   # до курсора, км
            self._label(self._text(live), cx, cy, anchor=(-0.1, 1.2))
        else:
            self.preview.setData([], [])

    def _total_label(self, prev_pt, last_pt, total):
        """Подпись суммы за последней точкой. Вход: предпоследняя и последняя точки, км."""
        (px, py), (lx, ly) = prev_pt, last_pt
        dx, dy = lx - px, ly - py
        d = float(np.hypot(dx, dy)) or 1.0
        # отступ в долях ширины кадра: на любом масштабе подпись отходит одинаково
        rng = self.pi.vb.viewRange()
        off = self.TOTAL_OFFSET_KM * float(rng[0][1] - rng[0][0])   # отступ, км
        self._label("всего %s" % self._text(total).replace("\n", " · "),
                    lx + dx / d * off, ly + dy / d * off,
                    angle=self._angle_deg(px, py, lx, ly), bold=True)
