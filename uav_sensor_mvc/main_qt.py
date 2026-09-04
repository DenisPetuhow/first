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


def _resource_path(rel):
    """Путь к файлу-ресурсу и в обычном запуске, и в собранном .exe.

    PyInstaller распаковывает данные во временную папку и кладёт её путь в
    `sys._MEIPASS`; при обычном запуске отсчитываем от каталога этого файла."""
    rel = os.path.normpath(rel)
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base, rel)
    if os.path.exists(p):
        return p
    p2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)
    return p2 if os.path.exists(p2) else ""


def _make_splash(QtWidgets, QtGui, QtCore, screen_size=None):
    """Заставка на `SPLASH_SECONDS` секунд. None, если картинки нет.

    Компоновка сверху вниз: НАЗВАНИЕ работы → эмблема (поверх неё, на жёлтой ленте, —
    красная надпись отдела) → «Разработали:» и список фамилий. Весь текст берётся из
    `config.SPLASH_*` — программу для его правки трогать не нужно.

    ВО ВЕСЬ ЭКРАН (`SPLASH_FULLSCREEN`): холст равен экрану, эмблема занимает
    `SPLASH_EMBLEM_FRAC` его высоты, а кегли считаются В ПИКСЕЛЯХ от высоты экрана —
    не в пунктах. Пункты зависят от системного DPI, и на разных машинах текст выезжал бы
    за край или тонул бы в пустоте; доля экрана даёт одинаковый вид везде.

    Исходник эмблемы 3564×4358 в любом случае ужимается со сглаживанием."""
    from config import (SPLASH_IMAGE, SPLASH_MAX_PX, SPLASH_PAD, SPLASH_TITLE,
                        SPLASH_FULLSCREEN, SPLASH_EMBLEM_FRAC,
                        SPLASH_EMBLEM_TEXT, SPLASH_EMBLEM_TEXT_COLOR,
                        SPLASH_EMBLEM_TEXT_Y, SPLASH_EMBLEM_TEXT_PT,
                        SPLASH_AUTHORS_LABEL, SPLASH_AUTHORS, THEME)
    path = _resource_path(SPLASH_IMAGE) if SPLASH_IMAGE else ""
    if not path:
        return None
    src = QtGui.QPixmap(path)
    if src.isNull():
        return None

    full = bool(SPLASH_FULLSCREEN) and screen_size is not None
    if full:
        W, H = int(screen_size.width()), int(screen_size.height())
        emblem_h = int(H * float(SPLASH_EMBLEM_FRAC))
        pad = max(24, int(H * 0.04))
        gap = max(10, int(H * 0.018))
        title_px = max(18, int(H * 0.032))            # ≈ 34 px на экране 1080
        authors_px = max(13, int(H * 0.019))
        emblem_px = max(14, int(emblem_h * 0.052))
    else:
        emblem_h = int(SPLASH_MAX_PX)
        pad, gap = int(SPLASH_PAD), 16
        title_px = 21
        authors_px = 15
        emblem_px = int(SPLASH_EMBLEM_TEXT_PT * 1.33)

    emblem = src.scaledToHeight(emblem_h, QtCore.Qt.SmoothTransformation)

    def font(px, bold=False, black=False):
        f = QtGui.QFont()
        f.setPixelSize(int(px))
        f.setBold(bool(bold or black))
        if black:
            f.setWeight(QtGui.QFont.Black)
        return f

    if not full:                                       # окно по размеру содержимого
        W = max(emblem.width() + 2 * pad, 640)
    inner = W - 2 * pad

    # высота заголовка считается ЗАРАНЕЕ: название длинное и переносится по словам,
    # поэтому сколько строк оно займёт, заранее неизвестно
    title_h = 0
    if SPLASH_TITLE:
        m = QtGui.QFontMetrics(font(title_px, True))
        title_h = m.boundingRect(QtCore.QRect(0, 0, inner, 10000),
                                 QtCore.Qt.TextWordWrap | QtCore.Qt.AlignHCenter,
                                 SPLASH_TITLE).height()

    authors = [a for a in (SPLASH_AUTHORS or ()) if a]
    line_h = int(authors_px * 1.7)
    authors_h = ((line_h if SPLASH_AUTHORS_LABEL else 0) + len(authors) * line_h)

    if not full:
        H = title_h + gap + emblem.height() + gap + authors_h + 2 * pad
    canvas = QtGui.QPixmap(W, H)
    canvas.fill(QtGui.QColor(THEME["bg"]))

    pr = QtGui.QPainter(canvas)
    pr.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pr.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
    # на весь экран блок центрируется по вертикали, иначе прижимается к полю сверху
    block_h = title_h + gap + emblem.height() + gap + authors_h
    y = max(pad, (H - block_h) // 2)

    if SPLASH_TITLE:                                   # 1) название работы
        pr.setFont(font(title_px, True))
        pr.setPen(QtGui.QColor(THEME["text"]))
        pr.drawText(QtCore.QRect(pad, y, inner, title_h),
                    QtCore.Qt.TextWordWrap | QtCore.Qt.AlignHCenter, SPLASH_TITLE)
        y += title_h + gap

    ex = (W - emblem.width()) // 2                     # 2) эмблема
    pr.drawPixmap(ex, y, emblem)
    if SPLASH_EMBLEM_TEXT:
        # надпись ПОВЕРХ знака — на жёлтой ленте; положение задаётся долей высоты,
        # чтобы не зависеть от того, в каком размере показана эмблема. Самый жирный
        # вес: на пёстрой ленте обычный bold «тонет»
        ty = y + int(emblem.height() * float(SPLASH_EMBLEM_TEXT_Y))
        pr.setFont(font(emblem_px, black=True))
        pr.setPen(QtGui.QColor(SPLASH_EMBLEM_TEXT_COLOR))
        pr.drawText(QtCore.QRect(ex, ty, emblem.width(), int(emblem_px * 1.8)),
                    QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, SPLASH_EMBLEM_TEXT)
    y += emblem.height() + gap

    if SPLASH_AUTHORS_LABEL:                           # 3) разработчики
        pr.setFont(font(authors_px, True))
        pr.setPen(QtGui.QColor(THEME["text"]))
        pr.drawText(QtCore.QRect(pad, y, inner, line_h),
                    QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, SPLASH_AUTHORS_LABEL)
        y += line_h
    pr.setFont(font(authors_px))
    pr.setPen(QtGui.QColor(THEME["muted"]))
    for name in authors:
        pr.drawText(QtCore.QRect(pad, y, inner, line_h),
                    QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, name)
        y += line_h
    pr.end()

    splash = QtWidgets.QSplashScreen(canvas)
    splash.setWindowFlags(splash.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
    return splash


def selftest():
    """Самопроверка данных: `UAV_SELFTEST=1` перед запуском — программа печатает, что
    нашла, и завершается, не открывая окна.

    Нужна для СОБРАННОЙ программы: ошибки чтения высот и карты гасятся внутри (файла
    может не быть — это не повод падать), поэтому снаружи видно только «рельеф не
    работает», без причины. Здесь причина печатается прямо — именно так нашлась
    незаметная потеря `rasterio.sample` в первой сборке.

    ⚠️ В `.exe` вывод виден только у сборки с консолью (`DEMO_CONSOLE=1`): у обычной
    консоли нет вовсе. Из исходников работает всегда.

    ⚠️ Имена файлов НЕ ЗАШИТЫ: участок задаёт их сам (`THREAT_AREAS`), и лежат они в
    своей папке `geo_cache/<участок>/`. Прежняя версия этой проверки искала
    `threat_layers.npz` и `dem.tif` в корне кэша и после перехода на папки участков
    сообщала бы «НЕТ» о файлах, которые на месте."""
    import importlib.util
    from model.threat_grid import (geo_cache_root, area_cache_dir, area_file,
                                   dem_files, ThreatModel)
    from view_qt import geomap as gm
    from config import (Params, THREAT_AREA, THREAT_LAYERS_FILE, THREAT_SOURCE_FILE,
                        THREAT_DEM_FILE, THREAT_BBOX_LONLAT)

    line = "=" * 62
    print(line)
    print("САМОПРОВЕРКА ДАННЫХ")
    print(line)
    print("участок        :", THREAT_AREA, THREAT_BBOX_LONLAT)
    print("каталог карты  :", geo_cache_root())
    print("папка участка  :", area_cache_dir())
    print("каталог тайлов :", gm.cache_root())
    for name in (THREAT_LAYERS_FILE, THREAT_SOURCE_FILE):
        if not name:
            continue
        p = area_file(name)
        print("  %-24s %s" % (name, "есть" if os.path.exists(p) else "НЕТ"))
    dems = dem_files()
    print("  файлов рельефа           %d %s"
          % (len(dems), os.path.basename(dems[0]) if dems else "(нет)"))
    for mod in ("rasterio", "numpy", "matplotlib", "pyqtgraph", "PyQt5"):
        ok = importlib.util.find_spec(mod) is not None
        print("  модуль %-18s %s" % (mod, "есть" if ok else "НЕТ"))
    # почему именно не читаются высоты — с полной ошибкой, а не молча
    try:
        import rasterio
        if not dems:
            raise FileNotFoundError("файл рельефа участка не найден: "
                                    + (THREAT_DEM_FILE or "имя не задано"))
        with rasterio.open(dems[0]) as ds:
            # без символа «x» из типографики: консоль Windows его не печатает
            print("  файл высот открыт: %d x %d, тип %s"
                  % (ds.width, ds.height, ds.dtypes[0]))
    except Exception as e:
        print("  ОШИБКА ЧТЕНИЯ ВЫСОТ: %s: %s" % (type(e).__name__, e))
    m = ThreatModel(Params())
    # РАЙОН МОДЕЛИРОВАНИЯ при запуске может быть не задан (план 8, задача 8.2): в
    # программе его задают мышью или загрузкой своей карты. Самопроверке нужен хоть
    # какой-то — берём середину области, 40 % её размера: проверяем, что слои читаются и
    # сетка строится, а не считаем всерьёз.
    if not m.area_ready:
        lo, la, ho, ha = m.bbox_lonlat
        dx, dy = (ho - lo) * 0.3, (ha - la) * 0.3
        m.set_area((lo + dx, la + dy, ho - dx, ha - dy))
        print("  район (самопроверка): %.2f x %.2f км" % m.area_size_km())
    m.ensure_built()
    print("  слои взяты из     :", m.source)
    print("  карта построена   :", m.grid is not None)
    if m.grid is not None:
        print("  сетка             : %d x %d" % (m.grid.nx, m.grid.ny))
    print("  высоты загружены  :", m.has_dem())
    if m.has_dem():
        m.set_relief(True)
        m.ensure_built()
        print("  рельеф в весе     :", m.grid.relief_k() is not None)
    print(line)


def main():
    # САМОПРОВЕРКА — до всего остального: она не поднимает Qt и не открывает окон,
    # поэтому работает и там, где интерфейс запуститься не может
    if os.environ.get("UAV_SELFTEST"):
        selftest()
        return
    if _ensure_qt_plugin_path() is None and sys.platform.startswith("win"):
        print("ВНИМАНИЕ: не найдены платформенные плагины Qt (PyQt5).\n"
              "Переустановите PyQt5:\n"
              "  pip uninstall -y PyQt5 PyQt5-Qt5 PyQt5-sip\n"
              "  pip install PyQt5\n"
              "Если установлен opencv-python — он конфликтует с Qt:\n"
              "  pip uninstall -y opencv-python && pip install opencv-python-headless\n",
              file=sys.stderr)

    import time
    from pyqtgraph.Qt import QtWidgets, QtGui, QtCore
    from config import SPLASH_SECONDS
    from model import SimulationModel
    from model.area_start import AreaStartModel
    from model.threat_grid import ThreatModel
    from view_qt import SimulationView
    from view_qt.area_view import AreaStartView
    from view_qt.threat_view import ThreatMapView
    from controller_qt import QtSimulationController
    from controller_qt.area_controller import AreaStartController
    from controller_qt.threat_controller import ThreatController

    app = QtWidgets.QApplication(sys.argv)

    # ЗАСТАВКА. Показывается СРАЗУ, до сборки вкладок: их построение занимает время, и
    # пользователь всё это время смотрел бы на пустой экран. `processEvents` обязателен —
    # без него окно заставки останется пустым прямоугольником (Qt не успеет отрисовать).
    scr = app.primaryScreen()
    scr_size = scr.availableGeometry().size() if scr is not None else None
    splash = (_make_splash(QtWidgets, QtGui, QtCore, scr_size)
              if SPLASH_SECONDS > 0 else None)
    t_splash = time.time()
    if splash is not None:
        splash.show()
        app.processEvents()

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

    # Вкладка 3 — цифровая карта угроз (весовая сетка + датчики по весам)
    p3 = Params()
    m3 = ThreatModel(p3)
    # area_max_km — ЧЁРНАЯ рамка: предел, за которым нет ни векторных данных, ни рельефа.
    # Синяя рамка (район моделирования) появится, когда район зададут (план 8, задача 8.2).
    from model.geo_frame import bbox_lonlat_to_km as _bb2km
    from config import THREAT_BBOX_LONLAT as _AREA_BBOX
    v3 = ThreatMapView(m3.bbox_km, m3.lon0, m3.lat0, p3,
                       area_max_km=_bb2km(_AREA_BBOX, m3.lon0, m3.lat0))
    c3 = ThreatController(m3, v3)

    tabs = QtWidgets.QTabWidget()
    tabs.setWindowTitle("Размещение датчиков обнаружения БПЛА — Qt/pyqtgraph")
    tabs.setStyleSheet(
        f"QTabWidget::pane {{ border: 0; }} "
        f"QTabBar::tab {{ background: {THEME['panel']}; color: {THEME['text']};"
        f" padding: 8px 16px; }} "
        f"QTabBar::tab:selected {{ background: {THEME['accent']}; color: white; }}")
    tabs.addTab(v1, "Маршрут A→B")
    tabs.addTab(v2, "Зона старта → цель")
    tabs.addTab(v3, "Карта угроз")
    tabs._controllers = (c1, c2, c3)    # удержать от сборки мусора
    # Размер «как было» нужен на случай, если окно потом свернут из максимума:
    # без него восстановленное окно оказалось бы крошечным.
    tabs.resize(1380, 800)

    if splash is None:
        tabs.showMaximized()            # программа открывается на весь экран
    else:
        # Досидеть оставшееся до SPLASH_SECONDS и только потом показать интерфейс.
        # Ждём ТАЙМЕРОМ, а не sleep: со sleep поток стоит, заставка не перерисовывается
        # и Windows рисует «программа не отвечает».
        left = max(0.0, float(SPLASH_SECONDS) - (time.time() - t_splash))

        def _show_main():
            splash.finish(tabs)
            tabs.showMaximized()        # программа открывается на весь экран
            tabs.raise_(); tabs.activateWindow()

        QtCore.QTimer.singleShot(int(left * 1000), _show_main)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
