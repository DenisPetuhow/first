# -*- coding: utf-8 -*-
"""
MODEL · Типы БПЛА: характеристики аппарата как предмет выбора (план 12, задача 12.3).

Раньше условия полёта задавались «вообще»: одна скорость, один крен, один запас хода на
всю выборку. Здесь они собраны в тип — то, что выбирают по названию («Лютый») и что несёт
свои числа.

⚠️ КЛАСС РЕШАЕТ, КАК ЛЕТИТ; ЗАПИСЬ В ФАЙЛЕ — КАКИМИ ЧИСЛАМИ. Наследование заведено не ради
себя: у самолётного типа радиус разворота считается по крену, а мультиротор
разворачивается на месте, и крен на манёвренность не влияет. Различались бы одни числа —
хватило бы одного класса.

ГДЕ СПРАВОЧНИК: `geo_cache/типы_бпла.json` — рядом с остальными данными человека, в git не
попадает. Файла нет — берутся встроенные умолчания, и он создаётся при первой записи.
Разбор — [план 12 §4](../теория/планы/12_ПЛАН_УСЛОВИЯ_МОДЕЛИРОВАНИЯ.md).
"""
import io
import json
import os
from dataclasses import dataclass

FILE_NAME = "типы_бпла.json"     # имя справочника в geo_cache


@dataclass(frozen=True)
class UavType:
    """Аппарат: название и характеристики. Поля и проверки — здесь, расчёт разворота — у
    наследников. Неизменяем: тип выбран, и числа под ним не должны меняться по дороге."""

    name: str = "БПЛА"           # то, что видно в списке выбора
    speed_kmh: float = 180.0     # крейсерская скорость, км/ч
    bank_deg: float = 25.0       # предельный крен, градусы
    L_max_km: float = 1500.0     # запас хода, км
    alt_m: float = 150.0         # высота полёта, м — ⚠️ ЗАВЕДЕНА, В РАСЧЁТ НЕ ИДЁТ

    KIND = "самолёт"             # как класс записывается в файле справочника

    # Пределы здравого смысла — не «правильные» значения, а те, вне которых число заведомо
    # опечатка: 18 000 км/ч или крен 89° модель примет молча и выдаст ерунду.
    LIMITS = (
        ("speed_kmh", 1.0, 1000.0, "скорость, км/ч"),
        ("bank_deg", 1.0, 80.0, "крен, °"),
        ("L_max_km", 1.0, 40000.0, "запас хода, км"),
        ("alt_m", 0.0, 20000.0, "высота, м"),
    )

    def validate(self):
        """Жалобы на свои числа. Отдаёт: список строк, пустой — всё в порядке."""
        bad = []                                     # жалобы, по строке на поле
        if not str(self.name).strip():               # без названия тип не выбрать
            bad.append("название пустое")            # — в списке будет пустая строка
        for field, lo, hi, title in self.LIMITS:
            val = float(getattr(self, field))
            if not (lo <= val <= hi):                # число вне здравого смысла
                bad.append("%s: %g — ждём от %g до %g" % (title, val, lo, hi))
        return bad

    def turn_radius_km(self):
        """Минимальный радиус разворота, км — по общей формуле проекта, чтобы тип и модель
        не разошлись."""
        from model.trajectories import turn_radius_km
        return float(turn_radius_km(self.speed_kmh / 3.6, self.bank_deg))


@dataclass(frozen=True)
class FixedWing(UavType):
    """Самолётного типа: летит вперёд, курс меняет дугой — её радиус и задаёт крен."""

    KIND = "самолёт"             # как этот класс записан в файле справочника


@dataclass(frozen=True)
class Multirotor(UavType):
    """Мультиротор: разворачивается на месте, поэтому крен на манёвренность не влияет."""

    KIND = "мультиротор"         # как этот класс записан в файле справочника

    def turn_radius_km(self):
        """Ноль: аппарат меняет курс, не смещаясь."""
        return 0.0


KINDS = {FixedWing.KIND: FixedWing, Multirotor.KIND: Multirotor}   # запись в файле -> класс

# Ключи файла — по-русски, как в `область.txt`: справочник правит человек, а не программа.
_KEYS = (
    ("скорость_кмч", "speed_kmh"),
    ("крен_град", "bank_deg"),
    ("запас_км", "L_max_km"),
    ("высота_м", "alt_m"),
)
# поле класса -> ключ файла; нужен тому, кто собирает запись по одному полю (окно правки)
KEY_BY_FIELD = dict([("name", "название")] + [(f, k) for k, f in _KEYS])


def defaults():
    """Встроенный справочник — с чего начинают, когда файла ещё нет. Отдаёт: список типов.
    «Лютый» — пример заказчика (20.09.2026); «Коптер» показывает второй класс."""
    return [FixedWing("Лютый", 180.0, 25.0, 1500.0, 150.0),
            Multirotor("Коптер", 60.0, 15.0, 30.0, 120.0)]


def path():
    """Полный путь справочника. Отдаёт: строку."""
    from model.threat_grid import geo_cache_root
    return os.path.join(geo_cache_root(), FILE_NAME)


def _num(row, key, default):
    """Число из записи файла. Вход: словарь, ключ, умолчание. Отдаёт: float.
    Нечитаемое значение — умолчание: одна битая строка не должна губить весь справочник."""
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        return float(default)


def to_row(uav):
    """Тип — в запись файла. Вход: UavType. Отдаёт: словарь с русскими ключами."""
    row = {"название": uav.name, "класс": uav.KIND}
    row.update({key: getattr(uav, field) for key, field in _KEYS})
    return row


def from_row(row):
    """Запись файла — в тип. Вход: словарь. Отдаёт: UavType нужного класса.
    Чужой или пропущенный «класс» — самолётный: он же умолчание модели."""
    cls = KINDS.get(str(row.get("класс", "")).strip(), FixedWing)
    base = cls()                                     # умолчания класса — запасные значения
    name = str(row.get("название", base.name)).strip() or base.name
    return cls(name, **{field: _num(row, key, getattr(base, field))
                        for key, field in _KEYS})


def load():
    """Справочник из файла. Отдаёт: список типов; файла нет или он пуст — умолчания."""
    try:
        with io.open(path(), encoding="utf-8") as f:
            rows = json.load(f).get("типы")
    except Exception:                                # нет файла, повреждён, нет прав —
        rows = None                                  # начинаем со встроенных
    if not isinstance(rows, list):                   # вместо списка что-то другое
        return defaults()                            # — файлу не верим
    out = [from_row(r) for r in rows if isinstance(r, dict)]
    return out or defaults()                         # пустой список равен отсутствию файла


def save(types):
    """Записать справочник. Вход: список типов. Отдаёт: True — записан, False — не вышло."""
    data = {"типы": [to_row(t) for t in types]}
    try:
        with io.open(path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        return True
    except Exception:                                # нет прав, диск занят — не ронять окно
        return False


def by_name(name, types=None):
    """Тип по названию. Вход: название, список (по умолчанию читается из файла).
    Отдаёт: тип или None — что делать с пропажей, решает вызывающий."""
    for uav in (load() if types is None else types):
        if uav.name == name:
            return uav
    return None
