# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Таблица точек входа — кнопка «Показать данные» окна «Сектор налёта»
(план 12, задача 12.5).

Что видно по каждой точке: номер, координаты, азимут от цели, расстояние от старта БПЛА
и **запас хода**, который у неё останется. Запас можно ввести руками — он перебивает
расчётный у этой одной точки.

⚠️ РУЧНОЙ ЗАПАС ЖИВЁТ ДО ПЕРЕОТБОРА ТОЧЕК: он привязан к номеру, а при смене сектора
точки другие. Модель чистит правки сама (`_compose_entries`).
"""
from PyQt5 import QtCore, QtWidgets

# столбец: заголовок · ключ строки · ширина, px · правится ли
_COLS = (
    ("№", "n", 40, False),
    ("Широта", "lat", 92, False),
    ("Долгота", "lon", 92, False),
    ("Азимут от цели", "az", 110, False),
    ("От старта, км", "dist", 105, False),
    ("Запас хода, км", "L_max", 115, True),
)
ROW_HEIGHT = 26          # высота строки, px — тёмная тема сплющивает ячейки


class EntriesTableDialog(QtWidgets.QDialog):
    """Точки входа таблицей. Вход: родитель; `rows()` — строки из модели,
    `on_L_max(номер, км)` — правка запаса (км = None вернёт расчётный)."""

    def __init__(self, parent, rows, on_L_max):
        super().__init__(parent)
        self.setWindowTitle("Точки входа")
        self.setWindowFlag(QtCore.Qt.WindowContextHelpButtonHint, False)
        self._rows, self._on_L_max = rows, on_L_max
        self._quiet = False                          # таблицу заполняет программа
        col = QtWidgets.QVBoxLayout(self)

        hint = QtWidgets.QLabel(
            "Запас хода — сколько останется у аппарата, дошедшего до этой точки. "
            "Введите своё число, чтобы задать его вручную; пустая ячейка вернёт расчётный.")
        hint.setWordWrap(True)
        col.addWidget(hint)

        self.table = QtWidgets.QTableWidget(0, len(_COLS), self)
        self.table.setHorizontalHeaderLabels([c[0] for c in _COLS])
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        for i, (_t, _k, width, _e) in enumerate(_COLS):
            self.table.setColumnWidth(i, width)
        self.table.itemChanged.connect(self._edited)
        col.addWidget(self.table)

        self.lbl_state = QtWidgets.QLabel("")
        self.lbl_state.setWordWrap(True)
        col.addWidget(self.lbl_state)

        row = QtWidgets.QHBoxLayout()
        btn_reset = QtWidgets.QPushButton("Вернуть расчётные")
        btn_reset.setToolTip("Снять все введённые вручную запасы хода.")
        btn_reset.clicked.connect(self._reset_all)
        btn_close = QtWidgets.QPushButton("Закрыть")
        btn_close.clicked.connect(self.close)
        row.addWidget(btn_reset)
        row.addWidget(btn_close)
        col.addLayout(row)

        self.refresh()
        self.setMinimumWidth(sum(c[2] for c in _COLS) + 60)
        self.adjustSize()

    def refresh(self):
        """Перечитать строки из модели и показать их. Отдаёт: ничего."""
        rows = self._rows() or []
        self._quiet = True
        try:
            self.table.setRowCount(0)
            for r in rows:
                self._add_row(r)
        finally:
            self._quiet = False
        n_manual = sum(1 for r in rows if r.get("manual"))     # задано руками, шт.
        lmax = [r["L_max"] for r in rows if r.get("L_max") is not None]   # запасы, км
        text = "Точек: %d" % len(rows)
        if lmax:                                     # запасы известны
            text += " · запас от %.0f до %.0f км, среднее %.0f км" % (
                min(lmax), max(lmax), sum(lmax) / len(lmax))
        if n_manual:                                 # часть задана руками
            text += " · задано вручную: %d" % n_manual
        if rows and rows[0].get("dist") is None:     # старт БПЛА не поставлен
            text += "<br>⚠️ Точка старта не задана — запас у всех общий, из панели."
        self.lbl_state.setText(text)

    def _add_row(self, r):
        """Дописать строку таблицы. Вход: словарь из модели. Отдаёт: ничего."""
        i = self.table.rowCount()                    # номер новой строки
        self.table.insertRow(i)
        for j, (_t, key, _w, editable) in enumerate(_COLS):
            val = r.get(key)
            if val is None:                          # старт не задан — расстояния нет
                text = "—"
            elif key == "n":
                text = str(val)
            elif key in ("lat", "lon"):
                text = "%.5f" % val
            else:
                text = "%.1f" % val
            item = QtWidgets.QTableWidgetItem(text)
            if not editable:                         # считаем мы — править нечего
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
            elif r.get("manual"):                    # запас введён человеком
                item.setToolTip("Задано вручную. Очистите ячейку, чтобы вернуть расчётный.")
            self.table.setItem(i, j, item)

    def _edited(self, item):
        """Ячейку изменили: если это запас хода — отдать его модели."""
        if self._quiet or _COLS[item.column()][1] != "L_max":
            return                                   # заполняем программно или столбец чужой
        text = item.text().strip().replace(",", ".")
        if not text or text == "—":                  # очистили — вернуть расчётный
            self._on_L_max(item.row(), None)
        else:
            try:
                self._on_L_max(item.row(), float(text))
            except ValueError:                       # не число
                self.lbl_state.setText("Запас хода вводится числом, км.")
        self.refresh()

    def _reset_all(self):
        """Кнопка «Вернуть расчётные»: снять все ручные запасы."""
        for i in range(self.table.rowCount()):
            self._on_L_max(i, None)
        self.refresh()
