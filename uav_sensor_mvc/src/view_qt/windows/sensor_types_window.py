# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Окно «Тип датчика» — справочник типов (план 13, задача 13.3).

Слева список типов участка, справа паспорт выбранного: название, класс, радиус, кратность, цвет,
правило места и его полосы, режим нового датчика по умолчанию, у ПВН — число у объектов защиты
(6 полей, §0.4е) и бонусы места (13.7); у МОГ — отступ от рек и болот и дальность по дорогам (план 13.3).
Галка «показать разрешённую карту» кладёт на карту ячейки и места типа — заказчик просил «увидеть
разрешённые районы и выборку точек»; у МОГ ещё и векторные слои: дороги, реки, ручьи, контуры болот.

⚠️ ПЕРЕХОДНЫЙ МОСТ (план 13 §13.1.4): у номеров 1–4 поля R и k показывают и правят ЧИСЛА ЗАДАНИЯ
(поля `Params`) — их отдаёт колбэк `on_task`; паспорт хранит то же. Окно немодальное: иначе за
ним не разглядеть показанную карту места. Образец — `src/view_qt/windows/uav_types_window.py`.
"""
from PyQt5 import QtCore, QtGui, QtWidgets

from model.placement import sensor_types as catalog
from model.placement.sensors import TYPE_IDS, sensor_types

# поле формы: поле паспорта · подпись · вид (text, float, int, bool, zone, kind, color) · правило места, при котором
# поле живое (None — всегда): «застройка» — ПВН, «дорога» — МОГ (план 13.3)
FIELDS = (
    ("name", "Название", "text", None),
    ("KIND", "Класс", "kind", None),
    ("r_km", "Радиус R, км", "float", None),
    ("k", "Кратность k", "int", None),
    # «площадное» (план 16.01, 16.1): его участки малым не в счёт — «степень покрытия» H; у кольца (тип 4) не действует
    ("areal", "Площадное — его участки малым не в счёт", "bool", None),
    ("color", "Цвет", "color", None),
    ("static", "Новый датчик — статический", "bool", None),
    ("zone", "Место установки", "zone", None),
    ("zone_buf_km", "Полоса у застройки, км", "float", "застройка"),
    ("strip_km", "Полоса в лесу у дорог и построек, км", "float", "застройка"),
    ("wet_buf_km", "Отступ от рек и болот, км", "float", "дорога"),
    ("drive_km", "Дальность по дорогам от целей, км (0 — без предела)", "float", "дорога"),
    ("targets", "Ставить у объектов защиты", "bool", "застройка"),
    ("tgt_min", "Меньше не ставить у цели, шт.", "int", "застройка"),   # минимум — доля по приоритету (30.09)
    ("tgt_gain_pct", "Порог прироста у цели, %", "float", "застройка"),
    ("tgt_level_pct", "Уровень защиты цели, %", "float", "застройка"),
    ("tgt_cap_one", "Потолок при одной цели, доля N", "float", "застройка"),
    ("tgt_cap_all", "Потолок на все цели, доля N", "float", "застройка"),
    ("tgt_gap_r", "Отступ от цели, доля R", "float", "застройка"),
    ("tgt_k", "Кратность защиты цели", "int", "застройка"),
    ("bonus_height", "Бонус высоте крыши", "float", "застройка"),
    ("bonus_city", "Бонус крупному пункту", "float", "застройка"),
)

# поля с галкой «от k»: число считается от кратности защиты цели и меняется вместе с ней (заказчик 25.09.2026)
AUTO = {"tgt_gain_pct": catalog.auto_gain_pct, "tgt_level_pct": catalog.auto_level_pct}


class SensorTypesWindow(QtWidgets.QDialog):
    """Справочник типов датчиков. Вход: родитель, Params, цвет типа по номеру.
    Колбэки: `on_saved()` — справочник записан, `on_task(номер, R, k)` — числа моста 1–4, `on_show_zone(номер|None)`."""
    # слагаемые карты места (Д3) по правилу места: ключ `sensor_zones.allowed_parts` и подпись; порядок — снизу вверх
    PARTS = {
        "застройка": (("forest", "лес"), ("wetland", "болота"), ("rough", "прочие неудобья"),
                      ("nopass", "непроходимо для БПЛА"), ("settled", "застройка"), ("buildings", "постройки ± полоса"),
                      ("strip", "полоса у дорог и построек")),
        "дорога": (("roads", "дороги"), ("reach", "доезжает от целей"), ("river", "реки"), ("wetland", "болота"),
                   ("ban", "запрет: реки и болота ± отступ"), ("nopass", "непроходимо для БПЛА")),
    }
    # векторные слои у правила «дорога» (заказчик 29.09.2026): ключ слоя карты (`model.layers`) и подпись. С 02.10.2026
    # (13.5.5): ЛЭП, просеки, ж/д — для сверки, МОГ по ним не едет; «пути доезда» — дерево кратчайших путей от целей со
    # стрелками; контуры болот — у галки «болота» (она рисует полигоны OSM контуром)
    VECTORS = (("road_major", "дороги крупные"), ("road_local", "дороги местные"), ("track", "лесные дороги"),
               ("river", "реки"), ("stream", "ручьи"), ("power", "ЛЭП — не дорога"), ("cutline", "просеки — не дорога"),
               ("railway", "ж/д — не дорога"), ("reach_lines", "пути доезда от целей ➜"))
    CONTOUR_PARTS = ("forest", "wetland", "rough")   # слагаемые, что рисуются контуром полигонов OSM, а не клетками
    # что входит в слой — подсказка у галки (заказчик 29.09.2026: «что относишь к неудобьям, что значит непроходимо»)
    PART_TIPS = {
        "forest": "Контуры OSM landuse=forest и natural=wood — рисуются контуром полигона. Где лес в OSM не нарисован, "
                  "его нет и здесь (восток района arh: на спутнике лес, в OSM — 3.6 % клеток против 73.5 % на западе).",
        "wetland": "Болота: natural=wetland — контуром полигона OSM (в расчёте — клетки, которые он накрывает).",
        "rough": "Прочие неудобья: natural=scrub (кустарник), heath (пустошь), sand (пески), bare_rock (скалы), "
                 "scree (осыпи) — контуром полигона OSM.",
        "nopass": "Где модель маршрутов НЕ ЛЕТАЕТ: вдали от ориентиров (глухой лес, болота без дорог и рек) и в городе "
                  "(кроме свободной зоны у цели). Тип там разрешён только не дальше R от проходимого.",
        "roads": "Ячейки с дорогой: крупной, местной или лесной (highway=track). По ним МОГ едет.",
        "reach": "Дороги, до которых МОГ доезжает от объектов защиты: Дейкстра по ячейкам дорог, 8 соседей; "
                 "с «Дальностью по дорогам» — не дальше неё.",
        "river": "Реки и каналы (waterway=river, canal). Ручьи в запрет МОГ не входят.",
        "ban": "Запрет места МОГ: реки и болота ± «Отступ от рек и болот». Проезжать по дороге через них можно "
               "(мост, гать).",
        "settled": "Застройка — ПЛОЩАДИ пунктов: застроенные ячейки весовой карты, город с буфером облёта, зона штрафа "
                   "деревень.",
        "buildings": "Постройки ± полоса — отдельные дома из OSM (building=*), расширенные на «Полосу у застройки»: "
                     "дачи, фермы, площадки вне площадей пунктов.",
        "strip": "Полоса у дорог, ЖД и построек: в ней лес и неудобья место не запрещают.",
    }
    # что показать из самой карты (заказчик 29.09.2026: «места — в группу галок»): ключ · подпись · подсказка
    SHOW = (("result", "разрешённая карта", "Ячейки, где тип можно ставить, — итог слоёв ниже."),
            ("places", "места (позиции)", "Точки, где тип может встать: крыша самой высокой постройки ячейки "
                                          "или узел шагом R/2 в ячейке без построек."))


    def __init__(self, parent, params, color_of):
        super().__init__(parent)
        self.setWindowTitle("Тип датчика")
        self.setModal(False)                         # за окном видна карта места
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Tool)
        self._params = params                        # параметры модели — числа моста 1–4
        self._color_of = color_of                    # цвет типа по номеру — его знает карта
        self._types = catalog.load()                 # справочник участка (правится до «Сохранить»)
        self._cur = -1                               # номер строки списка, чей паспорт в форме
        self._w = {}                                 # поле паспорта -> виджет формы
        self._color = ""                             # цвет в форме: ключ палитры или «#rrggbb»
        self._touched = set()                        # номера, чья форма открывалась — их числа в задание
        self._auto = {}                              # поле -> галка «от k» (порог и уровень у цели)
        self._editing = False                        # идёт правка паспорта (кнопка «Редактировать», Д1)
        self._snap = None                            # справочник и открытые формы до правки — для «Отмена»
        self.on_saved = lambda: None                 # справочник записан — карте и окну «Датчики» перечитать
        self.on_task = lambda tid, r_km, k: None     # R и k номеров 1–4 — в задание (мост)
        self.on_show_zone = lambda tid: None         # показать карту места типа; None — спрятать
        self._build_ui()
        self._fill_list()

    def _build_ui(self):
        """Список слева, форма паспорта справа, кнопки снизу. Отдаёт: ничего."""
        col = QtWidgets.QVBoxLayout(self)            # окно сверху вниз
        top = QtWidgets.QHBoxLayout()                # список и форма
        self.lst = QtWidgets.QListWidget()           # типы участка
        self.lst.setMinimumWidth(170)
        self.lst.currentRowChanged.connect(self._select)
        top.addWidget(self.lst)
        form = QtWidgets.QFormLayout()               # паспорт выбранного типа
        for field, title, kind, _pvn in FIELDS:      # поле паспорта — виджет своего вида
            if kind == "bool":                       # да / нет — галка
                w = QtWidgets.QCheckBox()            # поле формы
            elif kind in ("zone", "kind"):           # выбор из списка — выпадающий список
                w = QtWidgets.QComboBox()
                w.addItems(catalog.ZONES if kind == "zone" else sorted(catalog.KINDS))
                if kind == "zone":                   # место решает, какие поля видны
                    w.currentTextChanged.connect(self._zone_changed)
            elif kind == "color":                    # цвет — кнопка с палитрой
                w = QtWidgets.QPushButton("")
                w.clicked.connect(self._pick_color)
            else:
                w = QtWidgets.QLineEdit()
            self._w[field] = w
            if field in AUTO:                        # порог и уровень у цели — поле и галка «от k»
                cb = QtWidgets.QCheckBox("от k")     # галка: число считает программа
                cb.setToolTip("Считать от кратности защиты цели: порог 1 · 3 · 4 · 5 %, уровень 99 · 92 · 89.3 · "
                              "86.7 %. Снять — вписать своё число.")
                cb.toggled.connect(lambda _on: self._refresh_auto())
                self._auto[field] = cb
                box = QtWidgets.QHBoxLayout()        # поле и галка в строку
                box.addWidget(w, 1)
                box.addWidget(cb)
                form.addRow(title + ":", box)
            else:
                form.addRow(title + ":", w)
            if field == "k":                         # под k — с каким числом считают на самом деле
                self.lbl_k = QtWidgets.QLabel("")    # «общая / своя / паспорт»
                self.lbl_k.setWordWrap(True)
                self.lbl_k.setStyleSheet("color:#8a93a6;")
                form.addRow("", self.lbl_k)
        self._w["tgt_k"].textChanged.connect(lambda _t: self._refresh_auto())   # k правят — числа за ней
        # ПОКАЗ НА КАРТЕ (заказчик 29.09.2026): одна галка «показывать данные» — в заголовке группы; внутри — что
        # именно: разрешённая карта, места и слагаемые (Д3), каждое своей галкой. Снята — группа гаснет целиком
        self.chk_zone = QtWidgets.QGroupBox("Показывать данные на карте")
        self.chk_zone.setCheckable(True)
        self.chk_zone.setChecked(False)
        self.chk_zone.toggled.connect(self._zone_toggled)
        grid = QtWidgets.QGridLayout(self.chk_zone)  # по две галки в строке
        self._show = {}                              # «result» / «places» -> галка
        for n, (key, title, tip) in enumerate(self.SHOW):   # сама карта и места — первой строкой, включены
            cb = QtWidgets.QCheckBox(title)
            cb.setChecked(True)
            cb.setToolTip(tip)
            cb.toggled.connect(lambda _on: self._zone_toggled(self.chk_zone.isChecked()))
            self._show[key] = cb
            grid.addWidget(cb, 0, n)
        self._parts_by, self._rule_box, self._vecs = {}, {}, {}   # правило -> {слой -> галка}, блок галок; вектор -> галка
        for rule, parts in self.PARTS.items():       # у каждого правила места — свой блок галок, виден один
            box = QtWidgets.QWidget()                # блок галок правила
            lay = QtWidgets.QGridLayout(box)         # по две галки в строке
            lay.setContentsMargins(0, 0, 0, 0)
            chks = {}                                # ключ слоя -> галка
            for n, (key, title) in enumerate(parts):   # слагаемые — по умолчанию сняты
                cb = QtWidgets.QCheckBox(title)      # галка слоя
                cb.setToolTip(self.PART_TIPS[key])
                cb.toggled.connect(lambda _on: self._zone_toggled(self.chk_zone.isChecked()))
                chks[key] = cb
                lay.addWidget(cb, n // 2, n % 2)
            row = (len(parts) + 1) // 2              # строка под галками слоёв
            if rule == "застройка":                  # у ПВН — пояснение про открытую местность
                note = QtWidgets.QLabel("без контура — открытая местность (поле, луг, вырубка): в OSM её не рисуют")
                note.setStyleSheet("color:#8a93a6;")
                note.setWordWrap(True)
                lay.addWidget(note, row, 0, 1, 2)
            else:                                    # у МОГ — векторные слои линиями (заказчик 29.09.2026)
                lay.addWidget(QtWidgets.QLabel("векторные слои:"), row, 0, 1, 2)
                for n, (key, title) in enumerate(self.VECTORS):   # слой карты — своей галкой
                    cb = QtWidgets.QCheckBox(title)  # галка векторного слоя
                    cb.toggled.connect(lambda _on: self._zone_toggled(self.chk_zone.isChecked()))
                    self._vecs[key] = cb
                    lay.addWidget(cb, row + 1 + n // 2, n % 2)
            self._parts_by[rule], self._rule_box[rule] = chks, box
            grid.addWidget(box, 1, 0, 1, 2)
        self._parts = self._parts_by["застройка"]    # галки слоёв правила в форме; сменит `_zone_changed`
        form.addRow(self.chk_zone)
        top.addLayout(form, 1)
        col.addLayout(top)
        self.lbl_state = QtWidgets.QLabel("")       # итог действий
        self.lbl_state.setWordWrap(True)
        col.addWidget(self.lbl_state)
        row = QtWidgets.QHBoxLayout()                # кнопки
        self._btn = {}                               # подпись -> кнопка: доступность зависит от правки
        for title, slot, tip in (                    # кнопка: подпись, обработчик, подсказка
                ("Редактировать", self._edit, "Открыть паспорт для правки; до этого поля только для чтения."),
                ("Вернуть исходные", self._reset, "Встроенный справочник: МОГ, ПВН, большой, обычный."),
                ("Сохранить", self._save, "Проверить числа и записать справочник участка."),
                ("Отмена", self._cancel, "Вернуть паспорта, как были до «Редактировать»."),
                ("Закрыть", self.hide, "Закрыть без записи несохранённых правок.")):
            b = QtWidgets.QPushButton(title)         # кнопка окна
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
            self._btn[title] = b
        col.addLayout(row)
        self._set_editable(False)
        self.setMinimumWidth(620)

    def _fill_list(self, keep=None):
        """Список типов. Вход: номер типа, который выбрать (None — первый). Отдаёт: ничего."""
        self.lst.blockSignals(True)
        self.lst.clear()
        for t in self._types:                        # строка списка на тип
            self.lst.addItem("№%d · %s" % (t.type_id, t.name))
        self.lst.blockSignals(False)
        ids = [t.type_id for t in self._types]      # номера по строкам
        self._cur = -1
        self.lst.setCurrentRow(ids.index(keep) if keep in ids else 0)

    def open_on(self, tid):
        """Показать окно на типе `tid` (справочник перечитывается). Вход: номер типа. Отдаёт: ничего."""
        self._types = catalog.load()
        self._touched = set()                        # правок ещё не было
        self._fill_list(keep=int(tid) if tid is not None else None)
        self._set_editable(False)                    # открыли — смотрим, правка по кнопке

    def _task_values(self, t):
        """Числа R и k для формы: у номеров 1–4 — задание (Params), у прочих — паспорт. Вход: паспорт.
        Отдаёт: (R км, k)."""
        if t.type_id not in TYPE_IDS:                # тип справочника — паспорт
            return t.r_km, t.k
        spec = next((s for s in sensor_types(self._params) if s.type_id == t.type_id), None)   # тип задания
        return (spec.r_km, spec.k) if spec is not None else (t.r_km, t.k)

    def _select(self, row):
        """Выбрана строка списка: прежний паспорт — из формы в справочник, новый — в форму. Вход: номер строки.
        Отдаёт: ничего."""
        # ⚠️ только в правке: просмотренный номер 1–4 иначе попал бы в `_touched` и переписал задание
        if 0 <= self._cur < len(self._types) and self._editing:   # правили другую строку
            self._types[self._cur] = self._from_form(self._types[self._cur])
        self._cur = row
        if not 0 <= row < len(self._types):          # список пуст
            return
        t = self._types[row]                         # паспорт выбранного
        r_km, k = self._task_values(t)               # числа, с которыми считают
        for field, _title, kind, _pvn in FIELDS:     # значение паспорта — в поле формы
            w = self._w[field]                       # поле формы
            val = t.KIND if field == "KIND" else getattr(t, field)   # значение паспорта
            if field == "r_km":                      # радиус номеров 1–4 — из задания
                val = r_km
            elif field == "k":                       # кратность — тоже из задания
                val = k
            if kind == "bool":                       # галка
                w.setChecked(bool(val))
            elif kind in ("zone", "kind"):           # выпадающий список
                w.setCurrentText(str(val))
            elif kind == "color":                    # кнопка цвета — фоном
                self._color = str(val)
                w.setStyleSheet("background:%s; border:1px solid #888;" % self._color_of(t.type_id))
            else:
                w.setText(str(val) if kind == "text" else "%g" % float(val))
        self._w["KIND"].setEnabled(t.type_id not in TYPE_IDS)   # у моста класс задан номером
        for field, cb in self._auto.items():         # 0 в паспорте — «от k»
            cb.blockSignals(True)
            cb.setChecked(float(getattr(t, field)) <= 0)
            cb.blockSignals(False)
        self._refresh_auto()
        self.lbl_k.setText(self._k_note(t, k))
        self._zone_changed(t.zone)
        if self.chk_zone.isChecked():                # карта показана — показать для нового
            self.on_show_zone(t.type_id)

    def _zone_changed(self, zone):
        """Место сменилось: живы поля своего правила (полосы и цели — «застройка», отступ и дальность — «дорога»), виден
        блок галок его слоёв. Вход: правило места из списка. Отдаёт: ничего."""
        for field, _title, _kind, rule in FIELDS:    # поля паспорта
            if rule:                                 # поле своего правила места
                self._w[field].setEnabled(rule == zone)
        for cb in self._auto.values():               # галки «от k» — вместе с полями целей ПВН
            cb.setEnabled(zone == "застройка")
        for rule, box in self._rule_box.items():     # блоки галок слоёв
            box.setVisible(rule == zone)             # — виден блок своего правила
        self._parts = self._parts_by.get(zone, {})   # галки слоёв правила в форме; «везде» — слоёв нет
        self._set_editable(self._editing)            # поверх правила места — режим правки

    def _k_note(self, t, k):
        """С каким k считают и откуда оно (Д1). Вход: паспорт, k формы. Отдаёт: строку под полем k."""
        if bool(getattr(self._params, "threat_k_same", True)):   # «одна на все типы» в окне «Датчики»
            return ("считается с k = %d — одна на все типы (окно «Датчики»); число паспорта не действует"
                    % int(getattr(self._params, "threat_k", k)))
        if t.type_id in TYPE_IDS:                    # номер моста — поле задания
            return "считается с k = %d — своя, поле задания (окно «Датчики»); правка здесь пишет туда" % int(k)
        return "считается с k = %d — из этого паспорта" % int(t.k)

    def _set_editable(self, on):
        """Режим правки (Д1): без него паспорт только для чтения. Вход: bool. Поля ПВН — ещё и по правилу места.
        Отдаёт: ничего."""
        self._editing = bool(on)
        zone = self._w["zone"].currentText()         # правило места в форме
        mid = self._types[self._cur].type_id if 0 <= self._cur < len(self._types) else None   # номер в форме
        for field, _title, _kind, rule in FIELDS:    # доступность каждого поля: правка и правило места
            w = self._w[field]                       # поле формы
            live = (not rule) or rule == zone        # поле открыто своим правилом места
            if isinstance(w, QtWidgets.QLineEdit):   # текст — только для чтения, но выделяется и копируется
                w.setReadOnly(not on or (field in self._auto and self._auto[field].isChecked()))
                w.setEnabled(live)
            elif field == "KIND":                    # у моста класс задан номером
                w.setEnabled(on and mid not in TYPE_IDS)
            else:                                    # галки, списки, кнопка цвета
                w.setEnabled(on and live)
        for cb in self._auto.values():               # галки «от k» — только при правке и у ПВН
            cb.setEnabled(on and zone == "застройка")
        self.lst.setEnabled(not on)                  # при правке тип не сменить — сперва «Сохранить» или «Отмена»
        if getattr(self, "_btn", None):              # кнопки уже созданы
            self._btn["Редактировать"].setEnabled(not on)
            for title in ("Вернуть исходные", "Сохранить", "Отмена"):   # кнопки правки — только при правке
                self._btn[title].setEnabled(on)

    def _edit(self):
        """«Редактировать»: запомнить справочник и открытые формы для «Отмена», открыть поля. Отдаёт: ничего."""
        self._snap = (list(self._types), set(self._touched))
        self._set_editable(True)
        self.lbl_state.setText("Правка: «Сохранить» — записать, «Отмена» — вернуть как было.")

    def _cancel(self):
        """«Отмена»: паспорта и признак «форма открывалась» — как до «Редактировать» (иначе номер 1–4 ушёл бы в задание).
        Отдаёт: ничего."""
        if self._snap is not None:                   # правка начиналась
            self._types, self._touched = list(self._snap[0]), set(self._snap[1])
        row = self._cur                              # строка в форме
        self._cur = -1                               # форму в справочник НЕ переносить
        self._set_editable(False)
        self._select(row)
        self.lbl_state.setText("Правка отменена.")

    def _refresh_auto(self):
        """Порог и уровень с галкой «от k» — пересчитать по кратности защиты в форме; без галки — править самому.
        Отдаёт: ничего."""
        try:
            k = int(float(self._w["tgt_k"].text().replace(",", ".")))   # кратность защиты из формы
        except ValueError:                           # поле правят — пока не число
            return
        for field, cb in self._auto.items():         # поле с галкой «от k» — число считает программа
            self._w[field].setReadOnly(cb.isChecked() or not self._editing)
            if cb.isChecked():                       # от кратности — число считает программа
                self._w[field].setText("%g" % round(AUTO[field](k), 1))

    def _from_form(self, t):
        """Паспорт из формы. Вход: прежний паспорт (номер и умолчания). Отдаёт: новый паспорт."""
        self._touched.add(t.type_id)                 # форма этого типа открывалась
        vals = {}                                    # поле паспорта -> значение из формы
        for field, _title, kind, _rule in FIELDS:    # поле формы — в значение паспорта
            w = self._w[field]                       # поле формы
            if field == "KIND":                      # класс номера не правится формой
                continue
            if field in self._auto and self._auto[field].isChecked():   # «от k» — в паспорт 0
                vals[field] = 0.0
                continue
            if kind == "bool":                       # галка
                vals[field] = w.isChecked()
            elif kind == "zone":                     # правило места
                vals[field] = w.currentText()
            elif kind == "color":                    # цвет: выбранный или прежний
                vals[field] = self._color or t.color
            elif kind == "text":                     # название: пустое — прежнее
                vals[field] = w.text().strip() or t.name
            else:
                try:
                    vals[field] = (int(float(w.text().replace(",", "."))) if kind == "int"
                                   else float(w.text().replace(",", ".")))
                except ValueError:                   # опечатка — прежнее значение, скажет проверка
                    vals[field] = getattr(t, field)
        cls = catalog.KINDS.get(self._w["KIND"].currentText(), type(t))   # класс из списка
        if t.type_id in TYPE_IDS:                    # у моста класс задан номером
            cls = type(t)
        return cls(type_id=t.type_id, **vals)

    def _pick_color(self):
        """Кнопка цвета: выбрать «#rrggbb» для типа в форме. Отдаёт: ничего."""
        if not 0 <= self._cur < len(self._types):   # формы нет
            return
        t = self._types[self._cur]                   # тип в форме
        col = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._color_of(t.type_id)), self, "Цвет типа")
        if col.isValid():                            # выбрали
            self._color = col.name()
            self._w["color"].setStyleSheet("background:%s; border:1px solid #888;" % self._color)

    def show_allowed(self):
        """Галка «разрешённая карта» в группе показа. Отдаёт: bool."""
        return self._show["result"].isChecked()

    def show_places(self):
        """Галка «места (позиции)» в группе показа. Отдаёт: bool."""
        return self._show["places"].isChecked()

    def shown_parts(self):
        """Отмеченные слагаемые карты места (Д3) у правила в форме. Отдаёт: список ключей в порядке показа."""
        return [k for k, cb in self._parts.items() if cb.isChecked()]

    def shown_vectors(self):
        """Отмеченные векторные слои — только у правила «дорога» (план 13.3). Отдаёт: список ключей слоёв."""
        if self._w["zone"].currentText() != "дорога":   # векторные слои — у МОГ
            return []
        return [k for k, _t in self.VECTORS if self._vecs[k].isChecked()]

    def part_keys(self):
        """Ключи слагаемых всех правил места — для палитры. Отдаёт: множество ключей."""
        return {k for parts in self.PARTS.values() for k, _t in parts}

    def set_part_colors(self, colors, vec_colors=None):
        """Подписи галок слоёв — цветом слоя на карте (Д3). Вход: dict ключ -> «#rrggbb» слагаемых и векторных слоёв.
        Отдаёт: ничего."""
        for chks in self._parts_by.values():         # галки слоёв каждого правила
            for key, cb in chks.items():             # подпись галки — цветом её слоя
                cb.setStyleSheet("QCheckBox { color: %s; }" % colors.get(key, "#cccccc"))
        for key, cb in self._vecs.items():           # векторные слои — цветом линии
            cb.setStyleSheet("QCheckBox { color: %s; }" % (vec_colors or {}).get(key, "#cccccc"))

    def _zone_toggled(self, on):
        """Галка «Показывать данные на карте» или галка в группе: карте — номер типа в форме или None.
        Вход: включена ли группа. Отдаёт: ничего."""
        ok = 0 <= self._cur < len(self._types)       # тип в форме есть
        self.on_show_zone(self._types[self._cur].type_id if (on and ok) else None)

    def _reset(self):
        """«Вернуть исходные»: встроенный справочник в форму; записывается по «Сохранить». Отдаёт: ничего."""
        self._types = catalog.defaults()
        self._fill_list()
        self.lbl_state.setText("Показан встроенный справочник. Нажмите «Сохранить», чтобы записать.")

    def _save(self):
        """«Сохранить»: проверить паспорта, записать справочник; числа моста 1–4 — в задание. Отдаёт: ничего."""
        if 0 <= self._cur < len(self._types):        # форма — в справочник
            self._types[self._cur] = self._from_form(self._types[self._cur])
        bad = []                                     # жалобы по типам
        for t in self._types:                        # каждый паспорт — через `validate`
            bad += ["№%d, %s" % (t.type_id, s) for s in t.validate()]
        if bad:                                      # числа не проходят проверку
            QtWidgets.QMessageBox.warning(self, "Справочник не записан", "Поправьте:\n\n• " + "\n• ".join(bad))
            return
        if not catalog.save(self._types):            # нет прав, диск занят
            QtWidgets.QMessageBox.warning(self, "Справочник не записан",
                                          "Не удалось записать %s." % catalog.path())
            return
        # ⚠️ В ЗАДАНИЕ — ТОЛЬКО ТЕ НОМЕРА 1–4, ЧЬЯ ФОРМА ОТКРЫВАЛАСЬ: неоткрытый паспорт (МОГ 1 км) иначе
        # молча переписал бы рабочий радиус типа 1 (2 км — эталон)
        for t in self._types:
            if t.type_id in TYPE_IDS and t.type_id in self._touched:   # номер моста, правился в форме
                self.on_task(t.type_id, float(t.r_km), int(t.k))
        self.lbl_state.setText("Записано типов: %d · файл: %s" % (len(self._types), catalog.FILE_NAME))
        self._set_editable(False)                    # записано — снова только чтение
        self._snap = None
        self.on_saved()
