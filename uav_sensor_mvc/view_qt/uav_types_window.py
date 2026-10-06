# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Окно «Виды БПЛА» — справочник типов (план 12, задача 12.3).

Таблица характеристик: добавить · изменить · удалить. Правится прямо в ячейках, при
сохранении числа проверяются (`UavType.validate`) и справочник пишется в
`geo_cache/типы_бпла.json`.

⚠️ НОВЫЙ МЕХАНИЗМ — СВОЙ ФАЙЛ (правило проекта, CLAUDE.md), тем же приёмом, что
`sector_window.py`. Окно модальное: справочник с картой не связан, а два открытых
редактора одних данных разошлись бы.
"""
from PyQt5 import QtCore, QtWidgets

from model import uav_types as ut

# столбец таблицы: заголовок · поле типа · ширина, px
_COLS = (
    ("Название", "name", 150),
    ("Класс", "KIND", 130),
    ("Скорость, км/ч", "speed_kmh", 110),
    ("Крен, °", "bank_deg", 80),
    ("Запас хода, км", "L_max_km", 110),
    ("Высота, м", "alt_m", 90),
)
ROW_HEIGHT = 28          # высота строки, px — тёмная тема сплющивает поля до нечитаемых


class UavTypesDialog(QtWidgets.QDialog):
    """Справочник типов БПЛА. Вход: родитель; `on_saved(типы)` — что вызвать после записи.
    Отдаёт: ничего — новый список приходит колбэком."""

    def __init__(self, parent, on_saved=None):
        super().__init__(parent)
        self.setWindowTitle("Виды БПЛА")
        self.setWindowFlag(QtCore.Qt.WindowContextHelpButtonHint, False)
        self.setModal(True)
        self._on_saved = on_saved
        col = QtWidgets.QVBoxLayout(self)

        hint = QtWidgets.QLabel(
            "Характеристики аппарата. Скорость и крен задают радиус разворота, запас хода — "
            "предел длины маршрута. Высота хранится, но в расчёт пока не идёт.")
        hint.setWordWrap(True)
        col.addWidget(hint)

        self.table = QtWidgets.QTableWidget(0, len(_COLS), self)
        self.table.setHorizontalHeaderLabels([c[0] for c in _COLS])
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        for i, (_, _, width) in enumerate(_COLS):
            self.table.setColumnWidth(i, width)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        col.addWidget(self.table)

        self.lbl_state = QtWidgets.QLabel("")
        self.lbl_state.setWordWrap(True)
        col.addWidget(self.lbl_state)

        row = QtWidgets.QHBoxLayout()
        for title, slot, tip in (
                ("Добавить", self._add, "Новая строка с умолчаниями — поправьте её в таблице."),
                ("Удалить", self._remove, "Убрать выделенную строку."),
                ("Вернуть исходные", self._reset, "Встроенный список: «Лютый» и «Коптер»."),
                ("Сохранить", self._save, "Проверить числа и записать справочник."),
                ("Закрыть", self.reject, "Закрыть без записи несохранённых правок.")):
            btn = QtWidgets.QPushButton(title)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        col.addLayout(row)

        self._fill(ut.load())
        self.setMinimumWidth(sum(c[2] for c in _COLS) + 60)
        self.adjustSize()

        self.table.horizontalHeader().setStyleSheet("color: #000000; background: #eeeeee;")

    def _fill(self, types):
        """Показать список в таблице. Вход: список типов. Отдаёт: ничего."""
        self.table.setRowCount(0)
        for uav in types:
            self._add_row(uav)
        self.lbl_state.setText("Типов: %d · файл: %s" % (len(types), ut.FILE_NAME))

    def _add_row(self, uav):
        """Дописать строку таблицы. Вход: тип. Отдаёт: ничего."""
        r = self.table.rowCount()                    # номер новой строки
        self.table.insertRow(r)
        for i, (_, field, _w) in enumerate(_COLS):
            if field == "KIND":                      # класс выбирают из списка,
                box = QtWidgets.QComboBox()          # — руками его не набрать
                box.addItems(sorted(ut.KINDS))
                box.setCurrentText(uav.KIND)
                self.table.setCellWidget(r, i, box)
            else:
                val = getattr(uav, field)
                text = val if field == "name" else ("%g" % float(val))
                self.table.setItem(r, i, QtWidgets.QTableWidgetItem(text))

    def _row_type(self, r):
        """Тип из строки таблицы. Вход: номер строки. Отдаёт: UavType (битые числа —
        умолчания класса, о них скажет проверка)."""
        box = self.table.cellWidget(r, 1)            # выпадающий список класса
        row = {"класс": box.currentText() if box else ""}
        for i, (_, field, _w) in enumerate(_COLS):
            if field == "KIND":
                continue
            item = self.table.item(r, i)
            row[ut.KEY_BY_FIELD[field]] = item.text().strip() if item else ""
        return ut.from_row(row)

    def _collect(self):
        """Все строки таблицы. Отдаёт: (список типов, список жалоб)."""
        types = [self._row_type(r) for r in range(self.table.rowCount())]
        bad = []                                     # жалобы, по строке на замечание
        for n, uav in enumerate(types, 1):
            bad += ["строка %d, %s" % (n, s) for s in uav.validate()]
        names = [u.name for u in types]
        for name in sorted(set(names)):
            if names.count(name) > 1:                # тип выбирают по названию —
                bad.append("название «%s» повторяется %d раза" % (name, names.count(name)))
        if not types:                                # пустой справочник нечего выбирать
            bad.append("список пуст — добавьте хотя бы один тип")
        return types, bad

    def _add(self):
        """Кнопка «Добавить»: строка с умолчаниями, название свободное."""
        names = {self._row_type(r).name for r in range(self.table.rowCount())}
        name = "Новый тип"
        while name in names:                         # такое название уже есть
            name += " 2"                             # — не плодим дубль
        self._add_row(ut.FixedWing(name))
        self.table.selectRow(self.table.rowCount() - 1)

    def _remove(self):
        """Кнопка «Удалить»: убрать выделенную строку."""
        r = self.table.currentRow()                  # выделенная строка, -1 — нет такой
        if r < 0:                                    # ничего не выделено
            self.lbl_state.setText("Выделите строку, которую убрать.")
            return
        self.table.removeRow(r)

    def _reset(self):
        """Кнопка «Вернуть исходные»: встроенный список вместо текущего."""
        self._fill(ut.defaults())
        self.lbl_state.setText("Показан встроенный список. Нажмите «Сохранить», чтобы записать.")

    def _save(self):
        """Кнопка «Сохранить»: проверить числа и записать. Жалобы — не записывать."""
        types, bad = self._collect()
        if bad:                                      # числа не проходят проверку
            QtWidgets.QMessageBox.warning(
                self, "Справочник не записан", "Поправьте:\n\n• " + "\n• ".join(bad))
            return
        if not ut.save(types):                       # нет прав, диск занят
            QtWidgets.QMessageBox.warning(
                self, "Справочник не записан",
                "Не удалось записать %s. Проверьте права на папку." % ut.path())
            return
        self.lbl_state.setText("Записано типов: %d." % len(types))
        if self._on_saved:                           # кому-то нужен новый список
            self._on_saved(types)                    # — например, выпадающему списку условий
        self.accept()
