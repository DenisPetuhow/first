# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Окно «Условия моделирования» (план 12, задача 12.2).

Чем летим и откуда: тип БПЛА из справочника и точка старта кликом по карте. Справочник
правится соседним окном «Виды БПЛА» (задача 12.3).

⚠️ ТОЧКА СТАРТА ВКЛЮЧАЕТ СВОЙ ЗАПАС ХОДА У КАЖДОЙ ТОЧКИ ВХОДА (задача 12.4): запас
аппарата минус прямая до этой точки. Без старта расход неизвестен, и действует один
общий запас из панели — как было до условий моделирования.

⚠️ НОВЫЙ МЕХАНИЗМ — СВОЙ ФАЙЛ (правило проекта). Немодальное, как `sector_window.py`:
точка старта ставится кликом по карте, а модальное окно карту бы перекрыло.
"""
from PyQt5 import QtCore, QtWidgets


class SimConditionsDialog(QtWidgets.QDialog):
    """Условия моделирования. Вход: родитель; колбэки on_pick_start(), on_clear_start(),
    on_type(название), on_edit_types(). Отдаёт: ничего — состояние приходит `set_state`."""

    def __init__(self, parent, on_pick_start, on_clear_start, on_type, on_edit_types):
        super().__init__(parent)
        self.setWindowTitle("Условия моделирования")
        self.setWindowFlag(QtCore.Qt.WindowContextHelpButtonHint, False)
        self._on_type = on_type
        self._quiet = False                          # список меняет программа — не применять
        col = QtWidgets.QVBoxLayout(self)

        hint = QtWidgets.QLabel(
            "Чем летим и откуда. Тип несёт скорость, крен, запас хода и высоту; точка "
            "старта — место, откуда БПЛА вышел к району.")
        hint.setWordWrap(True)
        col.addWidget(hint)

        row = QtWidgets.QHBoxLayout()
        self.combo_type = QtWidgets.QComboBox()
        self.combo_type.setMinimumHeight(28)
        self.combo_type.setToolTip("Тип БПЛА из справочника. Правится кнопкой «Виды БПЛА».")
        self.combo_type.currentIndexChanged.connect(self._type_changed)
        btn_types = QtWidgets.QPushButton("Виды БПЛА")
        btn_types.setToolTip("Справочник типов: добавить, изменить, удалить.")
        btn_types.clicked.connect(on_edit_types)
        row.addWidget(QtWidgets.QLabel("Тип БПЛА"))
        row.addWidget(self.combo_type, 1)
        row.addWidget(btn_types)
        col.addLayout(row)

        self.lbl_type = QtWidgets.QLabel("")         # характеристики выбранного типа
        self.lbl_type.setWordWrap(True)
        col.addWidget(self.lbl_type)

        self.btn_start = QtWidgets.QPushButton("Задать точку старта БПЛА")
        self.btn_start.setToolTip(
            "Окно спрячется, следующий щелчок по карте поставит точку — и окно вернётся.")
        self.btn_start.clicked.connect(on_pick_start)
        btn_clear = QtWidgets.QPushButton("Убрать точку старта")
        btn_clear.setToolTip("Точка старта перестаёт учитываться; тип остаётся выбранным.")
        btn_clear.clicked.connect(on_clear_start)
        col.addWidget(self.btn_start)
        col.addWidget(btn_clear)

        self.lbl_start = QtWidgets.QLabel("")        # координаты точки старта
        self.lbl_start.setWordWrap(True)
        self.lbl_start.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        col.addWidget(self.lbl_start)

        note = QtWidgets.QLabel(
            "Пока точка старта не задана, действует один запас хода из панели. С ней у "
            "каждой точки входа свой: запас аппарата минус прямая от старта.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        col.addWidget(note)

        btn_close = QtWidgets.QPushButton("Закрыть")
        btn_close.clicked.connect(self.close)
        col.addWidget(btn_close)
        self.setMinimumWidth(430)
        self.adjustSize()

    def _type_changed(self, *_):
        """Список типов переключили — отдать название контроллеру."""
        if self._quiet:                              # значение ставит программа
            return                                   # — это не выбор человека
        self._on_type(self.combo_type.currentText())

    def set_state(self, names, current, type_text, start_text):
        """Показать состояние. Вход: названия типов (список), выбранное название,
        строка характеристик, строка координат старта («» — не задан). Отдаёт: ничего."""
        self._quiet = True
        try:
            if [self.combo_type.itemText(i) for i in range(self.combo_type.count())] != list(names):
                self.combo_type.clear()              # справочник правили — пересобрать
                self.combo_type.addItems(list(names))
            if current and current != self.combo_type.currentText():
                self.combo_type.setCurrentText(current)
        finally:
            self._quiet = False
        self.lbl_type.setText(type_text)
        self.lbl_start.setText(start_text or "Точка старта не задана.")
