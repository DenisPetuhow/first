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

# ДАННЫЕ, которые кладутся рядом с .exe. Без threat_layers.npz программа бесполезна;
# tile_cache даёт карту-подложку без интернета (в кэше зумы 7…15 — весь рабочий диапазон).
datas = [
    (os.path.join(ROOT, "geo_cache", "threat_layers.npz"), "geo_cache"),
    (os.path.join(ROOT, "geo_cache", "dem.tif"), "geo_cache"),
    (os.path.join(ROOT, "tile_cache", "osm"), os.path.join("tile_cache", "osm")),
    (os.path.join(ROOT, "tile_cache", "osm_hot"), os.path.join("tile_cache", "osm_hot")),
    (os.path.join(ROOT, "docs", "Методичка_пользователя.docx"), "docs"),
]
datas = [(src, dst) for src, dst in datas if os.path.exists(src)]
datas += rasterio_datas                     # файлы данных GDAL (см. выше)

# НЕ КЛАДЁМ: severodonetsk.osm.pbf (23 МБ) и старые копии .npz (53 МБ) — они нужны только
# для пересборки карты, для показа бесполезны.

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
