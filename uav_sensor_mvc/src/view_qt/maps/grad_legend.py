# -*- coding: utf-8 -*-
"""
VIEW · Пояснение градаций слоя (план 13.5, задача 13.5.1): компактная шкала «цвет — значение» поверх окна карты, справа
сверху, под плашкой режимов. Образец — `фото пример/Снимок экрана 2026-10-02 015613.png`.

⚠️ ВНЕ РАСКЛАДКИ ГРАФИКА. Прежняя шкала `pg.ColorBarItem` вставлялась в раскладку и, появляясь, отнимала колонку справа:
вид карты сужался («съезжает, ужата на 0.97»). Плашка — дочерний виджет окна карты: геометрию графика не трогает, щелчки
пропускает к карте. Какую шкалу показать (последний включённый слой), решает `ThreatMapView._update_scale`.
"""
from PyQt5 import QtCore, QtWidgets


def ink_for(rgb):
    """Цвет надписи на цветной клетке: чёрный на светлом, белый на тёмном. Вход: (r, g, b) 0…255. Отдаёт: «#rrggbb»."""
    r, g, b = (float(c) for c in rgb[:3])            # каналы фона
    return "#111111" if 0.299 * r + 0.587 * g + 0.114 * b > 140 else "#ffffff"   # яркость по ITU-R BT.601


class GradLegend(QtWidgets.QFrame):
    """Плашка шкалы поверх карты. Вход: виджет карты (родитель). Отдаёт: виджет; `show_rows` — заполнить и показать,
    `place` — прижать к правому краю."""

    ROWS_MAX = 12                                    # строк шкалы больше не бывает (категории + непрерывные)
    MARGIN_PX = 8                                    # отступ от правого края окна карты, px

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("gradLegend")
        # ⚠️ щелчки и колесо — карте: плашка лежит поверх неё и иначе съедала бы перетаскивание вида
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("#gradLegend { background: #ffffff; border: 1px solid #9aa3ad; border-radius: 6px; }")
        lay = QtWidgets.QVBoxLayout(self)            # заголовок и строки сверху вниз
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(1)
        self._title = QtWidgets.QLabel()             # что за величина и в чём
        self._title.setAlignment(QtCore.Qt.AlignCenter)
        # ⚠️ фон — явно: тёмная тема окна красит фон всех надписей, и заголовок выходил тёмным по тёмному
        self._title.setStyleSheet("background:#ffffff; color:#222222; font-size:8pt; font-weight:bold; padding:0 2px;")
        lay.addWidget(self._title)
        self._cells = []                             # строки шкалы — подписи на цветном фоне
        for _ in range(self.ROWS_MAX):               # заготовка строк: прячутся лишние
            cell = QtWidgets.QLabel()                # строка шкалы
            cell.setAlignment(QtCore.Qt.AlignCenter)
            lay.addWidget(cell)
            self._cells.append(cell)
        self.key = None                              # чья шкала сейчас показана (ключ слоя) — для проверок
        self.hide()

    def show_rows(self, key, title, rows):
        """Заполнить шкалу и показать. Вход: ключ слоя, заголовок, строки [((r, g, b), подпись)] сверху вниз.
        Отдаёт: ничего."""
        self.key = key
        self._title.setText(title)
        for i, cell in enumerate(self._cells):       # строки по порядку; лишние — спрятать
            if i >= len(rows):                       # строк меньше заготовки
                cell.hide()
                continue
            rgb, text = rows[i]                      # цвет клетки и подпись
            hexc = "#%02x%02x%02x" % tuple(int(round(c)) for c in rgb[:3])   # цвет клетки
            cell.setText(text)
            cell.setStyleSheet("background:%s; color:%s; font-size:8pt; padding:1px 6px; border-radius:2px;"
                               % (hexc, ink_for(rgb)))
            cell.show()
        self.adjustSize()
        self.show()
        self.raise_()

    def hide_rows(self):
        """Спрятать шкалу: ни одного слоя с градацией не включено. Вход: нет. Отдаёт: ничего."""
        self.key = None
        self.hide()

    def place(self, top_px):
        """Прижать к правому краю окна карты. Вход: верх плашки, px от верха окна. Отдаёт: ничего."""
        par = self.parentWidget()                    # окно карты
        if par is None:                              # не на чем держаться
            return
        self.adjustSize()
        self.move(max(0, par.width() - self.width() - self.MARGIN_PX), int(top_px))
