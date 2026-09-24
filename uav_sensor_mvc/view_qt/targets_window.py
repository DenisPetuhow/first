# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Окно «Объекты защиты» — кнопка «Указать цель» (план 13, задача 13.13).

Сколько объектов поставить → «Задать объект»: окна прячутся, ДВОЙНОЙ клик по карте ставит
цель, правая кнопка — досрочный выход, набрали заданное число — режим закрывается и цели
применяются сами. Ниже — таблица «номер · координаты · приоритет» с правкой и «Очистить».

⚠️ ОКНО МОДЕЛИ НЕ ЗНАЕТ (MVC): строки и правки идут через колбэки, как у таблицы точек входа.
⚠️ ПРИОРИТЕТ В РОЗЫГРЫШ ПОКА НЕ ИДЁТ (заказчик 21.09.2026) — только в кольцо больших датчиков.
Колонка «Зона ПВН» и кнопки «Район защиты…» / «Снять район» — цель в лесу (план 13, В13.21).
"""
from PyQt5 import QtCore, QtWidgets

# столбец: заголовок · ключ строки · ширина, px · правится ли
_COLS = (
    ("№", "n", 40, False),
    ("Широта", "lat", 92, True),
    ("Долгота", "lon", 92, True),
    ("X, км", "x_km", 70, False),
    ("Y, км", "y_km", 70, False),
    ("Приоритет", "weight", 90, True),
    ("Сектора", "sectors", 70, True),      # галочка: компас «анализа» у этой цели
    ("Зона ПВН", "zone", 118, False),      # по застройке · задана вручную · укажите район (В13.21)
)
ROW_HEIGHT = 26          # высота строки, px — тёмная тема сплющивает ячейки


class TargetsDialog(QtWidgets.QDialog):
    """Объекты защиты. Колбэки: `rows()` — строки из модели; `on_place(n)` — поставить n
    целей мышью; `on_weight(i, доля)`, `on_move(i, lon, lat)`, `on_sectors(i, вкл)` — правка
    ('' или причина отказа); `on_clear()` — убрать все; `on_area(i)` / `on_area_clear(i)` — район
    защиты цели (В13.21). `limit` — сколько целей всего, шт."""

    def __init__(self, parent, rows, on_place, on_weight, on_move, on_clear, limit,
                 on_sectors=None, on_area=None, on_area_clear=None):
        super().__init__(parent)
        self.setWindowTitle("Объекты защиты")
        self.setWindowFlag(QtCore.Qt.WindowContextHelpButtonHint, False)
        self._rows, self._on_place = rows, on_place
        self._on_weight, self._on_move, self._on_clear = on_weight, on_move, on_clear
        self._on_sectors = on_sectors or (lambda i, on: "")   # галочка «Сектора» у цели
        self._on_area = on_area or (lambda i: None)           # «Район защиты…» — чертить мышью
        self._on_area_clear = on_area_clear or (lambda i: None)   # «Снять район»
        self._quiet = False                          # таблицу заполняет программа
        col = QtWidgets.QVBoxLayout(self)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Количество объектов:"))
        self.spin_n = QtWidgets.QSpinBox()
        self.spin_n.setRange(1, int(limit))
        self.spin_n.setValue(1)
        self.spin_n.setToolTip("Сколько целей поставить за один заход (всего не больше %d)."
                               % int(limit))
        row.addWidget(self.spin_n)
        self.btn_place = QtWidgets.QPushButton("Задать объект")
        self.btn_place.setToolTip(
            "Окна спрячутся. ДВОЙНОЙ клик по карте — поставить цель. Набрали заданное число — "
            "режим закроется и цели применятся сами. Правая кнопка мыши — закончить раньше.")
        self.btn_place.clicked.connect(lambda: self._on_place(int(self.spin_n.value())))
        row.addWidget(self.btn_place, 1)
        col.addLayout(row)

        hint = QtWidgets.QLabel(
            "Цель каждого маршрута разыгрывается случайно, подряд одна не повторяется. "
            "Приоритеты раздаются поровну, их можно править (сумма не больше 1); "
            "в розыгрыш они пока не идут — по приоритетной цели ставится кольцо больших "
            "датчиков.")
        hint.setWordWrap(True)
        col.addWidget(hint)

        self.table = QtWidgets.QTableWidget(0, len(_COLS), self)
        self.table.setHorizontalHeaderLabels([c[0] for c in _COLS])
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.table.verticalHeader().setVisible(False)
        for i, (_t, _k, width, _e) in enumerate(_COLS):
            self.table.setColumnWidth(i, width)
        self.table.itemChanged.connect(self._edited)
        col.addWidget(self.table)

        self.lbl_state = QtWidgets.QLabel("")
        self.lbl_state.setWordWrap(True)
        col.addWidget(self.lbl_state)

        row = QtWidgets.QHBoxLayout()
        self.btn_clear = QtWidgets.QPushButton("Очистить")
        self.btn_clear.setToolTip("Убрать все цели — расчёт вернётся к цели по умолчанию.")
        self.btn_clear.clicked.connect(self._clear)
        # РАЙОН ЗАЩИТЫ (В13.21): у цели в лесу зону задаёт человек — прямоугольник или круг мышью
        self.btn_area = QtWidgets.QPushButton("Район защиты…")
        self.btn_area.setToolTip("Начертить район защиты выбранной цели: датчики у неё встанут в нём, "
                                 "где им можно стоять (без леса, болот и воды).")
        self.btn_area.clicked.connect(lambda: self._with_row(self._on_area))
        self.btn_area_clear = QtWidgets.QPushButton("Снять район")
        self.btn_area_clear.setToolTip("Зона выбранной цели — снова по массиву застройки.")
        self.btn_area_clear.clicked.connect(lambda: self._with_row(self._on_area_clear))
        row.addWidget(self.btn_area)
        row.addWidget(self.btn_area_clear)
        btn_close = QtWidgets.QPushButton("Закрыть")
        btn_close.clicked.connect(self.close)
        row.addWidget(self.btn_clear)
        row.addWidget(btn_close)
        col.addLayout(row)

        self.refresh()
        self.setMinimumWidth(sum(c[2] for c in _COLS) + 60)
        self.adjustSize()

    def refresh(self, note=""):
        """Перечитать строки из модели и показать их. Вход: строка-пояснение под таблицей."""
        rows = self._rows() or []
        self._quiet = True
        try:
            self.table.setRowCount(0)
            for r in rows:
                self._add_row(r)
        finally:
            self._quiet = False
        total = sum(r["weight"] for r in rows)       # сумма приоритетов, доля
        if not rows:                                 # целей не ставили
            text = "Целей нет — расчёт идёт к цели по умолчанию (метка участка или центр круга)."
        else:
            text = "Целей: %d · сумма приоритетов %.2f" % (len(rows), total)
            top = [r["n"] for r in rows if r.get("top")]   # номер приоритетной цели
            if top:                                  # одна с наибольшей долей
                text += " · кольцо больших датчиков — у цели №%d" % top[0]
            elif len(rows) > 1:                      # доли равны
                text += ("<br>⚠️ Приоритеты равны: большие датчики автоматически не "
                         "ставятся — задайте их позиции вручную.")
        if note:                                     # отказ или подсказка после правки
            text += "<br>" + note
        self.lbl_state.setText(text)

    def _add_row(self, r):
        """Дописать строку таблицы. Вход: словарь из модели. Отдаёт: ничего."""
        i = self.table.rowCount()                    # номер новой строки
        self.table.insertRow(i)
        for j, (_t, key, _w, editable) in enumerate(_COLS):
            val = r.get(key)
            if key == "sectors":                     # галочка, а не текст
                item = QtWidgets.QTableWidgetItem("")
                item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(QtCore.Qt.Checked if val else QtCore.Qt.Unchecked)
                item.setToolTip("Показывать у этой цели компас секторов (галочка «Сектора» "
                                "в группе «пролёт БПЛА»).")
                self.table.setItem(i, j, item)
                continue
            if key in ("n", "zone"):
                text = str(val if val is not None else "")
            elif key in ("lat", "lon"):
                text = "%.5f" % val
            elif key == "weight":
                text = "%.2f" % val
            else:
                text = "%.1f" % val
            item = QtWidgets.QTableWidgetItem(text)
            if not editable:                         # считаем мы — править нечего
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
            if key == "n" and r.get("top"):          # приоритетная цель
                item.setToolTip("Приоритетная: кольцо больших датчиков строится здесь.")
            self.table.setItem(i, j, item)

    def _edited(self, item):
        """Ячейку изменили: приоритет или координату отдать контроллеру, отказ показать."""
        if self._quiet:                              # заполняем программно
            return
        key = _COLS[item.column()][1]                # что правили
        i = item.row()                               # номер цели с нуля
        try:
            if key == "sectors":                     # галочку компаса переключили
                note = self._on_sectors(i, item.checkState() == QtCore.Qt.Checked)
            elif key == "weight":
                note = self._on_weight(i, float(item.text().strip().replace(",", ".")))
            else:                                    # широта или долгота — нужны обе
                lat = float(self.table.item(i, 1).text().strip().replace(",", "."))   # градусы
                lon = float(self.table.item(i, 2).text().strip().replace(",", "."))   # градусы
                note = self._on_move(i, lon, lat)
        except (ValueError, AttributeError):         # не число или ячейка пуста
            note = "Координаты и приоритет вводятся числом."
        self.refresh("⚠️ " + note if note else "")

    def _clear(self):
        """Кнопка «Очистить»: убрать все цели."""
        self._on_clear()
        self.refresh()

    def _with_row(self, fn):
        """Кнопка района: позвать колбэк с номером выбранной цели. Вход: колбэк f(номер с нуля)."""
        i = self.table.currentRow()                  # выбранная строка
        if i < 0:                                    # не выбрана
            self.refresh("⚠️ Выберите цель в таблице.")
            return
        fn(i)
        self.refresh()
