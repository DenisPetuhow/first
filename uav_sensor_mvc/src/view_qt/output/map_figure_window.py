# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Модуль вывода — окно «Показ карты» (рисунки 5–7 «Описания ПК», заказчик 06.10.2026).

Кнопка «Показ карты» окна «Аналитика»: карта района для отчёта — весовая сетка, маршруты, средства с зонами, объекты
защиты, точки входа. Готовые виды «Рис. 5 / 6 / 7» повторяют рисунки Word, «Поверх подложки» — рисунок на карте
местности. ФОН на выбор («наложить рисунок на карту», заказчик): подложка из тайлов (тот же слой, участок и офлайн, что
на вкладке; собирается в фоновом потоке), своя карта (растр с той же привязкой — с поворотом и перекосом) или без фона;
слабые ячейки весов можно сделать прозрачными, чтобы под ними читалась карта. Рамка — по всему нарисованному (как в
Word), по весовой сетке или как на карте программы сейчас. Рисует `figures.py :: draw_map`.
⚠️ Векторная своя карта (SVG) фоном не идёт: у неё нет растра — окно так и пишет.
"""
from PyQt5 import QtCore, QtWidgets

from view_qt.output.figure_base import FigureDialog
from view_qt.output.figures import MAP_DEFAULTS, MAP_PRESETS, draw_map

TILES_PX = 2400                                      # ширина мозаики тайлов, пикс.: хватает на 30 см при 200 dpi
KEEP_USER = ("title", "font", "family", "frame", "axes", "colors")   # готовый вид их не трогает — это оформление


class _TilesSignals(QtCore.QObject):
    """Сигнал из потока сборки подложки: (рамка км, результат или None)."""
    done = QtCore.pyqtSignal(object)                 # (рамка км, (rgba, рамка) или None)


class _TilesTask(QtCore.QRunnable):
    """Сборка подложки в фоновом потоке (чтение, декод и склейка тайлов). Вход: колбэк вида, рамка км, пикс., сигнал."""

    def __init__(self, fn, box, px, sig):
        super().__init__()
        self._fn, self._box, self._px, self._sig = fn, box, px, sig   # что звать, рамка, ширина, куда сообщить

    def run(self):
        """Собрать подложку и сообщить окну; ошибка — как «тайлов нет». Отдаёт: ничего."""
        try:
            res = self._fn(self._box, self._px)      # (rgba, рамка км) или None
        except Exception:                            # сеть, диск, битый тайл
            res = None
        try:
            self._sig.done.emit((self._box, res))
        except RuntimeError:                         # окно уже удалено
            pass


class MapFigureDialog(FigureDialog):
    """Окно карты для отчёта. Вход — как у `FigureDialog`, ещё колбэки вида: `basemap(рамка км, пикс.)` → (rgba, рамка)
    или None (тяжёлый, зовётся из потока), `own_map()` → (rgba, коэф.) / None / «vector», `view_box()` → рамка вида."""
    TITLE = "Показ карты"                            # заголовок окна
    FILE = "карта_района"                            # начало имени PNG
    SIZE_CM = (25.4, 16.3)                           # как рисунки 5–7 Word (10 × 6,4 дюйма)
    COLORS = True                                    # цвета типов — как на карте или как в Word

    def __init__(self, parent, data, choices=None, colors=None, target=None, basemap=None, own_map=None,
                 view_box=None):
        super().__init__(parent, data, choices, colors, target)
        self._basemap_fn = basemap                   # сборка подложки по рамке
        self._own_map_fn = own_map or (lambda: None)   # своя карта вкладки
        self._view_box_fn = view_box or (lambda: None)   # рамка вида карты программы
        self._view_box = None                        # рамка вида на момент снимка, км
        self._tiles = dict(box=None, res=None, state="idle")   # подложка: для какой рамки, картинка, состояние
        self._sig = _TilesSignals(self)              # сигнал из потока сборки
        self._sig.done.connect(self._tiles_ready)

    def _build_settings(self):
        """Настройки рисунков 5–7: готовые виды, фон, веса, маршруты, средства, рамка. Отдаёт: ничего."""
        d = MAP_DEFAULTS                             # умолчания — рисунок 7 Word
        self._section("Готовый вид")
        for name in MAP_PRESETS:                     # кнопка на каждый готовый вид
            b = QtWidgets.QPushButton(name)          # кнопка вида
            b.clicked.connect(lambda _c=False, n=name: self.apply_preset(n))
            self.form.addRow(b)
        self._section("Фон (подложка под рисунком)")
        self._add_combo("background", "Подложка:", [("none", "нет"), ("tiles", "карта местности (тайлы)"),
                                                    ("own", "своя карта (растр)")], d["background"])
        self._add_spin("bg_alpha", "Непрозрачность:", d["bg_alpha"], 0.05, 1.0, 0.05, 2)
        self._section("Весовая карта")
        self._add_check("weights", "весовая сетка", d["weights"])
        self._add_spin("weights_alpha", "Непрозрачность:", d["weights_alpha"], 0.05, 1.0, 0.05, 2)
        self._add_combo("cmap", "Палитра:", [("Greys", "серая (рис. 6–7)"), ("turbo", "цветная (рис. 5)"),
                                             ("viridis", "viridis"), ("YlOrRd", "жёлто-красная"), ("magma", "magma")],
                        d["cmap"])
        self._add_check("hide_low", "слабые ячейки прозрачны", d["hide_low"])
        self._add_spin("weights_cut", "Порог прозрачности:", int(d["weights_cut"]), 0, 90, 5, 0, " % шкалы")
        self._add_check("colorbar", "шкала цвета весов", d["colorbar"])
        self._section("Маршруты")
        self._add_check("routes", "маршруты выборки", d["routes"])
        self._add_spin("routes_n", "Сколько (0 — все):", d["routes_n"], 0, 1000000, 50)
        self._add_color("routes_color", "Цвет:", d["routes_color"])
        self._add_spin("routes_lw", "Толщина:", d["routes_lw"], 0.1, 5.0, 0.1, 1)
        self._add_spin("routes_alpha", "Непрозрачность:", d["routes_alpha"], 0.01, 1.0, 0.05, 2)
        self._section("Средства и объекты")
        self._add_check("sensors", "средства", d["sensors"])
        self._add_check("zones", "зоны обнаружения", d["zones"])
        self._add_spin("zones_alpha", "Непрозрачность зон:", d["zones_alpha"], 0.02, 1.0, 0.02, 2)
        self._add_check("numbers", "номера средств", d["numbers"])
        self._add_check("targets", "объекты защиты", d["targets"])
        self._add_check("entries", "точки входа", d["entries"])
        self._add_check("legend", "легенда типов", d["legend"])
        self._section("Рамка")
        self._add_combo("frame", "Рамка:", [("auto", "по всему нарисованному (как в Word)"),
                                            ("area", "весовая сетка района"), ("view", "как на карте сейчас")],
                        d["frame"])
        self._add_check("axes", "оси в километрах", d["axes"])

    def apply_preset(self, name):
        """Готовый вид: умолчания, поверх — вид; оформление (заголовок, шрифт, рамка) — как было. Вход: имя вида.
        Отдаёт: ничего."""
        vals = dict(MAP_DEFAULTS)                    # все настройки вида
        vals.update(MAP_PRESETS[name])
        self.set_opts({k: v for k, v in vals.items() if k not in KEEP_USER})

    def _on_snapshot(self):
        """Новый снимок — запомнить рамку вида программы; подложку собрать заново при надобности. Отдаёт: ничего."""
        vb = self._view_box_fn()                     # рамка вида карты, км; None — нет
        self._view_box = tuple(float(v) for v in vb) if vb else None

    def _tiles_box(self, o):
        """Рамка подложки: вид программы или район с полем под зоны средств. Вход: настройки. Отдаёт: (x0, x1, y0, y1)."""
        if o["frame"] == "view" and self._view_box:  # как на карте сейчас
            return self._view_box
        b = self._snap.get("bbox")                   # рамка весовой сетки, км
        if not b:                                    # карты нет — подложку не по чему собирать
            return None
        pad = max([2.0] + [s["r_km"] for s in self._snap.get("sensors") or []]) + 1.0   # поле: самая большая зона, км
        return (b[0] - pad, b[1] + pad, b[2] - pad, b[3] + pad)

    def _tiles_for(self, o):
        """Подложка для рисунка; нет — запустить сборку в потоке. Вход: настройки. Отдаёт: (rgba, рамка) или None."""
        box = self._tiles_box(o)                     # нужная рамка, км
        if box is None or self._basemap_fn is None:  # собирать не по чему или нечем
            return None
        t = self._tiles                              # состояние подложки
        if t["box"] == box and t["state"] in ("done", "none", "loading"):   # уже есть, нет тайлов или собирается
            return t["res"] if t["state"] == "done" else None
        t.update(box=box, res=None, state="loading")
        QtCore.QThreadPool.globalInstance().start(_TilesTask(self._basemap_fn, box, TILES_PX, self._sig))
        return None

    def _tiles_ready(self, payload):
        """Подложка собрана — запомнить и перерисовать (если рамка ещё та). Вход: (рамка, результат). Отдаёт: ничего."""
        box, res = payload                           # для какой рамки и что получилось
        if box != self._tiles["box"]:                # рамку уже сменили — старый результат не нужен
            return
        self._tiles.update(res=res, state="done" if res is not None else "none")
        self.redraw()

    def wait_tiles(self, ms=60000):
        """Дождаться сборки подложки (проверки без экрана). Вход: предел, мс. Отдаёт: True — подложка есть."""
        QtCore.QThreadPool.globalInstance().waitForDone(int(ms))
        QtWidgets.QApplication.processEvents()
        return self._tiles["state"] == "done"

    def _draw(self, fig, snap, opts, colors):
        """Рисунки 5–7: снимок плюс фон и рамка вида. Вход: рисунок, снимок, настройки, цвета. Отдаёт: оси."""
        s = dict(snap)                               # снимок с фоном — копия, сам снимок не трогаем
        s["view_box"] = self._view_box
        if opts["background"] == "tiles":            # подложка из тайлов
            s["basemap"] = self._tiles_for(opts)
        own = self._own_map_fn() if opts["background"] == "own" else None   # своя карта или «vector»
        s["own_map"] = own if isinstance(own, tuple) else None
        return draw_map(fig, s, opts, colors)

    def _info(self):
        """Пояснение: что на рисунке и что с фоном. Отдаёт: список строк."""
        o = self.opts()                              # настройки с экрана
        R = self._snap.get("routes") or []           # маршруты выборки
        k = int(o["routes_n"])                       # сколько показать; 0 — все
        out = ["Маршрутов в выборке: %d (на рисунке %d) · средств: %d · объектов защиты: %d."
               % (len(R), len(R) if k <= 0 else min(k, len(R)), len(self._snap.get("sensors") or []),
                  len(self._snap.get("targets") or []))]   # пояснение
        if o["background"] == "tiles":               # что с подложкой
            out.append({"loading": "Подложка собирается…", "none": "Подложки нет: тайлов участка нет в кэше "
                        "(офлайн) или нет сети.", "done": "Подложка: карта местности, слой как на вкладке."}
                       .get(self._tiles["state"], ""))
        elif o["background"] == "own":               # что со своей картой
            own = self._own_map_fn()                 # своя карта вкладки
            out.append("Своя карта: растр с привязкой вкладки." if isinstance(own, tuple) else
                       "Своя карта векторная (SVG) — фоном не идёт." if own == "vector" else
                       "Своя карта не загружена — «Своя карта» на вкладке.")
        if o["frame"] == "view" and not self._view_box:   # рамки вида нет
            out.append("Рамки вида карты нет — взята рамка по всему нарисованному.")
        return out
