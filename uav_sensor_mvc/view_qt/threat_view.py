# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Вкладка 3 «Цифровая карта угроз».

Ограниченный участок местности (район Северодонецка, bbox по ориентирам Рубежное /
Дебальцево / Молодогвардейск) с жёстким пределом обзора. На карту накладываются:
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
                    THREAT_BBOX_POINTS, THREAT_ENTRY, THREAT_TARGET)
from .basemap_mixin import BasemapMixin, gm_qcolor as _qcolor
from . import geomap as gm

# Цвета векторных слоёв цифровой карты (различимые на тёмной подложке/спутнике)
LAYER_STYLE = {
    "river":      dict(color="#3aa0ff", width=2.6, dash=None),
    "road_major": dict(color="#ff9f43", width=2.4, dash=None),
    "road_local": dict(color="#ffd18c", width=1.4, dash=None),
    "railway":    dict(color="#e6edf3", width=1.6, dash=[6, 5]),
    "power":      dict(color="#f6e05e", width=1.3, dash=[2, 3]),
    "pipeline":   dict(color="#b794f6", width=1.4, dash=[8, 4]),
    "tree_row":   dict(color="#4fd18b", width=1.6, dash=[1, 3]),
    "built_up":   dict(color="#8aa0b6", width=1.2, dash=None),
}


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


class ThreatMapView(QtWidgets.QWidget, BasemapMixin):

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

        self.setWindowTitle("Цифровая карта угроз — вкладка 3")
        self._apply_stylesheet()
        self._build_ui(params)
        self._build_scene_items()
        self._init_basemap(lon0, lat0, lambda: self._map_layer,
                           lambda: self._map_offline,
                           scheme_points=self._orient_points())
        self._set_view_limits(self.bbox_km)
        self.plot.scene().sigMouseClicked.connect(self._on_scene_click)

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

    # ---- опорные точки-ориентиры для схемы/подписей ----
    def _orient_points(self):
        pts = [(name, lon, lat) for name, (lon, lat) in THREAT_BBOX_POINTS.items()]
        pts.append((THREAT_ENTRY[0], THREAT_ENTRY[1], THREAT_ENTRY[2]))
        pts.append((THREAT_TARGET[0], THREAT_TARGET[1], THREAT_TARGET[2]))
        return pts

    def set_callbacks(self, **cbs):
        for name, fn in cbs.items():
            setattr(self, name, fn)

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
            "Кликните точку ЦЕЛИ на карте. Точка входа фиксирована (Северодонецк, "
            "у реки). Цель — задел для построения маршрутов (Этап 3).")
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
        self.chk_water = QtWidgets.QCheckBox("запрет воды (для датчиков)")
        self.chk_cand = QtWidgets.QCheckBox("кандидатные позиции")
        for chk in (self.chk_threat, self.chk_layers, self.chk_water, self.chk_cand):
            chk.stateChanged.connect(lambda _s: self.on_toggle())
            col.addWidget(chk)

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

    def _on_field_submit(self):
        if not self._suppress:
            self.on_apply()

    # ==================================================================
    def _build_scene_items(self):
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

        # векторные слои
        for name in THREAT_LAYER_ORDER:
            if name == "bridge" or name not in LAYER_STYLE:
                continue
            st = LAYER_STYLE[name]
            pen = pg.mkPen(_qcolor(st["color"], 235), width=st["width"],
                           dash=st["dash"])
            item = self.pi.plot([], [], pen=pen); item.setZValue(-5)
            self._layer_items[name] = item
        # мосты — точечные маркеры
        self.bridge_scatter = pg.ScatterPlotItem(
            size=13, symbol="t", brush=pg.mkBrush(_qcolor(THEME["warn"])),
            pen=pg.mkPen("white", width=1.0))
        self.bridge_scatter.setZValue(-3); self.pi.addItem(self.bridge_scatter)

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

        # вход/цель
        self.entry_scatter = pg.ScatterPlotItem(size=16, symbol="star",
            brush=pg.mkBrush(_qcolor(THEME["ok"])), pen=pg.mkPen("white", width=1.2))
        self.entry_scatter.setZValue(4); self.pi.addItem(self.entry_scatter)
        self.target_scatter = pg.ScatterPlotItem(size=16, symbol="x",
            brush=pg.mkBrush(_qcolor(THEME["warn"])), pen=pg.mkPen("white", width=1.2))
        self.target_scatter.setZValue(4); self.pi.addItem(self.target_scatter)

    # ==================================================================
    # API для контроллера
    # ==================================================================
    def get_toggles(self):
        return dict(show_threat=self.chk_threat.isChecked(),
                    show_layers=self.chk_layers.isChecked(),
                    show_water=self.chk_water.isChecked(),
                    show_cand=self.chk_cand.isChecked())

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
        kx0, kx1, ky0, ky1 = self.bbox_km
        self.pi.setRange(xRange=(kx0, kx1), yRange=(ky0, ky1), padding=0.02)

    def process_pending(self):
        QtWidgets.QApplication.processEvents()

    # ---- отрисовка данных карты ----
    def render_layers(self, layers, bridge_pts, toggles):
        show = toggles["show_layers"]
        for name, item in self._layer_items.items():
            polys = layers.get(name, []) if show else []
            xs, ys = [], []
            for p in polys:
                p = np.asarray(p, float)
                xs += list(p[:, 0]) + [np.nan]
                ys += list(p[:, 1]) + [np.nan]
            item.setData(xs, ys)
            item.setVisible(show)
        if show and len(bridge_pts):
            bp = np.asarray(bridge_pts, float)
            self.bridge_scatter.setData(bp[:, 0], bp[:, 1])
            self.bridge_scatter.setVisible(True)
        else:
            self.bridge_scatter.setVisible(False)

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

    def render_water(self, mask, extent, toggles):
        if not toggles["show_water"] or mask is None:
            self.water_img.setVisible(False); return
        rgba = np.zeros(mask.shape + (4,), np.ubyte)
        rgba[mask] = (58, 160, 255, 150)               # синий там, где запрет датчика
        self.water_img.setImage(rgba, autoLevels=False)
        x0, x1, y0, y1 = extent
        self.water_img.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
        self.water_img.setVisible(True)

    def render_candidates(self, cand, toggles):
        if not toggles["show_cand"] or cand is None or len(cand) == 0:
            self.cand_scatter.setVisible(False); return
        self.cand_scatter.setData(cand[:, 0], cand[:, 1])
        self.cand_scatter.setVisible(True)

    def render_sensors(self, sensors, R):
        for it in self._sensor_items:
            self.pi.removeItem(it)
        self._sensor_items.clear()
        for i, s in enumerate(sensors, 1):
            e = QtWidgets.QGraphicsEllipseItem(s[0] - R, s[1] - R, 2 * R, 2 * R)
            e.setPen(pg.mkPen(_qcolor(THEME["ok"]), width=1.2))
            e.setBrush(pg.mkBrush(_qcolor(THEME["ok"], 30)))
            e.setZValue(2)
            self.pi.addItem(e, ignoreBounds=True)
            self._sensor_items.append(e)
        if len(sensors):
            dots = pg.ScatterPlotItem(
                [p[0] for p in sensors], [p[1] for p in sensors], size=9, symbol="o",
                brush=pg.mkBrush(_qcolor(THEME["ok"])), pen=pg.mkPen("white", width=0.9))
            dots.setZValue(3); self.pi.addItem(dots)
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
