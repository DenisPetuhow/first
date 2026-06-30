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
    "topo":      "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
    "satellite": "https://server.arcgisonline.com/ArcGIS/rest/services/"
                 "World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "dark":      "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
}
LAYER_LABELS = {
    "scheme":    "схема (оффлайн)",
    "osm":       "OSM (улицы)",
    "topo":      "топо",
    "satellite": "спутник",
    "dark":      "тёмная",
}
_USER_AGENT = "uav_sensor_mvc/1.0 (educational UAV-detection modeling)"

# Ограничение кэша: не более 10 уровней зума (чтобы кэш не разрастался).
# Самый детальный уровень = ZOOM_MAX. Переопределяется env UAV_ZOOM_MIN/MAX.
ZOOM_MAX = int(os.environ.get("UAV_ZOOM_MAX", "15"))
ZOOM_MIN = int(os.environ.get("UAV_ZOOM_MIN", str(ZOOM_MAX - 9)))   # 10 уровней

# Раскладка оси Y тайлов: XYZ (как OSM/Esri-online) или TMS (как кэш SAS.Planet,
# ось Y инвертирована). Для онлайна — XYZ; если кладёте свою папку SAS — TMS.
TILE_TMS = os.environ.get("UAV_TILE_TMS", "0") == "1"


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


def tile_cache_path(layer, z, x, y):
    return os.path.join(cache_root(), layer, str(z), str(x), f"{y}.png")


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
    """Байты тайла: сперва оффлайн-кэш, затем (если разрешено) сеть с кешированием.
    Возвращает bytes или None."""
    path = tile_cache_path(layer, z, x, y)
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
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
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:                      # кешируем для оффлайна
            f.write(data)
        return data
    except Exception:
        return None


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

    Возвращает (rgba (H,W,4) float, (ex0,ex1,ey0,ey1) км-экстент картинки) либо
    None, если ни одного тайла нет (оффлайн и кэш пуст) — тогда рисуется схема.
    Изображение перевёрнуто так, что строка 0 = юг (для setRect с осью Y вверх).
    """
    if layer not in TILE_LAYERS:
        return None
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
    canvas = np.zeros((ny * 256, nx * 256, 4), np.float32)
    got = 0
    for ix in range(nx):
        for iy in range(ny):
            data = fetch_tile_bytes(layer, z, x0 + ix, y0 + iy, allow_net)
            if data is None:
                continue
            try:
                img = _decode_tile(data)
            except Exception:
                continue
            if img.shape[0] >= 256 and img.shape[1] >= 256:
                canvas[iy * 256:(iy + 1) * 256, ix * 256:(ix + 1) * 256] = img[:256, :256]
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
