# -*- coding: utf-8 -*-
"""
VIEW (Qt) · ОКНО «ИСХОДНЫЕ ДАННЫЕ» (задача 8.7) — одно окно вместо двух прежних.

Заменяет собой окна «Входные данные маршрута» и «Датчики: параметры…» и блок полей
датчиков в панели: параметры подбирают ВМЕСТЕ, а раньше для этого приходилось держать
открытыми два окна и косить в панель.

⚠️ ПОЧЕМУ ВСЁ В ОДНОМ ФАЙЛЕ (решение заказчика 04.09.2026). Здесь и форма, и таблица
датчиков, и маленький диалог «тип и режим», и чтение-запись файла датчиков. Дробить
это по модулям не нужно: правило проекта — отдельный файл заводится под НОВЫЙ механизм,
а не под каждую часть одного окна, и связанный код должен лежать вместе (CLAUDE.md,
журнал п. 212). Отдельный модуль здесь ровно один — сам `input_window.py`: дописывать
окно в `threat_view.py` (под 2 700 строк) значило бы смешать карту и ввод данных.

ЧТО В ОКНЕ (компоновка утверждена заказчиком, план 8, §8.7.1а):

    режим работы + кратность          — вверху, они меняют смысл полей ниже
    слева параметры ЧЕТЫРЁХ типов     — справа параметры маршрута
    внизу во всю ширину — ТАБЛИЦА датчиков с прокруткой

ДВА РЕЖИМА РАБОТЫ:

* «Рассчитать позиции» — датчики расставляет алгоритм, таблица показывает РЕЗУЛЬТАТ;
* «Задать позиции» — датчики стоят там, где их задали, таблица служит ИСТОЧНИКОМ.

Таблица заполняется в обоих: в первом отвечает на вопрос «куда программа их поставила»,
во втором — задаёт расстановку.
"""
import io
import os
import time

from PyQt5 import QtCore, QtWidgets

from config import THEME
from model.sensors import TYPE_IDS, KIND_LABEL, sensor_types, format_types_header
from . import ui_state

# ── имена разделов памяти полей (`ui_state`): что подставится при следующем открытии ──
STATE_KEY = "input_window"        # параметры окна и режимы
STATE_ROW = "sensor_row"          # последний добавленный датчик: тип и режим
STATE_FILE = "sensor_file"        # последняя папка файла датчиков

FILE_MARK = "UAV-SENSORS"         # опознавательная строка в шапке файла
FILE_VERSION = "v1"

# Слова, которыми в файле и в таблице записывается режим датчика. По-русски — человеку,
# по-английски (s/d) — на случай чужой кодировки: файл правят руками в блокноте.
_STATIC_WORDS = ("статический", "стат", "static", "s", "с")   # ⚠️ последняя — РУССКАЯ «с»
_DYNAMIC_WORDS = ("динамический", "дин", "dynamic", "d", "д")


# ══════════════════════════════════════════════════════════════════════════════
# ФАЙЛ ДАТЧИКОВ (формат — план 8, §8.7.7). Один и тот же на чтение и на запись:
# файл, выгруженный программой, обязан читаться ею же без правки руками.
# ══════════════════════════════════════════════════════════════════════════════
def mode_word(static):
    return "статический" if static else "динамический"


def parse_mode(word):
    """Слово режима → True (статический) / False (динамический) / None (не режим)."""
    w = str(word).strip().lower()
    if w in _STATIC_WORDS:
        return True
    if w in _DYNAMIC_WORDS:
        return False
    return None


def write_sensors_file(path, rows, params=None):
    """Записать датчики в текстовый файл с шапкой.

    `rows` — список словарей `n, type_id, static, lon, lat` (как отдаёт
    `ThreatModel.sensors_table()`). Кодировка UTF-8 БЕЗ BOM: с BOM первая строка
    перестаёт опознаваться при обратном чтении."""
    lines = ["# %s %s" % (FILE_MARK, FILE_VERSION),
             "# дата: %s" % time.strftime("%Y-%m-%d %H:%M"),
             "#"]
    if params is not None:
        lines += ["# " + s for s in format_types_header(params)] + ["#"]
    lines += ["#  N  тип  режим           lon          lat",
              "#--- ---- -------------- ----------- -----------"]
    for i, r in enumerate(rows, 1):
        lines.append("%5d %4d  %-14s %11.6f %11.6f"
                     % (i, int(r["type_id"]), mode_word(bool(r["static"])),
                        float(r["lon"]), float(r["lat"])))
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return len(rows)


def read_sensors_file(path):
    """Прочитать файл датчиков → `(записи, отчёт)`.

    Записи — список `(type_id, static, lon, lat)`. Отчёт — словарь `accepted`,
    `rejected` (список `(номер строки, причина)`) и `types` (таблица типов из шапки,
    если она там была).

    ⚠️ ОДНА ПЛОХАЯ СТРОКА НЕ РОНЯЕТ ЧТЕНИЕ. Отчёт «принято 18, отброшено 2 (строки 7,
    12)» полезнее, чем отказ читать весь файл: файлы правят руками, и опечатка в одной
    строке — обычное дело.

    ⚠️ НОМЕР ДАТЧИКА В НАЧАЛЕ СТРОКИ НЕОБЯЗАТЕЛЕН. Опора разбора — слово режима: тип
    стоит перед ним, координаты — сразу после. Поэтому читается и файл, выгруженный
    программой (с номерами), и строка из трёх полей, набранная руками."""
    records, rejected, types = [], [], {}
    with io.open(path, encoding="utf-8-sig") as f:      # -sig снимает BOM блокнота
        raw = f.read()
    for no, line in enumerate(raw.splitlines(), 1):
        s = line.strip().lstrip("﻿")
        if not s:
            continue
        if s.startswith("#"):
            _parse_header_type(s[1:], types)
            continue
        parts = s.replace(",", ".").split()
        # найти токен режима — он и есть опора строки
        pos = next((i for i, t in enumerate(parts) if parse_mode(t) is not None), None)
        if pos is None or pos < 1 or len(parts) < pos + 3:
            rejected.append((no, "не разобрана: нет режима или не хватает колонок"))
            continue
        try:
            tid = int(float(parts[pos - 1]))
            lon = float(parts[pos + 1])
            lat = float(parts[pos + 2])
        except ValueError:
            rejected.append((no, "тип или координаты — не число"))
            continue
        if tid not in TYPE_IDS:
            rejected.append((no, "тип %d: бывают только %s"
                             % (tid, ", ".join(str(t) for t in TYPE_IDS))))
            continue
        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            rejected.append((no, "координаты вне земного диапазона"))
            continue
        records.append((tid, bool(parse_mode(parts[pos])), lon, lat))
    return records, dict(accepted=len(records), rejected=rejected, types=types)


def _parse_header_type(text, types):
    """Строка таблицы типов из шапки: `1  малый  20  2.0  3` → types[1] = (20, 2.0, 3).

    Типы из шапки НЕ обязательны: нет их — берутся текущие параметры окна."""
    parts = text.replace(",", ".").split()
    if len(parts) < 5:
        return
    kinds = tuple(KIND_LABEL.values())
    if parts[1].lower() not in kinds:
        return
    try:
        tid, n, r, k = int(parts[0]), int(parts[2]), float(parts[3]), int(parts[4])
    except ValueError:
        return
    if tid in TYPE_IDS:
        types[tid] = (n, r, k)


# ══════════════════════════════════════════════════════════════════════════════
# МАЛЕНЬКИЙ ДИАЛОГ «ТИП И РЕЖИМ» — спрашивается при добавлении датчика
# ══════════════════════════════════════════════════════════════════════════════
class SensorRowDialog(QtWidgets.QDialog):
    """Тип (1–4), режим (статический / динамический) и координаты одного датчика.

    Открывается двумя путями: кнопкой «Добавить» в таблице (координаты вводятся) и
    двойным кликом по карте (координаты уже известны и подставлены).

    ⚠️ ПАМЯТЬ ПОЛЕЙ (правило заказчика). Тип и режим запоминаются и подставляются в
    следующий раз: при проверке подряд ставят десяток датчиков одного типа, и выбирать
    его каждый раз заново — потерянное время."""

    def __init__(self, parent, lon=None, lat=None, title="Добавить датчик"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        last = ui_state.get(STATE_ROW)
        lay = QtWidgets.QFormLayout(self)

        self.cb_type = QtWidgets.QComboBox()
        for s in sensor_types(_params_of(parent)):
            self.cb_type.addItem("%d · %s (R = %g км)"
                                 % (s.type_id, KIND_LABEL.get(s.kind, s.kind), s.r_km),
                                 s.type_id)
        self._select(self.cb_type, last.get("type_id", 1))
        lay.addRow("Тип датчика:", self.cb_type)

        self.cb_mode = QtWidgets.QComboBox()
        self.cb_mode.addItem("динамический — позицию выбирает алгоритм", False)
        self.cb_mode.addItem("статический — позиция закреплена", True)
        self._select(self.cb_mode, bool(last.get("static", False)))
        lay.addRow("Режим:", self.cb_mode)

        self.ed_lon = QtWidgets.QLineEdit("" if lon is None else "%.6f" % lon)
        self.ed_lat = QtWidgets.QLineEdit("" if lat is None else "%.6f" % lat)
        lay.addRow("Долгота, °:", self.ed_lon)
        lay.addRow("Широта, °:", self.ed_lat)

        note = QtWidgets.QLabel(
            "Статический датчик остаётся ровно там, куда поставлен: алгоритм принимает "
            "его позицию как данность и достраивает остальные вокруг. Оптимальность "
            "расстановки при этом снижается — это осознанная плата за возможность "
            "поставить датчик там, где он нужен.")
        note.setWordWrap(True)
        note.setStyleSheet("color:%s;" % THEME["muted"])
        note.setMaximumWidth(380)
        lay.addRow(note)

        box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok
                                         | QtWidgets.QDialogButtonBox.Cancel)
        box.button(QtWidgets.QDialogButtonBox.Ok).setText("Добавить")
        box.button(QtWidgets.QDialogButtonBox.Cancel).setText("Отмена")
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        lay.addRow(box)

    @staticmethod
    def _select(combo, value):
        i = combo.findData(value)
        if i >= 0:
            combo.setCurrentIndex(i)

    def values(self):
        """Введённое: `(тип, статический, lon, lat)` — или None, если координаты пусты."""
        try:
            lon = float(self.ed_lon.text().strip().replace(",", "."))
            lat = float(self.ed_lat.text().strip().replace(",", "."))
        except ValueError:
            return None
        return (int(self.cb_type.currentData()), bool(self.cb_mode.currentData()),
                lon, lat)

    def _accept(self):
        v = self.values()
        if v is None:
            QtWidgets.QMessageBox.warning(
                self, "Координаты", "Долгота и широта должны быть числами "
                "в градусах, например 40.881100 и 62.793400.")
            return
        if not (-180.0 <= v[2] <= 180.0 and -90.0 <= v[3] <= 90.0):
            QtWidgets.QMessageBox.warning(
                self, "Координаты", "Долгота бывает от −180° до 180°, "
                "широта — от −90° до 90°.")
            return
        ui_state.save(STATE_ROW, dict(type_id=v[0], static=v[1]))
        self.accept()


def _params_of(widget):
    """Достать Params у окна-владельца (для подписи радиусов в списке типов)."""
    w = widget
    while w is not None:
        p = getattr(w, "_params_ref", None) or getattr(w, "_params", None)
        if p is not None:
            return p
        w = w.parent()
    from config import Params
    return Params()


# ══════════════════════════════════════════════════════════════════════════════
# ГЛАВНОЕ ОКНО
# ══════════════════════════════════════════════════════════════════════════════
class InputDataWindow(QtWidgets.QDialog):
    """Окно «Исходные данные»: параметры четырёх типов датчиков, параметры маршрута и
    таблица датчиков. НЕ блокирует основное окно — карту можно двигать, не закрывая."""

    # Параметры маршрута (правая колонка). Кортеж: поле Params, подпись, подсказка.
    ROUTE_FIELDS = (
        ("threat_L_max", "Запас хода L_max, км",
         "Главное ограничение маршрута: полная длина пути вход→цель не может его "
         "превысить."),
        ("threat_speed_kmh", "Скорость, км/ч",
         "Крейсерская скорость БПЛА. Вместе с креном задаёт радиус разворота R_min."),
        ("threat_bank_deg", "Крен, °",
         "Максимальный угол крена — через него считается радиус разворота R_min."),
        ("threat_turn_interval_km", "Длина прямого участка, км",
         "Целевая длина прямого отрезка: маршрут держит курс примерно столько "
         "километров, потом доворачивает."),
        ("threat_max_gap_km", "Разрыв между секторами, км",
         "Пустое место между весовыми секторами: КОРОЧЕ этого — БПЛА перелетит, и "
         "коридоры сшиваются в один; длиннее — коридор разорван, маршрута нет.\n"
         "Меняет и область залёта, и набор возможных маршрутов ещё до итераций."),
        ("threat_corridor_slack_km", "Уход от ориентира, км",
         "Насколько маршрут отклоняется ВБОК от реки или дороги, вдоль которой идёт.\n"
         "Это не то же, что разрыв: разрыв — про перелёт через пустоту МЕЖДУ "
         "коридорами, а это — про свободу манёвра вдоль коридора."),
    )
    # Параметры расстановки (там же, ниже маршрута).
    PLACE_FIELDS = (
        ("threat_cand_step_km", "Шаг сетки позиций, км",
         "Через сколько километров стоят кандидатные позиции датчика. Мельче шаг — "
         "точнее расстановка, но дольше расчёт. В авто — половина НАИМЕНЬШЕГО радиуса "
         "среди работающих малых типов."),
        ("threat_min_sep_frac", "Разнос датчиков (доля R)",
         "Нижняя граница расстояния между датчиками в долях радиуса — чтобы они не "
         "сбивались в кучу. Желаемый разнос 1.6·R, при нехватке позиций он ослабляется "
         "до этой границы."),
        ("threat_big_ring_lo", "Кольцо у цели, ближняя доля R",
         "Ближняя граница кольца больших датчиков вокруг цели, в долях их радиуса: "
         "не даёт им сбиться в кучу над самой целью."),
        ("threat_big_ring_hi", "Кольцо у цели, дальняя доля R",
         "Дальняя граница кольца. 1.0 — цель ровно на краю зоны обзора; больше 1.0 "
         "цель выйдет из-под наблюдения."),
    )
    INT_FIELDS = {"threat_N", "threat_N2", "threat_N3", "threat_N_big",
                  "threat_k", "threat_k2", "threat_k3"}
    # Поля одного типа датчиков: (число, радиус, кратность) — совпадает с `model.sensors`.
    TYPE_FIELDS = {
        1: ("threat_N", "threat_R", "threat_k"),
        2: ("threat_N2", "threat_R2", "threat_k2"),
        3: ("threat_N3", "threat_R3", "threat_k3"),
        4: ("threat_N_big", "threat_R_big", "threat_k"),
    }
    COLUMNS = ("№", "тип", "режим", "долгота", "широта")

    def __init__(self, parent, params, on_apply):
        super().__init__(parent)
        self.setWindowTitle("Исходные данные")
        self.setModal(False)                       # НЕ блокирует основное окно
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Tool)
        self._params = params
        self._on_apply = on_apply
        self._edits = {}
        self._rows = []                            # что показано в таблице

        # колбэки — их ставит threat_view (представление ничего не знает о модели)
        self.on_manual_add = lambda tid, static, lon, lat: None
        self.on_manual_remove = lambda index: None
        self.on_manual_clear = lambda: None
        self.on_mode_changed = lambda manual: None
        self.on_pick_mode = lambda: None           # «ставить датчики кликом по карте»

        self._build_ui()
        self.refresh(params)

    # ---------- построение окна ----------
    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # ⚠️ ВСЁ ОКНО В ПРОКРУТКЕ. На ноутбуке 1366×768 форма с четырьмя типами, шестью
        # параметрами маршрута и таблицей в высоту не помещается, а окно, вылезшее за
        # край экрана, кнопкой «Применить» не нажать.
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        inner = QtWidgets.QWidget()
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        lay = QtWidgets.QVBoxLayout(inner)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        lay.addWidget(self._build_top())           # режим работы + кратность
        mid = QtWidgets.QHBoxLayout(); mid.setSpacing(10)
        mid.addWidget(self._build_types(), 1)      # слева — датчики
        mid.addWidget(self._build_route(), 1)      # справа — маршрут
        lay.addLayout(mid)
        lay.addWidget(self._build_table(), 1)      # внизу — таблица во всю ширину

        row = QtWidgets.QHBoxLayout()
        self.lbl_note = QtWidgets.QLabel("")
        self.lbl_note.setStyleSheet("color:%s;" % THEME["muted"])
        self.lbl_note.setWordWrap(True)
        row.addWidget(self.lbl_note, 1)
        btn_apply = QtWidgets.QPushButton("Применить")
        btn_apply.setDefault(True)
        btn_apply.clicked.connect(self._apply)
        btn_close = QtWidgets.QPushButton("Закрыть")
        btn_close.clicked.connect(self.hide)
        row.addWidget(btn_apply); row.addWidget(btn_close)
        lay.addLayout(row)

        scr = QtWidgets.QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr is not None else QtCore.QRect(0, 0, 1200, 800)
        self.resize(min(980, avail.width() - 60), min(760, avail.height() - 60))

    def _build_top(self):
        """Режим работы и кратность — вверху: они меняют смысл полей ниже."""
        box = QtWidgets.QGroupBox("Режим работы")
        grid = QtWidgets.QGridLayout(box)
        # ⚠️ ДВЕ ОТДЕЛЬНЫЕ ГРУППЫ ПЕРЕКЛЮЧАТЕЛЕЙ. Радиокнопки Qt объединяет в группу по
        # ОБЩЕМУ РОДИТЕЛЮ, а здесь их четыре в одном блоке: без явных групп выбор режима
        # работы снимал бы выбор кратности и наоборот. Поймано `tools/gui_check.py`:
        # «у каждого своя» не открывала поля, потому что «одна на всех» не снималась.
        self._grp_mode = QtWidgets.QButtonGroup(self)
        self._grp_k = QtWidgets.QButtonGroup(self)

        self.rb_calc = QtWidgets.QRadioButton("Рассчитать позиции")
        self.rb_calc.setToolTip(
            "Датчики расставляет алгоритм по выборке возможных пролётов — как было до "
            "сих пор. Таблица внизу покажет РЕЗУЛЬТАТ расстановки с координатами.")
        self.rb_manual = QtWidgets.QRadioButton("Задать позиции")
        self.rb_manual.setToolTip(
            "Датчики стоят ровно там, где их задали: мышью, из файла или строкой в "
            "таблице. Таблица внизу — ИСТОЧНИК: что в ней, то и на карте.")
        self.rb_calc.setChecked(True)
        for rb in (self.rb_calc, self.rb_manual):
            self._grp_mode.addButton(rb)
            rb.toggled.connect(self._mode_changed)
        grid.addWidget(QtWidgets.QLabel("Позиции датчиков:"), 0, 0)
        grid.addWidget(self.rb_calc, 0, 1)
        grid.addWidget(self.rb_manual, 0, 2)

        self.rb_k_same = QtWidgets.QRadioButton("одна на всех")
        self.rb_k_same.setToolTip(
            "Кратность вводится в одной группе и сразу подставляется во все остальные: "
            "в полях видно то самое число, с которым программа будет считать.")
        self.rb_k_own = QtWidgets.QRadioButton("у каждого своя")
        self.rb_k_own.setToolTip("Кратность задаётся отдельно для каждого типа датчиков.")
        self.rb_k_same.setChecked(True)
        for rb in (self.rb_k_same, self.rb_k_own):
            self._grp_k.addButton(rb)
            rb.toggled.connect(self._k_mode_changed)
        grid.addWidget(QtWidgets.QLabel("Кратность засечки:"), 1, 0)
        grid.addWidget(self.rb_k_same, 1, 1)
        grid.addWidget(self.rb_k_own, 1, 2)
        grid.setColumnStretch(3, 1)
        return box

    def _build_types(self):
        """Левая колонка — четыре типа датчиков, каждый своей группой."""
        box = QtWidgets.QGroupBox("ДАТЧИКИ")
        lay = QtWidgets.QVBoxLayout(box)
        lay.setSpacing(6)
        specs = {s.type_id: s for s in sensor_types(self._params)}
        for tid in TYPE_IDS:
            s = specs[tid]
            grp = QtWidgets.QGroupBox("Тип %d · %s%s"
                                      % (tid, KIND_LABEL.get(s.kind, s.kind),
                                         " (щит у цели)" if s.kind == "big" else ""))
            form = QtWidgets.QGridLayout(grp)
            form.setContentsMargins(8, 4, 8, 4); form.setSpacing(4)
            f_n, f_r, f_k = self.TYPE_FIELDS[tid]
            for col, (name, label, tip) in enumerate((
                    (f_n, "N, шт.", "Сколько датчиков этого типа ставить. "
                                    "0 — тип выключен, его настройки сохраняются."),
                    (f_r, "R, км", "Радиус зоны обнаружения датчика этого типа."),
                    (f_k, "k", "Сколько РАЗНЫХ датчиков должны увидеть маршрут, чтобы "
                               "он считался надёжно засечённым."))):
                lab = QtWidgets.QLabel(label)
                lab.setStyleSheet("color:%s;" % THEME["muted"])
                e = QtWidgets.QLineEdit()
                e.setToolTip(tip)
                e.returnPressed.connect(self._apply)
                if name in ("threat_k", "threat_k2", "threat_k3"):
                    e.textEdited.connect(self._k_edited)
                # ⚠️ поле кратности типа 4 всегда общее: большие ставятся кольцом ПО УГЛУ,
                # и кратность в их расстановке не участвует
                if tid == 4 and label == "k":
                    e.setToolTip("Кратность у больших датчиков общая: они ставятся "
                                 "кольцом по углу вокруг цели, и кратность в их "
                                 "расстановке не участвует.")
                    e.setReadOnly(True)
                self._edits.setdefault(name, e)
                if self._edits[name] is not e:      # тип 4 делит поле k с типом 1
                    self._k_big = e
                form.addWidget(lab, 0, col)
                form.addWidget(e, 1, col)
            lay.addWidget(grp)
        lay.addStretch(1)
        return box

    def _build_route(self):
        """Правая колонка — параметры маршрута и расстановки."""
        box = QtWidgets.QGroupBox("МАРШРУТ БПЛА")
        lay = QtWidgets.QVBoxLayout(box)
        form = QtWidgets.QFormLayout()
        form.setSpacing(4)

        self.lbl_ab = QtWidgets.QLabel("—")
        form.addRow("|AB| (вход→цель), км:", self.lbl_ab)
        self.chk_lmax_auto = QtWidgets.QCheckBox("авто: |AB| + 25 %")
        self.chk_lmax_auto.setToolTip(
            "Запас хода = расстояние вход→цель +25 % (не меньше кратчайшего коридора). "
            "Снимите галочку — задать L_max руками.")
        self.chk_lmax_auto.stateChanged.connect(self._auto_changed)
        form.addRow(self.chk_lmax_auto)
        for name, label, tip in self.ROUTE_FIELDS:
            form.addRow(label + ":", self._field(name, tip))
        lay.addLayout(form)

        lay.addWidget(self._sep())
        form2 = QtWidgets.QFormLayout()
        form2.setSpacing(4)
        self.chk_step_auto = QtWidgets.QCheckBox("авто: половина наименьшего R")
        self.chk_step_auto.setToolTip(
            "Шаг сетки считается сам от НАИМЕНЬШЕГО радиуса работающих малых типов и "
            "обновляется при его смене. Снимите галочку — держать своё значение.")
        self.chk_step_auto.stateChanged.connect(self._auto_changed)
        form2.addRow(self.chk_step_auto)
        for name, label, tip in self.PLACE_FIELDS:
            form2.addRow(label + ":", self._field(name, tip))
        lay.addLayout(form2)
        lay.addStretch(1)
        return box

    def _field(self, name, tip):
        e = QtWidgets.QLineEdit()
        e.setToolTip(tip)
        e.returnPressed.connect(self._apply)
        self._edits[name] = e
        return e

    @staticmethod
    def _sep():
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        return line

    def _build_table(self):
        """Таблица датчиков во всю ширину — с прокруткой и кнопками справа."""
        box = QtWidgets.QGroupBox("ДАТЧИКИ НА КАРТЕ")
        lay = QtWidgets.QVBoxLayout(box)

        top = QtWidgets.QHBoxLayout()
        self.lbl_count = QtWidgets.QLabel("датчиков: 0")
        self.lbl_count.setStyleSheet("color:%s;" % THEME["muted"])
        top.addWidget(self.lbl_count, 1)
        for text, tip, slot in (
                ("Из файла…", "Прочитать датчики из текстового файла: номер, тип, "
                              "режим, долгота, широта.", self._load_file),
                ("В файл…", "Записать таблицу в текстовый файл — его же программа "
                            "читает обратно без правки руками.", self._save_file),
                ("Добавить", "Добавить датчик строкой: тип, режим и координаты.",
                 self._add_row),
                ("По клику на карте", "Ставить датчики двойным кликом по карте. "
                                      "Повторный двойной клик — выход из режима.",
                 self._pick_mode)):
            b = QtWidgets.QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            top.addWidget(b)
        lay.addLayout(top)

        mid = QtWidgets.QHBoxLayout()
        self.table = QtWidgets.QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        self.table.setSelectionMode(QtWidgets.QTableWidget.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        # ⚠️ ФИКСИРОВАННАЯ ВЫСОТА + СВОЯ ПРОКРУТКА. Датчиков бывает за полсотни (три типа
        # по двадцать плюс большие), и таблица, растущая под содержимое, вытолкнула бы
        # кнопки за край экрана.
        self.table.setMinimumHeight(180)
        self.table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        # ⚠️ ТАБЛИЦА — ОБЫЧНАЯ, БЕЛАЯ, С ЧЁРНЫМ ТЕКСТОМ (требование заказчика 05.09.2026).
        # Тёмная тема THEME живёт на КАРТЕ, где она уместна: там подложка тёмная. Здесь
        # же таблицу читают как документ и сверяют с распечаткой — на белом это привычнее
        # и разборчивее. Цвет задаётся явно, а не наследуется: иначе таблица подхватит
        # системную тему, и у заказчика с тёмной темой Windows текст окажется светлым
        # на светлом.
        self.table.setStyleSheet(
            "QTableWidget { background:#ffffff; color:#000000; "
            "gridline-color:#c8c8c8; selection-background-color:#cfe3ff; "
            "selection-color:#000000; }"
            "QHeaderView::section { background:#eeeeee; color:#000000; "
            "padding:3px; border:0px; border-right:1px solid #c8c8c8; "
            "border-bottom:1px solid #c8c8c8; }")
        self.table.setAlternatingRowColors(False)   # чередование мешает читать координаты
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        for c in range(2, len(self.COLUMNS)):
            hh.setSectionResizeMode(c, QtWidgets.QHeaderView.Stretch)
        mid.addWidget(self.table, 1)

        side = QtWidgets.QVBoxLayout()
        self.btn_edit = QtWidgets.QPushButton("Изменить")
        self.btn_edit.setToolTip("Изменить тип, режим или координаты выбранной строки.")
        self.btn_edit.clicked.connect(self._edit_row)
        self.btn_del = QtWidgets.QPushButton("Удалить")
        self.btn_del.setToolTip("Убрать выбранный датчик из таблицы и с карты.")
        self.btn_del.clicked.connect(self._del_row)
        self.btn_clear = QtWidgets.QPushButton("Очистить")
        self.btn_clear.setToolTip("Убрать ВСЕ заданные вручную датчики.")
        self.btn_clear.clicked.connect(self._clear_rows)
        side.addWidget(self.btn_edit); side.addWidget(self.btn_del)
        side.addWidget(self.btn_clear); side.addStretch(1)
        mid.addLayout(side)
        lay.addLayout(mid)
        return box

    # ---------- заполнение и чтение полей ----------
    def refresh(self, params=None):
        """Подставить в поля текущие параметры модели."""
        if params is not None:
            self._params = params
        p = self._params
        for name, e in self._edits.items():
            v = getattr(p, name, 0)
            e.setText("%d" % int(v) if name in self.INT_FIELDS else "%g" % float(v))
        big_k = getattr(self, "_k_big", None)
        if big_k is not None:
            big_k.setText("%d" % int(getattr(p, "threat_k", 3)))
        self.chk_lmax_auto.blockSignals(True)
        self.chk_lmax_auto.setChecked(not getattr(p, "threat_L_max_manual", False))
        self.chk_lmax_auto.blockSignals(False)
        self.chk_step_auto.blockSignals(True)
        self.chk_step_auto.setChecked(not getattr(p, "threat_cand_step_manual", False))
        self.chk_step_auto.blockSignals(False)
        same = bool(getattr(p, "threat_k_same", True))
        (self.rb_k_same if same else self.rb_k_own).setChecked(True)
        manual = bool(getattr(p, "threat_manual_mode", False))
        (self.rb_manual if manual else self.rb_calc).setChecked(True)
        self._auto_changed()
        self._k_mode_changed()
        self._sync_counts()

    def set_ab(self, km):
        self.lbl_ab.setText("%.0f" % km)

    def is_manual_mode(self):
        """Окно в режиме «Задать позиции»?"""
        return self.rb_manual.isChecked()

    def _auto_changed(self, *_):
        """Поля в авто-режиме показывают значение, но правке не поддаются.

        ⚠️ Цвета НЕ из тёмной темы: это системный светлый диалог, а не карта. Красить
        текст в THEME['text'] (почти белый) значило бы писать белым по белому — так уже
        было с полем L_max."""
        for chk, field in ((self.chk_lmax_auto, "threat_L_max"),
                           (self.chk_step_auto, "threat_cand_step_km")):
            e = self._edits.get(field)
            if e is None:
                continue
            auto = chk.isChecked()
            e.setText("%g" % float(getattr(self._params, field, 0.0)))
            e.setReadOnly(auto)
            e.setStyleSheet("color:#6b7785; background:#f0f0f0;" if auto else "")

    def _k_mode_changed(self, *_):
        """«Одна на всех» — поля кратности у типов 2–4 показывают общее число и закрыты
        для правки; «у каждого своя» — открываются."""
        same = self.rb_k_same.isChecked()
        for name in ("threat_k2", "threat_k3"):
            e = self._edits.get(name)
            if e is None:
                continue
            e.setReadOnly(same)
            e.setStyleSheet("color:#6b7785; background:#f0f0f0;" if same else "")
        if same:
            self._k_edited(self._edits["threat_k"].text())

    def _k_edited(self, text):
        """Кратность вводится в одной группе и СРАЗУ дублируется в остальные — человек
        должен видеть в полях то число, с которым программа будет считать, а не
        догадываться о нём (требование заказчика)."""
        if not self.rb_k_same.isChecked():
            return
        for name in ("threat_k2", "threat_k3"):
            e = self._edits.get(name)
            if e is not None and e.text() != text:
                e.setText(text)
        big_k = getattr(self, "_k_big", None)
        if big_k is not None:
            big_k.setText(text)

    def _mode_changed(self, *_):
        manual = self.rb_manual.isChecked()
        self.lbl_note.setText(
            "Позиции берутся из таблицы: что в ней, то и на карте. "
            "Количество считается по таблице — задать можно только радиус и кратность."
            if manual else
            "Позиции подбирает алгоритм. Датчики, поставленные вручную, он берёт как "
            "есть и ДОБИРАЕТ остальные до заказанного количества.")
        self._sync_counts()
        self.on_mode_changed(manual)

    def _sync_counts(self):
        """В режиме «Задать позиции» КОЛИЧЕСТВО задаёт таблица, а не человек.

        ⚠️ Требование заказчика 05.09.2026: «интерфейс сам ставит значения в зависимости
        от того, что ввели, — мы менять не можем, только задать радиус и k». И правда:
        в этом режиме на карте ровно то, что в таблице, и поле «N = 20» при трёх строках
        обещало бы то, чего не будет. Поля становятся только для чтения и показывают
        фактическое число датчиков каждого типа."""
        manual = self.rb_manual.isChecked()
        counts = {}
        for r in self._rows:
            counts[int(r["type_id"])] = counts.get(int(r["type_id"]), 0) + 1
        for tid in TYPE_IDS:
            e = self._edits.get(self.TYPE_FIELDS[tid][0])
            if e is None:
                continue
            e.setReadOnly(manual)
            e.setStyleSheet("color:#6b7785; background:#f0f0f0;" if manual else "")
            if manual:
                e.setText("%d" % counts.get(tid, 0))
            e.setToolTip("Количество задаётся таблицей: столько датчиков этого типа "
                         "в ней сейчас. Чтобы изменить — добавьте или удалите строку."
                         if manual else
                         "Сколько датчиков этого типа ставить. 0 — тип выключен, его "
                         "настройки сохраняются.")

    def _apply(self):
        """Собрать поля и отдать контроллеру.

        Нечисловое значение берётся из текущих параметров: опечатка в одном поле не
        должна ронять остальные."""
        vals = {}
        for name, e in self._edits.items():
            txt = e.text().strip().replace(",", ".")
            try:
                vals[name] = int(float(txt)) if name in self.INT_FIELDS else float(txt)
            except ValueError:
                vals[name] = getattr(self._params, name, 0)
        vals["threat_k_same"] = self.rb_k_same.isChecked()
        if vals["threat_k_same"]:                  # общая кратность — одно число на всех
            vals["threat_k2"] = vals["threat_k3"] = vals["threat_k"]
        lmax_auto = self.chk_lmax_auto.isChecked()
        vals["threat_L_max_manual"] = not lmax_auto
        if lmax_auto:
            vals.pop("threat_L_max", None)         # его считает модель
        step_auto = self.chk_step_auto.isChecked()
        vals["threat_cand_step_manual"] = not step_auto
        if step_auto:
            vals.pop("threat_cand_step_km", None)
        ui_state.save(STATE_KEY, dict(k_same=vals["threat_k_same"],
                                      manual_mode=self.is_manual_mode(),
                                      lmax_auto=lmax_auto, step_auto=step_auto))
        self._on_apply(vals)

    # ---------- таблица ----------
    def set_rows(self, rows):
        """Показать датчики в таблице. `rows` — как отдаёт `ThreatModel.sensors_table()`."""
        self._rows = list(rows or [])
        self.table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            cells = ("%d" % (i + 1), "%d" % int(r["type_id"]),
                     mode_word(bool(r.get("static"))),
                     "%.6f" % float(r["lon"]), "%.6f" % float(r["lat"]))
            for c, text in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(text)
                if c in (0, 1):
                    it.setTextAlignment(QtCore.Qt.AlignCenter)
                # НОМЕР — жирным всегда: по нему строку сверяют с подписью на карте,
                # где номер тоже чёрный и жирный. Закреплённый датчик выделяется целиком.
                if c == 0 or r.get("static"):
                    f = it.font(); f.setBold(True); it.setFont(f)
                self.table.setItem(i, c, it)
        n_static = sum(1 for r in self._rows if r.get("static"))
        self.lbl_count.setText("датчиков: %d%s"
                               % (len(self._rows),
                                  ("  ·  закреплённых: %d" % n_static) if n_static else ""))
        self._sync_counts()          # в ручном режиме таблица задаёт количество

    def _selected(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return rows[0].row() if rows else -1

    def _add_row(self):
        dlg = SensorRowDialog(self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            tid, static, lon, lat = dlg.values()
            self.on_manual_add(tid, static, lon, lat)

    def _edit_row(self):
        i = self._selected()
        if i < 0:
            self._warn("Выберите строку в таблице.")
            return
        r = self._rows[i]
        dlg = SensorRowDialog(self, lon=r["lon"], lat=r["lat"], title="Изменить датчик")
        dlg._select(dlg.cb_type, int(r["type_id"]))
        dlg._select(dlg.cb_mode, bool(r.get("static")))
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            tid, static, lon, lat = dlg.values()
            self.on_manual_remove(i)
            self.on_manual_add(tid, static, lon, lat)

    def _del_row(self):
        i = self._selected()
        if i < 0:
            self._warn("Выберите строку в таблице.")
            return
        self.on_manual_remove(i)

    def _clear_rows(self):
        if not self._rows:
            return
        if QtWidgets.QMessageBox.question(
                self, "Очистить", "Убрать все заданные вручную датчики?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes:
            self.on_manual_clear()

    def _pick_mode(self):
        self.on_pick_mode()

    # ---------- файл ----------
    def _start_dir(self):
        """Папка последнего файла датчиков (память полей) либо папка программы."""
        last = ui_state.get(STATE_FILE).get("dir", "")
        return last if last and os.path.isdir(last) else os.getcwd()

    def _load_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Датчики из файла", self._start_dir(),
            "Текстовые файлы (*.txt);;Все файлы (*)")
        if not path:
            return
        ui_state.save(STATE_FILE, dict(dir=os.path.dirname(path)))
        try:
            records, rep = read_sensors_file(path)
        except Exception as ex:                    # нет прав, битая кодировка, каталог
            self._warn("Файл не прочитан:\n%s" % ex)
            return
        for tid, static, lon, lat in records:
            self.on_manual_add(tid, static, lon, lat)
        msg = "Принято датчиков: %d." % rep["accepted"]
        if rep["rejected"]:
            lines = ", ".join(str(no) for no, _ in rep["rejected"][:10])
            msg += ("\nОтброшено строк: %d (%s%s).\nПричина первой: %s"
                    % (len(rep["rejected"]), lines,
                       "…" if len(rep["rejected"]) > 10 else "",
                       rep["rejected"][0][1]))
        if rep["types"]:
            msg += "\n\nВ шапке файла описаны типы: %s. Параметры типов из файла НЕ " \
                   "применяются — они берутся из полей окна." \
                   % ", ".join(str(t) for t in sorted(rep["types"]))
        QtWidgets.QMessageBox.information(self, "Датчики из файла", msg)

    def _save_file(self):
        if not self._rows:
            self._warn("Таблица пуста — записывать нечего.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Датчики в файл", os.path.join(self._start_dir(), "sensors.txt"),
            "Текстовые файлы (*.txt);;Все файлы (*)")
        if not path:
            return
        ui_state.save(STATE_FILE, dict(dir=os.path.dirname(path)))
        try:
            n = write_sensors_file(path, self._rows, self._params)
        except Exception as ex:
            self._warn("Файл не записан:\n%s" % ex)
            return
        QtWidgets.QMessageBox.information(
            self, "Датчики в файл",
            "Записано датчиков: %d.\n\n%s\n\nЭтот же файл читается кнопкой «Из файла…» "
            "без правки руками." % (n, path))

    def _warn(self, text):
        QtWidgets.QMessageBox.warning(self, "Исходные данные", text)
