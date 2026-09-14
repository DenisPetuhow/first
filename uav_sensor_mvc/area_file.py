# -*- coding: utf-8 -*-
"""
ОБЛАСТЬ ИЗ ПАПКИ: файл `область.txt` — координаты области, начальный район и файлы.

ЗАЧЕМ (заказчик 13.09.2026, план 10 §10.5). Областей уже две — Архангельская и Донецк, —
и будет больше. Раньше область задавалась ПРАВКОЙ `config.py` (`THREAT_AREA` и запись в
`THREAT_AREAS`), а в собранной программе сменить её было нельзя вовсе. Теперь у каждой
папки области свой текстовый файл, и программа открывает область по нему.

СЛОВА. ОБЛАСТЬ — большая чёрная рамка: под неё грузятся тайлы, векторные слои и рельеф.
РАЙОН МОДЕЛИРОВАНИЯ — синяя рамка внутри области, по нему строится сетка. В коде область
по старой памяти зовётся «участок» (`THREAT_AREA`); в интерфейсе и в файле — «область».

ГДЕ ЛЕЖИТ: `geo_cache/<область>/область.txt` — рядом с `.osm.pbf`, слоями `.npz` и
рельефом, чтобы область переносилась одной папкой (ОГРАНИЧЕНИЯ 5.1д). Тайлы — ОТДЕЛЬНАЯ
папка, выбирается своей кнопкой: в ней подпапка с именем области.

    название = Архангельская обл.
    область  = 37.9510, 61.7099, 43.6800, 63.9671    # lon_min, lat_min, lon_max, lat_max
    район    = 39.8700, 62.4800, 42.1500, 63.1400
    исходник = arh.osm.pbf
    слои     = threat_layers_arh.npz
    рельеф   = arh_hh
    вход     = Восточный край, 43.40, 62.90
    цель     = Мирный, 40.33, 62.76

⚠️ МОДУЛЬ НЕ ЗАВИСИТ НИ ОТ ЧЕГО В ПРОЕКТЕ — только stdlib. Его импортирует `config.py`, а
`model/` оттуда импортировать нельзя: `model/__init__` → `threat_grid` → `config` — круг.
Разбор механизма — теория/код/ОБЛАСТЬ_ИЗ_ФАЙЛА.md; решения — план 10 §10.5.
"""
import glob
import io
import json
import os

AREA_FILE_NAME = "область.txt"      # имя файла области в её папке
UI_SECTION = "область"              # раздел памяти окна в geo_cache/ui_state.json
ENV_AREA = "UAV_AREA_DIR"           # папка (или сам файл) области, выбранная в программе
ENV_TILES = "UAV_TILE_CACHE"        # корень кэша тайлов (его же читает geomap.cache_root)

# Ключи файла: имя в файле -> что это. Незнакомый ключ — ошибка, а не молчаливый пропуск:
# опечатка «раион» иначе тихо открыла бы область без района.
KEYS = {
    "название": "подпись области",
    "область": "lon_min, lat_min, lon_max, lat_max — чёрная рамка",
    "район": "lon_min, lat_min, lon_max, lat_max — район моделирования",
    "исходник": "экстракт .osm.pbf",
    "слои": "кэш слоёв .npz",
    "рельеф": "GeoTIFF без расширения",
    "вход": "подпись, lon, lat",
    "цель": "подпись, lon, lat",
}


class AreaFileError(ValueError):
    # Файл области не принят. Сообщение готово для показа человеку как есть.
    pass


def geo_cache_root():
    # Корень кэша геоданных — тем же правилом, что `model.threat_grid.geo_cache_root`.
    # Вход: ничего (читает env UAV_GEO_CACHE). Отдаёт: путь к папке, строка.
    env = os.environ.get("UAV_GEO_CACHE")            # кэш, перенесённый переменной окружения
    if env:                                          # переменная задана
        return env                                   # — берём её
    # повторено здесь, а не импортом из модели: из config модель импортировать нельзя (круг)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "geo_cache")


# ----------------------------------------------------------------------
# Разбор
# ----------------------------------------------------------------------
def _numbers(text, n, key):
    # Строка чисел -> список из n чисел. Вход: текст («a, b» или «37,95; 61,71»), сколько
    # чисел нужно, имя ключа для сообщения. Отдаёт: list[float]; иначе AreaFileError.
    t = text.strip()                                 # значение без краевых пробелов
    if ";" in t:                                     # есть «;» — запятая в числах десятичная
        parts = [p.replace(",", ".") for p in t.split(";")]   # делим по «;», запятую в точку
    else:                                            # «;» нет — запятая разделяет числа
        parts = t.replace("\t", " ").replace(",", " ").split()
    try:
        vals = [float(p) for p in parts if p.strip()]         # числа ключа
    except ValueError:                               # среди частей не число
        raise AreaFileError("«%s»: ожидались %d числа, получено «%s»" % (key, n, text))
    if len(vals) != n:                               # чисел не столько, сколько нужно
        raise AreaFileError("«%s»: ожидалось %d чисел, получено %d" % (key, n, len(vals)))
    return vals


def _box(text, key):
    # Рамка из четырёх чисел с проверкой порядка. Вход: текст «lon_min, lat_min, lon_max,
    # lat_max» (градусы), имя ключа. Отдаёт: кортеж из 4 float; иначе AreaFileError.
    lo, la, ho, ha = _numbers(text, 4, key)          # края рамки, градусы
    if not (-180.0 <= lo < ho <= 180.0):             # долготы перепутаны или вне мира
        raise AreaFileError("«%s»: долгота min < max в пределах ±180, получено %g и %g"
                            % (key, lo, ho))         # сетка из нуля ячеек — не пускаем
    if not (-85.0 <= la < ha <= 85.0):               # широты перепутаны или у полюса
        raise AreaFileError("«%s»: широта min < max в пределах ±85, получено %g и %g"
                            % (key, la, ha))
    return (lo, la, ho, ha)


def _point(text, key):
    # Точка с подписью. Вход: текст «подпись, lon, lat» (градусы; в подписи могут быть
    # пробелы), имя ключа. Отдаёт: (подпись, lon, lat); иначе AreaFileError.
    head, sep, tail = text.rpartition(",")           # tail — широта
    head2, sep2, mid = head.rpartition(",")          # mid — долгота, head2 — подпись
    if not (sep and sep2):                           # меньше трёх полей
        raise AreaFileError("«%s»: нужно «подпись, долгота, широта», получено «%s»"
                            % (key, text))
    try:
        return (head2.strip() or key, float(mid), float(tail))
    except ValueError:                               # долгота или широта — не число
        raise AreaFileError("«%s»: долгота и широта должны быть числами: «%s»" % (key, text))


def _find(folder, pattern):
    # Первый файл по маске в папке области. Вход: папка, маска («*.tif»).
    # Отдаёт: имя файла без пути или "" — не нашлось.
    hits = sorted(glob.glob(os.path.join(folder, pattern)))   # подходящие файлы по алфавиту
    return os.path.basename(hits[0]) if hits else ""


def parse_area_text(text, folder):
    # Текст `область.txt` -> запись области того же вида, что `config.THREAT_AREAS[...]`.
    # Вход: текст файла, папка области (имя и поиск файлов). Отдаёт: dict; иначе AreaFileError.
    raw = {}                                         # ключ файла -> значение строкой
    for n, line in enumerate(text.splitlines(), 1):
        s = line.split("#", 1)[0].strip()            # строка без комментария
        if not s:                                    # пустая строка или только комментарий
            continue                                 # разбирать нечего
        key, sep, val = s.partition("=")
        key = key.strip().lower()                    # ключ без регистра: «Район» = «район»
        if not sep:                                  # в строке нет «=»
            raise AreaFileError("строка %d: нужно «ключ = значение», получено «%s»" % (n, s))
        if key not in KEYS:                          # опечатка или чужой ключ
            raise AreaFileError("строка %d: незнакомый ключ «%s». Допустимы: %s"
                                % (n, key, ", ".join(KEYS)))
        raw[key] = val.strip()
    if "область" not in raw:                         # без области нет ни предела вида, ни якоря
        raise AreaFileError("не задана «область» — четыре числа lon_min, lat_min, lon_max, lat_max")

    bbox = _box(raw["область"], "область")           # чёрная рамка, градусы
    lo, la, ho, ha = bbox
    work = _box(raw["район"], "район") if raw.get("район") else None   # синяя рамка или None
    if work is not None:                             # район задан — обязан лежать в области
        eps = 1e-9                                   # допуск на запись чисел, градусы
        wlo, wla, who, wha = work
        out = [name for name, bad in (("западный", wlo < lo - eps), ("южный", wla < la - eps),
                                      ("восточный", who > ho + eps), ("северный", wha > ha + eps))
               if bad]                               # края района, вылезшие за область
        if out:                                      # район вылез за край
            # расчёт вне области идёт без слоёв и рельефа, а на карте этого не видно
            raise AreaFileError("район выходит за область: %s край. Расчёт вне области "
                                "идёт без слоёв и рельефа" % ", ".join(out))

    folder = os.path.abspath(folder)                 # полный путь папки области
    name = os.path.basename(folder.rstrip("/\\"))    # имя папки = имя области и папки тайлов
    root = os.path.abspath(geo_cache_root())         # корень geo_cache/
    # папка внутри geo_cache/ пишется ИМЕНЕМ, как в реестре config: тогда `area_cache_dir`
    # даёт тот же путь, что и раньше; снаружи — полным путём (os.path.join его сохранит)
    in_root = os.path.normcase(os.path.dirname(folder)) == os.path.normcase(root)
    dem = raw.get("рельеф")                          # имя растра без расширения или None
    if dem is None:                                  # рельеф в файле не указан
        dem = os.path.splitext(_find(folder, "*.tif"))[0]   # — берём первый .tif в папке
    cy = (work[1] + work[3]) * 0.5 if work else (la + ha) * 0.5   # широта середины, градусы
    target_c = ((work[0] + work[2]) * 0.5, cy) if work else ((lo + ho) * 0.5, cy)   # центр, градусы
    rec = dict(
        label=raw.get("название") or name,
        dir=name if in_root else folder,
        tile_area=name,
        points={"Северо-запад": (lo, ha), "Юго-запад": (lo, la), "Юго-восток": (ho, la)},
        bbox=bbox,
        source=raw.get("исходник") or _find(folder, "*.osm.pbf"),
        layers=raw.get("слои") or _find(folder, "threat_layers*.npz"),
        dem=dem,
        # вход по умолчанию — у восточного края (3 % ширины внутрь), цель — центр района
        entry=(_point(raw["вход"], "вход") if raw.get("вход")
               else ("Восточный край", ho - (ho - lo) * 0.03, cy)),
        target=(_point(raw["цель"], "цель") if raw.get("цель")
                else ("Центр района", target_c[0], target_c[1])),
    )
    if work is not None:                             # район есть — кладём в запись
        rec["work"] = work                           # без него программа откроется на обзоре
    return rec


def read_area_folder(folder):
    # Папка области ИЛИ сам файл -> запись области. Вход: путь к папке или к `область.txt`.
    # Отдаёт: dict (см. parse_area_text); нет файла, битый — AreaFileError с подсказкой.
    if os.path.isfile(folder):                       # указали сам файл области
        path, folder = folder, os.path.dirname(os.path.abspath(folder))   # файл и его папка
    else:                                            # указали папку
        path = os.path.join(folder, AREA_FILE_NAME)  # — файл ищем в ней
    if not os.path.isfile(path):                     # файла нет
        # окно «Выбор папки» Windows файлов не показывает, и человек выбирал папку уровнем
        # выше (заказчик 13.09.2026) — подсказываем, где файл на самом деле
        subs = sorted(d for d in (os.listdir(folder) if os.path.isdir(folder) else [])
                      if os.path.isfile(os.path.join(folder, d, AREA_FILE_NAME)))   # папки с файлом
        hint = ("; он есть во вложенных папках: %s — выберите одну из них" % ", ".join(subs)
                if subs else "")                     # есть вложенные с файлом → подсказка
        raise AreaFileError("в папке нет файла «%s»: %s%s" % (AREA_FILE_NAME, folder, hint))
    try:
        with io.open(path, encoding="utf-8-sig") as f:   # -sig: Блокнот пишет BOM
            text = f.read()                          # весь файл области
    except (OSError, UnicodeDecodeError) as e:       # нет прав или не та кодировка
        raise AreaFileError("не прочитать «%s»: %s" % (path, e))
    return parse_area_text(text, folder)


def format_area_text(rec):
    # Запись области -> текст `область.txt` (обратное к parse_area_text).
    # Вход: dict вида config.THREAT_AREAS[...]. Отдаёт: текст файла, строка.
    def box(b):                                      # рамка четырьмя числами через запятую
        return ", ".join("%.4f" % v for v in b)

    lines = ["# Область: координаты, район моделирования и файлы. Разбор — area_file.py,",
             "# план 10 §10.5. Координаты в градусах: lon_min, lat_min, lon_max, lat_max.",
             "название = %s" % rec.get("label", ""),
             "область  = %s" % box(rec["bbox"])]     # строки будущего файла
    if rec.get("work"):                              # район в записи есть
        lines.append("район    = %s" % box(rec["work"]))      # — пишем
    else:                                            # района нет
        lines.append("# район  = не задан: программа откроется на обзоре области")   # — подсказка
    for key, field in (("исходник", "source"), ("слои", "layers"), ("рельеф", "dem")):
        lines.append("%-8s = %s" % (key, rec.get(field, "")))
    for key, field in (("вход", "entry"), ("цель", "target")):
        if rec.get(field):                           # точка задана
            lab, lon, lat = rec[field]               # подпись и координаты, градусы
            lines.append("%-8s = %s, %.4f, %.4f" % (key, lab, lon, lat))
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# Какая область активна
# ----------------------------------------------------------------------
LAST_ERROR = ""     # почему выбранная область не открылась — показать в «Исходных данных»


def active_area(registry, default_key):
    # Активная область. Вход: реестр config.THREAT_AREAS, имя области по умолчанию.
    # Отдаёт: (имя, запись dict, откуда взята — строка). Порядок см. ниже.
    global LAST_ERROR
    LAST_ERROR = ""
    # Порядок: 1) область, выбранная в программе (UAV_AREA_DIR); 2) файл области по
    # умолчанию; 3) запись реестра — прежнее поведение, пока у папки нет файла.
    chosen = os.environ.get(ENV_AREA, "").strip()    # выбранная папка или файл области
    if chosen:                                       # в программе выбрали область
        try:
            rec = read_area_folder(chosen)           # запись выбранной области
            src = chosen if os.path.isfile(chosen) else os.path.join(chosen, AREA_FILE_NAME)
            return rec["tile_area"], rec, src
        except AreaFileError as e:                   # файл битый или пропал
            LAST_ERROR = str(e)                      # — запоминаем причину, открываем умолчание
    base = registry[default_key]                     # запись области по умолчанию из реестра
    folder = os.path.join(geo_cache_root(), base.get("dir", default_key))   # её папка
    if os.path.isfile(os.path.join(folder, AREA_FILE_NAME)):   # у неё есть свой файл
        try:
            return default_key, read_area_folder(folder), os.path.join(folder, AREA_FILE_NAME)
        except AreaFileError as e:                   # файл испорчен
            LAST_ERROR = LAST_ERROR or str(e)        # — берём реестр, причину сохраняем
    rec = dict(base)                                 # копия записи реестра
    rec.setdefault("tile_area", os.path.basename(str(base.get("dir", "")).rstrip("/\\")))
    return default_key, rec, "config.THREAT_AREAS[%r]" % default_key


# ----------------------------------------------------------------------
# Память выбора (правило заказчика 04.09.2026: последний выбор подставляется снова)
# ----------------------------------------------------------------------
def saved_choice():
    # Сохранённый выбор области и папки тайлов. Вход: ничего (geo_cache/ui_state.json).
    # Отдаёт: dict {"папка": …, "тайлы": …}; нет файла — пустой dict.
    # читаем напрямую, а не через view_qt.ui_state: тот тянет модель, а нужно ДО import config
    try:
        with io.open(os.path.join(geo_cache_root(), "ui_state.json"), encoding="utf-8") as f:
            data = json.load(f)                      # вся память окон
    except Exception:                                # файла нет или он битый
        return {}                                    # — просто нет сохранённого выбора
    sec = data.get(UI_SECTION) if isinstance(data, dict) else None   # раздел «область»
    return sec if isinstance(sec, dict) else {}


def apply_saved_choice():
    # Сохранённый выбор -> переменные окружения UAV_AREA_DIR, UAV_TILE_CACHE. Звать ДО
    # `import config`. Вход: ничего. Отдаёт: прочитанный раздел памяти, dict.
    sec = saved_choice()                             # что выбирали в прошлый раз
    for env, key in ((ENV_AREA, "папка"), (ENV_TILES, "тайлы")):
        val = str(sec.get(key) or "").strip()        # сохранённый путь или ""
        # заданную снаружи переменную не перебиваем: явный запуск важнее памяти окна
        if val and not os.environ.get(env):          # выбор есть, снаружи не задано
            os.environ[env] = val                    # — выставляем
    return sec
