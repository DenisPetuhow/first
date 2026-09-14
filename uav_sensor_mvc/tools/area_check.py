# -*- coding: utf-8 -*-
"""
ПРОВЕРКА ОБЛАСТЕЙ ИЗ ФАЙЛОВ (план 10 §10.5): `область.txt` разбирается, совпадает с
прежним реестром, битое не принимается, `config` открывает выбранную область.

Седьмая проверка программы. Нужна потому, что ошибка здесь НЕ видна остальным шести:
`reference_run` сверяет числа на области по умолчанию, а выбор области в программе
идёт другим путём — через переменную окружения до импорта `config`. Поймано уже при
написании: окно «Выбор папки» не показывает файлов, человек выбирал папку уровнем выше
и получал «нет файла» (разделы 4 и 5).

    python tools/area_check.py
"""
import io
import os
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):          # консоль Windows cp1251 роняет «→» и «×»
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import area_file as af                                                    # noqa: E402

BAD = []                                          # что не прошло: (раздел, описание)


def check(section, ok, text):
    # Напечатать строку проверки и запомнить провал. Вход: номер раздела, прошла ли (bool),
    # текст. Отдаёт: ничего; провал попадает в BAD и в код возврата.
    print("  %s %s" % ("✓" if ok else "✗", text))
    if not ok:                                    # проверка провалена
        BAD.append((section, text))               # — запомнить для итога


def rejects(text, must):
    # Файл с этим текстом обязан быть отвергнут. Вход: текст файла области, слово, которое
    # должно быть в сообщении. Отдаёт: (отвергнут с этим словом — bool, сообщение).
    try:
        af.parse_area_text(text, tempfile.gettempdir())
    except af.AreaFileError as e:                 # отвергнут — как и должен
        return must in str(e), str(e)
    return False, "принят, а не должен"           # принят — это провал


def config_under(env_area):
    # Импорт config в ОТДЕЛЬНОМ процессе: область читается при импорте, в этом уже прочитана.
    # Вход: путь для UAV_AREA_DIR или None — без выбора. Отдаёт: (имя, рамка, ошибка) строками.
    env = dict(os.environ)                        # окружение дочернего процесса
    env.pop(af.ENV_AREA, None)
    if env_area is not None:                      # проверяем выбор из программы
        env[af.ENV_AREA] = env_area               # — как его выставляет main_qt
    env["PYTHONIOENCODING"] = "utf-8"
    code = ("import config as c; print(c.THREAT_AREA); print(tuple(c.THREAT_BBOX_LONLAT)); "
            "print(c.THREAT_AREA_ERROR or '-')")  # что напечатать из config
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                         capture_output=True, text=True, encoding="utf-8")
    lines = out.stdout.strip().splitlines()       # три строки ответа
    return (lines + ["?", "?", "?"])[:3] if out.returncode == 0 else ("СБОЙ", out.stderr[-300:], "")


def main():
    # Все разделы проверки по очереди. Вход: ничего. Отдаёт: 0 — всё прошло, 1 — есть провалы.
    geo = af.geo_cache_root()                     # корень geo_cache/
    from config import THREAT_AREAS

    print("1. ФАЙЛЫ ОБЛАСТЕЙ В geo_cache/")
    folders = sorted(d for d in os.listdir(geo)
                     if os.path.isfile(os.path.join(geo, d, af.AREA_FILE_NAME)))   # папки с файлом области
    check(1, len(folders) >= 2, "файлов области: %d (%s)" % (len(folders), ", ".join(folders)))
    for d in folders:
        p = os.path.join(geo, d)                  # полный путь папки области
        try:
            rec = af.read_area_folder(p)          # запись области
        except af.AreaFileError as e:             # файл не разобран
            check(1, False, "%s: не разобран — %s" % (d, e))
            continue
        files = [(k, rec.get(k)) for k in ("layers", "dem")]
        have = ["%s %s" % (k, "есть" if v and any(os.path.exists(os.path.join(p, v + ext))
                                                 for ext in ("", ".tif", ".tiff")) else "НЕТ")
                for k, v in files]
        check(1, True, "%s: «%s», район %s, %s" % (d, rec["label"],
                                                   "задан" if rec.get("work") else "не задан",
                                                   ", ".join(have)))

    print("2. ФАЙЛ arh СОВПАДАЕТ С ПРЕЖНИМ РЕЕСТРОМ config.THREAT_AREAS")
    rec = af.read_area_folder(os.path.join(geo, "arh"))
    for k in ("bbox", "work", "source", "layers", "dem", "entry", "target", "points", "dir"):
        check(2, rec.get(k) == THREAT_AREAS["arh"].get(k), "%s: %s" % (k, rec.get(k)))

    # запись -> текст -> разбор даёт то же самое: новые области заводятся из реестра этим путём
    tmp = tempfile.mkdtemp()
    for key, reg in sorted(THREAT_AREAS.items()):
        with io.open(os.path.join(tmp, af.AREA_FILE_NAME), "w", encoding="utf-8") as f:
            f.write(af.format_area_text(reg))
        back = af.parse_area_text(io.open(os.path.join(tmp, af.AREA_FILE_NAME),
                                          encoding="utf-8").read(), tmp)
        diff = [k for k in ("bbox", "work", "layers", "dem", "entry", "target")
                if back.get(k) != reg.get(k)]
        check(2, not diff, "запись и разбор %s сходятся%s" % (key, (": " + ", ".join(diff)) if diff else ""))

    print("3. БИТЫЙ ФАЙЛ НЕ ПРИНИМАЕТСЯ")
    for text, must, what in (
            ("район = 40, 62, 41, 63\n", "не задана", "без области"),
            ("область = 38, 61, 43, 63\nрайон = 37, 62, 41, 63\n", "западный", "район за краем"),
            ("область = 38, 61, 43, 63\nраион = 39, 62, 41, 62.5\n", "незнакомый", "опечатка в ключе"),
            ("область = 43, 61, 38, 63\n", "долгота", "перепутан порядок"),
            ("область = 38, 61, 43\n", "4 чисел", "три числа")):
        ok, msg = rejects(text, must)
        check(3, ok, "%s → %s" % (what, msg))
    ok = af.parse_area_text("область = 37,95; 61,71; 43,68; 63,97\n", tempfile.gettempdir())["bbox"]
    check(3, ok == (37.95, 61.71, 43.68, 63.97), "десятичная запятая через «;» → %s" % (ok,))

    print("4. ВЫБОР ФАЙЛА И ПАПКИ УРОВНЕМ ВЫШЕ")
    f = os.path.join(geo, "arh", af.AREA_FILE_NAME)
    check(4, af.read_area_folder(f)["bbox"] == rec["bbox"], "путь к самому файлу принимается")
    try:
        af.read_area_folder(geo)
        check(4, False, "папка geo_cache/ принята, а файла в ней нет")
    except af.AreaFileError as e:
        check(4, "вложенных папках" in str(e), "папка уровнем выше → подсказка: %s" % str(e)[-80:])

    print("5. config ОТКРЫВАЕТ ВЫБРАННУЮ ОБЛАСТЬ")
    name, box, err = config_under(None)
    check(5, name == "arh" and err == "-", "без выбора → %s %s" % (name, box))
    for d in folders:
        name, box, err = config_under(os.path.join(geo, d))
        check(5, name == d and err == "-", "UAV_AREA_DIR=%s → %s %s" % (d, name, box))
    name, box, err = config_under(os.path.join(geo, "arh", af.AREA_FILE_NAME))
    check(5, name == "arh" and err == "-", "UAV_AREA_DIR=файл области → %s" % name)
    name, box, err = config_under(geo)
    check(5, name == "arh" and err != "-", "папка без файла → откат на arh, причина: %s" % err[:60])

    print("6. УПАКОВАННЫЙ ФОРМАТ СЛОЁВ ЧИТАЕТСЯ ТАК ЖЕ, КАК ПРЕЖНИЙ")
    # ⚠️ 14.09.2026: слои Marshut (2.6 млн массивов) читались десятки минут; упакованный
    # формат — два массива на слой. Итог обязан быть тем же до бита, иначе поедут веса.
    import numpy as np
    from model.threat_grid import packed_npz_arrays, _load_layers_npz
    rng = np.random.default_rng(3)
    layers = {"road_local": [rng.random((n, 2)).astype(np.float32) for n in (2, 7, 1, 40)],
              "place_poly": [rng.random((n, 3)).astype(np.float32) for n in (5, 9)],
              "place_names": [np.asarray(["Тверь", "Клин"], dtype="U")]}
    old = {"%s__%d" % (k, i): a for k, v in layers.items() for i, a in enumerate(v)}
    p_old, p_new = os.path.join(tmp, "old.npz"), os.path.join(tmp, "packed.npz")
    np.savez_compressed(p_old, **old)
    np.savez_compressed(p_new, **packed_npz_arrays(layers))
    a, b = _load_layers_npz(p_old), _load_layers_npz(p_new)
    same = set(a) == set(b) and all(len(a[k]) == len(b[k]) and
                                    all(np.array_equal(x, y) for x, y in zip(a[k], b[k]))
                                    for k in a)
    n_keys = len(np.load(p_new).files)
    check(6, same, "слои из обоих форматов совпали побайтово (линий %d, ключей в упакованном %d)"
          % (sum(len(v) for v in layers.values()), n_keys))

    print("=" * 66)
    if BAD:                                       # есть провалы
        print("ПРОВАЛОВ: %d" % len(BAD))
        return 1
    print("ВСЕ ПРОВЕРКИ ОБЛАСТЕЙ ПРОШЛИ.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
