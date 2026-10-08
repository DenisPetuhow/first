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

ЧТО В ОКНЕ (с 24.09.2026, план 13, задача 13.2): условия моделирования, область и тайлы, параметры
маршрута и расстановки. ДАТЧИКИ — типы, режим «Рассчитать / Задать позиции», кратность, таблица —
переехали в своё окно `src/view_qt/windows/sensors_window.py` (кнопка «Датчики…»). Здесь остались файл датчиков
и диалог «тип и режим»: ими пользуется и окно «Датчики».
"""
import io
import os
import project_root                       # корень проекта: данные лежат рядом с src/ (журнал 364)
import time

from PyQt5 import QtCore, QtWidgets

from config import THEME
from model.placement.sensors import TYPE_IDS, KIND_LABEL, sensor_types, format_types_header
from view_qt.common import ui_state

# ── имена разделов памяти полей (`ui_state`): что подставится при следующем открытии ──
STATE_KEY = "input_window"        # параметры окна и режимы
STATE_ROW = "sensor_row"          # последний добавленный датчик: тип и режим
STATE_FILE = "sensor_file"        # последняя папка файла датчиков

FILE_MARK = "UAV-SENSORS"         # опознавательная строка в шапке файла
FILE_VERSION = "v1"

# ── СВОЯ ПАПКА ФАЙЛОВ ДАТЧИКОВ (требование заказчика 05.09.2026) ──────────────────────
# Две подпапки: что читаем — в одной, что пишем — в другой. Раньше файл предлагался в
# корне программы, рядом с исходниками, и выгрузки терялись среди них.
SENSORS_DIR = "датчики"
DIR_LOAD, DIR_SAVE = "загрузка", "выгрузка"

# Слова, которыми в файле и в таблице записывается режим датчика. По-русски — человеку,
# по-английски (s/d) — на случай чужой кодировки: файл правят руками в блокноте.
_STATIC_WORDS = ("статический", "стат", "static", "s", "с")   # ⚠️ последняя — РУССКАЯ «с»
_DYNAMIC_WORDS = ("динамический", "дин", "dynamic", "d", "д")


# ══════════════════════════════════════════════════════════════════════════════
# ФАЙЛ ДАТЧИКОВ (формат — план 8, §8.7.7). Один и тот же на чтение и на запись:
# файл, выгруженный программой, обязан читаться ею же без правки руками.
# ══════════════════════════════════════════════════════════════════════════════
def sensors_dir(kind):
    """Папка файлов датчиков: `датчики/загрузка` или `датчики/выгрузка`.

    Создаётся при первом обращении — обеих подпапок в репозитории нет, это рабочие
    данные. Считается от КОРНЯ ПРОГРАММЫ, а не от `os.getcwd()`: рабочая директория
    зависит от того, откуда программу запустили, и файлы разбредались бы по диску.
    Не удалось создать (нет прав, диск только на чтение) — отдаём корень программы:
    диалог всё равно откроется, просто не в своей папке."""
    root = os.path.join(project_root.ROOT,   # папка датчиков в корне проекта
                        SENSORS_DIR, kind)
    try:
        if not os.path.isdir(root):
            os.makedirs(root)
    except OSError:
        return project_root.ROOT
    return root


def default_file_name(when=None):
    """Имя файла выгрузки по дате и времени: `2026_09_05_09_15_sensor.txt`.

    ⚠️ ГОД ПЕРВЫМ, разделитель — подчёркивание (заказчик 05.09.2026). Имена в таком
    виде сортируются по алфавиту так же, как по времени, и в папке выгрузки видно
    порядок работ. Двоеточие времени в имени файла Windows не допускает."""
    return time.strftime("%Y_%m_%d_%H_%M_sensor.txt", when or time.localtime())


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


def read_sensors_file(path, known=None):
    """Прочитать файл датчиков. Вход: путь, допустимые номера типов (None — 1–4; окно «Датчики» даёт
    и справочник ≥ 5, план 13). Отдаёт: (записи [(type_id, static, lon, lat)], отчёт: accepted,
    rejected [(номер строки, причина)], types — таблица типов из шапки)."""
    # ⚠️ ОДНА ПЛОХАЯ СТРОКА НЕ РОНЯЕТ ЧТЕНИЕ: «принято 18, отброшено 2 (строки 7, 12)» полезнее отказа —
    # файлы правят руками. ⚠️ НОМЕР В НАЧАЛЕ СТРОКИ НЕОБЯЗАТЕЛЕН: опора — слово режима, тип перед ним,
    # координаты после; так читается и выгрузка программы, и строка из трёх полей, набранная руками
    ids = tuple(known) if known else TYPE_IDS        # допустимые номера типов
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
        if tid not in ids:                           # такого типа нет
            rejected.append((no, "тип %d: бывают только %s"
                             % (tid, ", ".join(str(t) for t in ids))))
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
            # подпись — название из справочника (13.2): «малый» не отличает МОГ от ПВН
            self.cb_type.addItem("%d · %s (R = %g км)" % (s.type_id, s.name, s.r_km), s.type_id)
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
            "поставить датчик там, где он нужен.\n\n"
            "Динамический в режиме «Рассчитать позиции» место не закрепляет: по кнопке "
            "«Расставить датчики» алгоритм подберёт его сам, и датчик может встать в "
            "другой точке. В режиме «Задать позиции» подбора нет вовсе — там оба режима "
            "стоят там, где заданы.")
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
    """Окно «Исходные данные»: условия, область, параметры маршрута и расстановки. Датчики — в своём
    окне «Датчики» (план 13, 13.2), отсюда оно открывается кнопкой. НЕ блокирует основное окно."""

    # Параметры маршрута. Кортеж: поле Params, подпись, подсказка. Кортеж: поле Params, подпись, подсказка.
    ROUTE_FIELDS = (
        ("threat_L_max", "Запас хода L_max, км",
         "Главное ограничение маршрута: полная длина пути вход→цель не может его "
         "превысить."),
        # ⚠️ СКОРОСТЬ И КРЕН УБРАНЫ ОТСЮДА 21.09.2026 (решение заказчика): они — свойства
        # ВЫБРАННОГО ТИПА БПЛА и правятся в справочнике «Виды БПЛА», а два места для одной
        # величины расходятся. Поля `threat_speed_kmh` и `threat_bank_deg` живы в `Params`.
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
    INT_FIELDS = {"L_seg"}                         # целые поля окна: число сегментов, шт.

    def __init__(self, parent, params, on_apply):
        super().__init__(parent)
        self.setWindowTitle("Исходные данные")
        self.setModal(False)                       # НЕ блокирует основное окно
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Tool)
        self._params = params                      # параметры модели (общие с картой)
        self._on_apply = on_apply                  # отдать поля контроллеру
        self._edits = {}                           # поле Params -> поле окна
        # колбэки — их ставит threat_view (представление ничего не знает о модели)
        self.on_open_sensors = lambda: None        # «Датчики…» — своё окно (план 13, 13.2)
        self.on_view_routes = lambda: None         # «Посмотреть маршруты» (задача 8.6)
        self.on_pick_area = lambda: None           # «Область…» — папка с область.txt (10.5)
        self.on_pick_tiles = lambda reset: None    # «Папка тайлов…», reset — по умолчанию
        self.on_sim_conditions = lambda: None      # «Задать условия моделирования» (12.2)
        self._build_ui()
        self.refresh(params)

    # ---------- построение окна ----------
    def _build_ui(self):
        """Окно в прокрутке: условия, область, кнопки «Датчики» и маршрутов, параметры маршрута. Отдаёт: ничего."""
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # ⚠️ ВСЁ ОКНО В ПРОКРУТКЕ: окно, вылезшее за край экрана, кнопкой «Применить» не нажать
        scroll = QtWidgets.QScrollArea()           # прокрутка всего окна
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        inner = QtWidgets.QWidget()                # содержимое прокрутки
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        lay = QtWidgets.QVBoxLayout(inner)         # блоки сверху вниз
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        lay.addWidget(self._build_conditions())    # условия моделирования — перед областью (12.2)
        lay.addWidget(self._build_area())          # область и папка тайлов
        lay.addWidget(self._build_sensors_link())  # «Датчики…» и «Посмотреть маршруты»
        lay.addWidget(self._build_route(), 1)      # параметры маршрута и расстановки
        row = QtWidgets.QHBoxLayout()              # кнопки окна
        row.addStretch(1)
        btn_apply = QtWidgets.QPushButton("Применить")
        btn_apply.setDefault(True)
        btn_apply.clicked.connect(self._apply)
        btn_close = QtWidgets.QPushButton("Закрыть")
        btn_close.clicked.connect(self.hide)
        row.addWidget(btn_apply); row.addWidget(btn_close)
        lay.addLayout(row)
        scr = QtWidgets.QApplication.primaryScreen()   # экран — под него размер окна
        avail = scr.availableGeometry() if scr is not None else QtCore.QRect(0, 0, 1200, 800)   # доступно
        self.resize(min(620, avail.width() - 60), min(760, avail.height() - 60))

    def _build_conditions(self):
        # Блок «Условия моделирования» (план 12, 12.2) — ПЕРЕД областью, как велел заказчик.
        # Вход: ничего. Отдаёт: QGroupBox с кнопкой и подписью о доступности.
        box = QtWidgets.QGroupBox("Условия моделирования")   # рамка блока
        v = QtWidgets.QVBoxLayout(box)
        self.btn_conditions = QtWidgets.QPushButton("Задать условия моделирования")
        self.btn_conditions.setToolTip(
            "Тип БПЛА со своими характеристиками и точка старта, откуда он вышел к району.")
        self.btn_conditions.clicked.connect(lambda: self.on_sim_conditions())
        v.addWidget(self.btn_conditions)
        self.lbl_conditions = QtWidgets.QLabel("")
        self.lbl_conditions.setWordWrap(True)
        self.lbl_conditions.setStyleSheet("color:%s;" % THEME["muted"])
        v.addWidget(self.lbl_conditions)
        return box

    def set_conditions_ready(self, ready, why=""):
        # Доступность кнопки условий: без сектора и цели считать запас хода не от чего.
        # Вход: можно ли открыть (bool), причина отказа. Отдаёт: ничего.
        self.btn_conditions.setEnabled(bool(ready))
        self.lbl_conditions.setText(why)

    def _build_area(self):
        # Блок «Область и папка тайлов» — самый первый: от него зависит всё окно (план 10 §10.5).
        # Вход: ничего. Отдаёт: QGroupBox с подписью и тремя кнопками; нажатия — в колбэки.
        box = QtWidgets.QGroupBox("Область и папка тайлов")   # рамка блока
        v = QtWidgets.QVBoxLayout(box)                          # подпись сверху, кнопки строкой
        self.lbl_area = QtWidgets.QLabel("")
        self.lbl_area.setWordWrap(True)
        self.lbl_area.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        v.addWidget(self.lbl_area)
        row = QtWidgets.QHBoxLayout()
        b_area = QtWidgets.QPushButton("Область…")
        b_area.setToolTip(
            "Папка области с файлом «область.txt»: координаты области и начального района, "
            "векторные слои и рельеф. Программа перезапустится на выбранной области.")
        b_area.clicked.connect(lambda: self.on_pick_area())
        b_tiles = QtWidgets.QPushButton("Папка тайлов…")
        b_tiles.setToolTip("Папка, ОТКУДА показывается подложка и КУДА докачиваются тайлы. "
                           "Меняется сразу, без перезапуска.")
        b_tiles.clicked.connect(lambda: self.on_pick_tiles(False))
        b_tiles0 = QtWidgets.QPushButton("Тайлы по умолчанию")
        b_tiles0.setToolTip("Вернуть папку tile_cache/ рядом с программой.")
        b_tiles0.clicked.connect(lambda: self.on_pick_tiles(True))
        row.addWidget(b_area); row.addWidget(b_tiles); row.addWidget(b_tiles0); row.addStretch(1)
        v.addLayout(row)
        return box

    def set_area_info(self, html):
        # Подпись блока области: что открыто и где тайлы; текст готовит карта (`_area_info_text`).
        # Вход: HTML-строка. Отдаёт: ничего.
        self.lbl_area.setText(html)

    def _build_sensors_link(self):
        """Кнопки «Датчики…» (своё окно, план 13, 13.2) и «Посмотреть маршруты» (8.6). Отдаёт: QGroupBox."""
        box = QtWidgets.QGroupBox("Датчики и маршруты")   # рамка блока
        row = QtWidgets.QHBoxLayout(box)           # кнопки строкой
        self.btn_sensors = QtWidgets.QPushButton("Датчики…")
        self.btn_sensors.setToolTip("Типы датчиков, их число, радиус и кратность, режим расстановки и "
                                    "таблица датчиков — в своём окне.")
        self.btn_sensors.clicked.connect(lambda: self.on_open_sensors())
        self.btn_view_routes = QtWidgets.QPushButton("Посмотреть маршруты")
        self.btn_view_routes.setToolTip(
            "Список маршрутов, пройденных к этому моменту, и отдельно — загруженная история полёта. "
            "Выбор показывает все точки маршрута в таблице и умеет подсветить его на карте.")
        self.btn_view_routes.clicked.connect(lambda: self.on_view_routes())
        row.addWidget(self.btn_sensors); row.addWidget(self.btn_view_routes); row.addStretch(1)
        return box

    def _build_route(self):
        """Правая колонка — параметры маршрута и расстановки."""
        box = QtWidgets.QGroupBox("МАРШРУТ БПЛА")
        lay = QtWidgets.QVBoxLayout(box)
        form = QtWidgets.QFormLayout()
        form.setSpacing(4)

        self.lbl_ab = QtWidgets.QLabel("—")
        form.addRow("|AB| (вход→цель), км:", self.lbl_ab)
        # ⚠️ ЧЕКБОКС ПЕРЕВЁРНУТ 21.09.2026 (решение заказчика): было «авто», стало «задать
        # вручную». Так короче объяснить: снят — запас считается сам и у КАЖДОЙ точки входа
        # свой (минус путь от старта БПЛА); поставлен — одно число на всех, из поля ниже.
        self.chk_lmax_manual = QtWidgets.QCheckBox("задать вручную (одинаково для всех точек)")
        self.chk_lmax_manual.setToolTip(
            "Снят — запас хода считается сам: у каждой точки входа свой, с учётом "
            "расстояния от старта БПЛА (без старта — |AB| + 25 %).\n"
            "Поставлен — работает число из поля «Запас хода», одно для всех точек.")
        self.chk_lmax_manual.stateChanged.connect(self._auto_changed)
        form.addRow(self.chk_lmax_manual)
        for name, label, tip in self.ROUTE_FIELDS:
            form.addRow(label + ":", self._field(name, tip))
        # ЧИСЛО СЕГМЕНТОВ (12.6) — переехало сюда из колонки датчиков (13.2): оно про маршрут.
        # ⚠️ ПОДСКАЗКИ НЕТ — прямое указание заказчика 20.09.2026.
        e_seg = QtWidgets.QLineEdit()            # число сегментов маршрута, шт. (1…31)
        e_seg.returnPressed.connect(self._apply)
        self._edits["L_seg"] = e_seg
        form.addRow("Сегментов маршрута:", e_seg)
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

    # ---------- заполнение и чтение полей ----------
    def refresh(self, params=None):
        """Подставить в поля текущие параметры модели. Вход: Params или None. Отдаёт: ничего."""
        if params is not None:                     # пришли свежие параметры
            self._params = params
        p = self._params
        for name, e in self._edits.items():
            v = getattr(p, name, 0)                # значение параметра
            e.setText("%d" % int(v) if name in self.INT_FIELDS else "%g" % float(v))
        self.chk_lmax_manual.blockSignals(True)
        self.chk_lmax_manual.setChecked(bool(getattr(p, "threat_L_max_manual", False)))
        self.chk_lmax_manual.blockSignals(False)
        self.chk_step_auto.blockSignals(True)
        self.chk_step_auto.setChecked(not getattr(p, "threat_cand_step_manual", False))
        self.chk_step_auto.blockSignals(False)
        self._auto_changed()

    def set_ab(self, km):
        self.lbl_ab.setText("%.0f" % km)

    def _auto_changed(self, *_):
        """Поля в авто-режиме показывают значение, но правке не поддаются.

        ⚠️ Цвета НЕ из тёмной темы: это системный светлый диалог, а не карта. Красить
        текст в THEME['text'] (почти белый) значило бы писать белым по белому — так уже
        было с полем L_max."""
        # ⚠️ у запаса хода галочка ПЕРЕВЁРНУТА: «задать вручную» — значит поле ОТКРЫТО
        for auto, field in ((not self.chk_lmax_manual.isChecked(), "threat_L_max"),
                            (self.chk_step_auto.isChecked(), "threat_cand_step_km")):
            e = self._edits.get(field)
            if e is None:
                continue
            e.setText("%g" % float(getattr(self._params, field, 0.0)))
            e.setReadOnly(auto)
            e.setStyleSheet("color:#6b7785; background:#f0f0f0;" if auto else "")

    def _apply(self):
        """Собрать поля и отдать контроллеру. Нечисловое значение — из текущих параметров: опечатка в
        одном поле не роняет остальные."""
        vals = {}                                  # имя поля Params -> значение
        for name, e in self._edits.items():
            txt = e.text().strip().replace(",", ".")   # текст поля
            try:
                vals[name] = int(float(txt)) if name in self.INT_FIELDS else float(txt)
            except ValueError:                     # не число — прежнее значение
                vals[name] = getattr(self._params, name, 0)
        lmax_auto = not self.chk_lmax_manual.isChecked()   # галочка «задать вручную»
        vals["threat_L_max_manual"] = not lmax_auto
        if lmax_auto:                              # запас считает модель
            vals.pop("threat_L_max", None)
        step_auto = self.chk_step_auto.isChecked()   # шаг сетки — сам
        vals["threat_cand_step_manual"] = not step_auto
        if step_auto:                              # шаг считает модель
            vals.pop("threat_cand_step_km", None)
        ui_state.save(STATE_KEY, dict(lmax_auto=lmax_auto, step_auto=step_auto))
        self._on_apply(vals)
