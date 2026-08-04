# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Вкладка 3 «Цифровая карта угроз».

Ограниченный демонстрационный участок местности (фиксированный bbox по трём
условным ориентирам) с жёстким пределом обзора. На карту накладываются:
  * векторные слои цифровой карты (реки, дороги, ж/д, ЛЭП, трубопроводы, лесополосы,
    мосты, застройка) — основные вероятные ориентиры маршрута БПЛА;
  * весовая сетка 500×500 м как тепловой слой (сумма весов слоёв по каждой ячейке);
  * датчики, расставленные по весовой карте (максимизация покрытого веса + разнос),
    с запретом на воду.

Только отрисовка: расчёт карты и расстановка — в model/threat_grid.py.
"""
import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets
import matplotlib.cm as cm

from config import (THEME, THREAT_LAYERS, THREAT_LAYER_ORDER, MODE_LABELS,
                    THREAT_BBOX_POINTS, THREAT_ENTRY, THREAT_TARGET,
                    THREAT_ITER_MODE_LABELS, THREAT_ITER_SPREAD_LABELS,
                    THREAT_SPEND_LABELS)
from .basemap_mixin import BasemapMixin, gm_qcolor as _qcolor
from . import geomap as gm

# ═══════════════════════════════════════════════════════════════════════════════
#  ЦВЕТА ВКЛАДКИ 3 — ПРАВИТЬ ЗДЕСЬ (одно место на всю вкладку)
# ───────────────────────────────────────────────────────────────────────────────
#  1) LAYER_STYLE — векторные слои цифровой карты (реки/дороги/ЛЭП/…): цвет,
#     толщина линии, штрих (dash). Каждый ключ = слой из THREAT_LAYERS.
#  2) THREAT_COLORS — маршруты, огибающая, датчики, маркеры и легенда. Значение
#     используется И в слое на карте, И в легенде — правка в одном месте меняет оба.
#     В комментарии указано, ГДЕ на карте применяется цвет (имя элемента/метода).
#  Формат: "#rrggbb" (hex) либо (r, g, b, a) — RGBA 0..255 для полупрозрачных заливок.
#  Прозрачность отдельных ЛИНИЙ задаётся рядом с элементом (alpha в mkPen) — см.
#  _build_scene_items; здесь — базовые цвета.
# ═══════════════════════════════════════════════════════════════════════════════
LAYER_STYLE = {
    "river":      dict(color="#3aa0ff", width=3.4, dash=None),
    "stream":     dict(color="#7cc4ff", width=1.8, dash=None),
    "road_major": dict(color="#ff9f43", width=3.2, dash=None),
    "road_local": dict(color="#ffd18c", width=2.1, dash=None),
    "railway":    dict(color="#e6edf3", width=2.3, dash=[6, 5]),
    "power":      dict(color="#f6e05e", width=2.0, dash=[2, 3]),
    "pipeline":   dict(color="#b794f6", width=2.2, dash=[8, 4]),
    "tree_row":   dict(color="#4fd18b", width=2.2, dash=[1, 3]),
    "built_up":   dict(color="#8aa0b6", width=1.7, dash=None),
}

THREAT_COLORS = {
    # — МАРШРУТЫ (линии на карте и в легенде) —
    "route_all":   "#00e5ff",           # все возможные маршруты (полупрозр.), self.route_item
    "route_iter":  "#ff5a5a",           # накопленные итерационные («выборка»), self.route_iter_item
    "route_gen":   "#ff2d2d",           # обобщённая выборка 10 %, self.route_gen_item
    "route_main":  "#0a0a0a",           # ОСНОВНОЙ (текущий) маршрут итерации — чёрный, self.route_cur_item
    "uav":         "#ff4dff",           # маркер летящего БПЛА, self.uav_marker
    # — ОГИБАЮЩАЯ И ДАТЧИКИ —
    "envelope":    (255, 70, 245, 120),  # «все места пролёта» (розовая заливка), render_routes
    "sensor":      "#ff8c1a",           # датчики: кольцо зоны обзора + центр, render_sensors
    # — ПРОЧИЕ ЗНАЧКИ/ЛЕГЕНДА —
    "crossing":    "#ffd166",           # перекрёстки-развилки (ромбы), self.crossing_scatter
    "crossing_edge": "#7a5c00",         # обводка ромбов перекрёстков
    "legend_bg":   "#0b111c",           # фон панели-легенды
    "legend_head": "#36c5f0",           # заголовок легенды
    "legend_area": "#ff4dff",           # квадрат «места пролёта» в легенде (= розовая заливка)
    "legend_main": "#e6edf3",           # образец ОСНОВНОГО маршрута в легенде: светлый, т.к.
                                        # чёрная линия (route_main) на тёмном фоне легенды не видна
    "legend_entry": "#3ddc97",          # вход A в легенде (на карте — THEME['ok'])
    "legend_target": "#ff5d6c",         # цель B в легенде (на карте — THEME['warn'])
    "legend_bridge": "#ff5d6c",         # мост в легенде
}
# Зоны-исключения (маска), render_exclusions — RGBA 0..255:
THREAT_URBAN_RGBA = (255, 93, 108, 120)   # город — красный (исключён из пролёта)
THREAT_WATER_RGBA = (58, 160, 255, 150)   # вода — синий (запрет установки датчика)


def _threat_cmap():
    try:
        return pg.colormap.getFromMatplotlib("turbo")
    except Exception:
        stops = np.linspace(0, 1, 256)
        cols = (cm.turbo(stops) * 255).astype(np.ubyte)
        return pg.colormap.ColorMap(stops, cols)


def _threat_lut():
    stops = np.linspace(0, 1, 256)
    cols = (cm.turbo(stops) * 255).astype(np.ubyte)
    return cols


def _iter_heat_lut():
    """LUT для 2-й карты (частота пролёта БПЛА) — magma, отличается от turbo весов."""
    stops = np.linspace(0, 1, 256)
    return (cm.magma(stops) * 255).astype(np.ubyte)


class ThreatMapView(QtWidgets.QWidget, BasemapMixin):
    """Экран вкладки 3: карта-подложка + слои (тепловая карта весов, векторные слои,
    огибающая, маршруты, датчики) + панель управления справа.

    ТОЛЬКО отрисовка и ввод — расчётов не содержит (их делает ThreatModel, дирижирует
    ThreatController). Каждый слой карты — отдельный элемент pyqtgraph (`ImageItem` для
    растров-масок, `PlotDataItem` для линий-маршрутов, `ScatterPlotItem` для точек). Порядок
    наложения слоёв задаётся Z-value (см. `_build_scene_items`): чем больше — тем выше;
    зона обзора датчиков стоит поверх всего. Цвета — в палитрах THREAT_COLORS / LAYER_STYLE
    вверху файла. Методы `render_*` принимают данные из контроллера и обновляют элементы;
    методы `get_*`/`set_*` — обмен значениями полей с контроллером."""

    THREAT_PARAM_SPECS = [
        ("threat_N", "Датчиков N", int),
        ("threat_R", "Радиус R, км", float),
        ("threat_k", "Кратность k", int),
        ("threat_cand_step_km", "Шаг сетки датчиков, км", float),
    ]
    # подсказки к полям (всплывают при наведении) — поясняют смысл параметров
    FIELD_TIPS = {
        "threat_N": "Сколько датчиков расставить по весовой карте.",
        "threat_R": "Радиус обнаружения одного датчика (круг покрытия), км.",
        "threat_k": "Насыщение по кратности: перекрывать одну ячейку более чем k "
                    "датчиками уже невыгодно — так они не сваливаются в одну точку.",
        "threat_cand_step_km":
            "ШАГ СЕТКИ КАНДИДАТНЫХ ПОЗИЦИЙ, км. Датчик можно поставить только в узел "
            "воображаемой сетки с этим шагом (перебор возможных мест). Меньше шаг — "
            "точнее размещение, но больше вариантов и дольше счёт; больше — грубее и "
            "быстрее.",
    }

    def __init__(self, model_bbox_km, lon0, lat0, params):
        super().__init__()
        self.bbox_km = model_bbox_km
        self._lon0, self._lat0 = lon0, lat0
        self._map_layer = getattr(params, "map_layer3", "osm")
        self._map_offline = bool(getattr(params, "map_offline3", False))
        self._lut = _threat_lut()
        self._fields = {}
        self._suppress = False
        self._layer_items = {}
        self._sensor_items = []
        self._framed = False

        self._target_mode = False
        self._data_path = None            # текущий источник (для префилла окна выбора)
        self._enabled_layers = None       # текущий набор слоёв (None = все)

        # callbacks (контроллер переопределит)
        self.on_build = lambda: None
        self.on_place = lambda: None
        self.on_apply = lambda: None
        self.on_reset = lambda: None
        self.on_reset_view = lambda: None
        self.on_mode = lambda key: None
        self.on_toggle = lambda: None
        self.on_map_layer = lambda key: None
        self.on_map_offline = lambda flag: None
        self.on_set_target = lambda x, y: None
        self.on_choose_data = lambda path, layers: None
        self.on_input_apply = lambda vals: None
        self.on_iter_mode = lambda key: None
        self.on_iter_spread = lambda key: None
        self.on_iter_spend = lambda key: None
        self.on_iter_play = lambda: None
        self.on_iter_step = lambda: None
        self.on_iter_batch = lambda: None
        self.on_iter_speed = lambda v: None
        self.on_iter_gen_frac = lambda v: None
        self._params_ref = params         # для префилла окна входных данных
        self._input_dlg = None

        self.setWindowTitle("Цифровая карта угроз — вкладка 3")
        self._apply_stylesheet()
        self._build_ui(params)
        self._build_scene_items()
        self._init_basemap(lon0, lat0, lambda: self._map_layer,
                           lambda: self._map_offline,
                           scheme_points=self._orient_points())
        self._set_view_limits(self.bbox_km)
        self.plot.scene().sigMouseClicked.connect(self._on_scene_click)
        # смена масштаба/панорама -> перерисовать слои под новый кадр (прореживание по
        # видимой области + переключение застройки растр/контуры). С задержкой, чтобы
        # не считать на каждом кадре плавного зума.
        self.on_view_changed = lambda: None
        self._view_timer = QtCore.QTimer(self)
        self._view_timer.setSingleShot(True)
        self._view_timer.setInterval(180)
        self._view_timer.timeout.connect(lambda: self.on_view_changed())
        self.vb.sigRangeChanged.connect(lambda *_: self._view_timer.start())

    # ---- режим «указать цель» (клик по карте) ----
    def _begin_target(self):
        self._target_mode = True
        self.set_title("Кликните точку ЦЕЛИ на карте (вход — фиксирован у реки)")

    def _on_scene_click(self, ev):
        if not self._target_mode:
            return
        try:
            if ev.button() != QtCore.Qt.LeftButton:
                return
        except Exception:
            pass
        pt = self.vb.mapSceneToView(ev.scenePos())
        self._target_mode = False
        self.on_set_target(float(pt.x()), float(pt.y()))

    # ---- окно выбора цифровых карт (источник + слои) ----
    def _open_data_dialog(self):
        dlg = DigitalMapsDialog(self, self._data_path, self._enabled_layers)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self._data_path, self._enabled_layers = dlg.result_choices()
            self.on_choose_data(self._data_path, self._enabled_layers)

    # ---- окно «Входные данные» (не блокирует программу) ----
    def _open_input_dialog(self):
        if self._input_dlg is None:
            self._input_dlg = InputDataDialog(self, self._params_ref,
                                              self.on_input_apply)
        self._input_dlg.refresh(self._params_ref)
        self._input_dlg.show(); self._input_dlg.raise_()

    def set_ab_distance(self, km):
        if self._input_dlg is not None:
            self._input_dlg.set_ab(km)

    def refresh_input_dialog(self):
        """Обновить поля окна «Входные данные» под текущие параметры (авто-L_max и пр.)."""
        if self._input_dlg is not None and self._input_dlg.isVisible():
            self._input_dlg.refresh(self._params_ref)

    def set_iter_checked(self, on):
        """Включить/выключить чекбокс «итерации» без повторного запуска расчёта."""
        self.chk_iter.blockSignals(True)
        self.chk_iter.setChecked(bool(on))
        self.chk_iter.blockSignals(False)

    # ---- опорные точки-ориентиры для схемы/подписей ----
    def _orient_points(self):
        pts = [(name, lon, lat) for name, (lon, lat) in THREAT_BBOX_POINTS.items()]
        pts.append((THREAT_ENTRY[0], THREAT_ENTRY[1], THREAT_ENTRY[2]))
        pts.append((THREAT_TARGET[0], THREAT_TARGET[1], THREAT_TARGET[2]))
        return pts

    def set_callbacks(self, **cbs):
        for name, fn in cbs.items():
            setattr(self, name, fn)

    def _legend_html(self):
        """HTML-легенда (что каким цветом). ВСЕ цвета берутся из палитр THREAT_COLORS и
        LAYER_STYLE (вверху файла) — правка там меняет и карту, и легенду синхронно."""
        c = THREAT_COLORS
        rows = [f'<div style="background:{c["legend_bg"]};padding:8px 12px;border:2px solid '
                f'{c["legend_head"]};border-radius:6px;font-size:10pt;color:#ffffff;line-height:155%;">']
        rows.append(f'<b style="color:{c["legend_head"]};">ЛЕГЕНДА · слои цифровой карты</b><br>')
        from config import THREAT_LAYERS as _TL
        for name in THREAT_LAYER_ORDER:
            st = LAYER_STYLE.get(name)
            if not st:
                continue
            lab = _TL.get(name, {}).get("label", name)
            rows.append(f'<span style="color:{st["color"]};font-size:13pt;">&#9644;&#9644;</span> {lab}<br>')
        rows.append(f'<span style="color:{c["legend_bridge"]};">&#9650;</span> мост &nbsp; '
                    f'<span style="color:{c["crossing"]};">&#9670;</span> пересечение (развилка)<br>')
        rows.append(f'<span style="color:{c["legend_area"]};">&#9632;</span> места пролёта (огибающая) '
                    f'&nbsp; <span style="color:{c["route_all"]};font-size:13pt;">&#9644;&#9644;</span> '
                    'возможные маршруты<br>')
        rows.append(f'<span style="color:{c["legend_main"]};font-size:13pt;">&#9644;&#9644;</span> основной '
                    f'маршрут (чёрный) &nbsp; <span style="color:{c["route_gen"]};font-size:13pt;">&#9644;&#9644;</span> '
                    'выборка маршрутов<br>')
        rows.append(f'<span style="color:{c["sensor"]};">&#9679;</span> датчик (зона обзора — поверх всего)<br>')
        rows.append(f'<span style="color:{c["legend_entry"]};">&#9733;</span> вход (A) &nbsp; '
                    f'<span style="color:{c["legend_target"]};">&#10005;</span> цель (B)')
        rows.append('</div>')
        return "".join(rows)

    # ==================================================================
    def _apply_stylesheet(self):
        self.setStyleSheet(f"""
            QWidget {{ background: {THEME['bg']}; color: {THEME['text']};
                       font-size: 12px; }}
            QFrame#panel {{ background: {THEME['panel']};
                            border: 1px solid {THEME['grid']}; border-radius: 8px; }}
            QLabel#header {{ color: {THEME['accent']}; font-weight: bold; }}
            QLabel#muted {{ color: {THEME['muted']}; font-size: 10px; }}
            QLineEdit {{ background: #e6edf3; color: #10202f; border-radius: 4px;
                         padding: 3px; }}
            QPushButton {{ background: {THEME['grid']}; color: white;
                           border-radius: 6px; padding: 7px; font-weight: bold; }}
            QPushButton:hover {{ background: {THEME['accent']}; }}
            QRadioButton, QCheckBox {{ color: {THEME['text']}; font-size: 12px; }}
            QPlainTextEdit {{ background: {THEME['axes']}; color: {THEME['text']};
                              border: 1px solid {THEME['grid']}; border-radius: 6px;
                              font-family: monospace; font-size: 11px; }}
        """)

    def _header(self, text):
        lab = QtWidgets.QLabel(text); lab.setObjectName("header")
        return lab

    def _tint(self, btn, color):
        btn.setStyleSheet(
            f"QPushButton {{ background: {color}; color: white; border-radius: 6px;"
            f" padding: 7px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {THEME['accent']}; }}")

    def _build_ui(self, params):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8); root.setSpacing(8)

        self.plot = pg.PlotWidget()
        self.pi = self.plot.getPlotItem()
        self.pi.setAspectLocked(True)
        self.pi.showGrid(x=True, y=True, alpha=0.12)
        self.pi.setLabel("bottom", "X, км", color=THEME["muted"])
        self.pi.setLabel("left", "Y, км", color=THEME["muted"])
        self.vb = self.pi.getViewBox()
        root.addWidget(self.plot, stretch=1)

        panel = QtWidgets.QFrame(); panel.setObjectName("panel")
        panel.setMinimumWidth(360)
        col = QtWidgets.QVBoxLayout(panel)
        col.setContentsMargins(12, 12, 12, 12); col.setSpacing(8)
        scroll = QtWidgets.QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFixedWidth(394); scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setWidget(panel)
        root.addWidget(scroll)

        col.addWidget(self._header("ПАРАМЕТРЫ  (Enter — пересчёт)"))
        grid = QtWidgets.QGridLayout(); grid.setSpacing(6)
        for i, (name, label, _t) in enumerate(self.THREAT_PARAM_SPECS):
            r, c = divmod(i, 2)
            cell = QtWidgets.QVBoxLayout(); cell.setSpacing(1)
            lab = QtWidgets.QLabel(label); lab.setObjectName("muted")
            edit = QtWidgets.QLineEdit(str(getattr(params, name)))
            edit.returnPressed.connect(self._on_field_submit)
            tip = self.FIELD_TIPS.get(name, "")
            lab.setToolTip(tip); edit.setToolTip(tip)
            self._fields[name] = edit
            cell.addWidget(lab); cell.addWidget(edit)
            grid.addLayout(cell, r, c)
        col.addLayout(grid)

        self.btn_input = QtWidgets.QPushButton("Входные данные…")
        self.btn_input.setToolTip("Отдельное окно (не блокирует программу): запас хода, "
                                  "скорость, крен, интервал смены направления; |AB| "
                                  "считается между входом и целью. Enter — сразу применить.")
        self.btn_input.clicked.connect(self._open_input_dialog)
        col.addWidget(self.btn_input)

        col.addWidget(self._header("КАРТА УГРОЗ"))
        self.btn_data = QtWidgets.QPushButton("Выбрать цифровые карты…")
        self.btn_data.setToolTip(
            "Отдельное окно: выбрать источник данных (файл .npz / .osm.pbf либо "
            "авто) и какие слои (реки/дороги/…) накладывать на сетку.")
        self.btn_data.clicked.connect(self._open_data_dialog)
        col.addWidget(self.btn_data)

        self.btn_build = QtWidgets.QPushButton("Построить карту")
        self.btn_build.setToolTip(
            "Наложить выбранные цифровые слои на сетку 500 м и посчитать веса ячеек.")
        self._tint(self.btn_build, THEME["accent"])
        self.btn_build.clicked.connect(lambda: self.on_build())
        col.addWidget(self.btn_build)

        self.btn_target = QtWidgets.QPushButton("Указать цель")
        self.btn_target.setToolTip(
            "Кликните точку ЦЕЛИ на карте. Точка появления БПЛА фиксирована "
            "(у реки). Цель — задел для построения маршрутов (Этап 3).")
        self.btn_target.clicked.connect(self._begin_target)
        col.addWidget(self.btn_target)

        self.btn_place = QtWidgets.QPushButton("Расставить датчики")
        self.btn_place.setToolTip(
            "Разместить N датчиков по весам карты (макс. покрытого веса + разнос), "
            "не ставя их на воду.")
        self._tint(self.btn_place, THEME["ok"])
        self.btn_place.clicked.connect(lambda: self.on_place())
        col.addWidget(self.btn_place)

        self.src_label = QtWidgets.QLabel("источник данных: —")
        self.src_label.setObjectName("muted"); self.src_label.setWordWrap(True)
        col.addWidget(self.src_label)

        # режим оптимизации
        col.addWidget(self._header("РЕЖИМ ОПТИМ."))
        self._mode_keys = list(MODE_LABELS)
        self.grp_mode = QtWidgets.QButtonGroup(self)
        for k in self._mode_keys:
            rb = QtWidgets.QRadioButton(MODE_LABELS[k])
            if k == getattr(params, "mode", "balanced"):
                rb.setChecked(True)
            self.grp_mode.addButton(rb)
            col.addWidget(rb)
            rb._key = k
        self.grp_mode.buttonClicked.connect(
            lambda btn: self.on_mode(btn._key))

        # карта-подложка
        self._build_map_combo(col, params)

        # показ слоёв
        col.addWidget(self._header("ПОКАЗ"))
        self.chk_threat = QtWidgets.QCheckBox("весовая карта (тепло)")
        self.chk_threat.setChecked(True)
        self.chk_layers = QtWidgets.QCheckBox("векторные слои (реки/дороги/…)")
        self.chk_layers.setChecked(True)
        self.chk_water = QtWidgets.QCheckBox("исключения (вода/город)")
        self.chk_water.setToolTip("Синим — вода (запрет датчика), красным — населённые "
                                  "пункты (исключены из пролёта и из веса).")
        self.chk_cand = QtWidgets.QCheckBox("кандидатные позиции")
        self.chk_routes = QtWidgets.QCheckBox("маршруты (все места пролёта)")
        self.chk_routes.setToolTip("Розовый ФОН = ВСЕ достижимые места пролёта (огибающая): "
                                   "куда БПЛА может дойти A→место→цель ≤ L_max по коридорам "
                                   "(вес≠0 + мостики < шага БПЛА) — считается ГРАФОМ, точно, "
                                   "включая юг. Поверх — линии-ПРИМЕРЫ путей (их всего "
                                   "экспоненциально много, показываем выборку).")
        self.chk_cross = QtWidgets.QCheckBox("пересечения (перекрёстки)")
        self.chk_cross.setToolTip("Узлы, где сходятся ≥2 разных слоёв (дорога×река=мост, "
                                  "дорога×ЛЭП и т.д.) — точки развилок. Скрыто по умолчанию.")
        self.chk_iter = QtWidgets.QCheckBox("итерационные маршруты (все)")
        self.chk_iter.setToolTip("Показ ВСЕХ сгенерированных итерационных маршрутов БПЛА "
                                 "(накопленная выборка). Генерация — кнопки Пуск/Шаг/Пакетно.")
        self.chk_iter_heat = QtWidgets.QCheckBox("тепловая карта итераций (частота пролёта)")
        self.chk_iter_heat.setToolTip("2-я тепловая карта: как часто маршруты БПЛА проходят "
                                      "над клеткой. По ней и расставляются датчики.")
        self.chk_iter_gen = QtWidgets.QCheckBox("обобщённая выборка (10% маршрутов)")
        self.chk_iter_gen.setToolTip("Показать НЕ весь веер, а ~10 % пройденных итерационных "
                                     "маршрутов: самые ЧАСТЫЕ (по тепловой карте), но "
                                     "РАСПРЕДЕЛЁННО по всей карте, не кучкой. Прошло 150 из "
                                     "500 → покажет 15. Обобщает тепловую карту пролётов.")
        self.chk_legend = QtWidgets.QCheckBox("легенда")
        self.chk_legend.setChecked(True)
        self.chk_legend.setToolTip("Легенда (какой цвет какой объект) — в левом нижнем углу.")
        for chk in (self.chk_threat, self.chk_layers, self.chk_water, self.chk_cand,
                    self.chk_routes, self.chk_cross, self.chk_iter, self.chk_iter_heat,
                    self.chk_iter_gen, self.chk_legend):
            chk.stateChanged.connect(lambda _s: self.on_toggle())
            col.addWidget(chk)
        # режим стохастического выбора развилки (для «итераций»)
        row_it = QtWidgets.QHBoxLayout()
        row_it.addWidget(QtWidgets.QLabel("режим итераций:"))
        self.combo_iter = QtWidgets.QComboBox()
        self._iter_keys = list(THREAT_ITER_MODE_LABELS)
        for k in self._iter_keys:
            self.combo_iter.addItem(THREAT_ITER_MODE_LABELS[k])
        cur = getattr(params, "threat_iter_mode", "mix")
        self.combo_iter.setCurrentIndex(self._iter_keys.index(cur)
                                        if cur in self._iter_keys else 0)
        self.combo_iter.setToolTip("ПРИОРИТЕТ выбора клетки по ВЕСУ сетки: макс. (реки/"
                                   "дороги) / средний / мин. (пустоши, одинокая река) / смесь.")
        self.combo_iter.currentIndexChanged.connect(self._iter_mode_changed)
        row_it.addWidget(self.combo_iter, 1)
        col.addLayout(row_it)
        # 2-й список: РАЗБРОС маршрута по карте (отдельно от приоритета веса)
        row_sp2 = QtWidgets.QHBoxLayout()
        row_sp2.addWidget(QtWidgets.QLabel("разброс по карте:"))
        self.combo_spread = QtWidgets.QComboBox()
        self._spread_keys = list(THREAT_ITER_SPREAD_LABELS)
        for k in self._spread_keys:
            self.combo_spread.addItem(THREAT_ITER_SPREAD_LABELS[k])
        cur = getattr(params, "threat_iter_spread", "mix")
        self.combo_spread.setCurrentIndex(self._spread_keys.index(cur)
                                          if cur in self._spread_keys else 0)
        self.combo_spread.setToolTip("Насколько далеко маршрут уходит от прямой вход→цель: "
                                     "центр (вдоль) / середина / края карты (дальний обход, "
                                     "заход с любой стороны) / смесь. Комбинируется с весом.")
        self.combo_spread.currentIndexChanged.connect(self._spread_changed)
        row_sp2.addWidget(self.combo_spread, 1)
        col.addLayout(row_sp2)
        # 3-й список: РАСХОД ЗАПАСА ХОДА — когда БПЛА виляет, а когда идёт прямо на цель.
        # Действует на ИТЕРАЦИИ. На «возможные маршруты» (до итераций) не влияет: там
        # перебирается веер всех повадок, чтобы область покрывалась целиком.
        row_sp3 = QtWidgets.QHBoxLayout()
        row_sp3.addWidget(QtWidgets.QLabel("расход запаса:"))
        self.combo_spend = QtWidgets.QComboBox()
        self._spend_keys = list(THREAT_SPEND_LABELS)
        for k in self._spend_keys:
            self.combo_spend.addItem(THREAT_SPEND_LABELS[k])
        cur = getattr(params, "threat_spend", "late")
        self.combo_spend.setCurrentIndex(self._spend_keys.index(cur)
                                         if cur in self._spend_keys else 0)
        self.combo_spend.setToolTip(
            "Когда БПЛА тратит СВОБОДНЫЙ ЗАПАС хода (остаток минус кратчайший путь до цели):\n"
            "• виляет, потом прямо — пока запас есть, идёт по коридорам; на исходе "
            "выпрямляется и идёт к цели;\n"
            "• прямо, потом виляет — сначала кратчайшим путём, запас тратит ближе к цели;\n"
            "• равномерно — тратит запас понемногу на всём пути.\n"
            "Действует на ИТЕРАЦИИ (возможные маршруты строятся веером всех повадок).")
        self.combo_spend.currentIndexChanged.connect(self._spend_changed)
        row_sp3.addWidget(self.combo_spend, 1)
        col.addLayout(row_sp3)
        # число итераций T (как во вкладке 2) + доля обобщённой выборки, %
        row_t = QtWidgets.QHBoxLayout()
        row_t.addWidget(QtWidgets.QLabel("число итераций:"))
        self.ed_iter_T = QtWidgets.QLineEdit(str(getattr(params, "threat_iter_routes", 150)))
        self.ed_iter_T.setToolTip("Сколько маршрутов сгенерировать (аналог T во вкладке 2). "
                                  "«Пуск» строит их по одному с анимацией полёта БПЛА.")
        self.ed_iter_T.returnPressed.connect(lambda: self.on_iter_batch())
        row_t.addWidget(self.ed_iter_T, 1)
        row_t.addWidget(QtWidgets.QLabel("обобщ. %:"))
        self.ed_iter_gen_frac = QtWidgets.QLineEdit(
            f"{getattr(params, 'threat_iter_gen_frac', 0.10) * 100:g}")
        self.ed_iter_gen_frac.setFixedWidth(56)
        self.ed_iter_gen_frac.setToolTip("Доля «обобщённой выборки», % от ПРОЙДЕННЫХ итераций "
                                         "(по умолчанию 10%). Прошло 500, доля 10% → 50 "
                                         "маршрутов; доля 20% → 100. Enter — применить.")
        self.ed_iter_gen_frac.returnPressed.connect(self._gen_frac_changed)
        row_t.addWidget(self.ed_iter_gen_frac)
        col.addLayout(row_t)
        # скорость анимации полёта
        row_sp = QtWidgets.QHBoxLayout()
        row_sp.addWidget(QtWidgets.QLabel("скорость:"))
        self.slider_speed = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider_speed.setMinimum(1); self.slider_speed.setMaximum(20)
        self.slider_speed.setValue(6)
        self.slider_speed.valueChanged.connect(lambda v: self.on_iter_speed(int(v)))
        row_sp.addWidget(self.slider_speed, 1)
        col.addLayout(row_sp)
        # Пуск / Шаг / Пакетно — как во вкладке 2
        row_run = QtWidgets.QHBoxLayout()
        self.btn_iter_play = QtWidgets.QPushButton("▶ Пуск")
        self.btn_iter_step = QtWidgets.QPushButton("Шаг")
        self.btn_iter_batch = QtWidgets.QPushButton("Пакетно")
        self._tint(self.btn_iter_play, THEME["ok"])
        self._tint(self.btn_iter_batch, THEME["accent2"])
        self.btn_iter_play.setToolTip("Анимация: итерация за итерацией строит маршрут и "
                                      "показывает движение БПЛА по нему (Пуск/Пауза).")
        self.btn_iter_step.setToolTip("Одна итерация — добавить один маршрут.")
        self.btn_iter_batch.setToolTip("Сразу все T итераций (без анимации) — весь набор мест пролёта.")
        self.btn_iter_play.clicked.connect(lambda: self.on_iter_play())
        self.btn_iter_step.clicked.connect(lambda: self.on_iter_step())
        self.btn_iter_batch.clicked.connect(lambda: self.on_iter_batch())
        row_run.addWidget(self.btn_iter_play); row_run.addWidget(self.btn_iter_step)
        row_run.addWidget(self.btn_iter_batch)
        col.addLayout(row_run)

        b1 = QtWidgets.QHBoxLayout()
        self.btn_apply = QtWidgets.QPushButton("Применить")
        self.btn_apply.setToolTip("Применить параметры (N/R/k/шаг) и пересчитать.")
        self.btn_reset = QtWidgets.QPushButton("Сброс")
        self.btn_reset.setToolTip("Убрать датчики и заданную цель (карта остаётся).")
        self.btn_view = QtWidgets.QPushButton("Весь участок")
        self.btn_view.setToolTip("Вернуть камеру к полному участку (bbox).")
        self._tint(self.btn_apply, THEME["accent2"])
        self.btn_apply.clicked.connect(lambda: self.on_apply())
        self.btn_reset.clicked.connect(lambda: self.on_reset())
        self.btn_view.clicked.connect(lambda: self.on_reset_view())
        b1.addWidget(self.btn_apply); b1.addWidget(self.btn_reset)
        b1.addWidget(self.btn_view)
        col.addLayout(b1)

        col.addWidget(self._header("ПОКАЗАТЕЛИ"))
        self.metrics = QtWidgets.QPlainTextEdit(); self.metrics.setReadOnly(True)
        self.metrics.setMinimumHeight(200)
        col.addWidget(self.metrics, stretch=1)

    def _build_map_combo(self, col, params):
        col.addWidget(self._header("КАРТА"))
        self.combo_map = QtWidgets.QComboBox()
        labels = dict(gm.LAYER_LABELS)
        self._map_keys = list(labels)
        for name in gm.discover_local_layers():
            labels[name] = f"{name} (локальный кэш)"
            self._map_keys.append(name)
        for k in self._map_keys:
            self.combo_map.addItem(labels[k])
        cur = getattr(params, "map_layer3", "osm")
        self.combo_map.setCurrentIndex(self._map_keys.index(cur)
                                       if cur in self._map_keys else 0)
        self.combo_map.currentIndexChanged.connect(self._map_changed)
        col.addWidget(self.combo_map)
        self.chk_offline = QtWidgets.QCheckBox("офлайн (только кэш, без сети)")
        self.chk_offline.setChecked(bool(getattr(params, "map_offline3", False)))
        self.chk_offline.stateChanged.connect(self._offline_changed)
        col.addWidget(self.chk_offline)

    def _map_changed(self, i):
        self._map_layer = self._map_keys[i]
        self.on_map_layer(self._map_layer)
        self._refresh_basemap(force=True)

    def _offline_changed(self, _s):
        self._map_offline = self.chk_offline.isChecked()
        self.on_map_offline(self._map_offline)
        self._refresh_basemap(force=True)

    def _iter_mode_changed(self, i):
        self.on_iter_mode(self._iter_keys[i])

    def _gen_frac_changed(self):
        self.on_iter_gen_frac(self.get_iter_gen_frac())

    def get_iter_gen_frac(self):
        """Доля обобщённой выборки как число 0..1 (поле задаётся в процентах). При ошибке —
        текущее значение из параметров. Ограничено диапазоном [1%, 100%]."""
        try:
            v = float(self.ed_iter_gen_frac.text().strip().replace(",", ".").rstrip("% "))
        except (ValueError, AttributeError):
            return float(getattr(self._params_ref, "threat_iter_gen_frac", 0.10))
        return min(1.0, max(0.01, v / 100.0))

    def _spread_changed(self, i):
        self.on_iter_spread(self._spread_keys[i])

    def _spend_changed(self, i):
        self.on_iter_spend(self._spend_keys[i])

    def _on_field_submit(self):
        if not self._suppress:
            self.on_apply()

    # ==================================================================
    def _build_scene_items(self):
        """Создать ВСЕ постоянные слои-элементы карты один раз (потом только меняем данные,
        а не пересоздаём). Порядок наложения — через setZValue: подложка/тепло — отрицательные,
        маршруты — 1..6, датчики и точки A/B — 20..22 (поверх всего). Цвета — из THREAT_COLORS."""
        # весовая карта (тепловой слой)
        self.threat_img = pg.ImageItem(); self.threat_img.setOpts(axisOrder="row-major")
        self.threat_img.setZValue(-8); self.threat_img.setOpacity(0.55)
        self.threat_img.setVisible(False)
        self.pi.addItem(self.threat_img)
        self.cbar = pg.ColorBarItem(
            values=(0, 1), colorMap=_threat_cmap(),
            label="вес ячейки (низкий → высокий)", interactive=False)
        try:
            self.cbar.setImageItem(self.threat_img, insert_in=self.pi)
        except Exception:
            pass
        self.cbar.setVisible(False)

        # запрет воды (маска)
        self.water_img = pg.ImageItem(); self.water_img.setOpts(axisOrder="row-major")
        self.water_img.setZValue(-7); self.water_img.setOpacity(0.5)
        self.water_img.setVisible(False)
        self.pi.addItem(self.water_img)

        # застройка растром — на ОБЩЕМ виде вместо 8275 контуров (они там всё равно
        # сливаются в пятно, а рисуются долго); при приближении включаются контуры
        self.builtup_img = pg.ImageItem(); self.builtup_img.setOpts(axisOrder="row-major")
        self.builtup_img.setZValue(-5.5); self.builtup_img.setOpacity(0.55)
        self.builtup_img.setVisible(False)
        self.pi.addItem(self.builtup_img)

        # векторные слои. antialias=False + connect="finite" — на реальных данных
        # (сотни тысяч точек) сглаживание линий делает панораму медленной; без него
        # быстро. Данные ещё и ПРОРЕЖИВАЮТСЯ при отрисовке (см. _polys_to_xy).
        for name in THREAT_LAYER_ORDER:
            if name not in LAYER_STYLE:
                continue
            st = LAYER_STYLE[name]
            pen = pg.mkPen(_qcolor(st["color"], 235), width=st["width"],
                           dash=st["dash"])
            item = self.pi.plot([], [], pen=pen, antialias=False, connect="finite")
            item.setZValue(-5)
            self._layer_items[name] = item
        # мосты — точечные маркеры (число ограничено, см. render_layers)
        self.bridge_scatter = pg.ScatterPlotItem(
            size=11, symbol="t", brush=pg.mkBrush(_qcolor(THEME["warn"])),
            pen=pg.mkPen("white", width=0.8))
        self.bridge_scatter.setZValue(-3); self.pi.addItem(self.bridge_scatter)

        # ВСЕ возможные места пролёта (envelope) — полупрозрачный слой ячеек
        self.route_area_img = pg.ImageItem(); self.route_area_img.setOpts(axisOrder="row-major")
        self.route_area_img.setZValue(-6); self.route_area_img.setVisible(False)
        self.pi.addItem(self.route_area_img)
        # линии-примеры маршрутов поверх фона-огибающей: бирюзовый, но ПОЛУПРОЗРАЧНЫЙ —
        # чтобы не перекрывать датчики и не сливаться в сплошную заливку.
        self.route_item = self.pi.plot([], [], antialias=True, connect="finite",
                                       pen=pg.mkPen(_qcolor(THREAT_COLORS["route_all"], 100), width=1.6))
        self.route_item.setZValue(3)
        # 2-я тепловая карта — частота пролёта БПЛА (плотность итерационных маршрутов)
        self.iter_heat_img = pg.ImageItem(); self.iter_heat_img.setOpts(axisOrder="row-major")
        self.iter_heat_img.setZValue(-7); self.iter_heat_img.setOpacity(0.62)
        self.iter_heat_img.setVisible(False)
        self.pi.addItem(self.iter_heat_img)
        # ИТЕРАЦИИ: накопленные маршруты (тонкие) + текущий (ярче) + маркер БПЛА.
        # Анимацию ведёт контроллер (iter_setup_flight/iter_update_flight) — как вкладка 2.
        # накопленные итерационные маршруты («выборка») — КРАСНЫЕ и полупрозрачные (менее плотно)
        self.route_iter_item = self.pi.plot([], [], antialias=True, connect="finite",
                                            pen=pg.mkPen(_qcolor(THREAT_COLORS["route_iter"], 80), width=1.4))
        self.route_iter_item.setZValue(1)
        # ОБОБЩЁННАЯ выборка (~10 % итераций) — тоже КРАСНАЯ, но плотнее накопленных (её видно).
        self.route_gen_item = self.pi.plot([], [], antialias=True, connect="finite",
                                           pen=pg.mkPen(_qcolor(THREAT_COLORS["route_gen"], 160), width=2.2))
        self.route_gen_item.setZValue(4); self.route_gen_item.setVisible(False)
        # ОСНОВНОЙ (текущий) маршрут итерации — ЧЁРНЫЙ, чётко виден на светлой карте
        self.route_cur_item = self.pi.plot([], [], antialias=True, connect="finite",
                                           pen=pg.mkPen(_qcolor(THREAT_COLORS["route_main"], 255), width=3.2))
        self.route_cur_item.setZValue(5); self.route_cur_item.setVisible(False)
        self.uav_marker = pg.ScatterPlotItem(
            size=14, symbol="t1", brush=pg.mkBrush(_qcolor(THREAT_COLORS["uav"])),
            pen=pg.mkPen("white", width=1.3))
        self.uav_marker.setZValue(6); self.uav_marker.setVisible(False)
        self.pi.addItem(self.uav_marker)
        # пересечения (перекрёстки дорог/рек/ЛЭП) — узлы развилок, скрыто по умолчанию
        self.crossing_scatter = pg.ScatterPlotItem(
            size=7, symbol="d", brush=pg.mkBrush(_qcolor(THREAT_COLORS["crossing"], 220)),
            pen=pg.mkPen(THREAT_COLORS["crossing_edge"], width=0.6))
        self.crossing_scatter.setZValue(-2); self.pi.addItem(self.crossing_scatter)

        # легенда векторных слоёв (цвет -> объект) — что чем отображается.
        # anchor (0,1) — точка привязки = НИЖНИЙ-левый угол текста (легенда в левом
        # нижнем углу вида); включается отдельным чекбоксом «легенда».
        self.legend = pg.TextItem(anchor=(0, 1), fill=pg.mkBrush(THREAT_COLORS["legend_bg"]))
        self.legend.setZValue(20); self.legend.setHtml(self._legend_html())
        self.pi.addItem(self.legend); self.legend.setVisible(False)

        # рамка bbox
        self.bbox_item = self.pi.plot([], [], pen=pg.mkPen(_qcolor(THEME["accent"], 180),
                                                           width=1.6, dash=[8, 5]))
        self.bbox_item.setZValue(-4)
        kx0, kx1, ky0, ky1 = self.bbox_km
        self.bbox_item.setData([kx0, kx1, kx1, kx0, kx0], [ky0, ky0, ky1, ky1, ky0])

        # кандидатные позиции
        self.cand_scatter = pg.ScatterPlotItem(
            size=4, symbol="o", brush=pg.mkBrush(_qcolor(THEME["muted"], 120)),
            pen=None)
        self.cand_scatter.setZValue(-2); self.pi.addItem(self.cand_scatter)

        # вход/цель — крупные контрастные значки (A — зелёная звезда, B — красный крест)
        self.entry_scatter = pg.ScatterPlotItem(size=26, symbol="star",
            brush=pg.mkBrush(_qcolor(THEME["ok"])), pen=pg.mkPen("white", width=2.2))
        self.entry_scatter.setZValue(22); self.pi.addItem(self.entry_scatter)   # A/B — поверх датчиков
        self.target_scatter = pg.ScatterPlotItem(size=24, symbol="x",
            brush=pg.mkBrush(_qcolor(THEME["warn"])), pen=pg.mkPen("white", width=2.6))
        self.target_scatter.setZValue(22); self.pi.addItem(self.target_scatter)

    # ==================================================================
    # API для контроллера
    # ==================================================================
    def get_toggles(self):
        return dict(show_threat=self.chk_threat.isChecked(),
                    show_layers=self.chk_layers.isChecked(),
                    show_water=self.chk_water.isChecked(),
                    show_cand=self.chk_cand.isChecked(),
                    show_routes=self.chk_routes.isChecked(),
                    show_cross=self.chk_cross.isChecked(),
                    show_iter=self.chk_iter.isChecked(),
                    show_iter_heat=self.chk_iter_heat.isChecked(),
                    show_iter_gen=self.chk_iter_gen.isChecked(),
                    iter_mode=self._iter_keys[self.combo_iter.currentIndex()],
                    show_legend=self.chk_legend.isChecked())

    def get_param_values(self):
        out = {}
        for name, _l, typ in self.THREAT_PARAM_SPECS:
            raw = self._fields[name].text().strip().replace(",", ".")
            out[name] = typ(float(raw)) if typ is int else typ(raw)
        return out

    def set_param_values(self, params):
        self._suppress = True
        try:
            for name, _l, _t in self.THREAT_PARAM_SPECS:
                self._fields[name].setText(str(getattr(params, name)))
        finally:
            self._suppress = False

    def set_busy(self, busy):
        """Заблокировать кнопки действий на время фонового расчёта (чтобы не запускать
        второй параллельно) и показать курсор ожидания."""
        for b in (self.btn_data, self.btn_build, self.btn_target, self.btn_place,
                  self.btn_apply, self.btn_reset):
            b.setEnabled(not busy)
        self.setCursor(QtCore.Qt.WaitCursor if busy else QtCore.Qt.ArrowCursor)

    def set_source(self, text):
        self.src_label.setText(f"источник данных: {text}")

    def set_metrics(self, lines):
        self.metrics.setPlainText("\n".join(lines))

    def flash_title(self, text, color=None):
        self.pi.setTitle(text, color=color or THEME["warn"], size="10pt")

    def set_title(self, text):
        self.pi.setTitle(text, color=THEME["text"], size="10pt")

    def frame_bbox(self):
        """Показать ВЕСЬ участок целиком (кнопка «Весь участок»). Небольшой отступ, чтобы
        рамка bbox не липла к краям; при широком экране участок помещается полностью
        (пределы вида это теперь допускают, см. _set_view_limits)."""
        kx0, kx1, ky0, ky1 = self.bbox_km
        self.pi.setRange(xRange=(kx0, kx1), yRange=(ky0, ky1), padding=0.06)

    def process_pending(self):
        QtWidgets.QApplication.processEvents()

    # ---- отрисовка данных карты ----
    MAX_LAYER_PTS = 48000     # предел точек на слой при отрисовке (прореживание)
    MAX_LAYER_POLYS = 10000   # предел числа линий на слой (берём самые длинные)
    MAX_BRIDGES = 150         # предел маркеров мостов (чтобы не засорять карту)
    BUILTUP_RASTER_FRAC = 0.35  # кадр уже этой доли участка -> застройка контурами

    @classmethod
    def _polys_to_xy(cls, polys, view_box=None):
        """Список ломаных -> массивы X,Y с NaN-разрывами, с ПРОРЕЖИВАНИЕМ (иначе
        сотни тысяч точек и тысячи мелких линий тормозят отрисовку при панораме):
        оставляем не более MAX_LAYER_POLYS самых длинных линий и прореживаем точки до
        ~MAX_LAYER_PTS. Концы линий сохраняются. Полностью на numpy.

        «Длинные» — по ГЕОГРАФИЧЕСКОЙ длине (км), не по числу точек: у порезанных
        сегментов число точек одинаковое (2), и сортировка по len() оставляла первые
        попавшиеся — дороги рисовались только вокруг одного города.

        view_box (x0,x1,y0,y1) — ВИДИМАЯ область: линии вне кадра отбрасываются ДО
        прореживания, поэтому при приближении лимиты тратятся только на то, что видно,
        и объект рисуется целиком. При отдалении в кадр попадает всё — прореживание
        работает как раньше."""
        arrs = [np.asarray(p, float) for p in polys
                if np.ndim(p) == 2 and len(p) >= 2]
        if not arrs:
            return np.empty(0), np.empty(0)
        if view_box is not None:
            vx0, vx1, vy0, vy1 = view_box
            vis = []
            for p in arrs:                                 # habbox линии пересекает кадр?
                if (p[:, 0].max() >= vx0 and p[:, 0].min() <= vx1 and
                        p[:, 1].max() >= vy0 and p[:, 1].min() <= vy1):
                    vis.append(p)
            arrs = vis or arrs                             # пусто — показать хоть что-то
        if len(arrs) > cls.MAX_LAYER_POLYS:                # самые длинные (значимые) линии
            km = [float(np.hypot(*(np.diff(p, axis=0).T)).sum()) for p in arrs]
            order = np.argsort(km)[::-1][:cls.MAX_LAYER_POLYS]
            arrs = [arrs[i] for i in order]
        total = sum(len(p) for p in arrs)
        step = max(1, int(np.ceil(total / cls.MAX_LAYER_PTS)))
        nan = np.array([np.nan])
        xs, ys = [], []
        for p in arrs:
            if step > 1 and len(p) > 4:
                idx = np.arange(0, len(p), step)
                if len(idx) < 4:                           # мелкий полигон (квартал города)
                    idx = np.linspace(0, len(p) - 1, 4).astype(int)   # не схлопывать
                elif idx[-1] != len(p) - 1:
                    idx = np.append(idx, len(p) - 1)       # сохранить конец линии
                p = p[idx]
            xs.append(p[:, 0]); xs.append(nan)
            ys.append(p[:, 1]); ys.append(nan)
        return np.concatenate(xs), np.concatenate(ys)

    def _view_box_km(self):
        """Видимая область в км (x0,x1,y0,y1) — по ней прореживаются слои."""
        try:
            (x0, x1), (y0, y1) = self.vb.viewRange()
            return (float(x0), float(x1), float(y0), float(y1))
        except Exception:
            return None

    def _zoomed_in(self, view_box):
        """Приблизились ли настолько, что застройку пора рисовать НАСТОЯЩИМИ контурами
        (а не растровой маской). Порог — ширина кадра меньше доли всего участка."""
        if view_box is None:
            return True
        kx0, kx1, _, _ = self.bbox_km
        span = max(1e-6, kx1 - kx0)
        return (view_box[1] - view_box[0]) <= span * self.BUILTUP_RASTER_FRAC

    def render_layers(self, layers, bridge_pts, toggles, built_mask=None,
                      extent=None):
        """Отрисовать векторные слои. Чекбокс «векторные слои» ПОЛНОСТЬЮ скрывает их
        (данные очищаются -> нулевая стоимость отрисовки).

        Прореживание идёт по ВИДИМОЙ области: при приближении лимиты тратятся только на
        то, что в кадре, поэтому объекты рисуются целиком. Застройка на общем виде
        показывается растровой маской (8275 контуров всё равно сливаются в пятно и
        только тормозят), а при приближении переключается на настоящие контуры."""
        show = toggles["show_layers"]
        vb = self._view_box_km() if show else None
        raster_built = (show and built_mask is not None and extent is not None
                        and not self._zoomed_in(vb))
        for name, item in self._layer_items.items():
            if show and not (name == "built_up" and raster_built):
                xs, ys = self._polys_to_xy(layers.get(name, []), vb)
                item.setData(xs, ys, antialias=False, connect="finite")
            else:
                item.setData([], [])                     # полностью убрать нагрузку
            item.setVisible(show and not (name == "built_up" and raster_built))
        self._render_builtup_raster(built_mask if raster_built else None, extent)
        if show and len(bridge_pts):
            bp = np.asarray(bridge_pts, float)
            if len(bp) > self.MAX_BRIDGES:               # не заваливать карту маркерами
                idx = np.linspace(0, len(bp) - 1, self.MAX_BRIDGES).astype(int)
                bp = bp[idx]
            self.bridge_scatter.setData(bp[:, 0], bp[:, 1])
            self.bridge_scatter.setVisible(True)
        else:
            self.bridge_scatter.setData([], [])
            self.bridge_scatter.setVisible(False)
        # легенда — в левом НИЖНЕМ углу вида, по своему чекбоксу (независимо от слоёв)
        show_legend = toggles.get("show_legend", False)
        if show_legend:
            try:
                (x0, x1), (y0, y1) = self.vb.viewRange()
                self.legend.setPos(x0 + (x1 - x0) * 0.005, y0 + (y1 - y0) * 0.02)
            except Exception:
                pass
        self.legend.setVisible(show_legend)

    def _render_builtup_raster(self, mask, extent):
        """Застройка растровой маской (общий вид). None — скрыть слой."""
        if mask is None or extent is None:
            self.builtup_img.setVisible(False)
            return
        m = np.asarray(mask, bool)
        rgba = np.zeros((m.shape[0], m.shape[1], 4), np.ubyte)
        col = _qcolor(LAYER_STYLE["built_up"]["color"])
        rgba[m, 0] = col.red(); rgba[m, 1] = col.green(); rgba[m, 2] = col.blue()
        rgba[m, 3] = 255
        self.builtup_img.setImage(rgba, autoLevels=False)
        x0, x1, y0, y1 = extent
        self.builtup_img.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
        self.builtup_img.setVisible(True)

    def render_threat(self, weight, extent, toggles):
        if not toggles["show_threat"] or weight is None:
            self.threat_img.setVisible(False); self.cbar.setVisible(False); return
        vmax = float(np.percentile(weight[weight > 0], 97)) if np.any(weight > 0) else 1.0
        vmax = max(vmax, 1e-6)
        disp = np.clip(weight / vmax, 0.0, 1.0)
        self.threat_img.setImage(disp, levels=(0, 1), lut=self._lut, autoLevels=False)
        x0, x1, y0, y1 = extent
        self.threat_img.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
        self.threat_img.setVisible(True); self.cbar.setVisible(True)

    def render_exclusions(self, water_mask, urban_mask, extent, toggles):
        """Показать зоны-исключения: вода (синий — запрет датчика) и населённые пункты
        (красный — исключены из пролёта и обнулены в весе)."""
        if not toggles["show_water"] or (water_mask is None and urban_mask is None):
            self.water_img.setVisible(False); return
        shape = (water_mask if water_mask is not None else urban_mask).shape
        rgba = np.zeros(shape + (4,), np.ubyte)
        if urban_mask is not None:
            rgba[urban_mask] = THREAT_URBAN_RGBA       # красный — город (исключён), палитра
        if water_mask is not None:
            rgba[water_mask] = THREAT_WATER_RGBA       # синий — вода (запрет датчика), палитра
        self.water_img.setImage(rgba, autoLevels=False)
        x0, x1, y0, y1 = extent
        self.water_img.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
        self.water_img.setVisible(True)

    def render_routes(self, routes, route_area, extent, toggles):
        """ВСЕ возможные места пролёта: розовый ФОН = огибающая (route_area) — КОРРЕКТНО
        посчитанная ГРАФОМ достижимость A→ячейка→цель ≤ L_max (все места, куда может дойти
        БПЛА по коридорам, включая юг). Поверх — розовые ЛИНИИ-примеры маршрутов."""
        show = toggles["show_routes"]
        if show and route_area is not None and np.asarray(route_area).any():
            ra = np.asarray(route_area)
            rgba = np.zeros(ra.shape + (4,), np.ubyte)
            rgba[ra] = THREAT_COLORS["envelope"]           # розовый фон — все достижимые места (палитра)
            self.route_area_img.setImage(rgba, autoLevels=False)
            x0, x1, y0, y1 = extent
            self.route_area_img.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
            self.route_area_img.setVisible(True)
        else:
            self.route_area_img.setVisible(False)
        # линии-примеры маршрутов поверх фона
        if show and routes:
            nan = np.array([np.nan])
            xs, ys = [], []
            for r in routes:
                r = np.asarray(r, float)
                xs.append(r[:, 0]); xs.append(nan)
                ys.append(r[:, 1]); ys.append(nan)
            self.route_item.setData(np.concatenate(xs), np.concatenate(ys), connect="finite")
            self.route_item.setVisible(True)
        else:
            self.route_item.setData([], []); self.route_item.setVisible(False)

    def render_iter_routes(self, routes, toggles):
        """Показать НАКОПЛЕННЫЕ итерационные маршруты (статично). Пошаговую анимацию полёта
        ведёт контроллер через iter_setup_flight/iter_update_flight."""
        show = bool(toggles.get("show_iter")) and bool(routes)
        self.iter_show_accumulated(routes if show else [])

    def render_generalized(self, routes, toggles):
        """ОБОБЩЁННАЯ выборка (~10 % итераций): частые, но распределённые маршруты —
        отдельный ярко-лаймовый слой поверх веера. routes уже отобраны моделью."""
        show = bool(toggles.get("show_iter_gen")) and bool(routes)
        if not show:
            self.route_gen_item.setData([], []); self.route_gen_item.setVisible(False)
            return
        nan = np.array([np.nan]); xs, ys = [], []
        for r in routes:
            r = np.asarray(r, float)
            xs.append(r[:, 0]); xs.append(nan)
            ys.append(r[:, 1]); ys.append(nan)
        self.route_gen_item.setData(np.concatenate(xs), np.concatenate(ys), connect="finite")
        self.route_gen_item.setVisible(True)

    def render_iter_heat(self, density, extent, toggles):
        """2-я тепловая карта — частота пролёта БПЛА (плотность маршрутов), LUT magma."""
        if not toggles.get("show_iter_heat") or density is None:
            self.iter_heat_img.setVisible(False); return
        d = np.asarray(density, float)
        ds = np.sqrt(np.clip(d, 0.0, 1.0))              # √ поднимает редкие пролёты (виднее)
        lut = _iter_heat_lut()
        rgba = lut[np.clip((ds * 255).astype(int), 0, 255)].copy()
        rgba[..., 3] = np.where(d > 0.005, 220, 0).astype(np.ubyte)  # прозрачно, где не летали
        self.iter_heat_img.setImage(rgba, autoLevels=False)
        x0, x1, y0, y1 = extent
        self.iter_heat_img.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
        self.iter_heat_img.setVisible(True)

    def iter_show_accumulated(self, routes):
        """Отрисовать все накопленные маршруты тонкими линиями (одним item, через NaN)."""
        if not routes:
            self.route_iter_item.setData([], []); self.route_iter_item.setVisible(False)
            return
        nan = np.array([np.nan]); xs, ys = [], []
        for r in routes:
            r = np.asarray(r, float)
            xs.append(r[:, 0]); xs.append(nan)
            ys.append(r[:, 1]); ys.append(nan)
        self.route_iter_item.setData(np.concatenate(xs), np.concatenate(ys), connect="finite")
        self.route_iter_item.setVisible(True)

    def iter_setup_flight(self, route):
        """Начать анимацию полёта по маршруту: текущий путь и маркер БПЛА — в старте."""
        r = np.asarray(route, float)
        self.route_cur_item.setData([r[0, 0]], [r[0, 1]]); self.route_cur_item.setVisible(True)
        self.uav_marker.setData([r[0, 0]], [r[0, 1]]); self.uav_marker.setVisible(True)

    def iter_update_flight(self, route, j):
        """Прорисовать текущий маршрут до точки j и передвинуть маркер БПЛА в неё."""
        r = np.asarray(route, float); j = int(max(0, min(j, len(r) - 1)))
        self.route_cur_item.setData(r[:j + 1, 0], r[:j + 1, 1])
        self.uav_marker.setData([r[j, 0]], [r[j, 1]])

    def iter_clear_current(self):
        """Убрать текущий (летящий) маршрут и маркер БПЛА (накопленные остаются)."""
        self.route_cur_item.setData([], []); self.route_cur_item.setVisible(False)
        self.uav_marker.setData([], []); self.uav_marker.setVisible(False)

    def get_iter_T(self):
        """Число итераций (T) из поля ввода; при ошибке — прежнее из params."""
        try:
            return max(1, int(float(self.ed_iter_T.text().strip().replace(",", "."))))
        except (ValueError, AttributeError):
            return int(getattr(self._params_ref, "threat_iter_routes", 150))

    def set_iter_running(self, running):
        """Текст кнопки «Пуск»/«Пауза» (как во вкладке 2)."""
        self.btn_iter_play.setText("⏸ Пауза" if running else "▶ Пуск")

    def render_crossings(self, crossings, toggles):
        """Пересечения (перекрёстки дорог/рек/ЛЭП) — узлы развилок. Скрыто по умолчанию."""
        if not toggles.get("show_cross") or crossings is None or len(crossings) == 0:
            self.crossing_scatter.setVisible(False); return
        c = np.asarray(crossings, float)
        self.crossing_scatter.setData(c[:, 0], c[:, 1])
        self.crossing_scatter.setVisible(True)

    def render_candidates(self, cand, toggles):
        if not toggles["show_cand"] or cand is None or len(cand) == 0:
            self.cand_scatter.setVisible(False); return
        self.cand_scatter.setData(cand[:, 0], cand[:, 1])
        self.cand_scatter.setVisible(True)

    SENSOR_COLOR = THREAT_COLORS["sensor"]   # цвет датчиков — см. палитру THREAT_COLORS

    def render_sensors(self, sensors, R):
        """Нарисовать датчики: у каждого — КОЛЬЦО зоны обзора радиуса R (залитый круг) и
        яркая точка-центр. Старые убираем и создаём заново (число меняется). Стоят поверх
        всех слоёв (Z=20/21), чтобы маршруты их не перекрывали. Цвет — THREAT_COLORS['sensor']."""
        for it in self._sensor_items:
            self.pi.removeItem(it)
        self._sensor_items.clear()
        for i, s in enumerate(sensors, 1):
            e = QtWidgets.QGraphicsEllipseItem(s[0] - R, s[1] - R, 2 * R, 2 * R)
            e.setPen(pg.mkPen(_qcolor(self.SENSOR_COLOR, 245), width=2.2))
            e.setBrush(pg.mkBrush(_qcolor(self.SENSOR_COLOR, 70)))   # залитая зона обзора
            e.setZValue(20)                                          # НАД всем: маршруты не перекрывают
            self.pi.addItem(e, ignoreBounds=True)
            self._sensor_items.append(e)
        if len(sensors):
            dots = pg.ScatterPlotItem(
                [p[0] for p in sensors], [p[1] for p in sensors], size=15, symbol="o",
                brush=pg.mkBrush(_qcolor(self.SENSOR_COLOR)), pen=pg.mkPen("white", width=1.6))
            dots.setZValue(21); self.pi.addItem(dots)               # центры — поверх колец
            self._sensor_items.append(dots)

    def render_entry_target(self, entry, target):
        self.entry_scatter.setData([entry[0]], [entry[1]])
        self.target_scatter.setData([target[0]], [target[1]])

    def showEvent(self, e):
        QtWidgets.QWidget.showEvent(self, e)
        if not self._framed:
            self.frame_bbox()
            self._framed = True
        self._refresh_basemap(force=True)


class DigitalMapsDialog(QtWidgets.QDialog):
    """Отдельное окно: выбор ИСТОЧНИКА цифровых карт и набора накладываемых СЛОЁВ.

    Источник: файл .npz (готовые слои) / .osm.pbf (сырой OSM-экстракт) либо «авто»
    (кэш geo_cache/ → иначе демо-схема). Слои: чекбоксы по THREAT_LAYERS с их весами.
    Возвращает выбор через result_choices(); саму загрузку делает контроллер."""

    def __init__(self, parent, data_path, enabled_layers):
        super().__init__(parent)
        self.setWindowTitle("Выбор цифровых карт (слоёв) для наложения")
        self.setMinimumWidth(440)
        self._data_path = data_path
        lay = QtWidgets.QVBoxLayout(self)

        lay.addWidget(QtWidgets.QLabel("<b>1. Источник данных</b>"))
        self.src_lbl = QtWidgets.QLabel(self._src_text())
        self.src_lbl.setWordWrap(True)
        self.src_lbl.setStyleSheet(f"color: {THEME['muted']};")
        lay.addWidget(self.src_lbl)
        row = QtWidgets.QHBoxLayout()
        btn_file = QtWidgets.QPushButton("Загрузить файл (.npz / .osm.pbf)…")
        btn_file.clicked.connect(self._pick_file)
        btn_auto = QtWidgets.QPushButton("Авто (кэш → демо)")
        btn_auto.clicked.connect(self._use_auto)
        row.addWidget(btn_file); row.addWidget(btn_auto)
        lay.addLayout(row)
        hint = QtWidgets.QLabel(
            "Где взять .osm.pbf — см. теория/МЕТОДИЧКА_ЗАГРУЗКА_КАРТ.md "
            "(BBBike/Geofabrik). .npz готовит tools/build_threat_grid.py.")
        hint.setWordWrap(True); hint.setStyleSheet(f"color: {THEME['muted']};")
        lay.addWidget(hint)

        lay.addWidget(QtWidgets.QLabel("<b>2. Слои для наложения</b> (вес ячейки)"))
        self._checks = {}
        for name in THREAT_LAYER_ORDER:
            spec = THREAT_LAYERS.get(name)
            if not spec:
                continue
            role = "репеллер" if not spec.get("attractor", True) else "аттрактор"
            chk = QtWidgets.QCheckBox(f"{spec['label']}  ·  вес {spec['weight']:+g}  ·  {role}")
            chk.setChecked(enabled_layers is None or name in enabled_layers)
            self._checks[name] = chk
            lay.addWidget(chk)

        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _src_text(self):
        return (f"файл: {self._data_path}" if self._data_path
                else "авто: geo_cache/threat_layers.npz → иначе демо-схема")

    def _pick_file(self):
        fn, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Файл цифровых карт", "",
            "Гео-данные (*.npz *.pbf *.osm);;Все файлы (*)")
        if fn:
            self._data_path = fn
            self.src_lbl.setText(self._src_text())

    def _use_auto(self):
        self._data_path = None
        self.src_lbl.setText(self._src_text())

    def result_choices(self):
        """(data_path|None, enabled_layers_set). Все галочки сняты -> пустой набор
        (карта без слоёв); чтобы «все слои» — просто отметить все."""
        enabled = {n for n, c in self._checks.items() if c.isChecked()}
        return self._data_path, enabled


class InputDataDialog(QtWidgets.QDialog):
    """НЕмодальное окно «Входные данные» маршрута: запас хода, скорость, крен, длина прямого
    участка, РАЗРЫВ между весовыми секторами. |AB| считается между входом и целью (только
    показ). Enter в любом поле СРАЗУ применяет к основной карте (не блокирует программу —
    окно остаётся открытым).
    """
    FIELDS = [
        ("threat_L_max", "Запас хода L_max, км"),
        ("threat_speed_kmh", "Скорость, км/ч"),
        ("threat_bank_deg", "Крен, °"),
        ("threat_turn_interval_km", "Длина прямого участка, км"),
        ("threat_max_gap_km", "Разрыв между секторами, км"),
    ]
    # Подсказки к полям (иначе смысл «разрыва» неочевиден).
    HINTS = {
        "threat_max_gap_km":
            "Пустое место между весовыми секторами: если оно КОРОЧЕ этого значения — БПЛА "
            "его перелетит, и коридоры сшиваются в один (маршрут возможен). Длиннее — "
            "коридор разорван, маршрута через него нет.\nМеняет и ОБЛАСТЬ ЗАЛЁТА, и набор "
            "возможных маршрутов — ещё до итераций.",
        "threat_turn_interval_km":
            "Целевая длина ПРЯМОГО участка: маршрут держит курс примерно столько км, "
            "потом доворачивает.",
        "threat_L_max":
            "Главное ограничение маршрута: полная длина пути вход→цель не может его превысить.",
    }

    def __init__(self, parent, params, on_apply):
        super().__init__(parent)
        self.setWindowTitle("Входные данные маршрута")
        self.setModal(False)                      # НЕ блокирует основное окно
        self._on_apply = on_apply
        self._params = params
        self._edits = {}
        lay = QtWidgets.QFormLayout(self)
        self.ab_lbl = QtWidgets.QLabel("—")
        lay.addRow("|AB| (вход→цель), км:", self.ab_lbl)
        # запас хода: авто = |AB| + 25 % (галочка) либо руками (снять галочку)
        self.chk_auto = QtWidgets.QCheckBox("авто: |AB| + 25 %")
        self.chk_auto.setChecked(not getattr(params, "threat_L_max_manual", False))
        self.chk_auto.setToolTip("Запас хода = расстояние вход→цель +25 % (не меньше "
                                 "кратчайшего коридора). Снимите галочку — задать L_max руками.")
        self.chk_auto.stateChanged.connect(self._auto_changed)
        lay.addRow(self.chk_auto)
        for name, label in self.FIELDS:
            e = QtWidgets.QLineEdit(str(getattr(params, name)))
            e.returnPressed.connect(self._apply)
            if name in self.HINTS:
                e.setToolTip(self.HINTS[name])
            self._edits[name] = e
            lay.addRow(label, e)
        self._auto_changed()                       # выставить доступность поля L_max
        row = QtWidgets.QHBoxLayout()
        btn = QtWidgets.QPushButton("Применить (Enter)")
        btn.clicked.connect(self._apply)
        row.addWidget(btn)
        lay.addRow(row)
        hint = QtWidgets.QLabel("Enter в поле — сразу применяется к карте. Окно можно "
                                "держать открытым.")
        hint.setWordWrap(True); hint.setStyleSheet(f"color:{THEME['muted']};")
        lay.addRow(hint)

    def refresh(self, params):
        self._params = params
        self.chk_auto.blockSignals(True)
        self.chk_auto.setChecked(not getattr(params, "threat_L_max_manual", False))
        self.chk_auto.blockSignals(False)
        for name, e in self._edits.items():
            e.setText(f"{float(getattr(params, name)):g}")
        self._auto_changed()

    def set_ab(self, km):
        self.ab_lbl.setText(f"{km:.0f}")

    def _auto_changed(self, *_):
        """Авто-запас: поле L_max ВСЕГДА показывает текущее значение (и при снятии галочки
        оно не пропадает — просто становится редактируемым). В авто — только для чтения.

        Цвета БЕРЁМ НЕ ИЗ ТЁМНОЙ ТЕМЫ: это окно — обычный системный диалог со светлым фоном
        (THEME задана только на карте). Раньше редактируемое поле красилось в THEME['text']
        (#e6edf3) — почти белым по белому, и значение было не видно."""
        auto = self.chk_auto.isChecked()
        e = self._edits.get("threat_L_max")
        if e is None:
            return
        e.setText(f"{float(getattr(self._params, 'threat_L_max', 0.0)):g}")  # всегда заполнено
        e.setReadOnly(auto)
        e.setStyleSheet("color:#6b7785; background:#f0f0f0;" if auto   # авто: серым, «только чтение»
                        else "")                                       # руками: обычное поле

    def _apply(self):
        """Применить поля. Нечисловое поле берётся из текущих параметров (не роняем ввод).
        В авто-режиме L_max не передаём — его считает модель (|AB|+25 %)."""
        vals = {}
        for name, e in self._edits.items():
            txt = e.text().strip().replace(",", ".")
            try:
                vals[name] = float(txt)
            except ValueError:
                vals[name] = float(getattr(self._params, name))
        auto = self.chk_auto.isChecked()
        vals["threat_L_max_manual"] = (not auto)
        if auto:
            vals.pop("threat_L_max", None)
        self._on_apply(vals)
