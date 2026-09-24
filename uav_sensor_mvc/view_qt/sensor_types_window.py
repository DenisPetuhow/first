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
    ("tgt_share", "Доля от вместимости зоны цели", "float", True),
    ("tgt_gamma", "Сила приоритета γ", "float", True),
    ("tgt_min", "Минимум у цели, шт.", "int", True),
    ("tgt_cap_one", "Потолок при одной цели, доля N", "float", True),
    ("tgt_cap_all", "Потолок на все цели, доля N", "float", True),
    ("tgt_k", "Кратность цели", "int", True),
    ("bonus_height", "Бонус высоте крыши", "float", True),
    ("bonus_city", "Бонус крупному пункту", "float", True),
)


class SensorTypesWindow(QtWidgets.QDialog):
    """Справочник типов датчиков. Вход: родитель, Params, цвет типа по номеру.
    Колбэки: `on_saved()` — справочник записан, `on_task(номер, R, k)` — числа моста 1–4, `on_show_zone(номер|None)`."""

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
            form.addRow(title + ":", w)
        self.chk_zone = QtWidgets.QCheckBox("показать разрешённую карту")
        self.chk_zone.setToolTip("Ячейки, где тип можно ставить, и его места — на карте.")
        self.chk_zone.toggled.connect(self._zone_toggled)
        form.addRow(self.chk_zone)
        top.addLayout(form, 1)
        col.addLayout(top)
        self.lbl_state = QtWidgets.QLabel("")       # итог действий
        self.lbl_state.setWordWrap(True)
        col.addWidget(self.lbl_state)
        row = QtWidgets.QHBoxLayout()                # кнопки
        for title, slot, tip in (
                ("Вернуть исходные", self._reset, "Встроенный справочник: МОГ, ПВН, большой."),
                ("Сохранить", self._save, "Проверить числа и записать справочник участка."),
                ("Закрыть", self.hide, "Закрыть без записи несохранённых правок.")):
            b = QtWidgets.QPushButton(title)         # кнопка окна
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        col.addLayout(row)
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

    def _task_values(self, t):
        """Числа R и k для формы: у номеров 1–4 — задание (Params), у прочих — паспорт. Вход: паспорт."""
        if t.type_id not in TYPE_IDS:                # тип справочника — паспорт
            return t.r_km, t.k
        spec = next((s for s in sensor_types(self._params) if s.type_id == t.type_id), None)   # тип задания
        return (spec.r_km, spec.k) if spec is not None else (t.r_km, t.k)

    def _select(self, row):
        """Выбрана строка списка: прежний паспорт — из формы в справочник, новый — в форму."""
        if 0 <= self._cur < len(self._types):        # была открыта другая строка
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
        self._zone_changed(t.zone)
        if self.chk_zone.isChecked():                # карта показана — показать для нового
            self.on_show_zone(t.type_id)

    def _zone_changed(self, zone):
        """Место сменилось: поля полос, долей у целей и бонусов — только у типа с правилом места."""
        on = zone != "везде"                         # у типа правило места
        for field, _title, _kind, pvn in FIELDS:
            if pvn:
                self._w[field].setEnabled(on)

    def _from_form(self, t):
        """Паспорт из формы. Вход: прежний паспорт (номер и умолчания). Отдаёт: новый паспорт."""
        self._touched.add(t.type_id)                 # форма этого типа открывалась
        vals = {}                                    # поле паспорта -> значение из формы
        for field, _title, kind, _pvn in FIELDS:
            w = self._w[field]                       # поле формы
            if field == "KIND":
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
        self.on_saved()
