# -*- coding: utf-8 -*-
"""
CONTROLLER (Qt) · Вкладка 2 «Зона старта -> цель».

Наследует анимацию и логику просмотра из QtSimulationController, добавляя:
  * поток создания маршрута кликами (A — центр зоны, направление -> цель B на
    расстоянии |AB| из поля ввода);
  * блокировку «Пуск/Шаг/Пакетно» до создания маршрута;
  * выбор модели движения (дуги из старта / ломаная-манёвр);
  * показатели зоны старта.
"""
import numpy as np

from config import (MODE_LABELS, MOTION_LABELS, AREA_TRAJ_LABELS,
                    WAYPOINT_ZONE_LABELS)
from .controller_qt import QtSimulationController


class AreaStartController(QtSimulationController):

    def __init__(self, model, view):
        super().__init__(model, view)
        view.set_callbacks(on_movement=self.on_movement, on_create=self.on_create,
                           on_route_ready=self.on_route_ready, on_zone=self.on_zone)
        self._redraw_idle_or_last()
        self._update_metrics_idle()

    # ---- гейтинг геометрии/отрисовки до создания маршрута ----
    def _sync_geometry(self):
        # ВАЖНО: брать A/B из кликнутого маршрута модели (model.A/B), а НЕ из
        # model.p (там дефолтные координаты) — иначе точки рисуются не там.
        if self.model.has_route:
            self.view.update_geometry(tuple(self.model.A), tuple(self.model.B),
                                      self.model.corridor_outline(),
                                      self._current_bbox())

    def _redraw_idle_or_last(self, title=None):
        if not self.model.has_route:
            self.view.prompt_create()
            return
        super()._redraw_idle_or_last(title)

    def _update_metrics_idle(self):
        if not self.model.has_route:
            self.view.set_metrics([
                "ВКЛАДКА 2 · старт из ЗОНЫ → цель B", "",
                "1) глубина/ширина зоны и |AB|;",
                "2) «Создать маршрут»;",
                "3) клик A (центр зоны) и направление B.", "",
                "Старт — из множества точек области;",
                "оптимизация учитывает разброс стартов",
                "и вероятностные линии маршрутов.",
            ])
            return
        p = self.model.p
        x0, x1, y0, y1 = self.model.corridor_bbox()
        self.view.set_metrics([
            "ГЕОМЕТРИЯ", f"  |AB|={p.ab_distance:g} км  L_max={p.L_max:g} км",
            f"  зона старта {p.corridor_depth:g}×{p.corridor_width:g} км",
            f"  область движения {x1-x0:.0f}×{y1-y0:.0f} км",
            f"  кандидатов: {len(self.model.candidates)}", "",
            "БПЛА", f"  V={p.speed_kmh:g} км/ч  крен={p.bank_deg:g}°  "
            f"R_min={self.model.r_min*1000:.0f} м", "",
            "РЕСУРС", f"  N={p.N}  R={p.R:g}  k={p.k}  L_seg={p.L_seg}",
            f"  движение: {AREA_TRAJ_LABELS[self.model.movement]}",
            f"  разброс: {MOTION_LABELS[p.motion_profile]}", "",
            "«Пуск», «Шаг» или «Пакетно».",
        ])

    # ---- создание маршрута ----
    def on_create(self):
        # сброс старого маршрута (и программно, и графически) перед новой постановкой
        self._pause()
        self.model.has_route = False
        self.model.reset()
        self.cur_traj = None
        self.j = 0
        self.view.prompt_create()
        self.view.begin_create()
        self._update_metrics_idle()

    def on_route_ready(self, A, dir_pt):
        A = np.asarray(A, float); v = np.asarray(dir_pt, float) - A
        nrm = float(np.linalg.norm(v))
        u = v / nrm if nrm > 1e-6 else np.array([1.0, 0.0])
        B = A + self.model.p.ab_distance * u
        self.model.set_route(tuple(A), tuple(B))
        self.cur_traj = None; self.j = 0
        self.target_iters = self.model.p.T
        self._sync_geometry()
        self._redraw_idle_or_last("Маршрут создан. «Пуск» / «Шаг» / «Пакетно».")
        self._update_metrics_idle()

    def on_movement(self, key):
        self.model.set_movement(key)
        if self.model.has_route:
            self._restart(f"Движение: {AREA_TRAJ_LABELS[key]}. «Пуск».")

    def on_zone(self, key):
        self.model.p.waypoint_zone = key
        if self.model.has_route:
            self._restart(f"Точки маршрута: {WAYPOINT_ZONE_LABELS[key]}. «Пуск».")

    # ---- блокировка просмотра до маршрута ----
    def _require_route(self):
        if not self.model.has_route:
            self.view.flash_title("Сначала «Создать маршрут».")
            return False
        return True

    def on_start_pause(self):
        if not self._require_route():
            return
        super().on_start_pause()

    def on_step(self):
        if not self._require_route():
            return
        super().on_step()

    def on_batch(self):
        if not self._require_route():
            return
        super().on_batch()

    def _iter_title(self, j):
        return (f"Итерация {self.model.iteration}/{self.target_iters}   |   "
                f"датчиков {len(self.model.sensors)}/{self.model.p.N}   |   "
                f"{AREA_TRAJ_LABELS[self.model.movement]} · "
                f"{MOTION_LABELS[self.model.p.motion_profile]}")
