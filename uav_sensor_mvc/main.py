# -*- coding: utf-8 -*-
"""
Точка входа — matplotlib-версия (одна вкладка «маршрут A→B»).

ТОЛЬКО графический интерфейс. Полная версия с двумя вкладками и реальной
картой (вкладка «зона старта → цель») — в main_qt.py (PyQt5 + pyqtgraph):

    python main_qt.py

Здесь параметры задаются и меняются прямо в интерфейсе (панель справа,
Enter — пересчёт). Консольных режимов и аргументов командной строки нет.
"""
from config import Params


def main():
    from model import SimulationModel
    from view import SimulationView
    from controller import SimulationController

    params = Params()
    model = SimulationModel(params)
    view = SimulationView(params.A, params.B, model.corridor_outline(),
                          model.corridor_bbox(), params, speed=4)
    SimulationController(model, view).run()


if __name__ == "__main__":
    main()
