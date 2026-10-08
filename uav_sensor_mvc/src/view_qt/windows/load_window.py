# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Окно «Нагрузка на средства» (план 13.4, заказчик 30.09.2026).

Кнопка «Рассчитать нагрузку» окна «Анализ моделирования» открывает это окно: доля маршрутов выборки, прошедших
через каждое средство. Сверху — цель («Все цели» или одна, если целей несколько), «Все средства» или один тип и порядок
строк; таблица — № по списку (как в списке средств: малые по типам, внутри типа — порядок выбора, затем большие) ·
[тип] · нагрузка, % ·
маршрутов; выбранная строка подсвечивает средство на карте; «Выгрузить в Word» — та же таблица.

⚠️ ОКНО НИЧЕГО НЕ СЧИТАЕТ: снимок отдаёт колбэк `data(цель)` (модель), порядок строк — `sensor_load.sort_rows`, файл
пишет колбэк `on_export`, подсветку рисует карта (`on_pick`). Снимок — на момент нажатия, как у «Анализа»;
«Пересчитать» и смена цели — заново.
"""
import datetime
import os

from PyQt5 import QtCore, QtWidgets

from model.placement.sensor_load import ORDERS, sort_rows

ALL = "Все средства"                                 # первая строка выбора типа
ALL_TARGETS = "Все цели"                             # первая строка выбора цели
SOURCE = {"iter": "итерации моделирования", "loaded": "загруженная выборка",
          "zone": "возможные маршруты (до моделирования)"}   # откуда выборка — подпись


class SensorLoadDialog(QtWidgets.QDialog):
    """Немодальное окно нагрузки на средства. Вход: родитель; колбэки `data(цель)` → dict модели, `on_export(путь,
    заголовок, пояснение, столбцы, ячейки)` → None или ошибка, `choices()` → подписи целей, `on_pick(строка|None)`."""

    def __init__(self, parent, data, on_export, choices=None, on_pick=None):
        super().__init__(parent)
        self.setWindowTitle("Нагрузка на средства")
        self.resize(600, 660)
        self._data, self._on_export = data, on_export   # снимок модели и запись в Word
        self._choices = choices or (lambda: [])      # подписи целей; пусто — цель одна
        self._on_pick = on_pick or (lambda row: None)   # подсветка средства на карте
        self._snap = {}                              # последний снимок: строки, выборка, названия типов, цель
        self._shown = []                             # строки таблицы в показанном порядке — для подсветки
        col = QtWidgets.QVBoxLayout(self)            # окно сверху вниз
        top = QtWidgets.QFormLayout()                # выбор цели, типа и порядка
        self.combo_target = QtWidgets.QComboBox()    # «Все цели» или одна цель
        self.combo_type = QtWidgets.QComboBox()      # «Все средства» или тип
        self.combo_order = QtWidgets.QComboBox()     # порядок строк
        for key, title in ORDERS:                    # порядки из модели — один список на окно и выгрузку
            self.combo_order.addItem(title, key)
        self.combo_target.currentIndexChanged.connect(lambda _i: self.refresh(keep_targets=True))
        self.combo_type.currentIndexChanged.connect(lambda _i: self._show())
        self.combo_order.currentIndexChanged.connect(lambda _i: self._show())
        top.addRow("Цель:", self.combo_target)
        top.addRow("Средства:", self.combo_type)
        top.addRow("Порядок:", self.combo_order)
        col.addLayout(top)
        self.lbl_info = QtWidgets.QLabel("")         # выборка, число средств, без пролёта
        self.lbl_info.setWordWrap(True)
        col.addWidget(self.lbl_info)
        self.table = QtWidgets.QTableWidget()        # таблица нагрузки
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._picked)
        col.addWidget(self.table, 1)
        row = QtWidgets.QHBoxLayout()                # кнопки
        for title, slot in (("Пересчитать", self.refresh), ("Выгрузить в Word", self._export),
                            ("Закрыть", self.close)):   # кнопка: подпись и обработчик
            b = QtWidgets.QPushButton(title)         # кнопка окна
            b.clicked.connect(lambda _c=False, s=slot: s())
            row.addWidget(b)
        col.addLayout(row)

    def refresh(self, keep_targets=False):
        """Новый снимок по выбранной цели; списки целей и типов — заново, выбор сохраняется, если он есть.
        Вход: keep_targets — список целей не трогать (сменили цель). Отдаёт: ничего."""
        if not keep_targets:                         # «Пересчитать» или открыли окно — цели могли смениться
            self._fill_targets()
        self._snap = self._data(self.combo_target.currentData()) or {}
        keep = self.combo_type.currentData()         # номер выбранного типа; None — все
        tids = sorted({r["type_id"] for r in self._snap.get("rows", [])})   # типы на карте
        names = self._snap.get("names", {})          # номер типа -> название
        self.combo_type.blockSignals(True)
        self.combo_type.clear()
        self.combo_type.addItem(ALL, None)
        for t in tids:                               # тип — строка выбора
            self.combo_type.addItem("№%d %s" % (t, names.get(t, "тип %d" % t)), t)
        i = self.combo_type.findData(keep)           # прежний выбор в новом списке
        self.combo_type.setCurrentIndex(i if keep is not None and i >= 0 else 0)
        self.combo_type.blockSignals(False)
        self._show()

    def _fill_targets(self):
        """Список целей: «Все цели» и подписи из `choices()`; цель одна — выбор скрыт. Отдаёт: ничего."""
        keep = self.combo_target.currentData()       # номер выбранной цели; None — все
        labels = self._choices() or []               # «Цель №N — маршрутов M»; пусто — цель одна
        self.combo_target.blockSignals(True)
        self.combo_target.clear()
        self.combo_target.addItem(ALL_TARGETS, None)
        for i, text in enumerate(labels):            # цель — строка выбора, данные — номер с нуля
            self.combo_target.addItem(text, i)
        i = self.combo_target.findData(keep)         # прежний выбор в новом списке
        self.combo_target.setCurrentIndex(i if keep is not None and i >= 0 else 0)
        self.combo_target.blockSignals(False)
        self.combo_target.setEnabled(bool(labels))
        self.combo_target.setToolTip("" if labels else "Цель одна — нагрузка считается по всей выборке.")

    def _rows(self):
        """Строки текущего выбора типа в выбранном порядке. Отдаёт: список строк `sensor_load`."""
        rows = self._snap.get("rows", [])            # строки всех средств
        tid = self.combo_type.currentData()          # выбранный тип; None — все средства
        if tid is not None:                          # один тип — только его средства
            rows = [r for r in rows if r["type_id"] == tid]
        return sort_rows(rows, self.combo_order.currentData())

    def _view(self):
        """Что сейчас на экране. Отдаёт: (заголовок, строки пояснения, подписи столбцов, ячейки [[str]])."""
        snap = self._snap                            # снимок модели
        rows = self._rows()                          # строки в показанном порядке
        names = snap.get("names", {})                # номер типа -> название
        if self.combo_type.currentData() is None:    # общий режим — ещё и тип средства
            headers = ["№ по списку", "Тип средства", "Нагрузка, %", "Маршрутов"]   # подписи столбцов
            cells = [["%d" % r["n"], names.get(r["type_id"], "тип %d" % r["type_id"]), "%.1f" % r["load_pct"],
                      "%d" % r["routes"]] for r in rows]   # ячейки строк, текстом
        else:                                        # по типу — номер, нагрузка, маршрутов
            headers = ["№ по списку", "Нагрузка, %", "Маршрутов"]   # подписи столбцов
            cells = [["%d" % r["n"], "%.1f" % r["load_pct"], "%d" % r["routes"]] for r in rows]   # ячейки строк
        m = int(snap.get("n_routes", 0))             # маршрутов в выборке
        tgt = snap.get("target")                     # номер цели; None — все цели
        whom = "все цели" if tgt is None else "маршруты к цели №%d" % (int(tgt) + 1)   # чья выборка
        idle = sum(1 for r in rows if r["routes"] == 0)   # средств без пролёта
        info = ["Выборка: %s, %d маршрутов (%s)." % (SOURCE.get(snap.get("source"), "—"), m, whom),   # пояснение
                "Средства: %s · %d шт., без пролёта %d. Порядок: %s." % (self.combo_type.currentText(), len(rows), idle,
                                                                        self.combo_order.currentText()),
                "Нагрузка — доля маршрутов, прошедших через зону средства (хоть одна точка не дальше радиуса)."]
        return "Нагрузка на средства", info, headers, cells

    def _show(self):
        """Таблица и пояснение по текущему выбору; подсветка на карте снимается. Отдаёт: ничего."""
        _title, info, headers, cells = self._view()  # то, что показать: пояснение, столбцы, ячейки
        self._shown = self._rows()                   # те же строки, что в ячейках, — строка таблицы -> средство
        if not self._snap.get("rows"):               # считать было нечем
            empty = ("Маршрутов нет: запустите моделирование или «Расставить датчики»."
                     if not self._snap.get("n_routes") else "Средств на карте нет — «Расставить датчики».")   # почему пусто
            info = [empty]
        self.lbl_info.setText("<br>".join(info))
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(cells))
        for i, row in enumerate(cells):              # строка таблицы на средство
            for c, v in enumerate(row):              # ячейки по столбцам
                it = QtWidgets.QTableWidgetItem(v)   # ячейка
                it.setTextAlignment(QtCore.Qt.AlignCenter)
                self.table.setItem(i, c, it)
        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        self._on_pick(None)

    def _picked(self):
        """Выбрали строку — подсветить её средство на карте; выбор сняли — убрать подсветку. Отдаёт: ничего."""
        sel = self.table.selectionModel().selectedRows()   # выбранные строки (одна)
        i = sel[0].row() if sel else -1              # номер строки; -1 — ничего
        self._on_pick(self._shown[i] if 0 <= i < len(self._shown) else None)

    def closeEvent(self, event):
        """Окно закрыли — подсветку с карты убрать. Вход: событие Qt. Отдаёт: ничего."""
        self._on_pick(None)
        super().closeEvent(event)

    def _export(self):
        """«Выгрузить в Word»: выбрать файл и записать таблицу как на экране. Отдаёт: ничего."""
        title, info, headers, cells = self._view()   # таблица ровно как на экране
        if not cells:                                # выгружать нечего
            self.lbl_info.setText("Таблица пуста — выгружать нечего.")
            return
        stamp = datetime.datetime.now()              # когда выгружено
        name = "нагрузка_на_средства_%s.docx" % stamp.strftime("%Y%m%d_%H%M")   # имя по умолчанию
        path, _flt = QtWidgets.QFileDialog.getSaveFileName(self, "Выгрузить в Word", name, "Word (*.docx)")
        if not path:                                 # человек передумал
            return
        if not path.lower().endswith(".docx"):       # расширение не указали
            path += ".docx"
        err = self._on_export(path, title, ["Выгружено: %s." % stamp.strftime("%d.%m.%Y %H:%M")] + info,
                              headers, cells)   # None — записано, иначе текст ошибки
        self.lbl_info.setText("<br>".join(info) + "<br><b>%s</b>" % (
            "Записано: %s" % os.path.basename(path) if err is None else "Ошибка: %s" % err))
