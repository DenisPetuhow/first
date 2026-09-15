# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Окно «Сектор налёта» (план 11, задача 11.6, заказчик 16.09.2026).

Всё про сектор появления БПЛА в одном окне: раствор сектора и число точек входа (меняются —
точки сразу переотбираются), «Построить сектор» (все 360°, лучшая точка на каждые 10°),
«Задать сектор» (режим на карте: щелчок по краю района; правая кнопка — выход из режима и
закрытие этого окна) и «Убрать сектор». Работает и в прямоугольном районе, и в круглом.

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

    def __init__(self, parent, on_params, on_auto, on_click_mode, on_clear):
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
        self.btn_clear.setToolTip("Убрать сектор. Точки старта, поставленные вручную, останутся.")
        self.btn_clear.clicked.connect(on_clear)
        col.addWidget(self.btn_auto)
        col.addWidget(self.btn_click)
        col.addWidget(self.btn_clear)

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

    def set_state(self, half_deg, n_entries, kind, n_points, n_manual, click_mode):
        """Показать текущее состояние. Вход: полураствор °, заказано точек шт., вид сектора
        (None / "click" / "auto"), отобрано точек шт., ручных точек шт., включён ли режим кликов."""
        self._quiet = True
        try:
            self.spin_angle.setValue(int(round(2.0 * float(half_deg))))
            self.spin_n.setValue(int(n_entries))
        finally:
            self._quiet = False
        what = {None: "сектор не задан", "click": "сектор задан кликом",
                "auto": "сектор по всем 360°"}.get(kind, str(kind))
        text = "%s · точек сектора: %d" % (what, n_points)
        if n_manual:                                 # есть точки старта человека
            text += " · заданных вручную: %d" % n_manual
        if click_mode:                               # идёт режим кликов по карте
            text += "<br><b>Режим задания: щёлкните край района; правая кнопка — выход.</b>"
        self.lbl_state.setText(text)
        self.btn_click.setText("Щёлкайте край района…" if click_mode else "Задать сектор кликом")
