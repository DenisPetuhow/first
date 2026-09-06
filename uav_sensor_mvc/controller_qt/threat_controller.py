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
import os

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
        # Снимок состояния ПЕРЕД моделированием — к нему возвращает кнопка «Очистить».
        # None — моделирование ещё не запускали, возвращаться некуда.
        self._pre_iter = None
        # цель/сектор заданы кликом, но маршруты ещё не пересчитаны: ждём «Применить»
        self._pending_recalc = False
        view.set_callbacks(
            on_build=self.on_build, on_relief=self.on_relief,
            on_place=self.on_place, on_apply=self.on_apply,
            on_reset=self.on_reset, on_reset_view=self.on_reset_view,
            on_clear_iter=self.on_clear_iter,
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
            on_sensors_apply=self.on_sensors_apply, on_set_area=self.on_set_area,
            on_clear_area=self.on_clear_area,
            # датчики, заданные человеком (задача 8.7)
            on_manual_add=self.on_manual_add, on_manual_remove=self.on_manual_remove,
            on_manual_edit=self.on_manual_edit, on_clear_sensors=self.on_clear_sensors,
            on_sensor_delete=self.on_sensor_delete,
            on_manual_clear=self.on_manual_clear, on_manual_mode=self.on_manual_mode,
            on_sensor_rows=self._sensor_rows,
            # история полётов: выгрузка/загрузка (задача 8.6)
            on_flights_save=self.on_flights_save, on_flights_load=self.on_flights_load,
            on_flights_clear=self.on_flights_clear,
            # окно «Посмотреть маршруты» (задача 8.6, довесок 06.09.2026)
            on_routes_data=self._routes_data_for_viewer,
            on_route_preview=self.on_route_preview,
            on_route_preview_reset=self.on_route_preview_reset)
        # СТАРТ: район не задан — расчётные кнопки закрыты, синей рамки нет
        view.set_area_ready(model.area_ready,
                            model.area_size_km() if model.area_ready else None,
                            model.bbox_km if model.area_ready else None)
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
        # ПОКАЗ ВКЛЮЧАЕТСЯ ВМЕСТЕ С РЕЛЬЕФОМ. Иначе рельеф применялся к весам, а на карте
        # ничего не менялось — галка показа осталась снятой, и выглядело это как «рельеф
        # не воспроизводится» (заказчик 04.09.2026). Выключаем — гасим и показ.
        self.view.set_relief_shown(self.model.relief_on)
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
            self._snapshot_before_iter()                  # чтобы «Очистить» вернуло это
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
        self._render_sensors()
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
            self.view.refresh_route_viewer()   # маршрут долетел — виден в списке сразу
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
            self._snapshot_before_iter()                  # чтобы «Очистить» вернуло это
            if not self.model.iter_reset():
                self.view.flash_title("Цель недостижима по коридорам.")
                return
            self._sensors_at = 0                          # выборка начата заново
        route = self.model.iter_step()
        self.view.iter_show_accumulated(self.model.iter_routes)
        if route is not None:
            self.view.iter_update_flight(route, len(route) - 1)   # весь маршрут + БПЛА в цели
            self.view.refresh_route_viewer()       # маршрут добавлен — виден в списке сразу
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
        self._snapshot_before_iter()
        self._run_async("iter", self.model.iter_batch)

    # ---- «ОЧИСТИТЬ»: назад к состоянию ДО МОДЕЛИРОВАНИЯ (заказчик 05.09.2026) ----
    def _snapshot_before_iter(self):
        """Запомнить расстановку и заданные датчики ПЕРЕД запуском итераций.

        ⚠️ Снимок делается сам, а не по кнопке: человек нажимает «Пуск» или «Пакетно», не
        думая о том, что потом захочет вернуться. Момент — старт выборки, то есть когда
        `iter_iteration == 0`: повторный запуск поверх уже накопленного снимок НЕ
        обновляет, иначе «до моделирования» означало бы «до последней добавки».

        Что запоминаем: расстановку со всеми её спутниками (тип, режим, кем поставлен),
        заданные человеком датчики (их динамические позиции при итерациях меняются) и
        показатели. Карту, рельеф и район — нет: моделирование их не трогает."""
        import copy
        m = self.model
        if m.iter_iteration and self._pre_iter is not None:
            return                                    # выборка уже идёт — снимок не трогаем
        self._pre_iter = dict(
            sensors=np.array(m.sensors, float, copy=True),
            sensors_big=np.array(m.sensors_big, float, copy=True),
            sensors_type=np.array(m.sensors_type, int, copy=True),
            sensors_static=np.array(m.sensors_static, bool, copy=True),
            sensors_big_static=np.array(m.sensors_big_static, bool, copy=True),
            sensors_manual=np.array(m.sensors_manual, bool, copy=True),
            sensors_big_manual=np.array(m.sensors_big_manual, bool, copy=True),
            manual=copy.deepcopy(m.manual_sensors),
            metrics=copy.deepcopy(m.metrics()),
        )

    def on_clear_iter(self):
        """Кнопка «Очистить»: убрать итерации и вернуть состояние до моделирования.

        Отличие от «Сброса»: тот обнуляет всё, оставляя район, весовую карту и рельеф.
        Здесь карта, маршруты «все возможные» и заданные условия остаются — уходит
        только то, что дало моделирование."""
        import copy
        m = self.model
        self._anim_stop()
        m.iter_routes = []
        m.iter_iteration = 0
        self._sensors_at = 0
        snap = self._pre_iter
        if snap is not None:
            m.sensors = np.array(snap["sensors"], float, copy=True)
            m.sensors_big = np.array(snap["sensors_big"], float, copy=True)
            m.sensors_type = np.array(snap["sensors_type"], int, copy=True)
            m.sensors_static = np.array(snap["sensors_static"], bool, copy=True)
            m.sensors_big_static = np.array(snap["sensors_big_static"], bool, copy=True)
            m.sensors_manual = np.array(snap["sensors_manual"], bool, copy=True)
            m.sensors_big_manual = np.array(snap["sensors_big_manual"], bool, copy=True)
            m.manual_sensors = copy.deepcopy(snap["manual"])
            m._metrics = copy.deepcopy(snap["metrics"])
        self.view.iter_show_accumulated([])
        self.view.iter_clear_current()
        self.view.set_iter_checked(False)
        self.view.refresh_sensor_table(self._sensor_rows())
        self.view.set_manual_count(len(m.manual_sensors))
        self._render_all()
        self._full_metrics()
        self.view.refresh_route_viewer()   # список «своих маршрутов» опустел — обновить
        self.view.set_title(
            "Итерации убраны — вернулись к состоянию до моделирования."
            if snap is not None else
            "Итерации убраны. Снимка «до моделирования» не было — датчики остались "
            "теми, что посчитаны сейчас.")

    # ================= ИСТОРИЯ ПОЛЁТОВ: выгрузка/загрузка (задача 8.6) =================
    def on_flights_save(self, path):
        """Кнопка «Выгрузить историю полётов». Путь уже выбран диалогом во View —
        здесь только проверка готовности (заказчик: «если пакетное моделирование не
        завершилось — предупреждение, что нет данных») и сама запись."""
        from model import flight_log
        if not self.model.iterations_complete():
            self.view.flash_title(
                "Нет данных: пакет итераций не завершён (%d/%d) — дойдите «Пуск»/"
                "«Пакетно» до конца."
                % (len(self.model.iter_routes), int(self.model.p.threat_iter_routes)))
            return
        try:
            n_routes, n_points = flight_log.save_flights(path, self.model)
        except OSError as ex:
            self.view.flash_title("Файл не записан: %s" % ex)
            return
        msg = "Записано в файл: маршрутов %d, точек %d." % (n_routes, n_points)
        size_mb = os.path.getsize(path) / (1024.0 * 1024.0)
        if size_mb > 8.0:                      # план 8 §8.6.4: предупредить про размер
            msg += "  ⚠ файл %.1f МБ" % size_mb
        self.view.flash_title(msg)

    def on_flights_load(self, path):
        """Кнопка «Загрузить историю полёта». Маршруты идут В ОТДЕЛЬНОЕ поле
        `model.loaded_routes` — НЕ в `iter_routes` (план 8 §8.6.4: иначе счётчик
        итераций и вся статистика соврут). Участок и якорь НЕ проверяются — координаты
        в градусах самодостаточны, как и у заданных вручную датчиков."""
        from model import flight_log
        try:
            routes_deg, report = flight_log.load_flights(path)
        except OSError as ex:
            self.view.flash_title("Файл не прочитан: %s" % ex)
            return
        reason = flight_log.check_compatible(routes_deg, report)
        if reason:
            self.view.flash_title("Файл не подходит: %s" % reason)
            return
        n = self.model.load_routes_lonlat(routes_deg)
        self.model.show_loaded_routes = True
        self.view.set_loaded_routes_checked(True)
        msg = "Загружено маршрутов: %d, точек: %d." % (n, report["n_points"])
        if report["rejected"]:
            msg += "  Отброшено строк: %d." % len(report["rejected"])
        self.view.flash_title(msg)
        self._render_all()
        self.view.refresh_route_viewer()   # «Загруженная история» появилась/сменилась

    def on_flights_clear(self):
        """Кнопка «Удалить выборку» — убирает ТОЛЬКО загруженную историю (заказчик
        06.09.2026): после этого «Расставить датчики» снова считает по зоне пролёта
        (или по своим итерациям, если они уже накоплены), как было бы без загрузки.
        В отличие от «Сброса» — датчики, цель и зоны не трогает."""
        n = self.model.clear_loaded_routes()
        self.view.set_loaded_routes_checked(False)
        self.view.flash_title(
            "Загруженная история убрана (%d маршрутов)." % n if n else
            "Загруженной истории и не было.")
        self._render_all()
        self.view.refresh_route_viewer()   # «Загруженная история» опустела — обновить

    # ---- окно «Посмотреть маршруты» (задача 8.6, довесок 06.09.2026) ----
    def _routes_data_for_viewer(self):
        """Данные для окна просмотра: свои пройденные маршруты и загруженная история,
        оба в градусах — то же представление, что в файле."""
        return self.model.routes_lonlat(), self.model.loaded_routes_lonlat()

    def on_route_preview(self, is_loaded, index):
        """Подсветить на карте ОДИН выбранный маршрут (км — тот же фрейм, что у карты).
        Индекс — по тому же списку, что вернул `_routes_data_for_viewer`."""
        src = self.model.loaded_routes if is_loaded else self.model.iter_routes
        if 0 <= index < len(src):
            self.view.render_route_preview(src[index])

    def on_route_preview_reset(self):
        self.view.render_route_preview(None)

    # ---- окно «Исходные данные»: применить сразу (не блокирует программу) ----
    # ⚠️ ОДИН ОБРАБОТЧИК НА ВСЁ ОКНО (задача 8.7). Прежде их было два — «Датчики» и
    # «Входные данные», каждый со своим окном. Теперь окно одно и отдаёт все поля разом,
    # а что пересчитывать, решается по тому, ЧТО именно изменилось: правка радиуса
    # датчика не должна пересчитывать маршруты, а правка разрыва — обязана.
    ROUTE_KEYS = ("threat_L_max", "threat_L_max_manual", "threat_speed_kmh",
                  "threat_bank_deg", "threat_turn_interval_km", "threat_max_gap_km",
                  "threat_corridor_slack_km")

    def on_sensors_apply(self, vals):
        """Применить поля окна «Исходные данные».

        ⚠️ РАССТАНОВКУ НЕ ЗАПУСКАЕТ (правило заказчика 05.09.2026). «Применить» здесь
        значит «принять введённые данные», и не более: дальше они учитываются там, где
        нужны — при добавлении датчика мышью, при расстановке по кнопке основного окна,
        при расчёте маршрутов. Раньше кнопка сама расставляла датчики, и правка одного
        радиуса запускала секундный пересчёт, которого никто не просил.

        Исключение — параметры МАРШРУТА (запас хода, разрыв и т. п.): они меняют саму
        проходимость карты и область залёта, поэтому пересчёт остаётся."""
        p = self.model.p
        before = {n: getattr(p, n, None) for n in self.ROUTE_KEYS}
        r_before = float(getattr(p, "threat_R", 0.0))
        for n, v in vals.items():
            setattr(p, n, v)
        if p.threat_N < 1 or p.threat_R <= 0:
            self.view.flash_title("Датчиков типа 1 должно быть ≥ 1, радиус > 0.")
            return
        # Шаг сетки кандидатов подгоняется под радиус — но только если радиус СМЕНИЛСЯ,
        # иначе затирали бы значение, которое пользователь только что ввёл в этом окне.
        if abs(float(p.threat_R) - r_before) > 1e-9:
            self.model.sync_cand_step()
        self.view.set_param_values(p)
        route_changed = any(getattr(p, n, None) != before[n] for n in self.ROUTE_KEYS)
        if route_changed:
            # ⚠️ ПРИЗНАК СМЕНЫ РАЗРЫВА СЧИТАЕМ ЗДЕСЬ и передаём готовым: значения уже
            # проставлены выше, и внутри `on_input_apply` сравнивать было бы не с чем —
            # «до» и «после» там совпали бы всегда, и сброс маршрутов молча не сработал.
            self.on_input_apply({}, gap_changed=(
                getattr(p, "threat_max_gap_km", None) != before["threat_max_gap_km"]))
            return
        if self.model.grid is None:
            self.view.set_title("Исходные данные приняты. Нажмите «Построить карту».")
            return
        # Датчики НЕ переставляем — только обновляем показатели и карту под новые числа.
        self._full_metrics()
        self._render_all()
        n = len(self.model.sensors) + len(self.model.sensors_big)
        self.view.set_title(
            "Исходные данные приняты." + (
                " Датчики на карте прежние — нажмите «Расставить датчики», чтобы "
                "пересчитать их по новым данным." if n else
                " Нажмите «Расставить датчики»."))

    def on_input_apply(self, vals, gap_changed=None):
        """Пересчёт после правки параметров МАРШРУТА (запас хода, разрыв, скорость…).

        `vals` может быть пустым: значения уже проставлены вызывающим — тогда это просто
        «пересчитай под новые параметры», а `gap_changed` приходит готовым."""
        gap_before = getattr(self.model.p, "threat_max_gap_km", None)
        for n, v in vals.items():
            setattr(self.model.p, n, v)
        if gap_changed is None:
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

    def _render_sensors(self, empty=False):
        """Отдать датчики на отрисовку вместе с типами и признаком «закреплён».

        ⚠️ ОДНО МЕСТО НА ВСЕ ВЫЗОВЫ. Раньше вызовов было три, с разными наборами
        аргументов; теперь у каждого датчика есть ещё тип, признак закрепления и свой
        радиус, и рассинхрон между вызовами (где-то передали, где-то нет) означал бы,
        что на карте датчики выглядят по-разному в зависимости от того, что их
        обновило."""
        import numpy as _np
        from model.sensors import sensor_types
        m, p = self.model, self.model.p
        if empty:
            # ⚠️ ДАЖЕ КОГДА РАСЧЁТА НЕТ, поставленные мышью датчики показываем: карта
            # может быть ещё не построена, а человек уже расставляет — и он должен
            # видеть, куда попал клик.
            self.view.render_sensors(
                _np.empty((0, 2), float), p.threat_R,
                _np.empty((0, 2), float), p.threat_R_big,
                pending=[(s.x_km, s.y_km, s.type_id, s.static)
                         for s in m.manual_sensors])
            return
        radii = {s.type_id: s.r_km for s in sensor_types(p)}
        # ПОСТАВЛЕННЫЕ МЫШЬЮ, НО ЕЩЁ НЕ УЧТЁННЫЕ РАСЧЁТОМ — показываем сразу. Отбираем
        # тех, кого нет в текущей расстановке: после «Расставить датчики» они уже там, и
        # рисовать их вторично значило бы удваивать значки.
        #
        # ⚠️ ФЛАГ `placed` ЗДЕСЬ ГЛАВНЕЕ РАССТОЯНИЯ. Одной проверки «нет в расстановке»
        # мало: ДИНАМИЧЕСКИЙ датчик в неё и не попадает — алгоритм ставит его в другом
        # месте, — и точка клика оставалась на карте навсегда. Со стороны это выглядело
        # так, будто динамические не двигаются вовсе (замечание заказчика 05.09.2026).
        # Учтённые расстановкой заявки не рисуем: их место теперь показывает расчёт.
        placed = list(_np.asarray(m.sensors, float)) + list(_np.asarray(m.sensors_big, float))
        pending = []
        for mm in m.manual_sensors:
            if getattr(mm, "placed", False):
                continue
            near = any(abs(pt[0] - mm.x_km) < 1e-3 and abs(pt[1] - mm.y_km) < 1e-3
                       for pt in placed)
            if not near:
                pending.append((mm.x_km, mm.y_km, mm.type_id, mm.static))
        self.view.render_sensors(
            m.sensors, p.threat_R, getattr(m, "sensors_big", None),
            getattr(p, "threat_R_big", 0.0),
            types=getattr(m, "sensors_type", None),
            static=getattr(m, "sensors_static", None),
            big_static=getattr(m, "sensors_big_static", None),
            manual=getattr(m, "sensors_manual", None),
            big_manual=getattr(m, "sensors_big_manual", None),
            type_radii=radii, pending=pending)

    # ================= ДАТЧИКИ, ЗАДАННЫЕ ЧЕЛОВЕКОМ (задача 8.7) =================
    def on_manual_add(self, type_id, static, lon, lat):
        """Добавить датчик по координатам (клик по карте, строка таблицы или файл)."""
        self.model.add_manual_lonlat(type_id, static, lon, lat)
        self._after_manual_change()

    def on_manual_remove(self, index):
        """Убрать датчик по номеру строки таблицы.

        ⚠️ НОМЕР СТРОКИ — НЕ НОМЕР В `manual_sensors`. В режиме расчёта таблица
        показывает результат расстановки, где заданные вручную вперемешку с
        подобранными: удалять по такому номеру значит удалить не тот датчик. Поэтому
        строка сперва переводится в позицию, а позиция — в запись."""
        rows = self._sensor_rows()
        if not (0 <= int(index) < len(rows)):
            return
        r = rows[int(index)]
        best, best_d = None, 1e-3
        for i, m in enumerate(self.model.manual_sensors):
            d = ((m.x_km - r["x_km"]) ** 2 + (m.y_km - r["y_km"]) ** 2) ** 0.5
            if d <= best_d:
                best, best_d = i, d
        if best is None:
            self.view.flash_title("Этот датчик поставила программа — его нельзя убрать "
                                  "из таблицы: измените число датчиков типа.")
            return
        self.model.remove_manual_sensor(best)
        self._after_manual_change()

    def on_manual_edit(self, index, type_id, static, lon, lat):
        """Изменить датчик строкой таблицы: тип, режим и координаты — одним действием.

        ⚠️ ЗАПИСЬ ПРАВИТСЯ НА МЕСТЕ, а не «удалить и добавить заново». Пара вызовов
        ломалась о то, что в режиме расчёта таблица показывает РЕЗУЛЬТАТ расстановки:
        удаление не находило ручной записи и отказывалось работать, а добавление создавало
        лишний датчик — правка выглядела «не работает» (заказчик 05.09.2026). Заодно
        сохраняется ПОРЯДОК записей: строка остаётся на своём месте в таблице.

        Строку, которую поставила программа (её нет среди заданных), правка превращает в
        заданную: человек указал для неё место — значит теперь место выбрал он."""
        rows = self._sensor_rows()
        if not (0 <= int(index) < len(rows)):
            return
        r = rows[int(index)]
        rec = self._manual_at(r["x_km"], r["y_km"])
        if rec is None:
            rec = self.model.add_manual_lonlat(int(type_id), bool(static), lon, lat)
        else:
            self.model.update_manual_lonlat(rec, int(type_id), bool(static), lon, lat)
        # ⚠️ ДВИГАЕМ И САМУ РАССТАНОВКУ, а не только заявку (заказчик 05.09.2026: «сразу
        # отобразиться на карте, перерисовать данный датчик»). Без этого на карте
        # оказывались бы двое: старый — из расчёта, новый — как «только что поставленный».
        if self.model.move_placed_sensor((r["x_km"], r["y_km"]),
                                         (rec.x_km, rec.y_km),
                                         int(type_id), bool(static)):
            rec.placed = True                 # заявка уже отражена в расстановке
            self.model._metrics = self.model._evaluate_current()
        self._after_manual_change()

    def on_sensor_delete(self, lon, lat):
        """Удалить датчик, выбранный на карте в режиме правки (клавиша Delete).

        ⚠️ ПО КООРДИНАТЕ, А НЕ ПО НОМЕРУ СТРОКИ. Номер меняется при любой правке — после
        переноса он указывает уже на другой датчик, и удалялся бы не тот. Убираются обе
        стороны: и заявка человека, и точка в расстановке, — иначе датчик исчезал бы из
        таблицы, но оставался на карте (или наоборот)."""
        from model.geo_frame import lonlat_to_km
        x, y = lonlat_to_km(float(lon), float(lat),
                            self.model.lon0, self.model.lat0)
        rec = self._manual_at(x, y, tol=0.05)
        if rec is not None:
            self.model.manual_sensors.remove(rec)
        gone = self.model.remove_placed_sensor(x, y)
        if gone:
            self.model._metrics = self.model._evaluate_current()
        if not gone and rec is None:
            self.view.flash_title("Датчик не найден — щёлкните точнее по его центру.")
            return
        self._after_manual_change()

    def _manual_at(self, x_km, y_km, tol=1e-3):
        """Заданная человеком запись в этой точке — или None, если её поставил алгоритм."""
        best, best_d = None, tol
        for m in self.model.manual_sensors:
            d = ((m.x_km - x_km) ** 2 + (m.y_km - y_km) ** 2) ** 0.5
            if d <= best_d:
                best, best_d = m, d
        return best

    def on_manual_clear(self):
        self.model.clear_manual_sensors()
        self._after_manual_change()

    def on_clear_sensors(self):
        """Кнопка «Очистить датчики»: убрать с карты ВСЕ — и подобранные, и заданные.

        Отличие от «Сброса»: цель, сектор, запретные зоны, маршруты и итерации остаются
        на месте. Убирается только расстановка, чтобы посчитать её заново — например с
        другими параметрами типов (заказчик 05.09.2026)."""
        self.model._reset_sensors()
        self.model.clear_manual_sensors()
        self.model._metrics = None
        self._after_manual_change()
        self.view.set_title("Датчики убраны с карты. Нажмите «Расставить датчики», "
                            "чтобы расставить заново.")

    def on_manual_mode(self, manual):
        """Переключение «Рассчитать позиции» ⇄ «Задать позиции»."""
        self.model.p.threat_manual_mode = bool(manual)
        self._after_manual_change()

    def _sensor_rows(self):
        """Чем заполнять таблицу окна.

        В режиме «Задать позиции» и до расстановки показываем ЗАДАННЫЕ датчики: иначе
        таблица была бы пуста и человеку некуда было бы добавлять строки. После
        расстановки в режиме расчёта — её РЕЗУЛЬТАТ (требование заказчика: таблица
        заполняется в обоих режимах)."""
        manual = bool(getattr(self.model.p, "threat_manual_mode", False))
        if not manual and len(self.model.sensors) + len(self.model.sensors_big):
            return self.model.sensors_table()
        rows = []
        for m, (tid, static, lon, lat) in zip(self.model.manual_sensors,
                                              self.model.manual_lonlat()):
            rows.append(dict(n=len(rows) + 1, type_id=tid, static=static,
                             lon=lon, lat=lat, x_km=m.x_km, y_km=m.y_km))
        return rows

    def _left_to_place(self):
        """Сколько датчиков алгоритму осталось доставить к заданным человеком.

        Считается ПО ТИПАМ и суммируется: заказ у каждого типа свой, и «20 всего» ничего
        не сказало бы, если пять из них уже стоят типом 2. Отрицательное значение —
        заданных больше, чем заказано: лишние в расстановку не попадут."""
        from model.sensors import sensor_types
        p = self.model.p
        left = 0
        for s in sensor_types(p):
            if not s.active:
                continue
            have = sum(1 for m in self.model.manual_sensors if m.type_id == s.type_id)
            left += int(s.n) - have
        return left

    def _after_manual_change(self):
        """Список заданных датчиков изменился: обновить таблицу и карту.

        ⚠️ В РУЧНОМ РЕЖИМЕ ДАТЧИКИ ПОКАЗЫВАЮТСЯ СРАЗУ. Там расстановка ничего не считает
        (позиции уже названы), это доли секунды — и человек видит результат клика тут же.
        В режиме расчёта пересчитывать на каждый клик нельзя: расстановка занимает
        секунды, а датчики ставят подряд по одному. Поэтому там — подсказка, что нужно
        нажать «Расставить датчики»."""
        manual = bool(getattr(self.model.p, "threat_manual_mode", False))
        if manual and self.model.grid is not None:
            self.model.place_sensors()
        self.view.refresh_sensor_table(self._sensor_rows())
        self.view.set_manual_count(len(self.model.manual_sensors))
        n = len(self.model.manual_sensors)
        n_fix = sum(1 for m in self.model.manual_sensors if m.static)
        out = self.model.manual_outside_area()
        msg = "Задано датчиков вручную: %d%s%s." % (
            n, ", статических: %d" % n_fix if n_fix else "",
            ", ВНЕ РАЙОНА: %d" % out if out else "")
        # ⚠️ СКОЛЬКО ОСТАЛОСЬ ДОСТАВИТЬ — считаем ДО нажатия кнопки (заказчик
        # 05.09.2026). Заданные входят в заказанное число, и человек должен видеть,
        # сколько датчиков алгоритм добавит к его собственным, а не узнавать это постфактум.
        if not manual and n:
            left = self._left_to_place()
            msg += (" Останется доставить: %d." % left if left >= 0 else
                    " Задано БОЛЬШЕ, чем заказано: лишние %d не поместятся." % -left)
        if not manual and n and self.model.grid is not None:
            msg += " Нажмите «Расставить датчики»."
        self.view.set_title(msg)
        self._render_all()

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
            self._full_metrics()
            tg = self.view.get_toggles()
            if tg.get("show_iter"):                      # идут итерации — их и обновить
                self._run_async("iter", self.model.iterate_routes)
            else:
                # ⚠️ МАРШРУТЫ ПОСЛЕ ПОСТРОЕНИЯ НЕ СЧИТАЕМ (правило заказчика 04.09.2026).
                # Раньше здесь безусловно запускался `plan_routes` — самая тяжёлая часть
                # конвейера (два поля Дейкстры, огибающая, выборка путей), и она уходила
                # в работу при каждом построении карты: включили рельеф, сменили слои,
                # поправили район. А нужна она только для датчиков и итераций, и там
                # считается сама, если выборки ещё нет (см. `_work_place`, `on_iter_*`).
                self.view.set_title(
                    "Карта построена. «Расставить датчики» или «Пуск» — "
                    "маршруты посчитаются при первом обращении.")
        elif kind == "place":
            self._render_all()
            self.view.refresh_sensor_table(self._sensor_rows())   # таблица окна
            me = self.model.metrics()
            # В ручном режиме кнопка ничего не переставляла — она посчитала показатели
            # заданной расстановки. Об этом и пишем, иначе слово «расставлено» вводило бы
            # в заблуждение: позиции задал человек, программа их не выбирала.
            manual = bool(getattr(self.model.p, "threat_manual_mode", False))
            # ОТКУДА ВЗЯТА ВЫБОРКА (задача 8.6, заказчик 06.09.2026): если позиции считал
            # алгоритм по ЗАГРУЖЕННОЙ истории полёта — сказать об этом прямо, а не молчать,
            # будто расстановка обычная (по своей выборке или зоне пролёта).
            if manual:
                head = "Задано датчиков %d (позиции ваши, расчёт по ним)" % me["n_sensors"]
            elif getattr(self.model, "sensor_source", None) == "loaded":
                head = ("Датчиков %d (расстановка по ЗАГРУЖЕННОЙ истории маршрутов)"
                        % me["n_sensors"])
            else:
                head = "Датчиков %d" % me["n_sensors"]
            self.view.set_title(
                f"{head} · засечено пролётов "
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
            self.view.refresh_route_viewer()   # пакет добавил маршруты все разом — обновить
            self.view.set_title(self._iter_title() + " (пакетно). Датчики по выборке пролётов.")

    # ---- построение карты (в фоне) ----
    def _work_build(self):
        self.model.build()
        self.model.candidates = self.model.candidate_positions()

    def _work_place(self):
        # ВЫБОРКА СЧИТАЕТСЯ ЗДЕСЬ, ЕСЛИ ЕЁ ЕЩЁ НЕТ. Датчики ставятся по маршрутам
        # (`_sample_for_sensors`): до итераций — по «всем возможным», после — по
        # накопленным пролётам. Раз маршруты больше не считаются при построении карты
        # (04.09.2026), первым их спрашивает тот, кому они нужны.
        if not self.model.iter_routes and not self.model.routes:
            self.model.plan_routes()
        self.model.place_sensors()

    def on_build(self):
        self._run_async("build", self._work_build)

    # ---- РАБОЧИЙ РАЙОН (план 8, задача 8.2) ----
    def on_set_area(self, bbox_lonlat):
        """Задать рабочий район: сетка, веса, маршруты и датчики — только по нему.

        Область ПОКАЗА при этом не меняется: подложка и своя карта видны и за его
        пределами, туда просто не заходит расчёт. Пересборка тяжёлая (чтение `.osm.pbf`
        и наложение слоёв — десятки секунд), поэтому идёт в фоновом потоке."""
        self.model.set_area(bbox_lonlat)
        w_km, h_km = self.model.area_size_km()
        # синяя рамка — по НОВОМУ району: иначе она осталась бы вокруг прежнего
        self.view.set_area_ready(True, (w_km, h_km), self.model.bbox_km)
        # ⚠️ ПРЕДЕЛ РАЙОНА. Дальше ~150 км по широте подложка расходится с векторными
        # слоями сильнее радиуса малого датчика (план 8 §0.4): тайлы в Меркаторе,
        # наши слои — в равнопромежуточной проекции, привязка идёт по углам.
        warn = ""
        if h_km > 150.0 or w_km > 150.0:
            warn = (" ⚠️ Район %.0f×%.0f км — больше 150 км: подложка и слои разойдутся, "
                    "смотрите план 8 §0.4." % (w_km, h_km))
        # ⚠️ КАРТА НЕ СТРОИТСЯ САМА (правило заказчика 04.09.2026). Район задают, глядя на
        # местность, и часто поправляют два-три раза подряд; автоматический пересчёт после
        # каждого движения — это десятки секунд чтения `.osm.pbf` впустую. Считает кнопка
        # «Построить карту» — как и с выбором цифровых карт (`on_choose_data`).
        self.view.set_title("Район %.1f × %.1f км задан. Нажмите «Построить карту».%s"
                            % (w_km, h_km, warn))
        self.view.set_source("район задан · карта не построена")
        self.view.set_metrics(["Район %.1f × %.1f км." % (w_km, h_km),
                               "Нажмите «Построить карту», чтобы наложить слои",
                               "и посчитать веса."])
        self._render_all()          # показать новую рамку и убрать старые слои

    def on_clear_area(self):
        """Убрать рабочий район: всё расчётное обнуляется, остаётся только показ."""
        self._anim_stop()                      # остановить анимацию полёта, если шла
        self.view.iter_clear_current()
        self.view.set_iter_checked(False)
        self._sensors_at = 0
        self.model.clear_area()
        # кнопка рельефа возвращается в исходное «Добавить рельеф»: сам рельеф сброшен
        self.view.set_relief_button(self.model.relief_on, self.model.has_dem())
        self.view.set_area_ready(False)
        self.view.set_source("район не задан")
        self.view.set_metrics(["Район не задан.",
                               "Очертите его мышью либо загрузите свою карту —",
                               "её рамка станет районом моделирования."])
        self._render_all()

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
        # ⚠️ ДАТЧИКИ СБРАСЫВАЮТСЯ ВСЕ. Здесь обнулялись только малые, и большие
        # оставались висеть на карте после «Сброса» (замечание заказчика 05.09.2026).
        # Заодно уходят и поставленные вручную: иначе следующая расстановка вернула бы
        # их обратно как якоря, и сброс выглядел бы неполным.
        self.model._reset_sensors()
        self.model.clear_manual_sensors()
        self.model.clear_loaded_routes()   # загруженная история полётов — тоже входные данные
        self.view.set_loaded_routes_checked(False)
        self.view.render_route_preview(None)   # подсветка маршрута могла указывать в никуда
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
        self.view.refresh_sensor_table([])          # таблица в окне тоже пустеет
        self.view.refresh_route_viewer()            # оба списка маршрутов опустели
        self.view.set_title("Сброшено: датчики (малые, большие и заданные вручную), "
                            "цель, зоны и загруженная история полётов убраны. "
                            "Карта сохранена.")
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
        # чекбокс «показать загруженную выборку» — модель узнаёт о нём здесь: она сама
        # решает (в `_sample_for_sensors`), можно ли по нему ставить датчики (задача 8.6)
        self.model.show_loaded_routes = bool(t.get("show_loaded_routes"))
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
            # СЕТКИ НЕТ (район не задан или убран) — карту нужно ОЧИСТИТЬ, а не просто
            # выйти. Раньше здесь стоял голый `return`, и уже нарисованные весовая карта,
            # рельеф и векторные слои оставались на экране после снятия района: данных
            # нет, а картинка есть (замечание заказчика 04.09.2026).
            self.view.render_threat(None, None, t)
            self.view.render_relief(None, None, t)
            self.view.render_relief_priority(None, None, None, t)
            self.view.render_exclusions(None, None, None, t)
            self.view.render_layers({}, [], t, built_mask=None, extent=None)
            self.view.render_candidates(None, t)
            self.view.render_routes([], None, None, t)
            self.view.render_iter_routes([], t)
            self.view.render_generalized([], t)
            self.view.render_loaded_routes([], t)
            self.view.render_iter_heat(None, None, t)
            self.view.render_crossings(None, t)
            self._render_sensors(empty=True)
            return
        extent = g.extent_km()
        # рельеф: карта высот берётся в РОДНОМ разрешении (сетка 500 м для показа груба),
        # приоритет — с сетки, потому что он показывает ровно то, что ушло в вес
        # ⚠️ ЧТЕНИЕ РЕЛЬЕФА ЗАЩИЩЕНО try. Растр читается с диска ПРИ ОТРИСОВКЕ, и на
        # большом файле (у активной области `arh_hh.tif` — 568 МБ, 20908 × 8256) любая
        # беда — нехватка памяти, битый тайл, срезанный край — валила бы всю перерисовку,
        # а с ней и окно. Показ высот — не то, ради чего стоит терять карту: при сбое
        # гасим слой и пишем причину в заголовок (заказчик 04.09.2026: «при выборе
        # рельефа программа крашится»).
        if t.get("show_relief"):
            try:
                disp = self.model.relief_display()
                if disp is None:
                    self.view.render_relief(g.relief_height(), extent, t)
                else:
                    self.view.render_relief(disp[0], disp[1], t)
            except Exception as e:                       # noqa: BLE001 — донесём в UI
                self.view.render_relief(None, None, t)
                self.view.flash_title("Не удалось показать высоты: %s: %s"
                                      % (type(e).__name__, e))
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
        self.view.render_loaded_routes(self.model.loaded_routes, t)
        self._refresh_generalized(t)
        self.view.render_iter_heat(
            self.model.route_density_field() if t.get("show_iter_heat") else None, extent, t)
        if t.get("show_cross"):
            self.view.render_crossings(g.crossing_cells_km(), t)
        else:
            self.view.render_crossings(None, t)
        self._render_sensors()

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
        lines += self._resource_lines(p)
        if me:
            lines += [
                f"  кандидатов: {me.get('n_candidates', 0)}", "",
                "ВЕС КАРТЫ (по слоям)",
            ]
            by = me.get("by_layer", {})
            for name in sorted(by, key=lambda n: -abs(by[n])):
                lab = THREAT_LAYERS.get(name, {}).get("label", name)
                lines.append(f"  {lab:<18}{by[name]:+8.0f}")
            lines += ["", "РАССТАНОВКА"] + self._placement_lines(me)
            lines += [
                f"  покрытый вес: {me.get('covered_weight', 0):.0f} / "
                f"{me.get('total_weight', 0):.0f}",
                f"  доля покрытия: {me.get('covered_frac', 0)*100:.0f}%",
                "",
                f"ЗАСЕЧКА ПРОЛЁТОВ (по {me.get('n_routes_eval', 0)} маршрутам)",
                f"  засечено ≥1 датчиком: {me.get('detect_frac', 0)*100:.0f}%",
                f"  засечено ≥k({p.threat_k}): {me.get('detect_k_frac', 0)*100:.0f}%",
                f"  средняя кратность: {me.get('mean_hits', 0):.1f}",
            ] + self._by_type_lines(me)
        self.view.set_metrics(lines)

    # ---- отчёт по ТИПАМ датчиков (задача 8.7) ----
    # ⚠️ ПОЧЕМУ РАЗБИВКА ОБЯЗАТЕЛЬНА. Пока тип был один, «N=10, R=2, k=3» описывало
    # расстановку целиком. С четырьмя типами те же три числа описывают только первый из
    # них, а строка «датчиков: 30» не отвечает на главный вопрос — работают ли все типы
    # или два из них стоят впустую. Общая кратность 16.7 этого тоже не показывает.
    def _resource_lines(self, p):
        """Блок РЕСУРС: строка на каждый работающий тип."""
        from model.sensors import sensor_types, KIND_LABEL
        out = ["РЕСУРС"]
        specs = sensor_types(p)
        total = 0
        for s in specs:
            if not s.active:
                continue
            total += s.n
            kind = KIND_LABEL.get(s.kind, s.kind)
            # у больших кратность в расстановке не участвует (кольцо по углу)
            k_txt = "" if s.kind == "big" else f"  k={s.k}"
            out.append(f"  тип {s.type_id} · {kind:<7} N={s.n:<4} R={s.r_km:g} км{k_txt}")
        if not any(s.active for s in specs):
            out.append("  типы не заданы: N = 0 у всех")
        elif len([s for s in specs if s.active]) > 1:
            out.append(f"  всего заказано: {total}")
        if bool(getattr(p, "threat_manual_mode", False)):
            out.append("  режим: ЗАДАННЫЕ ПОЗИЦИИ (алгоритм не подбирает)")
        else:
            out.append(f"  режим: {MODE_LABELS[p.mode]}")
        return out

    def _placement_lines(self, me):
        """Блок РАССТАНОВКА: сколько поставлено, по типам и сколько закреплено."""
        by = me.get("by_type") or {}
        n_small, n_big = me.get("n_sensors", 0), me.get("n_big", 0)
        out = [f"  датчиков: {n_small + n_big}"
               + (f"  (малых {n_small} + больших {n_big})" if n_big else "")]
        if len(by) > 1:
            parts = " · ".join(f"тип {t}: {by[t]['n']}" for t in sorted(by))
            out.append(f"  по типам: {parts}")
        # ⚠️ ДВЕ РАЗНЫЕ СТРОКИ. «Задано человеком» — сколько позиций он назвал сам;
        # «из них статических» — сколько из них не сдвинется и при итерациях. До
        # итераций стоят все заданные, при итерациях динамические уезжают, и разница
        # между строками показывает, что именно сейчас закреплено.
        n_hand, n_fix = me.get("n_manual", 0), me.get("n_static", 0)
        if n_hand:
            out.append(f"  задано человеком: {n_hand}"
                       + (f"  (из них статических: {n_fix})" if n_fix else ""))
        elif n_fix:
            out.append(f"  статических: {n_fix}")
        return out

    def _by_type_lines(self, me):
        """Засечка ОТДЕЛЬНО ПО ТИПАМ: свой радиус, своя доля, свои простаивающие.

        `простаивает` — датчики этого типа, не увидевшие ни одного пролёта. Именно этот
        показатель отвечает, зачем тип вообще стоит на карте: у закреплённых вручную
        датчиков он законно бывает ненулевым (человек поставил, где считает нужным),
        у подобранных программой — признак, что типу не хватило места."""
        by = me.get("by_type") or {}
        if len(by) < 2:
            return []
        out = ["", "ПО ТИПАМ ДАТЧИКОВ (свой радиус и своя кратность)"]
        for t in sorted(by):
            d = by[t]
            out.append(f"  тип {t}: {d['n']} шт. · R={d['r_km']:g} км · k={d.get('k', 0)}")
            out.append(f"     ловит ≥1: {d['detect_frac']*100:.0f}% · "
                       f"≥k: {d.get('detect_k_frac', 0)*100:.0f}% · "
                       f"кратность {d['mean_hits']:.1f}")
            if d.get("idle"):
                out.append(f"     ⚠ простаивает: {d['idle']} (не видят ни одного пролёта)")
        return out

    def run(self):
        self.view.show()
