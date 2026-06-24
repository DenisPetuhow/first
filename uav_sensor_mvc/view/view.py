# -*- coding: utf-8 -*-
"""
VIEW · Слой визуализации на нативных виджетах matplotlib.

Отрисовывает сцену (эллипс достижимости, точки A/B, зоны датчиков, маршрут,
положение БПЛА), панель управления (режим оптимизации, модель движения,
скорость, кнопки) и сводные показатели. Не содержит бизнес-логики: получает
готовые данные и вызывает зарегистрированные контроллером обработчики.

Зависимости: только matplotlib (+ numpy для типов данных).
"""
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Ellipse
from matplotlib.widgets import Button, RadioButtons, Slider

from config import MODE_LABELS, TRAJ_LABELS


# ----------------------------------------------------------------------
# Переиспользуемые помощники отрисовки (годятся и для headless-рендера)
# ----------------------------------------------------------------------
def draw_static(ax, geom, A, B):
    """Статичная сцена: эллипс достижимости и точки A, B."""
    ax.add_patch(Ellipse(geom["center"], 2 * geom["a"], 2 * geom["b"],
                         angle=np.degrees(geom["angle"]),
                         fill=False, ls="--", ec="#999", lw=1.0))
    for P, name in ((A, "A"), (B, "B")):
        ax.plot(*P, "s", color="k", ms=7)
        ax.annotate(name, P, textcoords="offset points", xytext=(6, 6),
                    fontsize=12, fontweight="bold")
    ax.set_aspect("equal")
    ax.grid(alpha=0.2)


def draw_sensors(ax, sensors, R, color="#2a6"):
    """Датчики и их зоны обнаружения (круги радиусом R)."""
    for s in sensors:
        ax.add_patch(Circle(s, R, fill=True, fc=color, alpha=0.16, ec=color, lw=1.0))
        ax.plot(*s, "o", color="#185", ms=6)


# ----------------------------------------------------------------------
# Интерактивное представление
# ----------------------------------------------------------------------
class SimulationView:
    """Окно приложения: сцена + панель управления + показатели."""

    def __init__(self, geom, A, B, mode="balanced", traj_model="arc",
                 speed=4, speed_max=20):
        self.geom, self.A, self.B = geom, A, B
        self._mode = mode
        self._traj = traj_model

        # обработчики (заполняет контроллер через set_callbacks)
        self.on_start_pause = lambda: None
        self.on_step = lambda: None
        self.on_reset = lambda: None
        self.on_batch = lambda: None
        self.on_mode = lambda key: None
        self.on_traj = lambda key: None
        self.on_speed = lambda val: None

        self.fig = plt.figure(figsize=(13.0, 7.2))
        self.fig.canvas.manager.set_window_title(
            "Размещение датчиков обнаружения БПЛА — MVC")
        self.ax = self.fig.add_axes([0.05, 0.08, 0.60, 0.86])

        self._build_controls(mode, traj_model, speed, speed_max)
        self.draw_clear()

    # ------------------------------------------------------------------
    # Панель управления
    # ------------------------------------------------------------------
    def _build_controls(self, mode, traj_model, speed, speed_max):
        # --- режим оптимизации ---
        ax_mode = self.fig.add_axes([0.70, 0.68, 0.27, 0.24])
        ax_mode.set_title("Режим оптимизации", fontsize=10, loc="left")
        self._mode_keys = list(MODE_LABELS.keys())
        mode_labels = [MODE_LABELS[k] for k in self._mode_keys]
        self.radio_mode = RadioButtons(ax_mode, mode_labels,
                                       active=self._mode_keys.index(mode))
        self.radio_mode.on_clicked(self._mode_clicked)

        # --- модель движения ---
        ax_traj = self.fig.add_axes([0.70, 0.50, 0.27, 0.14])
        ax_traj.set_title("Модель движения", fontsize=10, loc="left")
        self._traj_keys = list(TRAJ_LABELS.keys())
        traj_labels = [TRAJ_LABELS[k] for k in self._traj_keys]
        self.radio_traj = RadioButtons(ax_traj, traj_labels,
                                       active=self._traj_keys.index(traj_model))
        self.radio_traj.on_clicked(self._traj_clicked)

        # --- скорость БПЛА ---
        ax_speed = self.fig.add_axes([0.73, 0.45, 0.22, 0.03])
        self.slider_speed = Slider(ax_speed, "Скорость", 1, speed_max,
                                   valinit=speed, valstep=1)
        self.slider_speed.on_changed(lambda v: self.on_speed(int(v)))

        # --- кнопки ---
        ax_run = self.fig.add_axes([0.70, 0.36, 0.13, 0.055])
        ax_step = self.fig.add_axes([0.84, 0.36, 0.13, 0.055])
        ax_reset = self.fig.add_axes([0.70, 0.29, 0.13, 0.055])
        ax_batch = self.fig.add_axes([0.84, 0.29, 0.13, 0.055])
        self.btn_run = Button(ax_run, "▶ Пуск")
        self.btn_step = Button(ax_step, "Шаг")
        self.btn_reset = Button(ax_reset, "Сброс")
        self.btn_batch = Button(ax_batch, "Пакетно")
        self.btn_run.on_clicked(lambda e: self.on_start_pause())
        self.btn_step.on_clicked(lambda e: self.on_step())
        self.btn_reset.on_clicked(lambda e: self.on_reset())
        self.btn_batch.on_clicked(lambda e: self.on_batch())

        # --- область показателей ---
        self.ax_metrics = self.fig.add_axes([0.70, 0.03, 0.28, 0.22])
        self.ax_metrics.axis("off")
        self._metrics_text = self.ax_metrics.text(
            0.0, 1.0, "", va="top", ha="left", fontsize=9, family="monospace",
            transform=self.ax_metrics.transAxes)

    # обработчики виджетов -> чистые ключи для контроллера
    def _mode_clicked(self, label):
        for k, v in MODE_LABELS.items():
            if v == label:
                self._mode = k
                self.on_mode(k)
                return

    def _traj_clicked(self, label):
        for k, v in TRAJ_LABELS.items():
            if v == label:
                self._traj = k
                self.on_traj(k)
                return

    def set_callbacks(self, **cbs):
        """Регистрация обработчиков контроллера по именам."""
        for name, fn in cbs.items():
            setattr(self, name, fn)

    def set_running_label(self, running):
        self.btn_run.label.set_text("⏸ Пауза" if running else "▶ Пуск")
        self.fig.canvas.draw_idle()

    # ------------------------------------------------------------------
    # Отрисовка сцены
    # ------------------------------------------------------------------
    def draw_clear(self, title="Готово к запуску. Выберите режим и нажмите «Пуск»."):
        self.ax.clear()
        draw_static(self.ax, self.geom, self.A, self.B)
        self.ax.set_title(title, fontsize=10)
        self.fig.canvas.draw_idle()

    def draw_iterative_frame(self, traj, sensors, j, R, title):
        """Кадр итеративного режима: маршрут, пройденный участок, БПЛА, датчики."""
        self.ax.clear()
        draw_static(self.ax, self.geom, self.A, self.B)
        if traj is not None and len(traj):
            self.ax.plot(traj[:, 0], traj[:, 1], color="#39c", lw=1.0, alpha=0.5)
            j = int(np.clip(j, 0, len(traj) - 1))
            self.ax.plot(traj[:j + 1, 0], traj[:j + 1, 1], color="#06c", lw=2.4)
            self.ax.plot(traj[j, 0], traj[j, 1], "o", color="crimson", ms=9)
        draw_sensors(self.ax, sensors, R)
        self.ax.set_title(title, fontsize=10)
        self.fig.canvas.draw_idle()

    def draw_batch(self, flights, sensors, R, title):
        """Кадр пакетного режима: датчики + десять частых пролётов по весу."""
        import matplotlib.cm as cm
        self.ax.clear()
        draw_static(self.ax, self.geom, self.A, self.B)
        for rank, (traj, w) in enumerate(flights):
            self.ax.plot(traj[:, 0], traj[:, 1],
                         color=cm.plasma(0.15 + 0.7 * w),
                         lw=1.0 + 3.0 * w, alpha=0.85,
                         label=f"пролёт {rank + 1} (вес {w:.2f})" if rank < 5 else None)
        draw_sensors(self.ax, sensors, R)
        self.ax.set_title(title, fontsize=10)
        if flights:
            self.ax.legend(loc="upper right", fontsize=7)
        self.fig.canvas.draw_idle()

    def set_metrics(self, lines):
        """Многострочный текст показателей в правом нижнем углу."""
        self._metrics_text.set_text("\n".join(lines))
        self.fig.canvas.draw_idle()

    # ------------------------------------------------------------------
    def show(self):
        plt.show()
