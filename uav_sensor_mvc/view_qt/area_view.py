# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Вкладка 2 «Зона старта -> цель».

Подкласс Qt-представления: переиспользует всю отрисовку сцены, анимацию полёта,
легенды и контракт методов из view_qt.SimulationView, добавляя:
  * поля «глубина зоны» и «ширина зоны»;
  * выбор модели движения (дуги из старта / ломаная-манёвр);
  * кнопку «Создать маршрут» и постановку точек кликом по карте
    (A — центр зоны старта; второй клик задаёт направление на цель B);
  * отображение зоны старта (эллипс) и оси A->B.
"""
import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

from config import (MODE_LABELS, MOTION_LABELS, AREA_TRAJ_LABELS,
                    WAYPOINT_ZONE_LABELS, THEME)
from view.view import PARAM_SPECS
from .view_qt import SimulationView, _qcolor, QT_EXTRA
from . import geomap as gm

AREA_PARAM_SPECS = list(PARAM_SPECS) + QT_EXTRA + [
    ("corridor_depth", "Глубина зоны", float),
    ("corridor_width", "Ширина зоны", float),
]


class AreaStartView(SimulationView):

    def __init__(self, A, B, outline, bbox, params, speed=4, speed_max=20):
        # дополнительные callbacks и состояние постановки точек (до super().__init__)
        self.on_movement = lambda key: None
        self.on_create = lambda: None
        self.on_route_ready = lambda A, dir_pt: None
        self.on_zone = lambda key: None
        self.on_map_layer = lambda key: None
        self._click_state = None
        self._map_layer = getattr(params, "map_layer", "scheme")
        self._has_route_view = False
        super().__init__(A, B, outline, bbox, params, speed, speed_max)

    def get_param_specs(self):
        return AREA_PARAM_SPECS

    def _move_changed(self, key):
        self.on_movement(key)

    def _build_zone_combo(self, col, params):
        """Выпадающий список «Точки маршрута»: в зоне старта / только за зоной."""
        row = QtWidgets.QHBoxLayout()
        lab = QtWidgets.QLabel("Точки маршрута:"); lab.setObjectName("muted")
        self.combo_zone = QtWidgets.QComboBox()
        self._zone_keys = list(WAYPOINT_ZONE_LABELS)
        for k in self._zone_keys:
            self.combo_zone.addItem(WAYPOINT_ZONE_LABELS[k])
        cur = getattr(params, "waypoint_zone", "outside")
        self.combo_zone.setCurrentIndex(self._zone_keys.index(cur)
                                        if cur in self._zone_keys else 0)
        self.combo_zone.currentIndexChanged.connect(
            lambda i: self.on_zone(self._zone_keys[i]))
        row.addWidget(lab); row.addWidget(self.combo_zone, 1)
        col.addLayout(row)

    def _build_map_combo(self, col, params):
        """Выпадающий список «Карта»: схема (оффлайн) / OSM / топо / спутник / тёмная."""
        row = QtWidgets.QHBoxLayout()
        lab = QtWidgets.QLabel("Карта:"); lab.setObjectName("muted")
        self.combo_map = QtWidgets.QComboBox()
        self._map_keys = list(gm.LAYER_LABELS)
        for k in self._map_keys:
            self.combo_map.addItem(gm.LAYER_LABELS[k])
        cur = getattr(params, "map_layer", "scheme")
        self.combo_map.setCurrentIndex(self._map_keys.index(cur)
                                       if cur in self._map_keys else 0)
        self.combo_map.currentIndexChanged.connect(self._map_changed)
        row.addWidget(lab); row.addWidget(self.combo_map, 1)
        col.addLayout(row)

    def _map_changed(self, i):
        self._map_layer = self._map_keys[i]
        self.on_map_layer(self._map_layer)
        self._refresh_basemap(force=True)

    # ---- панель вкладки 2 ----
    def _build_ui(self, params, speed, speed_max):
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

        panel = QtWidgets.QFrame(); panel.setObjectName("panel"); panel.setFixedWidth(372)
        col = QtWidgets.QVBoxLayout(panel)
        col.setContentsMargins(12, 12, 12, 12); col.setSpacing(8)
        root.addWidget(panel)

        col.addWidget(self._header("ПАРАМЕТРЫ  (Enter — пересчёт)"))
        grid = QtWidgets.QGridLayout(); grid.setSpacing(6)
        for i, (name, label, _t) in enumerate(AREA_PARAM_SPECS):
            r, c = divmod(i, 2)
            cell = QtWidgets.QVBoxLayout(); cell.setSpacing(1)
            lab = QtWidgets.QLabel(label); lab.setObjectName("muted")
            edit = QtWidgets.QLineEdit(str(getattr(params, name)))
            edit.returnPressed.connect(self._on_field_submit)
            self._fields[name] = edit
            cell.addWidget(lab); cell.addWidget(edit)
            grid.addLayout(cell, r, c)
        col.addLayout(grid)

        col.addWidget(self._header("МАРШРУТ"))
        self.btn_create = QtWidgets.QPushButton("Создать маршрут")
        self._tint(self.btn_create, THEME["accent"])
        self.btn_create.clicked.connect(lambda: self.on_create())
        col.addWidget(self.btn_create)
        self.hint = QtWidgets.QLabel("Кликните A (центр зоны), затем направление B.")
        self.hint.setObjectName("muted"); self.hint.setWordWrap(True)
        col.addWidget(self.hint)

        two = QtWidgets.QHBoxLayout()
        self._mode_keys, self.grp_mode = self._radio_group(
            "РЕЖИМ ОПТИМ.", MODE_LABELS, params.mode, self._mode_changed)
        two.addWidget(self._wrap_group("РЕЖИМ ОПТИМ.", MODE_LABELS, self.grp_mode), 1)
        self._move_keys, self.grp_move = self._radio_group(
            "ДВИЖЕНИЕ", AREA_TRAJ_LABELS, "area_arc", self._move_changed)
        self._prof_keys, self.grp_prof = self._radio_group(
            "РАЗБРОС", MOTION_LABELS, params.motion_profile, self._prof_changed)
        rb = QtWidgets.QVBoxLayout()
        rb.addWidget(self._wrap_group("ДВИЖЕНИЕ", AREA_TRAJ_LABELS, self.grp_move))
        rb.addWidget(self._wrap_group("РАЗБРОС", MOTION_LABELS, self.grp_prof))
        rw = QtWidgets.QWidget(); rw.setLayout(rb)
        two.addWidget(rw, 1)
        col.addLayout(two)
        self._build_law_combo(col, params)
        self._build_zone_combo(col, params)
        self._build_map_combo(col, params)

        col.addWidget(self._header("ПОКАЗ"))
        self.chk_heat = QtWidgets.QCheckBox("тепловая карта")
        self.chk_freq = QtWidgets.QCheckBox("10 маршрутов")
        self.chk_fan = QtWidgets.QCheckBox("веер маршрутов")
        for chk in (self.chk_heat, self.chk_freq, self.chk_fan):
            chk.stateChanged.connect(lambda _s: self.on_toggle())
            col.addWidget(chk)

        col.addWidget(self._header("СКОРОСТЬ"))
        self._speed = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._speed.setMinimum(1); self._speed.setMaximum(speed_max); self._speed.setValue(speed)
        self._speed.valueChanged.connect(lambda v: self.on_speed(int(v)))
        col.addWidget(self._speed)

        b1 = QtWidgets.QHBoxLayout()
        self.btn_apply = QtWidgets.QPushButton("Применить")
        self.btn_reset = QtWidgets.QPushButton("Сброс")
        self._tint(self.btn_apply, THEME["accent2"])
        b1.addWidget(self.btn_apply); b1.addWidget(self.btn_reset)
        col.addLayout(b1)
        b2 = QtWidgets.QHBoxLayout()
        self.btn_run = QtWidgets.QPushButton("Пуск")
        self.btn_step = QtWidgets.QPushButton("Шаг")
        self.btn_batch = QtWidgets.QPushButton("Пакетно")
        self._tint(self.btn_run, THEME["ok"]); self._tint(self.btn_batch, THEME["accent2"])
        b2.addWidget(self.btn_run); b2.addWidget(self.btn_step); b2.addWidget(self.btn_batch)
        col.addLayout(b2)
        self.btn_apply.clicked.connect(lambda: self.on_apply())
        self.btn_reset.clicked.connect(lambda: self.on_reset())
        self.btn_run.clicked.connect(lambda: self.on_start_pause())
        self.btn_step.clicked.connect(lambda: self.on_step())
        self.btn_batch.clicked.connect(lambda: self.on_batch())

        col.addWidget(self._header("ПОКАЗАТЕЛИ"))
        self.metrics = QtWidgets.QPlainTextEdit(); self.metrics.setReadOnly(True)
        self.metrics.setMinimumHeight(150)
        col.addWidget(self.metrics, stretch=1)

    # ---- сцена: карта-подложка, зона старта, ось, клик-постановка ----
    def _build_scene_items(self):
        super()._build_scene_items()
        # --- КАРТА-ПОДЛОЖКА (растровые тайлы) и оффлайн-СХЕМА ---
        self.basemap = pg.ImageItem(); self.basemap.setZValue(-20)
        self.basemap.setOpts(axisOrder="row-major"); self.basemap.setVisible(False)
        self.pi.addItem(self.basemap)
        self.graticule = self.pi.plot([], [], pen=pg.mkPen(_qcolor(THEME["grid"], 150),
                                                           width=1.0))
        self.graticule.setZValue(-15)
        self.oblast = self.pi.plot([], [], pen=pg.mkPen(_qcolor(THEME["accent"], 130),
                                                        width=1.2, dash=[6, 5]))
        self.oblast.setZValue(-14)
        self.cities = pg.ScatterPlotItem(size=6, symbol="o",
                                         brush=pg.mkBrush(_qcolor(THEME["text"], 200)),
                                         pen=pg.mkPen(_qcolor(THEME["accent"]), width=0.8))
        self.cities.setZValue(-13); self.pi.addItem(self.cities)
        self._city_labels = []
        for name, x, y in gm.city_points_km():
            t = pg.TextItem(name, color=THEME["muted"], anchor=(0, 1))
            t.setPos(x, y); t.setZValue(-12); t.setVisible(False)
            self.pi.addItem(t); self._city_labels.append((t, x, y))
        # дебаунс обновления подложки при панораме/зуме
        self._map_timer = QtCore.QTimer(self); self._map_timer.setSingleShot(True)
        self._map_timer.timeout.connect(lambda: self._refresh_basemap())
        self.vb.sigRangeChanged.connect(lambda *a: self._map_timer.start(280))

        # зона старта (outline_lo) — отдельным цветом; реах-эллипс (outline_up) — как контур
        self.outline_lo.setPen(pg.mkPen(_qcolor(THEME["accent2"], 230), width=1.7,
                                        dash=[4, 4]))
        self.lab_A.setText("A — зона старта"); self.lab_B.setText("B — цель")
        self.axis_item = self.pi.plot([], [], pen=pg.mkPen(_qcolor(THEME["muted"], 160),
                                                           width=1.0, dash=[2, 4]))
        self.axis_item.setZValue(1)
        self.plot.scene().sigMouseClicked.connect(self._on_scene_click)

    # ---- карта-подложка: растровые тайлы или оффлайн-схема ----
    def _refresh_basemap(self, force=False):
        """Обновить подложку под ТЕКУЩЕЕ окно. «scheme» — оффлайн (сетка+города),
        иначе — растровые тайлы (кэш/интернет). Любая ошибка -> откат на схему."""
        try:
            (kx0, kx1), (ky0, ky1) = self.vb.viewRange()
        except Exception:
            return
        if not (np.isfinite([kx0, kx1, ky0, ky1]).all()):
            return
        layer = getattr(self, "_map_layer", "scheme")
        if layer == "scheme":
            self.basemap.setVisible(False)
            self._draw_scheme(kx0, kx1, ky0, ky1)
            return
        # растровые тайлы
        self._set_scheme_visible(False)
        try:
            res = gm.build_raster_basemap(kx0, kx1, ky0, ky1, layer=layer,
                                          allow_net=True)
        except Exception:
            res = None
        if res is None:                                  # нет тайлов -> схема как запас
            self.basemap.setVisible(False)
            self._draw_scheme(kx0, kx1, ky0, ky1)
            return
        img, (ex0, ex1, ey0, ey1) = res
        img8 = (np.clip(img, 0.0, 1.0) * 255).astype(np.ubyte)   # uint8 RGBA для pg
        self.basemap.setImage(img8, autoLevels=False)
        self.basemap.setRect(QtCore.QRectF(ex0, ey0, ex1 - ex0, ey1 - ey0))
        self.basemap.setVisible(True)

    def _set_scheme_visible(self, vis):
        self.graticule.setVisible(vis); self.oblast.setVisible(vis)
        self.cities.setVisible(vis)
        for t, _x, _y in self._city_labels:
            t.setVisible(vis)

    def _draw_scheme(self, kx0, kx1, ky0, ky1):
        """Оффлайн-схема: сетка широт/долгот, рамка области, города."""
        xs, ys = [], []
        for kind, val, _lab in gm.graticule_km(kx0, kx1, ky0, ky1):
            if kind == "v":
                xs += [val, val, np.nan]; ys += [ky0, ky1, np.nan]
            else:
                xs += [kx0, kx1, np.nan]; ys += [val, val, np.nan]
        self.graticule.setData(xs, ys)
        ob = gm.oblast_outline_km()
        self.oblast.setData(ob[:, 0], ob[:, 1])
        pts = gm.city_points_km()
        self.cities.setData([p[1] for p in pts], [p[2] for p in pts])
        self._set_scheme_visible(True)

    def update_geometry(self, A, B, outline, bbox):
        self._has_route_view = True
        super().update_geometry(A, B, outline, bbox)
        self.axis_item.setData([A[0], B[0]], [A[1], B[1]])
        self.lab_A.setText("A — зона старта"); self.lab_B.setText("B — цель")

    def _set_kursk_view(self, half=130.0):
        """Начальный вид: Курск в центре, окно ~±half км."""
        self.pi.setRange(xRange=(-half, half), yRange=(-half, half), padding=0.02)

    def showEvent(self, e):
        QtWidgets.QWidget.showEvent(self, e)     # range/карту ведём сами (см. ниже)
        if self._has_route_view:
            self._apply_range(force=True)
        else:
            self._set_kursk_view()
        self._refresh_basemap(force=True)

    # ---- постановка маршрута кликами ----
    def begin_create(self):
        self._click_state = "A"
        self.hint.setText("Кликните на карте точку A — центр зоны старта.")
        self._set_title("Кликните A — центр зоны старта (Курск в центре)")

    def prompt_create(self):
        t = dict(show_heat=False, show_freq=False, show_fan=False)
        self._render_static([], 0, None, None, None, t)
        self._set_flight_visible(False)
        for it in (self.outline_up, self.outline_lo, self.axis_item):
            it.setData([], [])
        self.ab_scatter.setData([], [])
        self.lab_A.setText(""); self.lab_B.setText("")
        self._has_route_view = False
        self._set_kursk_view()
        self._refresh_basemap(force=True)          # карта остаётся видимой
        self.hint.setText("«Создать маршрут» → клик A (центр зоны), затем клик B "
                          "(цель). Расстояние |AB| посчитается автоматически.")
        self._set_title("Вкладка 2 · карта Курской области · «Создать маршрут»")

    def _on_scene_click(self, ev):
        if self._click_state is None:
            return
        try:
            if ev.button() != QtCore.Qt.LeftButton:
                return
        except Exception:
            pass
        pt = self.vb.mapSceneToView(ev.scenePos())
        P = (float(pt.x()), float(pt.y()))
        if self._click_state == "A":
            self._click_A = P
            self._click_state = "B"
            self.ab_scatter.setData([P[0]], [P[1]])
            self.hint.setText("Кликните точку цели B — расстояние посчитается само.")
            self._set_title("Кликните точку цели B")
        elif self._click_state == "B":
            self._click_state = None
            self.on_route_ready(self._click_A, P)
