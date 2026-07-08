# -*- coding: utf-8 -*-
"""
CONTROLLER (Qt) · Вкладка 3 «Цифровая карта угроз».

Связывает model.threat_grid.ThreatModel (построение весовой карты + расстановка
датчиков по весам) с view_qt.threat_view.ThreatMapView (ограниченная карта участка,
векторные слои, тепловой слой весов, датчики). Анимации нет — карта статична, датчики
считаются одним проходом (без итераций накопления, как и просил пользователь).
"""
import numpy as np
from pyqtgraph.Qt import QtCore

from config import (MODE_LABELS, THREAT_LAYERS, THREAT_CELL_M,
                    THREAT_ITER_MODE_LABELS)


class _TaskSignals(QtCore.QObject):
    done = QtCore.pyqtSignal(object)          # (kind, error|None)


class _Task(QtCore.QRunnable):
    """Тяжёлый расчёт (чтение OSM / наложение слоёв / расстановка) в ФОНОВОМ потоке,
    чтобы интерфейс не подвисал (на реальных данных это секунды). Работает только с
    моделью (чистый numpy) — Qt из потока не трогает; результат отдаёт сигналом."""

    def __init__(self, kind, work_fn, sig):
        super().__init__()
        self._kind = kind; self._work = work_fn; self._sig = sig

    def run(self):
        err = None
        try:
            self._work()
        except Exception as e:                 # noqa: BLE001 — донесём текст в UI
            err = e
        try:
            self._sig.done.emit((self._kind, err))
        except RuntimeError:
            pass                               # окно закрыто во время расчёта


class ThreatController:

    def __init__(self, model, view):
        self.model = model
        self.view = view
        self._pool = QtCore.QThreadPool.globalInstance()
        self._sig = _TaskSignals()
        self._sig.done.connect(self._on_task_done)
        self._busy = False
        view.set_callbacks(
            on_build=self.on_build, on_place=self.on_place, on_apply=self.on_apply,
            on_reset=self.on_reset, on_reset_view=self.on_reset_view,
            on_mode=self.on_mode, on_toggle=self.on_toggle,
            on_map_layer=self.on_map_layer, on_map_offline=self.on_map_offline,
            on_set_target=self.on_set_target, on_choose_data=self.on_choose_data,
            on_input_apply=self.on_input_apply, on_iter_mode=self.on_iter_mode)
        self._idle_metrics()

    # ---- окно «Входные данные»: применить сразу (не блокирует программу) ----
    def on_input_apply(self, vals):
        for n, v in vals.items():
            setattr(self.model.p, n, v)
        # маршруты/итерации зависят от запаса хода — пересчитать показанное
        t = self.view.get_toggles()
        if self.model.grid is not None and t.get("show_iter"):
            self._run_async("iter", self.model.iterate_routes)
        elif self.model.grid is not None and t["show_routes"]:
            self.model.routes = []
            self._run_async("routes", self.model.plan_routes)
        else:
            self._render_all()
        self.view.set_title("Входные данные применены.")

    # ---- смена режима итераций (heavy/balanced/light/mix) ----
    def on_iter_mode(self, key):
        self.model.p.threat_iter_mode = key
        if (self.model.grid is not None and self.view.get_toggles().get("show_iter")
                and not self._busy):
            self._run_async("iter", self.model.iterate_routes)

    # ---- запуск тяжёлого расчёта в фоне ----
    def _run_async(self, kind, work_fn):
        if self._busy:
            self.view.flash_title("Идёт расчёт — подождите…")
            return
        self._busy = True
        self.view.set_busy(True)
        titles = {"build": "Строю весовую карту угроз…",
                  "place": "Расставляю датчики по весам…",
                  "routes": "Строю маршруты пролёта…",
                  "iter": "Генерирую маршруты (итерации)…"}
        self.view.flash_title(titles.get(kind, "Расчёт…"))
        self.view.process_pending()
        self._pool.start(_Task(kind, work_fn, self._sig))

    def _on_task_done(self, payload):
        kind, err = payload
        self._busy = False
        self.view.set_busy(False)
        if err is not None:
            self.view.flash_title(f"Ошибка: {err}")
            return
        if kind == "build":
            self.view.set_source(self.model.source)
            self._render_all()
            self.view.set_title("Карта угроз построена. «Расставить датчики».")
            self._full_metrics()
            tg = self.view.get_toggles()
            if tg.get("show_iter"):                      # итерации были включены — обновить
                self._run_async("iter", self.model.iterate_routes)
            elif tg["show_routes"]:                      # маршруты были включены — обновить
                self._run_async("routes", self.model.plan_routes)
        elif kind == "place":
            self._render_all()
            me = self.model.metrics()
            self.view.set_title(
                f"Датчиков {me['n_sensors']} · покрыто веса "
                f"{me['covered_frac']*100:.0f}% · режим {MODE_LABELS[self.model.p.mode]}")
            self._full_metrics()
        elif kind == "routes":
            self._render_all()
            self.view.set_title(f"Построено маршрутов: {len(self.model.routes)} "
                                "(вход у реки → цель, обход городов).")
        elif kind == "iter":
            self._render_all()
            lab = THREAT_ITER_MODE_LABELS.get(self.model.p.threat_iter_mode,
                                              self.model.p.threat_iter_mode)
            self.view.set_title(
                f"Итерации: {len(self.model.iter_routes)} маршрутов · режим «{lab}» · "
                f"L_max={self.model.p.threat_L_max:g} км (запас хода).")

    # ---- построение карты (в фоне) ----
    def _work_build(self):
        self.model.build()
        self.model.candidates = self.model.candidate_positions()

    def _work_place(self):
        self.model.place_sensors()

    def on_build(self):
        self._run_async("build", self._work_build)

    # ---- выбор цифровых карт (источник + слои) из отдельного окна ----
    def on_choose_data(self, path, enabled):
        # Окно ТОЛЬКО задаёт выбор; наложение выполняет кнопка «Построить карту»
        # (как и просил пользователь). enabled — набор слоёв (пустой = без слоёв);
        # None приходит только программно и означает «все слои».
        self.model.set_data_source(path)
        self.model.set_enabled_layers(enabled)
        src = f"файл: {path}" if path else "авто (кэш → демо)"
        n = "все" if enabled is None else str(len(enabled))
        self.view.set_source(f"{src} · слоёв: {n} (нажмите «Построить карту»)")
        self.view.set_title("Выбор сохранён. Нажмите «Построить карту».")

    # ---- «указать цель» кликом по карте ----
    def on_set_target(self, x, y):
        self.model.set_target(x, y)                       # пересчитает авто-запас хода
        entry, target = self.model.entry_target_km()
        self.view.render_entry_target(entry, target)
        ab = float(np.hypot(target[0] - entry[0], target[1] - entry[1]))
        self.view.set_ab_distance(ab)
        self.view.refresh_input_dialog()                  # окно покажет новый L_max
        t = self.view.get_toggles()                       # маршруты/итерации зависят от цели
        if self.model.grid is not None and t.get("show_iter"):
            self._run_async("iter", self.model.iterate_routes)
        elif self.model.grid is not None and t["show_routes"]:
            self._run_async("routes", self.model.plan_routes)
        else:
            self.view.set_title(f"Цель задана: ({x:.0f}, {y:.0f}) км. Вход — у реки "
                                "(Северодонецк).")

    # ---- сброс: убрать датчики и заданную цель (карта остаётся) ----
    def on_reset(self):
        self.model.sensors = np.empty((0, 2), float)
        self.model.target_km = None
        self.model.routes = []
        self.model.iter_routes = []
        self.model.route_area = None
        self.model._metrics = {}
        self._render_all()
        self.view.set_title("Сброшено: датчики и цель убраны. Карта сохранена.")
        if self.model.grid is None:
            self._idle_metrics()
        else:
            self._full_metrics()

    def on_place(self):
        # place_sensors сам построит карту, если её ещё нет (в фоновом потоке)
        self._run_async("place", self._work_place)

    # ---- параметры / режим ----
    def on_apply(self):
        try:
            vals = self.view.get_param_values()
        except (ValueError, TypeError):
            self.view.set_param_values(self.model.p)
            self.view.flash_title("Ошибка ввода: проверьте числовые поля.")
            return
        for n, v in vals.items():
            setattr(self.model.p, n, v)
        if self.model.p.threat_N < 1 or self.model.p.threat_R <= 0:
            self.view.flash_title("N ≥ 1 и R > 0.")
            return
        if self.model.grid is None:
            self.on_build()
        elif len(self.model.sensors):
            self.on_place()
        else:                                   # карта есть, датчиков нет — лишь пересетка
            self.model.candidates = self.model.candidate_positions()
            self._render_all()
            self._full_metrics()

    def on_mode(self, key):
        self.model.p.mode = key
        if len(self.model.sensors):
            self.on_place()

    def on_toggle(self):
        t = self.view.get_toggles()
        # итерации включили, а их ещё нет -> сгенерировать в фоне (стохастика, ~1 с)
        if (t.get("show_iter") and self.model.grid is not None
                and not self.model.iter_routes and not self._busy):
            self._run_async("iter", self.model.iterate_routes)
            return
        # маршруты включили, а их ещё нет -> посчитать в фоне (Дейкстра, ~1 с)
        if (t["show_routes"] and self.model.grid is not None
                and not self.model.routes and not self._busy):
            self._run_async("routes", self.model.plan_routes)
            return
        self._render_all()

    def on_map_layer(self, key):
        self.model.p.map_layer3 = key

    def on_map_offline(self, flag):
        self.model.p.map_offline3 = bool(flag)

    def on_reset_view(self):
        self.view.frame_bbox()

    # ---- отрисовка ----
    def _render_all(self):
        t = self.view.get_toggles()
        g = self.model.grid
        entry, target = self.model.entry_target_km()
        self.view.render_entry_target(entry, target)
        # |AB| вход->цель (для окна входных данных)
        ab = float(np.hypot(target[0] - entry[0], target[1] - entry[1]))
        self.model.p.ab_distance = round(ab, 1)
        self.view.set_ab_distance(ab)
        self.view.refresh_input_dialog()                  # окно «Входные данные» — актуальный L_max
        if g is None:
            return
        extent = g.extent_km()
        self.view.render_threat(g.weight, extent, t)
        self.view.render_exclusions(g.water_mask(), g.urban_mask(), extent, t)
        self.view.render_layers(self.model.layers,
                                self.model.layers.get("bridge_pts", []), t)
        if t["show_cand"] and len(self.model.candidates) == 0:
            self.model.candidates = self.model.candidate_positions()
        self.view.render_candidates(self.model.candidates, t)
        self.view.render_routes(self.model.routes, self.model.route_area, extent, t)
        self.view.render_iter_routes(self.model.iter_routes, t)
        if t.get("show_cross"):
            self.view.render_crossings(g.crossing_cells_km(), t)
        else:
            self.view.render_crossings(None, t)
        self.view.render_sensors(self.model.sensors, self.model.p.threat_R)

    # ---- показатели ----
    def _idle_metrics(self):
        kx0, kx1, ky0, ky1 = self.model.bbox_km
        cell = THREAT_CELL_M / 1000.0
        nx = int(round((kx1 - kx0) / cell)); ny = int(round((ky1 - ky0) / cell))
        self.view.set_metrics([
            "ВКЛАДКА 3 · цифровая карта угроз", "",
            "Участок (район Северодонецка):",
            f"  {kx1-kx0:.0f}×{ky1-ky0:.0f} км",
            f"  сетка {cell*1000:.0f} м → {nx}×{ny} = {nx*ny} ячеек", "",
            "1) «Построить карту» — наложение слоёв",
            "   (реки/дороги/ЛЭП/мосты/…) на сетку;",
            "2) «Расставить датчики» — по сумме весов",
            "   (макс. покрытого веса + разнос),",
            "   без датчиков на воде.", "",
            "Веса слоёв — в config.THREAT_LAYERS.",
        ])

    def _full_metrics(self):
        me = self.model.metrics()
        p = self.model.p
        kx0, kx1, ky0, ky1 = self.model.bbox_km
        cell = THREAT_CELL_M / 1000.0
        lines = [
            "ГЕОМЕТРИЯ",
            f"  участок {kx1-kx0:.0f}×{ky1-ky0:.0f} км",
            f"  ячейка {cell*1000:.0f} м",
            f"  источник: {self.model.source}", "",
            "РЕСУРС",
            f"  N={p.threat_N}  R={p.threat_R:g} км  k={p.threat_k}",
            f"  режим: {MODE_LABELS[p.mode]}",
        ]
        if me:
            lines += [
                f"  кандидатов: {me.get('n_candidates', 0)}", "",
                "ВЕС КАРТЫ (по слоям)",
            ]
            by = me.get("by_layer", {})
            for name in sorted(by, key=lambda n: -abs(by[n])):
                lab = THREAT_LAYERS.get(name, {}).get("label", name)
                lines.append(f"  {lab:<18}{by[name]:+8.0f}")
            lines += [
                "",
                "РАССТАНОВКА",
                f"  датчиков: {me.get('n_sensors', 0)}",
                f"  покрытый вес: {me.get('covered_weight', 0):.0f} / "
                f"{me.get('total_weight', 0):.0f}",
                f"  доля покрытия: {me.get('covered_frac', 0)*100:.0f}%",
            ]
        self.view.set_metrics(lines)

    def run(self):
        self.view.show()
