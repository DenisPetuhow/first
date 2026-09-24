# -*- coding: utf-8 -*-
"""
VIEW (Qt) · ОКНО «ДАТЧИКИ» (план 13, задача 13.2) — всё про датчики в одном окне.

Раньше датчики жили в окне «Исходные данные» вместе с маршрутом; по поручению заказчика
21.09.2026 они переехали сюда. Перенесены, а не переписаны: режим «Рассчитать / Задать
позиции», кратность «одна на всех / своя», распределение между типами, таблица датчиков с
файлом. Новое — МИНИ-ТАБЛИЦА ТИПОВ «тип · цвет · N · R · k» (уточнение заказчика 23.09.2026,
§0.4а плана 13): строка на тип, в ячейках те же поля, что были группами; типы справочника с
номером ≥ 5 (`model/sensor_types.py`) — отдельными строками, их R и k правятся в окне типа.

⚠️ ПЕРЕХОДНЫЙ МОСТ (план 13 §13.1.4): у номеров 1–4 N, R, k — поля `Params`, у номеров ≥ 5 число —
`Params.threat_N_types`, R и k — паспорт. Файл датчиков и диалог строки — в `input_window.py`.
"""
import dataclasses
import os

from PyQt5 import QtCore, QtGui, QtWidgets

from config import THEME
from model import sensor_types as catalog
from model.sensors import BIG_TYPE, TYPE_IDS, sensor_types, type_ids
from . import ui_state
from .input_window import (DIR_LOAD, DIR_SAVE, STATE_FILE, SensorRowDialog, default_file_name,
                           mode_word, read_sensors_file, sensors_dir, write_sensors_file)

STATE_KEY = "sensors_window"       # раздел памяти окна: режимы
GREY = "color:#6b7785; background:#f0f0f0;"   # поле только для чтения: светлый диалог, не тёмная карта
ORDER_NAMES = (("gain", "по наибольшему приросту"), ("tight", "стеснённый первым"), ("round", "по кругу 1-2-3"), ("types", "по очереди номеров"))   # правила общего прохода (13.6)


def swatch_style(color):
    """Стиль кнопки-образца цвета. Вход: цвет строкой. Отдаёт: стиль Qt."""
    return "background:%s; border:1px solid #888; min-width:34px;" % color


class SensorsWindow(QtWidgets.QDialog):
    """Окно «Датчики»: режим, кратность, распределение, мини-таблица типов и таблица датчиков.
    НЕ блокирует основное окно — карту можно двигать, не закрывая."""

    INT_FIELDS = {"threat_N", "threat_N2", "threat_N3", "threat_N_big", "threat_k", "threat_k2", "threat_k3"}   # целые поля, шт.
    TYPE_FIELDS = {1: ("threat_N", "threat_R", "threat_k"),   # поля номера: число, радиус, кратность
                   2: ("threat_N2", "threat_R2", "threat_k2"),
                   3: ("threat_N3", "threat_R3", "threat_k3"),
                   4: ("threat_N_big", "threat_R_big", "threat_k")}
    TYPE_COLUMNS = ("№", "тип", "цвет", "N, шт.", "R, км", "k")   # мини-таблица типов
    COLUMNS = ("№", "тип", "режим", "долгота", "широта")          # таблица датчиков

    def __init__(self, parent, params, on_apply, color_of):
        super().__init__(parent)
        self.setWindowTitle("Датчики")
        self.setModal(False)                         # НЕ блокирует основное окно
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Tool)
        self._params = params                        # параметры модели (общие с картой)
        self._on_apply = on_apply                    # отдать поля контроллеру
        self._color_of = color_of                    # цвет типа по номеру — его знает карта
        self._edits = {}                             # поля номеров 1–4: имя Params -> поле
        self._edits_more = {}                        # поля N номеров ≥ 5: номер -> поле
        self._k_big = None                           # поле кратности у больших (только чтение)
        self._rows = []                            # что показано в таблице датчиков
        # колбэки — их ставит threat_view (представление ничего не знает о модели)
        self.on_manual_add = lambda tid, static, lon, lat: None
        self.on_manual_remove = lambda index: None
        self.on_manual_edit = lambda index, tid, static, lon, lat: None
        self.on_manual_clear = lambda: None
        self.on_clear_sensors = lambda: None         # ВСЯ расстановка (режим расчёта)
        self.on_mode_changed = lambda manual: None
        self.on_pick_mode = lambda: None             # «ставить датчики кликом по карте»
        self.on_edit_type = lambda tid: None         # окно «Тип датчика» на этот номер (13.3)
        self.on_types_changed = lambda: None         # справочник правили — перерисовать карту
        self.on_type_deleted = lambda tid: None      # тип удалён — убрать его заданные датчики
        self._build_ui()
        self.refresh(params)

    # ---------- построение окна ----------
    def _build_ui(self):
        """Окно в прокрутке: режимы сверху, мини-таблица типов, таблица датчиков, кнопки."""
        outer = QtWidgets.QVBoxLayout(self)          # окно: только прокрутка
        outer.setContentsMargins(0, 0, 0, 0)
        # ⚠️ ВСЁ ОКНО В ПРОКРУТКЕ: на ноутбуке 1366×768 окно, вылезшее за край, не нажать
        scroll = QtWidgets.QScrollArea()             # прокрутка всего окна
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        inner = QtWidgets.QWidget()                  # содержимое прокрутки
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        lay = QtWidgets.QVBoxLayout(inner)           # содержимое сверху вниз
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        lay.addWidget(self._build_top())
        lay.addWidget(self._build_types())
        lay.addWidget(self._build_table(), 1)
        row = QtWidgets.QHBoxLayout()                # подпись режима и кнопки
        self.lbl_note = QtWidgets.QLabel("")
        self.lbl_note.setStyleSheet("color:%s;" % THEME["muted"])
        self.lbl_note.setWordWrap(True)
        row.addWidget(self.lbl_note, 1)
        btn_apply = QtWidgets.QPushButton("Применить")   # принять поля
        btn_apply.setDefault(True)
        btn_apply.clicked.connect(self._apply)
        btn_close = QtWidgets.QPushButton("Закрыть")     # спрятать окно
        btn_close.clicked.connect(self.hide)
        row.addWidget(btn_apply); row.addWidget(btn_close)
        lay.addLayout(row)
        scr = QtWidgets.QApplication.primaryScreen()     # экран — под его размер окно
        avail = scr.availableGeometry() if scr is not None else QtCore.QRect(0, 0, 1200, 800)   # доступно
        self.resize(min(760, avail.width() - 60), min(760, avail.height() - 60))

    def _build_top(self):
        """Режим позиций, кратность, распределение и очерёдность типов — они меняют смысл полей ниже."""
        box = QtWidgets.QGroupBox("Режим работы")    # рамка блока
        grid = QtWidgets.QGridLayout(box)            # подписи слева, переключатели справа
        # ⚠️ ДВЕ ОТДЕЛЬНЫЕ ГРУППЫ ПЕРЕКЛЮЧАТЕЛЕЙ: Qt объединяет радиокнопки по общему родителю, и без
        # явных групп выбор режима снимал бы выбор кратности (поймано `gui_check.py`)
        self._grp_mode = QtWidgets.QButtonGroup(self)
        self._grp_k = QtWidgets.QButtonGroup(self)
        self.rb_calc = QtWidgets.QRadioButton("Рассчитать позиции")
        self.rb_calc.setToolTip("Датчики расставляет алгоритм по выборке возможных пролётов. "
                                "Таблица внизу покажет РЕЗУЛЬТАТ расстановки с координатами.")
        self.rb_manual = QtWidgets.QRadioButton("Задать позиции")
        self.rb_manual.setToolTip("Датчики стоят ровно там, где их задали: мышью, из файла или "
                                  "строкой в таблице. Таблица внизу — ИСТОЧНИК: что в ней, то и на карте.")
        self.rb_calc.setChecked(True)
        for rb in (self.rb_calc, self.rb_manual):
            self._grp_mode.addButton(rb)
            rb.toggled.connect(self._mode_changed)
        grid.addWidget(QtWidgets.QLabel("Позиции датчиков:"), 0, 0)
        grid.addWidget(self.rb_calc, 0, 1)
        grid.addWidget(self.rb_manual, 0, 2)
        self.rb_k_same = QtWidgets.QRadioButton("общая кратность")
        self.rb_k_same.setToolTip("Одна k на всех: засечки считают все типы вместе, число из строки "
                                  "первого типа подставляется во все.")
        self.rb_k_own = QtWidgets.QRadioButton("у каждого своя")
        self.rb_k_own.setToolTip("У типа своя k, и набирает он её только СВОИМИ датчиками (В13.13).")
        self.rb_k_same.setChecked(True)
        for rb in (self.rb_k_same, self.rb_k_own):
            self._grp_k.addButton(rb)
            rb.toggled.connect(self._k_mode_changed)
        grid.addWidget(QtWidgets.QLabel("Кратность засечки:"), 1, 0)
        grid.addWidget(self.rb_k_same, 1, 1)
        grid.addWidget(self.rb_k_own, 1, 2)
        self.chk_spread = QtWidgets.QCheckBox("распределённое размещение")
        self.chk_spread.setToolTip(
            "ВКЛ: датчики РАЗНЫХ типов не ставятся ближе 0.8·(R₁ + R₂) — то же правило, что внутри "
            "типа. ВЫКЛ: разнос только внутри своего типа.\nБольших датчиков не касается: у них кольцо.")
        self.chk_spread.setChecked(True)
        grid.addWidget(QtWidgets.QLabel("Разнос датчиков:"), 2, 0)
        grid.addWidget(self.chk_spread, 2, 1, 1, 2)
        self.cb_order = QtWidgets.QComboBox()        # правило общего прохода по типам (13.6)
        for key, title in ORDER_NAMES:
            self.cb_order.addItem(title, key)
        self.cb_order.setToolTip(
            "Кто ставит следующий датчик в общем проходе по типам (план 13, 13.6):\n"
            "по приросту — где польза больше; стеснённый первым — типы ровнее, общая засечка ниже;\n"
            "по кругу — 1-2-3-1-2-3; по очереди — как раньше: тип 1 целиком, затем тип 2.")
        grid.addWidget(QtWidgets.QLabel("Очерёдность типов:"), 3, 0)
        grid.addWidget(self.cb_order, 3, 1, 1, 2)
        grid.setColumnStretch(3, 1)
        return box

    def _build_types(self):
        """Мини-таблица типов «тип · цвет · N · R · k» и кнопки справочника; строки — `_fill_types`."""
        box = QtWidgets.QGroupBox("ТИПЫ ДАТЧИКОВ")   # рамка блока
        lay = QtWidgets.QVBoxLayout(box)             # таблица, под ней кнопки
        self.types = QtWidgets.QTableWidget(0, len(self.TYPE_COLUMNS))
        self.types.setHorizontalHeaderLabels(self.TYPE_COLUMNS)
        self.types.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        self.types.setSelectionMode(QtWidgets.QTableWidget.SingleSelection)
        self.types.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self.types.verticalHeader().setVisible(False)
        hh = self.types.horizontalHeader()           # ширина колонок
        for col in range(len(self.TYPE_COLUMNS)):
            hh.setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeToContents if col < 3
                                    else QtWidgets.QHeaderView.Stretch)
        self.types.setMinimumHeight(150)
        lay.addWidget(self.types)
        row = QtWidgets.QHBoxLayout()                # кнопки справочника
        for text, tip, slot in (
                ("Настроить тип…", "Паспорт выбранного типа: название, радиус, кратность, место "
                                   "установки, режим по умолчанию, разрешённая карта.", self._edit_type),
                ("Добавить тип", "Новый тип в справочник участка; его число — в таблице выше.",
                 self._add_type),
                ("Удалить тип", "Убрать выбранный тип из справочника (номера 1–4 держат поля "
                                "задания — выключите их числом N = 0).", self._del_type)):
            b = QtWidgets.QPushButton(text)          # кнопка справочника
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        return box

    def _type_field(self, text, name=None, read_only=False, tip=""):
        """Поле мини-таблицы. Вход: текст, имя поля Params (None — не поле), только чтение, подсказка.
        Отдаёт: QLineEdit; поле номера 1–4 — ещё и в `_edits`."""
        e = QtWidgets.QLineEdit(text)                # поле ячейки
        e.setToolTip(tip)
        e.returnPressed.connect(self._apply)
        if read_only:                                # правится в окне типа
            e.setReadOnly(True)
            e.setStyleSheet(GREY)
        if name is not None:                         # поле задания номера 1–4
            self._edits[name] = e
            if name in ("threat_k", "threat_k2", "threat_k3"):
                e.textEdited.connect(self._k_edited)
        return e

    def _fill_types(self):
        """Строки мини-таблицы по типам задания (номера 1–4 и справочник ≥ 5). Отдаёт: ничего."""
        p = self._params
        specs = sensor_types(p)                      # типы задания с паспортами
        self._edits, self._edits_more, self._k_big = {}, {}, None
        self.types.setRowCount(len(specs))
        for i, s in enumerate(specs):
            it = QtWidgets.QTableWidgetItem("%d" % s.type_id)   # номер типа
            it.setTextAlignment(QtCore.Qt.AlignCenter)
            it.setData(QtCore.Qt.UserRole, int(s.type_id))
            self.types.setItem(i, 0, it)
            self.types.setItem(i, 1, QtWidgets.QTableWidgetItem(
                s.name + (" (кольцо у цели)" if s.kind == "big" else "")))
            sw = QtWidgets.QPushButton("")           # образец цвета: щелчок — выбрать другой
            sw.setStyleSheet(swatch_style(self._color_of(s.type_id)))
            sw.setToolTip("Цвет типа на карте. Щёлкните, чтобы выбрать другой.")
            sw.clicked.connect(lambda _c=False, tid=s.type_id: self._pick_color(tid))
            self.types.setCellWidget(i, 2, sw)
            if s.type_id in TYPE_IDS:                # номер моста — поля Params
                f_n, f_r, f_k = self.TYPE_FIELDS[s.type_id]
                self.types.setCellWidget(i, 3, self._type_field(
                    "%d" % int(getattr(p, f_n, 0)), f_n,
                    tip="Сколько датчиков этого типа ставить. 0 — тип выключен."))
                self.types.setCellWidget(i, 4, self._type_field(
                    "%g" % float(getattr(p, f_r, 0.0)), f_r, tip="Радиус зоны обнаружения, км."))
                if s.type_id == BIG_TYPE:            # у больших кратность не участвует — поле общее
                    self._k_big = self._type_field("%d" % int(getattr(p, "threat_k", 3)), read_only=True,
                                                   tip="Большие ставятся кольцом по углу вокруг цели — "
                                                       "кратность в их расстановке не участвует.")
                    self.types.setCellWidget(i, 5, self._k_big)
                else:
                    self.types.setCellWidget(i, 5, self._type_field(
                        "%d" % int(getattr(p, f_k, 3)), f_k,
                        tip="Сколько РАЗНЫХ датчиков должны увидеть маршрут, чтобы он считался засечённым."))
            else:                                    # тип справочника: N — задание, R и k — паспорт
                e_n = self._type_field("%d" % int(s.n), tip="Сколько датчиков этого типа ставить.")   # поле N
                self._edits_more[int(s.type_id)] = e_n
                self.types.setCellWidget(i, 3, e_n)
                self.types.setCellWidget(i, 4, self._type_field(
                    "%g" % s.r_km, read_only=True, tip="Радиус — в паспорте типа («Настроить тип…»)."))
                self.types.setCellWidget(i, 5, self._type_field(
                    "%d" % s.k, read_only=True, tip="Кратность — в паспорте типа («Настроить тип…»)."))

    def _build_table(self):
        """Таблица датчиков во всю ширину — с прокруткой и кнопками справа (перенесена из «Исходных данных»)."""
        box = QtWidgets.QGroupBox("ДАТЧИКИ НА КАРТЕ")   # рамка блока
        lay = QtWidgets.QVBoxLayout(box)                # кнопки сверху, таблица ниже
        top = QtWidgets.QHBoxLayout()                   # счётчик и кнопки
        self.lbl_count = QtWidgets.QLabel("датчиков: 0")
        self.lbl_count.setStyleSheet("color:%s;" % THEME["muted"])
        top.addWidget(self.lbl_count, 1)
        for text, tip, slot in (
                ("Из файла…", "Прочитать датчики из текстового файла: номер, тип, режим, долгота, широта.",
                 self._load_file),
                ("В файл…", "Записать таблицу в текстовый файл — его же программа читает обратно.",
                 self._save_file),
                ("Добавить", "Добавить датчик строкой: тип, режим и координаты.", self._add_row),
                ("По клику на карте", "Ставить датчики двойным кликом по карте. Повторный двойной "
                                      "клик — выход из режима.", self._pick_mode)):
            b = QtWidgets.QPushButton(text)             # кнопка над таблицей
            b.setToolTip(tip)
            b.clicked.connect(slot)
            top.addWidget(b)
        lay.addLayout(top)
        mid = QtWidgets.QHBoxLayout()                   # таблица и кнопки сбоку
        self.table = QtWidgets.QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        self.table.setSelectionMode(QtWidgets.QTableWidget.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        # ⚠️ ФИКСИРОВАННАЯ ВЫСОТА + СВОЯ ПРОКРУТКА: датчиков бывает за полсотни, и таблица по содержимому
        # вытолкнула бы кнопки за край экрана
        self.table.setMinimumHeight(180)
        self.table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        # ⚠️ ТАБЛИЦА БЕЛАЯ С ЧЁРНЫМ ТЕКСТОМ (заказчик 05.09.2026): её сверяют с распечаткой; цвет задан явно,
        # иначе при тёмной теме Windows текст вышел бы светлым на светлом
        self.table.setStyleSheet(
            "QTableWidget { background:#ffffff; color:#000000; gridline-color:#c8c8c8; "
            "selection-background-color:#cfe3ff; selection-color:#000000; }"
            "QHeaderView::section { background:#eeeeee; color:#000000; padding:3px; border:0px; "
            "border-right:1px solid #c8c8c8; border-bottom:1px solid #c8c8c8; }")
        self.table.setAlternatingRowColors(False)       # чередование мешает читать координаты
        hh = self.table.horizontalHeader()              # ширина колонок
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        for col in range(2, len(self.COLUMNS)):
            hh.setSectionResizeMode(col, QtWidgets.QHeaderView.Stretch)
        mid.addWidget(self.table, 1)
        side = QtWidgets.QVBoxLayout()                  # правка строки
        self.btn_edit = QtWidgets.QPushButton("Изменить")
        self.btn_edit.setToolTip("Изменить тип, режим или координаты выбранной строки.")
        self.btn_edit.clicked.connect(self._edit_row)
        self.btn_del = QtWidgets.QPushButton("Удалить")
        self.btn_del.setToolTip("Убрать выбранный датчик из таблицы и с карты.")
        self.btn_del.clicked.connect(self._del_row)
        self.btn_clear = QtWidgets.QPushButton("Очистить")
        self.btn_clear.setToolTip("«Задать позиции» — убрать ВСЕ заданные вручную датчики.\n"
                                  "«Рассчитать позиции» — убрать ВСЮ расстановку (как «Очистить датчики»).")
        self.btn_clear.clicked.connect(self._clear_rows)
        side.addWidget(self.btn_edit); side.addWidget(self.btn_del)
        side.addWidget(self.btn_clear); side.addStretch(1)
        mid.addLayout(side)
        lay.addLayout(mid)
        return box

    # ---------- заполнение и чтение полей ----------
    def refresh(self, params=None):
        """Подставить в поля текущие параметры модели и справочник типов. Вход: Params или None."""
        if params is not None:                       # пришли свежие параметры
            self._params = params
        p = self._params
        self._fill_types()
        same = bool(getattr(p, "threat_k_same", True))   # общая кратность
        (self.rb_k_same if same else self.rb_k_own).setChecked(True)
        self.chk_spread.setChecked(bool(getattr(p, "threat_spread_types", True)))
        order = str(getattr(p, "threat_type_order", "gain"))   # правило общего прохода
        self.cb_order.setCurrentIndex(max(0, self.cb_order.findData(order)))
        manual = bool(getattr(p, "threat_manual_mode", False))   # режим «Задать позиции»
        (self.rb_manual if manual else self.rb_calc).setChecked(True)
        self._k_mode_changed()
        self._sync_counts()

    def is_manual_mode(self):
        """Окно в режиме «Задать позиции»?"""
        return self.rb_manual.isChecked()

    def _k_mode_changed(self, *_):
        """«Общая» — поля кратности типов 2–3 показывают общее число и закрыты; «своя» — открываются."""
        same = self.rb_k_same.isChecked()            # одна кратность на всех
        for name in ("threat_k2", "threat_k3"):
            e = self._edits.get(name)                # поле кратности типа
            if e is None:                            # строки такого типа нет
                continue
            e.setReadOnly(same)
            e.setStyleSheet(GREY if same else "")
        if same and "threat_k" in self._edits:       # общая — подставить её во все
            self._k_edited(self._edits["threat_k"].text())

    def _k_edited(self, text):
        """Кратность вводится в строке типа 1 и СРАЗУ дублируется в остальные: в полях видно то число,
        с которым программа будет считать (требование заказчика). Вход: текст поля."""
        if not self.rb_k_same.isChecked():           # у каждого своя — не дублировать
            return
        for name in ("threat_k2", "threat_k3"):
            e = self._edits.get(name)                # поле кратности типа
            if e is not None and e.text() != text:   # отличается — подставить
                e.setText(text)
        if self._k_big is not None:                  # у больших поле общее
            self._k_big.setText(text)

    def _mode_changed(self, *_):
        """Режим позиций сменился: подпись, счётчики типов, сообщить карте."""
        manual = self.rb_manual.isChecked()          # «Задать позиции»
        self.lbl_note.setText(
            "Позиции берутся из таблицы: что в ней, то и на карте. Количество считается по "
            "таблице — задать можно только радиус и кратность." if manual else
            "Позиции подбирает алгоритм. Датчики, поставленные вручную, он берёт как есть и "
            "ДОБИРАЕТ остальные до заказанного количества.")
        self._sync_counts()
        self.on_mode_changed(manual)

    def _sync_counts(self):
        """В «Задать позиции» КОЛИЧЕСТВО задаёт таблица: поля N только для чтения и показывают число
        строк каждого типа (заказчик 05.09.2026: «мы менять не можем, только радиус и k»)."""
        manual = self.rb_manual.isChecked()          # «Задать позиции»
        counts = {}                                  # строк таблицы по номеру типа
        for r in self._rows:
            counts[int(r["type_id"])] = counts.get(int(r["type_id"]), 0) + 1
        fields = [(tid, self._edits.get(self.TYPE_FIELDS[tid][0])) for tid in TYPE_IDS]   # поля N
        fields += list(self._edits_more.items())
        for tid, e in fields:
            if e is None:                            # строки такого типа нет
                continue
            e.setReadOnly(manual)
            e.setStyleSheet(GREY if manual else "")
            if manual:                               # число — по таблице
                e.setText("%d" % counts.get(tid, 0))

    def _apply(self):
        """Собрать поля и отдать контроллеру. Нечисловое значение — из текущих параметров: опечатка в
        одном поле не роняет остальные."""
        vals = {}                                    # имя поля Params -> значение
        for name, e in self._edits.items():
            txt = e.text().strip().replace(",", ".")   # текст поля
            try:
                vals[name] = int(float(txt)) if name in self.INT_FIELDS else float(txt)
            except ValueError:                       # не число — прежнее значение
                vals[name] = getattr(self._params, name, 0)
        more = dict(getattr(self._params, "threat_N_types", {}) or {})   # число типов ≥ 5
        for tid, e in self._edits_more.items():
            try:
                more[tid] = max(0, int(float(e.text().strip().replace(",", "."))))
            except ValueError:                       # не число — прежнее
                pass
        vals["threat_N_types"] = more
        vals["threat_k_same"] = self.rb_k_same.isChecked()
        vals["threat_spread_types"] = self.chk_spread.isChecked()
        vals["threat_type_order"] = str(self.cb_order.currentData())
        if vals["threat_k_same"] and "threat_k" in vals:   # общая кратность — одно число на всех
            vals["threat_k2"] = vals["threat_k3"] = vals["threat_k"]
        ui_state.save(STATE_KEY, dict(k_same=vals["threat_k_same"], manual_mode=self.is_manual_mode()))
        self._on_apply(vals)

    # ---------- справочник типов ----------
    def _selected_type(self):
        """Номер типа выбранной строки мини-таблицы. Отдаёт: int или None."""
        rows = self.types.selectionModel().selectedRows() if self.types.selectionModel() else []
        if not rows:                                 # ничего не выбрано
            return None
        return int(self.types.item(rows[0].row(), 0).data(QtCore.Qt.UserRole))

    def _edit_type(self):
        """«Настроить тип…» — окно паспорта выбранного типа (без выбора — первого)."""
        tid = self._selected_type()                  # выбранный номер
        self.on_edit_type(tid if tid is not None else 1)

    def _add_type(self):
        """Новый малый тип в справочник участка: свободный номер ≥ 5, число 0 — включается в таблице."""
        types = catalog.load()                       # справочник участка
        tid = catalog.next_id(types)                 # номер нового типа
        new = catalog.Small(type_id=tid, name="Тип %d" % tid, color="sensor_t3")   # паспорт по умолчанию
        if not catalog.save(types + [new]):          # файл не записался
            self._warn("Справочник не записан — нет прав на папку участка?")
            return
        self.refresh()
        self.on_types_changed()
        self.on_edit_type(tid)

    def _del_type(self):
        """«Удалить тип»: номера 1–4 держат поля задания — их не удаляют; у типа с заданными датчиками —
        спросить и убрать датчики вместе с ним; последний тип удалять нельзя."""
        tid = self._selected_type()                  # выбранный номер
        if tid is None:                              # ничего не выбрано
            self._warn("Выберите тип в таблице.")
            return
        if tid in TYPE_IDS:                          # номер моста
            self._warn("Номера 1–4 держат поля задания (переходный мост, план 13 §13.1.4). "
                       "Выключите тип числом N = 0.")
            return
        types = catalog.load()                       # справочник участка
        left = [t for t in types if t.type_id != tid]   # без удаляемого
        if not left:                                 # последний тип
            self._warn("Последний тип удалять нельзя.")
            return
        n_rows = sum(1 for r in self._rows if int(r["type_id"]) == tid)   # его строк в таблице
        ask = "Удалить тип №%d%s?" % (tid, (" и %d его датчиков" % n_rows) if n_rows else "")   # вопрос
        if QtWidgets.QMessageBox.question(self, "Удалить тип", ask,
                                          QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                                          QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        self.on_type_deleted(tid)
        catalog.save(left)
        self.refresh()
        self.on_types_changed()

    def _pick_color(self, tid):
        """Выбрать цвет типа: пишется в паспорт «#rrggbb». Вход: номер типа."""
        col = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._color_of(tid)), self, "Цвет типа №%d" % tid)
        if not col.isValid():                        # передумали
            return
        types = catalog.load()                       # справочник участка
        st = catalog.by_id(tid, types)               # паспорт типа
        if st is None:                               # номера нет в справочнике — завести паспорт
            st = (catalog.Big if tid == BIG_TYPE else catalog.Small)(type_id=tid, name="Тип %d" % tid)
            types = types + [st]
        types = [dataclasses.replace(t, color=col.name()) if t.type_id == tid else t for t in types]
        catalog.save(types)
        self.refresh()
        self.on_types_changed()

    # ---------- таблица датчиков ----------
    def set_rows(self, rows):
        """Показать датчики в таблице. Вход: строки, как отдаёт `ThreatModel.sensors_table()`."""
        self._rows = list(rows or [])
        self.table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            cells = ("%d" % (i + 1), "%d" % int(r["type_id"]), mode_word(bool(r.get("static"))), "%.6f" % float(r["lon"]), "%.6f" % float(r["lat"]))   # тексты ячеек строки
            for c, text in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(text)   # ячейка
                if c in (0, 1):                      # номер и тип — по центру
                    it.setTextAlignment(QtCore.Qt.AlignCenter)
                # номер жирным всегда (по нему сверяют с подписью на карте); закреплённый — целиком
                if c == 0 or r.get("static"):
                    f = it.font()                    # шрифт ячейки — жирным
                    f.setBold(True)
                    it.setFont(f)
                self.table.setItem(i, c, it)
        n_static = sum(1 for r in self._rows if r.get("static"))   # закреплённых
        self.lbl_count.setText("датчиков: %d%s" % (len(self._rows),
                                                   ("  ·  закреплённых: %d" % n_static) if n_static else ""))
        self._sync_counts()                          # в ручном режиме таблица задаёт количество

    def _selected(self):
        """Номер выбранной строки таблицы датчиков. Отдаёт: int, −1 — не выбрана."""
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return rows[0].row() if rows else -1

    def _add_row(self):
        """«Добавить» — датчик строкой: тип, режим, координаты."""
        dlg = SensorRowDialog(self)                  # диалог строки
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            tid, static, lon, lat = dlg.values()
            self.on_manual_add(tid, static, lon, lat)

    def _edit_row(self):
        """«Изменить» — правка строки ОДНИМ действием (не «удалить и добавить»: в режиме расчёта удаление
        не находило ручной записи, и правка пропадала, а датчик добавлялся лишний — заказчик 05.09.2026)."""
        i = self._selected()                         # выбранная строка
        if i < 0:                                    # не выбрана
            self._warn("Выберите строку в таблице.")
            return
        r = self._rows[i]                            # её данные
        dlg = SensorRowDialog(self, lon=r["lon"], lat=r["lat"], title="Изменить датчик")   # диалог строки
        dlg._select(dlg.cb_type, int(r["type_id"]))
        dlg._select(dlg.cb_mode, bool(r.get("static")))
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            tid, static, lon, lat = dlg.values()
            self.on_manual_edit(i, tid, static, lon, lat)

    def _del_row(self):
        """«Удалить» — выбранная строка."""
        i = self._selected()                         # выбранная строка
        if i < 0:                                    # не выбрана
            self._warn("Выберите строку в таблице.")
            return
        self.on_manual_remove(i)

    def _clear_rows(self):
        """«Очистить»: в «Задать позиции» — все заданные вручную; в «Рассчитать» таблица — РЕЗУЛЬТАТ
        расстановки, и убирается ВСЯ расстановка (заказчик 06.09.2026: «не убирает данные в таблице»)."""
        if not self._rows:                           # чистить нечего
            return
        yes_no = QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No   # кнопки вопроса
        if self.is_manual_mode():
            if QtWidgets.QMessageBox.question(self, "Очистить", "Убрать все заданные вручную датчики?",
                                              yes_no, QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes:
                self.on_manual_clear()
        elif QtWidgets.QMessageBox.question(
                self, "Очистить", "Убрать С КАРТЫ ВСЕ датчики — и подобранные алгоритмом, и заданные вручную?",
                yes_no, QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes:
            self.on_clear_sensors()

    def _pick_mode(self):
        """«По клику на карте» — режим постановки мышью."""
        self.on_pick_mode()

    # ---------- файл ----------
    def _start_dir(self, kind):
        """Откуда начинать выбор файла: СВОЯ папка (`датчики/загрузка`, `датчики/выгрузка`); память
        последней — только если своей нет (заказчик 05.09.2026). Вход: вид папки. Отдаёт: путь."""
        own = sensors_dir(kind)                      # своя папка
        if os.path.isdir(own):                       # есть — с неё
            return own
        last = ui_state.get(STATE_FILE).get(kind, "")   # куда уходил человек
        return last if last and os.path.isdir(last) else os.getcwd()

    @staticmethod
    def _remember_dir(kind, path):
        """Запомнить, куда человек ушёл сам. ⚠️ Раздел памяти пишется ЦЕЛИКОМ — старые ключи переносим."""
        d = dict(ui_state.get(STATE_FILE))           # раздел памяти папок
        d[kind] = os.path.dirname(path)
        ui_state.save(STATE_FILE, d)

    def _load_file(self):
        """«Из файла…» — датчики из текстового файла (формат 8.7.7)."""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Датчики из файла", self._start_dir(DIR_LOAD), "Текстовые файлы (*.txt);;Все файлы (*)")
        if not path:                                 # передумали
            return
        self._remember_dir(DIR_LOAD, path)
        try:
            records, rep = read_sensors_file(path, known=type_ids(self._params))   # с типами справочника
        except Exception as ex:                      # нет прав, битая кодировка, каталог
            self._warn("Файл не прочитан:\n%s" % ex)
            return
        for tid, static, lon, lat in records:
            self.on_manual_add(tid, static, lon, lat)
        msg = "Принято датчиков: %d." % rep["accepted"]   # отчёт о чтении
        if rep["rejected"]:                          # были отброшенные строки
            lines = ", ".join(str(no) for no, _ in rep["rejected"][:10])   # их номера
            msg += ("\nОтброшено строк: %d (%s%s).\nПричина первой: %s"
                    % (len(rep["rejected"]), lines, "…" if len(rep["rejected"]) > 10 else "",
                       rep["rejected"][0][1]))
        if rep["types"]:                             # в шапке описаны типы
            msg += ("\n\nВ шапке файла описаны типы: %s. Параметры типов из файла НЕ применяются — "
                    "они берутся из полей окна." % ", ".join(str(t) for t in sorted(rep["types"])))
        QtWidgets.QMessageBox.information(self, "Датчики из файла", msg)

    def _save_file(self):
        """«В файл…» — таблица в текстовый файл, который программа читает обратно без правки."""
        if not self._rows:                           # таблица пуста
            self._warn("Таблица пуста — записывать нечего.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Датчики в файл", os.path.join(self._start_dir(DIR_SAVE), default_file_name()),
            "Текстовые файлы (*.txt);;Все файлы (*)")
        if not path:                                 # передумали
            return
        self._remember_dir(DIR_SAVE, path)
        try:
            n = write_sensors_file(path, self._rows, self._params)
        except Exception as ex:                      # нет прав, диск занят
            self._warn("Файл не записан:\n%s" % ex)
            return
        QtWidgets.QMessageBox.information(
            self, "Датчики в файл", "Записано датчиков: %d.\n\n%s\n\nЭтот же файл читается кнопкой "
                                    "«Из файла…» без правки руками." % (n, path))

    def _warn(self, text):
        """Предупреждение окна. Вход: текст."""
        QtWidgets.QMessageBox.warning(self, "Датчики", text)
