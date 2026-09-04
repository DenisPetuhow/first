# -*- coding: utf-8 -*-
"""
VIEW (Qt) · ПАМЯТЬ ПОЛЕЙ ВВОДА: последнее введённое значение подставляется снова.

ПРАВИЛО ЗАКАЗЧИКА (04.09.2026). Любое поле ввода и любой выбор файла запоминают то, что
в них ввели в прошлый раз, и подставляют это при следующем открытии — в том числе после
перезапуска программы. Причина простая: при проверке одни и те же четыре координаты и
один и тот же путь к файлу вводятся десятки раз, и каждый раз заново — это и время, и
опечатки.

ЧТО ЭТО НЕ ДЕЛАЕТ. Это не настройки расчёта: параметры модели живут в `config.Params` и
задаются в своих окнах. Здесь только «что было набрано в прошлый раз», чтобы не набирать
снова. Файл можно удалить в любой момент — программа просто начнёт с пустых полей.

ГДЕ ЛЕЖИТ: `geo_cache/ui_state.json` — рядом с остальными локальными данными, в git не
попадает (как и весь `geo_cache/`).
"""
import io
import json
import os

_FILE_NAME = "ui_state.json"
_cache = None


def _path():
    from model.threat_grid import geo_cache_root
    return os.path.join(geo_cache_root(), _FILE_NAME)


def load_all():
    """Всё сохранённое состояние словарём. Ошибки чтения — не беда: пустой словарь."""
    global _cache
    if _cache is not None:
        return _cache
    try:
        with io.open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        _cache = data if isinstance(data, dict) else {}
    except Exception:            # файла нет, повреждён, нет прав — начинаем с чистого
        _cache = {}
    return _cache


def get(section, default=None):
    """Сохранённые значения одного окна (`section`) или `default`."""
    val = load_all().get(section)
    return val if isinstance(val, dict) else (default if default is not None else {})


def save(section, values):
    """Запомнить значения окна. Пишет весь файл целиком — он крошечный.

    Сбой записи (нет прав, диск занят) НЕ должен ронять интерфейс: память полей —
    удобство, а не работа программы. Поэтому исключение гасится."""
    data = load_all()
    data[section] = dict(values)
    try:
        with io.open(_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    except Exception:
        pass
    return True
