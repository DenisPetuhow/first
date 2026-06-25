# -*- coding: utf-8 -*-
"""
Точка входа Qt-версии приложения (PyQt5 + pyqtgraph).

Альтернатива matplotlib-интерфейсу (main.py --mode gui): плавная анимация,
зум/панорамирование мышью, гибкая компоновка. Вычислительное ядро (model/) и
логика контроллера — общие с базовой версией.

Примеры:
    python main_qt.py
    python main_qt.py --ab 100 --Lmax 150 --N 6 --R 12 --k 3 --angle 10
    python main_qt.py --traj serpentine --profile complex
    python main_qt.py --geo --A 55.75 37.62 --B 56.20 38.40   # широта долгота

Зум — колесо мыши; панорамирование — перетаскивание; авто-вписывание — правый
клик по карте («View All»). По умолчанию seed случаен (см. --seed).
"""
import argparse
import sys

from config import MODES, MOTION_PROFILES
from main import build_params


def main():
    ap = argparse.ArgumentParser(description="Размещение датчиков БПЛА — Qt/pyqtgraph.")
    ap.add_argument("--opt", choices=list(MODES), default="balanced")
    ap.add_argument("--traj", choices=["arc", "serpentine"], default="arc")
    ap.add_argument("--profile", choices=list(MOTION_PROFILES), default="mixed")
    ap.add_argument("--ab", type=float); ap.add_argument("--Lmax", type=float)
    ap.add_argument("--N", type=int); ap.add_argument("--R", type=float)
    ap.add_argument("--k", type=int); ap.add_argument("--Lseg", type=int)
    ap.add_argument("--angle", type=float); ap.add_argument("--T", type=int)
    ap.add_argument("--seed", type=int); ap.add_argument("--speed", type=int, default=4)
    ap.add_argument("--geo", action="store_true")
    ap.add_argument("--A", type=float, nargs=2, metavar=("LAT", "LON"))
    ap.add_argument("--B", type=float, nargs=2, metavar=("LAT", "LON"))
    args = ap.parse_args()
    params = build_params(args)

    from pyqtgraph.Qt import QtWidgets
    from config import THEME
    from model import SimulationModel
    from model.area_start import AreaStartModel
    from view_qt import SimulationView
    from view_qt.area_view import AreaStartView
    from controller_qt import QtSimulationController
    from controller_qt.area_controller import AreaStartController

    app = QtWidgets.QApplication(sys.argv)

    # Вкладка 1 — маршрут A->B как есть
    p1 = params
    m1 = SimulationModel(p1)
    v1 = SimulationView(p1.A, p1.B, m1.corridor_outline(), m1.corridor_bbox(),
                        p1, speed=args.speed)
    c1 = QtSimulationController(m1, v1)

    # Вкладка 2 — старт из зоны -> цель
    p2 = build_params(args)
    m2 = AreaStartModel(p2)
    v2 = AreaStartView(p2.A, p2.B, m2.corridor_outline(), m2.corridor_bbox(),
                       p2, speed=args.speed)
    c2 = AreaStartController(m2, v2)

    tabs = QtWidgets.QTabWidget()
    tabs.setWindowTitle("Размещение датчиков обнаружения БПЛА — Qt/pyqtgraph")
    tabs.setStyleSheet(
        f"QTabWidget::pane {{ border: 0; }} "
        f"QTabBar::tab {{ background: {THEME['panel']}; color: {THEME['text']};"
        f" padding: 8px 16px; }} "
        f"QTabBar::tab:selected {{ background: {THEME['accent']}; color: white; }}")
    tabs.addTab(v1, "Маршрут A→B")
    tabs.addTab(v2, "Зона старта → цель")
    tabs._controllers = (c1, c2)        # удержать от сборки мусора
    tabs.resize(1380, 800)
    tabs.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
