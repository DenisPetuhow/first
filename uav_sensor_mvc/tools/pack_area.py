# -*- coding: utf-8 -*-
"""
УПАКОВКА ОБЛАСТИ: несколько скачанных файлов -> по одному файлу на вид данных.

ЗАЧЕМ (заказчик 13.09.2026, область `3.Marshut`). Большую область качают кусками:
векторные слои — тремя `.pbf` полосами по широте, рельеф — двумя `.tif`. Программа же
берёт ОДИН файл слоёв (`.npz`) и ОДИН растр рельефа (`область.txt` → `слои`, `рельеф`).
Прежний `build_threat_grid.py` умел ровно один исходник, склейки не было нигде.

ЧТО ДЕЛАЕТ — два шага, каждый можно запускать отдельно:

  * РЕЛЬЕФ: все `.tif` папки сшиваются в один GeoTIFF. Сетки обязаны совпадать (та же
    проекция, шаг и выравнивание пикселей) — иначе сшивка сдвинула бы высоты, и скрипт
    откажет с причиной. Пишется построчными кусками: целиком 5 ГБ в память не берутся;
  * СЛОИ: каждый `.pbf` разбирается тем же `layers_from_osm`, что и у одного исходника,
    в км-фрейме ОБЛАСТИ (якорь — её юго-западный угол). Промежуточный результат по
    каждому файлу кладётся в `_части/` — прерванный прогон не начинается заново, а файлы
    можно разбирать параллельно (`--pbf`). Затем части сливаются в УПАКОВАННЫЙ `.npz`.

⚠️ ДУБЛИ НА СТЫКАХ. Дорога, пересекающая границу полос, лежит целиком в ОБОИХ соседних
`.pbf`. Без очистки её длина вошла бы в вес дважды — ровно на стыке появилась бы ложная
«полоса коридоров». Дубль — та же ломаная с точностью до 1 м; такие выбрасываются.

    python tools/pack_area.py geo_cache/3.Marshut                  # рельеф и слои
    python tools/pack_area.py geo_cache/3.Marshut --only dem       # только рельеф
    python tools/pack_area.py geo_cache/3.Marshut --pbf Mar_2.pbf  # одна часть слоёв
    python tools/pack_area.py geo_cache/3.Marshut --only layers    # доделать части и слить

Имена выходных файлов — из `область.txt` (`слои`, `рельеф`). Разбор —
теория/код/УПАКОВКА_ОБЛАСТИ.md, план 10 §10.5.8 и 10.13.
"""
import argparse
import glob
import hashlib
import os
import sys
import time

import numpy as np

if hasattr(sys.stdout, "reconfigure"):          # консоль Windows cp1251 роняет «→» и «×»
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import area_file as af                                                    # noqa: E402

PARTS_DIR = "_части"          # промежуточные слои по каждому .pbf
DEDUP_KM = 1e-3               # точность сравнения дублей: 1 м в км
DEM_CHUNK_ROWS = 2048         # строк растра за один проход: 2048 × 33 528 × 4 Б ≈ 275 МБ


def log(msg):
    # Печать со временем: прогон идёт десятки минут, и видно, что он жив.
    # Вход: текст. Отдаёт: ничего.
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def _files(folder, pattern):
    # Файлы по маске в папке области и в её `_части/`, без повторов по имени.
    # Вход: папка области, маска («*.pbf»). Отдаёт: список полных путей.
    # ⚠️ исходники после упаковки убирают в `_части/` (заказчик 13.09.2026 так и сделал):
    # искать только в корне — значит молча потерять полосу, лежащую уже там
    seen, out = set(), []                            # уже взятые имена; найденные пути
    for d in (folder, os.path.join(folder, PARTS_DIR)):
        for p in sorted(glob.glob(os.path.join(d, pattern))):
            if os.path.basename(p) not in seen:      # имя ещё не встречалось
                seen.add(os.path.basename(p)); out.append(p)   # — берём (корень раньше `_части/`)
    return out


def _part_path(folder, pbf):
    # Где лежит часть слоёв этого .pbf. Вход: папка области, путь к .pbf.
    # Отдаёт: путь в `_части/` или в корне, где часть есть; нет нигде — путь в `_части/`.
    name = os.path.basename(pbf) + ".npz"            # имя части: <файл>.pbf.npz
    for d in (os.path.join(folder, PARTS_DIR), folder):
        if os.path.exists(os.path.join(d, name)):    # часть уже собрана и лежит здесь
            return os.path.join(d, name)             # — её и берём
    return os.path.join(folder, PARTS_DIR, name)


# ----------------------------------------------------------------------
# Рельеф
# ----------------------------------------------------------------------
def merge_dem(folder, out_name):
    # Сшить все .tif папки (и `_части/`) в один GeoTIFF. Вход: папка области, имя итога
    # без расширения. Отдаёт: путь итога; сетки не совпадают — SystemExit с причиной.
    import rasterio
    from rasterio.transform import from_origin

    out = os.path.join(folder, out_name + ".tif")    # путь итогового растра
    paths = [p for p in _files(folder, "*.tif")
             if os.path.basename(p).lower() != os.path.basename(out).lower()]   # итог сам с собой не сшиваем
    if len(paths) < 2:                               # меньше двух растров
        raise SystemExit("рельеф: в папке меньше двух .tif — сшивать нечего (%d)" % len(paths))
    srcs = [rasterio.open(p) for p in paths]         # открытые растры-куски
    try:
        a = srcs[0]                                  # образец сетки — первый кусок
        rx, ry = a.res                               # шаг пикселя, градусы
        for s in srcs[1:]:
            if s.crs != a.crs or s.dtypes[0] != a.dtypes[0]:     # другая проекция или тип
                raise SystemExit("рельеф: %s не совпадает с %s по проекции или типу"
                                 % (os.path.basename(s.name), os.path.basename(a.name)))
            if abs(s.res[0] - rx) > 1e-12 or abs(s.res[1] - ry) > 1e-12:   # другой шаг пикселя
                raise SystemExit("рельеф: у %s другой шаг пикселя %s против %s"
                                 % (os.path.basename(s.name), s.res, a.res))
        left = min(s.bounds.left for s in srcs)      # западный край итога, градусы
        top = max(s.bounds.top for s in srcs)        # северный край итога, градусы
        right = max(s.bounds.right for s in srcs)    # восточный край итога, градусы
        bottom = min(s.bounds.bottom for s in srcs)  # южный край итога, градусы
        W = int(round((right - left) / rx))          # ширина итога, пикселей
        H = int(round((top - bottom) / ry))          # высота итога, пикселей
        place = []                                   # (растр, строка, столбец) в итоговой сетке
        for s in srcs:
            c = (s.bounds.left - left) / rx          # сдвиг куска по столбцам, пикселей
            r = (top - s.bounds.top) / ry            # сдвиг куска по строкам, пикселей
            # сдвиг обязан быть целым числом пикселей: иначе высоты съедут на долю шага
            if abs(c - round(c)) > 0.01 or abs(r - round(r)) > 0.01:   # дробный сдвиг
                raise SystemExit("рельеф: %s смещён на дробную долю пикселя (%.3f, %.3f)"
                                 % (os.path.basename(s.name), c, r))
            place.append((s, int(round(r)), int(round(c))))
        log("рельеф: %d файла -> %d × %d пикселей (%.1f ГБ без сжатия)"
            % (len(srcs), W, H, W * H * 4 / 2 ** 30))

        prof = a.profile.copy()                      # параметры записи — от первого куска
        prof.update(driver="GTiff", width=W, height=H, count=1, nodata=None,
                    transform=from_origin(left, top, rx, ry), tiled=True,
                    blockxsize=256, blockysize=256, compress="lzw", BIGTIFF="YES",
                    NUM_THREADS="ALL_CPUS")
        tmp = out + ".tmp"                           # пишем во временный: оборванный прогон итог не портит
        with rasterio.open(tmp, "w", **prof) as dst:
            # итог пишется полосами по DEM_CHUNK_ROWS строк: целиком 5 ГБ в память не берутся
            for r0 in range(0, H, DEM_CHUNK_ROWS):
                n = min(DEM_CHUNK_ROWS, H - r0)      # строк в этой полосе
                buf = np.full((n, W), np.nan, np.float32)   # полоса итога, пока пустая
                for s, sr, sc in place:
                    lo, hi = max(r0, sr), min(r0 + n, sr + s.height)   # общие строки полосы и куска
                    if hi <= lo:                     # кусок эту полосу не покрывает
                        continue                     # — следующий
                    part = s.read(1, window=((lo - sr, hi - sr), (0, s.width)))   # высоты куска, м
                    tgt = buf[lo - r0:hi - r0, sc:sc + s.width]   # его место в полосе (вид)
                    take = np.isnan(tgt) & np.isfinite(part)      # первый кусок важнее: не затираем
                    tgt[take] = part[take]
                dst.write(buf, 1, window=((r0, r0 + n), (0, W)))
                log("  рельеф: строки %d…%d из %d" % (r0, r0 + n, H))
        os.replace(tmp, out)
    finally:
        for s in srcs:
            s.close()
    log("рельеф готов: %s (%.2f ГБ)" % (out, os.path.getsize(out) / 2 ** 30))
    return out


# ----------------------------------------------------------------------
# Слои
# ----------------------------------------------------------------------
def _save_layers(path, layers, packed=False):
    # Слои в .npz. Вход: путь, dict имя -> список массивов (км), packed — упакованно
    # (`threat_grid.packed_npz_arrays`, два массива на слой). Отдаёт: ничего.
    if packed:                                       # итоговый файл области
        from model.threat_grid import packed_npz_arrays
        out = packed_npz_arrays(layers)              # массивы файла: <слой>@xy и <слой>@off
    else:                                            # промежуточная часть
        out = {}                                     # массивы файла: <слой>__<i> на каждую линию
        for name, parts in layers.items():
            for i, arr in enumerate(parts):
                a = np.asarray(arr)
                # названия пунктов — строки, их к float32 не приводим
                out["%s__%d" % (name, i)] = a if a.dtype.kind in "US" else a.astype(np.float32)
    tmp = path + ".tmp.npz"                          # временный: оборванная запись итог не портит
    np.savez_compressed(tmp, **out)
    os.replace(tmp, path)


def _load_layers(path):
    # Слои из .npz. Вход: путь. Отдаёт: dict имя -> список массивов В ПОРЯДКЕ номеров
    # (у точек пунктов и их имён порядок обязан совпадать).
    d = np.load(path, allow_pickle=False)
    if any("@" in k for k in d.files):               # файл упакованный
        from model.threat_grid import _load_layers_npz
        return _load_layers_npz(path)                # — общим загрузчиком модели
    keyed = {}                                       # имя -> [(номер, массив)]
    for key in d.files:
        name, _sep, idx = key.rpartition("__")
        keyed.setdefault(name, []).append((int(idx), d[key]))
    return {n: [a for _i, a in sorted(v, key=lambda t: t[0])] for n, v in keyed.items()}


# ТРУДОЁМКОСТЬ СЛОЁВ для раскладки по ядрам — число объектов в полосе `Mar_1` (Москва,
# 13.09.2026). Не точная цена, а весы: чтобы тяжёлые слои не собрались в один процесс.
LAYER_COST = {"track": 377327, "road_local": 232705, "built_up": 132380, "stream": 34479,
              "road_major": 26450, "ditch": 23277, "railway": 22709, "power": 20033,
              "place": 15305, "tree_row": 12383, "bridge": 11828, "river": 6685,
              "cutline": 4716, "railway_old": 1883, "pipeline": 1540}


def _groups(names, jobs):
    # Разложить слои по процессам с близкой трудоёмкостью. Вход: имена слоёв ("place" —
    # пункты), число процессов. Отдаёт: список множеств имён, без пустых.
    groups = [(0, set()) for _ in range(max(1, jobs))]   # (суммарная трудоёмкость, имена)
    # жадно: слои от тяжёлого к лёгкому, каждый — в самую лёгкую на этот момент группу
    for n in sorted(names, key=lambda k: -LAYER_COST.get(k, 1)):
        i = min(range(len(groups)), key=lambda j: groups[j][0])   # номер самой лёгкой группы
        cost, members = groups[i]
        members.add(n)
        groups[i] = (cost + LAYER_COST.get(n, 1), members)
    return [m for _c, m in groups if m]


def _worker(args):
    # Тело процесса по ядру: своя группа слоёв из того же файла тем же кодом.
    # Вход: (путь .pbf, lon0, lat0 град, рамка град, имена слоёв). Отдаёт: (dict слоёв, секунды).
    pbf, lon0, lat0, bbox, names = args
    from model.threat_grid import layers_from_osm
    t0 = time.time()                                 # начало работы процесса
    only = {n for n in names if n != "place"}        # настоящие слои группы, без пунктов
    out = layers_from_osm(pbf, lon0, lat0, bbox, only_layers=only, with_places="place" in names)
    return out, time.time() - t0


def build_part(pbf, folder, lon0, lat0, bbox, force=False, jobs=1, part=None):
    # Слои одного .pbf -> часть `_части/<файл>.npz`; свежая часть не пересобирается.
    # Вход: .pbf, папка, якорь lon0/lat0 и рамка (град), force, jobs — процессов, part — свой путь.
    # Отдаёт: путь части.
    from model.threat_grid import layers_from_osm, OSM_LAYER_FILTERS
    part = part or _part_path(folder, pbf)           # куда писать часть
    if (not force and os.path.exists(part)
            and os.path.getmtime(part) >= os.path.getmtime(pbf)):   # часть есть и новее исходника
        log("слои: %s — часть уже есть, пропуск" % os.path.basename(pbf))
        return part                                  # — не пересобираем
    t0 = time.time()                                 # начало разбора
    log("слои: разбираю %s (%.0f МБ), процессов %d…"
        % (os.path.basename(pbf), os.path.getsize(pbf) / 2 ** 20, jobs))
    if jobs <= 1:                                    # одним процессом
        layers = layers_from_osm(pbf, lon0, lat0, bbox)   # все слои и пункты
    else:                                            # по ядрам
        # ⚠️ делим СЛОИ, а не карту: линия, обрезанная по краю квадрата, в двух кусках
        # разная, и дубли уже не узнать. Группы слоёв тем же кодом дают итог, совпадающий с
        # последовательным побайтово. Цена — каждый процесс читает файл сам (память ×jobs).
        # Замер 13.09.2026: 4.2 → 5.1 мин — время съедает чтение (УПАКОВКА_ОБЛАСТИ §6.8).
        from concurrent.futures import ProcessPoolExecutor
        groups = _groups(list(OSM_LAYER_FILTERS) + ["place"], jobs)   # слои по процессам
        layers = {}                                  # собранные слои всех процессов
        with ProcessPoolExecutor(max_workers=len(groups)) as ex:
            for got, sec in ex.map(_worker, [(pbf, lon0, lat0, bbox, sorted(g)) for g in groups]):
                layers.update(got)                   # группы не пересекаются по слоям
                log("  процесс готов за %.1f мин: %s" % (sec / 60, ", ".join(sorted(got))))
        from model.threat_grid import PLACE_LAYER, PLACE_NAME_LAYER, PLACE_POLY_LAYER
        order = list(OSM_LAYER_FILTERS) + [PLACE_LAYER, PLACE_NAME_LAYER, PLACE_POLY_LAYER]   # порядок слоёв
        layers = {n: layers[n] for n in order if n in layers}   # как у последовательного разбора
    os.makedirs(os.path.dirname(part), exist_ok=True)
    _save_layers(part, layers)
    log("слои: %s готов за %.1f мин: %s" % (
        os.path.basename(pbf), (time.time() - t0) / 60,
        ", ".join("%s %d" % (n, len(v)) for n, v in sorted(layers.items()))))
    return part


def _key(arr):
    # Отпечаток ломаной для поиска дублей. Вход: массив (N,2|3), км.
    # Отдаёт: 16 байт; одинаковые с точностью DEDUP_KM ломаные дают один отпечаток.
    r = np.round(np.asarray(arr, np.float64) / DEDUP_KM).astype(np.int64)   # координаты в метрах, целые
    return hashlib.blake2b(r.tobytes() + str(r.shape).encode(), digest_size=16).digest()


def _length_km(arr):
    # Длина ломаной. Вход: массив (N,2|3), первые два столбца — x, y в км. Отдаёт: км, float.
    a = np.asarray(arr, np.float64)                  # точки ломаной, км
    return float(np.hypot(*np.diff(a[:, :2], axis=0).T).sum()) if len(a) > 1 else 0.0


def merge_parts(parts, out_path):
    # Слить части в один упакованный .npz, выбросив дубли со стыков полос.
    # Вход: пути частей, путь итога. Отдаёт: (сводка слой -> [частей, дублей, км, км дублей], пунктов).
    from model.threat_grid import PLACE_LAYER, PLACE_NAME_LAYER
    out, seen, stat = {}, {}, {}                     # итог; отпечатки по слоям; сводка
    pts, names, seen_pts = [], [], set()             # точки пунктов, их имена, уже взятые пункты
    for part in parts:
        layers = _load_layers(part)                  # слои одной части
        if PLACE_LAYER in layers:                    # в части есть пункты
            p = np.asarray(layers[PLACE_LAYER][0])   # точки (N,3): x, y км, код типа
            nm = (layers.get(PLACE_NAME_LAYER) or [np.empty(0, "U")])[0]   # имена, в том же порядке
            for i in range(len(p)):
                label = str(nm[i]) if i < len(nm) else ""   # имя пункта или ""
                # пункт — по координатам (1 м), типу И имени: точка и подпись идут парой
                k = (int(round(p[i, 0] / DEDUP_KM)), int(round(p[i, 1] / DEDUP_KM)),
                     int(p[i, 2]), label)
                if k in seen_pts:                    # тот же пункт из соседней полосы
                    continue                         # — не дублируем
                seen_pts.add(k); pts.append(p[i]); names.append(label)
        for name, arrs in layers.items():
            if name in (PLACE_LAYER, PLACE_NAME_LAYER):   # пункты уже разобраны выше
                continue
            s = stat.setdefault(name, [0, 0, 0.0, 0.0])   # частей, дублей, км всего, км дублей
            keys = seen.setdefault(name, set())      # отпечатки линий этого слоя
            for a in arrs:
                k = _key(a)                          # отпечаток линии
                L = _length_km(a)                    # её длина, км
                s[0] += 1; s[2] += L
                if k in keys:                        # дубль со стыка
                    s[1] += 1; s[3] += L             # — в вес не пускаем, только считаем
                    continue
                keys.add(k)
                out.setdefault(name, []).append(a)
    if pts:                                          # пункты нашлись
        out[PLACE_LAYER] = [np.asarray(pts, np.float32)]
        out[PLACE_NAME_LAYER] = [np.asarray(names, dtype="U")]
    # ⚠️ итог — УПАКОВАННО: прежний формат на Marshut дал 2.6 млн массивов в 741 МБ, и модель
    # читала файл десятки минут (МЕТОДИЧКА_ФОРМАТЫ_PBF_И_NPZ §3.1а)
    _save_layers(out_path, out, packed=True)
    return stat, len(pts)


def pack_layers(folder, rec, pbf_only, force, jobs=1):
    # Шаг «слои»: собрать нужные части и, если собраны ВСЕ, слить в файл `слои`.
    # Вход: папка, запись области, имена .pbf (пусто — все), force, jobs. Отдаёт: путь итога или None.
    lon0, lat0 = rec["bbox"][0], rec["bbox"][1]      # якорь — юго-западный угол ОБЛАСТИ, градусы
    pbfs = _files(folder, "*.pbf")                   # исходники: и в корне, и в `_части/`
    if not pbfs:                                     # исходников нет
        raise SystemExit("слои: в папке и в %s нет .pbf" % PARTS_DIR)
    log("слои: исходники %s" % ", ".join(os.path.basename(p) for p in pbfs))
    todo = [p for p in pbfs if not pbf_only or os.path.basename(p) in pbf_only]   # что разбирать сейчас
    for p in todo:
        build_part(p, folder, lon0, lat0, rec["bbox"], force, jobs)
    parts = [_part_path(folder, p) for p in pbfs]    # части всех исходников
    missing = [os.path.basename(p) for p in parts if not os.path.exists(p)]   # ещё не собранные
    if missing:                                      # не все части готовы
        # сливать рано: итог молча вышел бы без полосы
        log("слои: ещё не собраны части %s — слияние позже" % ", ".join(missing))
        return None
    out_name = rec.get("layers") or "threat_layers_%s.npz" % rec["tile_area"]   # имя из область.txt
    out_path = os.path.join(folder, out_name)
    stat, n_pts = merge_parts(parts, out_path)
    log("слои слиты: %s (%.1f МБ), пунктов %d" % (out_path, os.path.getsize(out_path) / 2 ** 20, n_pts))
    print("  %-14s %8s %8s %12s %12s" % ("слой", "частей", "дублей", "км всего", "км дублей"))
    for name, (n, dup, km, dkm) in sorted(stat.items()):
        print("  %-14s %8d %8d %12.1f %12.1f" % (name, n, dup, km, dkm))
    return out_path


def main(argv=None):
    # Разбор ключей и запуск шагов. Вход: argv (None — из командной строки). Отдаёт: код 0.
    ap = argparse.ArgumentParser(description="упаковать область: рельеф и слои в один файл")
    ap.add_argument("folder", help="папка области с файлом «%s»" % af.AREA_FILE_NAME)
    ap.add_argument("--only", choices=("dem", "layers"), default=None,
                    help="только рельеф или только слои")
    ap.add_argument("--pbf", action="append", default=None,
                    help="разобрать только этот .pbf (можно повторять) — для параллельных прогонов")
    ap.add_argument("--force", action="store_true", help="пересобрать части слоёв заново")
    ap.add_argument("--jobs", type=int, default=1,
                    help="процессов по ядрам на один .pbf (слои делятся по группам); "
                         "⚠️ замер 13.09.2026: НЕ ускоряет (4.2 -> 5.1 мин) — время съедает "
                         "чтение файла; память — как у полного разбора на КАЖДЫЙ процесс")
    a = ap.parse_args(argv)                          # разобранные ключи

    try:
        rec = af.read_area_folder(a.folder)          # запись области: рамка и имена итогов
    except af.AreaFileError as e:                    # без файла области нет рамки и имён
        raise SystemExit("область не прочитана: %s" % e)
    log("область %s: %s" % (rec["label"], rec["bbox"]))
    if a.only in (None, "dem") and not a.pbf:        # шаг рельефа нужен
        if not rec.get("dem"):                       # имя итога не задано
            raise SystemExit("в «%s» не задан «рельеф» — имя сшитого файла" % af.AREA_FILE_NAME)
        merge_dem(a.folder, rec["dem"])
    if a.only in (None, "layers"):                   # шаг слоёв нужен
        pack_layers(a.folder, rec, set(a.pbf or ()), a.force, max(1, a.jobs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
