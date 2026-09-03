# -*- coding: utf-8 -*-
"""
CONTROLLER (Qt) · Вкладка 3 «Цифровая карта угроз».

Связывает model.threat_grid.ThreatModel (построение весовой карты + расстановка
датчиков по весам) с view_qt.threat_view.ThreatMapView (ограниченная карта участка,
векторные слои, тепловой слой весов, датчики). Анимации нет — карта статична, датчики
считаются одним проходом (без итераций накопления, как и просил пользователь).

ДОКУМЕНТАЦИЯ (почему так, а не как устроено — это здесь в комментариях):
  теория/вкладка_3/ВКЛАДКА_3_КАК_РАБОТАЕТ.md — порядок работы кнопок простыми словами
  теория/ОГРАНИЧЕНИЯ.md — §6 что проверяет контрольный прогон

Карта кода — теория/карта_кода/README.md (генерируется codemap.py).
"""
import numpy as np
from pyqtgraph.Qt import QtCore

from config import (MODE_LABELS, THREAT_LAYERS, THREAT_CELL_M,
                    THREAT_ITER_MODE_LABELS, THREAT_SENSOR_REFRESH_EVERY)


class _TaskSignals(QtCore.QObject):
    """Мостик «фоновый поток → интерфейс». Qt-объекты из потока трогать нельзя, поэтому
    результат фоновой задачи возвращается СИГНАЛОМ `done`, который Qt доставляет уже в
    основной поток. Полезная нагрузка — кортеж (вид_задачи, ошибка|None)."""
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
    """Связывает МОДЕЛЬ (ThreatModel — чистый numpy) и ПРЕДСТАВЛЕНИЕ (ThreatMapView — Qt).

    Отвечает за: запуск тяжёлых расчётов в фоне (пул потоков, чтобы окно не подвисало),
    покадровую анимацию итераций (таймер 33 мс, «полёт» БПЛА по маршруту), сбор данных из
    модели и передачу их во view на отрисовку, обработку действий пользователя (кнопки,
    чекбоксы, клик «цель», окно входных данных). Логику расчётов сам не содержит — только
    оркестрирует. Публичные методы `on_*` — обработчики событий интерфейса (их имена
    говорят сами за себя); тяжёлое считается в `_run_async`, результат приходит в
    `_on_task_done`."""

    def __init__(self, model, view):
        self.model = model
        self.view = view
        self._pool = QtCore.QThreadPool.globalInstance()
        self._sig = _TaskSignals()
        self._sig.done.connect(self._on_task_done)
        self._busy = False
        # анимация итераций (как во вкладке 2): таймер прорисовки полёта БПЛА
        self._anim = QtCore.QTimer()
        self._anim.setInterval(33)
        self._anim.timeout.connect(self._anim_tick)
        self._anim_running = False
        self._cur_route = None            # маршрут, который сейчас «летит»
        self._cur_j = 0                   # индекс точки анимации
        self._speed = 6                   # скорость анимации (точек за кадр)
        self._sensors_at = 0              # при скольких маршрутах датчики пересчитаны
        # цель/сектор заданы кликом, но маршруты ещё не пересчитаны: ждём «Применить»
        self._pending_recalc = False
        view.set_callbacks(
            on_build=self.on_build, on_relief=self.on_relief,
            on_place=self.on_place, on_apply=self.on_apply,
            on_reset=self.on_reset, on_reset_view=self.on_reset_view,
            on_mode=self.on_mode, on_toggle=self.on_toggle,
            on_map_layer=self.on_map_layer, on_map_offline=self.on_map_offline,
            on_set_target=self.on_set_target, on_choose_data=self.on_choose_data,
            on_input_apply=self.on_input_apply, on_iter_mode=self.on_iter_mode,
            on_iter_spread=self.on_iter_spread, on_iter_spend=self.on_iter_spend,
            on_iter_play=self.on_iter_play, on_iter_step=self.on_iter_step,
            on_iter_batch=self.on_iter_batch, on_iter_speed=self.on_iter_speed,
            on_iter_gen_frac=self.on_iter_gen_frac,
            on_zone_added=self.on_zone_added, on_zone_undo=self.on_zone_undo,
            on_set_sector=self.on_set_sector, on_clear_sector=self.on_clear_sector,
            on_sensors_apply=self.on_sensors_apply)
        # перерисовка слоёв при смене масштаба/панораме: прореживание считается по
        # ВИДИМОЙ области, а застройка переключается растр <-> контуры
        self.view.on_view_changed = self._on_view_changed
        self.view.set_relief_button(self.model.relief_on, self.model.has_dem())
        self.model.sync_cand_step(force=True)      # шаг сетки под текущий радиус
        self.view.set_param_values(self.model.p)
        self._idle_metrics()

    # ================= ЗАПРЕТНЫЕ ЗОНЫ =================
    def on_zone_added(self, poly):
        """Пользователь нарисовал зону: пролёт там запрещён, всё зависящее — пересчитать."""
        if not self.model.add_no_fly_zone(poly):
            return
        self._after_zone_change()

    def on_zone_undo(self):
        """Убрать последнюю зону."""
        if not self.model.undo_no_fly_zone():
            self.view.set_title("Запретных зон нет")
            return
        self._after_zone_change()

    def _after_zone_change(self):
        """Общее для добавления и отмены: пересчёт и ЧЕСТНОЕ сообщение о последствиях.

        Зона могла перекрыть единственный коридор — тогда маршрутов не будет вовсе. Это
        законный исход, а не поломка, но молчать о нём нельзя: пустая карта выглядит как
        сбой программы."""
        self.model.sensors = np.empty((0, 2), float)
        self._sensors_at = 0
        info = self.model.no_fly_check()
        msg = "Запретных зон: %d (%.0f км²)" % (info["zones"], info["area_km2"])
        if info["blocked_entry"]:
            msg += " — ⚠ ТОЧКА ВЫЛЕТА внутри зоны"
        elif info["blocked_target"]:
            msg += " — ⚠ ЦЕЛЬ внутри зоны"
        elif info["unreachable"]:
            msg += " — ⚠ цель НЕДОСТИЖИМА: зона перекрыла коридор, обхода по весам нет"
        if info["zones"] == 0:
            msg = "Запретные зоны убраны"
        self.view.set_title(msg)
        self._render_all()

    # ================= РЕЛЬЕФ =================
    def on_relief(self):
        """Кнопка «Добавить рельеф» / «Убрать рельеф».

        Карта пересобирается целиком (рельеф — последний шаг конвейера весов, досчитать
        его поверх готовой карты нельзя). Пересборка обнуляет накопленные маршруты,
        итерации и датчики: они построены по другой карте, смешивать выборки нельзя."""
        if self._busy:
            self.view.flash_title("Идёт расчёт — подождите…")
            return
        if not self.model.has_dem():
            # путь называем ПОЛНОСТЬЮ, с папкой участка: файл, положенный не туда,
            # программа не найдёт, а сообщение «положите dem.tif» это не подскажет
            from model.threat_grid import area_cache_dir
            self.view.flash_title("Нет файла высот: положите растр (.tif/.hgt) в "
                                  f"{area_cache_dir()}")
            return
        want = not self.model.relief_on
        self._anim_stop()
        self.view.iter_clear_current()
        self.model.set_relief(want)
        self.view.set_relief_button(self.model.relief_on, True)
        self._sensors_at = 0
        self._run_async("build", self.model.build)

    def _on_view_changed(self):
        """Вид изменился — перерисовать только векторные слои (остальное не зависит
        от масштаба). Дёшево: слои и так строятся из готовых массивов."""
        self.view.reposition_legends()      # легенды прижаты к углам вида, а не участка
        g = self.model.grid
        if g is None:
            return
        t = self.view.get_toggles()
        # ⚠️ РАНЬШЕ ЗДЕСЬ БЫЛ ВЫХОД ПРИ СНЯТОЙ ГАЛКЕ «векторные слои». Теперь перерисовка
        # нужна и без слоёв: от масштаба зависят ещё и ПОДПИСИ пунктов (какие показывать и
        # какие попали в кадр), а они живут по своей галке. Пустой проход дёшев — слои
        # получают пустые массивы и прячутся.
        self.view.render_layers(self.model.layers,
                                self.model.layers.get("bridge_pts", []), t,
                                built_mask=g._built_cells, extent=g.extent_km())

    # ================= ИТЕРАЦИИ (симуляция как во вкладке 2) =================
    def _iter_ready(self):
        if self._busy:
            self.view.flash_title("Идёт расчёт — подождите…"); return False
        if self.model.grid is None:
            self.view.flash_title("Сначала «Построить карту»."); return False
        return True

    def on_iter_speed(self, v):
        self._speed = max(1, int(v))

    def _iter_title(self):
        lab = THREAT_ITER_MODE_LABELS.get(self.model.p.threat_iter_mode,
                                          self.model.p.threat_iter_mode)
        T = int(self.model.p.threat_iter_routes)
        # без `or ()`: sensors_big — массив numpy, его истинность неопределена
        big_arr = getattr(self.model, "sensors_big", None)
        n_big = 0 if big_arr is None else len(big_arr)
        big = (f" + {n_big}/{getattr(self.model.p, 'threat_N_big', 0)} больших"
               if getattr(self.model.p, "threat_N_big", 0) else "")
        return (f"Итерация {len(self.model.iter_routes)}/{T}  |  режим «{lab}»  |  "
                f"датчиков {len(self.model.sensors)}/{self.model.p.threat_N}{big}  |  "
                f"L_max={self.model.p.threat_L_max:g} км")

    # ▶ Пуск / ⏸ Пауза — пошаговая анимация с движением БПЛА
    def on_iter_play(self):
        if self._anim_running:                            # пауза
            self._anim_stop(); return
        if not self._iter_ready():
            return
        self.view.set_iter_checked(True)
        self.model.p.threat_iter_routes = self.view.get_iter_T()
        if self.model.iter_iteration == 0 or self._iter_finished():
            if not self.model.iter_reset():
                self.view.flash_title("Цель недостижима по коридорам (проверьте L_max/цель).")
                return
            self._sensors_at = 0                          # выборка начата заново
            self.view.iter_show_accumulated([])
        self._anim_running = True
        self.view.set_iter_running(True)
        self._anim.start()

    def _iter_finished(self):
        return self.model.iter_iteration >= int(self.model.p.threat_iter_routes)

    def _maybe_refresh_sensors(self, force=False):
        """Пересчитать датчики по УЖЕ накопленной выборке пролётов — периодически, а не
        только в самом конце. Раньше во время итераций датчики стояли неподвижно (те, что
        были посчитаны по «возможным путям»), и казалось, что модель на выборку не
        реагирует. Шаг обновления — `THREAT_SENSOR_REFRESH_EVERY` накопленных маршрутов."""
        n = len(self.model.iter_routes)
        step = max(1, int(THREAT_SENSOR_REFRESH_EVERY))
        if n < step:
            return False
        if not force and n - self._sensors_at < step:
            return False
        self.model.place_sensors()
        self._sensors_at = n
        self.view.render_sensors(self.model.sensors, self.model.p.threat_R,
                                 getattr(self.model, 'sensors_big', None),
                                 getattr(self.model.p, 'threat_R_big', 0.0))
        return True

    def _anim_stop(self):
        self._anim.stop()
        self._anim_running = False
        self.view.set_iter_running(False)

    def _anim_tick(self):
        """Один кадр анимации (таймер 33 мс). Если текущий маршрут «долетел» — берём из
        модели следующую итерацию и начинаем её; иначе сдвигаем маркер БПЛА вперёд по
        текущему маршруту. По завершении всех итераций — расставляем датчики и рисуем всё."""
        # начать новый маршрут, если предыдущий долетел
        if self._cur_route is None:
            if self._iter_finished():
                self._anim_stop()
                self._maybe_refresh_sensors(force=True)    # финал: по всей выборке пролётов
                self._render_all()
                self._full_metrics()
                self.view.set_title(self._iter_title() + " — готово. Датчики по выборке пролётов.")
                return
            route = self.model.iter_step()
            if route is None:                             # неудачная итерация
                self._anim_fails = getattr(self, "_anim_fails", 0) + 1
                if self._anim_fails > 40:                 # выборка иссякла — остановиться
                    self._anim_stop()
                    self.view.set_title(self._iter_title() + " — выборка исчерпана.")
                return
            self._anim_fails = 0
            self._cur_route = route
            self._cur_j = 0
            # «итерационные маршруты» выкл -> на карте только ТЕКУЩИЙ пролёт (не весь веер)
            show_acc = self.view.get_toggles().get("show_iter")
            self.view.iter_show_accumulated(self.model.iter_routes if show_acc else [])
            self.view.iter_setup_flight(route)
            self.view.set_title(self._iter_title())
        # двигать БПЛА по текущему маршруту. Шаг ~ пропорционален длине пути, чтобы любой
        # маршрут пролетался за одинаковое число кадров (иначе длинные — очень медленно).
        self._cur_j += max(1, int(len(self._cur_route) * self._speed / 120))
        end = len(self._cur_route) - 1
        if self._cur_j >= end:
            self._cur_j = end
        self.view.iter_update_flight(self._cur_route, self._cur_j)
        if self._cur_j >= end:                            # долетел -> следующая итерация
            self._cur_route = None
            self._maybe_refresh_sensors()                 # датчики «сходятся» по ходу выборки
            if self.view.get_toggles().get("show_iter_gen"):   # 10 % «на этот момент»
                self._refresh_generalized()

    # Шаг — одна итерация без анимации (сразу весь маршрут)
    def on_iter_step(self):
        if not self._iter_ready():
            return
        self._anim_stop()
        self.view.set_iter_checked(True)
        self.model.p.threat_iter_routes = self.view.get_iter_T()
        if self.model.iter_iteration == 0:
            if not self.model.iter_reset():
                self.view.flash_title("Цель недостижима по коридорам.")
                return
            self._sensors_at = 0                          # выборка начата заново
        route = self.model.iter_step()
        self.view.iter_show_accumulated(self.model.iter_routes)
        if route is not None:
            self.view.iter_update_flight(route, len(route) - 1)   # весь маршрут + БПЛА в цели
        self._maybe_refresh_sensors()                            # датчики по накопленной выборке
        self._refresh_generalized()                              # 10 % «на этот момент»
        self.view.set_title(self._iter_title())

    # Пакетно — все T итераций сразу (в фоне), без анимации
    def on_iter_batch(self):
        if not self._iter_ready():
            return
        self._anim_stop()
        self.view.iter_clear_current()
        self.view.set_iter_checked(True)
        self.model.p.threat_iter_routes = self.view.get_iter_T()
        self._sensors_at = 0                             # iter_batch начинает выборку заново
        self._run_async("iter", self.model.iter_batch)

    # ---- окно «Входные данные»: применить сразу (не блокирует программу) ----
    def on_sensors_apply(self, vals):
        """Окно «Датчики»: применить параметры ОБОИХ типов и переставить датчики.

        Карту не пересобираем и маршруты не трогаем — датчики от них зависят, а они от
        датчиков нет. Меняется только расстановка, поэтому достаточно `on_place`."""
        r_before = float(getattr(self.model.p, "threat_R", 0.0))
        for n, v in vals.items():
            setattr(self.model.p, n, v)
        if self.model.p.threat_N < 1 or self.model.p.threat_R <= 0:
            self.view.flash_title("Малых датчиков должно быть ≥ 1, радиус > 0.")
            return
        # шаг сетки кандидатов подгоняется под радиус — но только если радиус СМЕНИЛСЯ,
        # иначе затирали бы значение, которое пользователь только что ввёл в этом окне
        if abs(float(self.model.p.threat_R) - r_before) > 1e-9:
            self.model.sync_cand_step()
        self.view.set_param_values(self.model.p)
        self.view.refresh_sensors_dialog()
        if self.model.grid is None:
            self.view.set_title("Параметры датчиков приняты. Нажмите «Построить карту».")
            return
        self.on_place()

    def on_input_apply(self, vals):
        gap_before = getattr(self.model.p, "threat_max_gap_km", None)
        for n, v in vals.items():
            setattr(self.model.p, n, v)
        gap_changed = (getattr(self.model.p, "threat_max_gap_km", None) != gap_before)
        # РАЗРЫВ меняет саму проходимость: коридоры сшиваются/рвутся, а значит меняются и
        # область залёта, и набор возможных маршрутов. Всё накопленное построено по старой
        # проходимости — сбрасываем и считаем заново (контекст выборки модель пересоберёт
        # сама: разрыв входит в его ключ).
        if gap_changed:
            self.model.routes = []
            self.model.iter_routes = []
            self.model.iter_iteration = 0
        # Маршруты, область залёта и датчики зависят от запаса хода и разрыва —
        # пересчитываем ВСЕГДА, а не только то, что сейчас показано на карте. Раньше
        # условие смотрело на чекбоксы: при выключенных «маршрутах» правка L_max меняла
        # только надпись, а область, линии и датчики оставались от прежних значений —
        # подхватывалось лишь после «Сбросить» или «Расставить».
        if self.model.grid is not None:
            if self.view.get_toggles().get("show_iter") and self.model.iter_routes:
                self._run_async("iter", self.model.iterate_routes)
            else:
                self.model.routes = []
                self._run_async("routes", self.model.plan_routes)  # и область залёта, и датчики
        else:
            self._render_all()
        self.view.set_title("Входные данные применены — пересчёт области, маршрутов и датчиков."
                            + (" Разрыв изменён." if gap_changed else ""))

    # ---- смена приоритета по весу (max/medium/min/mix) ----
    def on_iter_mode(self, key):
        self.model.p.threat_iter_mode = key
        if (self.model.grid is not None and self.view.get_toggles().get("show_iter")
                and not self._busy):
            self._run_async("iter", self.model.iterate_routes)

    # ---- смена разброса по карте (center/middle/edge/mix) ----
    def on_iter_spread(self, key):
        self.model.p.threat_iter_spread = key
        if (self.model.grid is not None and self.view.get_toggles().get("show_iter")
                and not self._busy):
            self._run_async("iter", self.model.iterate_routes)

    # ---- смена профиля расхода запаса хода (late/early/even) ----
    # Влияет только на ИТЕРАЦИИ: «возможные маршруты» строятся веером всех повадок,
    # чтобы показывать возможности целиком, а не один выбранный сценарий.
    def on_iter_spend(self, key):
        self.model.p.threat_spend = key
        if (self.model.grid is not None and self.view.get_toggles().get("show_iter")
                and not self._busy):
            self._run_async("iter", self.model.iterate_routes)

    # ---- запуск тяжёлого расчёта в фоне ----
    def _run_async(self, kind, work_fn):
        """Запустить тяжёлую функцию модели `work_fn` в ФОНОВОМ потоке (пул QThreadPool),
        пометив интерфейс «занят». `kind` — метка задачи (build/place/routes/iter), по
        ней `_on_task_done` решит, что отрисовать. Пока идёт расчёт — новый не запускаем."""
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
        """Приходит в ОСНОВНОЙ поток по сигналу завершения фоновой задачи. Разбирает метку
        `kind` и обновляет карту/показатели: build → строить маршруты; place → показать
        датчики; routes → огибающая+линии; iter → расставить датчики по выборке пролётов."""
        kind, err = payload
        self._busy = False
        self.view.set_busy(False)
        if err is not None:
            self.view.flash_title(f"Ошибка: {err}")
            return
        if kind == "build":
            self.view.set_source(self.model.source)
            # файл высот мог появиться (или исчезнуть) между запусками — сверить кнопку
            self.view.set_relief_button(self.model.relief_on, self.model.has_dem())
            self._render_all()
            self.view.set_title("Карта построена. Считаю все возможные маршруты…")
            self._full_metrics()
            tg = self.view.get_toggles()
            if tg.get("show_iter"):                      # идут итерации — их и обновить
                self._run_async("iter", self.model.iterate_routes)
            else:                                        # ВСЕГДА считаем все возможные маршруты
                self._run_async("routes", self.model.plan_routes)
        elif kind == "place":
            self._render_all()
            me = self.model.metrics()
            self.view.set_title(
                f"Датчиков {me['n_sensors']} · засечено пролётов "
                f"{me.get('detect_k_frac', 0)*100:.0f}% (кратность ≥{self.model.p.threat_k}) · "
                f"покрыто веса {me['covered_frac']*100:.0f}% · "
                f"режим {MODE_LABELS[self.model.p.mode]}")
            self._full_metrics()
        elif kind == "routes":
            # Датчики стоят по ЭТОЙ ЖЕ выборке (до итераций — по «возможным маршрутам»,
            # см. _sample_for_sensors), поэтому после её пересчёта их надо обновить.
            # Раньше они оставались от прежнего запаса хода: область залёта на карте
            # выросла, маршруты перестроились, а кольца датчиков висели по-старому —
            # и подвинуть их можно было только «Расставить» или сбросом.
            if len(self.model.sensors):
                self.model.place_sensors()
                self._sensors_at = len(self.model.iter_routes)
            self._render_all()
            self._full_metrics()
            n, area = self.model.reachable_stats()
            # Всего маршрутов — астрономически много (~1e107), нарисовать их нельзя; полную
            # картину даёт ЗАКРАШЕННАЯ ОБЛАСТЬ, а линии — выборка, разложенная по ней широко.
            total = self.model.count_routes()
            tot_s = f" · всего путей ≈{total:.1e}" if total > 0 else ""
            self.view.set_title(
                f"Мест пролёта (≤L_max): {area:.0f} км² ({n} ячеек){tot_s} · "
                f"показано {len(self.model.routes)} · L_max={self.model.p.threat_L_max:g} км.")
        elif kind == "iter":
            self.view.iter_clear_current()               # пакетно — без летящего маркера
            self._maybe_refresh_sensors(force=True)      # датчики по выборке пролётов
            self._render_all()
            self._full_metrics()
            self.view.set_title(self._iter_title() + " (пакетно). Датчики по выборке пролётов.")

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
        """Клик ТОЛЬКО задаёт цель. Пересчёт — по кнопке «Применить».

        Почему не сразу: цель и сектор задаются подряд («поставил цель — очертил
        сектор»), и пересчёт после каждого клика шёл дважды, причём первый — по
        промежуточному состоянию. Теперь оба клика копят изменения, а «Применить»
        считает один раз по итоговой обстановке."""
        self.model.set_target(x, y)                       # пересчитает авто-запас хода
        self._pending_recalc = True
        self._render_after_input(f"Цель задана: ({x:.0f}, {y:.0f}) км. ")

    # ---- «задать сектор появления» кликом по краю карты ----
    def on_set_sector(self, x, y):
        """Клик задаёт ось «цель → точка»; модель отбирает по весам точки входа.
        Маршруты пересчитываются по «Применить» — см. `on_set_target`."""
        if self.model.grid is None:
            self.view.set_title("Сектор задаётся по построенной карте — «Построить карту».")
            return
        pts = self.model.set_sector(x, y)
        if not pts:
            self.view.set_title("В сектор не попал ни один край карты — укажите точку "
                                "на другой стороне.")
            return
        self._pending_recalc = True
        self._render_after_input("Сектор задан: %d точек входа. " % len(pts))

    def on_clear_sector(self):
        if not self.model.clear_sector():
            return
        self._pending_recalc = True
        self._render_after_input("Сектор убран: точка появления одна. ")

    def _render_after_input(self, msg):
        """Показать заданное (цель, сектор, точки входа) и напомнить про «Применить».

        Сам расчёт здесь НЕ запускается: модель уже сбросила маршруты и датчики — они
        построены по прежней обстановке, показывать их дальше было бы враньём."""
        # `_render_all` сам рисует цель, сектор, точки входа и обновляет |AB| с запасом
        # хода в окне входных данных — отдельных вызовов здесь не нужно
        self._render_all()                                # маршруты сброшены — убрать с карты
        if self.model.grid is None:
            self.view.set_title(msg + "Нажмите «Построить карту».")
        else:
            self.view.set_title(msg + "Нажмите «Применить» — пересчёт маршрутов.")

    # ---- сброс: убрать датчики и заданную цель (карта остаётся) ----
    def on_reset(self):
        self._anim_stop()
        self.view.iter_clear_current()
        # ЗАПРЕТНЫЕ ЗОНЫ убираются ТОЛЬКО здесь: пересборка карты и добавление рельефа их
        # сохраняют — они нарисованы пользователем и от местности не зависят.
        self.model.clear_no_fly_zones()
        self.model.sensors = np.empty((0, 2), float)
        self.model.target_km = None
        self.model.clear_sector()          # сектор отсчитывается от цели — уходит вместе с ней
        self._pending_recalc = False       # считать после сброса нечего
        self.model.routes = []
        self.model.iter_routes = []
        self.model.iter_iteration = 0
        self.model._iter_ctx = None
        self.model.route_area = None
        self.model._metrics = {}
        self._cur_route = None
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
        # ШАГ СЕТКИ ДАТЧИКОВ подгоняется под радиус, но только если радиус СМЕНИЛСЯ:
        # иначе затирали бы значение, которое пользователь только что ввёл руками
        before = float(self.model.p.threat_cand_step_km)
        if abs(self.model.sync_cand_step() - before) > 1e-9:
            self.view.set_param_values(self.model.p)     # показать пересчитанный шаг
        # ОТЛОЖЕННЫЙ ПЕРЕСЧЁТ: цель и/или сектор задавали кликом, и маршруты ждут именно
        # этой кнопки. Считается ОДИН раз по итоговой обстановке, даже если правок было
        # несколько подряд. Датчики после этого расставит `_on_task_done`.
        if self._pending_recalc and self.model.grid is not None:
            self._pending_recalc = False
            if self.view.get_toggles().get("show_iter"):
                self._run_async("iter", self.model.iterate_routes)
            else:
                self._run_async("routes", self.model.plan_routes)
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
        # итерации (веер ИЛИ обобщённая выборка) включили, а их ещё нет -> сгенерировать
        # в фоне (стохастика, ~1 с). Обобщённая выборка тоже строится из этих маршрутов.
        if ((t.get("show_iter") or t.get("show_iter_gen")) and self.model.grid is not None
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
        """Собрать текущее состояние модели и целиком перерисовать карту с учётом галочек
        показа (тепловая карта, слои, огибающая, маршруты, итерации, датчики и т.д.).
        Единая точка отрисовки — вызывается после любого изменения данных."""
        t = self.view.get_toggles()
        g = self.model.grid
        # какие слои вообще есть — чтобы галка не стояла над пустотой (см. set_layer_enabled)
        self.view.set_layer_enabled(
            grid=g is not None,
            dem=g is not None and g.relief_height() is not None,
            relief=g is not None and g.relief_k() is not None,
            iter=bool(self.model.iter_routes))
        entry, target = self.model.entry_target_km()
        self.view.render_entry_target(entry, target)
        # сектор появления рисуется здесь же: иначе он пропадал бы при любой перерисовке
        self.view.render_sector(self.model.sector_edges_km(), self.model.entry_points, t)
        self.view.render_no_fly(self.model.no_fly_zones, t)
        # |AB| вход->цель (для окна входных данных)
        ab = float(np.hypot(target[0] - entry[0], target[1] - entry[1]))
        self.model.p.ab_distance = round(ab, 1)
        self.view.set_ab_distance(ab)
        self.view.refresh_input_dialog()                  # окно «Входные данные» — актуальный L_max
        if g is None:
            return
        extent = g.extent_km()
        # рельеф: карта высот берётся в РОДНОМ разрешении (сетка 500 м для показа груба),
        # приоритет — с сетки, потому что он показывает ровно то, что ушло в вес
        if t.get("show_relief"):
            disp = self.model.relief_display()
            if disp is None:
                self.view.render_relief(g.relief_height(), extent, t)
            else:
                self.view.render_relief(disp[0], disp[1], t)
        else:
            self.view.render_relief(None, None, t)
        self.view.render_relief_priority(g.relief_k(), g.relief_cut(), extent, t)
        self.view.render_threat(g.weight, extent, t)
        self.view.render_exclusions(g.water_mask(), g.urban_mask(), extent, t)
        self.view.render_layers(self.model.layers,
                                self.model.layers.get("bridge_pts", []), t,
                                built_mask=g._built_cells, extent=extent)
        if t["show_cand"] and len(self.model.candidates) == 0:
            self.model.candidates = self.model.candidate_positions()
        self.view.render_candidates(self.model.candidates, t)
        self.view.render_routes(self.model.routes, self.model.route_area, extent, t)
        self.view.render_iter_routes(self.model.iter_routes, t)
        self._refresh_generalized(t)
        self.view.render_iter_heat(
            self.model.route_density_field() if t.get("show_iter_heat") else None, extent, t)
        if t.get("show_cross"):
            self.view.render_crossings(g.crossing_cells_km(), t)
        else:
            self.view.render_crossings(None, t)
        self.view.render_sensors(self.model.sensors, self.model.p.threat_R,
                                 getattr(self.model, 'sensors_big', None),
                                 getattr(self.model.p, 'threat_R_big', 0.0))

    def on_iter_gen_frac(self, frac):
        """Пользователь сменил долю обобщённой выборки (поле «обобщ. %»). Запоминаем и, если
        слой включён, сразу перерисовываем."""
        self.model.p.threat_iter_gen_frac = float(frac)
        if self.view.get_toggles().get("show_iter_gen"):
            self._refresh_generalized()

    def _refresh_generalized(self, toggles=None):
        """Обновить слой ОБОБЩЁННОЙ выборки (доля задаётся полем «обобщ. %», по умолч. 10 %):
        считаем подмножество в модели только когда чекбокс включён (иначе слой очищается).
        Долю берём из поля -> меняется без перезапуска. Дёшево — вызываем и по ходу анимации
        (на завершении каждого пролёта), чтобы доля пересчитывалась «на этот момент»."""
        t = toggles or self.view.get_toggles()
        frac = self.view.get_iter_gen_frac()
        self.model.p.threat_iter_gen_frac = frac
        gen = self.model.generalized_sample(frac) if t.get("show_iter_gen") else []
        self.view.render_generalized(gen, t)

    # ---- показатели ----
    def _idle_metrics(self):
        kx0, kx1, ky0, ky1 = self.model.bbox_km
        cell = THREAT_CELL_M / 1000.0
        nx = int(round((kx1 - kx0) / cell)); ny = int(round((ky1 - ky0) / cell))
        self.view.set_metrics([
            "ВКЛАДКА 3 · цифровая карта угроз", "",
            "Участок (демо-регион):",
            f"  {kx1-kx0:.0f}×{ky1-ky0:.0f} км",
            f"  сетка {cell*1000:.0f} м → {nx}×{ny} = {nx*ny} ячеек", "",
            "1) «Построить карту» — наложение слоёв",
            "   (реки/дороги/ЛЭП/мосты/…) на сетку;",
            "2) «Расставить датчики» — по сумме весов",
            "   (макс. покрытого веса + разнос),",
            "   без датчиков на воде.", "",
            "Веса слоёв — в config.THREAT_LAYERS.",
        ])

    def _relief_lines(self):
        """Блок показателей по рельефу. Пока рельеф не добавлен — одна строка-подсказка,
        чтобы было видно, что механизм есть и чем он управляется."""
        g = self.model.grid
        if g is None or not self.model.relief_on or g.relief_k() is None:
            hint = ("нет файла высот" if not self.model.has_dem()
                    else "выключен · кнопка «Добавить рельеф»")
            return ["РЕЛЬЕФ", f"  {hint}", ""]
        k = g.relief_k(); cut = g.relief_cut(); h = g.relief_height()
        import numpy as _np
        pos = g.weight > 0
        up = int((pos & (k > 1.02)).sum()); dn = int((pos & (k < 0.98)).sum())
        n = max(int(pos.sum()), 1)
        return [
            "РЕЛЬЕФ (в весе)",
            f"  высоты: {_np.nanmin(h):.0f}…{_np.nanmax(h):.0f} м",
            f"  укрытий: {up} ({100.0*up/n:.0f} %) · "
            f"открытых: {dn} ({100.0*dn/n:.0f} %)",
            f"  снято как гора: {int(cut.sum())} ячеек",
            "",
        ]

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
            "МЕСТА ПРОЛЁТА (граф, ≤ L_max)",
            f"  достижимо: {self.model.reachable_stats()[0]} ячеек "
            f"= {self.model.reachable_stats()[1]:.0f} км²",
            f"  линий-примеров: {len(self.model.routes)} · "
            f"итерац.: {len(self.model.iter_routes)}",
            f"  запас хода L_max: {p.threat_L_max:g} км", "",
        ]
        lines += self._relief_lines()
        lines += [
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
                "",
                f"ЗАСЕЧКА ПРОЛЁТОВ (по {me.get('n_routes_eval', 0)} маршрутам)",
                f"  засечено ≥1 датчиком: {me.get('detect_frac', 0)*100:.0f}%",
                f"  засечено ≥k({p.threat_k}): {me.get('detect_k_frac', 0)*100:.0f}%",
                f"  средняя кратность: {me.get('mean_hits', 0):.1f}",
            ]
        self.view.set_metrics(lines)

    def run(self):
        self.view.show()
