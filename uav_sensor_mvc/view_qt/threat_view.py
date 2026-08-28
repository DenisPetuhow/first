# -*- coding: utf-8 -*-
"""
VIEW (Qt) · Вкладка 3 «Цифровая карта угроз».

Ограниченный демонстрационный участок местности (фиксированный bbox по трём
условным ориентирам) с жёстким пределом обзора. На карту накладываются:
  * векторные слои цифровой карты (реки, дороги, ж/д, ЛЭП, трубопроводы, лесополосы,
    мосты, застройка) — основные вероятные ориентиры маршрута БПЛА;
  * весовая сетка 500×500 м как тепловой слой (сумма весов слоёв по каждой ячейке);
  * датчики, расставленные по весовой карте (максимизация покрытого веса + разнос),
    с запретом на воду.

Только отрисовка: расчёт карты и расстановка — в model/threat_grid.py.
"""
import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets
import matplotlib.cm as cm

from config import (THEME, THREAT_LAYERS, THREAT_LAYER_ORDER, MODE_LABELS,
                    THREAT_BBOX_POINTS, THREAT_ENTRY, THREAT_TARGET,
                    THREAT_ITER_MODE_LABELS, THREAT_ITER_SPREAD_LABELS,
                    THREAT_SPEND_LABELS)
from .basemap_mixin import BasemapMixin, gm_qcolor as _qcolor
from .ui_common import apply_dark_theme, make_side_panel   # общие детали трёх вкладок
from . import geomap as gm

# ═══════════════════════════════════════════════════════════════════════════════
#  ЦВЕТА ВКЛАДКИ 3 — ПРАВИТЬ ЗДЕСЬ (одно место на всю вкладку)
# ───────────────────────────────────────────────────────────────────────────────
#  1) LAYER_STYLE — векторные слои цифровой карты (реки/дороги/ЛЭП/…): цвет,
#     толщина линии, штрих (dash). Каждый ключ = слой из THREAT_LAYERS.
#  2) THREAT_COLORS — маршруты, огибающая, датчики, маркеры и легенда. Значение
#     используется И в слое на карте, И в легенде — правка в одном месте меняет оба.
#     В комментарии указано, ГДЕ на карте применяется цвет (имя элемента/метода).
#  Формат: "#rrggbb" (hex) либо (r, g, b, a) — RGBA 0..255 для полупрозрачных заливок.
#  Прозрачность отдельных ЛИНИЙ задаётся рядом с элементом (alpha в mkPen) — см.
#  _build_scene_items; здесь — базовые цвета.
# ═══════════════════════════════════════════════════════════════════════════════
#  ВЫБОР ЦВЕТА ЛИНИЙ. Слой рисуется поверх ПОДЛОЖКИ OSM — а это светло-зелёные поля,
#  салатовый лес и жёлто-песочные дороги самой OSM. Дороги и ЛЭП были СВЕТЛЫМИ жёлто-
#  оранжевыми (#ff9f43 / #ffd18c / #f6e05e) и на таком фоне не читались: контраст по
#  WCAG выходил 1.00…1.20 при норме 4.5 — линия совпадала с фоном по яркости.
#  Тон дорог сохранён (оранжевая магистраль, жёлтая местная — привычно по картам), но
#  ЗАТЕМНЁН до порога 4.5; ЛЭП уведена в пурпур, которого нет ни в OSM, ни в turbo.
#  Подбор — перебором по насыщенности при худшем фоне (лес #add19e), см.
#  МЕТОДИЧКА_КАРТА_УГРОЗ.md §12.0.
#
#  ПОЧЕМУ ОДНОГО ЦВЕТА МАЛО (правка по замечанию заказчика на общем виде). Когда включены
#  все слои сразу, карта превращается в кашу — и виноват не контраст с фоном, а четыре
#  других вещи, каждая со своим лекарством:
#    1) ПОДЛОЖКА ДУБЛИРУЕТ наши слои. OSM рисует СВОИ дороги, воду и лес, мы кладём сверху
#       свои — оранжевое на оранжевом, синее на синем. Лечится не палитрой, а гашением
#       подложки в бледный фон (BASEMAP_FADE ниже);
#    2) НЕТ ОБВОДКИ. Линии без канта сливаются друг с другом на пересечениях, а при
#       отдалении пересечений сотни. Белый кант (`casing`) физически разделяет линии —
#       это главный приём картографии, сильнее любого подбора оттенка;
#    3) РАЗЛИЧИЕ ТОЛЬКО ПО ОТТЕНКУ. Тонкие линии при отдалении сжимаются в пиксель, и
#       соседние оттенки смешиваются в грязь. Поэтому слои разведены ещё и ШТРИХОМ:
#       сплошная (дороги, реки) / шпалы (ж/д) / точки (ЛЭП) / длинный пунктир (труба) /
#       редкие точки (лесополоса). Даже если цвета сошлись, штрих разводит;
#    4) ПОРЯДОК НЕ ЗАДАН. У всех слоёв стоял один `setZValue(-5)` — кто нарисован
#       последним, тот и сверху, случайно. Теперь порядок явный (`z` ниже): снизу вверх
#       застройка → лесополоса → вода → ж/д → труба → ЛЭП → дороги → мост.
#  Поля: `casing` — цвет обводки (None = без неё), `casing_w` — её ширина, `alpha` —
#  прозрачность самой линии (второстепенные приглушены, чтобы главные читались сквозь).
#  ЯРКОСТЬ ПОДОБРАНА ЗАМЕРОМ, а не на глаз: для каждого цвета взят САМЫЙ СВЕТЛЫЙ тон,
#  который ещё проходит норму контраста на ХУДШЕМ фоне OSM (это лес — самый тёмный из
#  подложки). Не максимум контраста: все фоны светлые, и максимум даёт чёрный — тогда
#  река, ЛЭП и дороги становятся одинаково чёрными, а это ровно та каша, от которой
#  уходим. Слоям с обводкой порог мягче (3.6 против 4.5): белый кант сам отделяет линию
#  от фона, и цвет можно оставить живым, не загоняя дороги в бурый.
LAYER_STYLE = {
    # вода: синий далеко от оранжевого дорог; светлый кант отделяет от зелени подложки
    "river":      dict(color="#2a6794", width=2.6, dash=None, z=-5.6,
                       casing="#eaf2f7", casing_w=4.0, alpha=245),
    "stream":     dict(color="#35576e", width=1.6, dash=None, z=-5.7,
                       casing=None, alpha=230),
    # дороги: белый кант — они читаются поверх любой мешанины
    "road_major": dict(color="#9e4825", width=3.0, dash=None, z=-5.1,
                       casing="#ffffff", casing_w=5.0, alpha=250),
    "road_local": dict(color="#8a5725", width=1.6, dash=None, z=-5.2,
                       casing="#ffffff", casing_w=3.2, alpha=235),
    # Ж/Д, ЛЭП, ЛЕСОПОЛОСА — на общем виде их «не было видно вовсе»: линия шириной 1.2 px
    # с редким пунктиром ([1,5] — точка через пять пустых) вырождается в еле заметную
    # пыль, и различать там уже нечего, какой ни возьми цвет. Поэтому они СТАЛИ ТОЛЩЕ, а
    # штрих ПЛОТНЕЕ: сначала линия должна читаться как линия, и только потом её тон
    # что-то значит. Штрихи при этом разные — тем и разводятся: шпалы / точки / длинный
    # пунктир / короткие штрихи.
    "railway":    dict(color="#636363", width=2.2, dash=[7, 4], z=-5.5,
                       casing="#ffffff", casing_w=4.0, alpha=255),
    # Труба уведена из коричневого в ОЛИВКОВЫЙ: после затемнения до нормы она совпадала
    # с местной дорогой (17 единиц RGB — на карте это один цвет). Застройка по той же
    # причине уведена из бежевого в тёплый серый.
    # штрих [1,3] закрашивал лишь четверть длины — ЛЭП вырождалась в пыль и «не читалась»
    "power":      dict(color="#69428c", width=1.9, dash=[2, 3], z=-5.3,
                       casing=None, alpha=240),
    "pipeline":   dict(color="#57571b", width=1.9, dash=[9, 5], z=-5.4,
                       casing=None, alpha=240),
    "tree_row":   dict(color="#3b5c32", width=1.7, dash=[3, 4], z=-5.8,
                       casing=None, alpha=225),
    # мост — не отдельный кричащий цвет, а та же дорога с тёмным кантом
    "bridge":     dict(color="#9e4825", width=3.4, dash=None, z=-5.0,
                       casing="#333333", casing_w=5.4, alpha=255),
    # застройка — КОНТУР площади, поэтому пунктиром: сплошной тонкой линией она
    # неотличима от ручья (57 единиц RGB при одинаковой ширине), а пунктирная граница
    # к тому же привычнее читается как «край населённого пункта»
    "built_up":   dict(color="#5c5348", width=1.4, dash=[2, 3], z=-5.9,
                       casing=None, alpha=190),
}

THREAT_COLORS = {
    # — МАРШРУТЫ (линии на карте и в легенде) —
    "route_all":   "#00e5ff",           # все возможные маршруты (полупрозр.), self.route_item
    "route_iter":  "#ff5a5a",           # накопленные итерационные («выборка»), self.route_iter_item
    "route_gen":   "#ff2d2d",           # обобщённая выборка 10 %, self.route_gen_item
    "route_main":  "#0a0a0a",           # ОСНОВНОЙ (текущий) маршрут итерации — чёрный, self.route_cur_item
    "uav":         "#ff4dff",           # маркер летящего БПЛА, self.uav_marker
    # — ОГИБАЮЩАЯ И ДАТЧИКИ —
    "envelope":    (255, 70, 245, 120),  # «все места пролёта» (розовая заливка), render_routes
    "sensor":      "#ff8c1a",           # датчики: кольцо зоны обзора + центр, render_sensors
    # — ПРОЧИЕ ЗНАЧКИ/ЛЕГЕНДА —
    "crossing":    "#ffd166",           # перекрёстки-развилки (ромбы), self.crossing_scatter
    "crossing_edge": "#7a5c00",         # обводка ромбов перекрёстков
    # ЗАПРЕТНАЯ ЗОНА (рисует пользователь): контур, штриховка и линия рисования.
    # ЦВЕТ ПОДОБРАН ЗАМЕРОМ, а не на глаз. Задача трудная: зона лежит поверх ВЕСОВОЙ КАРТЫ,
    # а та в палитре turbo — синий → циан → зелёный → жёлтый → красный, то есть занимает
    # почти весь спектр. Плюс подложка OSM зелёная. Считалось расстояние до всей палитры
    # turbo и контраст к фонам: глубокий пурпур даёт 89 единиц RGB до ближайшего цвета
    # turbo при контрасте 5.22 к лесу и 7.47 к полям (норма 4.5). Красный не годится —
    # он занят возвышенностями «приоритета высот» и городом; маджента ярче, но на лесу
    # проваливается (3.53).
    "zone":        "#8e0e6b",           # контур заданной зоны, self.zone_item
    "zone_hatch":  (142, 14, 107, 190), # диагональная штриховка внутри, self.zone_hatch
    "zone_fill":   (142, 14, 107, 45),  # лёгкая подложка под штриховкой, self.zone_fill
    # Зона, которую рисуют ПРЯМО СЕЙЧАС. Раньше была #ffd166 — ровно цвет перекрёстков,
    # и при включённых развилках рисуемый контур в них терялся. Взят свободный сектор
    # тона (бирюзово-зелёный, между лесополосой 107° и маршрутами 186°).
    "zone_draw":   "#00e0a4",           # линия построения, self.zone_draw_item
    # Вершины — цветом БУДУЩЕЙ зоны: сразу видно, во что превратится контур. Обводка
    # цветом линии построения связывает их с ней.
    "zone_pt":     "#8e0e6b",
    "zone_pt_edge": "#00e0a4",
    "legend_bg":   "#0b111c",           # фон панели-легенды
    "legend_head": "#36c5f0",           # заголовок легенды
    "legend_area": "#ff4dff",           # квадрат «места пролёта» в легенде (= розовая заливка)
    "legend_main": "#e6edf3",           # образец ОСНОВНОГО маршрута в легенде: светлый, т.к.
                                        # чёрная линия (route_main) на тёмном фоне легенды не видна
    "legend_entry": "#3ddc97",          # вход A в легенде (на карте — THEME['ok'])
    "legend_target": "#ff5d6c",         # цель B в легенде (на карте — THEME['warn'])
    "legend_bridge": "#ff5d6c",         # мост в легенде
    # — РЕЛЬЕФ: два независимых слоя показа (чекбоксы «карта высот» / «приоритет по высоте») —
    "legend_relief": "#c9b458",         # образец карты высот в легенде
    "legend_hide":   "#3aa0ff",         # образец «укрытие» в легенде (= RELIEF_HIDE_RGB)
    "legend_cut":    "#101016",         # образец «коридор снят» (= RELIEF_CUT_RGBA)
    "legend_cut_edge": "#8a93a6",       # обводка чёрного образца: на тёмном фоне иначе не видно
}
# Карта высот — привычная топографическая шкала «низины зелёные → вершины светлые».
# Ступени равномерны по ПЕРЦЕНТИЛЯМ высоты, а не по метрам: иначе на участке с размахом
# 23…365 м вся обжитая полоса слилась бы в один оттенок.
RELIEF_RAMP = ((0.00, (47, 107, 61)),      # пойма, балки — зелёный
               (0.35, (140, 150, 70)),     # ровное место — оливковый
               (0.62, (201, 180, 88)),     # подъём — песочный
               (0.84, (150, 100, 62)),     # возвышенность — коричневый
               (1.00, (232, 224, 213)))    # вершины — светлый, как на топокартах
# Приоритет по высоте (множитель k): синим укрытия, красным возвышенности, чёрным — снятое
# отсечкой. Прозрачность растёт с отклонением k от единицы, нейтральное почти не видно.
RELIEF_HIDE_RGB = (58, 160, 255)           # k > 1: низина, укрытие
RELIEF_OPEN_RGB = (255, 93, 108)           # k < 1: возвышенность, заметнее
RELIEF_CUT_RGBA = (16, 16, 22, 210)        # отсечка: коридор снят полностью
RELIEF_MAX_ALPHA = 165                     # предел непрозрачности слоя приоритета
# Зоны-исключения (маска), render_exclusions — RGBA 0..255:
THREAT_URBAN_RGBA = (255, 93, 108, 120)   # город — красный (исключён из пролёта)
THREAT_WATER_RGBA = (58, 160, 255, 150)   # вода — синий (запрет установки датчика)


def _hillshade(h, az_deg=315.0, alt_deg=45.0, z=6.0, lo=0.72, hi=1.22):
    """Теневая отмывка карты высот: множитель яркости по наклону поверхности к источнику
    света (по умолчанию северо-запад, 45° над горизонтом — картографическая традиция).

    Без отмывки цветная шкала читается как абстрактные пятна: понять, где балка, а где
    гребень, нельзя. `z` — вертикальное преувеличение: на равнине уклоны малы, и без
    подъёма контраста рельеф не проявился бы."""
    gy, gx = np.gradient(np.asarray(h, float))
    gx = gx * z; gy = gy * z
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az = np.radians(360.0 - az_deg + 90.0)
    alt = np.radians(alt_deg)
    shade = (np.sin(alt) * np.cos(slope)
             + np.cos(alt) * np.sin(slope) * np.cos(az - aspect))
    return lo + (hi - lo) * np.clip(shade, 0.0, 1.0)


def _threat_cmap():
    try:
        return pg.colormap.getFromMatplotlib("turbo")
    except Exception:
        stops = np.linspace(0, 1, 256)
        cols = (cm.turbo(stops) * 255).astype(np.ubyte)
        return pg.colormap.ColorMap(stops, cols)


def _threat_lut():
    stops = np.linspace(0, 1, 256)
    cols = (cm.turbo(stops) * 255).astype(np.ubyte)
    return cols


def _iter_heat_lut():
    """LUT для 2-й карты (частота пролёта БПЛА) — magma, отличается от turbo весов."""
    stops = np.linspace(0, 1, 256)
    return (cm.magma(stops) * 255).astype(np.ubyte)


def _iter_heat_cmap():
    """Та же magma, но как ColorMap — для цветовой шкалы у правого края."""
    try:
        return pg.colormap.getFromMatplotlib("magma")
    except Exception:
        stops = np.linspace(0, 1, 256)
        return pg.colormap.ColorMap(stops, _iter_heat_lut())


class ThreatMapView(QtWidgets.QWidget, BasemapMixin):
    # Подложка гасится: поверх неё лежат НАШИ дороги, реки и лесополосы, а OSM рисует
    # свои — без гашения это две карты одного смысла друг на друге. Но и перебарщивать
    # нельзя: при 0.62/0.30 карта выцветала почти в белый лист, теряя контекст (лес,
    # поля, вода переставали различаться) — заказчик попросил не больше ~20 %.
    # 0, 0 возвращает прежний вид.
    BASEMAP_FADE = (0.20, 0.10)
    """Экран вкладки 3: карта-подложка + слои (тепловая карта весов, векторные слои,
    огибающая, маршруты, датчики) + панель управления справа.

    ТОЛЬКО отрисовка и ввод — расчётов не содержит (их делает ThreatModel, дирижирует
    ThreatController). Каждый слой карты — отдельный элемент pyqtgraph (`ImageItem` для
    растров-масок, `PlotDataItem` для линий-маршрутов, `ScatterPlotItem` для точек). Порядок
    наложения слоёв задаётся Z-value (см. `_build_scene_items`): чем больше — тем выше;
    зона обзора датчиков стоит поверх всего. Цвета — в палитрах THREAT_COLORS / LAYER_STYLE
    вверху файла. Методы `render_*` принимают данные из контроллера и обновляют элементы;
    методы `get_*`/`set_*` — обмен значениями полей с контроллером."""

    THREAT_PARAM_SPECS = [
        ("threat_N", "Датчиков N", int),
        ("threat_R", "Радиус R, км", float),
        ("threat_k", "Кратность k", int),
        ("threat_cand_step_km", "Шаг сетки датчиков, км", float),
    ]
    # подсказки к полям (всплывают при наведении) — поясняют смысл параметров
    FIELD_TIPS = {
        "threat_N": "Сколько датчиков расставить по весовой карте.",
        "threat_R": "Радиус обнаружения одного датчика (круг покрытия), км.",
        "threat_k": "Насыщение по кратности: перекрывать одну ячейку более чем k "
                    "датчиками уже невыгодно — так они не сваливаются в одну точку.",
        "threat_cand_step_km":
            "ШАГ СЕТКИ КАНДИДАТНЫХ ПОЗИЦИЙ, км. Датчик можно поставить только в узел "
            "воображаемой сетки с этим шагом (перебор возможных мест). Меньше шаг — "
            "точнее размещение, но больше вариантов и дольше счёт; больше — грубее и "
            "быстрее.\n\n0 = СЧИТАТЬ ОТ РАДИУСА (половина R) — так и рекомендуется. "
            "Когда шаг крупнее зоны обзора, между соседними кандидатами остаётся дыра "
            "шире, чем видит датчик, и лучшая точка становится недоступной. Замер при "
            "R = 2 км и пяти датчиках: шаг 0.5 км — засечка 96.7 %, шаг 3 км — 82.7 %, "
            "шаг 6 км — 17.3 %.",
    }

    def __init__(self, model_bbox_km, lon0, lat0, params):
        super().__init__()
        self.bbox_km = model_bbox_km
        self._lon0, self._lat0 = lon0, lat0
        self._map_layer = getattr(params, "map_layer3", "osm")
        self._map_offline = bool(getattr(params, "map_offline3", False))
        self._lut = _threat_lut()
        self._fields = {}
        self._suppress = False
        self._layer_items = {}
        self._layer_casing = {}            # обводка слоя (широкая светлая линия под ним)
        self._sensor_items = []
        self._framed = False
        # ВИДИМОСТЬ КАЖДОГО ВЕКТОРНОГО СЛОЯ по отдельности (окно «Векторные слои…»).
        # Живёт здесь, а не в диалоге: окно можно закрыть и открыть, набор остаётся.
        # Общий чекбокс «Векторные слои» гасит их все разом, не трогая этот выбор.
        self._vec_visible = {name: True for name in LAYER_STYLE}
        self._vec_dialog = None
        self._last_layers = {}             # последние данные слоёв — чтобы перерисовать
        self._last_built = None            # выбранный набор БЕЗ пересчёта модели
        self._last_extent = None
        self._group_busy = False           # идёт переключение группы: не перерисовывать на каждом
        self._base_opacity = {}            # родная прозрачность слоя (рельеф 0.55, тепло 0.62)
        self._marker_scale = 1.0           # текущий размер точечных маркеров (доля)
        # порядок включения тепловых карт: шкала показывается для ПОСЛЕДНЕЙ включённой.
        # Весовая карта стоит первой — она включена по умолчанию.
        self._scale_order = ["iter_heat", "threat"]
        self._scale_shown = None

        self._target_mode = False
        self._zone_mode = False           # идёт рисование запретной зоны
        self._zone_pts = []               # вершины зоны, которую рисуют прямо сейчас
        self._data_path = None            # текущий источник (для префилла окна выбора)
        self._enabled_layers = None       # текущий набор слоёв (None = все)

        # callbacks (контроллер переопределит)
        self.on_build = lambda: None
        self.on_relief = lambda: None     # кнопка «Добавить/Убрать рельеф»
        self.on_place = lambda: None
        self.on_apply = lambda: None
        self.on_reset = lambda: None
        self.on_reset_view = lambda: None
        self.on_mode = lambda key: None
        self.on_toggle = lambda: None
        self.on_map_layer = lambda key: None
        self.on_map_offline = lambda flag: None
        self.on_set_target = lambda x, y: None
        self.on_zone_added = lambda poly: None      # нарисована запретная зона
        self.on_zone_undo = lambda: None            # убрать последнюю зону
        self.on_choose_data = lambda path, layers: None
        self.on_input_apply = lambda vals: None
        self.on_iter_mode = lambda key: None
        self.on_iter_spread = lambda key: None
        self.on_iter_spend = lambda key: None
        self.on_iter_play = lambda: None
        self.on_iter_step = lambda: None
        self.on_iter_batch = lambda: None
        self.on_iter_speed = lambda v: None
        self.on_iter_gen_frac = lambda v: None
        self._params_ref = params         # для префилла окна входных данных
        self._input_dlg = None

        self.setWindowTitle("Цифровая карта угроз — вкладка 3")
        self._apply_stylesheet()
        self._build_ui(params)
        self._build_scene_items()
        self._init_basemap(lon0, lat0, lambda: self._map_layer,
                           lambda: self._map_offline,
                           scheme_points=self._orient_points())
        self._set_view_limits(self.bbox_km)
        self.plot.scene().sigMouseClicked.connect(self._on_scene_click)
        # смена масштаба/панорама -> перерисовать слои под новый кадр (прореживание по
        # видимой области + переключение застройки растр/контуры). С задержкой, чтобы
        # не считать на каждом кадре плавного зума.
        self.on_view_changed = lambda: None
        self._view_timer = QtCore.QTimer(self)
        self._view_timer.setSingleShot(True)
        self._view_timer.setInterval(180)
        self._view_timer.timeout.connect(lambda: self.on_view_changed())
        self.vb.sigRangeChanged.connect(lambda *_: self._view_timer.start())

    # ---- режим «указать цель» (клик по карте) ----
    def _begin_target(self):
        self._target_mode = True
        self.set_title("Кликните точку ЦЕЛИ на карте (вход — фиксирован у реки)")

    def _on_scene_click(self, ev):
        if self._zone_mode:
            self._zone_click(ev)
            return
        if not self._target_mode:
            return
        try:
            if ev.button() != QtCore.Qt.LeftButton:
                return
        except Exception:
            pass
        pt = self.vb.mapSceneToView(ev.scenePos())
        self._target_mode = False
        self.on_set_target(float(pt.x()), float(pt.y()))

    # ---- режим рисования ЗАПРЕТНОЙ ЗОНЫ ----
    def _begin_zone(self):
        """Включить рисование: клики ставят вершины, нажатие колёсика замыкает зону."""
        self._zone_mode = True
        self._zone_pts = []
        self._draw_zone_preview()
        self.set_title("Запретная зона: клики — вершины, КОЛЁСИКО — замкнуть, Esc — отмена")

    def _end_zone(self, apply_it):
        """Закончить рисование. apply_it=False — бросить начатое, ничего не меняя."""
        pts = list(self._zone_pts)
        self._zone_mode = False
        self._zone_pts = []
        self._draw_zone_preview()
        self.set_title("")
        if apply_it and len(pts) >= 3:
            self.on_zone_added(pts)

    def _zone_click(self, ev):
        """Клик в режиме рисования: левая кнопка — вершина, КОЛЁСИКО — замкнуть зону."""
        try:
            btn = ev.button()
        except Exception:
            btn = QtCore.Qt.LeftButton
        try:
            ev.accept()                       # не отдавать клик панораме/зуму
        except Exception:
            pass
        if btn == QtCore.Qt.MiddleButton:     # нажатие колеса — замкнуть
            self._end_zone(True)
            return
        if btn != QtCore.Qt.LeftButton:
            return
        pt = self.vb.mapSceneToView(ev.scenePos())
        self._zone_pts.append((float(pt.x()), float(pt.y())))
        self._draw_zone_preview()

    def keyPressEvent(self, ev):
        """Esc — бросить начатую зону (обычный способ отменить рисование)."""
        if self._zone_mode and ev.key() == QtCore.Qt.Key_Escape:
            self._end_zone(False)
            return
        super().keyPressEvent(ev)

    def _draw_zone_preview(self):
        """Показать зону, которую пользователь рисует прямо сейчас."""
        pts = self._zone_pts
        if not pts:
            self.zone_draw_item.setData([], [])
            self.zone_draw_pts.setData([], [])
            return
        a = np.asarray(pts, float)
        closed = np.vstack([a, a[:1]]) if len(a) >= 3 else a   # замыкаем от трёх вершин
        self.zone_draw_item.setData(closed[:, 0], closed[:, 1])
        self.zone_draw_pts.setData(a[:, 0], a[:, 1])

    # Шаг диагональной штриховки запретной зоны, км. Мельче — плотнее заливка и больше
    # линий на карте; крупнее — зона хуже читается как «залитая». На общем виде участка
    # (94 км в ширину) шаг 0.9 км даёт около сотни штрихов на зону средней величины.
    ZONE_HATCH_STEP_KM = 0.9

    @staticmethod
    def _hatch_polygon(poly, step_km, angle_deg=45.0):
        """Диагональные штрихи ВНУТРИ многоугольника: списки x, y с разрывами (NaN).

        Линии проводятся не «поверх» и не по прямоугольнику, а обрезаются по самому
        контуру: для каждой линии находятся пересечения со всеми сторонами, точки
        сортируются вдоль неё и соединяются парами. Пара = вход в фигуру и выход из неё,
        поэтому невыпуклая зона (а пользователь рисует мышью что угодно) штрихуется верно —
        в вырезах штрихов не будет."""
        P = np.asarray(poly, float)
        if len(P) < 3 or step_km <= 0:
            return [], []
        a = np.radians(angle_deg)
        d = np.array([np.cos(a), np.sin(a)])          # вдоль штриха
        n = np.array([-np.sin(a), np.cos(a)])         # поперёк (по нему идёт шаг)
        proj_n = P @ n
        A, B = P, np.roll(P, -1, axis=0)              # стороны замкнутого контура
        an, bn = A @ n, B @ n
        xs, ys = [], []
        c = np.ceil(proj_n.min() / step_km) * step_km
        while c <= proj_n.max():
            denom = bn - an
            with np.errstate(divide="ignore", invalid="ignore"):
                t = (c - an) / denom
            hit = (np.abs(denom) > 1e-12) & (t >= 0.0) & (t < 1.0)
            if hit.any():
                pts = A[hit] + (B[hit] - A[hit]) * t[hit][:, None]
                order = np.argsort(pts @ d)
                pts = pts[order]
                for i in range(0, len(pts) - 1, 2):   # пары «вход — выход»
                    xs += [pts[i, 0], pts[i + 1, 0], np.nan]
                    ys += [pts[i, 1], pts[i + 1, 1], np.nan]
            c += step_km
        return xs, ys

    def render_no_fly(self, polys, toggles):
        """Отрисовать УЖЕ ЗАДАННЫЕ запретные зоны (список полигонов в км)."""
        show = bool(toggles.get("show_zones", True))
        if not show or not polys:
            self.zone_item.setData([], [])
            self.zone_fill.setData([], [])
            self.zone_hatch.setData([], [])
            return
        xs, ys = [], []
        hx, hy = [], []
        nan = [np.nan]
        for p in polys:
            a = np.asarray(p, float)
            if len(a) < 3:
                continue
            xs += list(a[:, 0]) + [a[0, 0]] + nan     # замкнуть и разорвать перед следующей
            ys += list(a[:, 1]) + [a[0, 1]] + nan
            ax, ay = self._hatch_polygon(a, self.ZONE_HATCH_STEP_KM)
            hx += ax; hy += ay
        self.zone_item.setData(xs, ys)
        self.zone_fill.setData(xs, ys)
        self.zone_hatch.setData(hx, hy)

    # ---- окно выбора цифровых карт (источник + слои) ----
    def _open_data_dialog(self):
        dlg = DigitalMapsDialog(self, self._data_path, self._enabled_layers)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self._data_path, self._enabled_layers = dlg.result_choices()
            self.on_choose_data(self._data_path, self._enabled_layers)

    # ---- окно «Входные данные» (не блокирует программу) ----
    def _open_input_dialog(self):
        if self._input_dlg is None:
            self._input_dlg = InputDataDialog(self, self._params_ref,
                                              self.on_input_apply)
        self._input_dlg.refresh(self._params_ref)
        self._input_dlg.show(); self._input_dlg.raise_()

    def set_ab_distance(self, km):
        if self._input_dlg is not None:
            self._input_dlg.set_ab(km)

    def refresh_input_dialog(self):
        """Обновить поля окна «Входные данные» под текущие параметры (авто-L_max и пр.)."""
        if self._input_dlg is not None and self._input_dlg.isVisible():
            self._input_dlg.refresh(self._params_ref)

    def set_iter_checked(self, on):
        """Включить/выключить чекбокс «итерации» без повторного запуска расчёта."""
        self.chk_iter.blockSignals(True)
        self.chk_iter.setChecked(bool(on))
        self.chk_iter.blockSignals(False)

    # ---- опорные точки-ориентиры для схемы/подписей ----
    def _orient_points(self):
        pts = [(name, lon, lat) for name, (lon, lat) in THREAT_BBOX_POINTS.items()]
        pts.append((THREAT_ENTRY[0], THREAT_ENTRY[1], THREAT_ENTRY[2]))
        pts.append((THREAT_TARGET[0], THREAT_TARGET[1], THREAT_TARGET[2]))
        return pts

    def set_callbacks(self, **cbs):
        for name, fn in cbs.items():
            setattr(self, name, fn)

    # Размер общей легенды: базовый кегль при полном участке и предел при зуме. Раньше
    # она была 10 pt и НЕ масштабировалась — на приближении занимала треть экрана.
    LEGEND_PT = 7.8            # 6.5 читалось мелко — поднято на 20 % по замечанию
    LEGEND_PT_MIN = 4.8

    def _legend_html(self, scale=1.0):
        """HTML-легенда (что каким цветом). ВСЕ цвета берутся из палитр THREAT_COLORS и
        LAYER_STYLE (вверху файла) — правка там меняет и карту, и легенду синхронно.

        `scale` уменьшает кегль при зуме (как у легенды приоритета): панель поясняет
        цвета, а не соперничает с картой за место. Пояснения в скобках убраны — они есть
        в подсказках чекбоксов и в методичке."""
        c = THREAT_COLORS
        pt = max(self.LEGEND_PT * float(scale), self.LEGEND_PT_MIN)
        sq = pt * 1.3                                   # значок чуть крупнее текста
        rows = [f'<div style="background:{c["legend_bg"]};padding:3px 6px;border:1px solid '
                f'{c["legend_head"]};border-radius:4px;font-size:{pt:.1f}pt;color:#ffffff;'
                'line-height:125%;">']
        rows.append(f'<b style="color:{c["legend_head"]};">ЛЕГЕНДА</b><br>')
        from config import THREAT_LAYERS as _TL
        for name in THREAT_LAYER_ORDER:
            st = LAYER_STYLE.get(name)
            if not st:
                continue
            lab = _TL.get(name, {}).get("label", name)
            rows.append(f'<span style="color:{st["color"]};font-size:{sq:.1f}pt;">'
                        f'&#9644;&#9644;</span> {lab}<br>')
        rows.append(f'<span style="color:{c["legend_bridge"]};">&#9650;</span> мост &nbsp; '
                    f'<span style="color:{c["crossing"]};">&#9670;</span> пересечение<br>')
        rows.append(f'<span style="color:{c["legend_area"]};">&#9632;</span> места пролёта '
                    f'&nbsp; <span style="color:{c["route_all"]};font-size:{sq:.1f}pt;">'
                    '&#9644;&#9644;</span> возможные маршруты<br>')
        rows.append(f'<span style="color:{c["legend_main"]};font-size:{sq:.1f}pt;">&#9644;&#9644;</span> '
                    f'основной &nbsp; <span style="color:{c["route_gen"]};font-size:{sq:.1f}pt;">'
                    '&#9644;&#9644;</span> выборка<br>')
        rows.append(f'<span style="color:{c["sensor"]};">&#9679;</span> датчик<br>')
        # рельеф — два независимых слоя показа; цвета те же, что в RELIEF_* выше
        rows.append(f'<span style="color:{c["legend_relief"]};">&#9632;</span> высоты &nbsp; '
                    f'<span style="color:{c["legend_hide"]};">&#9632;</span> укрытие &nbsp; '
                    f'<span style="color:{c["legend_target"]};">&#9632;</span> возвышенность &nbsp; '
                    f'<span style="color:{c["legend_cut"]};">&#9632;</span> коридор снят<br>')
        rows.append(f'<span style="color:{c["legend_entry"]};">&#9733;</span> вход &nbsp; '
                    f'<span style="color:{c["legend_target"]};">&#10005;</span> цель')
        rows.append('</div>')
        return "".join(rows)

    # Размер легенды приоритета: базовый кегль при полном участке и предел при зуме.
    # Панель мелкая и подписана коротко — она только расшифровывает три цвета, подробности
    # (пороги, множители) есть в подсказке чекбокса и в методичке.
    RELIEF_LEGEND_PT = 7.5
    RELIEF_LEGEND_PT_MIN = 4.5

    def _relief_legend_html(self, scale=1.0):
        """Легенда режима «приоритет по высоте»: три цвета — три строки, без пояснений.

        Панель у ПРАВОГО края, появляется вместе со своим чекбоксом. `scale` уменьшает
        кегль при зуме (см. `reposition_legends`): приблизив карту, пользователь смотрит
        на местность, и панель не должна занимать место."""
        c = THREAT_COLORS
        fs = max(self.RELIEF_LEGEND_PT_MIN, self.RELIEF_LEGEND_PT * float(scale))
        rows = [f'<div style="background:{c["legend_bg"]};padding:{fs * 0.4:.1f}pt '
                f'{fs * 0.7:.1f}pt;border:1px solid {c["legend_head"]};border-radius:4px;'
                f'font-size:{fs:.1f}pt;color:#ffffff;line-height:135%;white-space:nowrap;">']
        rows.append(f'<b style="color:{c["legend_head"]};">ПРИОРИТЕТ ПО ВЫСОТЕ</b><br>')
        rows.append(f'<span style="color:{c["legend_hide"]};">&#9632;</span> укрытие<br>')
        rows.append(f'<span style="color:{c["legend_target"]};">&#9632;</span> '
                    'возвышенность<br>')
        rows.append(f'<span style="color:{c["legend_cut"]};'
                    f'background:{c["legend_cut_edge"]};">&nbsp;&#9632;&nbsp;</span> '
                    'коридор снят')
        rows.append('</div>')
        return "".join(rows)

    # ==================================================================
    def _apply_stylesheet(self):
        """Оформление — общее для трёх вкладок (`view_qt/ui_common.py`)."""
        apply_dark_theme(self)

    def _header(self, text):
        lab = QtWidgets.QLabel(text); lab.setObjectName("header")
        return lab

    def _tint(self, btn, color):
        btn.setStyleSheet(
            f"QPushButton {{ background: {color}; color: white; border-radius: 6px;"
            f" padding: 7px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {THEME['accent']}; }}")

    def _build_ui(self, params):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8); root.setSpacing(8)

        self.plot = pg.PlotWidget()
        self.pi = self.plot.getPlotItem()
        self.pi.setAspectLocked(True)
        self.pi.showGrid(x=True, y=True, alpha=0.12)
        self.pi.setLabel("bottom", "X, км", color=THEME["muted"])
        self.pi.setLabel("left", "Y, км", color=THEME["muted"])
        self.vb = self.pi.getViewBox()
        root.addWidget(self.plot, stretch=1)

        scroll, col = make_side_panel()   # общая для трёх вкладок
        root.addWidget(scroll)

        col.addWidget(self._header("ПАРАМЕТРЫ  (Enter — пересчёт)"))
        grid = QtWidgets.QGridLayout(); grid.setSpacing(6)
        for i, (name, label, _t) in enumerate(self.THREAT_PARAM_SPECS):
            r, c = divmod(i, 2)
            cell = QtWidgets.QVBoxLayout(); cell.setSpacing(1)
            lab = QtWidgets.QLabel(label); lab.setObjectName("muted")
            edit = QtWidgets.QLineEdit(str(getattr(params, name)))
            edit.returnPressed.connect(self._on_field_submit)
            tip = self.FIELD_TIPS.get(name, "")
            lab.setToolTip(tip); edit.setToolTip(tip)
            self._fields[name] = edit
            cell.addWidget(lab); cell.addWidget(edit)
            grid.addLayout(cell, r, c)
        col.addLayout(grid)

        self.btn_input = QtWidgets.QPushButton("Входные данные…")
        self.btn_input.setToolTip("Отдельное окно (не блокирует программу): запас хода, "
                                  "скорость, крен, интервал смены направления; |AB| "
                                  "считается между входом и целью. Enter — сразу применить.")
        self.btn_input.clicked.connect(self._open_input_dialog)
        col.addWidget(self.btn_input)

        col.addWidget(self._header("КАРТА УГРОЗ"))
        self.btn_data = QtWidgets.QPushButton("Выбрать цифровые карты…")
        self.btn_data.setToolTip(
            "Отдельное окно: выбрать источник данных (файл .npz / .osm.pbf либо "
            "авто) и какие слои (реки/дороги/…) накладывать на сетку.")
        self.btn_data.clicked.connect(self._open_data_dialog)
        col.addWidget(self.btn_data)

        self.btn_build = QtWidgets.QPushButton("Построить карту")
        self.btn_build.setToolTip(
            "Наложить выбранные цифровые слои на сетку 500 м и посчитать веса ячеек.")
        self._tint(self.btn_build, THEME["accent"])
        self.btn_build.clicked.connect(lambda: self.on_build())
        col.addWidget(self.btn_build)

        self.btn_relief = QtWidgets.QPushButton("Добавить рельеф")
        self.btn_relief.setToolTip(
            "Пересобрать весовую карту С УЧЁТОМ ВЫСОТ: та же дорога в низине станет "
            "привлекательнее, чем на гребне, а очень высокие места перестанут быть "
            "коридорами.\n\nБез этой кнопки карта считается без рельефа — как раньше.\n"
            "ВНИМАНИЕ: пересборка сбрасывает накопленные маршруты, итерации и датчики — "
            "они построены по другой карте.")
        self.btn_relief.clicked.connect(lambda: self.on_relief())
        col.addWidget(self.btn_relief)

        self.btn_target = QtWidgets.QPushButton("Указать цель")
        self.btn_target.setToolTip(
            "Кликните точку ЦЕЛИ на карте. Точка появления БПЛА фиксирована "
            "(у реки). Цель — задел для построения маршрутов (Этап 3).")
        self.btn_target.clicked.connect(self._begin_target)
        col.addWidget(self.btn_target)

        row_zone = QtWidgets.QHBoxLayout(); row_zone.setSpacing(6)
        self.btn_zone = QtWidgets.QPushButton("Запретная зона")
        self.btn_zone.setToolTip(
            "Нарисовать на карте область, где пролёт БПЛА НЕВОЗМОЖЕН — маршруты пойдут "
            "в обход.\n\nКлики задают вершины, нажатие колёсика замыкает зону, Esc — отмена.\n"
            "Датчики в зоне ставить можно: им запрещена только вода.\n"
            "ВНИМАНИЕ: большая зона поперёк коридора может сделать цель недостижимой — "
            "тогда маршрутов не будет вовсе, и программа об этом скажет.")
        self.btn_zone.clicked.connect(self._begin_zone)
        self.btn_zone_undo = QtWidgets.QPushButton("Убрать зону")
        self.btn_zone_undo.setToolTip("Убрать последнюю нарисованную зону. "
                                      "Все зоны разом убирает «Сброс».")
        self.btn_zone_undo.clicked.connect(lambda: self.on_zone_undo())
        row_zone.addWidget(self.btn_zone, 2); row_zone.addWidget(self.btn_zone_undo, 1)
        col.addLayout(row_zone)

        self.btn_place = QtWidgets.QPushButton("Расставить датчики")
        self.btn_place.setToolTip(
            "Разместить N датчиков по весам карты (макс. покрытого веса + разнос), "
            "не ставя их на воду.")
        self._tint(self.btn_place, THEME["ok"])
        self.btn_place.clicked.connect(lambda: self.on_place())
        col.addWidget(self.btn_place)

        self.src_label = QtWidgets.QLabel("источник данных: —")
        self.src_label.setObjectName("muted"); self.src_label.setWordWrap(True)
        col.addWidget(self.src_label)

        # режим оптимизации
        col.addWidget(self._header("РЕЖИМ ОПТИМ."))
        self._mode_keys = list(MODE_LABELS)
        self.grp_mode = QtWidgets.QButtonGroup(self)
        for k in self._mode_keys:
            rb = QtWidgets.QRadioButton(MODE_LABELS[k])
            if k == getattr(params, "mode", "balanced"):
                rb.setChecked(True)
            self.grp_mode.addButton(rb)
            col.addWidget(rb)
            rb._key = k
        self.grp_mode.buttonClicked.connect(
            lambda btn: self.on_mode(btn._key))

        # карта-подложка
        self._build_map_combo(col, params)

        # показ слоёв
        self._build_layer_box(col)
        # режим стохастического выбора развилки (для «итераций»)
        row_it = QtWidgets.QHBoxLayout()
        row_it.addWidget(QtWidgets.QLabel("режим итераций:"))
        self.combo_iter = QtWidgets.QComboBox()
        self._iter_keys = list(THREAT_ITER_MODE_LABELS)
        for k in self._iter_keys:
            self.combo_iter.addItem(THREAT_ITER_MODE_LABELS[k])
        cur = getattr(params, "threat_iter_mode", "mix")
        self.combo_iter.setCurrentIndex(self._iter_keys.index(cur)
                                        if cur in self._iter_keys else 0)
        self.combo_iter.setToolTip("ПРИОРИТЕТ выбора клетки по ВЕСУ сетки: макс. (реки/"
                                   "дороги) / средний / мин. (пустоши, одинокая река) / смесь.")
        self.combo_iter.currentIndexChanged.connect(self._iter_mode_changed)
        row_it.addWidget(self.combo_iter, 1)
        col.addLayout(row_it)
        # 2-й список: РАЗБРОС маршрута по карте (отдельно от приоритета веса)
        row_sp2 = QtWidgets.QHBoxLayout()
        row_sp2.addWidget(QtWidgets.QLabel("разброс по карте:"))
        self.combo_spread = QtWidgets.QComboBox()
        self._spread_keys = list(THREAT_ITER_SPREAD_LABELS)
        for k in self._spread_keys:
            self.combo_spread.addItem(THREAT_ITER_SPREAD_LABELS[k])
        cur = getattr(params, "threat_iter_spread", "mix")
        self.combo_spread.setCurrentIndex(self._spread_keys.index(cur)
                                          if cur in self._spread_keys else 0)
        self.combo_spread.setToolTip("Насколько далеко маршрут уходит от прямой вход→цель: "
                                     "центр (вдоль) / середина / края карты (дальний обход, "
                                     "заход с любой стороны) / смесь. Комбинируется с весом.")
        self.combo_spread.currentIndexChanged.connect(self._spread_changed)
        row_sp2.addWidget(self.combo_spread, 1)
        col.addLayout(row_sp2)
        # 3-й список: РАСХОД ЗАПАСА ХОДА — когда БПЛА виляет, а когда идёт прямо на цель.
        # Действует на ИТЕРАЦИИ. На «возможные маршруты» (до итераций) не влияет: там
        # перебирается веер всех повадок, чтобы область покрывалась целиком.
        row_sp3 = QtWidgets.QHBoxLayout()
        row_sp3.addWidget(QtWidgets.QLabel("расход запаса:"))
        self.combo_spend = QtWidgets.QComboBox()
        self._spend_keys = list(THREAT_SPEND_LABELS)
        for k in self._spend_keys:
            self.combo_spend.addItem(THREAT_SPEND_LABELS[k])
        cur = getattr(params, "threat_spend", "late")
        self.combo_spend.setCurrentIndex(self._spend_keys.index(cur)
                                         if cur in self._spend_keys else 0)
        self.combo_spend.setToolTip(
            "Когда БПЛА тратит СВОБОДНЫЙ ЗАПАС хода (остаток минус кратчайший путь до цели):\n"
            "• виляет, потом прямо — пока запас есть, идёт по коридорам; на исходе "
            "выпрямляется и идёт к цели;\n"
            "• прямо, потом виляет — сначала кратчайшим путём, запас тратит ближе к цели;\n"
            "• равномерно — тратит запас понемногу на всём пути.\n"
            "Действует на ИТЕРАЦИИ (возможные маршруты строятся веером всех повадок).")
        self.combo_spend.currentIndexChanged.connect(self._spend_changed)
        row_sp3.addWidget(self.combo_spend, 1)
        col.addLayout(row_sp3)
        # число итераций T (как во вкладке 2) + доля обобщённой выборки, %
        row_t = QtWidgets.QHBoxLayout()
        row_t.addWidget(QtWidgets.QLabel("число итераций:"))
        self.ed_iter_T = QtWidgets.QLineEdit(str(getattr(params, "threat_iter_routes", 150)))
        self.ed_iter_T.setToolTip("Сколько маршрутов сгенерировать (аналог T во вкладке 2). "
                                  "«Пуск» строит их по одному с анимацией полёта БПЛА.")
        self.ed_iter_T.returnPressed.connect(lambda: self.on_iter_batch())
        row_t.addWidget(self.ed_iter_T, 1)
        row_t.addWidget(QtWidgets.QLabel("обобщ. %:"))
        self.ed_iter_gen_frac = QtWidgets.QLineEdit(
            f"{getattr(params, 'threat_iter_gen_frac', 0.10) * 100:g}")
        self.ed_iter_gen_frac.setFixedWidth(56)
        self.ed_iter_gen_frac.setToolTip("Доля «обобщённой выборки», % от ПРОЙДЕННЫХ итераций "
                                         "(по умолчанию 10%). Прошло 500, доля 10% → 50 "
                                         "маршрутов; доля 20% → 100. Enter — применить.")
        self.ed_iter_gen_frac.returnPressed.connect(self._gen_frac_changed)
        row_t.addWidget(self.ed_iter_gen_frac)
        col.addLayout(row_t)
        # скорость анимации полёта
        row_sp = QtWidgets.QHBoxLayout()
        row_sp.addWidget(QtWidgets.QLabel("скорость:"))
        self.slider_speed = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider_speed.setMinimum(1); self.slider_speed.setMaximum(20)
        self.slider_speed.setValue(6)
        self.slider_speed.valueChanged.connect(lambda v: self.on_iter_speed(int(v)))
        row_sp.addWidget(self.slider_speed, 1)
        col.addLayout(row_sp)
        # Пуск / Шаг / Пакетно — как во вкладке 2
        row_run = QtWidgets.QHBoxLayout()
        self.btn_iter_play = QtWidgets.QPushButton("▶ Пуск")
        self.btn_iter_step = QtWidgets.QPushButton("Шаг")
        self.btn_iter_batch = QtWidgets.QPushButton("Пакетно")
        self._tint(self.btn_iter_play, THEME["ok"])
        self._tint(self.btn_iter_batch, THEME["accent2"])
        self.btn_iter_play.setToolTip("Анимация: итерация за итерацией строит маршрут и "
                                      "показывает движение БПЛА по нему (Пуск/Пауза).")
        self.btn_iter_step.setToolTip("Одна итерация — добавить один маршрут.")
        self.btn_iter_batch.setToolTip("Сразу все T итераций (без анимации) — весь набор мест пролёта.")
        self.btn_iter_play.clicked.connect(lambda: self.on_iter_play())
        self.btn_iter_step.clicked.connect(lambda: self.on_iter_step())
        self.btn_iter_batch.clicked.connect(lambda: self.on_iter_batch())
        row_run.addWidget(self.btn_iter_play); row_run.addWidget(self.btn_iter_step)
        row_run.addWidget(self.btn_iter_batch)
        col.addLayout(row_run)

        b1 = QtWidgets.QHBoxLayout()
        self.btn_apply = QtWidgets.QPushButton("Применить")
        self.btn_apply.setToolTip("Применить параметры (N/R/k/шаг) и пересчитать.")
        self.btn_reset = QtWidgets.QPushButton("Сброс")
        self.btn_reset.setToolTip("Убрать датчики и заданную цель (карта остаётся).")
        self.btn_view = QtWidgets.QPushButton("Весь участок")
        self.btn_view.setToolTip("Вернуть камеру к полному участку (bbox).")
        self._tint(self.btn_apply, THEME["accent2"])
        self.btn_apply.clicked.connect(lambda: self.on_apply())
        self.btn_reset.clicked.connect(lambda: self.on_reset())
        self.btn_view.clicked.connect(lambda: self.on_reset_view())
        b1.addWidget(self.btn_apply); b1.addWidget(self.btn_reset)
        b1.addWidget(self.btn_view)
        col.addLayout(b1)

        col.addWidget(self._header("ПОКАЗАТЕЛИ"))
        self.metrics = QtWidgets.QPlainTextEdit(); self.metrics.setReadOnly(True)
        self.metrics.setMinimumHeight(200)
        col.addWidget(self.metrics, stretch=1)

    # Слои показа: (атрибут, подпись, включён ли сразу, подсказка). Подпись короткая —
    # подробности живут в подсказке, иначе двенадцать строк во всю ширину панели
    # выдавливают вниз и управление итерациями, и показатели.
    LAYER_GROUPS = (
        ("местность", (
            ("chk_layers", "Векторные слои", True,
             "Реки, дороги, ЛЭП, железные дороги — то, по чему БПЛА ориентируется."),
            ("chk_relief", "Карта высот", False,
             "Абсолютные высоты: зелёные низины → коричневые возвышенности, со светотенью. "
             "Показ НЕ зависит от кнопки «Добавить рельеф» — посмотреть местность можно всегда."),
            ("chk_water", "Вода и города", False,
             "Синим — вода (датчик ставить нельзя), красным — населённые пункты "
             "(исключены из пролёта и из веса)."),
            ("chk_cross", "Пересечения", False,
             "Узлы, где сходятся ≥2 разных слоёв (дорога×река = мост, дорога×ЛЭП) — "
             "точки развилок. Скрыто по умолчанию."),
        )),
        ("весовая карта", (
            ("chk_threat", "Весовая карта", True,
             "Тепловая карта весов: где местность удобна для полёта вообще."),
            ("chk_relief_k", "Приоритет высот", False,
             "Что рельеф СДЕЛАЛ с весом: синим укрытия (вес вырос), красным возвышенности "
             "(вес упал), чёрным — где коридор снят как «очень высокая гора». "
             "Работает, только когда рельеф добавлен в вес."),
        )),
        ("пролёт БПЛА", (
            ("chk_routes", "Зона пролёта", False,
             "Розовый фон — ВСЕ достижимые места пролёта (огибающая): куда БПЛА может дойти "
             "вход→место→цель в пределах запаса хода. Считается графом, точно. Поверх — "
             "линии-примеры путей (их экспоненциально много, показываем выборку)."),
            ("chk_iter", "Маршруты движения", False,
             "Все накопленные итерационные маршруты БПЛА. Генерация — Пуск / Шаг / Пакетно."),
            ("chk_iter_heat", "Тепловая карта маршрутов", False,
             "Вторая тепловая карта: как часто маршруты проходят над клеткой. "
             "По ней и расставляются датчики."),
            ("chk_iter_gen", "Выборка", False,
             "Не весь веер, а ~10 % пройденных маршрутов: самые частые (по тепловой карте), "
             "но распределённо по карте, не кучкой. Прошло 150 → покажет 15."),
        )),
        ("датчики и подписи", (
            ("chk_cand", "Позиции датчиков", False,
             "Кандидатные позиции — узлы сетки, из которых жадный алгоритм выбирает N лучших."),
            ("chk_zones", "Запретные зоны", True,
             "Области, нарисованные кнопкой «Запретная зона»: пролёт там невозможен, "
             "маршруты идут в обход. Снятие галки прячет их с карты, но НЕ отменяет — "
             "убрать зоны можно кнопкой «Убрать зону» или «Сброс»."),
            ("chk_legend", "Легенда", False,
             "Легенда (какой цвет какой объект) — в левом нижнем углу. По умолчанию "
             "скрыта: она занимает место, а нужна не всегда."),
        )),
    )

    def _build_layer_box(self, col):
        """Блок «ПОКАЗ»: слои сгруппированы по смыслу и разложены в две колонки.

        У группы свой переключатель — гасит всех её детей разом. У групп с заливками
        (весовая карта, пролёт) есть ещё ползунок прозрачности: эти слои перекрывают друг
        друга, и притушить верхний бывает нужнее, чем выключить. Линиям и точкам ползунок
        не даём — они и так поверх, а двенадцать ползунков это и есть нагромождение."""
        col.addWidget(self._header("ПОКАЗ"))
        self._layer_checks = []
        self._group_masters = []
        for title, items in self.LAYER_GROUPS:
            head = QtWidgets.QHBoxLayout(); head.setSpacing(6)
            master = QtWidgets.QCheckBox(title)
            master.setObjectName("muted")
            master.setTristate(False)
            master.setToolTip("Показать или скрыть всю группу разом.")
            head.addWidget(master, 1)
            col.addLayout(head)
            grid = QtWidgets.QGridLayout()
            grid.setSpacing(2); grid.setContentsMargins(14, 0, 0, 2)
            kids = []
            for i, (attr, label, on, tip) in enumerate(items):
                chk = QtWidgets.QCheckBox(label)
                chk.setChecked(on)
                chk.setToolTip(tip)
                chk.stateChanged.connect(lambda _s: self._layer_toggled())
                # у тепловых карт общая цветовая шкала — запоминаем, какую включили позже
                for key, spec in self.SCALE_SPECS.items():
                    if spec["chk"] == attr:
                        chk.toggled.connect(
                            lambda on, k=key: self._note_scale_layer(k, on))
                setattr(self, attr, chk)
                self._layer_checks.append(chk)
                kids.append(chk)
                grid.addWidget(chk, i // 2, i % 2)
            col.addLayout(grid)
            master.setChecked(any(c.isChecked() for c in kids))
            master.toggled.connect(lambda on, ks=kids: self._toggle_group(ks, on))
            for c in kids:                       # ребёнка выключили руками — обновить шапку
                c.stateChanged.connect(
                    lambda _s, m=master, ks=kids: self._sync_master(m, ks))
            self._group_masters.append((master, kids))
            # кнопка «какие именно векторные слои показывать» — рядом со своей группой
            if any(a == "chk_layers" for a, _l, _o, _t in items):
                btn = QtWidgets.QPushButton("Векторные слои…")
                btn.setToolTip("Список рек, дорог, ЛЭП и прочего: что показывать по "
                               "отдельности. Влияет только на показ — ничего не "
                               "пересчитывается.")
                btn.clicked.connect(self._open_vector_dialog)
                wrap = QtWidgets.QHBoxLayout(); wrap.setContentsMargins(14, 0, 0, 4)
                wrap.addWidget(btn)
                col.addLayout(wrap)
            self._add_group_opacity(col, items)

    # ЧТО ГАСИТ ПОЛЗУНОК. Для каждого слоя перечислены ВСЕ его элементы на карте, а не
    # только заливка: сперва ползунок трогал одни растры, и выходило странное — розовая
    # зона пролёта тускнела, а синие линии поверх неё оставались яркими; у векторных
    # слоёв не менялось вовсе. Спецключ "*vectors" разворачивается в десяток линий и их
    # обводки — они живут в словарях, а не отдельными полями.
    OPACITY_TARGETS = {
        "chk_layers":    ("*vectors", "bridge_scatter"),
        "chk_relief":    ("relief_img",),
        "chk_water":     ("water_img", "builtup_img"),
        "chk_cross":     ("crossing_scatter",),
        "chk_threat":    ("threat_img", "cbar"),
        "chk_relief_k":  ("relief_k_img",),
        "chk_routes":    ("route_area_img", "route_item"),
        "chk_iter":      ("route_iter_item", "route_cur_item"),
        "chk_iter_heat": ("iter_heat_img",),
        "chk_iter_gen":  ("route_gen_item",),
        "chk_cand":      ("cand_scatter",),
    }

    def _opacity_items(self, attrs):
        """Развернуть цели в реальные элементы сцены (включая словарные слои)."""
        out = []
        for attr in attrs:
            for item_name in self.OPACITY_TARGETS.get(attr, ()):
                if item_name == "*vectors":
                    out += list(self._layer_items.values())
                    out += list(self._layer_casing.values())
                    continue
                it = getattr(self, item_name, None)
                if it is not None:
                    out.append(it)
        return out

    def _add_group_opacity(self, col, items):
        """Ползунок прозрачности на группу — гасит всё, что эта группа рисует."""
        targets = [a for a, _l, _o, _t in items if a in self.OPACITY_TARGETS]
        if not targets:
            return
        row = QtWidgets.QHBoxLayout(); row.setContentsMargins(14, 0, 0, 6); row.setSpacing(6)
        cap = QtWidgets.QLabel("прозрачность"); cap.setObjectName("muted")
        sld = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        sld.setRange(20, 100); sld.setValue(100); sld.setFixedWidth(96)
        sld.setToolTip("Притушить заливки этой группы, не выключая их. "
                       "Только показ — веса не меняются.")
        sld.valueChanged.connect(lambda v, ts=targets: self._set_group_opacity(ts, v))
        row.addWidget(cap); row.addWidget(sld, 1)
        col.addLayout(row)

    def _set_group_opacity(self, attrs, value):
        """Прозрачность слоёв-заливок. Модель не трогаем — это чистая отрисовка.

        Ползунок МАСШТАБИРУЕТ родную прозрачность слоя, а не задаёт её: у рельефа она
        0.55, у тепловой карты 0.62 — выставив 1.0, мы бы сделали их непрозрачными и
        закрыли всё под ними. При 100 % слой выглядит ровно так, как задумано."""
        a = max(0.0, min(1.0, value / 100.0))
        for it in self._opacity_items(attrs):
            try:
                key = id(it)
                if key not in self._base_opacity:
                    self._base_opacity[key] = float(it.opacity())
                it.setOpacity(self._base_opacity[key] * a)
            except Exception:
                pass

    def _layer_toggled(self):
        """Слой переключили. Во время группового переключения молчим — перерисуем один
        раз в конце, а не по разу на каждый слой группы."""
        if self._group_busy:
            return
        if self._vec_dialog is not None and self._vec_dialog.isVisible():
            self._vec_dialog.sync()          # окно «Векторные слои» и панель — одно состояние
        self.on_toggle()

    def _toggle_group(self, kids, on):
        """Переключатель группы: гасит/зажигает детей одной перерисовкой."""
        self._group_busy = True
        try:
            for c in kids:
                if c.isEnabled():
                    c.setChecked(bool(on))
        finally:
            self._group_busy = False
        self._layer_toggled()

    def _sync_master(self, master, kids):
        """Шапка группы показывает, включён ли хоть один её слой."""
        if self._group_busy:
            return
        master.blockSignals(True)
        master.setChecked(any(c.isChecked() for c in kids))
        master.blockSignals(False)

    def set_layer_enabled(self, **state):
        """Погасить слои, которых ещё нет: «галка стоит, а на карте ничего» — это вопрос
        к программе, а не к пользователю. Показ при этом НЕ трогаем: галка остаётся, где
        стояла, и слой вернётся сам, как только данные появятся."""
        # Гасим только то, что САМО не появится. «Зона пролёта», «Маршруты движения» и
        # «Выборка» считаются по включению галки (см. on_toggle контроллера) — их держим
        # доступными, пока есть карта. А тепловая карта маршрутов сама не считается: без
        # накопленных маршрутов галка над ней стояла бы впустую.
        rules = dict(
            chk_threat="grid", chk_layers="grid", chk_water="grid", chk_cross="grid",
            chk_routes="grid", chk_cand="grid", chk_iter="grid", chk_iter_gen="grid",
            chk_relief="dem", chk_relief_k="relief", chk_iter_heat="iter")
        hints = dict(
            grid="Сначала «Построить карту».",
            dem="Нет данных о высотах — карта высот не загружена.",
            relief="Сначала «Добавить рельеф» — тогда видно, что он сделал с весом.",
            iter="Сначала «Пуск», «Шаг» или «Пакетно» — маршрутов ещё нет.")
        for attr, key in rules.items():
            chk = getattr(self, attr, None)
            if chk is None:
                continue
            ok = bool(state.get(key, True))
            chk.setEnabled(ok)
            if not ok:
                chk.setToolTip(hints[key])
            else:                                   # вернуть родную подсказку
                for _t, items in self.LAYER_GROUPS:
                    for a, _l, _on, tip in items:
                        if a == attr:
                            chk.setToolTip(tip)

    def _build_map_combo(self, col, params):
        col.addWidget(self._header("КАРТА"))
        self.combo_map = QtWidgets.QComboBox()
        labels = dict(gm.LAYER_LABELS)
        self._map_keys = list(labels)
        for name in gm.discover_local_layers():
            labels[name] = f"{name} (локальный кэш)"
            self._map_keys.append(name)
        for k in self._map_keys:
            self.combo_map.addItem(labels[k])
        cur = getattr(params, "map_layer3", "osm")
        self.combo_map.setCurrentIndex(self._map_keys.index(cur)
                                       if cur in self._map_keys else 0)
        self.combo_map.currentIndexChanged.connect(self._map_changed)
        col.addWidget(self.combo_map)
        self.chk_offline = QtWidgets.QCheckBox("офлайн (только кэш, без сети)")
        self.chk_offline.setChecked(bool(getattr(params, "map_offline3", False)))
        self.chk_offline.stateChanged.connect(self._offline_changed)
        col.addWidget(self.chk_offline)

    def _map_changed(self, i):
        self._map_layer = self._map_keys[i]
        self.on_map_layer(self._map_layer)
        self._refresh_basemap(force=True)

    def _offline_changed(self, _s):
        self._map_offline = self.chk_offline.isChecked()
        self.on_map_offline(self._map_offline)
        self._refresh_basemap(force=True)

    def _iter_mode_changed(self, i):
        self.on_iter_mode(self._iter_keys[i])

    def _gen_frac_changed(self):
        self.on_iter_gen_frac(self.get_iter_gen_frac())

    def get_iter_gen_frac(self):
        """Доля обобщённой выборки как число 0..1 (поле задаётся в процентах). При ошибке —
        текущее значение из параметров. Ограничено диапазоном [1%, 100%]."""
        try:
            v = float(self.ed_iter_gen_frac.text().strip().replace(",", ".").rstrip("% "))
        except (ValueError, AttributeError):
            return float(getattr(self._params_ref, "threat_iter_gen_frac", 0.10))
        return min(1.0, max(0.01, v / 100.0))

    def _spread_changed(self, i):
        self.on_iter_spread(self._spread_keys[i])

    def _spend_changed(self, i):
        self.on_iter_spend(self._spend_keys[i])

    def _on_field_submit(self):
        if not self._suppress:
            self.on_apply()

    # ==================================================================
    def _build_scene_items(self):
        """Создать ВСЕ постоянные слои-элементы карты один раз (потом только меняем данные,
        а не пересоздаём). Порядок наложения — через setZValue: подложка/тепло — отрицательные,
        маршруты — 1..6, датчики и точки A/B — 20..22 (поверх всего). Цвета — из THREAT_COLORS."""
        # рельеф: карта высот — самый нижний слой (фон-подложка под всем остальным),
        # поверх неё «приоритет по высоте». Оба под весовой картой и векторными слоями,
        # чтобы реки и дороги оставались читаемыми.
        self.relief_img = pg.ImageItem(); self.relief_img.setOpts(axisOrder="row-major")
        self.relief_img.setZValue(-9.0); self.relief_img.setOpacity(0.55)
        self.relief_img.setVisible(False)
        self.pi.addItem(self.relief_img)
        self.relief_k_img = pg.ImageItem(); self.relief_k_img.setOpts(axisOrder="row-major")
        self.relief_k_img.setZValue(-8.5); self.relief_k_img.setOpacity(0.75)
        self.relief_k_img.setVisible(False)
        self.pi.addItem(self.relief_k_img)
        # весовая карта (тепловой слой)
        self.threat_img = pg.ImageItem(); self.threat_img.setOpts(axisOrder="row-major")
        self.threat_img.setZValue(-8); self.threat_img.setOpacity(0.55)
        self.threat_img.setVisible(False)
        self.pi.addItem(self.threat_img)
        self.cbar = pg.ColorBarItem(
            values=(0, 1), colorMap=_threat_cmap(),
            label="вес ячейки (низкий → высокий)", interactive=False)
        try:
            self.cbar.setImageItem(self.threat_img, insert_in=self.pi)
        except Exception:
            pass
        self.cbar.setVisible(False)

        # запрет воды (маска)
        self.water_img = pg.ImageItem(); self.water_img.setOpts(axisOrder="row-major")
        self.water_img.setZValue(-7); self.water_img.setOpacity(0.5)
        self.water_img.setVisible(False)
        self.pi.addItem(self.water_img)

        # застройка растром — на ОБЩЕМ виде вместо 8275 контуров (они там всё равно
        # сливаются в пятно, а рисуются долго); при приближении включаются контуры
        self.builtup_img = pg.ImageItem(); self.builtup_img.setOpts(axisOrder="row-major")
        self.builtup_img.setZValue(-5.5); self.builtup_img.setOpacity(0.55)
        self.builtup_img.setVisible(False)
        self.pi.addItem(self.builtup_img)

        # векторные слои. antialias=False + connect="finite" — на реальных данных
        # (сотни тысяч точек) сглаживание линий делает панораму медленной; без него
        # быстро. Данные ещё и ПРОРЕЖИВАЮТСЯ при отрисовке (см. _polys_to_xy).
        for name in THREAT_LAYER_ORDER:
            if name not in LAYER_STYLE:
                continue
            st = LAYER_STYLE[name]
            z = float(st.get("z", -5.0))
            # ОБВОДКА (casing) — широкая светлая линия ПОД цветной. Именно она разделяет
            # линии на пересечениях: без неё дорога, река и ЛЭП в одной точке сливаются
            # в неразличимое пятно. Своим элементом, чуть ниже по z.
            if st.get("casing"):
                cpen = pg.mkPen(_qcolor(st["casing"], 210),
                                width=st.get("casing_w", st["width"] + 2.0))
                cit = self.pi.plot([], [], pen=cpen, antialias=False, connect="finite")
                cit.setZValue(z - 0.04)
                self._layer_casing[name] = cit
            pen = pg.mkPen(_qcolor(st["color"], st.get("alpha", 235)), width=st["width"],
                           dash=st["dash"])
            item = self.pi.plot([], [], pen=pen, antialias=False, connect="finite")
            item.setZValue(z)                    # порядок задан явно, а не «кто последний»
            self._layer_items[name] = item
        # мосты — точечные маркеры (число ограничено, см. render_layers)
        self.bridge_scatter = pg.ScatterPlotItem(
            size=11, symbol="t", brush=pg.mkBrush(_qcolor(THEME["warn"])),
            pen=pg.mkPen("white", width=0.8))
        self.bridge_scatter.setZValue(-3); self.pi.addItem(self.bridge_scatter)

        # ВСЕ возможные места пролёта (envelope) — полупрозрачный слой ячеек
        self.route_area_img = pg.ImageItem(); self.route_area_img.setOpts(axisOrder="row-major")
        self.route_area_img.setZValue(-6); self.route_area_img.setVisible(False)
        self.pi.addItem(self.route_area_img)
        # линии-примеры маршрутов поверх фона-огибающей: бирюзовый, но ПОЛУПРОЗРАЧНЫЙ —
        # чтобы не перекрывать датчики и не сливаться в сплошную заливку.
        self.route_item = self.pi.plot([], [], antialias=True, connect="finite",
                                       pen=pg.mkPen(_qcolor(THREAT_COLORS["route_all"], 100), width=1.6))
        self.route_item.setZValue(3)
        # ЗАПРЕТНЫЕ ЗОНЫ, нарисованные пользователем. Поверх карты, но ПОД маршрутами и
        # датчиками: зона — это ограничение задачи, а не результат, заслонять его нечем.
        self.zone_fill = self.pi.plot([], [], antialias=False, connect="finite",
                                      pen=None, fillLevel=None,
                                      brush=pg.mkBrush(THREAT_COLORS["zone_fill"]))
        self.zone_fill.setZValue(0.4)
        # ШТРИХОВКА — то, что делает зону «закрашенной», не пряча карту под ней:
        # диагональные линии того же цвета, что и контур (см. _hatch_polygon)
        # Толщина линий зоны меняется НЕМНОГО и в обратную сторону, чем у векторных слоёв:
        # там канты худеют при ОТДАЛЕНИИ (иначе сливаются в кашу), а зона на общем виде
        # должна быть заметной — она одна-две на карту. Худеет она при ПРИБЛИЖЕНИИ, и
        # всего на четверть, чтобы толстый контур не закрывал местность (_scale_zone_widths).
        self.zone_hatch = self.pi.plot([], [], antialias=True, connect="finite",
                                       pen=pg.mkPen(_qcolor(THREAT_COLORS["zone_hatch"]),
                                                    width=2.0))
        self.zone_hatch.setZValue(0.45)
        self.zone_item = self.pi.plot([], [], antialias=True, connect="finite",
                                      pen=pg.mkPen(_qcolor(THREAT_COLORS["zone"], 245),
                                                   width=3.4, dash=[7, 4]))
        self.zone_item.setZValue(0.5)
        # то, что рисуется прямо сейчас: ломаная и поставленные вершины
        self.zone_draw_item = self.pi.plot([], [], antialias=True, connect="finite",
                                           pen=pg.mkPen(_qcolor(THREAT_COLORS["zone_draw"]),
                                                        width=3.0, dash=[5, 3]))
        self.zone_draw_item.setZValue(6)
        self.zone_draw_pts = pg.ScatterPlotItem(
            size=10, symbol="o", brush=pg.mkBrush(_qcolor(THREAT_COLORS["zone_pt"])),
            pen=pg.mkPen(THREAT_COLORS["zone_pt_edge"], width=1.8))
        self.zone_draw_pts.setZValue(6.1)
        self.pi.addItem(self.zone_draw_pts)
        # 2-я тепловая карта — частота пролёта БПЛА (плотность итерационных маршрутов)
        self.iter_heat_img = pg.ImageItem(); self.iter_heat_img.setOpts(axisOrder="row-major")
        self.iter_heat_img.setZValue(-7); self.iter_heat_img.setOpacity(0.62)
        self.iter_heat_img.setVisible(False)
        self.pi.addItem(self.iter_heat_img)
        # ИТЕРАЦИИ: накопленные маршруты (тонкие) + текущий (ярче) + маркер БПЛА.
        # Анимацию ведёт контроллер (iter_setup_flight/iter_update_flight) — как вкладка 2.
        # накопленные итерационные маршруты («выборка») — КРАСНЫЕ и полупрозрачные (менее плотно)
        self.route_iter_item = self.pi.plot([], [], antialias=True, connect="finite",
                                            pen=pg.mkPen(_qcolor(THREAT_COLORS["route_iter"], 80), width=1.4))
        self.route_iter_item.setZValue(1)
        # ОБОБЩЁННАЯ выборка (~10 % итераций) — тоже КРАСНАЯ, но плотнее накопленных (её видно).
        self.route_gen_item = self.pi.plot([], [], antialias=True, connect="finite",
                                           pen=pg.mkPen(_qcolor(THREAT_COLORS["route_gen"], 160), width=2.2))
        self.route_gen_item.setZValue(4); self.route_gen_item.setVisible(False)
        # ОСНОВНОЙ (текущий) маршрут итерации — ЧЁРНЫЙ, чётко виден на светлой карте
        self.route_cur_item = self.pi.plot([], [], antialias=True, connect="finite",
                                           pen=pg.mkPen(_qcolor(THREAT_COLORS["route_main"], 255), width=3.2))
        self.route_cur_item.setZValue(5); self.route_cur_item.setVisible(False)
        self.uav_marker = pg.ScatterPlotItem(
            size=14, symbol="t1", brush=pg.mkBrush(_qcolor(THREAT_COLORS["uav"])),
            pen=pg.mkPen("white", width=1.3))
        self.uav_marker.setZValue(6); self.uav_marker.setVisible(False)
        self.pi.addItem(self.uav_marker)
        # пересечения (перекрёстки дорог/рек/ЛЭП) — узлы развилок, скрыто по умолчанию
        self.crossing_scatter = pg.ScatterPlotItem(
            size=7, symbol="d", brush=pg.mkBrush(_qcolor(THREAT_COLORS["crossing"], 220)),
            pen=pg.mkPen(THREAT_COLORS["crossing_edge"], width=0.6))
        self.crossing_scatter.setZValue(-2); self.pi.addItem(self.crossing_scatter)

        # легенда векторных слоёв (цвет -> объект) — что чем отображается.
        # anchor (0,1) — точка привязки = НИЖНИЙ-левый угол текста (легенда в левом
        # нижнем углу вида); включается отдельным чекбоксом «легенда».
        self.legend = pg.TextItem(anchor=(0, 1), fill=pg.mkBrush(THREAT_COLORS["legend_bg"]))
        self.legend.setZValue(20)
        self._legend_scale = 1.0                 # текущий кегль (доля от базового)
        self.legend.setHtml(self._legend_html())
        self.pi.addItem(self.legend); self.legend.setVisible(False)

        # легенда режима «приоритет по высоте» — у ПРАВОГО края, как шкала весовой карты.
        # anchor (1,0) — привязка за ВЕРХНИЙ-ПРАВЫЙ угол. Показывается своим чекбоксом,
        # независимо от общей легенды: включил слой — сразу видно, что значат цвета.
        self.relief_legend = pg.TextItem(anchor=(1, 0),
                                         fill=pg.mkBrush(THREAT_COLORS["legend_bg"]))
        self.relief_legend.setZValue(20)
        self._relief_legend_scale = 1.0          # текущий кегль (доля от базового)
        self.relief_legend.setHtml(self._relief_legend_html())
        self.pi.addItem(self.relief_legend); self.relief_legend.setVisible(False)

        # рамка bbox
        self.bbox_item = self.pi.plot([], [], pen=pg.mkPen(_qcolor(THEME["accent"], 180),
                                                           width=1.6, dash=[8, 5]))
        self.bbox_item.setZValue(-4)
        kx0, kx1, ky0, ky1 = self.bbox_km
        self.bbox_item.setData([kx0, kx1, kx1, kx0, kx0], [ky0, ky0, ky1, ky1, ky0])

        # кандидатные позиции
        self.cand_scatter = pg.ScatterPlotItem(
            size=4, symbol="o", brush=pg.mkBrush(_qcolor(THEME["muted"], 120)),
            pen=None)
        self.cand_scatter.setZValue(-2); self.pi.addItem(self.cand_scatter)

        # вход/цель — крупные контрастные значки (A — зелёная звезда, B — красный крест)
        self.entry_scatter = pg.ScatterPlotItem(size=26, symbol="star",
            brush=pg.mkBrush(_qcolor(THEME["ok"])), pen=pg.mkPen("white", width=2.2))
        self.entry_scatter.setZValue(22); self.pi.addItem(self.entry_scatter)   # A/B — поверх датчиков
        self.target_scatter = pg.ScatterPlotItem(size=24, symbol="x",
            brush=pg.mkBrush(_qcolor(THEME["warn"])), pen=pg.mkPen("white", width=2.6))
        self.target_scatter.setZValue(22); self.pi.addItem(self.target_scatter)

    # ==================================================================
    # API для контроллера
    # ==================================================================
    def get_toggles(self):
        return dict(show_threat=self.chk_threat.isChecked(),
                    show_layers=self.chk_layers.isChecked(),
                    show_water=self.chk_water.isChecked(),
                    show_cand=self.chk_cand.isChecked(),
                    show_routes=self.chk_routes.isChecked(),
                    show_cross=self.chk_cross.isChecked(),
                    show_iter=self.chk_iter.isChecked(),
                    show_iter_heat=self.chk_iter_heat.isChecked(),
                    show_iter_gen=self.chk_iter_gen.isChecked(),
                    show_relief=self.chk_relief.isChecked(),
                    show_relief_k=self.chk_relief_k.isChecked(),
                    show_zones=self.chk_zones.isChecked(),
                    iter_mode=self._iter_keys[self.combo_iter.currentIndex()],
                    show_legend=self.chk_legend.isChecked())

    def get_param_values(self):
        out = {}
        for name, _l, typ in self.THREAT_PARAM_SPECS:
            raw = self._fields[name].text().strip().replace(",", ".")
            out[name] = typ(float(raw)) if typ is int else typ(raw)
        return out

    def set_param_values(self, params):
        self._suppress = True
        try:
            for name, _l, _t in self.THREAT_PARAM_SPECS:
                self._fields[name].setText(str(getattr(params, name)))
        finally:
            self._suppress = False

    def set_busy(self, busy):
        """Заблокировать кнопки действий на время фонового расчёта (чтобы не запускать
        второй параллельно) и показать курсор ожидания."""
        for b in (self.btn_data, self.btn_build, self.btn_target, self.btn_place,
                  self.btn_apply, self.btn_reset):
            b.setEnabled(not busy)
        # кнопка рельефа отдельно: без файла высот она остаётся недоступной и после расчёта
        self.btn_relief.setEnabled((not busy) and getattr(self, "_relief_enabled", False))
        self.setCursor(QtCore.Qt.WaitCursor if busy else QtCore.Qt.ArrowCursor)

    def set_source(self, text):
        self.src_label.setText(f"источник данных: {text}")

    def set_metrics(self, lines):
        self.metrics.setPlainText("\n".join(lines))

    def flash_title(self, text, color=None):
        self.pi.setTitle(text, color=color or THEME["warn"], size="10pt")

    def set_title(self, text):
        self.pi.setTitle(text, color=THEME["text"], size="10pt")

    def frame_bbox(self):
        """Показать ВЕСЬ участок целиком (кнопка «Весь участок»). Небольшой отступ, чтобы
        рамка bbox не липла к краям; при широком экране участок помещается полностью
        (пределы вида это теперь допускают, см. _set_view_limits)."""
        kx0, kx1, ky0, ky1 = self.bbox_km
        self.pi.setRange(xRange=(kx0, kx1), yRange=(ky0, ky1), padding=0.06)

    def process_pending(self):
        QtWidgets.QApplication.processEvents()

    # ---- отрисовка данных карты ----
    MAX_LAYER_PTS = 48000     # предел точек на слой при отрисовке (прореживание)
    MAX_LAYER_POLYS = 10000   # предел числа линий на слой (берём самые длинные)
    MAX_BRIDGES = 600         # маркеры ПЕРЕПРАВ (мост над водой); их ~400, влезают все
    BUILTUP_RASTER_FRAC = 0.35  # кадр уже этой доли участка -> застройка контурами

    @classmethod
    def _polys_to_xy(cls, polys, view_box=None):
        """Список ломаных -> массивы X,Y с NaN-разрывами, с ПРОРЕЖИВАНИЕМ (иначе
        сотни тысяч точек и тысячи мелких линий тормозят отрисовку при панораме):
        оставляем не более MAX_LAYER_POLYS самых длинных линий и прореживаем точки до
        ~MAX_LAYER_PTS. Концы линий сохраняются. Полностью на numpy.

        «Длинные» — по ГЕОГРАФИЧЕСКОЙ длине (км), не по числу точек: у порезанных
        сегментов число точек одинаковое (2), и сортировка по len() оставляла первые
        попавшиеся — дороги рисовались только вокруг одного города.

        view_box (x0,x1,y0,y1) — ВИДИМАЯ область: линии вне кадра отбрасываются ДО
        прореживания, поэтому при приближении лимиты тратятся только на то, что видно,
        и объект рисуется целиком. При отдалении в кадр попадает всё — прореживание
        работает как раньше."""
        arrs = [np.asarray(p, float) for p in polys
                if np.ndim(p) == 2 and len(p) >= 2]
        if not arrs:
            return np.empty(0), np.empty(0)
        if view_box is not None:
            vx0, vx1, vy0, vy1 = view_box
            vis = []
            for p in arrs:                                 # habbox линии пересекает кадр?
                if (p[:, 0].max() >= vx0 and p[:, 0].min() <= vx1 and
                        p[:, 1].max() >= vy0 and p[:, 1].min() <= vy1):
                    vis.append(p)
            arrs = vis or arrs                             # пусто — показать хоть что-то
        if len(arrs) > cls.MAX_LAYER_POLYS:                # самые длинные (значимые) линии
            km = [float(np.hypot(*(np.diff(p, axis=0).T)).sum()) for p in arrs]
            order = np.argsort(km)[::-1][:cls.MAX_LAYER_POLYS]
            arrs = [arrs[i] for i in order]
        total = sum(len(p) for p in arrs)
        step = max(1, int(np.ceil(total / cls.MAX_LAYER_PTS)))
        nan = np.array([np.nan])
        xs, ys = [], []
        for p in arrs:
            if step > 1 and len(p) > 4:
                idx = np.arange(0, len(p), step)
                if len(idx) < 4:                           # мелкий полигон (квартал города)
                    idx = np.linspace(0, len(p) - 1, 4).astype(int)   # не схлопывать
                elif idx[-1] != len(p) - 1:
                    idx = np.append(idx, len(p) - 1)       # сохранить конец линии
                p = p[idx]
            xs.append(p[:, 0]); xs.append(nan)
            ys.append(p[:, 1]); ys.append(nan)
        return np.concatenate(xs), np.concatenate(ys)

    def _view_box_km(self):
        """Видимая область в км (x0,x1,y0,y1) — по ней прореживаются слои."""
        try:
            (x0, x1), (y0, y1) = self.vb.viewRange()
            return (float(x0), float(x1), float(y0), float(y1))
        except Exception:
            return None

    def _zoomed_in(self, view_box):
        """Приблизились ли настолько, что застройку пора рисовать НАСТОЯЩИМИ контурами
        (а не растровой маской). Порог — ширина кадра меньше доли всего участка."""
        if view_box is None:
            return True
        kx0, kx1, _, _ = self.bbox_km
        span = max(1e-6, kx1 - kx0)
        return (view_box[1] - view_box[0]) <= span * self.BUILTUP_RASTER_FRAC

    def render_layers(self, layers, bridge_pts, toggles, built_mask=None,
                      extent=None):
        """Отрисовать векторные слои. Чекбокс «векторные слои» ПОЛНОСТЬЮ скрывает их
        (данные очищаются -> нулевая стоимость отрисовки).

        Прореживание идёт по ВИДИМОЙ области: при приближении лимиты тратятся только на
        то, что в кадре, поэтому объекты рисуются целиком. Застройка на общем виде
        показывается растровой маской (8275 контуров всё равно сливаются в пятно и
        только тормозят), а при приближении переключается на настоящие контуры."""
        self._last_layers = layers                       # для перерисовки без пересчёта
        self._last_built, self._last_extent = built_mask, extent
        show = toggles["show_layers"]
        vb = self._view_box_km() if show else None
        raster_built = (show and self._vec_visible.get("built_up", True)
                        and built_mask is not None and extent is not None
                        and not self._zoomed_in(vb))
        for name, item in self._layer_items.items():
            on = show and self._vec_visible.get(name, True)
            vis = on and not (name == "built_up" and raster_built)
            xs, ys = (self._polys_to_xy(layers.get(name, []), vb) if vis else ([], []))
            item.setData(xs, ys, antialias=False, connect="finite")
            item.setVisible(vis)                         # скрытый слой = нулевая нагрузка
            self._set_casing(name, xs, ys, vis)
        self._render_builtup_raster(built_mask if raster_built else None, extent)
        if show and len(bridge_pts):
            bp = np.asarray(bridge_pts, float)
            if len(bp) > self.MAX_BRIDGES:               # не заваливать карту маркерами
                idx = np.linspace(0, len(bp) - 1, self.MAX_BRIDGES).astype(int)
                bp = bp[idx]
            self.bridge_scatter.setData(bp[:, 0], bp[:, 1])
            self.bridge_scatter.setVisible(True)
        else:
            self.bridge_scatter.setData([], [])
            self.bridge_scatter.setVisible(False)
        # легенда — в левом НИЖНЕМ углу вида, по своему чекбоксу (независимо от слоёв)
        self.legend.setVisible(toggles.get("show_legend", False))
        self.reposition_legends()

    # ЦВЕТОВАЯ ШКАЛА — одна на две тепловые карты. Обе рисуются полупрозрачной заливкой
    # по всей области, и держать две шкалы разом негде: они займут правый край вдвое.
    # Показывается шкала ПОСЛЕДНЕГО ВКЛЮЧЁННОГО слоя — это и есть тот, на который
    # пользователь смотрит сейчас.
    SCALE_SPECS = {
        "threat":    dict(item="threat_img", chk="chk_threat", cmap=_threat_cmap,
                          label="вес ячейки (низкий → высокий)"),
        "iter_heat": dict(item="iter_heat_img", chk="chk_iter_heat", cmap=_iter_heat_cmap,
                          label="частота пролёта (редко → часто)"),
    }

    def _note_scale_layer(self, key, on):
        """Запомнить, какой слой включили последним (по нему выбирается шкала)."""
        if key in self._scale_order:
            self._scale_order.remove(key)
        if on:
            self._scale_order.append(key)
        else:                                  # выключенный уходит в начало, а не пропадает:
            self._scale_order.insert(0, key)   # включат обратно — снова станет последним

    def _update_scale(self):
        """Показать шкалу того слоя, который включён последним. Ни одного — скрыть."""
        live = [k for k in self._scale_order
                if getattr(self, self.SCALE_SPECS[k]["item"], None) is not None
                and getattr(self, self.SCALE_SPECS[k]["item"]).isVisible()]
        if not live:
            self.cbar.setVisible(False)
            self._scale_shown = None
            return
        key = live[-1]
        if key != self._scale_shown:           # не дёргать палитру на каждой отрисовке
            spec = self.SCALE_SPECS[key]
            try:
                self.cbar.setColorMap(spec["cmap"]())
                self.cbar.setLabel("right", spec["label"])
            except Exception:
                pass
            self._scale_shown = key
        self.cbar.setVisible(True)

    def _set_casing(self, name, xs, ys, visible):
        """Обводке — те же данные, что и самой линии (она рисуется под ней)."""
        cit = self._layer_casing.get(name)
        if cit is None:
            return
        cit.setData(xs, ys, antialias=False, connect="finite")
        cit.setVisible(bool(visible))

    def apply_vector_visibility(self):
        """Показать/скрыть отдельные векторные слои ПО СОХРАНЁННЫМ данным.

        Модель не трогается вовсе: веса, маршруты и датчики остаются как были — меняется
        только то, что нарисовано. Данные берутся из последнего вызова `render_layers`,
        поэтому включённый обратно слой рисуется сразу, а не после пересборки карты."""
        if not self._layer_items:
            return
        show = self.chk_layers.isChecked()
        vb = self._view_box_km() if show else None
        raster_built = (show and self._vec_visible.get("built_up", True)
                        and self._last_built is not None and self._last_extent is not None
                        and not self._zoomed_in(vb))
        for name, item in self._layer_items.items():
            on = show and self._vec_visible.get(name, True)
            vis = on and not (name == "built_up" and raster_built)
            xs, ys = (self._polys_to_xy(self._last_layers.get(name, []), vb)
                      if vis else ([], []))
            item.setData(xs, ys, antialias=False, connect="finite")
            item.setVisible(vis)
            self._set_casing(name, xs, ys, vis)
        self._render_builtup_raster(self._last_built if raster_built else None,
                                    self._last_extent)

    def _open_vector_dialog(self):
        """Окно со списком векторных слоёв. Не модальное: с ним можно работать, глядя
        на карту, — переключил слой и сразу видно результат. Второй раз открывается то же
        окно, а не новое."""
        if self._vec_dialog is None:
            self._vec_dialog = VectorLayersDialog(self)
        self._vec_dialog.sync()
        self._vec_dialog.show()
        self._vec_dialog.raise_()
        self._vec_dialog.activateWindow()

    def set_vector_visible(self, name, on):
        """Переключить один слой (вызывается из окна) и сразу перерисовать."""
        self._vec_visible[str(name)] = bool(on)
        self.apply_vector_visibility()

    def vector_visible(self):
        return dict(self._vec_visible)

    def reposition_legends(self):
        """Прижать обе легенды к углам ТЕКУЩЕГО вида: общую — влево вниз, легенду
        приоритета по высоте — вправо вверх.

        Отдельным методом, потому что вызывается ещё и при зуме/панораме: раньше
        позиция ставилась только внутри `render_layers`, а та при выключенных слоях
        не доходила до легенды — после зума панель оставалась на прежнем месте.

        Заодно МЕЛЬЧАЕТ панель приоритета: чем сильнее приближение, тем меньше кегль
        (корень сглаживает — иначе на двукратном зуме текст падал бы вдвое)."""
        try:
            (x0, x1), (y0, y1) = self.vb.viewRange()
        except Exception:
            return
        kx0, kx1, _ky0, _ky1 = self.bbox_km
        frac = abs(x1 - x0) / max(abs(kx1 - kx0), 1e-6)          # доля участка в кадре
        scale = float(np.clip(np.sqrt(min(frac, 1.0)), 0.55, 1.0))
        if self.legend.isVisible():
            # РАЗМЕР ПОСТОЯННЫЙ — тот, что на общем виде. Мельчание при зуме сделано для
            # панели «приоритет по высоте» (три цвета, её видно и мелкой), а общая легенда
            # длинная: ужимаясь, она становилась нечитаемой ровно тогда, когда по ней и
            # сверяют объекты — на приближении.
            self.legend.setPos(x0 + (x1 - x0) * 0.005, y0 + (y1 - y0) * 0.02)
        if self.relief_legend.isVisible():
            if abs(scale - self._relief_legend_scale) > 0.03:
                self._relief_legend_scale = scale
                self.relief_legend.setHtml(self._relief_legend_html(scale))
            self.relief_legend.setPos(x1 - (x1 - x0) * 0.005, y1 - (y1 - y0) * 0.02)
        self._scale_markers(scale)

    # Размер точечных маркеров: на общем виде и мосты, и развилки идут сотнями и
    # застилают карту сплошным ковром (линии при этом читаются). Уменьшаем их при
    # отдалении — линии оставляем все, разгружаем именно точки.
    MARKER_SIZE = {"bridge_scatter": 11.0, "crossing_scatter": 7.0}
    MARKER_MIN = 0.45              # доля от базового размера при полном участке

    def _scale_markers(self, scale):
        k = float(np.clip(scale, self.MARKER_MIN, 1.0))
        if abs(k - self._marker_scale) < 0.05:
            return                                        # не дёргать setSize на каждый кадр
        self._marker_scale = k
        for attr, base in self.MARKER_SIZE.items():
            it = getattr(self, attr, None)
            if it is not None:
                try:
                    it.setSize(max(3.0, base * k))
                except Exception:
                    pass
        self._scale_line_widths(k)
        self._scale_zone_widths(k)

    def _scale_zone_widths(self, k):
        """Толщина линий запретной зоны: на общем виде полная, при максимальном
        приближении — `ZONE_LINE_MIN` от неё (то есть на четверть тоньше). Между
        соседними уровнями зума разница выходит в доли процента — линия не «прыгает»."""
        lo = float(self.MARKER_MIN)
        frac = (float(k) - lo) / max(1.0 - lo, 1e-6)            # 0 вблизи … 1 на общем виде
        kz = self.ZONE_LINE_MIN + (1.0 - self.ZONE_LINE_MIN) * float(np.clip(frac, 0.0, 1.0))
        for attr, base in self.ZONE_WIDTH.items():
            it = getattr(self, attr, None)
            if it is None:
                continue
            pen = it.opts.get("pen")
            if pen is None:
                continue
            try:
                pen.setWidthF(max(0.8, base * kz))
                it.setPen(pen)
            except Exception:
                pass

    # Насколько худеет ОБВОДКА на общем виде. Сама линия почти не меняется (иначе слой
    # пропадёт), а кант ужимается сильно: вблизи он разделяет линии, а на общем виде,
    # где дороги идут густой сетью, пятимиллиметровые белые канты соседних дорог
    # сливаются в сплошное белёсое пятно — заказчик назвал это «странно накладываются».
    CASING_MIN = 0.35              # доля от базовой ширины канта при полном участке
    LINE_MIN = 0.75                # доля от базовой ширины самой линии
    # ЗАПРЕТНАЯ ЗОНА худеет иначе, чем векторные слои: на ОБЩЕМ виде она должна быть
    # заметной (зона одна-две на карту, теряться ей нельзя), а при приближении — чуть
    # тоньше, чтобы толстый контур не закрывал местность под собой. Разница четверть.
    ZONE_LINE_MIN = 0.75
    ZONE_WIDTH = dict(zone_item=3.4, zone_hatch=2.0, zone_draw_item=3.0)

    def _scale_line_widths(self, k):
        """Подогнать толщину линий и кантов под масштаб (k = 1 вблизи, меньше — дальше)."""
        kc = self.LINE_MIN + (1.0 - self.LINE_MIN) * k
        kg = self.CASING_MIN + (1.0 - self.CASING_MIN) * k
        for name, item in self._layer_items.items():
            st = LAYER_STYLE.get(name)
            if st is None:
                continue
            try:
                item.setPen(pg.mkPen(_qcolor(st["color"], st.get("alpha", 235)),
                                     width=max(0.8, st["width"] * kc), dash=st["dash"]))
                cit = self._layer_casing.get(name)
                if cit is not None:
                    cit.setPen(pg.mkPen(_qcolor(st["casing"], 210),
                                        width=max(1.0, st.get("casing_w", 3.0) * kg)))
            except Exception:
                pass

    def _render_builtup_raster(self, mask, extent):
        """Застройка растровой маской (общий вид). None — скрыть слой."""
        if mask is None or extent is None:
            self.builtup_img.setVisible(False)
            return
        m = np.asarray(mask, bool)
        rgba = np.zeros((m.shape[0], m.shape[1], 4), np.ubyte)
        col = _qcolor(LAYER_STYLE["built_up"]["color"])
        rgba[m, 0] = col.red(); rgba[m, 1] = col.green(); rgba[m, 2] = col.blue()
        rgba[m, 3] = 255
        self.builtup_img.setImage(rgba, autoLevels=False)
        x0, x1, y0, y1 = extent
        self.builtup_img.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
        self.builtup_img.setVisible(True)

    def render_threat(self, weight, extent, toggles):
        if not toggles["show_threat"] or weight is None:
            self.threat_img.setVisible(False)
            self._update_scale()                        # шкала перейдёт к тепловой карте
            return
        vmax = float(np.percentile(weight[weight > 0], 97)) if np.any(weight > 0) else 1.0
        vmax = max(vmax, 1e-6)
        disp = np.clip(weight / vmax, 0.0, 1.0)
        self.threat_img.setImage(disp, levels=(0, 1), lut=self._lut, autoLevels=False)
        x0, x1, y0, y1 = extent
        self.threat_img.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
        self.threat_img.setVisible(True)
        self._update_scale()

    def render_relief(self, dem, extent, toggles):
        """КАРТА ВЫСОТ: привычная топография (низины зелёные → вершины светлые) со
        светотенью. `dem` — высоты в метрах, `extent` — (x0,x1,y0,y1) в км.

        Шкала строится по ПЕРЦЕНТИЛЯМ, а не по метрам: на участке с размахом 23…365 м
        равномерная по высоте шкала слила бы всю обжитую полосу в один оттенок.
        Показ не зависит от того, добавлен ли рельеф в вес, — местность можно смотреть
        всегда."""
        if not toggles.get("show_relief") or dem is None or extent is None:
            self.relief_img.setVisible(False)
            return
        h = np.asarray(dem, float)
        ok = np.isfinite(h)
        if not ok.any():
            self.relief_img.setVisible(False)
            return
        lo, hi = np.percentile(h[ok], (2.0, 98.0))
        t = np.clip((h - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
        t = np.where(ok, t, 0.0)
        stops = np.array([s for s, _ in RELIEF_RAMP], float)
        cols = np.array([c for _, c in RELIEF_RAMP], float)
        rgb = np.empty(h.shape + (3,), float)
        for ch in range(3):                       # кусочно-линейная интерполяция шкалы
            rgb[..., ch] = np.interp(t, stops, cols[:, ch])
        rgb *= _hillshade(np.where(ok, h, np.nanmean(h[ok])))[..., None]
        rgba = np.zeros(h.shape + (4,), np.ubyte)
        rgba[..., :3] = np.clip(rgb, 0, 255).astype(np.ubyte)
        rgba[..., 3] = np.where(ok, 255, 0).astype(np.ubyte)
        self.relief_img.setImage(rgba, autoLevels=False)
        x0, x1, y0, y1 = extent
        self.relief_img.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
        self.relief_img.setVisible(True)

    def render_relief_priority(self, k, cut, extent, toggles):
        """ПРИОРИТЕТ ПО ВЫСОТЕ: что рельеф сделал с весом. Синим — укрытия (множитель > 1),
        красным — возвышенности (< 1), чёрным — места, где коридор снят как «очень высокая
        гора». Нейтральное (k ≈ 1) остаётся прозрачным, чтобы не мутить карту.

        `k` = None означает, что рельеф в вес не добавлен, — показывать нечего.

        Вместе со слоем появляется ЛЕГЕНДА у правого края: три цвета сами по себе ничего
        не объясняют (см. `_relief_legend_html`)."""
        if not toggles.get("show_relief_k") or k is None or extent is None:
            self.relief_k_img.setVisible(False)
            self.relief_legend.setVisible(False)
            return
        k = np.asarray(k, float)
        rgba = np.zeros(k.shape + (4,), np.ubyte)
        up = k > 1.0                              # укрытие: вес вырос
        dn = k < 1.0                              # возвышенность: вес упал
        span_up = max(float(np.nanmax(k)) - 1.0, 1e-6)
        span_dn = max(1.0 - float(np.nanmin(k)), 1e-6)
        a_up = np.clip((k - 1.0) / span_up, 0.0, 1.0) * RELIEF_MAX_ALPHA
        a_dn = np.clip((1.0 - k) / span_dn, 0.0, 1.0) * RELIEF_MAX_ALPHA
        for ch in range(3):
            rgba[up, ch] = RELIEF_HIDE_RGB[ch]
            rgba[dn, ch] = RELIEF_OPEN_RGB[ch]
        rgba[up, 3] = a_up[up].astype(np.ubyte)
        rgba[dn, 3] = a_dn[dn].astype(np.ubyte)
        if cut is not None:
            rgba[np.asarray(cut, bool)] = RELIEF_CUT_RGBA
        self.relief_k_img.setImage(rgba, autoLevels=False)
        x0, x1, y0, y1 = extent
        self.relief_k_img.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
        self.relief_k_img.setVisible(True)
        self.relief_legend.setVisible(True)
        self.reposition_legends()       # прижать к правому верхнему углу текущего вида

    def set_relief_button(self, active, enabled):
        """Состояние кнопки рельефа: подпись «Добавить/Убрать», подсветка и доступность
        (нет файла высот — нажимать нечего)."""
        self._relief_enabled = bool(enabled)
        self.btn_relief.setText("Убрать рельеф" if active else "Добавить рельеф")
        self.btn_relief.setEnabled(bool(enabled))
        self._tint(self.btn_relief, THEME["ok"] if active else THEME["muted"])
        if not enabled:
            self.btn_relief.setToolTip(
                "Нет файла высот. Положите geo_cache/dem.tif — см. "
                "теория/МЕТОДИЧКА_ЗАГРУЗКА_КАРТ.md §4.1")

    def render_exclusions(self, water_mask, urban_mask, extent, toggles):
        """Показать зоны-исключения: вода (синий — запрет датчика) и населённые пункты
        (красный — исключены из пролёта и обнулены в весе)."""
        if not toggles["show_water"] or (water_mask is None and urban_mask is None):
            self.water_img.setVisible(False); return
        shape = (water_mask if water_mask is not None else urban_mask).shape
        rgba = np.zeros(shape + (4,), np.ubyte)
        if urban_mask is not None:
            rgba[urban_mask] = THREAT_URBAN_RGBA       # красный — город (исключён), палитра
        if water_mask is not None:
            rgba[water_mask] = THREAT_WATER_RGBA       # синий — вода (запрет датчика), палитра
        self.water_img.setImage(rgba, autoLevels=False)
        x0, x1, y0, y1 = extent
        self.water_img.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
        self.water_img.setVisible(True)

    def render_routes(self, routes, route_area, extent, toggles):
        """ВСЕ возможные места пролёта: розовый ФОН = огибающая (route_area) — КОРРЕКТНО
        посчитанная ГРАФОМ достижимость A→ячейка→цель ≤ L_max (все места, куда может дойти
        БПЛА по коридорам, включая юг). Поверх — розовые ЛИНИИ-примеры маршрутов."""
        show = toggles["show_routes"]
        if show and route_area is not None and np.asarray(route_area).any():
            ra = np.asarray(route_area)
            rgba = np.zeros(ra.shape + (4,), np.ubyte)
            rgba[ra] = THREAT_COLORS["envelope"]           # розовый фон — все достижимые места (палитра)
            self.route_area_img.setImage(rgba, autoLevels=False)
            x0, x1, y0, y1 = extent
            self.route_area_img.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
            self.route_area_img.setVisible(True)
        else:
            self.route_area_img.setVisible(False)
        # линии-примеры маршрутов поверх фона
        if show and routes:
            nan = np.array([np.nan])
            xs, ys = [], []
            for r in routes:
                r = np.asarray(r, float)
                xs.append(r[:, 0]); xs.append(nan)
                ys.append(r[:, 1]); ys.append(nan)
            self.route_item.setData(np.concatenate(xs), np.concatenate(ys), connect="finite")
            self.route_item.setVisible(True)
        else:
            self.route_item.setData([], []); self.route_item.setVisible(False)

    def render_iter_routes(self, routes, toggles):
        """Показать НАКОПЛЕННЫЕ итерационные маршруты (статично). Пошаговую анимацию полёта
        ведёт контроллер через iter_setup_flight/iter_update_flight."""
        show = bool(toggles.get("show_iter")) and bool(routes)
        self.iter_show_accumulated(routes if show else [])

    def render_generalized(self, routes, toggles):
        """ОБОБЩЁННАЯ выборка (~10 % итераций): частые, но распределённые маршруты —
        отдельный ярко-лаймовый слой поверх веера. routes уже отобраны моделью."""
        show = bool(toggles.get("show_iter_gen")) and bool(routes)
        if not show:
            self.route_gen_item.setData([], []); self.route_gen_item.setVisible(False)
            return
        nan = np.array([np.nan]); xs, ys = [], []
        for r in routes:
            r = np.asarray(r, float)
            xs.append(r[:, 0]); xs.append(nan)
            ys.append(r[:, 1]); ys.append(nan)
        self.route_gen_item.setData(np.concatenate(xs), np.concatenate(ys), connect="finite")
        self.route_gen_item.setVisible(True)

    def render_iter_heat(self, density, extent, toggles):
        """2-я тепловая карта — частота пролёта БПЛА (плотность маршрутов), LUT magma."""
        if not toggles.get("show_iter_heat") or density is None:
            self.iter_heat_img.setVisible(False)
            self._update_scale()                        # шкала вернётся к весовой карте
            return
        d = np.asarray(density, float)
        ds = np.sqrt(np.clip(d, 0.0, 1.0))              # √ поднимает редкие пролёты (виднее)
        lut = _iter_heat_lut()
        rgba = lut[np.clip((ds * 255).astype(int), 0, 255)].copy()
        rgba[..., 3] = np.where(d > 0.005, 220, 0).astype(np.ubyte)  # прозрачно, где не летали
        self.iter_heat_img.setImage(rgba, autoLevels=False)
        x0, x1, y0, y1 = extent
        self.iter_heat_img.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
        self.iter_heat_img.setVisible(True)
        self._update_scale()

    def iter_show_accumulated(self, routes):
        """Отрисовать все накопленные маршруты тонкими линиями (одним item, через NaN)."""
        if not routes:
            self.route_iter_item.setData([], []); self.route_iter_item.setVisible(False)
            return
        nan = np.array([np.nan]); xs, ys = [], []
        for r in routes:
            r = np.asarray(r, float)
            xs.append(r[:, 0]); xs.append(nan)
            ys.append(r[:, 1]); ys.append(nan)
        self.route_iter_item.setData(np.concatenate(xs), np.concatenate(ys), connect="finite")
        self.route_iter_item.setVisible(True)

    def iter_setup_flight(self, route):
        """Начать анимацию полёта по маршруту: текущий путь и маркер БПЛА — в старте."""
        r = np.asarray(route, float)
        self.route_cur_item.setData([r[0, 0]], [r[0, 1]]); self.route_cur_item.setVisible(True)
        self.uav_marker.setData([r[0, 0]], [r[0, 1]]); self.uav_marker.setVisible(True)

    def iter_update_flight(self, route, j):
        """Прорисовать текущий маршрут до точки j и передвинуть маркер БПЛА в неё."""
        r = np.asarray(route, float); j = int(max(0, min(j, len(r) - 1)))
        self.route_cur_item.setData(r[:j + 1, 0], r[:j + 1, 1])
        self.uav_marker.setData([r[j, 0]], [r[j, 1]])

    def iter_clear_current(self):
        """Убрать текущий (летящий) маршрут и маркер БПЛА (накопленные остаются)."""
        self.route_cur_item.setData([], []); self.route_cur_item.setVisible(False)
        self.uav_marker.setData([], []); self.uav_marker.setVisible(False)

    def get_iter_T(self):
        """Число итераций (T) из поля ввода; при ошибке — прежнее из params."""
        try:
            return max(1, int(float(self.ed_iter_T.text().strip().replace(",", "."))))
        except (ValueError, AttributeError):
            return int(getattr(self._params_ref, "threat_iter_routes", 150))

    def set_iter_running(self, running):
        """Текст кнопки «Пуск»/«Пауза» (как во вкладке 2)."""
        self.btn_iter_play.setText("⏸ Пауза" if running else "▶ Пуск")

    def render_crossings(self, crossings, toggles):
        """Пересечения (перекрёстки дорог/рек/ЛЭП) — узлы развилок. Скрыто по умолчанию."""
        if not toggles.get("show_cross") or crossings is None or len(crossings) == 0:
            self.crossing_scatter.setVisible(False); return
        c = np.asarray(crossings, float)
        self.crossing_scatter.setData(c[:, 0], c[:, 1])
        self.crossing_scatter.setVisible(True)

    def render_candidates(self, cand, toggles):
        if not toggles["show_cand"] or cand is None or len(cand) == 0:
            self.cand_scatter.setVisible(False); return
        self.cand_scatter.setData(cand[:, 0], cand[:, 1])
        self.cand_scatter.setVisible(True)

    SENSOR_COLOR = THREAT_COLORS["sensor"]   # цвет датчиков — см. палитру THREAT_COLORS

    def render_sensors(self, sensors, R):
        """Нарисовать датчики: у каждого — КОЛЬЦО зоны обзора радиуса R (залитый круг) и
        яркая точка-центр. Старые убираем и создаём заново (число меняется). Стоят поверх
        всех слоёв (Z=20/21), чтобы маршруты их не перекрывали. Цвет — THREAT_COLORS['sensor']."""
        for it in self._sensor_items:
            self.pi.removeItem(it)
        self._sensor_items.clear()
        for i, s in enumerate(sensors, 1):
            e = QtWidgets.QGraphicsEllipseItem(s[0] - R, s[1] - R, 2 * R, 2 * R)
            e.setPen(pg.mkPen(_qcolor(self.SENSOR_COLOR, 245), width=2.2))
            e.setBrush(pg.mkBrush(_qcolor(self.SENSOR_COLOR, 70)))   # залитая зона обзора
            e.setZValue(20)                                          # НАД всем: маршруты не перекрывают
            self.pi.addItem(e, ignoreBounds=True)
            self._sensor_items.append(e)
        if len(sensors):
            dots = pg.ScatterPlotItem(
                [p[0] for p in sensors], [p[1] for p in sensors], size=15, symbol="o",
                brush=pg.mkBrush(_qcolor(self.SENSOR_COLOR)), pen=pg.mkPen("white", width=1.6))
            dots.setZValue(21); self.pi.addItem(dots)               # центры — поверх колец
            self._sensor_items.append(dots)

    def render_entry_target(self, entry, target):
        self.entry_scatter.setData([entry[0]], [entry[1]])
        self.target_scatter.setData([target[0]], [target[1]])

    def showEvent(self, e):
        QtWidgets.QWidget.showEvent(self, e)
        if not self._framed:
            self.frame_bbox()
            self._framed = True
        self._refresh_basemap(force=True)


class VectorLayersDialog(QtWidgets.QDialog):
    """Окно «Векторные слои»: какие именно ориентиры показывать на карте.

    ЧЕМ ОТЛИЧАЕТСЯ ОТ «ВЫБОРА ЦИФРОВЫХ КАРТ». Там решается, какие слои НАКЛАДЫВАТЬ НА
    СЕТКУ, то есть какие войдут в вес ячеек — смена набора требует пересборки карты и
    сбрасывает маршруты. Здесь — только ПОКАЗ: веса, маршруты и датчики не меняются
    вовсе, скрытие слоя ничего не пересчитывает. Поэтому окно не модальное: переключил —
    и сразу видно на карте.

    Выбор хранится в представлении (`_vec_visible`), а не в окне, поэтому окно можно
    закрыть и открыть — набор останется. Верхний переключатель — тот же, что чекбокс
    «Векторные слои» в панели: оба показывают одно состояние."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.setWindowTitle("Векторные слои — что показывать")
        self.setMinimumWidth(300)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Tool)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setSpacing(6)

        self.chk_all = QtWidgets.QCheckBox("Показывать векторные слои")
        self.chk_all.setToolTip("Тот же переключатель, что в панели: гасит все слои разом, "
                                "не сбрасывая выбор ниже.")
        self.chk_all.toggled.connect(self._master_toggled)
        lay.addWidget(self.chk_all)

        note = QtWidgets.QLabel("Влияет только на показ — веса, маршруты и датчики "
                                "не пересчитываются.")
        note.setWordWrap(True); note.setStyleSheet(f"color: {THEME['muted']}; font-size: 10px;")
        lay.addWidget(note)

        self._checks = {}
        for name in THREAT_LAYER_ORDER:
            style = LAYER_STYLE.get(name)
            if style is None:
                continue
            row = QtWidgets.QHBoxLayout(); row.setSpacing(8)
            swatch = QtWidgets.QLabel()                  # образец цвета — как на карте
            swatch.setFixedSize(18, 4)
            swatch.setStyleSheet(f"background: {style['color']}; border-radius: 2px;")
            spec = THREAT_LAYERS.get(name, {})
            chk = QtWidgets.QCheckBox(spec.get("label", name))
            chk.setToolTip("Вес ячейки: %+g" % spec["weight"] if "weight" in spec else name)
            chk.toggled.connect(lambda on, n=name: self.view.set_vector_visible(n, on))
            self._checks[name] = chk
            row.addWidget(swatch); row.addWidget(chk, 1)
            lay.addLayout(row)

        btns = QtWidgets.QHBoxLayout()
        b_all = QtWidgets.QPushButton("Показать все")
        b_none = QtWidgets.QPushButton("Скрыть все")
        b_all.clicked.connect(lambda: self._set_all(True))
        b_none.clicked.connect(lambda: self._set_all(False))
        btns.addWidget(b_all); btns.addWidget(b_none)
        lay.addLayout(btns)

        close = QtWidgets.QPushButton("Закрыть")
        close.setToolTip("Выбор сохраняется — окно можно открыть снова.")
        close.clicked.connect(self.close)
        lay.addWidget(close)

    def sync(self):
        """Подтянуть галки из представления (окно открыли повторно или сменили общий
        чекбокс в панели)."""
        vis = self.view.vector_visible()
        for name, chk in self._checks.items():
            chk.blockSignals(True)
            chk.setChecked(bool(vis.get(name, True)))
            chk.blockSignals(False)
        self.chk_all.blockSignals(True)
        self.chk_all.setChecked(self.view.chk_layers.isChecked())
        self.chk_all.blockSignals(False)
        self._update_enabled()

    def _master_toggled(self, on):
        self.view.chk_layers.setChecked(bool(on))        # панель и окно — одно состояние
        self._update_enabled()

    def _update_enabled(self):
        on = self.chk_all.isChecked()
        for chk in self._checks.values():
            chk.setEnabled(on)

    def _set_all(self, on):
        for name, chk in self._checks.items():
            chk.blockSignals(True)
            chk.setChecked(on)
            chk.blockSignals(False)
            self.view._vec_visible[name] = bool(on)
        self.view.apply_vector_visibility()              # одна перерисовка на все слои


class DigitalMapsDialog(QtWidgets.QDialog):
    """Отдельное окно: выбор ИСТОЧНИКА цифровых карт и набора накладываемых СЛОЁВ.

    Источник: файл .npz (готовые слои) / .osm.pbf (сырой OSM-экстракт) либо «авто»
    (кэш geo_cache/ → иначе демо-схема). Слои: чекбоксы по THREAT_LAYERS с их весами.
    Возвращает выбор через result_choices(); саму загрузку делает контроллер."""

    def __init__(self, parent, data_path, enabled_layers):
        super().__init__(parent)
        self.setWindowTitle("Выбор цифровых карт (слоёв) для наложения")
        self.setMinimumWidth(440)
        self._data_path = data_path
        lay = QtWidgets.QVBoxLayout(self)

        lay.addWidget(QtWidgets.QLabel("<b>1. Источник данных</b>"))
        self.src_lbl = QtWidgets.QLabel(self._src_text())
        self.src_lbl.setWordWrap(True)
        self.src_lbl.setStyleSheet(f"color: {THEME['muted']};")
        lay.addWidget(self.src_lbl)
        row = QtWidgets.QHBoxLayout()
        btn_file = QtWidgets.QPushButton("Загрузить файл (.npz / .osm.pbf)…")
        btn_file.clicked.connect(self._pick_file)
        btn_auto = QtWidgets.QPushButton("Авто (кэш → демо)")
        btn_auto.clicked.connect(self._use_auto)
        row.addWidget(btn_file); row.addWidget(btn_auto)
        lay.addLayout(row)
        hint = QtWidgets.QLabel(
            "Где взять .osm.pbf — см. теория/МЕТОДИЧКА_ЗАГРУЗКА_КАРТ.md "
            "(BBBike/Geofabrik). .npz готовит tools/build_threat_grid.py.")
        hint.setWordWrap(True); hint.setStyleSheet(f"color: {THEME['muted']};")
        lay.addWidget(hint)

        lay.addWidget(QtWidgets.QLabel("<b>2. Слои для наложения</b> (вес ячейки)"))
        self._checks = {}
        for name in THREAT_LAYER_ORDER:
            spec = THREAT_LAYERS.get(name)
            if not spec:
                continue
            role = "репеллер" if not spec.get("attractor", True) else "аттрактор"
            chk = QtWidgets.QCheckBox(f"{spec['label']}  ·  вес {spec['weight']:+g}  ·  {role}")
            chk.setChecked(enabled_layers is None or name in enabled_layers)
            self._checks[name] = chk
            lay.addWidget(chk)

        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _src_text(self):
        return (f"файл: {self._data_path}" if self._data_path
                else "авто: geo_cache/threat_layers.npz → иначе демо-схема")

    def _pick_file(self):
        fn, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Файл цифровых карт", "",
            "Гео-данные (*.npz *.pbf *.osm);;Все файлы (*)")
        if fn:
            self._data_path = fn
            self.src_lbl.setText(self._src_text())

    def _use_auto(self):
        self._data_path = None
        self.src_lbl.setText(self._src_text())

    def result_choices(self):
        """(data_path|None, enabled_layers_set). Все галочки сняты -> пустой набор
        (карта без слоёв); чтобы «все слои» — просто отметить все."""
        enabled = {n for n, c in self._checks.items() if c.isChecked()}
        return self._data_path, enabled


class InputDataDialog(QtWidgets.QDialog):
    """НЕмодальное окно «Входные данные» маршрута: запас хода, скорость, крен, длина прямого
    участка, РАЗРЫВ между весовыми секторами. |AB| считается между входом и целью (только
    показ). Enter в любом поле СРАЗУ применяет к основной карте (не блокирует программу —
    окно остаётся открытым).
    """
    FIELDS = [
        ("threat_L_max", "Запас хода L_max, км"),
        ("threat_speed_kmh", "Скорость, км/ч"),
        ("threat_bank_deg", "Крен, °"),
        ("threat_turn_interval_km", "Длина прямого участка, км"),
        ("threat_max_gap_km", "Разрыв между секторами, км"),
        ("threat_corridor_slack_km", "Уход от ориентира, км"),
    ]
    # Подсказки к полям (иначе смысл «разрыва» неочевиден).
    HINTS = {
        "threat_max_gap_km":
            "Пустое место между весовыми секторами: если оно КОРОЧЕ этого значения — БПЛА "
            "его перелетит, и коридоры сшиваются в один (маршрут возможен). Длиннее — "
            "коридор разорван, маршрута через него нет.\nМеняет и ОБЛАСТЬ ЗАЛЁТА, и набор "
            "возможных маршрутов — ещё до итераций.",
        "threat_corridor_slack_km":
            "Насколько маршрут отклоняется ВБОК от реки или дороги, вдоль которой идёт.\n"
            "Это НЕ то же, что разрыв: разрыв — про перелёт через пустоту МЕЖДУ коридорами, "
            "а это — про свободу манёвра вдоль коридора. Раньше обе величины задавались "
            "одним параметром, и дать маршруту место для обхода холма можно было, только "
            "увеличив перелёты через пустоту.\nБольше значение — шире полоса возможного "
            "манёвра и охотнее обход препятствий, но маршруты дальше уходят от ориентиров.",
        "threat_turn_interval_km":
            "Целевая длина ПРЯМОГО участка: маршрут держит курс примерно столько км, "
            "потом доворачивает.",
        "threat_L_max":
            "Главное ограничение маршрута: полная длина пути вход→цель не может его превысить.",
    }

    def __init__(self, parent, params, on_apply):
        super().__init__(parent)
        self.setWindowTitle("Входные данные маршрута")
        self.setModal(False)                      # НЕ блокирует основное окно
        self._on_apply = on_apply
        self._params = params
        self._edits = {}
        lay = QtWidgets.QFormLayout(self)
        self.ab_lbl = QtWidgets.QLabel("—")
        lay.addRow("|AB| (вход→цель), км:", self.ab_lbl)
        # запас хода: авто = |AB| + 25 % (галочка) либо руками (снять галочку)
        self.chk_auto = QtWidgets.QCheckBox("авто: |AB| + 25 %")
        self.chk_auto.setChecked(not getattr(params, "threat_L_max_manual", False))
        self.chk_auto.setToolTip("Запас хода = расстояние вход→цель +25 % (не меньше "
                                 "кратчайшего коридора). Снимите галочку — задать L_max руками.")
        self.chk_auto.stateChanged.connect(self._auto_changed)
        lay.addRow(self.chk_auto)
        for name, label in self.FIELDS:
            e = QtWidgets.QLineEdit(str(getattr(params, name)))
            e.returnPressed.connect(self._apply)
            if name in self.HINTS:
                e.setToolTip(self.HINTS[name])
            self._edits[name] = e
            lay.addRow(label, e)
        self._auto_changed()                       # выставить доступность поля L_max
        row = QtWidgets.QHBoxLayout()
        btn = QtWidgets.QPushButton("Применить (Enter)")
        btn.clicked.connect(self._apply)
        row.addWidget(btn)
        lay.addRow(row)
        hint = QtWidgets.QLabel("Enter в поле — сразу применяется к карте. Окно можно "
                                "держать открытым.")
        hint.setWordWrap(True); hint.setStyleSheet(f"color:{THEME['muted']};")
        lay.addRow(hint)

    def refresh(self, params):
        self._params = params
        self.chk_auto.blockSignals(True)
        self.chk_auto.setChecked(not getattr(params, "threat_L_max_manual", False))
        self.chk_auto.blockSignals(False)
        for name, e in self._edits.items():
            e.setText(f"{float(getattr(params, name)):g}")
        self._auto_changed()

    def set_ab(self, km):
        self.ab_lbl.setText(f"{km:.0f}")

    def _auto_changed(self, *_):
        """Авто-запас: поле L_max ВСЕГДА показывает текущее значение (и при снятии галочки
        оно не пропадает — просто становится редактируемым). В авто — только для чтения.

        Цвета БЕРЁМ НЕ ИЗ ТЁМНОЙ ТЕМЫ: это окно — обычный системный диалог со светлым фоном
        (THEME задана только на карте). Раньше редактируемое поле красилось в THEME['text']
        (#e6edf3) — почти белым по белому, и значение было не видно."""
        auto = self.chk_auto.isChecked()
        e = self._edits.get("threat_L_max")
        if e is None:
            return
        e.setText(f"{float(getattr(self._params, 'threat_L_max', 0.0)):g}")  # всегда заполнено
        e.setReadOnly(auto)
        e.setStyleSheet("color:#6b7785; background:#f0f0f0;" if auto   # авто: серым, «только чтение»
                        else "")                                       # руками: обычное поле

    def _apply(self):
        """Применить поля. Нечисловое поле берётся из текущих параметров (не роняем ввод).
        В авто-режиме L_max не передаём — его считает модель (|AB|+25 %)."""
        vals = {}
        for name, e in self._edits.items():
            txt = e.text().strip().replace(",", ".")
            try:
                vals[name] = float(txt)
            except ValueError:
                vals[name] = float(getattr(self._params, name))
        auto = self.chk_auto.isChecked()
        vals["threat_L_max_manual"] = (not auto)
        if auto:
            vals.pop("threat_L_max", None)
        self._on_apply(vals)
