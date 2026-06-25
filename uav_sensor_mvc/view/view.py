# -*- coding: utf-8 -*-
"""
VIEW · Слой визуализации на нативных виджетах matplotlib (тёмная тема).

Слои отображения (раздельные чекбоксы ПОКАЗ, по умолчанию выключены):
  * «тепловая карта»  — только плотность маршрутов (ярче = чаще) + легенда;
  * «10 маршрутов»    — 10 наиболее вероятных маршрутов (дуги или петли);
  * «веер (шаг угла)» — все возможные маршруты по шагу (предельные — пунктир).
Любые слои комбинируются. Маршрут текущего полёта и контур коридора видны всегда.

Масштаб подгоняется под коридор движения; анимация — через блиттинг.
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

HEAT_CMAP = "turbo"           # ярче на тёмном фоне, чем magma


# ----------------------------------------------------------------------
# Помощники отрисовки сцены
# ----------------------------------------------------------------------
def fit_axes(ax, bbox, margin=1.14):
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
                    lw=1.1, alpha=0.6, zorder=1)
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
    """Тепловая карта плотности. Слабые значения подняты гаммой и яркой палитрой,
    чтобы карта была видна на тёмном фоне."""
    if H is None:
        return
    disp = np.power(np.clip(H, 0, 1), 0.45)        # подъём слабых значений
    ax.imshow(disp, origin="lower", extent=extent, cmap=HEAT_CMAP, alpha=0.85,
              zorder=0, interpolation="bilinear", aspect="auto", vmin=0, vmax=1)


def draw_probable(ax, paths, show_labels=True):
    """Линии маршрутов: вес -> цвет/толщина; предельные (is_extreme) -> пунктир."""
    n_lab = 0
    for pth in paths:
        w = pth["weight"]
        if pth.get("is_extreme"):
            ax.plot(pth["traj"][:, 0], pth["traj"][:, 1], color=THEME["warn"],
                    lw=1.7, alpha=0.95, ls=(0, (7, 4)), zorder=3,
                    label=(f"предел {pth.get('label')}") if show_labels else None)
        else:
            lab = None
            if show_labels and n_lab < 5:
                lab = pth.get("label"); n_lab += 1
            ax.plot(pth["traj"][:, 0], pth["traj"][:, 1],
                    color=cm.viridis(0.30 + 0.65 * w), lw=1.2 + 2.8 * w,
                    alpha=0.55 + 0.4 * w, label=lab, zorder=2)


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

        self.fig = plt.figure(figsize=(13.6, 7.7), facecolor=THEME["bg"])
        try:
            self.fig.canvas.manager.set_window_title(
                "Размещение датчиков обнаружения БПЛА — MVC")
        except Exception:
            pass
        self.ax = self.fig.add_axes([0.045, 0.07, 0.55, 0.87])
        self._build_colorbar()

        self._blit_bg = None
        self._flown = self._uav = self._hud = None
        self._cur_traj = None
        self._panel_bg()
        self._textboxes = {}
        self._suppress = False
        self._build_controls(params, speed, speed_max)
        try:
            self.fig.canvas.mpl_connect("resize_event", self._on_resize)
        except Exception:
            pass
        self.draw_clear()

    def _on_resize(self, _event):
        self._blit_bg = None        # фон блита устарел -> пересоберём при след. кадре

    # ----- легенда тепловой карты -----
    def _build_colorbar(self):
        self.ax_cbar = self.fig.add_axes([0.602, 0.40, 0.014, 0.30])
        grad = np.linspace(0, 1, 256).reshape(-1, 1)
        self.ax_cbar.imshow(grad, cmap=HEAT_CMAP, aspect="auto", origin="lower",
                            extent=[0, 1, 0, 1])
        self.ax_cbar.set_xticks([])
        self.ax_cbar.set_yticks([0.04, 0.96])
        self.ax_cbar.set_yticklabels(["редко", "часто"], fontsize=7,
                                     color=THEME["muted"])
        self.ax_cbar.tick_params(length=0)
        self.ax_cbar.set_title("плотность", fontsize=7, color=THEME["muted"], pad=3)
        for sp in self.ax_cbar.spines.values():
            sp.set_color(THEME["grid"])
        self.ax_cbar.set_visible(False)

    # ----- панель -----
    def _panel_bg(self):
        p = self.fig.add_axes([0.635, 0.015, 0.355, 0.97]); p.axis("off")
        p.add_patch(FancyBboxPatch((0, 0), 1, 1,
                    boxstyle="round,pad=0,rounding_size=0.02", fc=THEME["panel"],
                    ec=THEME["grid"], lw=1.0, transform=p.transAxes, clip_on=False))

    def _header(self, x, y, text):
        self.fig.text(x, y, text, color=THEME["accent"], fontsize=9.5,
                      fontweight="bold")

    def _labeled_box(self, x, y, w, h, name, label, value):
        self.fig.text(x, y + h + 0.003, label, color=THEME["muted"], fontsize=7.5)
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
            lab.set_color(THEME["text"]); lab.set_fontsize(8.5)
        for sp in radio.ax.spines.values():
            sp.set_color(THEME["grid"])

    def _build_controls(self, params, speed, speed_max):
        self._header(0.650, 0.962, "ПАРАМЕТРЫ  (Enter — пересчёт)")
        colx, bw, bh = [0.680, 0.838], [0.082, 0.082], 0.026
        rows = [0.918, 0.856, 0.794, 0.732]
        for i, (name, label, _t) in enumerate(PARAM_SPECS):
            r, c = divmod(i, 2)
            self._labeled_box(colx[c], rows[r], bw[c], bh, name, label,
                              getattr(params, name))

        self._header(0.650, 0.700, "РЕЖИМ ОПТИМ.")
        self._mode_keys = list(MODE_LABELS)
        self.radio_mode = RadioButtons(
            self.fig.add_axes([0.678, 0.585, 0.140, 0.108], facecolor=THEME["panel"]),
            [MODE_LABELS[k] for k in self._mode_keys],
            active=self._mode_keys.index(params.mode))
        self._style_radio(self.radio_mode)
        self.radio_mode.on_clicked(self._mode_clicked)

        self._header(0.838, 0.700, "ДВИЖЕНИЕ")
        self._traj_keys = list(TRAJ_LABELS)
        self.radio_traj = RadioButtons(
            self.fig.add_axes([0.840, 0.648, 0.135, 0.045], facecolor=THEME["panel"]),
            [TRAJ_LABELS[k] for k in self._traj_keys],
            active=self._traj_keys.index(params.traj_model))
        self._style_radio(self.radio_traj)
        self.radio_traj.on_clicked(self._traj_clicked)

        self._header(0.838, 0.628, "РАЗБРОС")
        self._prof_keys = list(MOTION_LABELS)
        self.radio_prof = RadioButtons(
            self.fig.add_axes([0.840, 0.520, 0.135, 0.100], facecolor=THEME["panel"]),
            [MOTION_LABELS[k] for k in self._prof_keys],
            active=self._prof_keys.index(params.motion_profile))
        self._style_radio(self.radio_prof)
        self.radio_prof.on_clicked(self._prof_clicked)

        self._header(0.650, 0.558, "ПОКАЗ")
        self.checks = CheckButtons(
            self.fig.add_axes([0.678, 0.448, 0.150, 0.103], facecolor=THEME["panel"]),
            ["тепловая карта", "10 маршрутов", "веер (шаг угла)"],
            [False, False, False])
        for lab in self.checks.labels:
            lab.set_color(THEME["text"]); lab.set_fontsize(8.5)
        self.checks.on_clicked(lambda label: self.on_toggle())

        self._header(0.650, 0.418, "СКОРОСТЬ")
        self.slider_speed = Slider(
            self.fig.add_axes([0.700, 0.392, 0.275, 0.018], facecolor=THEME["grid"]),
            "", 1, speed_max, valinit=speed, valstep=1, color=THEME["accent"])
        self.slider_speed.valtext.set_color(THEME["text"])
        self.slider_speed.on_changed(lambda v: self.on_speed(int(v)))

        self.btn_apply = Button(self.fig.add_axes([0.650, 0.330, 0.158, 0.046]), "Применить")
        self.btn_reset = Button(self.fig.add_axes([0.818, 0.330, 0.158, 0.046]), "Сброс")
        self.btn_run = Button(self.fig.add_axes([0.650, 0.276, 0.100, 0.046]), "Пуск")
        self.btn_step = Button(self.fig.add_axes([0.758, 0.276, 0.085, 0.046]), "Шаг")
        self.btn_batch = Button(self.fig.add_axes([0.851, 0.276, 0.125, 0.046]), "Пакетно")
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

        self._header(0.650, 0.246, "ПОКАЗАТЕЛИ")
        axm = self.fig.add_axes([0.650, 0.02, 0.33, 0.215]); axm.axis("off")
        self._metrics_text = axm.text(0.0, 1.0, "", va="top", ha="left",
                                      fontsize=8.0, family="monospace",
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
        return dict(show_heat=st[0], show_freq=st[1], show_fan=st[2])

    def set_running_label(self, running):
        self.btn_run.label.set_text("Пауза" if running else "Пуск")
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

    # ----- полная отрисовка -----
    def _compose(self, sensors, R, freq, fan, density, t, cur=None, j=None):
        self.ax.clear()
        draw_static(self.ax, self.A, self.B, self._outline, self._bbox)
        self.ax_cbar.set_visible(bool(t["show_heat"]))
        if t["show_heat"]:
            draw_density(self.ax, *(density if density else (None, None)))
        if t["show_fan"] and fan:
            draw_probable(self.ax, fan, show_labels=(cur is None))
        if t["show_freq"] and freq:
            draw_probable(self.ax, freq, show_labels=(cur is None))
        if cur is not None and len(cur):
            jj = int(np.clip(j, 0, len(cur) - 1))
            self.ax.plot(cur[:, 0], cur[:, 1], color=THEME["accent"], lw=1.4,
                         alpha=0.45, zorder=7)
            self.ax.plot(cur[:jj + 1, 0], cur[:jj + 1, 1], color=THEME["accent"],
                         lw=2.6, zorder=8)
            self.ax.plot(cur[jj, 0], cur[jj, 1], "o", color=THEME["warn"], ms=10,
                         mec="white", mew=1.0, zorder=9)
        draw_sensors(self.ax, sensors, R)

    def draw_clear(self, freq=None, fan=None, density=None, toggles=None,
                   title="Готово. «Пуск», «Шаг» или «Пакетно»."):
        t = toggles or dict(show_heat=False, show_freq=False, show_fan=False)
        self._compose([], 0, freq, fan, density, t)
        self.ax.set_title(title, fontsize=10, color=THEME["text"])
        self.fig.canvas.draw_idle()

    def draw_static_frame(self, sensors, R, freq, fan, density, toggles, title):
        self._compose(sensors, R, freq, fan, density, toggles)
        if (toggles["show_freq"] and freq) or (toggles["show_fan"] and fan):
            self.ax.legend(loc="upper right", fontsize=7, framealpha=0.25,
                           labelcolor=THEME["text"], facecolor=THEME["panel"])
        self.ax.set_title(title, fontsize=10, color=THEME["text"])
        self.fig.canvas.draw_idle()

    # ----- быстрая анимация (блиттинг) -----
    def setup_flight(self, traj, sensors, R, freq, fan, density, toggles, title):
        self._compose(sensors, R, freq, fan, density, toggles)
        self.ax.set_title(title, fontsize=10, color=THEME["text"])
        self._cur_traj = traj
        self.ax.plot(traj[:, 0], traj[:, 1], color=THEME["accent"], lw=1.2,
                     alpha=0.35, zorder=7)
        self._flown, = self.ax.plot([], [], color=THEME["accent"], lw=2.6,
                                    zorder=8, animated=True)
        self._uav, = self.ax.plot([], [], "o", color=THEME["warn"], ms=10,
                                  mec="white", mew=1.0, zorder=9, animated=True)
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

    def process_pending(self):
        """Принудительно отрисовать отложенные изменения (общий метод с Qt-View)."""
        self.fig.canvas.draw_idle()
        try:
            self.fig.canvas.flush_events()
        except Exception:
            pass

    def flash_title(self, text, color=None):
        self.ax.set_title(text, fontsize=10, color=color or THEME["warn"])
        self.fig.canvas.draw_idle()

    def show(self):
        plt.show()
