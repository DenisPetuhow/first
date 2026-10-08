# -*- coding: utf-8 -*-
"""
CONTROLLER (Qt) · Тот же SimulationController, но таймер анимации — QTimer.

Вся логика (правка параметров, режимы просмотра, слои, инкрементальный SAA,
анимация полёта) наследуется без изменений из controller.controller. Здесь
переопределяется только фабрика таймера: вместо таймера холста matplotlib
используется QtCore.QTimer.
"""
from pyqtgraph.Qt import QtCore

from controller.controller import SimulationController


class _QtTimer:
    """Адаптер QTimer к интерфейсу таймера контроллера (add_callback/start/stop)."""

    def __init__(self, interval_ms):
        self._t = QtCore.QTimer()
        self._t.setInterval(int(interval_ms))

    def add_callback(self, cb):
        self._t.timeout.connect(cb)

    def start(self):
        self._t.start()

    def stop(self):
        self._t.stop()


class QtSimulationController(SimulationController):
    def _make_timer(self):
        return _QtTimer(self.INTERVAL_MS)
