# -*- coding: utf-8 -*-
"""
ПРИЁМКА ФАЙЛОВ ВЫСОТ — план 16.5, шаг 16.5.1 (файлы скачивает и сообщает заказчик) → проверка по §2.5 перед 16.5.2.

Смотрит `geo_cache/высоты_исходные/`: ETH (кроны, обязательно) — плитки `…_Map.tif`; FABDEM (земля, желательно) —
плитки `…_FABDEM_V1-2.tif`; GBA (высоты зданий, желательно) — `.json` «код постройки → высота». По каждому набору:
файлы и размер, доля района моделирования с запасом 25 км, которую накрывают плитки (недостающие — по имени, §2.4),
правдоподобие значений в окне у центра района (кроны: доля ≥ 5 м и медиана; земля: медиана и размах; GBA: доля
построек OSM с высотой).

    python -I tools/heights_files_check.py           # -I: скачанное — чужие данные, читать без путей текущей папки

Код возврата: 0 — кроны накрывают район целиком (FABDEM и GBA — только предупреждения), 1 — нет.
"""
import glob
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # корень проекта
for _p in (ROOT, os.path.join(ROOT, "src")):           # код программы — в src/ (журнал 364)
    if _p not in sys.path:   # ещё не в путях — добавить
        sys.path.insert(0, _p)
if hasattr(sys.stdout, "reconfigure"):   # печать в файл — UTF-8, иначе знаки «≥», «×» роняют вывод
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

SRC = os.path.join(ROOT, "geo_cache", "высоты_исходные")   # куда кладёт заказчик (§2)
MARGIN_KM = 25.0                                       # запас на наибольший радиус средства, км (§2.4)
GRID_DEG = 0.01                                        # шаг сетки при счёте покрытия, градусы
WIN_DEG = 0.05                                         # полуширина окна у центра района для пробы значений, градусы


def need_box():
    """Район моделирования с запасом. Вход: нет. Отдаёт: (lon0, lat0, lon1, lat1) градусы."""
    from config import THREAT_WORK_LONLAT
    lo, la, ho, ha = (float(v) for v in THREAT_WORK_LONLAT)   # район моделирования, градусы
    dla = MARGIN_KM / 111.32                           # запас по широте, градусы
    dlo = MARGIN_KM / (111.32 * math.cos(math.radians(0.5 * (la + ha))))   # по долготе, градусы
    return lo - dlo, la - dla, ho + dlo, ha + dla


def tile_names(box, kind):
    """Имена плиток, накрывающих рамку (§2.4). Вход: рамка градусы, «ETH» или «FABDEM». Отдаёт: список имён."""
    lo, la, ho, ha = box   # рамка с запасом, градусы
    st = 3 if kind == "ETH" else 1                     # размер плитки, градусы
    out = []
    for y in range(st * math.floor(la / st), int(math.ceil(ha)), st):   # юго-западные углы
        for x in range(st * math.floor(lo / st), int(math.ceil(ho)), st):   # по долготе — юго-западные углы плиток
            out.append("ETH_GlobalCanopyHeight_10m_2020_N%02dE%03d_Map.tif" % (y, x) if kind == "ETH"
                       else "N%02dE%03d_FABDEM_V1-2.tif" % (y, x))
    return out


def raster_set(sub, pattern, kind, box):
    """Проверить набор плиток. Вход: подпапка, шаблон имени, вид, рамка. Отдаёт: (доля рамки под плитками, список
    (путь, границы, пустое значение))."""
    import rasterio
    files = sorted(glob.glob(os.path.join(SRC, sub, pattern)))
    tiles = []   # (путь, границы, «нет данных», шаг) читаемых плиток
    for f in files:                                    # плитка за плиткой: границы и «нет данных»
        try:
            with rasterio.open(f) as ds:
                tiles.append((f, tuple(ds.bounds), ds.nodata, ds.res))
        except Exception as e:                         # файл не читается (недокачан?)
            print("    ❌ %s не читается: %s" % (os.path.basename(f), e))
    gx = np.arange(box[0], box[2], GRID_DEG)   # долготы точек сетки, градусы
    gy = np.arange(box[1], box[3], GRID_DEG)   # широты точек сетки, градусы
    X, Y = np.meshgrid(gx, gy)   # сетка точек рамки
    cov = np.zeros(X.shape, bool)                      # точки рамки под какой-либо плиткой
    for _f, b, _nd, _res in tiles:   # плитка за плиткой — отметить накрытые точки
        cov |= (X >= b[0]) & (X <= b[2]) & (Y >= b[1]) & (Y <= b[3])
    mb = sum(os.path.getsize(f) for f in files) / 2 ** 20   # объём набора, МБ
    print("  %s: файлов %d (%.0f МБ), читается %d; накрыто %.1f %% района с запасом %.0f км"
          % (kind, len(files), mb, len(tiles), 100 * cov.mean(), MARGIN_KM))
    have = {os.path.basename(f) for f in files}   # имена лежащих плиток
    miss = [n for n in tile_names(box, kind) if n not in have]   # нужные по §2.4, но не лежащие
    if cov.mean() < 1.0 and miss:                      # чего не хватает — по имени
        print("    недостаёт плиток: %s" % ", ".join(miss))
    if kind == "FABDEM" and glob.glob(os.path.join(SRC, sub, "*.zip")):   # архив лежит, плитки не извлечены
        print("    ⚠ в папке есть .zip — из архивов нужны только плитки .tif (§2.2)")
    return float(cov.mean()), tiles


def probe(tiles, box, kind):
    """Значения в окне у центра района. Вход: плитки, рамка, вид. Отдаёт: ничего (печатает строку)."""
    import rasterio
    from rasterio.windows import from_bounds
    cx, cy = 0.5 * (box[0] + box[2]), 0.5 * (box[1] + box[3])   # центр района, градусы
    for f, b, nd, _res in tiles:                       # плитка, накрывающая центр
        if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]:   # плитка накрывает центр — пробовать её
            with rasterio.open(f) as ds:
                # окно у центра района
                w = from_bounds(cx - WIN_DEG, cy - WIN_DEG, cx + WIN_DEG, cy + WIN_DEG, transform=ds.transform)
                a = ds.read(1, window=w.round_offsets().round_lengths()).astype(float)   # значения окна, м
            ok = np.isfinite(a) & (a != nd) if nd is not None else np.isfinite(a)   # есть данные
            v = a[ok]   # значения с данными, м
            if kind == "ETH":                          # кроны, м
                print("    проба у центра района (%.2f, %.2f): данных %.0f %%; деревья ≥ 5 м — %.0f %% пикселей, медиана "
                      "их высоты %.1f м (М10: в лесу OSM — 15.9 м)" % (cx, cy, 100 * ok.mean(), 100 * (v >= 5).mean(),
                                                                       np.median(v[v >= 5]) if (v >= 5).any() else 0))
                if (v >= 5).any() and np.median(v[v >= 5]) > 50:   # деревьев выше 50 м здесь нет — не тот файл
                    print("    ⚠ медиана выше 50 м — похоже не на кроны (рельеф или поверхность?)")
            else:                                      # земля, м
                print("    проба у центра района (%.2f, %.2f): данных %.0f %%; земля — медиана %.0f м, от %.0f до %.0f м"
                      % (cx, cy, 100 * ok.mean(), np.median(v) if len(v) else 0, v.min() if len(v) else 0,
                         v.max() if len(v) else 0))
            return
    print("    ⚠ ни одна плитка не накрывает центр района — пробы нет")


def gba_set(box):
    """Проверить файлы GBA. Вход: рамка. Отдаёт: число прочитанных файлов."""
    files = sorted(glob.glob(os.path.join(SRC, "GBA", "*.json")))
    n_ok = 0   # прочитано файлов
    for f in files:                                    # файл за файлом
        name = os.path.splitext(os.path.basename(f))[0]
        try:
            with open(f, encoding="utf-8") as fh:      # словарь «код постройки → {height: м}»
                d = json.load(fh)   # словарь «код постройки → запись»
        except Exception as e:                         # недокачан или не тот формат
            print("    ❌ %s не читается: %s" % (name, e))
            continue
        n_ok += 1
        keys = list(d)   # коды построек
        osm = [k for k in keys if k.startswith("osm")]   # постройки с контуром OSM — для каскада (§1.5)

        def h_of(v):
            """Высота из записи. Вход: запись. Отдаёт: м или NaN."""
            x = v.get("height", np.nan) if isinstance(v, dict) else v   # высота записи, м (−999 — нет)
            return float(x) if isinstance(x, (int, float)) else np.nan

        h = np.array([h_of(d[k]) for k in osm], float)   # высоты построек OSM, м
        okh = np.isfinite(h) & (h > 0)                 # высота есть (−999 — нет)
        print("  GBA %s (%.0f МБ): записей %d, из них OSM %d; с высотой %.1f %%, медиана %.1f м"
              % (name, os.path.getsize(f) / 2 ** 20, len(keys), len(osm), 100 * okh.mean() if len(h) else 0,
                 np.median(h[okh]) if okh.any() else 0))
    want = {"e040_n65_e045_n60", "e035_n65_e040_n60"}  # для arh (§2.2а)
    have = {os.path.splitext(os.path.basename(f))[0] for f in files}   # имена лежащих файлов
    if not files:                                      # не скачан
        print("  GBA: файлов нет (желательно; без него — этажи OSM и оценка по типу)")
    elif want - have:   # лежат не все нужные — назвать недостающие
        print("    недостаёт: %s" % ", ".join(sorted(want - have)))
    return n_ok


def main():
    """Проверить три набора. Вход: ключ `--src <папка>` — другая папка (для пробы скрипта). Отдаёт: код возврата."""
    global SRC
    if "--src" in sys.argv:                            # папка задана ключом
        SRC = os.path.abspath(sys.argv[sys.argv.index("--src") + 1])   # папка из ключа
    t0 = time.time()   # начало, с
    box = need_box()
    print("Файлы высот в %s · район с запасом %.0f км: %.2f–%.2f° в. д., %.2f–%.2f° с. ш."
          % (os.path.relpath(SRC, ROOT), MARGIN_KM, box[0], box[2], box[1], box[3]))
    if not os.path.isdir(SRC):                         # папки ещё нет
        print("НЕ НАЙДЕНО: папки нет — скачать по плану 16.5 §2")
        return 1
    cov_eth, eth = raster_set("ETH", "*_Map.tif", "ETH", box)   # доля района под кронами, плитки
    if eth:   # плитки есть — проба значений
        probe(eth, box, "ETH")
    cov_fab, fab = raster_set("FABDEM", "*_FABDEM_V1-2.tif", "FABDEM", box)   # доля района под FABDEM, плитки
    if fab:   # плитки есть — проба значений
        probe(fab, box, "FABDEM")
    gba_set(box)
    ok = cov_eth >= 1.0
    print("%s · %.0f с" % ("ВСЁ СОШЛОСЬ: кроны накрывают район" if ok else "РАСХОЖДЕНИЙ: кроны накрывают %.1f %% района"
                           % (100 * cov_eth), time.time() - t0))
    if ok and cov_fab < 1.0:                           # земля не полная — предупреждение
        print("⚠ FABDEM накрывает %.1f %% — без него под лесом остаётся поверхность Copernicus (план 16.5 §1.2)"
              % (100 * cov_fab))
    return 0 if ok else 1


if __name__ == "__main__":   # запуск скриптом, а не импортом
    sys.exit(main())
