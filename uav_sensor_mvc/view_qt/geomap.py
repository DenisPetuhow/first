# -*- coding: utf-8 -*-
"""
КАРТА · Гео-привязка, слои тайлов и оффлайн-схема для вкладки 2.

Локальный км-фрейм привязан к Курску (KURSK): км (0,0) = центр Курска. Перевод
км<->широта/долгота — равнопромежуточный (для области ~150 км погрешность мала).
Тайлы (Web Mercator) берутся из ОФФЛАЙН-кэша на диске; если тайла нет и разрешён
интернет — скачиваются и кешируются. Если тайлов нет вовсе — рисуется векторная
СХЕМА (сетка координат + города области), не требующая ни интернета, ни файлов.

Несколько бесплатных слоёв (OSM, топо, спутник, тёмная) задаются URL-шаблоном;
заранее скачать их в кэш можно скриптом tools/download_tiles.py.
"""
import io
import os
import math
import threading
from collections import OrderedDict
import numpy as np

# ----------------------------------------------------------------------
# Геопривязка: центр Курска
# ----------------------------------------------------------------------
KURSK_LON = 36.1936
KURSK_LAT = 51.7306
_KM_PER_DEG_LAT = 110.574


# ФОРМУЛА ПЕРЕВОДА — ОДНА НА ПРОЕКТ, в `model/geo_frame.py` (план 8, задача 8.3).
# Здесь лежала её ВТОРАЯ КОПИЯ: числа совпадали с моделью, но ничто этого не
# гарантировало — правка одной копии не дошла бы до другой, и векторные слои разъехались
# бы с подложкой молча, без единой ошибки. Импорт модели из представления допустим:
# `geo_frame` — чистый модуль без Qt и без config (MVC не нарушается, зависимость идёт
# в разрешённую сторону «представление → модель»).
#
# Историческая мель, ради которой это писалось: `_km_per_deg_lon` когда-то была
# КОНСТАНТОЙ от KURSK_LAT, и `lonlat_to_km(..., lat0=...)` игнорировал переданную
# широту — для Курска верно, а для участка вкладки 3 давало ~6 % ошибки масштаба по X.
from model.geo_frame import (km_per_deg_lon as _km_per_deg_lon,   # noqa: E402
                             lonlat_to_km as _ll_to_km,
                             km_to_lonlat as _km_to_ll)


def km_to_lonlat(x_km, y_km, lon0=KURSK_LON, lat0=KURSK_LAT):
    """Локальные км (восток, север от якоря) -> (долгота, широта)."""
    return _km_to_ll(x_km, y_km, lon0, lat0)


def lonlat_to_km(lon, lat, lon0=KURSK_LON, lat0=KURSK_LAT):
    """(долгота, широта) -> локальные км (восток, север от якоря)."""
    return _ll_to_km(lon, lat, lon0, lat0)


# ----------------------------------------------------------------------
# Web Mercator: тайловые координаты
# ----------------------------------------------------------------------
def deg2num(lon, lat, z):
    """(долгота, широта, зум) -> дробные тайловые координаты (xt, yt)."""
    n = 2.0 ** z
    xt = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    yt = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return xt, yt


def num2deg(xt, yt, z):
    """Дробные тайловые координаты -> (долгота, широта) угла тайла."""
    n = 2.0 ** z
    lon = xt / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * yt / n))))
    return lon, lat


# ----------------------------------------------------------------------
# Слои тайлов (бесплатные источники). {z}/{x}/{y}.
# ----------------------------------------------------------------------
TILE_LAYERS = {
    "osm":       "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "osm_hot":   "https://a.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
    "topo":      "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
    "cyclosm":   "https://a.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png",
    "opnv":      "https://tileserver.memomaps.de/tilegen/{z}/{x}/{y}.png",
    "satellite": "https://server.arcgisonline.com/ArcGIS/rest/services/"
                 "World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "dark":      "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
}
LAYER_LABELS = {
    "scheme":    "схема (оффлайн)",
    "osm":       "OSM (улицы)",
    "osm_hot":   "OSM HOT (гуманитарный)",
    "topo":      "топо (OpenTopoMap)",
    "cyclosm":   "CyclOSM",
    "opnv":      "ÖPNVKarte (транспорт)",
    "satellite": "спутник (Esri)",
    "dark":      "тёмная",
}
_USER_AGENT = "uav_sensor_mvc/1.0 (educational UAV-detection modeling)"

# Ограничение кэша: не более 10 уровней зума (чтобы кэш не разрастался).
# Самый детальный уровень = ZOOM_MAX. Переопределяется env UAV_ZOOM_MIN/MAX.
ZOOM_MAX = int(os.environ.get("UAV_ZOOM_MAX", "15"))
ZOOM_MIN = int(os.environ.get("UAV_ZOOM_MIN", str(ZOOM_MAX - 9)))   # 10 уровней

# Раскладка оси Y тайлов: XYZ (как OSM/Esri-online) или TMS (как часть экспортов
# SAS.Planet, ось Y инвертирована: y_TMS = 2^z-1-y_XYZ). Глобальный флажок — для
# случая, когда ВСЕ ваши локальные слои в одной раскладке; LAYER_TMS_OVERRIDE
# переопределяет per-слой (например, свой "sat" — TMS, а онлайн-слои — нет).
TILE_TMS = os.environ.get("UAV_TILE_TMS", "0") == "1"
# per-слой переопределение: UAV_TMS_LAYERS="Sat,MyLayer" -> TMS только для них,
# остальные (в т.ч. онлайн-слои) остаются в XYZ независимо от TILE_TMS.
LAYER_TMS_OVERRIDE = {name: True for name in
                      os.environ.get("UAV_TMS_LAYERS", "").split(",") if name}


def discover_local_layers():
    """Папки в кэше, не входящие в TILE_LAYERS (например, ваша 'Sat' с тайлами
    SAS.Planet) — это ОФФЛАЙН-ТОЛЬКО слои: сеть для них не запрашивается, читается
    только то, что уже лежит на диске по пути <cache_root>/<layer>/{z}/{x}/{y}.ext."""
    root = cache_root()
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        if name in TILE_LAYERS or name == "scheme":
            continue
        p = os.path.join(root, name)
        if not os.path.isdir(p):
            continue
        # ⚠️ ОТЛИЧИТЬ СЛОЙ ОТ ПАПКИ УЧАСТКА. С 03.09.2026 в корне кэша лежат и папки
        # участков (<cache>/plesetsk/osm/…), а они не слои: внутри слоя первым уровнем
        # идут ЗУМЫ (числа), внутри участка — имена слоёв (буквы). Без этой проверки
        # «plesetsk» попал бы в список слоёв и предлагался бы в выпадающем списке карт.
        try:
            kids = os.listdir(p)
        except OSError:
            continue
        if kids and not any(k.isdigit() for k in kids):
            continue                                  # это папка участка, а не слой
        out.append(name)
    return out


def cache_root():
    """Каталог дискового кэша тайлов.

    Приоритет: env UAV_TILE_CACHE -> <проект>/tile_cache. Сюда же можно положить
    свою папку тайлов SAS.Planet как <cache>/<layer>/{z}/{x}/{y}.png (учтите
    XYZ/TMS — см. TILE_TMS)."""
    env = os.environ.get("UAV_TILE_CACHE")
    if env:
        return env
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "tile_cache")


_TILE_EXTS = (".png", ".jpg", ".jpeg", ".webp")

# ПАПКА УЧАСТКА В КЭШЕ ТАЙЛОВ (правило «данные участка — в папке участка», ОГРАНИЧЕНИЯ
# 5.1д). Новая раскладка: <cache>/<участок>/<слой>/{z}/{x}/{y}. Старая — <cache>/<слой>/…
#
# ⚠️ СТАРОЕ НЕ ПЕРЕКЛАДЫВАЕМ (решение заказчика 03.09.2026): уже скачанные 13 273 тайла
# остаются на месте и продолжают находиться — старая раскладка служит ЗАПАСНЫМ местом
# поиска, ровно как корень geo_cache/ для файлов участка (threat_grid.area_file).
# В папку участка пишется только то, что качается заново.
#
# Имя участка передаётся ПАРАМЕТРОМ, а не берётся из config: этот модуль намеренно не
# зависит от config (только stdlib + numpy), и у вкладок разные районы — вкладка 2
# работает по Курску, вкладка 3 по своему участку. area=None -> прежнее поведение.
TILE_AREA_ENV = os.environ.get("UAV_TILE_AREA", "").strip()


def _area_dir(area):
    """Имя папки участка: явный аргумент, иначе UAV_TILE_AREA, иначе '' (старая
    раскладка). Пустая строка означает «без папки участка»."""
    name = TILE_AREA_ENV if area is None else str(area or "")
    return name.strip().strip("/\\")


def _tms_y(z, y):
    """XYZ <-> TMS: ось Y инвертирована (TMS считает строки снизу вверх)."""
    return (2 ** int(z)) - 1 - int(y)


def tile_cache_path(layer, z, x, y, ext=".png", area=None):
    """Путь тайла в кэше. y уже в РАСКЛАДКЕ КЭША (если слой TMS — см. _cache_y).
    area — папка участка; пусто -> старая раскладка <cache>/<слой>/…"""
    a = _area_dir(area)
    parts = [cache_root()] + ([a] if a else []) + [layer, str(z), str(x), f"{y}{ext}"]
    return os.path.join(*parts)


def _cache_y(layer, z, y, layer_tms=None):
    """y из XYZ (как считает вся остальная математика) -> y на диске для этого
    слоя. layer_tms=None -> берёт глобальный TILE_TMS (применяется к ЛЮБОМУ слою,
    в т.ч. локальным офлайн-кэшам вроде SAS.Planet); True/False — переопределение
    для конкретного слоя (см. LAYER_TMS_OVERRIDE)."""
    tms = LAYER_TMS_OVERRIDE.get(layer, TILE_TMS) if layer_tms is None else layer_tms
    return _tms_y(z, y) if tms else y


def find_cached_tile(layer, z, x, y, area=None):
    """Найти файл тайла в кэше, перебирая известные расширения и XYZ/TMS.
    Возвращает путь или None.

    ДВА МЕСТА ПОИСКА, по порядку: папка участка (новая раскладка) и корень кэша
    (старая). Второе — не «на всякий случай», а рабочий путь: там лежат все тайлы,
    скачанные до 03.09.2026, и перекладывать их не будем."""
    cy = _cache_y(layer, z, y)
    a = _area_dir(area)
    for folder in ([a] if a else []) + [""]:
        for ext in _TILE_EXTS:
            p = tile_cache_path(layer, z, x, cy, ext, area=folder)
            if os.path.exists(p):
                return p
    return None


def _sniff_ext(data):
    """Расширение по магическим байтам (для корректного имени файла в кэше)."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def _decode_tile(data):
    """Байты тайла (PNG или JPEG) -> RGBA float [0..1] (H,W,4).

    Сначала Pillow (умеет и JPEG — нужен для спутника Esri), иначе matplotlib (PNG).
    """
    arr = None
    try:                                                 # Pillow: PNG и JPEG
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        arr = np.asarray(im, dtype=np.float32) / 255.0
    except Exception:
        import matplotlib.image as mpimg                 # запасной путь: только PNG
        arr = mpimg.imread(io.BytesIO(data))
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    if arr.shape[-1] == 3:
        arr = np.concatenate([arr, np.ones(arr.shape[:2] + (1,), arr.dtype)], axis=-1)
    return arr.astype(np.float32)


def fetch_tile_bytes(layer, z, x, y, allow_net=True, timeout=6, area=None):
    """Байты тайла: сперва оффлайн-кэш (перебор расширений + XYZ/TMS для слоя),
    затем (если разрешено и слой известен в TILE_LAYERS) сеть с кешированием.
    Возвращает bytes или None. Слои НЕ из TILE_LAYERS (локальные, например 'Sat')
    читаются ТОЛЬКО из кэша — сеть для них не запрашивается.

    ⚠️ Скачанный тайл кладётся в ПАПКУ УЧАСТКА (area), а ищется — и там, и в старой
    общей раскладке. Так детальные уровни z13–15 докачиваются сами при приближении и
    сразу ложатся правильно (план 8, задача 8.1)."""
    found = find_cached_tile(layer, z, x, y, area=area)
    if found:
        try:
            with open(found, "rb") as f:
                return f.read()
        except OSError:
            pass
    if not allow_net or layer not in TILE_LAYERS:
        return None
    import urllib.request
    import ssl
    url = TILE_LAYERS[layer].format(z=z, x=x, y=y)
    try:
        ctx = ssl.create_default_context()
        ca = os.environ.get("SSL_CERT_FILE")
        if ca and os.path.exists(ca):
            ctx.load_verify_locations(ca)
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        data = urllib.request.urlopen(req, context=ctx, timeout=timeout).read()
        cy = _cache_y(layer, z, y)                        # онлайн-источники всегда XYZ,
        path = tile_cache_path(layer, z, x, cy, _sniff_ext(data),   # но пишем в раскладке слоя
                               area=area)                 # и в папку своего участка
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:                       # кешируем для оффлайна
            f.write(data)
        return data
    except Exception:
        return None


# In-memory кэш ДЕКОДИРОВАННЫХ тайлов (uint8), чтобы при панораме/смене зума не
# перечитывать и не перекодировать одни и те же тайлы (ускорение отрисовки).
# Потокобезопасен — вызывается из рабочих потоков сборки подложки.
_TILE_MEM = OrderedDict()
_TILE_MEM_MAX = 512
_TILE_MEM_LOCK = threading.Lock()


def _decoded_tile_u8(layer, z, x, y, allow_net, area=None):
    """Тайл как uint8 RGBA (256,256,4) из памяти/диска/сети, либо None."""
    # участок — ЧАСТЬ КЛЮЧА: у двух участков могут совпасть (z, x, y) на общих зумах,
    # и без участка в ключе вкладка получила бы чужую картинку из памяти
    key = (_area_dir(area), layer, int(z), int(x), int(y))
    with _TILE_MEM_LOCK:
        hit = _TILE_MEM.get(key)
        if hit is not None:
            _TILE_MEM.move_to_end(key)
            return hit
    data = fetch_tile_bytes(layer, z, x, y, allow_net, area=area)
    if data is None:
        return None
    try:
        arr = _decode_tile(data)
    except Exception:
        return None
    if arr.shape[0] < 256 or arr.shape[1] < 256:
        return None
    u8 = (np.clip(arr[:256, :256], 0.0, 1.0) * 255).astype(np.uint8)
    with _TILE_MEM_LOCK:
        _TILE_MEM[key] = u8
        if len(_TILE_MEM) > _TILE_MEM_MAX:
            _TILE_MEM.popitem(last=False)
    return u8


def _pick_zoom(lon_min, lon_max, zmin=ZOOM_MIN, zmax=ZOOM_MAX,
               target_px=None, min_tiles_x=2.0):
    """Зум, при котором тайлы отображаются ~1:1 к экрану (родной размер подписей).

    История: сначала было размытие (брали слишком НИЗКИЙ зум — мозаика меньше экрана,
    её растягивали). Потом перегнули в другую сторону: брали ПЕРВЫЙ зум, где мозаика
    уже ≥ экрана. Но тайлов на уровень вдвое больше, поэтому «первый ≥» давал мозаику
    до 2× шире экрана — её ужимали вдвое, и ПОДПИСИ на тайлах становились вдвое мельче
    и гуще (симптом «замельчает», не как в обычном OSM).

    Правильно — как в слиппи-картах: подобрать зум, при котором ширина мозаики в
    пикселях БЛИЖЕ ВСЕГО к ширине виджета (масштаб ≈ 1:1). Тогда подписи отрисованы
    в их «родном» размере. Выбираем z с минимальным |log2(nx) − log2(need)|.

    target_px — ЛОГИЧЕСКАЯ ширина виджета в px (без домножения на DPR: важнее верный
    размер подписей, чем субпиксельная чёткость на HiDPI — при необходимости обычные
    OSM-тайлы всё равно не дают retina-варианта).
    need — целевое число тайлов по 256 px, чтобы покрыть экран в масштабе 1:1.
    """
    span = max(lon_max - lon_min, 1e-6)
    need = max(min_tiles_x, (target_px / 256.0) if target_px else 4.0)
    best, best_err = zmin, float("inf")
    for z in range(zmin, zmax + 1):
        nx = deg2num(lon_max, 0, z)[0] - deg2num(lon_min, 0, z)[0]
        err = abs(math.log2(max(nx, 1e-9)) - math.log2(need))    # отклонение от 1:1 (в октавах)
        if err < best_err:
            best, best_err = z, err
    return best
    return best                              # даже zmax не дотянул — берём самый чёткий


def build_raster_basemap(kx0, kx1, ky0, ky1, layer="osm", allow_net=True,
                         max_tiles=192, target_px=None, lon0=KURSK_LON,
                         lat0=KURSK_LAT, area=None):
    """Собрать подложку для км-окна [kx0,kx1]×[ky0,ky1].

    Возвращает (rgba (H,W,4) uint8, (ex0,ex1,ey0,ey1) км-экстент картинки) либо
    None, если ни одного тайла нет (оффлайн и кэш пуст) — тогда рисуется схема.
    Тяжёлая функция (чтение/декод/склейка) — вызывать в рабочем ПОТОКЕ, не в UI.
    Изображение перевёрнуто так, что строка 0 = юг (для setRect с осью Y вверх).

    layer может быть и слоем НЕ из TILE_LAYERS (локальный оффлайн-кэш, например
    ваша папка 'Sat') — тогда тайлы берутся ТОЛЬКО с диска (см. fetch_tile_bytes).
    target_px — ширина виджета в пикселях (см. _pick_zoom: не даёт картинке быть
    мельче экрана — иначе она растягивается и выглядит размытой).
    allow_net=False — читать ТОЛЬКО кэш (чекбокс «офлайн»): ни одного сетевого
    запроса, отсутствующие тайлы просто пропускаются — быстрый путь без ожидания
    таймаутов.
    """
    lon_min, lat_min = (km_to_lonlat(kx0, ky0, lon0, lat0))
    lon_max, lat_max = (km_to_lonlat(kx1, ky1, lon0, lat0))
    lon_min, lon_max = float(min(lon_min, lon_max)), float(max(lon_min, lon_max))
    lat_min, lat_max = float(min(lat_min, lat_max)), float(max(lat_min, lat_max))
    z = _pick_zoom(lon_min, lon_max, target_px=target_px)
    x0 = int(math.floor(deg2num(lon_min, lat_max, z)[0]))
    x1 = int(math.floor(deg2num(lon_max, lat_min, z)[0]))
    y0 = int(math.floor(deg2num(lon_min, lat_max, z)[1]))    # верх (макс. широта)
    y1 = int(math.floor(deg2num(lon_max, lat_min, z)[1]))    # низ  (мин. широта)
    nx, ny = x1 - x0 + 1, y1 - y0 + 1
    while nx * ny > max_tiles and z > ZOOM_MIN:               # не качать слишком много
        z -= 1
        x0 = int(math.floor(deg2num(lon_min, lat_max, z)[0]))
        x1 = int(math.floor(deg2num(lon_max, lat_min, z)[0]))
        y0 = int(math.floor(deg2num(lon_min, lat_max, z)[1]))
        y1 = int(math.floor(deg2num(lon_max, lat_min, z)[1]))
        nx, ny = x1 - x0 + 1, y1 - y0 + 1
    canvas = np.zeros((ny * 256, nx * 256, 4), np.uint8)
    # ПАРАЛЛЕЛЬНАЯ загрузка (сеть/диск — I/O-bound): вместо тайла за тайлом
    # (было секундами при холодном кэше) — до 8 одновременно, как в браузере.
    import concurrent.futures
    jobs = [(ix, iy) for ix in range(nx) for iy in range(ny)]
    got = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(jobs) or 1)) as pool:
        fut_map = {pool.submit(_decoded_tile_u8, layer, z, x0 + ix, y0 + iy,
                               allow_net, area): (ix, iy) for ix, iy in jobs}
        for fut in concurrent.futures.as_completed(fut_map):
            ix, iy = fut_map[fut]
            try:
                u8 = fut.result()
            except Exception:
                u8 = None
            if u8 is None:
                continue
            canvas[iy * 256:(iy + 1) * 256, ix * 256:(ix + 1) * 256] = u8
            got += 1
    if got == 0:
        return None
    # геокрая собранной сетки тайлов -> км-экстент
    tl_lon, tl_lat = num2deg(x0, y0, z)                       # верх-лево (С-З)
    br_lon, br_lat = num2deg(x1 + 1, y1 + 1, z)              # низ-право (Ю-В)
    ex0, ey1 = lonlat_to_km(tl_lon, tl_lat, lon0, lat0)      # лево, верх
    ex1, ey0 = lonlat_to_km(br_lon, br_lat, lon0, lat0)      # право, низ
    canvas = canvas[::-1]                                     # строка 0 = юг (ось Y вверх)
    return canvas, (float(ex0), float(ex1), float(ey0), float(ey1))


# ----------------------------------------------------------------------
# Оффлайн-СХЕМА: города Курской области (прибл.) и рамка области.
# Координаты приблизительные — для ориентира (не точная граница).
# ----------------------------------------------------------------------
KURSK_CITIES = [
    ("Курск", 36.1936, 51.7306), ("Железногорск", 35.357, 52.330),
    ("Курчатов", 35.657, 51.660), ("Льгов", 35.280, 51.660),
    ("Щигры", 36.901, 51.875), ("Обоянь", 36.277, 51.207),
    ("Рыльск", 34.681, 51.566), ("Суджа", 35.273, 51.190),
    ("Фатеж", 35.861, 52.092), ("Дмитриев", 35.090, 52.131),
    ("Горшечное", 37.857, 51.553), ("Тим", 37.137, 51.633),
]
# приблизительный охват области (долгота/широта)
OBLAST_BBOX = (34.05, 50.90, 38.45, 52.35)   # lon_min, lat_min, lon_max, lat_max


# ⚠️ `city_points_km` УДАЛЕНА 02.09.2026 как мёртвая: она переводила `KURSK_CITIES` в
# км, но вкладка 2 передаёт сам список в `_init_basemap(scheme_points=...)`, а перевод
# делает миксин подложки. Единственная функция без единого вызова во всём проекте
# (журнал п. 210). Сами `KURSK_CITIES` и `oblast_outline_km` ЖИВЫЕ — их рисует
# оффлайн-схема вкладки 2, когда тайлов нет.


def oblast_outline_km(lon0=KURSK_LON, lat0=KURSK_LAT):
    """Прямоугольная рамка охвата области в км (замкнутый контур, для ориентира)."""
    lo, la, ho, ha = OBLAST_BBOX
    corners = [(lo, la), (ho, la), (ho, ha), (lo, ha), (lo, la)]
    xs, ys = [], []
    for lon, lat in corners:
        x, y = lonlat_to_km(lon, lat, lon0, lat0)
        xs.append(float(x)); ys.append(float(y))
    return np.column_stack([xs, ys])


def bbox_lonlat_to_km(bbox_lonlat, lon0, lat0):
    """(lon_min, lat_min, lon_max, lat_max) -> (kx0, kx1, ky0, ky1) в локальных км
    относительно якоря (lon0, lat0). Используется вкладкой 3 (свой участок карты)."""
    lo, la, ho, ha = bbox_lonlat
    x0, y0 = lonlat_to_km(lo, la, lon0, lat0)
    x1, y1 = lonlat_to_km(ho, ha, lon0, lat0)
    return (float(min(x0, x1)), float(max(x0, x1)),
            float(min(y0, y1)), float(max(y0, y1)))


def graticule_km(kx0, kx1, ky0, ky1, lon0=KURSK_LON, lat0=KURSK_LAT, step_deg=0.5):
    """Линии сетки широт/долгот в км для текущего окна. Возвращает список
    (тип, координаты, подпись): тип 'v'/'h'."""
    lon_a, lat_a = km_to_lonlat(kx0, ky0, lon0, lat0)
    lon_b, lat_b = km_to_lonlat(kx1, ky1, lon0, lat0)
    lon_min, lon_max = min(lon_a, lon_b), max(lon_a, lon_b)
    lat_min, lat_max = min(lat_a, lat_b), max(lat_a, lat_b)
    out = []
    lon = math.ceil(lon_min / step_deg) * step_deg
    while lon <= lon_max:
        x, _ = lonlat_to_km(lon, lat0, lon0, lat0)
        out.append(("v", float(x), f"{lon:.1f}°E"))
        lon += step_deg
    lat = math.ceil(lat_min / step_deg) * step_deg
    while lat <= lat_max:
        _, y = lonlat_to_km(lon0, lat, lon0, lat0)
        out.append(("h", float(y), f"{lat:.1f}°N"))
        lat += step_deg
    return out
