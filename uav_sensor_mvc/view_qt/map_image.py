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
import io
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

# ⚠️ ПОТОЛОК ЧИСЛА ПИКСЕЛЕЙ — 400 млн (примерно 20 000 × 20 000).
#
# Зачем он вообще нужен. Pillow сам отказывается открывать картинки больше ~179 млн
# пикселей: он не знает, кто прислал файл, и защищается от «архивной бомбы» — крошечного
# файла, который при распаковке съедает всю память. Ошибка выглядит так:
# «Image size (237219680 pixels) exceeds limit of 178956970 pixels, could be
# decompression bomb DOS attack» — ровно это заказчик и получил 05.09.2026, выгрузив
# карту из CorelDRAW.
#
# Почему поднимаем, но НЕ снимаем совсем (`MAX_IMAGE_PIXELS = None`). Карту приносит сам
# пользователь, а не сеть, так что «бомбы» тут ждать неоткуда — но за защитой стоит и
# настоящая беда: 237 млн пикселей в RGBA это ≈950 МБ, и программа может просто упасть
# по памяти. Поэтому предел остаётся, только осмысленный: до него картинка читается
# УМЕНЬШЕННОЙ (см. `load_image_rgba`), а выше — человеку честно говорится, что файл
# нужно пересохранить помельче.
MAX_PIXELS_ALLOWED = 400_000_000

_EXTS = (".png", ".jpg", ".jpeg")

# имя вида map_39.8794_62.5901_41.7516_63.0869.png -> координаты берутся из имени
_NAME_COORDS = re.compile(
    r"(-?\d+\.\d+)[_,\s]+(-?\d+\.\d+)[_,\s]+(-?\d+\.\d+)[_,\s]+(-?\d+\.\d+)")


# ══════════════════════════════════════════════════════════════════════════════
# ДЕТАЛИЗАЦИЯ ПО ВИДИМОЙ ОБЛАСТИ (05.09.2026)
#
# ЗАЧЕМ. Карта заказчика — 17 944 × 13 220 (237 млн пикселей) на район 156 км, то есть
# 8.7 м на пиксель. Показывали её ужатой до 4 000 px — 39 м на пиксель, вчетверо грубее
# оригинала: подписи топокарты высотой 20 px превращались в 4.5 px и переставали
# читаться (снимки заказчика 05.09.2026 — в «фото пример/»).
#
# ПОЧЕМУ НЕЛЬЗЯ ПРОСТО ПОКАЗАТЬ ЦЕЛИКОМ. Замер на этом файле: 8 000 px — 189 МБ,
# 12 000 — 424 МБ, 16 000 (≈оригинал) — 754 МБ, и столько же ещё держит Qt под свою
# копию. Полтора гигабайта ради картинки, которая на экран целиком всё равно не влезает.
#
# КАК СДЕЛАНО. Экран показывает 2–3 тысячи пикселей, поэтому в полном разрешении нужен
# только ВИДИМЫЙ КУСОК. Картинка один раз разворачивается в несжатый файл рядом с ней, и
# дальше нужный кусок читается прямо с диска через `numpy.memmap` — без распаковки,
# мгновенно и ровно в том объёме, который виден. При отдалении берётся тот же кусок с
# прореживанием, при приближении — один к одному.
#
# ЦЕНА. Файл кэша — ширина × высота × 3 байта, лежит рядом с картинкой и удаляется
# вместе с ней. Строится один раз. Замеры на картах заказчика (05.09.2026):
#
#   12 042 × 9 686  (116 млн, CMYK JPEG) — 1.6 с, файл 350 МБ
#   17 944 × 13 220 (237 млн, PNG RGBA)  — 2.5 с, файл 712 МБ
#
# Чтение куска после этого: 0.003…0.11 с — на глаз незаметно. Качество на той же карте
# 237 млн: видно 156 км — 43 м/пиксель, 39 км — 17 м/пиксель, 16 км — 8.7 м/пиксель,
# то есть ровно разрешение оригинала.
DETAIL_DIR = "детализация"
# Ниже этого порога кэш не нужен: картинка и так показывается почти без потерь
# (4 000 px хватает при 16 млн пикселей и меньше).
DETAIL_MIN_PIXELS = 30_000_000
# Сколько точек разрешено держать в ОДНОМ показанном куске: 40 млн — это 160 МБ в RGBA
# и около 0.2 с на чтение. Ограничение нужно на больших экранах, где запрошенный кусок
# иначе доходил до 85 млн точек (342 МБ).
MAX_WINDOW_PIXELS = 40_000_000


def detail_paths(image_path):
    """Пути кэша детализации: (файл данных, файл описания). Кладутся в подпапку рядом
    с картинкой — по правилу «данные участка лежат в своей папке» (ОГРАНИЧЕНИЯ 5.1б)."""
    d = os.path.join(os.path.dirname(image_path), DETAIL_DIR)
    base = os.path.splitext(os.path.basename(image_path))[0]
    return os.path.join(d, base + ".dat"), os.path.join(d, base + ".json")


def detail_ready(image_path):
    """Готов ли кэш детализации и не устарел ли он (сверяется с размером картинки)."""
    return read_detail_meta(image_path) is not None


def read_detail_meta(image_path):
    """Описание кэша либо None. Проверяет, что данные на месте и того самого размера."""
    import json
    dat, js = detail_paths(image_path)
    try:
        with io.open(js, encoding="utf-8") as f:
            meta = json.load(f)
        w, h = int(meta["w"]), int(meta["h"])
        if os.path.getsize(dat) != w * h * 3:
            return None                    # оборвалось на середине — считаем, что нет
        if os.path.getmtime(dat) < os.path.getmtime(image_path):
            return None                    # картинку заменили новой под тем же именем
        meta["dat"] = dat
        return meta
    except Exception:                      # нет файла, битый json, нет прав
        return None


def build_detail(image_path, on_progress=None):
    """Развернуть картинку в несжатый файл для быстрого чтения кусками.

    ⚠️ ОДИН РАЗ И НАДОЛГО. Полная распаковка большого файла стоит дорого (для 237 млн
    пикселей это около гигабайта памяти на несколько секунд), поэтому делается однократно,
    а результат остаётся на диске: дальше любой кусок читается без распаковки вовсе.

    `on_progress(доля, текст)` — необязательный отчёт о ходе работы."""
    import json
    from PIL import Image, ImageFile

    if (Image.MAX_IMAGE_PIXELS or 0) < MAX_PIXELS_ALLOWED:
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS_ALLOWED
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    dat, js = detail_paths(image_path)
    os.makedirs(os.path.dirname(dat), exist_ok=True)
    if on_progress:
        on_progress(0.0, "чтение картинки")
    with Image.open(image_path) as im:
        w, h = im.size
        if w * h > MAX_PIXELS_ALLOWED:
            raise ValueError("картинка больше допустимого: %.0f млн пикселей"
                             % (w * h / 1e6))
        rgb = im.convert("RGB")
        if on_progress:
            on_progress(0.5, "запись кэша (%.0f МБ)" % (w * h * 3 / 1e6))
        # ПИШЕМ ПОЛОСАМИ. Один `tobytes()` на всю картинку создал бы ВТОРУЮ копию рядом с
        # уже распакованной — лишние сотни мегабайт на ровном месте.
        band = max(1, 8_000_000 // max(1, w))          # ~8 МБ на полосу
        tmp = dat + ".part"                             # готовый файл появляется целиком
        with io.open(tmp, "wb") as f:
            for y in range(0, h, band):
                y2 = min(h, y + band)
                f.write(rgb.crop((0, y, w, y2)).tobytes())
                if on_progress:
                    on_progress(0.5 + 0.5 * y2 / h, "запись кэша")
        del rgb
    if os.path.exists(dat):
        os.remove(dat)
    os.rename(tmp, dat)
    with io.open(js, "w", encoding="utf-8") as f:
        json.dump(dict(w=w, h=h, src=os.path.basename(image_path)), f,
                  ensure_ascii=False)
    if on_progress:
        on_progress(1.0, "готово")
    return dict(w=w, h=h, dat=dat)


def read_detail_window(meta, box_frac, max_px=MAX_PX):
    """Кусок картинки в полном разрешении -> (RGBA (H, W, 4), фактическая доля кадра).

    `box_frac` — видимая область в долях картинки `(x0, y0, x1, y1)`, где 0 — левый
    ВЕРХНИЙ угол исходной картинки. Возвращается кусок, прореженный ровно настолько,
    чтобы уложиться в `max_px`: вблизи это один к одному, издали — то же, что и раньше.

    Строка 0 результата — ЮГ (как и в `load_image_rgba`)."""
    w, h = int(meta["w"]), int(meta["h"])
    x0 = int(np.clip(box_frac[0], 0.0, 1.0) * w)
    x1 = int(np.ceil(np.clip(box_frac[2], 0.0, 1.0) * w))
    y0 = int(np.clip(box_frac[1], 0.0, 1.0) * h)
    y1 = int(np.ceil(np.clip(box_frac[3], 0.0, 1.0) * h))
    x1, y1 = max(x0 + 1, x1), max(y0 + 1, y1)
    # ⚠️ ШАГ ОКРУГЛЯЕТСЯ ВНИЗ, В СТОРОНУ ДЕТАЛИ. Шаг целый — брать можно каждый второй
    # пиксель или каждый третий, но не «два с половиной», и между соседними шагами
    # разрешение меняется скачком. Округление вверх (было) один раз промахнулось мимо
    # экрана: при видимых 39 км и экране 2400 точек шаг 3 давал 17.4 м на пиксель, тогда
    # как экран показывает 16.2 — карта выглядела чуть мягче, чем могла. Округление вниз
    # берёт следующий, более подробный шаг; данных при этом не больше чем вчетверо
    # (сторона не более двух `max_px`), а замер даёт 130 МБ в худшем случае и доли
    # секунды на чтение.
    step = max(1, int(max(x1 - x0, y1 - y0) // float(max_px)))
    # ⚠️ ПОТОЛОК ПО ПАМЯТИ. Округление вниз на большом экране может затребовать кусок в
    # 85 млн точек — это 342 МБ и 0.4 с на чтение (замер на 4K, видно 94 км). Дальше
    # шаг увеличивается, пока кусок не уложится в потолок: качество при этом остаётся
    # не хуже экранного, потому что запас у нас и так двукратный.
    while ((x1 - x0) // step) * ((y1 - y0) // step) > MAX_WINDOW_PIXELS:
        step += 1
    mm = np.memmap(meta["dat"], dtype=np.uint8, mode="r", shape=(h, w, 3))
    try:
        sub = np.array(mm[y0:y1:step, x0:x1:step, :])   # копия — memmap не держим
    finally:
        del mm
    out = np.empty((sub.shape[0], sub.shape[1], 4), np.uint8)
    out[:, :, :3] = sub
    out[:, :, 3] = 255
    # фактические границы куска (шаг мог их чуть урезать) — по ним ставится setRect
    got = (x0 / float(w), y0 / float(h),
           (x0 + sub.shape[1] * step) / float(w), (y0 + sub.shape[0] * step) / float(h))
    return out[::-1].copy(), got


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
    (`geomap.build_raster_basemap`: `canvas = canvas[::-1]`).

    ⚠️ БОЛЬШАЯ КАРТА УМЕНЬШАЕТСЯ ПРИ ДЕКОДИРОВАНИИ, а не после него. Экспорт из
    CorelDRAW легко даёт 20 000 пикселей по стороне; развернуть такое целиком — почти
    гигабайт памяти, и всё это ради картинки, которую мы всё равно ужимаем до 4 000 px.
    У JPEG есть `draft()`: библиотека распаковывает сразу в 1/2, 1/4 или 1/8 размера,
    почти не тратя ни памяти, ни времени. Для PNG такого нет — там помогает `reduce()`,
    целочисленное прореживание, которое дешевле полноценного `resize`. Окончательный
    размер в обоих случаях доводится `resize` с хорошим фильтром.

    Бросает `ValueError` с понятным текстом, если картинка больше `MAX_PIXELS_ALLOWED`:
    вместо системной ошибки про «decompression bomb» человек должен прочитать, что
    именно ему сделать."""
    from PIL import Image, ImageFile

    # Порог Pillow поднимаем до своего (см. MAX_PIXELS_ALLOWED). Ставится ДО `open`:
    # проверка размера срабатывает именно там.
    if (Image.MAX_IMAGE_PIXELS or 0) < MAX_PIXELS_ALLOWED:
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS_ALLOWED
    # Обрезанный при копировании файл лучше показать по то место, докуда он цел, чем
    # не показать вовсе: карта — подложка для глаз, расчёт от неё не зависит.
    ImageFile.LOAD_TRUNCATED_IMAGES = True

    with Image.open(path) as im:
        src_w, src_h = im.size
        if src_w * src_h > MAX_PIXELS_ALLOWED:
            raise ValueError(
                "Картинка слишком большая: %d × %d = %.0f млн пикселей (предел %.0f млн).\n"
                "Пересохраните её меньшего размера — для карты района достаточно "
                "4000 пикселей по длинной стороне: при участке 120 км это 30 м на "
                "пиксель, мельче деталей на карте всё равно нет."
                % (src_w, src_h, src_w * src_h / 1e6, MAX_PIXELS_ALLOWED / 1e6))
        target = (max(1, int(max_px)), max(1, int(max_px)))
        im.draft(None, target)            # JPEG: распаковать сразу уменьшенным
        w, h = im.size                    # draft мог уже уменьшить картинку
        k = max(w, h) / float(max_px)
        if k >= 2.0:                      # PNG и всё, что draft не взял
            im = im.reduce(int(k))        # целочисленное прореживание — дёшево
            w, h = im.size
            k = max(w, h) / float(max_px)
        if k > 1.0:
            im = im.resize((max(1, int(w / k)), max(1, int(h / k))), Image.LANCZOS)
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

        # РЕЗКОСТЬ ПРИ ПРИБЛИЖЕНИИ (05.09.2026). Отдельной кнопкой, а не молча при
        # загрузке: подготовка занимает минуту-две и оставляет на диске файл размером с
        # саму карту. Нужна она только большим картам — у мелких и так всё видно.
        self.btn_detail = QtWidgets.QPushButton("Подготовить резкость при приближении…")
        self.btn_detail.setToolTip(
            "Разворачивает карту в служебный файл рядом с ней, и дальше при "
            "приближении видимый кусок показывается в ПОЛНОМ разрешении — подписи "
            "и мелкие значки читаются как в просмотрщике.\n\n"
            "Делается один раз на карту: замер на карте 237 млн точек — 2.5 секунды. "
            "Занимает место на диске: ширина × высота × 3 байта.")
        self.btn_detail.clicked.connect(self._build_detail)
        lay.addWidget(self.btn_detail)

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

    def _build_detail(self):
        """Построить кэш детализации для выбранной карты (см. `build_detail`)."""
        path = self.ed_path.text().strip()
        if not path or not os.path.exists(path):
            self.lbl_info.setText("⚠️ Сначала выберите файл картинки.")
            return
        if detail_ready(path):
            self.lbl_info.setText(
                "Резкость уже подготовлена для этой карты — просто покажите её.")
            return
        try:
            from PIL import Image
            Image.MAX_IMAGE_PIXELS = max(Image.MAX_IMAGE_PIXELS or 0, MAX_PIXELS_ALLOWED)
            with Image.open(path) as im:
                w, h = im.size
        except Exception as e:
            self.lbl_info.setText("⚠️ Файл не читается: %s" % e)
            return
        need_mb = w * h * 3 / 1e6
        if QtWidgets.QMessageBox.question(
                self, "Резкость при приближении",
                "Карта %d × %d (%.0f млн точек).\n\n"
                "Будет создан служебный файл на %.0f МБ рядом с картинкой, в папке «%s». "
                "Обычно это несколько секунд, и делается один раз.\n\n"
                "После этого при приближении карта показывается в полном разрешении.\n\n"
                "Продолжить?" % (w, h, w * h / 1e6, need_mb, DETAIL_DIR),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes) != QtWidgets.QMessageBox.Yes:
            return
        # ⚠️ ОКНО НЕ ДОЛЖНО ВЫГЛЯДЕТЬ ЗАВИСШИМ. Работа идёт в основном потоке (Pillow и
        # так держит GIL на распаковке, фоновый поток выигрыша не даст), поэтому просто
        # показываем ход работы и отдаём Qt время на перерисовку.
        dlg = QtWidgets.QProgressDialog("Подготовка резкости…", "", 0, 100, self)
        dlg.setCancelButton(None)
        dlg.setWindowTitle("Своя карта")
        dlg.setWindowModality(QtCore.Qt.WindowModal)
        dlg.show()

        def report(frac, text):
            dlg.setValue(int(100 * frac))
            dlg.setLabelText("%s…" % text)
            QtWidgets.QApplication.processEvents()

        try:
            build_detail(path, on_progress=report)
        except MemoryError:
            dlg.close()
            self.lbl_info.setText(
                "⚠️ Не хватило памяти на подготовку. Пересохраните карту меньшего "
                "размера — 8000 точек по длинной стороне обычно достаточно.")
            return
        except Exception as e:
            dlg.close()
            self.lbl_info.setText("⚠️ Не получилось: %s" % e)
            return
        dlg.close()
        self.btn_detail.setText("Резкость подготовлена ✓")
        self.lbl_info.setText(
            "Готово. Нажмите «Показать карту» — при приближении видимый кусок будет "
            "показан в полном разрешении (%d × %d)." % (w, h))

    # ------------------------------------------------------------------
    def _pick(self):
        fn, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Картинка карты местности", self._dir,
            "Картинки (*.png *.jpg *.jpeg);;Все файлы (*)")
        if not fn:
            return
        self.ed_path.setText(fn)
        self._sync_detail_button()
        got = coords_from_name(fn)
        if got:                              # координаты прямо в имени файла
            lo, la, ho, ha = got
            self.ed["lon_min"].setText("%.6f" % lo)
            self.ed["lat_min"].setText("%.6f" % la)
            self.ed["lon_max"].setText("%.6f" % ho)
            self.ed["lat_max"].setText("%.6f" % ha)
            self.lbl_info.setText("Координаты взяты из имени файла — сверьте по карте.")

    def _sync_detail_button(self):
        """Подпись кнопки под выбранный файл: подготовлена резкость или ещё нет."""
        path = self.ed_path.text().strip()
        if path and detail_ready(path):
            self.btn_detail.setText("Резкость подготовлена ✓")
        else:
            self.btn_detail.setText("Подготовить резкость при приближении…")

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
