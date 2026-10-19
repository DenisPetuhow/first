# -*- coding: utf-8 -*-
"""
СУММА ДВУХ ОБЛАСТЕЙ: векторные слои, рельеф и белая линия «где есть данные» (заказчик 19.10.2026).

ЗАЧЕМ. Для маршрутов сектора (план 16.01, 16.6) нужны данные от точки старта до района, а у районной области (`arh`)
они кончаются у её рамки. Большая область `3.Marshut` (Сумщина — Архангельская обл.) их покрывает. Заказчик: «сделай
область как на картинке, остальное просто совмести векторные слои и рельеф и добавь белую линию — где вообще есть
рельеф и векторы».

ЧТО ДЕЛАЕТ. Итоговая область = ОСНОВНАЯ (её рамка, район, вход, цель, якорь км-кадра — расчёт в районе прежний) плюс
данные ВТОРОЙ:
  * СЛОИ: основные — как есть; вторые — переведены в кадр основной (линейно, через градусы) и обрезаны по
    прямоугольнику данных основной: целиком внутри — выброшены, на краю — обрезаны (`shapely`). Иначе одна и та же
    река легла бы в вес дважды;
  * РЕЛЬЕФ: оба растра сшиты в один (`pack_area.merge_dem`), основной главнее на перекрытии;
  * `область.txt`: «карта» — объединение, «данные» — оба прямоугольника данных (белая линия на карте);
  * справочник типов и места датчиков — копии основной (они нужны только в районе).

    python tools/merge_areas.py geo_cache/arh geo_cache/3.Marshut geo_cache/4.arh_analitic
    python tools/merge_areas.py geo_cache/arh geo_cache/3.Marshut geo_cache/4.arh_analitic --only layers   # dem, file
"""
import argparse
import os
import shutil
import sys
import time

import numpy as np

if hasattr(sys.stdout, "reconfigure"):              # консоль Windows — кириллица без порчи
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TOOLS = os.path.dirname(os.path.abspath(__file__))   # папка инструментов
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.dirname(TOOLS))
sys.path.insert(0, os.path.join(os.path.dirname(TOOLS), "src"))   # код программы — в src/

import area_file as af  # noqa: E402
from model.threat.geo_frame import bbox_lonlat_to_km, km_to_lonlat, lonlat_to_km  # noqa: E402
from pack_area import _load_layers, _save_layers, merge_dem  # noqa: E402

AREAL = ("built_up", "place_poly")                   # площадные слои — кольца, режутся как площади
OUT_LAYERS = "threat_layers_%s.npz"                  # имя кэша слоёв итога (с «threat_layers» — для мест датчиков)


def log(msg):
    """Строка хода работы со временем. Вход: текст. Отдаёт: ничего."""
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def data_box(folder, rec):
    """Прямоугольник данных области — границы её рельефа (на них же обрезан экстракт OSM). Вход: папка, запись.
    Отдаёт: (lon_min, lat_min, lon_max, lat_max) градусы; растра нет — рамка области."""
    import rasterio
    p = os.path.join(folder, str(rec.get("dem") or "") + ".tif")   # растр рельефа области
    if not rec.get("dem") or not os.path.isfile(p):  # рельефа нет — данные по рамке
        return tuple(rec["bbox"])
    with rasterio.open(p) as d:
        b = d.bounds                                 # края растра, градусы
    return (b.left, b.bottom, b.right, b.top)


def union(*boxes):
    """Рамка, накрывающая все. Вход: рамки (lon_min, lat_min, lon_max, lat_max). Отдаёт: рамку."""
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _to_frame(arrs, src, dst):
    """Массивы точек из км-кадра src в км-кадр dst (якоря — градусы). Вход: список (N, 2|3), (lon0, lat0) ×2.
    Отдаёт: новые массивы того же вида; третий столбец (код) не трогается."""
    if not arrs:                                     # переводить нечего
        return []
    lens = [len(a) for a in arrs]                    # точек в каждом объекте
    allp = np.concatenate([np.asarray(a, np.float64) for a in arrs])   # все точки слоя подряд
    lon, lat = km_to_lonlat(allp[:, 0], allp[:, 1], src[0], src[1])    # градусы
    allp[:, 0], allp[:, 1] = lonlat_to_km(lon, lat, dst[0], dst[1])    # км кадра итога
    out = np.split(allp, np.cumsum(lens)[:-1])       # обратно по объектам
    return [o.astype(np.asarray(a).dtype) for o, a in zip(out, arrs)]


def _clip_parts(arr, box_km, areal):
    """Часть объекта вне прямоугольника. Вход: (N, 2|3) км, (x0, x1, y0, y1) км, площадной ли.
    Отдаёт: список массивов — кусков снаружи (площадь — внешние кольца)."""
    import shapely
    from shapely.geometry import LineString, Polygon, box
    a = np.asarray(arr, np.float64)                  # точки объекта
    hole = box(box_km[0], box_km[2], box_km[1], box_km[3])   # прямоугольник данных основной области
    if areal:                                        # кольцо — разность площадей
        if len(a) < 4:                               # вырожденное кольцо
            return []
        g = shapely.make_valid(Polygon(a[:, :2])).difference(hole)   # площадь вне прямоугольника
        polys = [p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon" and not p.is_empty]   # её куски
        rings = [np.asarray(p.exterior.coords) for p in polys]   # внешние кольца кусков
    else:                                            # линия — разность линий
        if len(a) < 2:                               # точка вместо линии
            return []
        g = LineString(a[:, :2]).difference(hole)    # линия вне прямоугольника
        rings = [np.asarray(p.coords) for p in getattr(g, "geoms", [g]) if p.geom_type == "LineString" and not p.is_empty]
    if a.shape[1] > 2:                               # третий столбец — код объекта: тот же у всех точек куска
        rings = [np.column_stack([r, np.full(len(r), a[0, 2])]) for r in rings]
    return [r.astype(np.asarray(arr).dtype) for r in rings if len(r) >= 2]


def outside(arrs, box_km, areal):
    """Объекты вне прямоугольника: снаружи — как есть, внутри — прочь, на краю — обрезаны. Вход: список (N, 2|3) км,
    (x0, x1, y0, y1) км, площадной ли. Отдаёт: (оставленные массивы, сводка: снаружи, внутри, обрезано)."""
    x0, x1, y0, y1 = box_km                          # края прямоугольника данных основной, км
    keep, n_out, n_in, n_cut = [], 0, 0, 0           # итог и счётчики
    for a in arrs:                                   # объект за объектом
        if len(a) == 0:                              # пустой — пропустить
            continue
        mn, mx = a[:, :2].min(axis=0), a[:, :2].max(axis=0)   # габарит объекта, км
        if mx[0] < x0 or mn[0] > x1 or mx[1] < y0 or mn[1] > y1:   # целиком снаружи
            keep.append(a); n_out += 1
        elif mn[0] >= x0 and mx[0] <= x1 and mn[1] >= y0 and mx[1] <= y1:   # целиком внутри — там свои данные
            n_in += 1
        else:                                        # на краю — оставить только кусок снаружи
            keep += _clip_parts(a, box_km, areal); n_cut += 1
    return keep, (n_out, n_in, n_cut)


def merge_layers(base_dir, base, extra_dir, extra, out_path, base_km):
    """Слои итога: основные как есть + вторые в кадре основной вне её данных. Вход: папки и записи двух областей,
    путь итога, прямоугольник данных основной (x0, x1, y0, y1) км. Отдаёт: ничего — пишет упакованный .npz."""
    from model.threat.threat_grid import PLACE_LAYER, PLACE_NAME_LAYER
    t0 = time.time()                                 # начало шага
    b = _load_layers(os.path.join(base_dir, base["layers"]))     # слои основной, её кадр
    e = _load_layers(os.path.join(extra_dir, extra["layers"]))   # слои второй, её кадр
    log("слои прочитаны: основная %d слоёв, вторая %d (%.0f с)" % (len(b), len(e), time.time() - t0))
    src, dst = tuple(extra["bbox"][:2]), tuple(base["bbox"][:2])   # якоря кадров: второй и итога (= основной)
    out = {n: list(v) for n, v in b.items() if n not in (PLACE_LAYER, PLACE_NAME_LAYER)}   # основные — как есть
    print("  %-12s %9s %10s %9s %9s" % ("слой", "основная", "вторая", "внутри", "обрезано"))
    for name, arrs in e.items():                     # слой второй области
        if name in (PLACE_LAYER, PLACE_NAME_LAYER):  # пункты — отдельно ниже
            continue
        moved = _to_frame(arrs, src, dst)            # в кадр итога
        keep, (n_out, n_in, n_cut) = outside(moved, base_km, name in AREAL)   # оставленное и сводка
        out.setdefault(name, []).extend(keep)
        print("  %-12s %9d %10d %9d %9d" % (name, len(b.get(name, [])), n_out + n_cut, n_in, n_cut))
    # ПУНКТЫ: точки (N, 3) и имена парой — у второй только те, что вне данных основной
    pb = np.asarray(b.get(PLACE_LAYER, [np.empty((0, 3))])[0])          # точки основной
    nb = np.asarray(b.get(PLACE_NAME_LAYER, [np.empty(0, "U1")])[0])    # их имена
    pe = _to_frame([np.asarray(e.get(PLACE_LAYER, [np.empty((0, 3))])[0])], src, dst)[0]   # точки второй, кадр итога
    ne = np.asarray(e.get(PLACE_NAME_LAYER, [np.empty(0, "U1")])[0])    # их имена
    x0, x1, y0, y1 = base_km                         # края прямоугольника данных основной, км
    far = ~((pe[:, 0] >= x0) & (pe[:, 0] <= x1) & (pe[:, 1] >= y0) & (pe[:, 1] <= y1))   # пункт вне данных основной
    out[PLACE_LAYER] = [np.concatenate([pb, pe[far]]).astype(np.float32)]
    out[PLACE_NAME_LAYER] = [np.concatenate([nb.astype("U"), ne[far].astype("U")])]
    print("  %-12s %9d %10d %9d" % ("пункты", len(pb), int(far.sum()), int((~far).sum())))
    log("слои сведены за %.0f с — пишу %s" % (time.time() - t0, out_path))
    _save_layers(out_path, out, packed=True)
    log("слои готовы: %.1f МБ" % (os.path.getsize(out_path) / 2 ** 20))


def area_text(base, out_box_view, boxes, layers, dem, label):
    """Текст `область.txt` итога. Вход: запись основной, рамка карты, прямоугольники данных, имена слоёв и рельефа,
    подпись. Отдаёт: строку файла."""
    def box(b):
        """Рамка пятью знаками — как в файлах областей. Вход: 4 числа, градусы. Отдаёт: строку."""
        return ", ".join("%.5f" % v for v in b)

    lines = ["# Область: сумма двух (tools/merge_areas.py, заказчик 19.10.2026). Координаты — градусы:",
             "# lon_min, lat_min, lon_max, lat_max. Область, район, вход и цель — как у основной: расчёт в районе прежний;",
             "# «данные» — где есть рельеф и векторы (белая линия на карте), «карта» — их объединение.",
             "название = %s" % label,
             "область  = %s" % box(base["bbox"]),
             "карта    = %s" % box(out_box_view),
             "данные   = %s" % " | ".join(box(b) for b in boxes)]   # строки файла
    if base.get("work"):                             # район основной
        lines.append("район    = %s" % box(base["work"]))
    lines += ["слои     = %s" % layers, "рельеф   = %s" % dem]
    for key, field in (("вход", "entry"), ("цель", "target")):   # точки основной
        lab, lon, lat = base[field]                  # подпись и координаты, градусы
        lines.append("%-8s = %s, %.4f, %.4f" % (key, lab, lon, lat))
    return "\n".join(lines) + "\n"


def main(argv=None):
    """Разбор ключей и шаги: файл, слои, рельеф. Вход: argv (None — командная строка). Отдаёт: код 0."""
    ap = argparse.ArgumentParser(description="сумма двух областей: слои, рельеф, белая линия данных")   # ключи
    ap.add_argument("base", help="основная область (её район, рамка и якорь)")
    ap.add_argument("extra", help="вторая область — её данные вне основной")
    ap.add_argument("out", help="папка итога")
    ap.add_argument("--only", choices=("file", "layers", "dem"), default=None, help="один шаг")
    a = ap.parse_args(argv)                          # разобранные ключи
    base, extra = af.read_area_folder(a.base), af.read_area_folder(a.extra)   # записи двух областей
    name = os.path.basename(os.path.abspath(a.out).rstrip("/\\"))   # имя итога = имя папки и папки тайлов
    stem = name.split(".", 1)[-1]                    # имя файлов без номера с точкой: «arh_analitic»
    layers, dem = OUT_LAYERS % stem, stem + "_dem"   # имена кэша слоёв и рельефа итога
    bb, eb = data_box(a.base, base), data_box(a.extra, extra)   # прямоугольники данных, градусы
    os.makedirs(a.out, exist_ok=True)
    if a.only in (None, "file"):                     # файл области, справочник, места датчиков
        view = union(base.get("view") or base["bbox"], extra.get("view") or extra["bbox"], bb, eb)   # рамка карты
        label = "%s + %s — анализ" % (os.path.basename(os.path.abspath(a.base)),   # подпись области
                                     os.path.basename(os.path.abspath(a.extra)))
        with open(os.path.join(a.out, af.AREA_FILE_NAME), "w", encoding="utf-8", newline="\n") as f:
            f.write(area_text(base, view, [bb, eb], layers, dem, label))
        from model.placement.sensor_zones import file_name
        # копии основной: места датчиков (под новым именем кэша) и справочник типов — они нужны только в районе
        for src, dst in ((file_name(base["layers"]), file_name(layers)), ("типы_датчиков.json", "типы_датчиков.json")):
            if os.path.isfile(os.path.join(a.base, src)):   # у основной есть — копия
                shutil.copy2(os.path.join(a.base, src), os.path.join(a.out, dst))
        log("файл области и копии основной записаны: %s" % a.out)
    if a.only in (None, "layers"):                   # векторные слои
        merge_layers(a.base, base, a.extra, extra, os.path.join(a.out, layers),
                     bbox_lonlat_to_km(bb, base["bbox"][0], base["bbox"][1]))
    if a.only in (None, "dem"):                      # рельеф: основной первым — он главнее на перекрытии
        merge_dem(a.out, dem, paths=[os.path.join(a.base, base["dem"] + ".tif"),
                                     os.path.join(a.extra, extra["dem"] + ".tif")])
    return 0


if __name__ == "__main__":                           # запуск из командной строки
    sys.exit(main())
