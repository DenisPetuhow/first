# -*- coding: utf-8 -*-
"""
CONTROLLER · Связующее звено между моделью и представлением.

Обрабатывает события виджетов (правка параметров с перезапуском, режимы
просмотра, переключатели, 3 чекбокса слоёв), ведёт плавную анимацию полёта
через блиттинг, запускает пакетный расчёт и сравнение режимов, поддерживает
панель показателей.
"""
from config import MODE_LABELS, TRAJ_LABELS, MOTION_LABELS, MANEUVER_LAW_LABELS
from view.view import PARAM_SPECS


class SimulationController:
    INTERVAL_MS = 33

    def __init__(self, model, view):
        self.model = model
        self.view = view
        self.speed = int(view.slider_speed.val)
        self.running = False
        self.cur_traj = None
        self.j = 0
        self.target_iters = model.p.T

        self.timer = self._make_timer()
        self.timer.add_callback(self._tick)

        view.set_callbacks(
            on_start_pause=self.on_start_pause, on_step=self.on_step,
            on_reset=self.on_reset, on_batch=self.on_batch, on_apply=self.on_apply,
            on_mode=self.on_mode, on_traj=self.on_traj, on_profile=self.on_profile,
            on_speed=self.on_speed, on_toggle=self.on_toggle, on_law=self.on_law)

        self._sync_geometry()
        self._redraw_idle_or_last()
        self._update_metrics_idle()

    def _make_timer(self):
        """Фабрика таймера анимации. Переопределяется в Qt-контроллере (QTimer);
        базовая реализация использует таймер холста matplotlib."""
        return self.view.fig.canvas.new_timer(interval=self.INTERVAL_MS)

    # ------------------------------------------------------------------
    # Слои отображения по чекбоксам
    # ------------------------------------------------------------------
    def _toggles(self):
        return self.view.get_toggles()        # show_heat / show_freq / show_fan

    def _layers(self, t):
        freq = self.model.frequent_paths(10) if t["show_freq"] else None
        fan = self.model.fan_paths() if t["show_fan"] else None
        density = self.model.density_field() if t["show_heat"] else None
        return freq, fan, density

    # ------------------------------------------------------------------
    # Правка параметров и переключатели -> перезапуск
    # ------------------------------------------------------------------
    def on_apply(self):
        try:
            vals = self.view.get_param_values()
        except (ValueError, TypeError):
            self.view.set_param_values(self.model.p)
            self.view.flash_title("Ошибка ввода: проверьте числовые поля.")
            return
        snap = {n: getattr(self.model.p, n) for n, _l, _t in self.view.get_param_specs()}
        for n, v in vals.items():
            setattr(self.model.p, n, v)
        try:
            self.model.p.validate()
        except ValueError as e:
            for n, v in snap.items():
                setattr(self.model.p, n, v)
            self.view.set_param_values(self.model.p)
            self.view.flash_title(f"Недопустимо: {e}")
            return
        self._restart("Параметры применены — расчёт перезапущен.")

    def on_mode(self, key):
        self.model.set_mode(key)
        if self.model.trajectories:
            self.model.recompute_placement()
            self._redraw_idle_or_last()
            self._update_metrics_full()

    def on_traj(self, key):
        self.model.set_traj_model(key)
        self._restart(f"Модель движения: {TRAJ_LABELS[key]}. Нажмите «Пуск».")

    def on_profile(self, key):
        self.model.set_profile(key)
        self._restart(f"Профиль разброса: {MOTION_LABELS[key]}. Нажмите «Пуск».")

    def on_law(self, key):
        self.model.p.maneuver_law = key
        self._restart(f"Закон манёвра: {MANEUVER_LAW_LABELS[key]}. Нажмите «Пуск».")

    def on_speed(self, val):
        self.speed = int(val)

    def on_toggle(self):
        self._sync_geometry()
        t = self._toggles()
        freq, fan, density = self._layers(t)
        if self.cur_traj is not None:
            self.view.setup_flight(self.cur_traj, self.model.sensors,
                                   self.model.p.R, freq, fan, density, t,
                                   self._iter_title(self.j))
        else:
            self._redraw_idle_or_last()

    # ------------------------------------------------------------------
    # Пуск / шаг / сброс / пакетно
    # ------------------------------------------------------------------
    def on_start_pause(self):
        self.running = not self.running
        self.view.set_running_label(self.running)
        (self.timer.start if self.running else self.timer.stop)()

    def on_step(self):
        self._pause()
        traj, sensors = self.model.step()
        self.cur_traj = None
        self._sync_geometry()
        t = self._toggles()
        freq, fan, density = self._layers(t)
        self.view.draw_static_frame(sensors, self.model.p.R, freq, fan, density,
                                    t, self._iter_title(0))
        self._update_metrics_full()

    def on_reset(self):
        self._pause()
        self.model.reset()
        self.cur_traj = None
        self.j = 0
        self._sync_geometry()
        self._redraw_idle_or_last("Сброшено. Новая случайная выборка.")
        self._update_metrics_idle()

    def on_batch(self):
        self._pause()
        self.view.flash_title("Идёт пакетный расчёт…", color=None)
        self.view.process_pending()
        sensors, metrics = self.model.run_batch()
        self._sync_geometry()
        t = self._toggles()
        freq, fan, density = self._layers(t)
        title = (f"Пакетно | {MODE_LABELS[self.model.p.mode]} | "
                 f"датчиков {metrics['n_sensors']} | "
                 f"покрытие {metrics['avg_coverage_percent']:.0f}% | "
                 f"пересеч. {metrics['avg_crossings']:.1f} | "
                 f"вне зоны {metrics['avg_uncovered_distance']:.0f} км")
        self.view.draw_static_frame(sensors, self.model.p.R, freq, fan, density,
                                    t, title)
        self._update_metrics_full(extra_compare=True)

    # ------------------------------------------------------------------
    # Анимация (блиттинг)
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
            self._sync_geometry()
            t = self._toggles()
            freq, fan, density = self._layers(t)
            self.view.setup_flight(traj, self.model.sensors, self.model.p.R,
                                   freq, fan, density, t, self._iter_title(0))
        self.j += self.speed * (1 + self.model.iteration // 8)
        end = len(self.cur_traj) - 1
        finished = self.j >= end
        if finished:
            self.j = end
        self.view.update_flight(self.j, self._hud_text(self.j))
        if finished:
            self._update_metrics_full()
            self.cur_traj = None

    # ------------------------------------------------------------------
    def _restart(self, title):
        self._pause()
        self.model.reset()
        self.cur_traj = None
        self.j = 0
        self.target_iters = self.model.p.T
        self._sync_geometry()
        self._redraw_idle_or_last(title)
        self._update_metrics_idle()

    def _pause(self):
        if self.running:
            self.running = False
            self.view.set_running_label(False)
        self.timer.stop()

    def _current_bbox(self):
        # «веер» включён -> показываем весь коридор (с пределами);
        # иначе зум на зону типичных маршрутов
        if self._toggles()["show_fan"]:
            return self.model.corridor_bbox()
        return self.model.view_bbox()

    def _sync_geometry(self):
        self.view.update_geometry(self.model.p.A, self.model.p.B,
                                  self.model.corridor_outline(),
                                  self._current_bbox())

    def _redraw_idle_or_last(self, title=None):
        self._sync_geometry()
        t = self._toggles()
        freq, fan, density = self._layers(t)
        if not self.model.trajectories:
            self.view.draw_clear(freq, fan, density, t,
                                 title or "Готово. «Пуск», «Шаг» или «Пакетно».")
        else:
            self.view.draw_static_frame(
                self.model.sensors, self.model.p.R, freq, fan, density, t,
                title or (f"Режим: {MODE_LABELS[self.model.p.mode]} | "
                          f"датчиков {len(self.model.sensors)}/{self.model.p.N}"))

    def _hud_text(self, j):
        seen, nd = self.model.live_metrics(self.cur_traj[:j + 1])
        return f"под наблюдением {seen:.0f}%   пересечений {nd}"

    def _iter_title(self, j):
        return (f"Итерация {self.model.iteration}/{self.target_iters}   |   "
                f"датчиков {len(self.model.sensors)}/{self.model.p.N}   |   "
                f"профиль: {MOTION_LABELS[self.model.p.motion_profile]}")

    def _update_metrics_idle(self):
        p = self.model.p
        x0, x1, y0, y1 = self.model.corridor_bbox()
        lines = [
            "ГЕОМЕТРИЯ",
            f"  |AB|={p.ab_distance:g} км  L_max={p.L_max:g} км",
            f"  большая полуось a=L_max/2={p.L_max/2:g} км",
            f"  предельный угол ±{self.model.theta_max:.0f}°",
            f"  коридор {x1-x0:.0f}×{y1-y0:.0f} км",
            f"  кандидатов: {len(self.model.candidates)}",
            "",
            "БПЛА (ТТХ -> физика)",
            f"  V={p.speed_kmh:g} км/ч  крен={p.bank_deg:g}°",
            f"  радиус разворота R_min={getattr(self.model,'r_min',0)*1000:.0f} м",
            f"  время полёта {p.ab_distance/max(p.speed_kmh,1)*60:.0f}–"
            f"{p.L_max/max(p.speed_kmh,1)*60:.0f} мин",
            "",
            "РЕСУРС",
            f"  N={p.N}  R={p.R:g} км  k={p.k}  L_seg={p.L_seg}",
            f"  профиль: {MOTION_LABELS[p.motion_profile]}",
            "",
            "Нажмите «Пуск», «Шаг» или «Пакетно».",
        ]
        self.view.set_metrics(lines)

    def _update_metrics_full(self, extra_compare=False):
        m = self.model.evaluate()
        lines = [
            f"ВЫБОРКА t={self.model.iteration}",
            f"  режим: {MODE_LABELS[self.model.p.mode]}",
            f"  задействовано: {m['n_sensors']}/{self.model.p.N}",
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

    def run(self):
        self.view.show()
