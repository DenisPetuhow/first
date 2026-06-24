# -*- coding: utf-8 -*-
"""
CONTROLLER · Связующее звено между моделью и представлением.

Регистрирует обработчики виджетов, ведёт анимацию итеративного режима через
таймер холста, запускает пакетный расчёт и сравнение режимов, поддерживает
панель показателей в актуальном состоянии. Бизнес-логику делегирует модели,
отрисовку — представлению.

Порядок итеративного режима (как в эталонном uav_demo): на каждой итерации
маршрут порождается, добавляется в выборку, расположение пересчитывается по
всей накопленной выборке, после чего показывается пролёт при новом расположении.
"""
from config import MODE_LABELS, TRAJ_LABELS


class SimulationController:
    """Контроллер приложения размещения датчиков обнаружения БПЛА."""

    INTERVAL_MS = 50          # период кадра таймера (~20 кадров/с)

    def __init__(self, model, view):
        self.model = model
        self.view = view
        self.speed = int(view.slider_speed.val)

        # состояние анимации
        self.running = False
        self.cur_traj = None          # маршрут текущего пролёта
        self.j = 0                    # индекс текущей точки маршрута
        self.target_iters = model.p.T  # сколько итераций накапливать

        # таймер анимации (создаётся под интерактивный backend)
        self.timer = view.fig.canvas.new_timer(interval=self.INTERVAL_MS)
        self.timer.add_callback(self._tick)

        # привязка обработчиков
        view.set_callbacks(
            on_start_pause=self.on_start_pause,
            on_step=self.on_step,
            on_reset=self.on_reset,
            on_batch=self.on_batch,
            on_mode=self.on_mode,
            on_traj=self.on_traj,
            on_speed=self.on_speed,
        )
        self._update_metrics_idle()

    # ------------------------------------------------------------------
    # Обработчики панели управления
    # ------------------------------------------------------------------
    def on_start_pause(self):
        self.running = not self.running
        self.view.set_running_label(self.running)
        if self.running:
            self.timer.start()
        else:
            self.timer.stop()

    def on_step(self):
        """Одна полная итерация мгновенно (без анимации пролёта)."""
        self._pause()
        traj, sensors = self.model.step()
        self.cur_traj = None
        title = self._iter_title(len(traj) - 1, traj)
        self.view.draw_iterative_frame(traj, sensors, len(traj) - 1,
                                       self.model.p.R, title)
        self._update_metrics_full()

    def on_reset(self):
        self._pause()
        self.model.reset()
        self.cur_traj = None
        self.j = 0
        self.view.draw_clear()
        self._update_metrics_idle()

    def on_batch(self):
        """Пакетный расчёт по всей выборке + сравнение режимов."""
        self._pause()
        self.view.ax.set_title("Идёт пакетный расчёт…", fontsize=10)
        self.view.fig.canvas.draw_idle()
        sensors, metrics = self.model.run_batch()
        flights = self.model.top_flights(top=10)
        title = (f"Пакетный режим | {MODE_LABELS[self.model.p.mode]} | "
                 f"покрытие {metrics['avg_coverage_percent']:.0f}%, "
                 f"ср. пересечений {metrics['avg_crossings']:.1f}, "
                 f"вне зоны {metrics['avg_uncovered_distance']:.1f}")
        self.view.draw_batch(flights, sensors, self.model.p.R, title)
        self._update_metrics_full(extra_compare=True)

    def on_mode(self, key):
        self.model.set_mode(key)
        if self.model.trajectories:
            self.model.recompute_placement()
            self._redraw_idle_with_sensors()
            self._update_metrics_full()

    def on_traj(self, key):
        # смена модели движения меняет распределение -> начинаем выборку заново
        self.model.set_traj_model(key)
        self.on_reset()
        self.view.draw_clear(
            title=f"Модель движения: {TRAJ_LABELS[key]}. Нажмите «Пуск».")

    def on_speed(self, val):
        self.speed = int(val)

    # ------------------------------------------------------------------
    # Анимация итеративного режима
    # ------------------------------------------------------------------
    def _tick(self):
        if not self.running:
            return
        # завершили накопление -> стабилизация, авто-пауза
        if self.cur_traj is None and self.model.iteration >= self.target_iters:
            self._pause()
            self._update_metrics_full()
            self.view.ax.set_title(
                f"Структура стабилизирована за {self.model.iteration} итераций.",
                fontsize=10)
            self.view.fig.canvas.draw_idle()
            return

        # начало новой итерации: породить маршрут, добавить, пересчитать S
        if self.cur_traj is None:
            traj, _ = self.model.step()      # внутри: append + recompute
            self.cur_traj = traj
            self.j = 0

        # шаг пролёта с ускорением по номеру итерации
        step = self.speed * (1 + self.model.iteration // 6)
        self.j += step
        end = len(self.cur_traj) - 1
        finished = self.j >= end
        if finished:
            self.j = end

        title = self._iter_title(self.j, self.cur_traj)
        self.view.draw_iterative_frame(self.cur_traj, self.model.sensors,
                                       self.j, self.model.p.R, title)

        if finished:                          # пролёт завершён -> следующая итерация
            self._update_metrics_full()
            self.cur_traj = None

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------
    def _pause(self):
        if self.running:
            self.running = False
            self.view.set_running_label(False)
        self.timer.stop()

    def _iter_title(self, j, traj):
        seen, nd = self.model.live_metrics(traj[:j + 1])
        return (f"Итерация {self.model.iteration}/{self.target_iters} | "
                f"датчиков {len(self.model.sensors)} | "
                f"под наблюдением {seen:.0f}% | пересечений {nd}")

    def _redraw_idle_with_sensors(self):
        traj = self.cur_traj
        self.view.draw_iterative_frame(
            traj, self.model.sensors,
            (len(traj) - 1 if traj is not None else 0),
            self.model.p.R,
            f"Режим: {MODE_LABELS[self.model.p.mode]} | "
            f"датчиков {len(self.model.sensors)}")

    def _update_metrics_idle(self):
        p = self.model.p
        lines = [
            "ПАРАМЕТРЫ",
            f"  A={p.A}  B={p.B}",
            f"  L_max={p.L_max}  N={p.N}  R={p.R}",
            f"  k={p.k}  L_seg={p.L_seg}",
            f"  кандидатов: {len(self.model.candidates)}",
            f"  s_max={self.model.s_max:.1f}",
            "",
            "Накопите выборку («Пуск»/«Шаг»)",
            "или нажмите «Пакетно».",
        ]
        self.view.set_metrics(lines)

    def _update_metrics_full(self, extra_compare=False):
        m = self.model.evaluate()
        lines = [
            f"ПОКАЗАТЕЛИ (выборка t={self.model.iteration})",
            f"  режим: {MODE_LABELS[self.model.p.mode]}",
            f"  ср. пересечений : {m['avg_crossings']:.2f}",
            f"  покрытие пути   : {m['avg_coverage_percent']:.1f}%",
            f"  вне зоны        : {m['avg_uncovered_distance']:.1f}",
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
        """Запуск интерактивного приложения."""
        self.view.show()
