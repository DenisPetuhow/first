# -*- coding: utf-8 -*-
"""
CONTROLLER · Связующее звено между моделью и представлением.

Регистрирует обработчики виджетов, применяет правку параметров (с перезапуском
расчёта), ведёт анимацию итеративного режима через таймер холста, рисует веер
вероятных путей и тепловую карту плотности, запускает пакетный расчёт и
сравнение режимов, поддерживает панель показателей.
"""
from config import MODE_LABELS, TRAJ_LABELS
from view.view import PARAM_SPECS


class SimulationController:
    INTERVAL_MS = 45

    def __init__(self, model, view):
        self.model = model
        self.view = view
        self.speed = int(view.slider_speed.val)

        self.running = False
        self.cur_traj = None
        self.j = 0
        self.target_iters = model.p.T
        self._paths = []
        self._density = None

        self.timer = view.fig.canvas.new_timer(interval=self.INTERVAL_MS)
        self.timer.add_callback(self._tick)

        view.set_callbacks(
            on_start_pause=self.on_start_pause, on_step=self.on_step,
            on_reset=self.on_reset, on_batch=self.on_batch,
            on_apply=self.on_apply, on_mode=self.on_mode,
            on_traj=self.on_traj, on_speed=self.on_speed)

        self._refresh_paths()
        self.view.draw_clear(paths=self._paths)
        self._update_metrics_idle()

    # ------------------------------------------------------------------
    # Правка параметров -> перезапуск
    # ------------------------------------------------------------------
    def on_apply(self):
        try:
            vals = self.view.get_param_values()
        except (ValueError, TypeError):
            self.view.set_param_values(self.model.p)
            self.view.flash_title("Ошибка ввода: проверьте числовые поля.")
            return
        snapshot = {n: getattr(self.model.p, n) for n, _l, _t in PARAM_SPECS}
        for n, v in vals.items():
            setattr(self.model.p, n, v)
        try:
            self.model.p.validate()
        except ValueError as e:
            for n, v in snapshot.items():
                setattr(self.model.p, n, v)
            self.view.set_param_values(self.model.p)
            self.view.flash_title(f"Недопустимо: {e}")
            return
        self._pause()
        self.model.reset()
        self._sync_geometry()
        self.cur_traj = None
        self.target_iters = self.model.p.T
        self._density = None
        self._refresh_paths()
        self.view.draw_clear(paths=self._paths,
                             title="Параметры применены — расчёт перезапущен.")
        self._update_metrics_idle()

    def on_mode(self, key):
        self.model.set_mode(key)
        if self.model.trajectories:
            self.model.recompute_placement()
            self._redraw_current()
            self._update_metrics_full()

    def on_traj(self, key):
        self.model.set_traj_model(key)
        self._pause()
        self.model.reset()
        self.cur_traj = None
        self._density = None
        self._refresh_paths()
        self.view.draw_clear(
            paths=self._paths,
            title=f"Модель движения: {TRAJ_LABELS[key]}. Нажмите «Пуск».")
        self._update_metrics_idle()

    def on_speed(self, val):
        self.speed = int(val)

    # ------------------------------------------------------------------
    # Запуск / шаг / сброс / пакетно
    # ------------------------------------------------------------------
    def on_start_pause(self):
        self.running = not self.running
        self.view.set_running_label(self.running)
        if self.running:
            self.timer.start()
        else:
            self.timer.stop()

    def on_step(self):
        self._pause()
        traj, sensors = self.model.step()
        self.cur_traj = None
        self._refresh_paths()
        self._density = self.model.density_field()
        self.view.draw_iterative_frame(traj, sensors, len(traj) - 1,
                                       self.model.p.R,
                                       self._iter_title(len(traj) - 1, traj),
                                       paths=self._paths, density=self._density)
        self._update_metrics_full()

    def on_reset(self):
        self._pause()
        self.model.reset()
        self.cur_traj = None
        self.j = 0
        self._density = None
        self._refresh_paths()
        self.view.draw_clear(paths=self._paths)
        self._update_metrics_idle()

    def on_batch(self):
        self._pause()
        self.view.flash_title("Идёт пакетный расчёт…", color=None)
        self.view.fig.canvas.draw_idle()
        sensors, metrics = self.model.run_batch()
        self._refresh_paths()
        self._density = self.model.density_field()
        title = (f"Пакетный режим | {MODE_LABELS[self.model.p.mode]} | "
                 f"датчиков {metrics['n_sensors']} | "
                 f"покрытие {metrics['avg_coverage_percent']:.0f}% | "
                 f"ср. пересечений {metrics['avg_crossings']:.1f} | "
                 f"вне зоны {metrics['avg_uncovered_distance']:.1f} км")
        self.view.draw_batch(self._paths, sensors, self.model.p.R, title,
                             density=self._density)
        self._update_metrics_full(extra_compare=True)

    # ------------------------------------------------------------------
    # Анимация
    # ------------------------------------------------------------------
    def _tick(self):
        if not self.running:
            return
        if self.cur_traj is None and self.model.iteration >= self.target_iters:
            self._pause()
            self._update_metrics_full()
            self.view.flash_title(
                f"Структура стабилизирована за {self.model.iteration} итераций.",
                color=None)
            return
        if self.cur_traj is None:
            traj, _ = self.model.step()
            self.cur_traj = traj
            self.j = 0
            self._refresh_paths()
            self._density = self.model.density_field()

        self.j += self.speed * (1 + self.model.iteration // 6)
        end = len(self.cur_traj) - 1
        finished = self.j >= end
        if finished:
            self.j = end
        self.view.draw_iterative_frame(
            self.cur_traj, self.model.sensors, self.j, self.model.p.R,
            self._iter_title(self.j, self.cur_traj),
            paths=self._paths, density=self._density)
        if finished:
            self._update_metrics_full()
            self.cur_traj = None

    # ------------------------------------------------------------------
    def _pause(self):
        if self.running:
            self.running = False
            self.view.set_running_label(False)
        self.timer.stop()

    def _refresh_paths(self):
        self._paths = self.model.probable_paths(top=10)

    def _sync_geometry(self):
        xlim, ylim = self.model.axes_limits()
        self.view.update_geometry(self.model.geom, self.model.p.A,
                                  self.model.p.B, xlim, ylim)

    def _redraw_current(self):
        traj = self.cur_traj
        self.view.draw_iterative_frame(
            traj, self.model.sensors,
            (len(traj) - 1 if traj is not None else 0), self.model.p.R,
            f"Режим: {MODE_LABELS[self.model.p.mode]} | "
            f"датчиков {len(self.model.sensors)}",
            paths=self._paths, density=self._density)

    def _iter_title(self, j, traj):
        seen, nd = self.model.live_metrics(traj[:j + 1])
        return (f"Итерация {self.model.iteration}/{self.target_iters} | "
                f"датчиков {len(self.model.sensors)}/{self.model.p.N} | "
                f"под наблюдением {seen:.0f}% | пересечений {nd}")

    def _update_metrics_idle(self):
        p = self.model.p
        lines = [
            "ГЕОМЕТРИЯ",
            f"  |AB|={p.ab_distance:g} км  L_max={p.L_max:g} км",
            f"  эллипс a={self.model.geom['a']:.0f} b={self.model.geom['b']:.0f} км",
            f"  предельный угол ±{self.model.theta_max:.0f}°",
            f"  кандидатов: {len(self.model.candidates)}",
            "",
            "РЕСУРС",
            f"  датчиков N={p.N}  радиус R={p.R:g} км",
            f"  кратность k={p.k}  сегментов={p.L_seg}",
            "",
            "Нажмите «Пуск», «Шаг» или «Пакетно».",
        ]
        self.view.set_metrics(lines)

    def _update_metrics_full(self, extra_compare=False):
        m = self.model.evaluate()
        lines = [
            f"ВЫБОРКА t = {self.model.iteration}",
            f"  режим: {MODE_LABELS[self.model.p.mode]}",
            f"  задействовано датчиков: {m['n_sensors']}/{self.model.p.N}",
            "",
            f"  ср. пересечений : {m['avg_crossings']:.2f}",
            f"  покрытие пути   : {m['avg_coverage_percent']:.1f}%",
            f"  вне зоны        : {m['avg_uncovered_distance']:.1f} км",
            f"  доля ≥ k        : {m['share_meeting_k']:.0f}%",
        ]
        if extra_compare:
            lines += ["", "СРАВНЕНИЕ РЕЖИМОВ (покрытие|≥k)"]
            for key, mt in self.model.compare_modes().items():
                lines.append(f"  {MODE_LABELS[key]:<14}"
                             f"{mt['avg_coverage_percent']:4.0f}% | "
                             f"{mt['share_meeting_k']:3.0f}%")
        self.view.set_metrics(lines)

    # ------------------------------------------------------------------
    def run(self):
        self.view.show()
