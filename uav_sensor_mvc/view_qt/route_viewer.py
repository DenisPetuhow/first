# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Окно «Посмотреть маршруты» (задача 8.6, довесок 06.09.2026, заказчик).

Список маршрутов ДВУХ источников — своих пройденных на текущий момент (`iter_routes`)
и, отдельно, загруженной истории полёта (`loaded_routes`, если она есть), — выбор
показывает все точки маршрута в таблице и умеет подсветить его на карте одной толстой
линией (`ThreatMapView.render_route_preview`).

⚠️ НОВЫЙ МЕХАНИЗМ — СВОЙ ФАЙЛ (правило проекта, CLAUDE.md): просмотр — самостоятельная
надстройка над уже существующими данными, а не часть окна «Исходные данные»
(`input_window.py`) — там и без того форма + таблица датчиков + маршрут ТТХ, дописывать
в него ещё один список с таблицей значило бы смешивать разные задачи в одном файле.

Представление НИЧЕГО НЕ СЧИТАЕТ и не знает модели: данные (списки маршрутов в градусах)
подаёт `set_data`, а подсветку/сброс на карте — колбэки `on_preview(is_loaded, index)` и
`on_reset()`, которые владелец (`ThreatMapView`) пробрасывает дальше в контроллер.
"""
from PyQt5 import QtWidgets


class RouteViewerDialog(QtWidgets.QDialog):
    """Окно немодальное — не мешает работать с картой, пока открыто."""

    def __init__(self, parent, on_preview, on_reset):
        super().__init__(parent)
        self.setWindowTitle("Посмотреть маршруты")
        self.resize(620, 520)
        self._on_preview = on_preview      # callback(is_loaded: bool, index: int)
        self._on_reset = on_reset          # callback() -> убрать подсветку на карте
        self._own_routes = []              # свои пройденные: список массивов (lon, lat)
        self._loaded_routes = []           # загруженная история: то же самое

        col = QtWidgets.QVBoxLayout(self)

        self.lbl_status = QtWidgets.QLabel("—")
        self.lbl_status.setWordWrap(True)
        col.addWidget(self.lbl_status)

        row_src = QtWidgets.QHBoxLayout()
        self.btn_own = QtWidgets.QPushButton("Свои маршруты")
        self.btn_own.setCheckable(True); self.btn_own.setChecked(True)
        self.btn_own.setToolTip("Маршруты, пройденные к текущему моменту (итерации).")
        self.btn_loaded = QtWidgets.QPushButton("Загруженная история")
        self.btn_loaded.setCheckable(True)
        self.btn_loaded.setToolTip("Маршруты из файла, загруженного кнопкой «Загрузить "
                                   "историю полёта». Недоступно, если ничего не загружено.")
        grp = QtWidgets.QButtonGroup(self); grp.setExclusive(True)
        grp.addButton(self.btn_own); grp.addButton(self.btn_loaded)
        self.btn_own.toggled.connect(self._refresh_list)
        row_src.addWidget(self.btn_own); row_src.addWidget(self.btn_loaded)
        col.addLayout(row_src)

        row = QtWidgets.QHBoxLayout()
        self.list = QtWidgets.QListWidget()
        self.list.setMaximumWidth(220)
        self.list.currentRowChanged.connect(self._show_route)
        row.addWidget(self.list)

        right = QtWidgets.QVBoxLayout()
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["№", "lon", "lat"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        right.addWidget(self.table, 1)
        btn_row = QtWidgets.QHBoxLayout()
        self.btn_show = QtWidgets.QPushButton("Показать на карте")
        self.btn_show.setToolTip("Подсветить выбранный маршрут на карте толстой белой "
                                 "линией — независимо от галочек показа.")
        self.btn_show.clicked.connect(self._show_on_map)
        self.btn_clear_preview = QtWidgets.QPushButton("Сбросить")
        self.btn_clear_preview.setToolTip("Убрать подсветку — карта вернётся к обычному "
                                          "показу (тому, что решают галочки «ПОКАЗ»).")
        self.btn_clear_preview.clicked.connect(self._reset_preview)
        btn_row.addWidget(self.btn_show); btn_row.addWidget(self.btn_clear_preview)
        right.addLayout(btn_row)
        row.addLayout(right, 1)
        col.addLayout(row)

        close = QtWidgets.QPushButton("Закрыть")
        close.clicked.connect(self.close)
        col.addWidget(close)

    # ---------- данные ----------
    def set_data(self, own_routes_lonlat, loaded_routes_lonlat):
        """Обновить оба списка — вызывается и при открытии окна, и ДИНАМИЧЕСКИ по ходу
        прохода (заказчик 06.09.2026: «в таблице чтобы сразу записывались маршруты после
        прохода», а не только при следующем открытии окна) — владелец зовёт это после
        каждого завершённого маршрута, если окно уже открыто.

        ⚠️ ВЫБОР СОХРАНЯЕТСЯ. Разрастание списка на каждый новый маршрут не должно
        сбрасывать то, что человек сейчас разглядывает; исключение — если он смотрел
        именно ПОСЛЕДНИЙ (самый свежий) маршрут, тогда выбор следует за новым последним,
        чтобы видеть, что добавилось только что."""
        prev_row = self.list.currentRow()
        prev_count = len(self._current_source())
        self._own_routes = own_routes_lonlat
        self._loaded_routes = loaded_routes_lonlat
        has_loaded = len(loaded_routes_lonlat) > 0
        self.btn_loaded.setEnabled(has_loaded)
        self.lbl_status.setText(
            "Своих маршрутов пройдено: %d.  Загруженная история: %s."
            % (len(own_routes_lonlat),
               ("есть, маршрутов %d" % len(loaded_routes_lonlat)) if has_loaded
               else "нет"))
        if not has_loaded and self.btn_loaded.isChecked():
            self.btn_own.setChecked(True)              # переключение само вызовет _refresh_list
            return
        src = self._current_source()
        keep_row = prev_row
        # ⚠️ УСЛОВИЕ «БЫЛ НЕПУСТ» ОБЯЗАТЕЛЬНО: при первом открытии список пуст,
        # prev_row=-1 и prev_count=0, а -1 == 0-1 — ложное совпадение отправляло бы
        # выбор на ПОСЛЕДНИЙ маршрут вместо первого сразу при открытии окна.
        if prev_count > 0 and prev_row == prev_count - 1 and len(src) > prev_count:
            keep_row = len(src) - 1                    # смотрели последний — остаёмся на нём
        self._refresh_list(keep_row)

    def _current_source(self):
        return self._loaded_routes if self.btn_loaded.isChecked() else self._own_routes

    def _refresh_list(self, keep_row=-1):
        self.list.blockSignals(True)
        self.list.clear()
        src = self._current_source()
        for i in range(len(src)):
            self.list.addItem("Маршрут %d (%d точек)" % (i + 1, len(src[i])))
        self.list.blockSignals(False)
        if not src:
            self.table.setRowCount(0)
            return
        row = keep_row if 0 <= keep_row < len(src) else 0
        self.list.setCurrentRow(row)
        self._show_route(row)          # setCurrentRow молчит, если строка не изменилась

    # ---------- показ ----------
    def _show_route(self, row):
        src = self._current_source()
        if row < 0 or row >= len(src):
            self.table.setRowCount(0)
            return
        pts = src[row]
        self.table.setRowCount(len(pts))
        for i, (lon, lat) in enumerate(pts):
            self.table.setItem(i, 0, QtWidgets.QTableWidgetItem(str(i + 1)))
            self.table.setItem(i, 1, QtWidgets.QTableWidgetItem("%.6f" % float(lon)))
            self.table.setItem(i, 2, QtWidgets.QTableWidgetItem("%.6f" % float(lat)))

    def _show_on_map(self):
        row = self.list.currentRow()
        src = self._current_source()
        if row < 0 or row >= len(src):
            return
        self._on_preview(self.btn_loaded.isChecked(), row)

    def _reset_preview(self):
        self._on_reset()
