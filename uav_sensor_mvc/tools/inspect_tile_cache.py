# -*- coding: utf-8 -*-
"""
Диагностика СВОЕЙ папки с тайлами (например, экспорт SAS.Planet вроде "Sat").

Запустите ЛОКАЛЬНО (на машине, где лежит папка) — скрипт:
  1) печатает дерево каталогов (несколько первых уровней);
  2) пробует открыть несколько файлов, печатает формат/размер;
  3) для зума по вашему выбору проверяет ОБЕ гипотезы раскладки оси Y (XYZ и
     TMS), вычисляя ожидаемый номер тайла для Курска и проверяя, какой из двух
     файлов реально существует на диске — так определяется XYZ это или TMS;
  4) печатает итоговую рекомендацию: какие переменные окружения выставить.

Пример:
  python tools/inspect_tile_cache.py --root "C:/SAS.Planet/cache_old/Sat" --zoom 2
  python tools/inspect_tile_cache.py --root /home/user/Sat --zoom 8
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from view_qt import geomap as gm   # noqa: E402


def _list_dirs(path):
    try:
        return sorted([d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))],
                     key=lambda s: (len(s), s))
    except OSError as e:
        return [f"<ошибка: {e}>"]


def _list_files(path):
    try:
        return sorted(f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f)))
    except OSError as e:
        return [f"<ошибка: {e}>"]


def print_tree(root, max_dirs=6, depth=3):
    print(f"\n=== ДЕРЕВО КАТАЛОГОВ: {root} ===")
    if not os.path.isdir(root):
        print("  ПАПКА НЕ НАЙДЕНА — проверьте путь (--root)."); return
    lvl0 = _list_dirs(root)
    print(f"уровень 1 (обычно ЗУМ): {lvl0[:max_dirs]}"
         f"{' …' if len(lvl0) > max_dirs else ''}  (всего {len(lvl0)})")
    if not lvl0:
        print("  В папке нет подкаталогов первого уровня."); return
    z_dir = os.path.join(root, lvl0[len(lvl0) // 2])     # средний (не самый большой/маленький)
    lvl1 = _list_dirs(z_dir)
    print(f"уровень 2 внутри '{lvl0[len(lvl0)//2]}' (обычно X): {lvl1[:max_dirs]}"
         f"{' …' if len(lvl1) > max_dirs else ''}  (всего {len(lvl1)})")
    if not lvl1:
        return
    x_dir = os.path.join(z_dir, lvl1[0])
    lvl2_files = _list_files(x_dir)
    lvl2_dirs = _list_dirs(x_dir)
    if lvl2_files:
        print(f"уровень 3 внутри '{lvl1[0]}' — ФАЙЛЫ (обычно Y.ext): "
             f"{lvl2_files[:max_dirs]}{' …' if len(lvl2_files) > max_dirs else ''}"
             f"  (всего {len(lvl2_files)})")
    if lvl2_dirs:
        print(f"уровень 3 внутри '{lvl1[0]}' — ЕЩЁ ПАПКИ (раскладка глубже, чем z/x/y): "
             f"{lvl2_dirs[:max_dirs]}")


def probe_sample_files(root, n=3):
    print(f"\n=== ПРОБА ФАЙЛОВ (формат/размер) ===")
    found = 0
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if found >= n:
                return
            full = os.path.join(dirpath, fn)
            try:
                with open(full, "rb") as f:
                    head = f.read(16)
                ext = gm._sniff_ext(head + b"\x00" * 12)
                size = os.path.getsize(full)
                print(f"  {full}\n    реальный формат (по байтам): {ext}  размер: {size} байт")
                found += 1
            except OSError as e:
                print(f"  {full}: ошибка чтения {e}")


def guess_xyz_or_tms(root, zoom, lon=gm.KURSK_LON, lat=gm.KURSK_LAT):
    print(f"\n=== ПРОВЕРКА XYZ vs TMS на зуме {zoom} (точка: Курск {lat:.4f},{lon:.4f}) ===")
    xt, yt = gm.deg2num(lon, lat, zoom)
    x, y_xyz = int(xt), int(yt)
    y_tms = gm._tms_y(zoom, y_xyz)
    print(f"  ожидаемый тайл (XYZ): z={zoom} x={x} y={y_xyz}")
    print(f"  тот же тайл в TMS:    z={zoom} x={x} y={y_tms}")
    hits = {}
    for label, y in (("XYZ", y_xyz), ("TMS", y_tms)):
        for ext in gm._TILE_EXTS:
            p = os.path.join(root, str(zoom), str(x), f"{y}{ext}")
            if os.path.exists(p):
                hits[label] = p
                break
    if hits and not (("XYZ" in hits) and ("TMS" in hits) and zoom == 0):
        for label, p in hits.items():
            print(f"  НАЙДЕН файл при гипотезе {label}: {p}")
        if "XYZ" in hits and "TMS" not in hits:
            print("  -> Похоже на XYZ-раскладку (как обычные онлайн-тайлы).")
        elif "TMS" in hits and "XYZ" not in hits:
            print("  -> Похоже на TMS-раскладку (ось Y инвертирована).")
        else:
            print("  -> Совпадение по обеим гипотезам на этом зуме — возьмите зум с "
                 "бОльшим диапазоном (--zoom 8 или выше), чтобы различить.")
    else:
        print("  Ни один файл не найден ни по одной из гипотез на этом зуме.")
        print("  Возможные причины: иной зум не скачан / иная вложенность папок /")
        print("  иные имена (без префиксов 'z'/'x'/'y'? см. дерево выше) /")
        print("  Курск не входит в скачанный регион на этом зуме.")
        print("  Попробуйте другой --zoom либо посмотрите дерево выше и сверьте вручную.")


def main():
    ap = argparse.ArgumentParser(description="Диагностика локальной папки с тайлами")
    ap.add_argument("--root", required=True, help="путь к папке с тайлами (например, .../Sat)")
    ap.add_argument("--zoom", type=int, default=8, help="зум для проверки XYZ/TMS")
    a = ap.parse_args()

    print_tree(a.root)
    probe_sample_files(a.root)
    guess_xyz_or_tms(a.root, a.zoom)

    print("\n=== ЧТО ДЕЛАТЬ ДАЛЬШЕ ===")
    print("1) Если структура подтвердилась как <root>/{z}/{x}/{y}.ext — переименуйте/")
    print("   скопируйте (или сделайте symlink) папку так, чтобы она лежала как")
    print("   <UAV_TILE_CACHE>/<имя_слоя>/{z}/{x}/{y}.ext, например:")
    print("     <проект>/tile_cache/Sat/{z}/{x}/{y}.jpg")
    print("   (или укажите UAV_TILE_CACHE=<папка, где лежит Sat> — тогда копировать не надо).")
    print("2) Если определилось TMS — запускайте приложение с переменной окружения:")
    print("     UAV_TMS_LAYERS=Sat python main_qt.py   (Linux/macOS)")
    print("     set UAV_TMS_LAYERS=Sat && python main_qt.py   (Windows cmd)")
    print("3) Слой появится в выпадающем списке «Карта» как «Sat (локальный кэш)»")
    print("   автоматически — ничего в коде менять не нужно.")


if __name__ == "__main__":
    main()
