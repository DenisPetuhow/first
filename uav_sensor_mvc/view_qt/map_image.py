# -*- coding: utf-8 -*-
"""
VIEW (Qt) · СВОЯ КАРТИНКА КАРТЫ поверх слоёв (план 8, задача 8.5).

ЗАЧЕМ. Заказчик приносит готовую карту местности картинкой (скан топокарты, экспорт из
другой программы, снимок) и хочет работать по ней: подписи и обозначения — с картинки,
датчики и маршруты — поверх неё, а масштаб и координаты — как на обычной карте.

КАК ЭТО РАБОТАЕТ. Картинке задают координаты углов, они переводятся в километры того же
локального фрейма, в котором живёт вся вкладка 3, и картинка кладётся на сцену через
`ImageItem.setRect` — ровно тем же приёмом, что и тайловая подложка
(`basemap_mixin._on_basemap_ready`). Никакой отдельной системы координат не заводится:
поэтому датчик, поставленный «на картинке», имеет те же километры и те же широту с
долготой, что и без неё.

⚠️ ЧЕГО КАРТИНКА НЕ ДЕЛАЕТ. Она не даёт данных для расчёта: веса по-прежнему считаются
по векторным слоям и рельефу. Картинка — это подложка для глаз, а не источник геометрии.

⚠️ ПРИВЯЗКА ТОЛЬКО ПРЯМОУГОЛЬНАЯ. Углы задают прямоугольник по параллелям и меридианам.
Если у карты есть поворот (снимок под углом, скан с рамкой), внутри останется сдвиг —
той же природы, что расхождение проекций в плане 8 §0.4. Для листа масштаба 1:50 000
(≈20 км) он мал; для листа 1:1 000 000 — километры.

Связанные документы: [план 8, задача 8.5](../теория/планы/8_ПЛАН_КАРТА_И_ИНТЕРФЕЙС.md),
раскладка данных участка — ОГРАНИЧЕНИЯ 5.1б.
"""
import os
import re

import numpy as np
from pyqtgraph.Qt import QtCore, QtWidgets
import pyqtgraph as pg

from . import ui_state

# ПАПКА ДЛЯ КАРТИНОК КАРТ — внутри кэша геоданных, по правилу «данные участка лежат в
# своей папке» (ОГРАНИЧЕНИЯ 5.1б). Диалог выбора открывается сразу здесь.
IMAGE_DIR_NAME = "карты_картинки"

# Предельный размер по длинной стороне. Скан 10 000×10 000 в RGBA — это 400 МБ в памяти
# и заметные тормоза при каждом перерисовывании; прореживаем, как это уже делает
# `threat_grid.load_dem_display` для рельефа.
MAX_PX = 4000

_EXTS = (".png", ".jpg", ".jpeg")

# имя вида map_39.8794_62.5901_41.7516_63.0869.png -> координаты берутся из имени
_NAME_COORDS = re.compile(
    r"(-?\d+\.\d+)[_,\s]+(-?\d+\.\d+)[_,\s]+(-?\d+\.\d+)[_,\s]+(-?\d+\.\d+)")


def images_dir(geo_root):
    """Папка картинок карт; создаётся при первом обращении."""
    d = os.path.join(geo_root, IMAGE_DIR_NAME)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def coords_from_name(path):
    """Координаты из имени файла (lon_min, lat_min, lon_max, lat_max) либо None.

    Удобно, когда картинку экспортировали с известной рамкой: не нужно вбивать
    четыре числа руками. Порядок — как в `config.THREAT_AREAS[...]['bbox']`."""
    m = _NAME_COORDS.search(os.path.basename(path))
    if not m:
        return None
    a, b, c, d = (float(v) for v in m.groups())
    lo, ho = min(a, c), max(a, c)
    la, ha = min(b, d), max(b, d)
    if not (-180.0 <= lo < ho <= 180.0 and -85.0 <= la < ha <= 85.0):
        return None
    return lo, la, ho, ha


def load_image_rgba(path, max_px=MAX_PX):
    """Картинка -> (RGBA uint8 (H, W, 4), (исходная ширина, высота)).

    Строка 0 массива — ЮГ: сцена вкладки идёт осью Y вверх, и `setRect` кладёт первую
    строку внизу. Тайловая подложка переворачивается ровно так же
    (`geomap.build_raster_basemap`: `canvas = canvas[::-1]`)."""
    from PIL import Image

    with Image.open(path) as im:
        src_w, src_h = im.size
        k = max(src_w, src_h) / float(max_px)
        if k > 1.0:                       # прореживаем большой скан
            im = im.resize((max(1, int(src_w / k)), max(1, int(src_h / k))),
                           Image.LANCZOS)
        arr = np.asarray(im.convert("RGBA"), dtype=np.uint8)
    return arr[::-1].copy(), (src_w, src_h)


class MapImageDialog(QtWidgets.QDialog):
    """НЕмодальное окно «Своя карта»: файл, координаты углов, применение.

    Не блокирует программу — как и остальные окна вкладки 3 (`InputDataDialog`,
    `SensorsDialog`): координаты углов сверяют, глядя на саму карту."""

    STATE_KEY = "map_image"          # раздел в geo_cache/ui_state.json

    def __init__(self, parent, start_dir, on_apply, on_clear, current=None):
        super().__init__(parent)
        self.setWindowTitle("Своя карта (картинкой)")
        self.setModal(False)
        self._dir = start_dir
        self._on_apply = on_apply
        self._on_clear = on_clear
        # ПАМЯТЬ ПОЛЕЙ (правило заказчика 04.09.2026): если в этот раз ничего не
        # показано, подставляем то, что вводили в прошлый запуск.
        if not current:
            current = ui_state.get(self.STATE_KEY) or None

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel(
            "Картинка ложится ПОВЕРХ векторных слоёв и рельефа.\n"
            "Датчики, маршруты и подписи остаются сверху.\n"
            "Веса по-прежнему считаются по слоям — картинка только для глаз."))

        row = QtWidgets.QHBoxLayout()
        self.ed_path = QtWidgets.QLineEdit(current.get("path", "") if current else "")
        self.ed_path.setPlaceholderText("файл .png / .jpg")
        btn_pick = QtWidgets.QPushButton("Выбрать…")
        btn_pick.clicked.connect(self._pick)
        row.addWidget(self.ed_path, 1); row.addWidget(btn_pick)
        lay.addLayout(row)

        grid = QtWidgets.QGridLayout()
        grid.addWidget(QtWidgets.QLabel("Координаты краёв картинки, градусы:"), 0, 0, 1, 4)
        self.ed = {}
        # ⚠️ ПОДПИСИ ПО КРАЯМ СВЕТА, а не «СЗ/ЮВ». С углами путаница неизбежна: у
        # северо-западного угла широта БОЛЬШАЯ, а долгота МАЛАЯ, и числа вводят
        # крест-накрест. «Западный край» перепутать не с чем.
        specs = [("Долгота, ЗАПАДНЫЙ край", "lon_min", 1, 0),
                 ("Долгота, ВОСТОЧНЫЙ край", "lon_max", 1, 2),
                 ("Широта, ЮЖНЫЙ край (низ)", "lat_min", 2, 0),
                 ("Широта, СЕВЕРНЫЙ край (верх)", "lat_max", 2, 2)]
        for label, key, r, c in specs:
            grid.addWidget(QtWidgets.QLabel(label), r, c)
            e = QtWidgets.QLineEdit()
            e.setMaximumWidth(110)
            grid.addWidget(e, r, c + 1)
            self.ed[key] = e
        lay.addLayout(grid)

        # ДВА РЕЖИМА РАБОТЫ, и галочка переключает не только тайлы:
        #   снята — картинка лежит внутри большой области, вокруг видны тайлы, ходить
        #           можно и за её пределы;
        #   стоит — работаем только по этой карте: её край = граница перемещения, вид
        #           сразу вписывается в неё по размеру окна.
        self.chk_only = QtWidgets.QCheckBox(
            "только эта карта: скрыть тайлы, границы и зум — по её краям")
        self.chk_only.setChecked(bool(current.get("hide_tiles")) if current else False)
        lay.addWidget(self.chk_only)

        self.lbl_info = QtWidgets.QLabel("")
        self.lbl_info.setWordWrap(True)
        lay.addWidget(self.lbl_info)

        # ⚠️ РАЙОН НАЗНАЧАЕТСЯ АВТОМАТИЧЕСКИ — галочки для этого нет (правило заказчика
        # 04.09.2026): «при загрузке картинки район задаётся в пределах картинки, но его
        # можно поменять». Загрузка своей карты и есть второй способ задать район, наравне
        # с рисованием мышью; после этого район свободно переопределяется кнопкой на панели.
        lay.addWidget(QtWidgets.QLabel(
            "Рамка карты станет РАЙОНОМ МОДЕЛИРОВАНИЯ.\n"
            "Потом его можно изменить кнопкой «Задать район моделирования»."))

        btns = QtWidgets.QHBoxLayout()
        b_apply = QtWidgets.QPushButton("Показать карту")
        b_apply.clicked.connect(self._apply)
        b_clear = QtWidgets.QPushButton("Убрать")
        b_clear.clicked.connect(self._clear)
        b_close = QtWidgets.QPushButton("Закрыть")
        b_close.clicked.connect(self.close)
        btns.addWidget(b_apply, 2); btns.addWidget(b_clear, 1); btns.addWidget(b_close, 1)
        lay.addLayout(btns)

        if current:
            for k in ("lon_min", "lat_min", "lon_max", "lat_max"):
                if current.get(k) is not None:
                    self.ed[k].setText("%.6f" % current[k])

    # ------------------------------------------------------------------
    def _pick(self):
        fn, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Картинка карты местности", self._dir,
            "Картинки (*.png *.jpg *.jpeg);;Все файлы (*)")
        if not fn:
            return
        self.ed_path.setText(fn)
        got = coords_from_name(fn)
        if got:                              # координаты прямо в имени файла
            lo, la, ho, ha = got
            self.ed["lon_min"].setText("%.6f" % lo)
            self.ed["lat_min"].setText("%.6f" % la)
            self.ed["lon_max"].setText("%.6f" % ho)
            self.ed["lat_max"].setText("%.6f" % ha)
            self.lbl_info.setText("Координаты взяты из имени файла — сверьте по карте.")

    def _values(self):
        """Числа из полей либо None, если что-то не заполнено или не число."""
        out = {}
        for k, e in self.ed.items():
            try:
                out[k] = float(e.text().replace(",", ".").strip())
            except ValueError:
                return None
        return out

    def _apply(self):
        path = self.ed_path.text().strip()
        if not path or not os.path.exists(path):
            self.lbl_info.setText("⚠️ Файл не найден — укажите путь к картинке.")
            return
        v = self._values()
        if v is None:
            self.lbl_info.setText("⚠️ Заполните все четыре координаты числами.")
            return
        # ПОРЯДОК ПОПРАВЛЯЕМ САМИ, а не ругаемся. Раньше здесь стояла проверка «левая
        # долгота должна быть меньше правой», и она отвергала ввод, хотя все четыре числа
        # верные — просто разложены крест-накрест. Программа знает, где запад, а где
        # восток: это min и max, и вычислить их проще, чем объяснять.
        lon_min, lon_max = sorted((v["lon_min"], v["lon_max"]))
        lat_min, lat_max = sorted((v["lat_min"], v["lat_max"]))
        swapped = (lon_min, lon_max, lat_min, lat_max) != (
            v["lon_min"], v["lon_max"], v["lat_min"], v["lat_max"])
        if lon_min == lon_max or lat_min == lat_max:
            self.lbl_info.setText("⚠️ Края совпали: у картинки нулевая ширина или высота. "
                                  "Проверьте, что долготы и широты — разные числа.")
            return
        if swapped:                       # показать, как поняли, чтобы это было видно
            for k, val in (("lon_min", lon_min), ("lon_max", lon_max),
                           ("lat_min", lat_min), ("lat_max", lat_max)):
                self.ed[k].setText("%.6f" % val)
        # ЗАПОМИНАЕМ ДО показа: даже если картинка не прочитается, введённое не пропадёт
        ui_state.save(self.STATE_KEY, dict(
            path=path, lon_min=lon_min, lat_min=lat_min, lon_max=lon_max,
            lat_max=lat_max, hide_tiles=bool(self.chk_only.isChecked())))
        msg = self._on_apply(path, lon_min, lat_min, lon_max, lat_max,
                             self.chk_only.isChecked(), True)
        if swapped:
            msg = "Порядок краёв поправлен автоматически. " + (msg or "")
        self.lbl_info.setText(msg or "Карта показана.")

    def _clear(self):
        self._on_clear()
        self.lbl_info.setText("Картинка убрана — вернулась обычная подложка.")


def make_image_item():
    """Элемент сцены для своей карты.

    Z = −19: САМЫЙ НИЖНИЙ слой данных — сразу над тайловой подложкой (−20) и под всем
    остальным (рельеф −9, веса −8, вода −7, застройка −5.5, векторные слои около −5…−3,
    зоны 0.4, маршруты 1…5, датчики и подписи выше).

    ⚠️ СНАЧАЛА БЫЛО Z = −1 — «поверх векторных слоёв и рельефа», как звучало первое
    задание. На деле картинка закрыла собой всё, кроме датчиков и маршрутов: на карте
    остались её собственные дороги и подписи, а наши слои и весовая карта пропали
    (снимок заказчика 04.09.2026). Правильное место — под всеми слоями: картинка
    заменяет собой ФОН, а расчётные слои ложатся на неё."""
    item = pg.ImageItem()
    item.setOpts(axisOrder="row-major")
    item.setZValue(-19.0)
    item.setVisible(False)
    return item


def place_image(item, rgba, bbox_km):
    """Положить картинку в километровый прямоугольник (kx0, kx1, ky0, ky1)."""
    kx0, kx1, ky0, ky1 = bbox_km
    item.setImage(rgba, autoLevels=False)
    item.setRect(QtCore.QRectF(kx0, ky0, kx1 - kx0, ky1 - ky0))
    item.setVisible(True)
