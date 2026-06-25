# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Слой визуализации на PyQt5 + pyqtgraph (тёмная тема).

Альтернатива matplotlib-представлению (view/view.py): даёт аппаратно ускоренную
плавную анимацию, удобные зум и панорамирование мышью и гибкую компоновку.
Реализует ТОТ ЖЕ контракт методов, что ожидает контроллер (update_geometry,
draw_clear, draw_static_frame, setup_flight, update_flight, set_metrics,
flash_title, set_running_label, get_toggles, get/set_param_values,
process_pending), поэтому используется с тем же SimulationController.

Слои (3 чекбокса ПОКАЗ): тепловая карта плотности; 10 наиболее вероятных
маршрутов; веер всех возможных маршрутов по шагу (предельные — пунктир).
Зависимости: PyQt5, pyqtgraph, numpy (matplotlib используется только для палитр).
"""
import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets
import matplotlib.cm as cm

from config import MODE_LABELS, TRAJ_LABELS, MOTION_LABELS, THEME
from view.view import PARAM_SPECS          # единый источник списка параметров

pg.setConfigOptions(antialias=True, background=THEME["axes"], foreground=THEME["text"])


def _qcolor(hex_or_rgba, alpha=None):
    c = pg.mkColor(hex_or_rgba)
    if alpha is not None:
        c.setAlpha(int(alpha))
    return c


def _viridis_qcolor(w):
    r, g, b, _ = cm.viridis(0.30 + 0.65 * float(np.clip(w, 0, 1)))
    return QtGui.QColor(int(r * 255), int(g * 255), int(b * 255))


def _heat_cmap():
    try:
        return pg.colormap.getFromMatplotlib("turbo")
    except Exception:
        stops = np.linspace(0, 1, 256)
        cols = (cm.turbo(stops) * 255).astype(np.ubyte)
        return pg.colormap.ColorMap(stops, cols)


class _SpeedAdapter:
    """Совместимость с контроллером: тот читает view.slider_speed.val."""
    def __init__(self, slider):
        self._s = slider

    @property
    def val(self):
        return self._s.value()


class SimulationView(QtWidgets.QWidget):
    """Qt-представление. Сигнатуры публичных методов совпадают с matplotlib-View."""

    def __init__(self, A, B, outline, bbox, params, speed=4, speed_max=20):
        super().__init__()
        self.A, self.B = A, B
        self._outline, self._bbox = outline, bbox
        self._last_bbox = None
        self._heat_cmap = _heat_cmap()
        self._heat_lut = self._heat_cmap.getLookupTable(0.0, 1.0, 256)

        # callbacks (контроллер переопределит через set_callbacks)
        self.on_start_pause = self.on_step = self.on_reset = lambda: None
        self.on_batch = self.on_apply = lambda: None
        self.on_mode = self.on_traj = self.on_profile = lambda key: None
        self.on_speed = lambda v: None
        self.on_toggle = lambda: None

        self._fields = {}
        self._suppress = False
        self._path_items = []
        self._sensor_items = []
        self._cur_traj = None

        self.setWindowTitle("Размещение датчиков обнаружения БПЛА — Qt/pyqtgraph")
        self.resize(1360, 780)
        self._apply_stylesheet()
        self._build_ui(params, speed, speed_max)
        self._build_scene_items()
        self.slider_speed = _SpeedAdapter(self._speed)

    # ==================================================================
    # Построение интерфейса
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

    def _build_ui(self, params, speed, speed_max):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # --- карта (pyqtgraph) ---
        self.plot = pg.PlotWidget()
        self.pi = self.plot.getPlotItem()
        self.pi.setAspectLocked(True)
        self.pi.showGrid(x=True, y=True, alpha=0.12)
        self.pi.setLabel("bottom", "X, км", color=THEME["muted"])
        self.pi.setLabel("left", "Y, км", color=THEME["muted"])
        self.vb = self.pi.getViewBox()
        root.addWidget(self.plot, stretch=1)

        # --- панель управления ---
        panel = QtWidgets.QFrame(); panel.setObjectName("panel")
        panel.setFixedWidth(372)
        col = QtWidgets.QVBoxLayout(panel)
        col.setContentsMargins(12, 12, 12, 12); col.setSpacing(8)
        root.addWidget(panel)

        col.addWidget(self._header("ПАРАМЕТРЫ  (Enter — пересчёт)"))
        grid = QtWidgets.QGridLayout(); grid.setSpacing(6)
        for i, (name, label, _t) in enumerate(PARAM_SPECS):
            r, c = divmod(i, 2)
            cell = QtWidgets.QVBoxLayout(); cell.setSpacing(1)
            lab = QtWidgets.QLabel(label); lab.setObjectName("muted")
            edit = QtWidgets.QLineEdit(str(getattr(params, name)))
            edit.returnPressed.connect(self._on_field_submit)
            self._fields[name] = edit
            cell.addWidget(lab); cell.addWidget(edit)
            grid.addLayout(cell, r, c)
        col.addLayout(grid)

        # режимы / движение / разброс
        two = QtWidgets.QHBoxLayout()
        self._mode_keys, self.grp_mode = self._radio_group(
            "РЕЖИМ ОПТИМ.", MODE_LABELS, params.mode, self._mode_changed)
        self.grp_mode_box = self._wrap_group("РЕЖИМ ОПТИМ.", MODE_LABELS,
                                             self.grp_mode)
        self._traj_keys, self.grp_traj = self._radio_group(
            "ДВИЖЕНИЕ", TRAJ_LABELS, params.traj_model, self._traj_changed)
        self._prof_keys, self.grp_prof = self._radio_group(
            "РАЗБРОС", MOTION_LABELS, params.motion_profile, self._prof_changed)
        two.addWidget(self.grp_mode_box, 1)
        rb = QtWidgets.QVBoxLayout()
        rb.addWidget(self._wrap_group("ДВИЖЕНИЕ", TRAJ_LABELS, self.grp_traj))
        rb.addWidget(self._wrap_group("РАЗБРОС", MOTION_LABELS, self.grp_prof))
        rw = QtWidgets.QWidget(); rw.setLayout(rb)
        two.addWidget(rw, 1)
        col.addLayout(two)

        # слои
        col.addWidget(self._header("ПОКАЗ"))
        self.chk_heat = QtWidgets.QCheckBox("тепловая карта")
        self.chk_freq = QtWidgets.QCheckBox("10 маршрутов")
        self.chk_fan = QtWidgets.QCheckBox("веер (шаг угла)")
        for chk in (self.chk_heat, self.chk_freq, self.chk_fan):
            chk.stateChanged.connect(lambda _s: self.on_toggle())
            col.addWidget(chk)

        # скорость
        col.addWidget(self._header("СКОРОСТЬ"))
        self._speed = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._speed.setMinimum(1); self._speed.setMaximum(speed_max)
        self._speed.setValue(speed)
        self._speed.valueChanged.connect(lambda v: self.on_speed(int(v)))
        col.addWidget(self._speed)

        # кнопки
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
        self._tint(self.btn_run, THEME["ok"])
        self._tint(self.btn_batch, THEME["accent2"])
        b2.addWidget(self.btn_run); b2.addWidget(self.btn_step)
        b2.addWidget(self.btn_batch)
        col.addLayout(b2)
        self.btn_apply.clicked.connect(lambda: self.on_apply())
        self.btn_reset.clicked.connect(lambda: self.on_reset())
        self.btn_run.clicked.connect(lambda: self.on_start_pause())
        self.btn_step.clicked.connect(lambda: self.on_step())
        self.btn_batch.clicked.connect(lambda: self.on_batch())

        # показатели
        col.addWidget(self._header("ПОКАЗАТЕЛИ"))
        self.metrics = QtWidgets.QPlainTextEdit(); self.metrics.setReadOnly(True)
        self.metrics.setMinimumHeight(180)
        col.addWidget(self.metrics, stretch=1)

    def _header(self, text):
        lab = QtWidgets.QLabel(text); lab.setObjectName("header")
        return lab

    def _radio_group(self, _title, labels_map, active_key, slot):
        keys = list(labels_map)
        grp = QtWidgets.QButtonGroup(self)
        grp._keys = keys
        grp._buttons = []
        for k in keys:
            rb = QtWidgets.QRadioButton(labels_map[k])
            if k == active_key:
                rb.setChecked(True)
            grp.addButton(rb)
            grp._buttons.append(rb)
        grp.buttonClicked.connect(lambda _btn: slot(self._active_key(grp)))
        return keys, grp

    def _wrap_group(self, title, _labels_map, grp):
        box = QtWidgets.QVBoxLayout(); box.setSpacing(2)
        box.addWidget(self._header(title))
        for rb in grp._buttons:
            box.addWidget(rb)
        w = QtWidgets.QWidget(); w.setLayout(box)
        return w

    @staticmethod
    def _active_key(grp):
        for k, rb in zip(grp._keys, grp._buttons):
            if rb.isChecked():
                return k
        return grp._keys[0]

    def _tint(self, btn, color):
        btn.setStyleSheet(
            f"QPushButton {{ background: {color}; color: white; border-radius: 6px;"
            f" padding: 7px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {THEME['accent']}; }}")

    # ==================================================================
    # Постоянные графические объекты сцены
    # ==================================================================
    def _build_scene_items(self):
        self.heat = pg.ImageItem(); self.heat.setOpts(axisOrder="row-major")
        self.heat.setZValue(0); self.heat.setOpacity(0.85); self.heat.setVisible(False)
        self.pi.addItem(self.heat)

        # цветовая шкала плотности (легенда тепловой карты)
        self.cbar = pg.ColorBarItem(values=(0, 1), colorMap=self._heat_cmap,
                                    label="плотность (редко → часто)",
                                    interactive=False)
        try:
            self.cbar.setImageItem(self.heat, insert_in=self.pi)
        except Exception:
            pass
        self.cbar.setVisible(False)

        # легенда слоёв (типы линий маршрутов)
        self.legend = self.pi.addLegend(offset=(8, 8),
                                        brush=pg.mkBrush(_qcolor(THEME["panel"], 210)),
                                        pen=pg.mkPen(_qcolor(THEME["grid"])))
        try:
            self.legend.setLabelTextColor(THEME["text"])
        except Exception:
            pass
        self._sw_route = pg.PlotDataItem([0, 1], [0, 0],
                                         pen=pg.mkPen(_viridis_qcolor(0.75), width=2.6))
        self._sw_limit = pg.PlotDataItem([0, 1], [0, 0],
                                         pen=pg.mkPen(_qcolor(THEME["warn"]), width=1.7,
                                                      dash=[7, 4]))
        self.legend.setVisible(False)

        dash = pg.mkPen(_qcolor(THEME["ellipse"], 150), width=1.2, dash=[6, 4])
        self.outline_up = self.pi.plot([], [], pen=dash); self.outline_up.setZValue(1)
        self.outline_lo = self.pi.plot([], [], pen=dash); self.outline_lo.setZValue(1)

        self.ab_scatter = pg.ScatterPlotItem(
            size=13, symbol="s", brush=pg.mkBrush(_qcolor(THEME["text"])),
            pen=pg.mkPen(_qcolor(THEME["accent"]), width=1.5))
        self.ab_scatter.setZValue(6); self.pi.addItem(self.ab_scatter)
        self.lab_A = pg.TextItem("A — старт", color=THEME["text"], anchor=(0, 1))
        self.lab_B = pg.TextItem("B — цель", color=THEME["text"], anchor=(0, 1))
        for t in (self.lab_A, self.lab_B):
            t.setZValue(6); self.pi.addItem(t)

        # анимация полёта (постоянные объекты)
        self.full_path = self.pi.plot([], [], pen=pg.mkPen(_qcolor(THEME["accent"], 90),
                                                           width=1.2))
        self.full_path.setZValue(7)
        self.flown = self.pi.plot([], [], pen=pg.mkPen(_qcolor(THEME["accent"]),
                                                       width=2.6))
        self.flown.setZValue(8)
        self.uav = pg.ScatterPlotItem(size=13, symbol="o",
                                      brush=pg.mkBrush(_qcolor(THEME["warn"])),
                                      pen=pg.mkPen("white", width=1.0))
        self.uav.setZValue(9); self.pi.addItem(self.uav)
        self.hud = pg.TextItem("", color=THEME["text"], anchor=(0, 0),
                               fill=pg.mkBrush(_qcolor(THEME["panel"], 180)))
        self.hud.setZValue(10); self.pi.addItem(self.hud)
        self._set_flight_visible(False)

    def _set_flight_visible(self, vis):
        for it in (self.full_path, self.flown, self.uav, self.hud):
            it.setVisible(vis)

    def _clear_list(self, items):
        for it in items:
            self.pi.removeItem(it)
        items.clear()

    # ==================================================================
    # API для контроллера
    # ==================================================================
    def get_param_specs(self):
        return PARAM_SPECS

    def set_callbacks(self, **cbs):
        for name, fn in cbs.items():
            setattr(self, name, fn)

    def get_toggles(self):
        return dict(show_heat=self.chk_heat.isChecked(),
                    show_freq=self.chk_freq.isChecked(),
                    show_fan=self.chk_fan.isChecked())

    def set_running_label(self, running):
        self.btn_run.setText("Пауза" if running else "Пуск")

    def get_param_values(self):
        out = {}
        for name, _l, typ in self.get_param_specs():
            raw = self._fields[name].text().strip().replace(",", ".")
            out[name] = typ(float(raw)) if typ is int else typ(raw)
        return out

    def set_param_values(self, params):
        self._suppress = True
        try:
            for name, _l, _t in self.get_param_specs():
                self._fields[name].setText(str(getattr(params, name)))
        finally:
            self._suppress = False

    def _on_field_submit(self):
        if not self._suppress:
            self.on_apply()

    def _mode_changed(self, key):
        self.on_mode(key)

    def _traj_changed(self, key):
        self.on_traj(key)

    def _prof_changed(self, key):
        self.on_profile(key)

    def update_geometry(self, A, B, outline, bbox):
        self.A, self.B = A, B
        self._outline = outline
        self._bbox = bbox
        if outline is not None:
            up, lo = outline
            self.outline_up.setData(up[:, 0], up[:, 1])
            self.outline_lo.setData(lo[:, 0], lo[:, 1])
        self.ab_scatter.setData([A[0], B[0]], [A[1], B[1]])
        self.lab_A.setPos(A[0], A[1]); self.lab_B.setPos(B[0], B[1])
        # масштаб меняем ТОЛЬКО при смене коридора — иначе зум пользователя сохраняется
        if bbox != self._last_bbox:
            x0, x1, y0, y1 = bbox
            self.pi.setRange(xRange=(x0, x1), yRange=(y0, y1), padding=0.06)
            self._last_bbox = bbox

    # ---- слои ----
    def _render_density(self, density, show):
        if not show or not density or density[0] is None:
            self.heat.setVisible(False); self.cbar.setVisible(False); return
        H, extent = density
        disp = np.power(np.clip(H, 0, 1), 0.45)
        self.heat.setImage(disp, levels=(0, 1), lut=self._heat_lut, autoLevels=False)
        x0, x1, y0, y1 = extent
        self.heat.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
        self.heat.setVisible(True); self.cbar.setVisible(True)

    def _update_legend(self, freq, fan, t, flight):
        self.legend.clear()
        show = (not flight) and ((t["show_freq"] and freq) or (t["show_fan"] and fan))
        if not show:
            self.legend.setVisible(False); return
        self.legend.addItem(self._sw_route, "вероятные маршруты")
        if t["show_fan"] and fan and any(p.get("is_extreme") for p in fan):
            self.legend.addItem(self._sw_limit, "предел запаса хода")
        self.legend.setVisible(True)

    def _render_paths(self, paths, show):
        if not show or not paths:
            return
        for pth in paths:
            tr = pth["traj"]; w = pth["weight"]
            if pth.get("is_extreme"):
                pen = pg.mkPen(_qcolor(THEME["warn"], 240), width=1.7, dash=[7, 4])
                z = 3
            else:
                col = _viridis_qcolor(w); col.setAlpha(int((0.55 + 0.4 * w) * 255))
                pen = pg.mkPen(col, width=1.2 + 2.8 * w)
                z = 2
            item = self.pi.plot(tr[:, 0], tr[:, 1], pen=pen)
            item.setZValue(z)
            self._path_items.append(item)

    def _render_sensors(self, sensors, R):
        for i, s in enumerate(sensors, 1):
            e = QtWidgets.QGraphicsEllipseItem(s[0] - R, s[1] - R, 2 * R, 2 * R)
            e.setPen(pg.mkPen(_qcolor(THEME["ok"]), width=1.2))
            e.setBrush(pg.mkBrush(_qcolor(THEME["ok"], 33)))
            e.setZValue(4)
            self.pi.addItem(e, ignoreBounds=True)
            self._sensor_items.append(e)
            num = pg.TextItem(str(i), color="white", anchor=(0, 1))
            num.setPos(s[0], s[1]); num.setZValue(6)
            self.pi.addItem(num); self._sensor_items.append(num)
        if len(sensors):
            dots = pg.ScatterPlotItem(
                [p[0] for p in sensors], [p[1] for p in sensors], size=8, symbol="o",
                brush=pg.mkBrush(_qcolor(THEME["ok"])), pen=pg.mkPen("white", width=0.8))
            dots.setZValue(5); self.pi.addItem(dots)
            self._sensor_items.append(dots)

    def _render_static(self, sensors, R, freq, fan, density, t):
        self._clear_list(self._path_items)
        self._clear_list(self._sensor_items)
        self._render_density(density, t["show_heat"])
        self._render_paths(fan, t["show_fan"])
        self._render_paths(freq, t["show_freq"])
        self._render_sensors(sensors, R)
        self._update_legend(freq, fan, t, flight=False)

    # ---- кадры ----
    def draw_clear(self, freq=None, fan=None, density=None, toggles=None,
                   title="Готово. «Пуск», «Шаг» или «Пакетно»."):
        t = toggles or dict(show_heat=False, show_freq=False, show_fan=False)
        self._render_static([], 0, freq, fan, density, t)
        self._set_flight_visible(False)
        self._set_title(title)

    def draw_static_frame(self, sensors, R, freq, fan, density, toggles, title):
        self._render_static(sensors, R, freq, fan, density, toggles)
        self._set_flight_visible(False)
        self._set_title(title)

    def setup_flight(self, traj, sensors, R, freq, fan, density, toggles, title):
        self._render_static(sensors, R, freq, fan, density, toggles)
        self._update_legend(freq, fan, toggles, flight=True)   # без легенды в полёте
        self._cur_traj = traj
        self.full_path.setData(traj[:, 0], traj[:, 1])
        self.flown.setData([], [])
        self.uav.setData([traj[0, 0]], [traj[0, 1]])
        x0, x1, y0, y1 = self._bbox
        self.hud.setPos(x0, y1)
        self.hud.setText("")
        self._set_flight_visible(True)
        self._set_title(title)

    def update_flight(self, j, hud):
        cur = self._cur_traj
        if cur is None:
            return
        jj = int(np.clip(j, 0, len(cur) - 1))
        self.flown.setData(cur[:jj + 1, 0], cur[:jj + 1, 1])
        self.uav.setData([cur[jj, 0]], [cur[jj, 1]])
        self.hud.setText(hud)

    def set_metrics(self, lines):
        self.metrics.setPlainText("\n".join(lines))

    def flash_title(self, text, color=None):
        self._set_title(text, color or THEME["warn"])

    def _set_title(self, text, color=None):
        self.pi.setTitle(text, color=color or THEME["text"], size="10pt")

    def process_pending(self):
        QtWidgets.QApplication.processEvents()

    def show(self):
        super().show()
