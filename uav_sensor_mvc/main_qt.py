# -*- coding: utf-8 -*-
"""
Точка входа Qt-версии приложения (PyQt5 + pyqtgraph) — ОСНОВНОЙ интерфейс.

Две вкладки: «Маршрут A→B» и «Зона старта → цель» (с реальной картой). Плавная
анимация, зум/панорамирование мышью. Все параметры задаются и меняются ТОЛЬКО в
графическом интерфейсе (панель справа, Enter — пересчёт). Аргументов командной
строки и консольных режимов нет.

    python main_qt.py

Зум — колесо мыши; панорамирование — перетаскивание; авто-вписывание — правый
клик по карте («View All»).
"""
import os
import sys

from config import Params, THEME


def _ensure_qt_plugin_path():
    """Указать Qt путь к платформенным плагинам PyQt5.

    Чинит частую ошибку Windows «Could not find the Qt platform plugin "windows"»,
    когда путь к плагинам пуст или перебит сторонним пакетом (cv2, conda и т.п.).
    Возвращает каталог plugins или None, если он не найден (плагины не установлены).
    """
    try:
        import PyQt5
    except Exception:
        return None
    base = os.path.dirname(PyQt5.__file__)
    for sub in ("Qt5", "Qt"):
        plugins = os.path.join(base, sub, "plugins")
        if os.path.isdir(os.path.join(plugins, "platforms")):
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = plugins
            bin_dir = os.path.join(base, sub, "bin")
            if os.path.isdir(bin_dir):
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
            return plugins
    return None


def main():
    if _ensure_qt_plugin_path() is None and sys.platform.startswith("win"):
        print("ВНИМАНИЕ: не найдены платформенные плагины Qt (PyQt5).\n"
              "Переустановите PyQt5:\n"
              "  pip uninstall -y PyQt5 PyQt5-Qt5 PyQt5-sip\n"
              "  pip install PyQt5\n"
              "Если установлен opencv-python — он конфликтует с Qt:\n"
              "  pip uninstall -y opencv-python && pip install opencv-python-headless\n",
              file=sys.stderr)

    from pyqtgraph.Qt import QtWidgets
    from model import SimulationModel
    from model.area_start import AreaStartModel
    from view_qt import SimulationView
    from view_qt.area_view import AreaStartView
    from controller_qt import QtSimulationController
    from controller_qt.area_controller import AreaStartController

    app = QtWidgets.QApplication(sys.argv)

    # Вкладка 1 — маршрут A->B (параметры по умолчанию, дальше — в интерфейсе)
    p1 = Params()
    m1 = SimulationModel(p1)
    v1 = SimulationView(p1.A, p1.B, m1.corridor_outline(), m1.corridor_bbox(),
                        p1, speed=4)
    c1 = QtSimulationController(m1, v1)

    # Вкладка 2 — старт из зоны -> цель (с картой)
    p2 = Params()
    m2 = AreaStartModel(p2)
    v2 = AreaStartView(p2.A, p2.B, m2.corridor_outline(), m2.corridor_bbox(),
                       p2, speed=4)
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
