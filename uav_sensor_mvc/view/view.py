# -*- coding: utf-8 -*-
"""
VIEW · Слой визуализации на нативных виджетах matplotlib (тёмная тема).

Особенности:
  * масштаб карты подгоняется под КОРИДОР движения (между предельными дугами),
    а не под весь эллипс достижимости;
  * контур коридора (предел запаса хода) показан всегда; веер вероятных путей и
    тепловая карта — по чекбоксам (по умолчанию выключены);
  * редактируемые параметры (правка + Enter перезапускает расчёт);
  * профиль разброса маршрутов (обычный / смешанный / сложный);
  * плавная анимация полёта через БЛИТТИНГ (фон рисуется раз на итерацию,
    в кадре обновляются только БПЛА и пройденный путь).

Зависимости: только matplotlib (+ numpy). Бизнес-логики не содержит.
"""
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from matplotlib.patches import Circle, FancyBboxPatch
from matplotlib.widgets import Button, RadioButtons, Slider, TextBox, CheckButtons

from config import MODE_LABELS, TRAJ_LABELS, MOTION_LABELS, THEME

PARAM_SPECS = [
    ("ab_distance",    "|AB|, км",    float),
    ("L_max",          "Запас, км",   float),
    ("N",              "Датчиков N",  int),
    ("R",              "Радиус R",    float),
    ("k",              "Кратность k", int),
    ("L_seg",          "Сегментов",   int),
    ("angle_step_deg", "Шаг угла °",  float),
    ("T",              "Итераций T",  int),
]


# ----------------------------------------------------------------------
# Помощники отрисовки сцены
# ----------------------------------------------------------------------
def fit_axes(ax, bbox, margin=1.14):
    """Подгонка осей под коридор (bbox) с равным масштабом и минимумом пустот."""
    x0, x1, y0, y1 = bbox
    cx, cy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
    dw, dh = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    fig = ax.figure
    pos = ax.get_position()
    box_aspect = (pos.width * fig.get_figwidth()) / (pos.height * fig.get_figheight())
    half_h = max(dh / 2, (dw / 2) / box_aspect) * margin
    half_w = half_h * box_aspect
    ax.set_xlim(cx - half_w, cx + half_w)
    ax.set_ylim(cy - half_h, cy + half_h)
    ax.set_aspect("equal")


def draw_static(ax, A, B, outline, bbox):
    ax.set_facecolor(THEME["axes"])
    if outline is not None:
        up, lo = outline
        for arc in (up, lo):
            ax.plot(arc[:, 0], arc[:, 1], color=THEME["ellipse"], ls="--",
                    lw=1.1, alpha=0.7, zorder=1)
    for P, name in ((A, "A — старт"), (B, "B — цель")):
        ax.plot(*P, "s", color=THEME["text"], ms=8, mec=THEME["accent"], mew=1.5,
                zorder=6)
        ax.annotate(name, P, textcoords="offset points", xytext=(8, 8),
                    fontsize=10, fontweight="bold", color=THEME["text"], zorder=6)
    ax.grid(alpha=0.15, color=THEME["grid"])
    ax.tick_params(colors=THEME["muted"], labelsize=8)
    for sp in ax.spines.values():
        sp.set_color(THEME["grid"])
    ax.set_xlabel("X, км", color=THEME["muted"], fontsize=9)
    ax.set_ylabel("Y, км", color=THEME["muted"], fontsize=9)
    fit_axes(ax, bbox)


def draw_density(ax, H, extent):
    if H is None:
        return
    ax.imshow(H, origin="lower", extent=extent, cmap="magma", alpha=0.45,
              zorder=0, interpolation="bilinear", aspect="auto")


def draw_probable(ax, paths, show_labels=True):
    n_lab = 0
    for pth in paths:
        w = pth["weight"]
        if pth.get("is_extreme"):
            ax.plot(pth["traj"][:, 0], pth["traj"][:, 1], color=THEME["warn"],
                    lw=1.7, alpha=0.95, ls=(0, (7, 4)), zorder=3,
                    label=(f"предел {pth.get('label')}") if show_labels else None)
        else:
            lab = None
            if show_labels and n_lab < 4:
                lab = pth.get("label"); n_lab += 1
            ax.plot(pth["traj"][:, 0], pth["traj"][:, 1],
                    color=cm.viridis(0.30 + 0.65 * w), lw=1.0 + 3.0 * w,
                    alpha=0.5 + 0.45 * w, label=lab, zorder=2)


def draw_sensors(ax, sensors, R):
    for i, s in enumerate(sensors, 1):
        ax.add_patch(Circle(s, R, fill=True, fc=THEME["ok"], alpha=0.13,
                            ec=THEME["ok"], lw=1.2, zorder=4))
        ax.plot(*s, "o", color=THEME["ok"], ms=7, mec="white", mew=0.8, zorder=5)
        ax.annotate(str(i), s, textcoords="offset points", xytext=(6, 4),
                    fontsize=8, color="white", zorder=6)


# ----------------------------------------------------------------------
class SimulationView:
    def __init__(self, A, B, outline, bbox, params, speed=4, speed_max=20):
        self.A, self.B = A, B
        self._outline, self._bbox = outline, bbox

        self.on_start_pause = self.on_step = self.on_reset = lambda: None
        self.on_batch = self.on_apply = lambda: None
        self.on_mode = self.on_traj = self.on_profile = lambda key: None
        self.on_speed = lambda v: None
        self.on_toggle = lambda: None

        self.fig = plt.figure(figsize=(14.2, 8.0), facecolor=THEME["bg"])
        try:
            self.fig.canvas.manager.set_window_title(
                "Размещение датчиков обнаружения БПЛА — MVC")
        except Exception:
            pass
        self.ax = self.fig.add_axes([0.045, 0.07, 0.585, 0.87])

        self._blit_bg = None
        self._flown = self._uav = self._hud = None
        self._cur_traj = None
        self._panel_bg()
        self._textboxes = {}
        self._suppress = False
        self._build_controls(params, speed, speed_max)
        self.draw_clear()

    # ----- панель -----
    def _panel_bg(self):
        p = self.fig.add_axes([0.655, 0.02, 0.335, 0.96]); p.axis("off")
        p.add_patch(FancyBboxPatch((0, 0), 1, 1,
                    boxstyle="round,pad=0,rounding_size=0.02", fc=THEME["panel"],
                    ec=THEME["grid"], lw=1.0, transform=p.transAxes, clip_on=False))

    def _header(self, x, y, text):
        self.fig.text(x, y, text, color=THEME["accent"], fontsize=10,
                      fontweight="bold")

    def _labeled_box(self, x, y, w, h, name, label, value):
        self.fig.text(x, y + h + 0.004, label, color=THEME["muted"], fontsize=8)
        tb = TextBox(self.fig.add_axes([x, y, w, h]), "", initial=str(value),
                     color="#e6edf3", hovercolor="#ffffff")
        try:
            tb.text_disp.set_color("#10202f")
        except Exception:
            pass
        tb.on_submit(lambda txt: None if self._suppress else self.on_apply())
        self._textboxes[name] = tb

    def _style_button(self, btn, color):
        btn.color = color; btn.hovercolor = THEME["accent"]
        btn.label.set_color("white"); btn.label.set_fontsize(9)
        btn.ax.set_facecolor(color)

    def _style_radio(self, radio):
        for lab in radio.labels:
            lab.set_color(THEME["text"]); lab.set_fontsize(9)
        for sp in radio.ax.spines.values():
            sp.set_color(THEME["grid"])

    def _build_controls(self, params, speed, speed_max):
        self._header(0.672, 0.952, "ПАРАМЕТРЫ  (Enter — пересчёт)")
        colx, bw, bh = [0.702, 0.858], [0.083, 0.083], 0.028
        rows = [0.900, 0.832, 0.764, 0.696]
        for i, (name, label, _t) in enumerate(PARAM_SPECS):
            r, c = divmod(i, 2)
            self._labeled_box(colx[c], rows[r], bw[c], bh, name, label,
                              getattr(params, name))

        self._header(0.672, 0.660, "РЕЖИМ ОПТИМИЗАЦИИ")
        self._mode_keys = list(MODE_LABELS)
        self.radio_mode = RadioButtons(
            self.fig.add_axes([0.700, 0.520, 0.140, 0.130], facecolor=THEME["panel"]),
            [MODE_LABELS[k] for k in self._mode_keys],
            active=self._mode_keys.index(params.mode))
        self._style_radio(self.radio_mode)
        self.radio_mode.on_clicked(self._mode_clicked)

        self._header(0.853, 0.660, "ДВИЖЕНИЕ")
        self._traj_keys = list(TRAJ_LABELS)
        self.radio_traj = RadioButtons(
            self.fig.add_axes([0.856, 0.600, 0.120, 0.050], facecolor=THEME["panel"]),
            [TRAJ_LABELS[k] for k in self._traj_keys],
            active=self._traj_keys.index(params.traj_model))
        self._style_radio(self.radio_traj)
        self.radio_traj.on_clicked(self._traj_clicked)

        self._header(0.853, 0.582, "РАЗБРОС")
        self._prof_keys = list(MOTION_LABELS)
        self.radio_prof = RadioButtons(
            self.fig.add_axes([0.856, 0.492, 0.120, 0.082], facecolor=THEME["panel"]),
            [MOTION_LABELS[k] for k in self._prof_keys],
            active=self._prof_keys.index(params.motion_profile))
        self._style_radio(self.radio_prof)
        self.radio_prof.on_clicked(self._prof_clicked)

        self._header(0.672, 0.470, "ПОКАЗ")
        self.checks = CheckButtons(
            self.fig.add_axes([0.700, 0.402, 0.150, 0.060], facecolor=THEME["panel"]),
            ["тепловая карта", "частые маршруты"], [False, False])
        for lab in self.checks.labels:
            lab.set_color(THEME["text"]); lab.set_fontsize(9)
        self.checks.on_clicked(lambda label: self.on_toggle())

        self._header(0.672, 0.378, "СКОРОСТЬ БПЛА")
        self.slider_speed = Slider(
            self.fig.add_axes([0.702, 0.352, 0.273, 0.020], facecolor=THEME["grid"]),
            "", 1, speed_max, valinit=speed, valstep=1, color=THEME["accent"])
        self.slider_speed.valtext.set_color(THEME["text"])
        self.slider_speed.on_changed(lambda v: self.on_speed(int(v)))

        self.btn_apply = Button(self.fig.add_axes([0.700, 0.288, 0.130, 0.050]), "Применить")
        self.btn_reset = Button(self.fig.add_axes([0.846, 0.288, 0.130, 0.050]), "Сброс")
        self.btn_run = Button(self.fig.add_axes([0.700, 0.228, 0.083, 0.050]), "▶ Пуск")
        self.btn_step = Button(self.fig.add_axes([0.792, 0.228, 0.070, 0.050]), "Шаг")
        self.btn_batch = Button(self.fig.add_axes([0.872, 0.228, 0.103, 0.050]), "Пакетно")
        self._style_button(self.btn_apply, THEME["accent2"])
        self._style_button(self.btn_reset, "#3a4760")
        self._style_button(self.btn_run, THEME["ok"])
        self._style_button(self.btn_step, "#3a4760")
        self._style_button(self.btn_batch, THEME["accent2"])
        self.btn_apply.on_clicked(lambda e: self.on_apply())
        self.btn_reset.on_clicked(lambda e: self.on_reset())
        self.btn_run.on_clicked(lambda e: self.on_start_pause())
        self.btn_step.on_clicked(lambda e: self.on_step())
        self.btn_batch.on_clicked(lambda e: self.on_batch())

        self._header(0.672, 0.196, "ПОКАЗАТЕЛИ")
        axm = self.fig.add_axes([0.672, 0.02, 0.312, 0.165]); axm.axis("off")
        self._metrics_text = axm.text(0.0, 1.0, "", va="top", ha="left",
                                      fontsize=8.3, family="monospace",
                                      color=THEME["text"], transform=axm.transAxes)

    def _mode_clicked(self, label):
        for k, v in MODE_LABELS.items():
            if v == label:
                self.on_mode(k); return

    def _traj_clicked(self, label):
        for k, v in TRAJ_LABELS.items():
            if v == label:
                self.on_traj(k); return

    def _prof_clicked(self, label):
        for k, v in MOTION_LABELS.items():
            if v == label:
                self.on_profile(k); return

    # ----- API для контроллера -----
    def set_callbacks(self, **cbs):
        for name, fn in cbs.items():
            setattr(self, name, fn)

    def get_toggles(self):
        st = self.checks.get_status()
        return dict(show_heat=st[0], show_paths=st[1])

    def set_running_label(self, running):
        self.btn_run.label.set_text("⏸ Пауза" if running else "▶ Пуск")
        self.fig.canvas.draw_idle()

    def get_param_values(self):
        out = {}
        for name, _l, typ in PARAM_SPECS:
            raw = self._textboxes[name].text.strip().replace(",", ".")
            out[name] = typ(float(raw)) if typ is int else typ(raw)
        return out

    def set_param_values(self, params):
        self._suppress = True
        try:
            for name, _l, _t in PARAM_SPECS:
                self._textboxes[name].set_val(str(getattr(params, name)))
        finally:
            self._suppress = False

    def update_geometry(self, A, B, outline, bbox):
        self.A, self.B = A, B
        self._outline, self._bbox = outline, bbox

    # ----- полная отрисовка (idle / шаг / пакетно) -----
    def _compose(self, sensors, R, paths, density, show_paths, show_heat,
                 cur=None, j=None):
        self.ax.clear()
        draw_static(self.ax, self.A, self.B, self._outline, self._bbox)
        if show_heat:
            draw_density(self.ax, *(density if density else (None, None)))
        if show_paths and paths:
            draw_probable(self.ax, paths, show_labels=(cur is None))
        if cur is not None and len(cur):
            jj = int(np.clip(j, 0, len(cur) - 1))
            self.ax.plot(cur[:, 0], cur[:, 1], color=THEME["accent"], lw=1.4,
                         alpha=0.45, zorder=7)
            self.ax.plot(cur[:jj + 1, 0], cur[:jj + 1, 1], color=THEME["accent"],
                         lw=2.6, zorder=8)
            self.ax.plot(cur[jj, 0], cur[jj, 1], "o", color=THEME["warn"], ms=10,
                         mec="white", mew=1.0, zorder=9)
        draw_sensors(self.ax, sensors, R)

    def draw_clear(self, paths=None, density=None, show_paths=False,
                   show_heat=False, title="Готово. «Пуск», «Шаг» или «Пакетно»."):
        self._compose([], 0, paths, density, show_paths, show_heat)
        self.ax.set_title(title, fontsize=10, color=THEME["text"])
        self.fig.canvas.draw_idle()

    def draw_iterative_frame(self, traj, sensors, j, R, title, paths=None,
                             density=None, show_paths=False, show_heat=False):
        self._compose(sensors, R, paths, density, show_paths, show_heat, traj, j)
        self.ax.set_title(title, fontsize=10, color=THEME["text"])
        self.fig.canvas.draw_idle()

    def draw_batch(self, paths, sensors, R, title, density=None,
                   show_paths=False, show_heat=False):
        self._compose(sensors, R, paths, density, show_paths, show_heat)
        if show_paths and paths:
            self.ax.legend(loc="upper right", fontsize=7, framealpha=0.25,
                           labelcolor=THEME["text"], facecolor=THEME["panel"])
        self.ax.set_title(title, fontsize=10, color=THEME["text"])
        self.fig.canvas.draw_idle()

    # ----- быстрая анимация (блиттинг) -----
    def setup_flight(self, traj, sensors, R, paths, density, show_paths,
                     show_heat, title):
        self._compose(sensors, R, paths, density, show_paths, show_heat)
        self.ax.set_title(title, fontsize=10, color=THEME["text"])
        self._cur_traj = traj
        self._flown, = self.ax.plot([], [], color=THEME["accent"], lw=2.6,
                                    zorder=8, animated=True)
        self._uav, = self.ax.plot([], [], "o", color=THEME["warn"], ms=10,
                                  mec="white", mew=1.0, zorder=9, animated=True)
        # слабый контур всего маршрута (статично, в фоне)
        self.ax.plot(traj[:, 0], traj[:, 1], color=THEME["accent"], lw=1.2,
                     alpha=0.35, zorder=7)
        self._hud = self.ax.text(0.012, 0.97, "", transform=self.ax.transAxes,
                                 va="top", ha="left", fontsize=9, color=THEME["text"],
                                 zorder=10, animated=True,
                                 bbox=dict(boxstyle="round", fc=THEME["panel"],
                                           ec=THEME["grid"], alpha=0.7))
        self.fig.canvas.draw()
        try:
            self._blit_bg = self.fig.canvas.copy_from_bbox(self.ax.bbox)
        except Exception:
            self._blit_bg = None

    def update_flight(self, j, hud):
        cur = self._cur_traj
        if cur is None:
            return
        jj = int(np.clip(j, 0, len(cur) - 1))
        self._flown.set_data(cur[:jj + 1, 0], cur[:jj + 1, 1])
        self._uav.set_data([cur[jj, 0]], [cur[jj, 1]])
        self._hud.set_text(hud)
        canvas = self.fig.canvas
        if self._blit_bg is not None:
            canvas.restore_region(self._blit_bg)
            self.ax.draw_artist(self._flown)
            self.ax.draw_artist(self._uav)
            self.ax.draw_artist(self._hud)
            canvas.blit(self.ax.bbox)
        else:
            canvas.draw_idle()

    def set_metrics(self, lines):
        self._metrics_text.set_text("\n".join(lines))
        self.fig.canvas.draw_idle()

    def flash_title(self, text, color=None):
        self.ax.set_title(text, fontsize=10, color=color or THEME["warn"])
        self.fig.canvas.draw_idle()

    def show(self):
        plt.show()
