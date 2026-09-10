# -*- mode: python ; coding: utf-8 -*-
"""
Сборка демонстрационного .exe (папка + данные, архивируется и переносится на чужой ПК).

Запуск:  pyinstaller --noconfirm build_demo.spec

ПОЧЕМУ ИМЕННО ТАК:

* --onedir (папка), а не один файл: одиночный .exe при каждом запуске распаковывает себя
  во временный каталог — старт долгий, а пути к geo_cache и tile_cache начинают вести
  «внутрь» временной папки, а не рядом с программой;

* тяжёлые геобиблиотеки ИСКЛЮЧЕНЫ. pyrosm/geopandas/rasterio нужны только для чтения
  сырого .osm.pbf (model/threat_grid.py импортирует их ЛЕНИВО, внутри функции). Для показа
  карта берётся из готового geo_cache/threat_layers.npz, поэтому в сборку они не идут:
  это сотни мегабайт и главный источник проблем при упаковке;

* СОБИРАТЬ НАДО ИЗ ПУТИ БЕЗ КИРИЛЛИЦЫ. Qt отдаёт PyInstaller путь к своим плагинам в
  системной кодировке, и в профиле вида C:\\Users\\Денис он превращается в C:\\Users\\?????,
  после чего сборка падает с «Qt plugin directory does not exist». Рабочее окружение —
  venv в каталоге типа C:\\uav_build\\venv.
"""
import os
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None
ROOT = os.path.abspath(os.getcwd())

# RASTERIO подтягивается ЦЕЛИКОМ. Его подмодули загружаются динамически, и обычный
# анализ импортов их не видит: собранная программа падала на `No module named
# 'rasterio.sample'` при чтении dem.tif — молча, внутри try/except, так что снаружи это
# выглядело как «рельеф не работает». Вместе с модулями нужны и файлы данных GDAL,
# иначе библиотека ругается «Cannot find gdalvrt.xsd (GDAL_DATA is not defined)».
rasterio_hidden = collect_submodules("rasterio")
rasterio_datas = collect_data_files("rasterio")

# ДАННЫЕ, которые кладутся рядом с .exe. Без файла слоёв программа бесполезна;
# tile_cache даёт карту-подложку без интернета (в кэше зумы 7…15 — весь рабочий диапазон).
#
# ⚠️ ПАПКА УЧАСТКА, А НЕ КОРЕНЬ geo_cache. С 29.08.2026 у каждого куска карты своя папка
# (`geo_cache/<участок>/`: исходник, слои .npz, рельеф). Кладём папку ТОЛЬКО активного
# участка: чужие данные — это лишние сотни мегабайт, а перепутанный рельеф выглядит как
# «карта высот не та», без единой ошибки на экране.
sys.path.insert(0, ROOT)
from config import THREAT_AREA, THREAT_AREAS      # noqa: E402  (после sys.path)

_area_dir = THREAT_AREAS[THREAT_AREA].get("dir", "")
_geo_src = os.path.join(ROOT, "geo_cache", _area_dir) if _area_dir \
    else os.path.join(ROOT, "geo_cache")
_geo_dst = os.path.join("geo_cache", _area_dir) if _area_dir else "geo_cache"

_area = THREAT_AREAS[THREAT_AREA]
datas = [
    # только рабочие файлы участка: слои и рельеф. Исходный .osm.pbf нужен лишь для
    # пересборки карты, в показе бесполезен
    (os.path.join(_geo_src, _area["layers"]), _geo_dst),
    # ЗАСТАВКА: без неё программа не упадёт, а просто запустится без эмблемы — молча
    (os.path.join(ROOT, "resurce", "Эмблема.png"), "resurce"),
    (os.path.join(ROOT, "docs", "Методичка_пользователя.docx"), "docs"),
]
if _area.get("dem"):
    for _ext in (".tif", ".tiff", ".hgt"):
        _p = os.path.join(_geo_src, _area["dem"] + _ext)
        if os.path.exists(_p):
            datas.append((_p, _geo_dst))

# ТАЙЛЫ ПОДЛОЖКИ — ТОЛЬКО ТЕ, ЧТО НАКРЫВАЮТ УЧАСТОК. Кэш общий и хранит ещё и прежние
# районы: копировать его целиком — это лишние сотни мегабайт картинок, которые в этой
# сборке никогда не покажутся (камера ограничена рамкой участка).
from config import THREAT_BBOX_LONLAT              # noqa: E402


def _tile_xy(lon, lat, z):
    import math
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(math.radians(lat))
                            + 1 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


_lo, _la, _ho, _ha = THREAT_BBOX_LONLAT
# ⚠️ ДВА МЕСТА ПОИСКА, КАК У ПОКАЗА. С 03.09.2026 у каждого участка свой
# tile_cache/<участок>/, но старая общая раскладка tile_cache/<слой>/ НЕ ПЕРЕЛОЖЕНА
# (ОГРАНИЧЕНИЯ 5.1д: правило действует на то, что качается заново). Поэтому
# `geomap.find_cached_tile` перебирает ОБА места — сперва папку участка, затем корень
# кэша, — и сборка обязана делать то же самое, иначе половина подложки не доедет.
#
# Замер по участку `arh` 08.09.2026 (слой osm, рабочая рамка Плесецка 117 × 73 км):
#   только корень кэша  — 2 759 тайлов, зумы 7…10 полные, но z13 лишь 69 %;
#   только папка участка — 1 200 тайлов, z13 30 %, а зумы 7…10 ПУСТЫ (0 из 53).
# Оба перекоса ломают показ по-своему: без папки участка теряется детализация, без
# корня кэша программа открывается на обзорном зуме БЕЗ ПОДЛОЖКИ ВООБЩЕ.
# Берём объединение: приоритет у папки участка (она свежее), корень добирает остальное.
_tile_roots = []                                   # (корень источника, папка назначения)
if _area_dir:
    _tile_roots.append((os.path.join(ROOT, "tile_cache", _area_dir),
                        os.path.join("tile_cache", _area_dir)))
_tile_roots.append((os.path.join(ROOT, "tile_cache"), "tile_cache"))

_seen_tiles = set()                                # (слой, z, x, y) — чтобы не дублировать
for _layer in ("osm", "osm_hot"):
    for _z in range(7, 16):                        # тот же диапазон, что у программы
        _x0, _y0 = _tile_xy(_lo, _ha, _z)
        _x1, _y1 = _tile_xy(_ho, _la, _z)
        for _src_root, _dst_root in _tile_roots:
            for _x in range(_x0, _x1 + 1):
                _src_dir = os.path.join(_src_root, _layer, str(_z), str(_x))
                if not os.path.isdir(_src_dir):
                    continue
                for _y in range(_y0, _y1 + 1):
                    _key = (_layer, _z, _x, _y)
                    if _key in _seen_tiles:        # уже взят из папки участка
                        continue
                    _f = os.path.join(_src_dir, "%d.png" % _y)
                    if os.path.exists(_f):
                        _seen_tiles.add(_key)
                        datas.append((_f, os.path.join(_dst_root, _layer,
                                                       str(_z), str(_x))))

datas = [(src, dst) for src, dst in datas if os.path.exists(src)]
datas += rasterio_datas                     # файлы данных GDAL (см. выше)

# НЕ КЛАДЁМ: исходные .osm.pbf (23 МБ) и старые копии .npz — они нужны только для
# пересборки карты, для показа бесполезны. Папка `<участок>/архив/` тоже не нужна.

excludes = [
    # ⚠️ RASTERIO ИСКЛЮЧАТЬ НЕЛЬЗЯ: им читается geo_cache/dem.tif (GeoTIFF с высотами).
    # Первая сборка была без него — и в собранной программе не грузилась карта высот, не
    # работал рельеф, а чекбоксы «Карта высот» и «Приоритет высот» оставались серыми
    # (они гаснут, когда данных нет). Здесь его нет намеренно.
    # Чтение .osm.pbf — только для подготовки данных, не для показа
    "pyrosm", "geopandas", "shapely", "pyproj", "fiona", "pandas",
    "pyarrow", "cykhash", "python-rapidjson", "rapidjson",
    # прочее, чего в программе нет
    "tkinter", "docx", "PyQt5.QtWebEngineWidgets", "PyQt5.QtBluetooth",
    "PyQt5.QtMultimedia", "PyQt5.QtQuick", "PyQt5.QtQml", "PyQt5.Qt3DCore",
    "IPython", "jupyter", "notebook", "pytest", "scipy",
]

a = Analysis(
    ["main_qt.py"],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=["pyqtgraph.graphicsItems.ColorBarItem"] + rasterio_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Карта угроз",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Обычно консоль не нужна — интерфейс графический. Но при разборе проблем сборки
    # без неё не видно ни ошибок импорта, ни трассировки: ставим DEMO_CONSOLE=1.
    console=bool(os.environ.get("DEMO_CONSOLE")),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Карта угроз",
)
