# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Окно «Тип датчика» — справочник типов (план 13, задача 13.3).

Слева список типов участка, справа паспорт выбранного: название, класс, радиус, кратность, цвет,
правило места и его полосы, режим нового датчика по умолчанию, у ПВН — число у объектов защиты
(6 полей, §0.4е) и бонусы места (13.7). Галка «показать разрешённую карту» кладёт на карту ячейки
и места типа — заказчик просил «увидеть разрешённые районы и выборку точек».

⚠️ ПЕРЕХОДНЫЙ МОСТ (план 13 §13.1.4): у номеров 1–4 поля R и k показывают и правят ЧИСЛА ЗАДАНИЯ
(поля `Params`) — их отдаёт колбэк `on_task`; паспорт хранит то же. Окно немодальное: иначе за
ним не разглядеть показанную карту места. Образец — `view_qt/uav_types_window.py`.
"""
from PyQt5 import QtCore, QtGui, QtWidgets

from model import sensor_types as catalog
from model.sensors import TYPE_IDS, sensor_types

# поле формы: поле паспорта · подпись · вид (text, float, int, bool, zone, kind, color) · только для ПВН-подобных
FIELDS = (
    ("name", "Название", "text", False),
    ("KIND", "Класс", "kind", False),
    ("r_km", "Радиус R, км", "float", False),
    ("k", "Кратность k", "int", False),
    ("color", "Цвет", "color", False),
    ("static", "Новый датчик — статический", "bool", False),
    ("zone", "Место установки", "zone", False),
    ("zone_buf_km", "Полоса у застройки, км", "float", True),
    ("strip_km", "Полоса в лесу у дорог и построек, км", "float", True),
    ("targets", "Ставить у объектов защиты", "bool", True),
    ("tgt_min", "Минимум у цели, шт.", "int", True),
    ("tgt_gain_pct", "Порог прироста у цели, %", "float", True),
    ("tgt_level_pct", "Уровень защиты цели, %", "float", True),
    ("tgt_cap_one", "Потолок при одной цели, доля N", "float", True),
    ("tgt_cap_all", "Потолок на все цели, доля N", "float", True),
    ("tgt_gap_r", "Отступ от цели, доля R", "float", True),
    ("tgt_k", "Кратность защиты цели", "int", True),
    ("bonus_height", "Бонус высоте крыши", "float", True),
    ("bonus_city", "Бонус крупному пункту", "float", True),
)

# поля с галкой «от k»: число считается от кратности защиты цели и меняется вместе с ней (заказчик 25.09.2026)
AUTO = {"tgt_gain_pct": catalog.auto_gain_pct, "tgt_level_pct": catalog.auto_level_pct}


class SensorTypesWindow(QtWidgets.QDialog):
    """Справочник типов датчиков. Вход: родитель, Params, цвет типа по номеру.
    Колбэки: `on_saved()` — справочник записан, `on_task(номер, R, k)` — числа моста 1–4, `on_show_zone(номер|None)`."""
    # слагаемые карты места (Д3): ключ `sensor_zones.allowed_parts` и подпись; порядок — снизу вверх на карте
    PARTS = (("forest", "лес"), ("wetland", "болота"), ("rough", "прочие неудобья"), ("nopass", "непроходимо (город)"),
             ("settled", "застройка"), ("buildings", "постройки ± полоса"), ("strip", "полоса у дорог и построек"))


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
        self.on_saved = lambda: None
        self.on_task = lambda tid, r_km, k: None
        self.on_show_zone = lambda tid: None
        self._build_ui()
        self._fill_list()

    def _build_ui(self):
        """Список слева, форма паспорта справа, кнопки снизу."""
        col = QtWidgets.QVBoxLayout(self)            # окно сверху вниз
        top = QtWidgets.QHBoxLayout()                # список и форма
        self.lst = QtWidgets.QListWidget()           # типы участка
        self.lst.setMinimumWidth(170)
        self.lst.currentRowChanged.connect(self._select)
        top.addWidget(self.lst)
        form = QtWidgets.QFormLayout()               # паспорт выбранного типа
        for field, title, kind, _pvn in FIELDS:
            if kind == "bool":
                w = QtWidgets.QCheckBox()
            elif kind in ("zone", "kind"):
                w = QtWidgets.QComboBox()
                w.addItems(catalog.ZONES if kind == "zone" else sorted(catalog.KINDS))
                if kind == "zone":                   # место решает, какие поля видны
                    w.currentTextChanged.connect(self._zone_changed)
            elif kind == "color":
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
        self.chk_zone = QtWidgets.QCheckBox("показать разрешённую карту")
        self.chk_zone.setToolTip("Ячейки, где тип можно ставить, и его места — на карте.")
        self.chk_zone.toggled.connect(self._zone_toggled)
        form.addRow(self.chk_zone)
        # СЛАГАЕМЫЕ КАРТЫ (Д3): из каких слоёв она сложена — каждый своим цветом, под итоговой картой
        self._parts = {}                             # ключ слоя -> галка
        grid = QtWidgets.QGridLayout()               # по две галки в строке
        for n, (key, title) in enumerate(self.PARTS):
            cb = QtWidgets.QCheckBox(title)          # галка слоя
            cb.toggled.connect(lambda _on: self._zone_toggled(self.chk_zone.isChecked()))
            self._parts[key] = cb
            grid.addWidget(cb, n // 2, n % 2)
        form.addRow(grid)
        note = QtWidgets.QLabel("без контура — открытая местность (поле, луг, вырубка): в OSM её не рисуют")
        note.setStyleSheet("color:#8a93a6;")
        form.addRow(note)
        top.addLayout(form, 1)
        col.addLayout(top)
        self.lbl_state = QtWidgets.QLabel("")       # итог действий
        self.lbl_state.setWordWrap(True)
        col.addWidget(self.lbl_state)
        row = QtWidgets.QHBoxLayout()                # кнопки
        self._btn = {}                               # подпись -> кнопка: доступность зависит от правки
        for title, slot, tip in (
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
        for t in self._types:
            self.lst.addItem("№%d · %s" % (t.type_id, t.name))
        self.lst.blockSignals(False)
        ids = [t.type_id for t in self._types]      # номера по строкам
        self._cur = -1
        self.lst.setCurrentRow(ids.index(keep) if keep in ids else 0)

    def open_on(self, tid):
        """Показать окно на типе `tid` (справочник перечитывается). Вход: номер типа."""
        self._types = catalog.load()
        self._touched = set()                        # правок ещё не было
        self._fill_list(keep=int(tid) if tid is not None else None)
        self._set_editable(False)                    # открыли — смотрим, правка по кнопке

    def _task_values(self, t):
        """Числа R и k для формы: у номеров 1–4 — задание (Params), у прочих — паспорт. Вход: паспорт."""
        if t.type_id not in TYPE_IDS:                # тип справочника — паспорт
            return t.r_km, t.k
        spec = next((s for s in sensor_types(self._params) if s.type_id == t.type_id), None)   # тип задания
        return (spec.r_km, spec.k) if spec is not None else (t.r_km, t.k)

    def _select(self, row):
        """Выбрана строка списка: прежний паспорт — из формы в справочник, новый — в форму."""
        # ⚠️ только в правке: просмотренный номер 1–4 иначе попал бы в `_touched` и переписал задание
        if 0 <= self._cur < len(self._types) and self._editing:   # правили другую строку
            self._types[self._cur] = self._from_form(self._types[self._cur])
        self._cur = row
        if not 0 <= row < len(self._types):          # список пуст
            return
        t = self._types[row]                         # паспорт выбранного
        r_km, k = self._task_values(t)               # числа, с которыми считают
        for field, _title, kind, _pvn in FIELDS:
            w = self._w[field]                       # поле формы
            val = t.KIND if field == "KIND" else getattr(t, field)   # значение паспорта
            if field == "r_km":
                val = r_km
            elif field == "k":
                val = k
            if kind == "bool":
                w.setChecked(bool(val))
            elif kind in ("zone", "kind"):
                w.setCurrentText(str(val))
            elif kind == "color":
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
        """Место сменилось: поля полос, долей у целей и бонусов — только у типа с правилом места."""
        on = zone != "везде"                         # у типа правило места
        for field, _title, _kind, pvn in FIELDS:
            if pvn:
                self._w[field].setEnabled(on)
        for cb in self._auto.values():               # галки «от k» — вместе со своими полями
            cb.setEnabled(on)
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
        """Режим правки (Д1): без него паспорт только для чтения. Вход: bool. Поля ПВН — ещё и по правилу места."""
        self._editing = bool(on)
        rule = self._w["zone"].currentText() != "везде"   # у типа правило места — поля ПВН живые
        mid = self._types[self._cur].type_id if 0 <= self._cur < len(self._types) else None   # номер в форме
        for field, _title, kind, pvn in FIELDS:
            w = self._w[field]                       # поле формы
            live = (not pvn) or rule                 # поле открыто правилом места
            if isinstance(w, QtWidgets.QLineEdit):   # текст — только для чтения, но выделяется и копируется
                w.setReadOnly(not on or (field in self._auto and self._auto[field].isChecked()))
                w.setEnabled(live)
            elif field == "KIND":                    # у моста класс задан номером
                w.setEnabled(on and mid not in TYPE_IDS)
            else:                                    # галки, списки, кнопка цвета
                w.setEnabled(on and live)
        for cb in self._auto.values():
            cb.setEnabled(on and rule)
        self.lst.setEnabled(not on)                  # при правке тип не сменить — сперва «Сохранить» или «Отмена»
        if getattr(self, "_btn", None):              # кнопки уже созданы
            self._btn["Редактировать"].setEnabled(not on)
            for title in ("Вернуть исходные", "Сохранить", "Отмена"):
                self._btn[title].setEnabled(on)

    def _edit(self):
        """«Редактировать»: запомнить справочник и открытые формы для «Отмена», открыть поля."""
        self._snap = (list(self._types), set(self._touched))
        self._set_editable(True)
        self.lbl_state.setText("Правка: «Сохранить» — записать, «Отмена» — вернуть как было.")

    def _cancel(self):
        """«Отмена»: паспорта и признак «форма открывалась» — как до «Редактировать» (иначе номер 1–4 ушёл бы в задание)."""
        if self._snap is not None:                   # правка начиналась
            self._types, self._touched = list(self._snap[0]), set(self._snap[1])
        row = self._cur                              # строка в форме
        self._cur = -1                               # форму в справочник НЕ переносить
        self._set_editable(False)
        self._select(row)
        self.lbl_state.setText("Правка отменена.")

    def _refresh_auto(self):
        """Порог и уровень с галкой «от k» — пересчитать по кратности защиты в форме; без галки — править самому."""
        try:
            k = int(float(self._w["tgt_k"].text().replace(",", ".")))   # кратность защиты из формы
        except ValueError:                           # поле правят — пока не число
            return
        for field, cb in self._auto.items():
            self._w[field].setReadOnly(cb.isChecked() or not self._editing)
            if cb.isChecked():                       # от кратности — число считает программа
                self._w[field].setText("%g" % round(AUTO[field](k), 1))

    def _from_form(self, t):
        """Паспорт из формы. Вход: прежний паспорт (номер и умолчания). Отдаёт: новый паспорт."""
        self._touched.add(t.type_id)                 # форма этого типа открывалась
        vals = {}                                    # поле паспорта -> значение из формы
        for field, _title, kind, _pvn in FIELDS:
            w = self._w[field]                       # поле формы
            if field == "KIND":
                continue
            if field in self._auto and self._auto[field].isChecked():   # «от k» — в паспорт 0
                vals[field] = 0.0
                continue
            if kind == "bool":
                vals[field] = w.isChecked()
            elif kind == "zone":
                vals[field] = w.currentText()
            elif kind == "color":
                vals[field] = self._color or t.color
            elif kind == "text":
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
        """Кнопка цвета: выбрать «#rrggbb» для типа в форме."""
        if not 0 <= self._cur < len(self._types):   # формы нет
            return
        t = self._types[self._cur]                   # тип в форме
        col = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._color_of(t.type_id)), self, "Цвет типа")
        if col.isValid():                            # выбрали
            self._color = col.name()
            self._w["color"].setStyleSheet("background:%s; border:1px solid #888;" % self._color)

    def shown_parts(self):
        """Отмеченные слагаемые карты места (Д3). Отдаёт: список ключей в порядке показа."""
        return [k for k, _t in self.PARTS if self._parts[k].isChecked()]

    def set_part_colors(self, colors):
        """Подписи галок слоёв — цветом слоя на карте (Д3). Вход: dict ключ -> «#rrggbb»."""
        for key, cb in self._parts.items():
            cb.setStyleSheet("QCheckBox { color: %s; }" % colors.get(key, "#cccccc"))

    def _zone_toggled(self, on):
        """Галка «показать разрешённую карту»: карте — номер типа в форме или None."""
        ok = 0 <= self._cur < len(self._types)       # тип в форме есть
        self.on_show_zone(self._types[self._cur].type_id if (on and ok) else None)

    def _reset(self):
        """«Вернуть исходные»: встроенный справочник в форму; записывается по «Сохранить»."""
        self._types = catalog.defaults()
        self._fill_list()
        self.lbl_state.setText("Показан встроенный справочник. Нажмите «Сохранить», чтобы записать.")

    def _save(self):
        """«Сохранить»: проверить паспорта, записать справочник; числа моста 1–4 — в задание."""
        if 0 <= self._cur < len(self._types):        # форма — в справочник
            self._types[self._cur] = self._from_form(self._types[self._cur])
        bad = []                                     # жалобы по типам
        for t in self._types:
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
