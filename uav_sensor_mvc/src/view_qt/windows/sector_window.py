# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Окно «Сектор налёта» (план 11, задача 11.6, заказчик 16.09.2026).

Всё про сектор появления БПЛА в одном окне: раствор сектора и число точек входа (меняются —
точки сразу переотбираются), «Построить сектор» (все 360°, лучшая точка на каждые 10°),
«Задать сектор» (режим на карте: щелчок по краю района; правая кнопка — выход из режима и
закрытие этого окна) и «Убрать сектор». Работает и в прямоугольном районе, и в круглом.
«Редактировать точки» и «Добавить точки» (заказчик 30.09.2026) — режим точек входа мышью, как «Править
датчики»: выбрать, перенести, удалить или ставить щелчком. Точки сектора белые и после переноса; розовые —
только добавленные человеком. Прежняя кнопка «Точки старта» панели переехала сюда.

⚠️ НОВЫЙ МЕХАНИЗМ — СВОЙ ФАЙЛ (правило проекта, CLAUDE.md), тем же приёмом, что
`analysis_window.py` и `route_viewer.py`: окно ничего не считает и модель не знает — оно
отдаёт действия колбэками, а что показать, ему сообщают через `set_state`.
"""
from PyQt5 import QtCore, QtWidgets


class SectorDialog(QtWidgets.QDialog):
    """Немодальное окно сектора налёта: работать с картой, пока оно открыто, можно.
    Вход: родитель; колбэки on_params(полураствор °, точек шт.), on_auto(), on_click_mode(),
    on_clear(). Отдаёт: ничего — всё через колбэки."""

    APPLY_DELAY_MS = 500      # пауза после последнего изменения поля до применения, мс

    def __init__(self, parent, on_params, on_auto, on_click_mode, on_clear, on_data=None, on_entry_edit=None,
                 on_entry_add=None):
        super().__init__(parent)
        self.setWindowTitle("Сектор налёта")
        self.setWindowFlag(QtCore.Qt.WindowContextHelpButtonHint, False)
        self._on_params = on_params
        self._quiet = False                          # поля меняет программа — не применять
        col = QtWidgets.QVBoxLayout(self)

        hint = QtWidgets.QLabel(
            "Откуда может появиться БПЛА. Вершина сектора — в цели; на попавшем в сектор "
            "краю района по перспективе движения отбираются точки входа. Старт каждого "
            "маршрута — случайно из них.")
        hint.setWordWrap(True)
        col.addWidget(hint)

        form = QtWidgets.QFormLayout()
        form.setVerticalSpacing(8)
        self.spin_angle = QtWidgets.QSpinBox()
        self.spin_angle.setRange(10, 360)
        self.spin_angle.setSingleStep(10)
        self.spin_angle.setSuffix("°")
        self.spin_angle.setToolTip(
            "Полный раствор сектора, заданного кликом: по половине в каждую сторону от оси "
            "«цель → клик». Меняется — точки входа сразу переотбираются.\n"
            "«Построить сектор» раствор не использует: он всегда по всем 360°.")
        self.spin_n = QtWidgets.QSpinBox()
        self.spin_n.setRange(1, 36)
        self.spin_n.setSuffix(" шт.")
        self.spin_n.setToolTip("Сколько точек входа отбирать на краю района.")
        # ⚠️ ВЫСОТА ПОЛЕЙ ЗАДАНА ЯВНО: тёмная тема окна без неё сплющивала счётчики до строки
        # в полвысоты текста — «60°» обрезалось (скриншот без экрана, 16.09.2026)
        for spin in (self.spin_angle, self.spin_n):
            spin.setMinimumHeight(28)
            spin.setMinimumWidth(110)
        form.addRow("Раствор сектора", self.spin_angle)
        form.addRow("Точек входа", self.spin_n)
        col.addLayout(form)
        # ⚠️ ПРИМЕНЕНИЕ С ЗАДЕРЖКОЙ: счётчик шлёт событие на каждый шаг стрелки, а отбор точек
        # с пересборкой карты стоит ~0.1 с — десяток шагов подряд подвесил бы окно
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.APPLY_DELAY_MS)
        self._timer.timeout.connect(self._apply_params)
        self.spin_angle.valueChanged.connect(self._params_changed)
        self.spin_n.valueChanged.connect(self._params_changed)

        self.btn_auto = QtWidgets.QPushButton("Построить сектор (360°, по 10°)")
        self.btn_auto.setToolTip(
            "Край района делится по 10° вокруг цели; в каждой доле берётся лучшая точка по "
            "перспективе движения, из них — приоритетные направления с разносом по углу.\n"
            "Старт маршрута в этом режиме не повторяется в зоне 45° три маршрута подряд.")
        self.btn_auto.clicked.connect(on_auto)
        self.btn_click = QtWidgets.QPushButton("Задать сектор кликом")
        self.btn_click.setToolTip(
            "Режим на карте: щелчок по краю района — ось сектора от цели к нему (можно "
            "щёлкать сколько угодно раз). Правая кнопка мыши — выход из режима и закрытие "
            "этого окна.")
        self.btn_click.clicked.connect(on_click_mode)
        self.btn_clear = QtWidgets.QPushButton("Убрать сектор")
        self.btn_clear.setToolTip("Убрать сектор вместе с перенесёнными точками. Добавленные вручную точки останутся.")
        self.btn_clear.clicked.connect(on_clear)
        col.addWidget(self.btn_auto)
        col.addWidget(self.btn_click)
        col.addWidget(self.btn_clear)
        # ТОЧКИ ВХОДА МЫШЬЮ (заказчик 30.09.2026) — как «Править датчики»: режим на карте; перенесённая точка
        # остаётся белой («розовые не надо»), розовые — только добавленные
        self.btn_edit = QtWidgets.QPushButton("Редактировать точки")
        self.btn_edit.setToolTip(
            "Режим на карте: щелчок — выбрать точку входа, тащить — перенести, DELETE — удалить, "
            "двойной клик — добавить.\n"
            "Перенесённая точка остаётся белой; переотбор сектора её не сдвинет и на прежнее место не встанет.\n"
            "Правая кнопка мыши или Esc — выход.")
        self.btn_edit.clicked.connect(on_entry_edit or (lambda: None))
        self.btn_add = QtWidgets.QPushButton("Добавить точки")
        self.btn_add.setToolTip(
            "Режим на карте: каждый щелчок внутри района — новая точка входа (розовая).\n"
            "Правая кнопка мыши или Esc — выход.")
        self.btn_add.clicked.connect(on_entry_add or (lambda: None))
        row = QtWidgets.QHBoxLayout()                # две кнопки точек входа в строку
        row.addWidget(self.btn_edit)
        row.addWidget(self.btn_add)
        for b, cb in ((self.btn_edit, on_entry_edit), (self.btn_add, on_entry_add)):   # кнопка и её колбэк
            b.setVisible(cb is not None)             # без колбэка кнопке нечего делать
        col.addLayout(row)
        # ⚠️ ТАБЛИЦА ЖИВЁТ ЗДЕСЬ, А НЕ В ОКНЕ УСЛОВИЙ (уточнение заказчика 20.09.2026):
        # точки входа отбирает сектор, значит и данные по ним смотрят рядом с ним
        self.btn_data = QtWidgets.QPushButton("Показать данные")
        self.btn_data.setToolTip(
            "Таблица точек входа: координаты, азимут от цели, расстояние от старта БПЛА "
            "и запас хода — его можно задать каждой точке вручную.")
        self.btn_data.clicked.connect(on_data if on_data else (lambda: None))
        self.btn_data.setVisible(on_data is not None)
        col.addWidget(self.btn_data)

        self.lbl_state = QtWidgets.QLabel("")
        self.lbl_state.setWordWrap(True)
        col.addWidget(self.lbl_state)
        btn_close = QtWidgets.QPushButton("Закрыть")
        btn_close.clicked.connect(self.close)
        col.addWidget(btn_close)
        # ширина — под подсказку в три строки; высота — по содержимому, иначе перенос строк
        # подсказки и состояния не помещался и обрезался снизу
        self.setMinimumWidth(400)
        self.adjustSize()

    def _params_changed(self, *_):
        """Поле раствора или числа точек изменилось — применить после паузы."""
        if self._quiet:                              # значение ставит программа
            return                                   # — это не правка человека
        self._timer.start()

    def _apply_params(self):
        """Пауза прошла — отдать раствор (как полураствор) и число точек контроллеру."""
        self._on_params(0.5 * float(self.spin_angle.value()), int(self.spin_n.value()))

    def set_state(self, half_deg, n_entries, kind, n_points, n_manual, click_mode, entry_mode=None):
        """Показать текущее состояние. Вход: полураствор °, заказано точек шт., вид сектора (None / "click" / "auto"),
        отобрано шт., ручных шт., режим кликов bool, режим точек мышью (None / "edit" / "add"). Отдаёт: ничего."""
        self._quiet = True
        try:
            self.spin_angle.setValue(int(round(2.0 * float(half_deg))))
            self.spin_n.setValue(int(n_entries))
        finally:
            self._quiet = False
        what = {None: "сектор не задан", "click": "сектор задан кликом",
                "auto": "сектор по всем 360°"}.get(kind, str(kind))
        text = "%s · точек сектора: %d" % (what, n_points)
        if n_manual:                                 # есть точки, добавленные человеком
            text += " · добавленных вручную: %d" % n_manual
        from view_qt.tabs.threat_view import THREAT_COLORS      # палитра вкладки 3 — одна на всё (правило проекта)
        text += ("<br>белые — сектор (и перенесённые), <span style='color:%s'>розовые</span> — добавленные вручную"
                 % THREAT_COLORS["entry_manual"])
        if click_mode:                               # идёт режим кликов по карте
            text += "<br><b>Режим задания: щёлкните край района; правая кнопка — выход.</b>"
        if entry_mode == "edit":                     # правка точек мышью
            text += "<br><b>Правка: щелчок — выбрать, тащить — перенести, DELETE — удалить; правая кнопка — выход.</b>"
        elif entry_mode == "add":                    # добавление точек мышью
            text += "<br><b>Добавление: щелчок внутри района — новая точка; правая кнопка — выход.</b>"
        self.lbl_state.setText(text)
        self.btn_click.setText("Щёлкайте край района…" if click_mode else "Задать сектор кликом")
        self.btn_edit.setText("Правлю… (ещё раз — выход)" if entry_mode == "edit" else "Редактировать точки")
        self.btn_add.setText("Добавляю… (ещё раз — выход)" if entry_mode == "add" else "Добавить точки")
