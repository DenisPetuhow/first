# -*- coding: utf-8 -*-
"""
CONTROLLER · Поток «Пуска» (план 13.5, задача 13.5.11; оптимизация, шаг 11в): маршруты и пересчёт датчиков — в фоновом
потоке, главный поток только рисует. Разбор — теория/код/ОПТИМИЗАЦИЯ_В_КОДЕ §10, теория/оптимизация/МНОГОПОТОЧНОСТЬ.

Правило «считать в фоне — принимать в главном»: окна и виджеты поток не трогает вовсе (Qt позволяет это только главному),
результат отдаёт сигналами — Qt доставляет их в главный поток очередью событий.

⚠️ МОДЕЛЬ ОДНА НА ДВА ПОТОКА, поэтому два замка-договорённости:
* поток строит не больше `ahead` маршрутов впрок (семафор `_slots`): анимация берёт маршрут — освобождает место;
  так пересчёт датчиков идёт в такт показанным маршрутам, а не убегает на сотню вперёд;
* после пересчёта датчиков поток ЖДЁТ (`_ack`), пока главный их нарисует: иначе следующий `place_sensors` менял бы
  `sensors` прямо во время отрисовки (датчиков 18, а типов уже 17).
Повторяемость не задета: маршруты строит один поток в прежнем порядке — выборка та же, что без потока.
"""
import threading

from PyQt5 import QtCore


class IterWorker(QtCore.QThread):
    """Поставщик маршрутов «Пуска». Вход: модель, шаг пересчёта датчиков (маршрутов), запас маршрутов впрок.
    Сигналы: `route_ready(маршрут)`, `sensors_ready(маршрутов)`, `ended(причина)` — done · exhausted · stopped · error."""

    route_ready = QtCore.pyqtSignal(object)          # маршрут (M,2) км построен — в очередь анимации
    sensors_ready = QtCore.pyqtSignal(int)           # датчики пересчитаны по стольким маршрутам — нарисовать
    ended = QtCore.pyqtSignal(str)                   # поток закончил: почему

    FAILS_MAX = 40                                   # неудачных розыгрышей подряд — выборка исчерпана (как прежде)

    def __init__(self, model, refresh_every, start_at=0, ahead=2):
        super().__init__()
        self.model = model                           # модель — общая с главным потоком
        self.every = max(1, int(refresh_every))      # пересчитывать датчики каждые столько маршрутов
        self.at = int(start_at)                      # при скольких маршрутах датчики уже пересчитаны
        self._slots = threading.Semaphore(int(ahead))   # мест «впрок»: маршрут построен, анимация его ещё не взяла
        self._ack = threading.Event()                # главный поток дорисовал датчики — можно трогать модель
        self._stop = threading.Event()               # просьба остановиться (пауза, «Очистить», другая кнопка)

    def consumed(self):
        """Анимация взяла маршрут — освободить место впрок. Вход: нет. Отдаёт: ничего. Зовётся из главного потока."""
        self._slots.release()

    def drawn(self):
        """Датчики нарисованы — потоку можно дальше. Вход: нет. Отдаёт: ничего. Зовётся из главного потока."""
        self._ack.set()

    def stop(self):
        """Попросить остановиться: поток доделает текущий шаг и выйдет. Вход: нет. Отдаёт: ничего."""
        self._stop.set()
        self._slots.release()                        # разбудить, если ждёт места
        self._ack.set()                              # и если ждёт отрисовки

    def _place(self, n):
        """Пересчитать датчики и дождаться, пока главный их нарисует. Вход: маршрутов в выборке. Отдаёт: ничего."""
        self.model.place_sensors()
        self.at = n
        self._ack.clear()
        self.sensors_ready.emit(n)
        self._ack.wait()                             # модель не трогать, пока её рисуют

    def run(self):
        """Цикл потока: маршрут → в очередь → раз в `every` маршрутов датчики. Вход: нет. Отдаёт: ничего."""
        m = self.model                               # модель
        fails = 0                                    # неудачных розыгрышей подряд
        try:
            while not self._stop.is_set():           # пока не попросили остановиться
                if m.iter_iteration >= m.iter_target_count():   # набрали число из панели
                    n = len(m.iter_routes)           # маршрутов в выборке
                    if n >= self.every and n > self.at:   # итоговая расстановка — по всей выборке
                        self._place(n)
                    self.ended.emit("done")
                    return
                self._slots.acquire()                # не уходить вперёд анимации больше чем на `ahead`
                if self._stop.is_set():              # пока ждали — попросили остановиться
                    break
                route = m.iter_step()                # маршрут; None — розыгрыш не удался
                if route is None:                    # неудача — место впрок не занято
                    self._slots.release()
                    fails += 1
                    if fails > self.FAILS_MAX:       # выборка иссякла
                        self.ended.emit("exhausted")
                        return
                    continue
                fails = 0
                self.route_ready.emit(route)
                n = len(m.iter_routes)               # маршрутов в выборке
                if n >= self.every and n - self.at >= self.every:   # пора пересчитать датчики
                    self._place(n)
            self.ended.emit("stopped")
        except Exception as e:                       # noqa: BLE001 — текст ошибки — в заголовок окна
            self.ended.emit("error: %s" % e)
