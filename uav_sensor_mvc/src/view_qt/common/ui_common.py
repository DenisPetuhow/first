# -*- coding: utf-8 -*-
"""
VIEW · Общие детали интерфейса для всех трёх вкладок.

Здесь лежит то, что раньше было СКОПИРОВАНО в каждую вкладку: оформление (тёмная тема)
и правая панель параметров с прокруткой. Копии были побайтово одинаковыми, поэтому
правка темы или ширины панели в одном файле не доходила до остальных.

Правило: сюда попадает только то, что у всех вкладок ОБЯЗАНО совпадать. Всё, что у
вкладки своё (набор полей, слои, кнопки), остаётся в её собственном файле.

Настройка внешнего вида — [теория/НАСТРОЙКА_ИНТЕРФЕЙСА.md](../теория/НАСТРОЙКА_ИНТЕРФЕЙСА.md).
"""
from PyQt5 import QtCore, QtWidgets
import pyqtgraph as pg

from config import THEME

# Ширина правой панели. ДВА числа, а не одно: панель уже прокручиваемой области ровно
# на ширину полосы прокрутки, иначе полоса налезает на поля ввода.
PANEL_MIN_WIDTH = 360        # сама панель с параметрами
PANEL_SCROLL_WIDTH = 394     # область прокрутки вокруг неё
PANEL_MARGIN = 12            # отступ от краёв панели
PANEL_SPACING = 8            # промежуток между строками
ALL_TARGETS = "Все цели"     # первая строка выбора цели — по всей выборке


def fill_target_combo(combo, labels, keep):
    """Список целей: «Все цели» и подписи по номерам; прежний выбор сохраняется, цель одна — выбор закрыт.
    Вход: QComboBox, подписи целей (пусто — цель одна), номер выбранной цели или None. Отдаёт: None."""
    combo.blockSignals(True)
    combo.clear()
    combo.addItem(ALL_TARGETS, None)
    for i, text in enumerate(labels):              # цель — строка выбора, данные — номер с нуля
        combo.addItem(text, i)
    i = combo.findData(keep)                       # прежний выбор в новом списке
    combo.setCurrentIndex(i if keep is not None and i >= 0 else 0)
    combo.blockSignals(False)
    combo.setEnabled(bool(labels))


def qcolor(hex_or_rgba, alpha=None):
    """Цвет Qt из «#rrggbb» или (r, g, b[, a]); alpha поверх — прозрачность 0–255. Одно определение на все вкладки.
    Вход: цвет, прозрачность или None. Отдаёт: QColor."""
    c = pg.mkColor(hex_or_rgba)                    # цвет как QColor
    if alpha is not None:                          # прозрачность задана → поверх своей
        c.setAlpha(int(alpha))
    return c


def apply_dark_theme(widget):
    """Тёмное оформление вкладки. Цвета — из `THEME` (`config.py`), правка там меняет
    сразу все три вкладки.

    Раньше эта таблица стилей была скопирована в `view_qt.py` и `threat_view.py`
    побайтово (1153 символа), и правка цвета в одной вкладке не доходила до другой."""
    widget.setStyleSheet(f"""
        QWidget {{ background: {THEME['bg']}; color: {THEME['text']};
                   font-size: 12px; }}
        QFrame#panel {{ background: {THEME['panel']};
                        border: 1px solid {THEME['grid']}; border-radius: 8px; }}
        QLabel#header {{ color: {THEME['accent']}; font-weight: bold; }}
        QLabel#muted {{ color: {THEME['muted']}; font-size: 10px; }}
        QLineEdit {{ background: #e6edf3; color: #10202f; border-radius: 4px;
                     padding: 3px; }}
        QPushButton {{ background: {THEME['grid']}; color: white;
                       border-radius: 6px; padding: 7px; font-weight: bold; }}
        QPushButton:hover {{ background: {THEME['accent']}; }}
        QRadioButton, QCheckBox {{ color: {THEME['text']}; font-size: 12px; }}
        QPlainTextEdit {{ background: {THEME['axes']}; color: {THEME['text']};
                          border: 1px solid {THEME['grid']}; border-radius: 6px;
                          font-family: monospace; font-size: 11px; }}
    """)


def make_side_panel():
    """Правая панель параметров в прокручиваемой области.

    Возвращает `(scroll, col)`: сам виджет для добавления в компоновку и вертикальную
    компоновку `col`, куда вкладка складывает свои поля.

    Прокрутка нужна вот зачем: при низком окне поля не должны сжиматься до нечитаемого
    состояния — вместо этого появляется вертикальная полоса. Горизонтальная выключена
    намеренно: панель фиксированной ширины, ездить вбок ей незачем."""
    panel = QtWidgets.QFrame()
    panel.setObjectName("panel")
    panel.setMinimumWidth(PANEL_MIN_WIDTH)
    col = QtWidgets.QVBoxLayout(panel)
    col.setContentsMargins(PANEL_MARGIN, PANEL_MARGIN, PANEL_MARGIN, PANEL_MARGIN)
    col.setSpacing(PANEL_SPACING)

    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFixedWidth(PANEL_SCROLL_WIDTH)
    scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    scroll.setWidget(panel)
    return scroll, col
