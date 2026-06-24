# -*- coding: utf-8 -*-
"""
VIEW · Слой визуализации на нативных виджетах matplotlib (тёмная тема).

Отрисовывает:
  * карту с авто-масштабом под эллипс достижимости (меняется при правке L_max/|AB|);
  * тепловую карту плотности маршрутов;
  * веер вероятных путей (крайние — пунктиром);
  * зоны датчиков и положение БПЛА;
  * панель РЕДАКТИРУЕМЫХ параметров (правка + Enter перезапускает расчёт),
    выбор режима оптимизации и модели движения, скорость, кнопки, показатели.

Зависимости: только matplotlib (+ numpy). Бизнес-логики не содержит.
"""
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from matplotlib.patches import Circle, Ellipse, FancyBboxPatch
from matplotlib.widgets import Button, RadioButtons, Slider, TextBox

from config import MODE_LABELS, TRAJ_LABELS, THEME


# Описание редактируемых параметров: (атрибут, подпись, тип)
PARAM_SPECS = [
    ("ab_distance",    "|AB|, км",   float),
    ("L_max",          "Запас, км",  float),
    ("N",              "Датчиков N", int),
    ("R",              "Радиус R",   float),
    ("k",              "Кратность k", int),
    ("L_seg",          "Сегментов",  int),
    ("angle_step_deg", "Шаг угла °", float),
    ("T",              "Итераций T", int),
]


# ----------------------------------------------------------------------
# Переиспользуемые помощники отрисовки сцены
# ----------------------------------------------------------------------
def draw_static(ax, geom, A, B, xlim, ylim):
    ax.set_facecolor(THEME["axes"])
    ax.add_patch(Ellipse(geom["center"], 2 * geom["a"], 2 * geom["b"],
                         angle=np.degrees(geom["angle"]), fill=False,
                         ls="--", ec=THEME["ellipse"], lw=1.2, alpha=0.9))
    for P, name in ((A, "A — старт"), (B, "B — цель")):
        ax.plot(*P, "s", color=THEME["text"], ms=8, mec=THEME["accent"], mew=1.5)
        ax.annotate(name, P, textcoords="offset points", xytext=(8, 8),
                    fontsize=10, fontweight="bold", color=THEME["text"])
    ax.set_xlim(*xlim); ax.set_ylim(*ylim)
    ax.set_aspect("equal")
    ax.grid(alpha=0.15, color=THEME["grid"])
    ax.tick_params(colors=THEME["muted"], labelsize=8)
    for sp in ax.spines.values():
        sp.set_color(THEME["grid"])
    ax.set_xlabel("X, км", color=THEME["muted"], fontsize=9)
    ax.set_ylabel("Y, км", color=THEME["muted"], fontsize=9)


def draw_density(ax, H, extent):
    if H is None:
        return
    ax.imshow(H, origin="lower", extent=extent, cmap="magma",
              alpha=0.38, zorder=0, interpolation="bilinear", aspect="auto")


def draw_probable(ax, paths, show_labels=True):
    """Веер вероятных путей: вес -> цвет/толщина (солидные); предельные дуги
    (граница запаса хода) -> яркий красный пунктир."""
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
                    color=cm.viridis(0.30 + 0.65 * w),
                    lw=1.0 + 3.2 * w, alpha=0.5 + 0.45 * w,
                    label=lab, zorder=2)


def draw_sensors(ax, sensors, R):
    for i, s in enumerate(sensors, 1):
        ax.add_patch(Circle(s, R, fill=True, fc=THEME["ok"], alpha=0.13,
                            ec=THEME["ok"], lw=1.2, zorder=3))
        ax.plot(*s, "o", color=THEME["ok"], ms=7, mec="white", mew=0.8, zorder=4)
        ax.annotate(str(i), s, textcoords="offset points", xytext=(6, 4),
                    fontsize=8, color="white", zorder=5)


# ----------------------------------------------------------------------
# Интерактивное представление
# ----------------------------------------------------------------------
class SimulationView:
    def __init__(self, geom, A, B, xlim, ylim, params, speed=4, speed_max=20):
        self.geom, self.A, self.B = geom, A, B
        self.xlim, self.ylim = xlim, ylim

        # обработчики (заполняет контроллер)
        self.on_start_pause = lambda: None
        self.on_step = lambda: None
        self.on_reset = lambda: None
        self.on_batch = lambda: None
        self.on_apply = lambda: None
        self.on_mode = lambda key: None
        self.on_traj = lambda key: None
        self.on_speed = lambda val: None

        self.fig = plt.figure(figsize=(14.2, 8.0), facecolor=THEME["bg"])
        try:
            self.fig.canvas.manager.set_window_title(
                "Размещение датчиков обнаружения БПЛА — MVC")
        except Exception:
            pass

        self.ax = self.fig.add_axes([0.045, 0.07, 0.585, 0.87])
        self._panel_bg()
        self._textboxes = {}
        self._suppress = False           # подавление submit при программной правке
        self._build_controls(params, speed, speed_max)
        self.draw_clear()

    # ------------------------------------------------------------------
    def _panel_bg(self):
        p = self.fig.add_axes([0.655, 0.02, 0.335, 0.96]); p.axis("off")
        p.add_patch(FancyBboxPatch((0.0, 0.0), 1.0, 1.0,
                    boxstyle="round,pad=0.0,rounding_size=0.02",
                    fc=THEME["panel"], ec=THEME["grid"], lw=1.0,
                    transform=p.transAxes, clip_on=False))

    def _header(self, x, y, text):
        self.fig.text(x, y, text, color=THEME["accent"], fontsize=10,
                      fontweight="bold")

    def _labeled_box(self, x, y, w, h, name, label, value):
        self.fig.text(x, y + h + 0.004, label, color=THEME["muted"], fontsize=8)
        axb = self.fig.add_axes([x, y, w, h])
        tb = TextBox(axb, "", initial=str(value), color="#e6edf3",
                     hovercolor="#ffffff")
        tb.label.set_color(THEME["text"])
        try:
            tb.text_disp.set_color("#10202f")
        except Exception:
            pass
        tb.on_submit(lambda txt: None if self._suppress else self.on_apply())
        self._textboxes[name] = tb

    def _style_button(self, btn, color):
        btn.color = color
        btn.hovercolor = THEME["accent"]
        btn.label.set_color("white")
        btn.label.set_fontsize(9)
        btn.ax.set_facecolor(color)

    def _build_controls(self, params, speed, speed_max):
        self._header(0.672, 0.945, "ПАРАМЕТРЫ МОДЕЛИ  (Enter — пересчёт)")
        colx = [0.700, 0.855]; bw = [0.085, 0.085]; bh = 0.030
        rows_y = [0.890, 0.820, 0.750, 0.680]
        for i, (name, label, _typ) in enumerate(PARAM_SPECS):
            r, c = divmod(i, 2)
            self._labeled_box(colx[c], rows_y[r], bw[c], bh, name, label,
                              getattr(params, name))

        # режим оптимизации + модель движения (рядом)
        self._header(0.672, 0.635, "РЕЖИМ ОПТИМИЗАЦИИ")
        ax_mode = self.fig.add_axes([0.700, 0.500, 0.150, 0.120],
                                    facecolor=THEME["panel"])
        self._mode_keys = list(MODE_LABELS)
        self.radio_mode = RadioButtons(
            ax_mode, [MODE_LABELS[k] for k in self._mode_keys],
            active=self._mode_keys.index(params.mode))
        self._style_radio(self.radio_mode)
        self.radio_mode.on_clicked(self._mode_clicked)

        self._header(0.860, 0.635, "ДВИЖЕНИЕ")
        ax_traj = self.fig.add_axes([0.865, 0.520, 0.110, 0.100],
                                    facecolor=THEME["panel"])
        self._traj_keys = list(TRAJ_LABELS)
        self.radio_traj = RadioButtons(
            ax_traj, [TRAJ_LABELS[k] for k in self._traj_keys],
            active=self._traj_keys.index(params.traj_model))
        self._style_radio(self.radio_traj)
        self.radio_traj.on_clicked(self._traj_clicked)

        # скорость
        self.fig.text(0.672, 0.470, "СКОРОСТЬ БПЛА", color=THEME["accent"],
                      fontsize=10, fontweight="bold")
        ax_speed = self.fig.add_axes([0.700, 0.435, 0.275, 0.022],
                                     facecolor=THEME["grid"])
        self.slider_speed = Slider(ax_speed, "", 1, speed_max, valinit=speed,
                                   valstep=1, color=THEME["accent"])
        self.slider_speed.valtext.set_color(THEME["text"])
        self.slider_speed.on_changed(lambda v: self.on_speed(int(v)))

        # кнопки
        self.btn_apply = Button(self.fig.add_axes([0.700, 0.360, 0.130, 0.050]),
                                "Применить")
        self.btn_reset = Button(self.fig.add_axes([0.845, 0.360, 0.130, 0.050]),
                                "Сброс")
        self.btn_run = Button(self.fig.add_axes([0.700, 0.298, 0.083, 0.052]),
                              "▶ Пуск")
        self.btn_step = Button(self.fig.add_axes([0.792, 0.298, 0.070, 0.052]),
                               "Шаг")
        self.btn_batch = Button(self.fig.add_axes([0.872, 0.298, 0.103, 0.052]),
                                "Пакетно")
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

        # показатели
        self._header(0.672, 0.262, "ПОКАЗАТЕЛИ")
        ax_m = self.fig.add_axes([0.672, 0.03, 0.31, 0.225]); ax_m.axis("off")
        self._metrics_text = ax_m.text(
            0.0, 1.0, "", va="top", ha="left", fontsize=8.5,
            family="monospace", color=THEME["text"], transform=ax_m.transAxes)

    def _style_radio(self, radio):
        for lab in radio.labels:
            lab.set_color(THEME["text"]); lab.set_fontsize(9)
        for sp in radio.ax.spines.values():
            sp.set_color(THEME["grid"])

    # обработчики виджетов -> ключи
    def _mode_clicked(self, label):
        for k, v in MODE_LABELS.items():
            if v == label:
                self.on_mode(k); return

    def _traj_clicked(self, label):
        for k, v in TRAJ_LABELS.items():
            if v == label:
                self.on_traj(k); return

    # ------------------------------------------------------------------
    def set_callbacks(self, **cbs):
        for name, fn in cbs.items():
            setattr(self, name, fn)

    def set_running_label(self, running):
        self.btn_run.label.set_text("⏸ Пауза" if running else "▶ Пуск")
        self.fig.canvas.draw_idle()

    def get_param_values(self):
        """Считать значения из полей. Бросает ValueError при некорректном вводе."""
        out = {}
        for name, label, typ in PARAM_SPECS:
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

    def update_geometry(self, geom, A, B, xlim, ylim):
        self.geom, self.A, self.B = geom, A, B
        self.xlim, self.ylim = xlim, ylim

    # ------------------------------------------------------------------
    # Отрисовка
    # ------------------------------------------------------------------
    def draw_clear(self, paths=None, title="Готово. Правьте параметры и нажмите «Пуск» или «Пакетно»."):
        self.ax.clear()
        draw_static(self.ax, self.geom, self.A, self.B, self.xlim, self.ylim)
        if paths:
            draw_probable(self.ax, paths)
            self.ax.legend(loc="upper right", fontsize=7, framealpha=0.25,
                           labelcolor=THEME["text"], facecolor=THEME["panel"])
        self.ax.set_title(title, fontsize=10, color=THEME["text"])
        self.fig.canvas.draw_idle()

    def draw_iterative_frame(self, traj, sensors, j, R, title,
                             paths=None, density=None):
        self.ax.clear()
        draw_static(self.ax, self.geom, self.A, self.B, self.xlim, self.ylim)
        if density is not None:
            draw_density(self.ax, *density)
        if paths:
            draw_probable(self.ax, paths, show_labels=False)
        if traj is not None and len(traj):
            j = int(np.clip(j, 0, len(traj) - 1))
            self.ax.plot(traj[:, 0], traj[:, 1], color=THEME["accent"],
                         lw=1.4, alpha=0.45, zorder=5)
            self.ax.plot(traj[:j + 1, 0], traj[:j + 1, 1],
                         color=THEME["accent"], lw=2.6, zorder=6)
            self.ax.plot(traj[j, 0], traj[j, 1], "o", color=THEME["warn"],
                         ms=10, mec="white", mew=1.0, zorder=7)
        draw_sensors(self.ax, sensors, R)
        self.ax.set_title(title, fontsize=10, color=THEME["text"])
        self.fig.canvas.draw_idle()

    def draw_batch(self, paths, sensors, R, title, density=None):
        self.ax.clear()
        draw_static(self.ax, self.geom, self.A, self.B, self.xlim, self.ylim)
        if density is not None:
            draw_density(self.ax, *density)
        draw_probable(self.ax, paths)
        draw_sensors(self.ax, sensors, R)
        self.ax.set_title(title, fontsize=10, color=THEME["text"])
        if paths:
            self.ax.legend(loc="upper right", fontsize=7, framealpha=0.25,
                           labelcolor=THEME["text"], facecolor=THEME["panel"])
        self.fig.canvas.draw_idle()

    def set_metrics(self, lines):
        self._metrics_text.set_text("\n".join(lines))
        self.fig.canvas.draw_idle()

    def flash_title(self, text, color=None):
        self.ax.set_title(text, fontsize=10, color=color or THEME["warn"])
        self.fig.canvas.draw_idle()

    def show(self):
        plt.show()
