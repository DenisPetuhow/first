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
_KM_PER_DEG_LON = 111.320 * math.cos(math.radians(KURSK_LAT))


def km_to_lonlat(x_km, y_km, lon0=KURSK_LON, lat0=KURSK_LAT):
    """Локальные км (восток, север от центра) -> (долгота, широта)."""
    return (lon0 + np.asarray(x_km) / _KM_PER_DEG_LON,
            lat0 + np.asarray(y_km) / _KM_PER_DEG_LAT)


def lonlat_to_km(lon, lat, lon0=KURSK_LON, lat0=KURSK_LAT):
    """(долгота, широта) -> локальные км (восток, север от центра)."""
    return ((np.asarray(lon) - lon0) * _KM_PER_DEG_LON,
            (np.asarray(lat) - lat0) * _KM_PER_DEG_LAT)


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
        if os.path.isdir(os.path.join(root, name)):
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


def _tms_y(z, y):
    """XYZ <-> TMS: ось Y инвертирована (TMS считает строки снизу вверх)."""
    return (2 ** int(z)) - 1 - int(y)


def tile_cache_path(layer, z, x, y, ext=".png"):
    """Путь тайла в кэше. y уже в РАСКЛАДКЕ КЭША (если слой TMS — см. _cache_y)."""
    return os.path.join(cache_root(), layer, str(z), str(x), f"{y}{ext}")


def _cache_y(layer, z, y, layer_tms=None):
    """y из XYZ (как считает вся остальная математика) -> y на диске для этого
    слоя. layer_tms=None -> берёт глобальный TILE_TMS (применяется к ЛЮБОМУ слою,
    в т.ч. локальным офлайн-кэшам вроде SAS.Planet); True/False — переопределение
    для конкретного слоя (см. LAYER_TMS_OVERRIDE)."""
    tms = LAYER_TMS_OVERRIDE.get(layer, TILE_TMS) if layer_tms is None else layer_tms
    return _tms_y(z, y) if tms else y


def find_cached_tile(layer, z, x, y):
    """Найти файл тайла в кэше, перебирая известные расширения и XYZ/TMS.
    Возвращает путь или None."""
    cy = _cache_y(layer, z, y)
    for ext in _TILE_EXTS:
        p = tile_cache_path(layer, z, x, cy, ext)
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


def fetch_tile_bytes(layer, z, x, y, allow_net=True, timeout=15):
    """Байты тайла: сперва оффлайн-кэш (перебор расширений + XYZ/TMS для слоя),
    затем (если разрешено и слой известен в TILE_LAYERS) сеть с кешированием.
    Возвращает bytes или None. Слои НЕ из TILE_LAYERS (локальные, например 'Sat')
    читаются ТОЛЬКО из кэша — сеть для них не запрашивается."""
    found = find_cached_tile(layer, z, x, y)
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
        path = tile_cache_path(layer, z, x, cy, _sniff_ext(data))  # но пишем в раскладке слоя
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


def _decoded_tile_u8(layer, z, x, y, allow_net):
    """Тайл как uint8 RGBA (256,256,4) из памяти/диска/сети, либо None."""
    key = (layer, int(z), int(x), int(y))
    with _TILE_MEM_LOCK:
        hit = _TILE_MEM.get(key)
        if hit is not None:
            _TILE_MEM.move_to_end(key)
            return hit
    data = fetch_tile_bytes(layer, z, x, y, allow_net)
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


def _pick_zoom(lon_min, lon_max, max_tiles_x=6, zmin=4, zmax=13):
    """Зум, при котором ширина окна ~ max_tiles_x тайлов."""
    span = max(lon_max - lon_min, 1e-6)
    for z in range(zmax, zmin - 1, -1):
        nx = (deg2num(lon_max, 0, z)[0] - deg2num(lon_min, 0, z)[0])
        if nx <= max_tiles_x:
            return z
    return zmin


def build_raster_basemap(kx0, kx1, ky0, ky1, layer="osm", allow_net=True,
                         max_tiles=64, lon0=KURSK_LON, lat0=KURSK_LAT):
    """Собрать подложку для км-окна [kx0,kx1]×[ky0,ky1].

    Возвращает (rgba (H,W,4) uint8, (ex0,ex1,ey0,ey1) км-экстент картинки) либо
    None, если ни одного тайла нет (оффлайн и кэш пуст) — тогда рисуется схема.
    Тяжёлая функция (чтение/декод/склейка) — вызывать в рабочем ПОТОКЕ, не в UI.
    Изображение перевёрнуто так, что строка 0 = юг (для setRect с осью Y вверх).

    layer может быть и слоем НЕ из TILE_LAYERS (локальный оффлайн-кэш, например
    ваша папка 'Sat') — тогда тайлы берутся ТОЛЬКО с диска (см. fetch_tile_bytes).
    """
    lon_min, lat_min = (km_to_lonlat(kx0, ky0, lon0, lat0))
    lon_max, lat_max = (km_to_lonlat(kx1, ky1, lon0, lat0))
    lon_min, lon_max = float(min(lon_min, lon_max)), float(max(lon_min, lon_max))
    lat_min, lat_max = float(min(lat_min, lat_max)), float(max(lat_min, lat_max))
    z = _pick_zoom(lon_min, lon_max)
    x0 = int(math.floor(deg2num(lon_min, lat_max, z)[0]))
    x1 = int(math.floor(deg2num(lon_max, lat_min, z)[0]))
    y0 = int(math.floor(deg2num(lon_min, lat_max, z)[1]))    # верх (макс. широта)
    y1 = int(math.floor(deg2num(lon_max, lat_min, z)[1]))    # низ  (мин. широта)
    nx, ny = x1 - x0 + 1, y1 - y0 + 1
    while nx * ny > max_tiles and z > 4:                      # не качать слишком много
        z -= 1
        x0 = int(math.floor(deg2num(lon_min, lat_max, z)[0]))
        x1 = int(math.floor(deg2num(lon_max, lat_min, z)[0]))
        y0 = int(math.floor(deg2num(lon_min, lat_max, z)[1]))
        y1 = int(math.floor(deg2num(lon_max, lat_min, z)[1]))
        nx, ny = x1 - x0 + 1, y1 - y0 + 1
    canvas = np.zeros((ny * 256, nx * 256, 4), np.uint8)
    got = 0
    for ix in range(nx):
        for iy in range(ny):
            u8 = _decoded_tile_u8(layer, z, x0 + ix, y0 + iy, allow_net)
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


def city_points_km(lon0=KURSK_LON, lat0=KURSK_LAT):
    """Города области в локальных км: список (имя, x, y)."""
    out = []
    for name, lon, lat in KURSK_CITIES:
        x, y = lonlat_to_km(lon, lat, lon0, lat0)
        out.append((name, float(x), float(y)))
    return out


def oblast_outline_km(lon0=KURSK_LON, lat0=KURSK_LAT):
    """Прямоугольная рамка охвата области в км (замкнутый контур, для ориентира)."""
    lo, la, ho, ha = OBLAST_BBOX
    corners = [(lo, la), (ho, la), (ho, ha), (lo, ha), (lo, la)]
    xs, ys = [], []
    for lon, lat in corners:
        x, y = lonlat_to_km(lon, lat, lon0, lat0)
        xs.append(float(x)); ys.append(float(y))
    return np.column_stack([xs, ys])


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
