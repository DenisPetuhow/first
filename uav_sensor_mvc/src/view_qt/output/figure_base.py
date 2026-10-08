# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Модуль вывода — ОБЩАЯ ОСНОВА окон с рисунком «Аналитики» (заказчик 06.10.2026): «Анализ нагрузки»,
«Показ сектора», «Показ карты».

Слева — настройки (цель, что и как рисовать, оформление, размер PNG), справа — рисунок matplotlib с панелью масштаба.
Любая настройка перерисовывает рисунок; «Сохранить PNG…» рисует ЗАНОВО в рисунок заданного размера (см) и разрешения —
файл не зависит от размера окна. Подкласс объявляет свои настройки (`_build_settings`) и рисует (`_draw`) функциями
`figures.py`; общие помощники `_add_*` кладут виджет в форму и в словарь настроек — `opts()` собирает его сам.
⚠️ ОКНО НИЧЕГО НЕ СЧИТАЕТ: снимок данных отдаёт колбэк `data(цель)` (контроллер → `report_data`) — на момент открытия
или «Обновить данные», как у «Аналитики».
"""
import datetime

from PyQt5 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure

from view_qt.output.figures import FAMILIES

ALL_TARGETS = "Все цели"                             # первая строка выбора цели
KEEP = "keep"                                        # refresh: цель оставить, как выбрана


def _value(w):
    """Значение виджета настройки. Вход: виджет. Отдаёт: bool, число, данные строки списка, текст или цвет."""
    if isinstance(w, QtWidgets.QCheckBox):           # галка
        return w.isChecked()
    if isinstance(w, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):   # число
        return w.value()
    if isinstance(w, QtWidgets.QComboBox):           # список — данные строки (None — «все»)
        return w.currentData()
    if isinstance(w, QtWidgets.QLineEdit):           # текст
        return w.text().strip()
    return w.property("color")                       # кнопка цвета


class FigureDialog(QtWidgets.QDialog):
    """Немодальное окно рисунка. Вход: родитель; колбэки `data(цель)` → снимок, `choices()` → подписи целей,
    `colors(номера типов)` → {тип: цвет карты}; цель (номер с нуля, None — все). Подкласс: TITLE, FILE, `_draw`."""
    TITLE = "Рисунок"                                # заголовок окна
    FILE = "рисунок"                                 # начало имени файла PNG
    SIZE_CM = (17.0, 11.0)                           # размер PNG по умолчанию, см
    COLORS = False                                   # есть выбор «цвета как на карте / как в Word»

    def __init__(self, parent, data, choices=None, colors=None, target=None):
        super().__init__(parent)
        self.setWindowTitle(self.TITLE)
        self.resize(1150, 720)
        self._data = data                            # снимок данных по цели
        self._choices = choices or (lambda: [])      # подписи целей; пусто — цель одна
        self._colors = colors or (lambda tids: None)   # цвета типов как на карте
        self._snap = {}                              # последний снимок данных
        self._w = {}                                 # настройки рисунка: ключ -> виджет
        self._timer = QtCore.QTimer(self)            # перерисовка не на каждый щелчок счётчика
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.redraw)
        root = QtWidgets.QHBoxLayout(self)           # слева настройки, справа рисунок
        side = QtWidgets.QWidget()                   # панель настроек
        self.form = QtWidgets.QFormLayout(side)      # строки настроек
        scroll = QtWidgets.QScrollArea()             # настроек много — с прокруткой
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(370)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)   # длинная подпись — не повод листать вбок
        left = QtWidgets.QVBoxLayout()               # настройки и кнопки
        left.addWidget(scroll, 1)
        self.combo_target = self._combo_box()        # «Все цели» или одна
        self.combo_target.currentIndexChanged.connect(lambda _i: self.refresh(KEEP, fill=False))
        self.form.addRow("Цель:", self.combo_target)
        self._build_settings()
        self._section("Оформление")
        if self.COLORS:                              # у рисунка есть цвета типов средств
            self._add_combo("colors", "Цвета типов:", [("map", "как на карте"), ("word", "как в Word")], "map")
        self._add_line("title", "Заголовок:", "")
        self._add_combo("family", "Шрифт:", [(f, f) for f in FAMILIES], FAMILIES[0])
        self._add_spin("font", "Размер шрифта:", 11, 6, 28)
        self._section("Сохранение PNG")
        self.png_w = self._spin_box(self.SIZE_CM[0], 3.0, 100.0, 0.5, 1, " см")   # ширина файла, см
        self.png_h = self._spin_box(self.SIZE_CM[1], 3.0, 100.0, 0.5, 1, " см")   # высота файла, см
        self.png_dpi = self._spin_box(200, 72, 600, 10, 0, " dpi")                # разрешение файла
        self.form.addRow("Ширина:", self.png_w)
        self.form.addRow("Высота:", self.png_h)
        self.form.addRow("Разрешение:", self.png_dpi)
        for title, slot in (("Сохранить PNG…", lambda: self.save_png()), ("Обновить данные", lambda: self.refresh()),
                            ("Закрыть", self.close)):   # кнопка: подпись и обработчик
            b = QtWidgets.QPushButton(title)         # кнопка окна
            b.clicked.connect(lambda _c=False, s=slot: s())
            left.addWidget(b)
        root.addLayout(left)
        right = QtWidgets.QVBoxLayout()              # рисунок с панелью масштаба
        self.fig = Figure(layout="constrained")      # рисунок на экране — под размер окна
        self.canvas = FigureCanvasQTAgg(self.fig)    # холст Qt
        self.toolbar = NavigationToolbar2QT(self.canvas, self)   # масштаб, сдвиг, вид по умолчанию
        self.lbl_info = QtWidgets.QLabel("")         # выборка, цель, что записано
        self.lbl_info.setWordWrap(True)
        right.addWidget(self.toolbar)
        right.addWidget(self.canvas, 1)
        right.addWidget(self.lbl_info)
        root.addLayout(right, 1)
        self._fill_targets(target)

    # ---- настройки: помощники подкласса ----
    def _section(self, text):
        """Заголовок группы настроек. Вход: текст. Отдаёт: ничего."""
        lab = QtWidgets.QLabel("<b>%s</b>" % text)   # подпись группы
        lab.setContentsMargins(0, 8, 0, 0)
        self.form.addRow(lab)

    def _add(self, key, label, w, signal):
        """Положить виджет в форму и в настройки; смена — перерисовка. Вход: ключ, подпись (None — без), виджет,
        сигнал смены. Отдаёт: виджет."""
        self._w[key] = w
        signal.connect(lambda *_a: self._timer.start())
        if label is None:                            # галка — подпись в ней самой
            self.form.addRow(w)
        else:
            self.form.addRow(label, w)
        return w

    def _spin_box(self, val, lo, hi, step=1, dec=0, suffix=""):
        """Счётчик. Вход: значение, пределы, шаг, знаков после запятой, единица. Отдаёт: виджет."""
        w = QtWidgets.QSpinBox() if dec == 0 else QtWidgets.QDoubleSpinBox()   # целый или дробный
        if dec:                                      # дробный — сколько знаков
            w.setDecimals(dec)
        w.setRange(lo, hi)
        w.setSingleStep(step)
        w.setValue(val)
        w.setSuffix(suffix)
        return w

    @staticmethod
    def _combo_box():
        """Список, который сжимается под панель (длинная строка не распирает её). Отдаёт: виджет."""
        w = QtWidgets.QComboBox()                    # список
        w.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        w.setMinimumContentsLength(8)
        return w

    def _add_check(self, key, text, on):
        """Галка настройки. Вход: ключ, подпись, включена ли. Отдаёт: виджет."""
        w = QtWidgets.QCheckBox(text)                # галка
        w.setChecked(bool(on))
        return self._add(key, None, w, w.toggled)

    def _add_spin(self, key, label, val, lo, hi, step=1, dec=0, suffix=""):
        """Числовая настройка. Вход: ключ, подпись, значение, пределы, шаг, знаков, единица. Отдаёт: виджет."""
        w = self._spin_box(val, lo, hi, step, dec, suffix)   # счётчик
        return self._add(key, label, w, w.valueChanged)

    def _add_combo(self, key, label, items, val):
        """Выбор из списка. Вход: ключ, подпись, [(данные, подпись)], выбранные данные. Отдаёт: виджет."""
        w = self._combo_box()                        # список
        for d, text in items:                        # строка списка
            w.addItem(text, d)
        i = w.findData(val)                          # строка выбранного
        w.setCurrentIndex(max(0, i))
        return self._add(key, label, w, w.currentIndexChanged)

    def _add_line(self, key, label, text):
        """Текстовая настройка. Вход: ключ, подпись, текст. Отдаёт: виджет."""
        w = QtWidgets.QLineEdit(text)                # строка ввода
        return self._add(key, label, w, w.textChanged)

    def _add_color(self, key, label, color):
        """Цвет: кнопка с образцом, щелчок — выбор цвета. Вход: ключ, подпись, «#rrggbb». Отдаёт: виджет."""
        w = QtWidgets.QPushButton()                  # кнопка-образец
        self._paint_color(w, color)
        w.clicked.connect(lambda: self._pick_color(w))
        self._w[key] = w
        self.form.addRow(label, w)
        return w

    def _paint_color(self, w, color):
        """Запомнить цвет в кнопке и закрасить её. Вход: кнопка, «#rrggbb». Отдаёт: ничего."""
        w.setProperty("color", color)
        w.setText(color)
        w.setStyleSheet("background:%s; color:%s;" % (color, "#000" if QtGui.QColor(color).lightness() > 128
                                                     else "#fff"))

    def _pick_color(self, w):
        """Выбрать цвет в стандартном окне; отмена — без изменений. Вход: кнопка. Отдаёт: ничего."""
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(w.property("color")), self, "Цвет")   # выбранный цвет
        if c.isValid():                              # выбор подтвердили
            self._paint_color(w, c.name())
            self._timer.start()

    def set_opts(self, values):
        """Выставить настройки разом (готовый вид), без лишних перерисовок. Вход: {ключ: значение}. Отдаёт: ничего."""
        for k, v in values.items():                  # настройка за настройкой
            w = self._w.get(k)                       # её виджет; нет — настройка не из этого окна
            if w is None:                            # чужая настройка — пропустить
                continue
            w.blockSignals(True)
            if isinstance(w, QtWidgets.QCheckBox):   # галка
                w.setChecked(bool(v))
            elif isinstance(w, QtWidgets.QSpinBox):   # целое — PyQt5 дробное не примет
                w.setValue(int(round(v)))
            elif isinstance(w, QtWidgets.QDoubleSpinBox):   # дробное
                w.setValue(float(v))
            elif isinstance(w, QtWidgets.QComboBox):   # список — по данным строки
                w.setCurrentIndex(max(0, w.findData(v)))
            elif isinstance(w, QtWidgets.QLineEdit):   # текст
                w.setText(str(v))
            else:                                    # кнопка цвета
                self._paint_color(w, v)
            w.blockSignals(False)
        self._timer.start()

    def opts(self):
        """Настройки рисунка с экрана. Отдаёт: {ключ: значение} — ключи `figures.*_DEFAULTS`."""
        return {k: _value(w) for k, w in self._w.items()}

    # ---- данные и рисунок ----
    def _fill_targets(self, target=KEEP):
        """Список целей: «Все цели» и подписи из `choices()`; цель одна — выбор закрыт. Вход: цель или KEEP.
        Отдаёт: ничего."""
        keep = self.combo_target.currentData() if target == KEEP else target   # какую цель выбрать
        labels = self._choices() or []               # «Цель №N — маршрутов M»; пусто — цель одна
        self.combo_target.blockSignals(True)
        self.combo_target.clear()
        self.combo_target.addItem(ALL_TARGETS, None)
        for i, text in enumerate(labels):            # цель — строка выбора, данные — номер с нуля
            self.combo_target.addItem(text, i)
        i = self.combo_target.findData(keep)         # выбор в новом списке
        self.combo_target.setCurrentIndex(i if keep is not None and i >= 0 else 0)
        self.combo_target.blockSignals(False)
        self.combo_target.setEnabled(bool(labels))

    def refresh(self, target=KEEP, fill=True):
        """Новый снимок данных по цели и перерисовка. Вход: цель (номер с нуля, None — все, KEEP — как выбрана),
        fill — перечитать список целей. Отдаёт: ничего."""
        if fill:                                     # открыли окно или «Обновить данные» — цели могли смениться
            self._fill_targets(target)
        self._snap = self._data(self.combo_target.currentData()) or {}
        self._on_snapshot()
        self.redraw()

    def _on_snapshot(self):
        """Пришёл новый снимок — подкласс обновляет свои списки (типы и т. п.). Отдаёт: ничего."""

    def _draw(self, fig, snap, opts, colors):
        """Нарисовать рисунок — подкласс функцией `figures.py`. Вход: рисунок, снимок, настройки, цвета. Отдаёт: оси."""
        raise NotImplementedError

    def _info(self):
        """Строки пояснения под рисунком — подкласс. Отдаёт: список строк."""
        return []

    def _draw_into(self, fig):
        """Нарисовать текущие данные с текущими настройками в рисунок. Вход: рисунок. Отдаёт: оси."""
        o = self.opts()                              # настройки с экрана
        tids = sorted((self._snap.get("types") or {}).keys())   # номера типов снимка
        colors = self._colors(tids) if o.pop("colors", "word") == "map" else None   # цвета типов; None — как в Word
        return self._draw(fig, self._snap, o, colors)

    def redraw(self):
        """Перерисовать рисунок на экране и пояснение под ним. Отдаёт: ничего."""
        self._timer.stop()
        try:
            self._draw_into(self.fig)
            msg = self._info()                       # пояснение
        except Exception as e:                       # рисунок не построился — сказать, а не упасть
            self.fig.clear()
            msg = ["Рисунок не построен: %s" % e]
        self.canvas.draw_idle()
        self.lbl_info.setText("<br>".join(msg))

    def save_png(self, path=None):
        """«Сохранить PNG…»: рисунок заново в заданном размере (см) и разрешении. Вход: путь или None — спросить.
        Отдаёт: путь записанного файла или None."""
        if path is None:                             # путь не задан — окно выбора файла
            name = "%s_%s.png" % (self.FILE, datetime.datetime.now().strftime("%Y%m%d_%H%M"))   # имя по умолчанию
            path, _flt = QtWidgets.QFileDialog.getSaveFileName(self, "Сохранить PNG", name, "PNG (*.png)")
            if not path:                             # человек передумал
                return None
        if not path.lower().endswith(".png"):        # расширение не указали
            path += ".png"
        fig = Figure(figsize=(self.png_w.value() / 2.54, self.png_h.value() / 2.54), layout="constrained")   # файл, дюймы
        FigureCanvasAgg(fig)
        try:
            self._draw_into(fig)
            fig.savefig(path, dpi=self.png_dpi.value())
        except Exception as e:                       # не записалось — сказать, где
            self.lbl_info.setText("<br>".join(self._info() + ["<b>Ошибка записи: %s</b>" % e]))
            return None
        self.lbl_info.setText("<br>".join(self._info() + ["<b>Записано: %s</b>" % path]))
        return path
