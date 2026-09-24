# -*- coding: utf-8 -*-
"""
MODEL · Справочник типов датчиков (план 13, задача 13.1).

Тип датчика — ПАСПОРТ: название, радиус, кратность, палитра показа, правило места
установки, режим по умолчанию; у ПВН — ещё правила числа у объектов защиты (13.18).
Число датчиков в паспорт не входит: «поставить 20 штук» — задание на расчёт, а не
свойство типа (В13.2), оно живёт в `Params`.

⚠️ КЛАСС РЕШАЕТ, КАК СТАВИТСЯ; ЗАПИСЬ В ФАЙЛЕ — КАКИМИ ЧИСЛАМИ. Малый ставится сетью по
маршрутам (жадный отбор), большой — кольцом у объекта защиты вне жадного.

⚠️ НОМЕР ТИПА УСТОЙЧИВ. На него завязаны файл датчиков (формат 8.7.7), `ManualSensor`,
показатели и цвета. Номера 1–3 — малые, 4 — большой (так с 04.09.2026); новый тип
получает свободный номер начиная с 5.

⚠️ ПЕРЕХОДНЫЙ МОСТ (до задач 13.3 и 13.10). У номеров 1–4 числа задания N, R, k — по-прежнему
поля `Params`: с ними считают расчёт, эталон и окно «Исходные данные». Паспорт даёт этим
номерам название, палитру, правило места и режим; его R и k подставит окно типа (13.3).
У номеров ≥ 5 R и k — из паспорта, N — из `Params.threat_N_types`. Сборка —
`model/sensors.py :: sensor_types`.

ГДЕ СПРАВОЧНИК: `geo_cache/<участок>/типы_датчиков.json` (В13.1, умолчание — папка
участка). Файла нет — встроенные умолчания. Разбор — план 13 §1.
"""
import io
import json
import os
from dataclasses import dataclass

FILE_NAME = "типы_датчиков.json"   # имя справочника в папке участка
ZONES = ("везде", "застройка")     # правила места: «застройка» — карта ПВН (13.4)
LEGACY_IDS = (1, 2, 3, 4)          # номера, чьи N, R, k живут в полях Params (мост)
BIG_ID = 4                         # номер большого типа — за ним поля threat_N_big / threat_R_big
FIRST_FREE_ID = 5                  # с него нумеруются новые типы


@dataclass(frozen=True)
class SensorType:
    """Паспорт типа датчика: номер, название и свойства. Как ставится — решает класс-наследник.
    Неизменяем: тип выбран — числа под ним по дороге не меняются."""

    type_id: int = FIRST_FREE_ID   # устойчивый номер: файл датчиков, показатели, цвета
    name: str = "Датчик"           # то, что видно в списке выбора
    r_km: float = 1.0              # радиус обнаружения, км
    k: int = 3                     # кратность засечки своего типа, шт.
    color: str = "sensor"          # ключ палитры THREAT_COLORS — сам цвет живёт в view_qt
    zone: str = "везде"            # правило места, одно из ZONES
    zone_buf_km: float = 0.0       # полоса вокруг застройки и построек, км (ПВН — 0.5)
    strip_km: float = 0.0          # полоса у дорог и построек, где лес и болота разрешены, км
    static: bool = False           # режим нового датчика по умолчанию: True — статический (В13.4)
    targets: bool = False          # ставится ли у объектов защиты до общего прохода (13.18)
    tgt_share: float = 0.5         # доля от вместимости зоны цели: «сколько помещается — пополам»
    tgt_gamma: float = 0.5         # сила приоритета γ в множителе 1 + γ·(p − p̄)/p̄
    tgt_min: int = 3               # минимум у цели, шт. — если столько помещается
    tgt_cap_one: float = 0.5       # потолок при одной цели, доля от N
    tgt_cap_all: float = 0.7       # потолок на все цели вместе, доля от N
    tgt_k: int = 1                 # кратность цели — критерий этапа целей
    bonus_height: float = 0.0      # бонус высоте крыши (13.7), доля от лучшего прироста первого шага
    bonus_city: float = 0.0        # бонус размеру пункта (13.7), та же доля

    KIND = "малый"                 # как класс записан в файле справочника
    SPEC_KIND = "small"            # как класс понимает расстановка (`SensorSpec.kind`)

    # Пределы здравого смысла: вне их число заведомо опечатка, а не выбор.
    LIMITS = (
        ("r_km", 0.01, 100.0, "радиус, км"),
        ("k", 1, 20, "кратность"),
        ("zone_buf_km", 0.0, 20.0, "полоса у застройки, км"),
        ("strip_km", 0.0, 20.0, "полоса в лесу, км"),
        ("tgt_share", 0.0, 1.0, "доля от вместимости"),
        ("tgt_gamma", 0.0, 5.0, "сила приоритета"),
        ("tgt_min", 0, 100, "минимум у цели"),
        ("tgt_cap_one", 0.0, 1.0, "потолок одной цели"),
        ("tgt_cap_all", 0.0, 1.0, "потолок всех целей"),
        ("tgt_k", 1, 20, "кратность цели"),
        ("bonus_height", 0.0, 5.0, "бонус высоте"),
        ("bonus_city", 0.0, 5.0, "бонус крупному пункту"),
    )

    def validate(self):
        """Жалобы на свои числа. Отдаёт: список строк, пустой — всё в порядке."""
        bad = []                                     # жалобы, по строке на поле
        if not str(self.name).strip():               # без названия тип не выбрать
            bad.append("название пустое")            # — в списке будет пустая строка
        if int(self.type_id) < 1:                    # номер — ключ файлов датчиков
            bad.append("номер %d — ждём от 1" % int(self.type_id))
        if self.zone not in ZONES:                   # чужое правило места расстановка не поймёт
            bad.append("место «%s» — бывает %s" % (self.zone, ", ".join(ZONES)))
        for field, lo, hi, title in self.LIMITS:
            val = float(getattr(self, field))
            if not (lo <= val <= hi):                # число вне здравого смысла
                bad.append("%s: %g — ждём от %g до %g" % (title, val, lo, hi))
        return bad


@dataclass(frozen=True)
class Small(SensorType):
    """Малый: сеть по маршрутам — позиции выбирает жадный отбор."""

    KIND = "малый"                 # как этот класс записан в файле справочника
    SPEC_KIND = "small"            # как его понимает расстановка


@dataclass(frozen=True)
class Big(SensorType):
    """Большой: кольцо у объекта защиты, вне жадного; кратность в расстановке не участвует."""

    KIND = "большой"               # как этот класс записан в файле справочника
    SPEC_KIND = "big"              # как его понимает расстановка


KINDS = {Small.KIND: Small, Big.KIND: Big}   # запись в файле -> класс

# Ключи файла — по-русски, как в «типы_бпла.json»: справочник правит человек, а не программа.
_KEYS = (
    ("радиус_км", "r_km", float),
    ("кратность", "k", int),
    ("палитра", "color", str),
    ("место", "zone", str),
    ("полоса_у_застройки_км", "zone_buf_km", float),
    ("полоса_в_лесу_км", "strip_km", float),
    ("статический", "static", bool),
    ("у_объектов_защиты", "targets", bool),
    ("доля_вместимости", "tgt_share", float),
    ("сила_приоритета", "tgt_gamma", float),
    ("минимум_у_цели", "tgt_min", int),
    ("потолок_одна_цель", "tgt_cap_one", float),
    ("потолок_все_цели", "tgt_cap_all", float),
    ("кратность_цели", "tgt_k", int),
    ("бонус_высоте", "bonus_height", float),
    ("бонус_крупному_пункту", "bonus_city", float),
)
# поле класса -> ключ файла; нужен тому, кто собирает запись по одному полю (окно типа, 13.3)
KEY_BY_FIELD = dict([("type_id", "номер"), ("name", "название")] + [(f, k) for k, f, _ in _KEYS])

_cache = {}                        # путь -> (время правки файла, типы): читают на каждом расчёте


def defaults():
    """Встроенный справочник — с чего начинают, когда файла нет. Отдаёт: список типов.
    МОГ и ПВН — умолчания заказчика (21.09.2026), большой — нынешний тип 4."""
    return [Small(type_id=1, name="МОГ", r_km=1.0, k=3, color="sensor"),
            # бонус высоте 0.1 — наименьший, при котором ПВН на ≥ 2 этажах чаще 3 из 20 (замер 13.7: 3 → 5);
            # бонус городу 0 — его цену выбирает заказчик (В13.23, план 13 §13.7.4)
            Small(type_id=2, name="ПВН", r_km=0.5, k=4, color="sensor_t2",
                  zone="застройка", zone_buf_km=0.5, strip_km=0.5, targets=True,
                  bonus_height=0.1, bonus_city=0.0),
            Big(type_id=BIG_ID, name="Большой", r_km=15.0, k=3, color="sensor_big",
                static=True)]


def path():
    """Полный путь справочника — в папке активного участка. Отдаёт: строку."""
    from model.threat_grid import area_cache_dir
    return os.path.join(area_cache_dir(), FILE_NAME)


def _val(row, key, default, kind):
    """Значение из записи файла. Вход: словарь, ключ, умолчание, тип (float/int/str/bool).
    Отдаёт: значение этого типа; нечитаемое — умолчание: битая строка не губит справочник."""
    raw = row.get(key, default)                      # как записано в файле
    try:
        if kind is bool and isinstance(raw, str):    # человек мог написать «да» / «нет»
            return raw.strip().lower() in ("да", "true", "1", "yes")
        if kind is str:                              # пустая строка — как пропуск
            return str(raw).strip() or default
        return kind(float(raw)) if kind is int else kind(raw)
    except (TypeError, ValueError):
        return default


def to_row(st):
    """Тип — в запись файла. Вход: SensorType. Отдаёт: словарь с русскими ключами."""
    row = {"номер": int(st.type_id), "название": st.name, "класс": st.KIND}
    row.update({key: getattr(st, field) for key, field, _ in _KEYS})
    return row


def from_row(row):
    """Запись файла — в тип. Вход: словарь. Отдаёт: SensorType нужного класса.
    ⚠️ Класс номеров 1–4 задан мостом (1–3 малые, 4 большой) — запись его не меняет."""
    tid = _val(row, "номер", 0, int)                 # номер из файла; 0 — не задан
    if tid in LEGACY_IDS:                            # номер моста — класс от номера
        cls = Big if tid == BIG_ID else Small
    else:                                            # новый тип — класс из записи
        cls = KINDS.get(str(row.get("класс", "")).strip(), Small)
    base = cls()                                     # умолчания класса — запасные значения
    name = _val(row, "название", base.name, str)
    return cls(type_id=tid, name=name,
               **{field: _val(row, key, getattr(base, field), kind) for key, field, kind in _KEYS})


def _parse(rows):
    """Записи файла — в справочник. Вход: список словарей. Отдаёт: типы без повторов номера.
    Повтор или номер < 1 отбрасывается: номер — ключ, двум типам под ним не жить."""
    out, seen = [], set()                            # типы и занятые номера
    for r in rows:
        if not isinstance(r, dict):                  # мусор вместо записи
            continue
        st = from_row(r)
        if st.type_id < 1 or st.type_id in seen:     # номер пуст или уже занят
            continue                                 # — первый по файлу остаётся
        seen.add(st.type_id)
        out.append(st)
    return out


def load():
    """Справочник из файла участка. Отдаёт: список типов; файла нет или он пуст — умолчания.
    Прочитанное держится до правки файла: `sensors.sensor_types` зовёт это на каждом расчёте."""
    fp = path()                                      # файл справочника участка
    try:
        mtime = os.path.getmtime(fp)                 # время правки — ключ кэша
    except OSError:                                  # файла нет
        return defaults()                            # — встроенный справочник
    hit = _cache.get(fp)                             # прошлое чтение этого файла или None
    if hit is not None and hit[0] == mtime:          # файл не менялся с прошлого чтения
        return list(hit[1])
    try:
        with io.open(fp, encoding="utf-8") as f:
            rows = json.load(f).get("типы")
    except Exception:                                # повреждён, нет прав —
        rows = None                                  # начинаем со встроенных
    out = _parse(rows) if isinstance(rows, list) else []
    out = out or defaults()                          # пустой список равен отсутствию файла
    _cache[fp] = (mtime, list(out))
    return out


def save(types):
    """Записать справочник. Вход: список типов. Отдаёт: True — записан, False — не вышло."""
    fp = path()                                      # файл справочника участка
    data = {"типы": [to_row(t) for t in types]}      # то, что уйдёт в файл
    try:
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with io.open(fp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        _cache.pop(fp, None)                         # прочитать заново при следующем load()
        return True
    except Exception:                                # нет прав, диск занят — не ронять окно
        return False


def by_id(type_id, types=None):
    """Тип по номеру. Вход: номер, список (по умолчанию — из файла). Отдаёт: тип или None."""
    for st in (load() if types is None else types):
        if st.type_id == int(type_id):
            return st
    return None


def by_name(name, types=None):
    """Тип по названию. Вход: название, список (по умолчанию — из файла). Отдаёт: тип или None."""
    for st in (load() if types is None else types):
        if st.name == name:
            return st
    return None


def next_id(types):
    """Номер для нового типа. Вход: список типов. Отдаёт: наименьший свободный номер ≥ 5.
    Номера 1–4 не выдаются: их числа живут в полях Params, и новый тип их бы унаследовал."""
    used = {int(t.type_id) for t in types}           # занятые номера
    tid = FIRST_FREE_ID                              # кандидат в номера
    while tid in used:
        tid += 1
    return tid
