# -*- coding: utf-8 -*-
"""
MODEL · Файл истории полётов — выгрузка и загрузка накопленной выборки маршрутов
(задача 8.6, план 8 «Карта и интерфейс»). Новый механизм — свой модуль, а не довесок
к `threat_grid.py` или `input_window.py` (правило проекта: новый крупный функционал —
сразу в отдельный файл, CLAUDE.md).

Формат и все семь общих правил ввода-вывода — теория/МЕТОДИЧКА_ВВОД_ВЫВОД.md §2, §5.
Разбор устроен ПО ОБРАЗЦУ файла датчиков (`view_qt/input_window.py ::
write_sensors_file/read_sensors_file`) — тот же принцип «одна плохая строка не роняет
чтение», тот же UTF-8 без BOM, но своё содержимое: маршрутов сотни, точек в каждом —
десятки. ⚠️ ФОРМАТ ТОЧЕК — НЕ «СТРОКА НА ТОЧКУ» (было в первой версии, заказчик
06.09.2026: «очень длинно»), а СТРОКА НА МАРШРУТ: все точки одного маршрута через " | ",
`N - lon lat`, — на 150 маршрутов × ~60 точек это 150 строк вместо 9 000.

⚠️ КООРДИНАТЫ — ТОЛЬКО ГРАДУСЫ, участок и якорь в шапке НЕ проверяются. Маршрут из
другого района загружается и показывается как есть — координаты самодостаточны, точно
так же, как заданные вручную датчики: это ВХОДНЫЕ ДАННЫЕ, а не результат привязки к
конкретному участку (план 8 §8.6, «открытые вопросы» — умолчание записано заранее).
"""
import io
import os
import re
import time

import numpy as np

FILE_MARK = "UAV-ROUTES"
FILE_VERSION = "v1"

# ── СВОЯ ПАПКА ФАЙЛОВ МАРШРУТОВ — та же схема, что у датчиков, но отдельная (03.09.2026:
# входные и выходные файлы разных механизмов не смешивать в одной папке) ──────────────
ROUTES_DIR = "маршруты"
DIR_LOAD, DIR_SAVE = "загрузка", "выгрузка"

# Строка-заголовок маршрута: "# R 3  длина 141.8 км". Опознаётся ДО общей проверки "#" —
# комментарий, чтобы не спутать с шапкой файла (дата, число маршрутов и т.п.).
_ROUTE_HEAD_RE = re.compile(r"^#\s*R\s*(\d+)\b", re.IGNORECASE)

# Одна точка внутри строки маршрута: "3 - 41.410034 63.137930" — номер и дефис
# необязательны (строку могут набрать рукой просто парой чисел), поэтому в скобках.
_POINT_RE = re.compile(r"^(?:\d+\s*-\s*)?([+-]?[\d.]+)\s+([+-]?[\d.]+)$")


def routes_dir(kind):
    """Папка файлов маршрутов: `маршруты/загрузка` или `маршруты/выгрузка`.

    Создаётся при первом обращении, считается от КОРНЯ ПРОГРАММЫ (не от `os.getcwd()`) —
    как `view_qt/input_window.py :: sensors_dir`. Не удалось создать — отдаём корень
    программы, диалог всё равно откроется."""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ROUTES_DIR, kind)
    try:
        if not os.path.isdir(root):
            os.makedirs(root)
    except OSError:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return root


def default_file_name(when=None):
    """Имя файла выгрузки: `2026_09_05_10_20_routes.txt`.

    Год первым, разделитель «_» — те же правила, что у датчиков (сортировка по алфавиту
    совпадает с сортировкой по времени)."""
    return time.strftime("%Y_%m_%d_%H_%M_routes.txt", when or time.localtime())


def _route_length_km(route_km):
    """Длина маршрута в километрах — считается по КИЛОМЕТРОВОМУ представлению (точнее и
    дешевле, чем пересчитывать обратно из градусов)."""
    a = np.asarray(route_km, float).reshape(-1, 2)
    if len(a) < 2:
        return 0.0
    return float(np.sum(np.hypot(np.diff(a[:, 0]), np.diff(a[:, 1]))))


def save_flights(path, model):
    """Записать НАКОПЛЕННЫЕ итерационные маршруты (`model.iter_routes`) в текстовый файл.

    Координаты — только градусы (`model.routes_lonlat()`); длина маршрута в его
    строке-заголовке — по километровому представлению. ⚠️ ВСЕ ТОЧКИ ОДНОГО МАРШРУТА —
    ОДНОЙ СТРОКОЙ, через " | " (заказчик 06.09.2026: построчно на точку выходило слишком
    длинно). UTF-8 БЕЗ BOM (правило 5, МЕТОДИЧКА_ВВОД_ВЫВОД §2). Возвращает
    `(маршрутов, точек всего)`."""
    routes_km = list(model.iter_routes)
    routes_deg = model.routes_lonlat()
    n_points = sum(len(r) for r in routes_deg)
    lines = ["# %s %s" % (FILE_MARK, FILE_VERSION),
             "# дата: %s" % time.strftime("%Y-%m-%d %H:%M"),
             "# маршрутов: %d   точек всего: %d" % (len(routes_deg), n_points),
             "#"]
    for i, (r_km, r_deg) in enumerate(zip(routes_km, routes_deg), 1):
        lines.append("# R %d  длина %.1f км" % (i, _route_length_km(r_km)))
        lines.append(" | ".join(
            "%d - %.6f %.6f" % (j, float(lon), float(lat))
            for j, (lon, lat) in enumerate(r_deg, 1)))
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return len(routes_deg), n_points


def load_flights(path):
    """Прочитать файл истории полётов → `(маршруты, отчёт)`.

    Маршруты — список массивов `(M,2)` в градусах (lon, lat). Отчёт — словарь:
    `recognized` (шапка UAV-ROUTES опознана), `n_routes`, `n_points`, `rejected`
    (список `(номер строки, причина)`) — та же дисциплина, что у `read_sensors_file`:
    ⚠️ ОДНА ПЛОХАЯ ТОЧКА НЕ РОНЯЕТ ЧТЕНИЕ ВСЕЙ СТРОКИ (а значит и всего маршрута) —
    точки внутри строки разбираются по одной, плохая просто выпадает."""
    routes, cur, rejected = [], [], []
    recognized = False
    with io.open(path, encoding="utf-8-sig") as f:      # -sig снимает BOM блокнота
        raw = f.read()
    for no, line in enumerate(raw.splitlines(), 1):
        s = line.strip().lstrip("﻿")
        if not s:
            continue
        if s.startswith("#"):
            if s[1:].strip().upper().startswith(FILE_MARK):
                recognized = True
                continue
            if _ROUTE_HEAD_RE.match(s):
                if cur:
                    routes.append(np.asarray(cur, float))
                cur = []
            continue                                    # прочий комментарий шапки
        for seg in s.split("|"):
            seg = seg.strip().replace(",", ".")
            if not seg:
                continue
            m = _POINT_RE.match(seg)
            if not m:
                rejected.append((no, "точка не разобрана: %r" % seg[:40]))
                continue
            try:
                lon, lat = float(m.group(1)), float(m.group(2))
            except ValueError:
                rejected.append((no, "координаты — не число"))
                continue
            if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
                rejected.append((no, "координаты вне земного диапазона"))
                continue
            cur.append((lon, lat))
    if cur:
        routes.append(np.asarray(cur, float))
    n_points = sum(len(r) for r in routes)
    return routes, dict(recognized=recognized, n_routes=len(routes),
                        n_points=n_points, rejected=rejected)


def check_compatible(routes, report):
    """Причина, по которой файл считается НЕПОДХОДЯЩИМ, либо `None`.

    ⚠️ РАЙОН И ЯКОРЬ НЕ ПРОВЕРЯЮТСЯ (сознательное решение — план 8 §8.6, «открытые
    вопросы»; теория/МЕТОДИЧКА_ВВОД_ВЫВОД.md §5): координаты в градусах самодостаточны,
    как и у датчиков. Маршрут из другого участка просто ложится в другое место сцены —
    программа его принимает и показывает, а не отвергает."""
    if not report.get("recognized", False):
        return "файл не в формате %s — не найдена опознавательная строка в шапке" % FILE_MARK
    if not routes:
        return "в файле не найдено ни одного маршрута"
    return None
