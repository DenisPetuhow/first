# -*- coding: utf-8 -*-
"""
ШАГ 7 СВЕРКИ · МЁРТВЫЙ КОД: функции, которые нигде не вызываются, и параметры
конфигурации, которые никто не читает.

    python tools/переносимое/check_dead_code.py
    python tools/переносимое/check_dead_code.py --config config.py

ЧТО ЛОВИТ. Функция осталась от прежней схемы, её перестали вызывать, но она живёт,
занимает место в файле и попадает в документацию как действующая. То же с параметром:
имя в `config.py` есть, а читать его давно некому — правка такого параметра «ничего не
меняет», и на это уходит вечер.

⚠️ СКРИПТ ВРЁТ ЧАЩЕ, ЧЕМ КОД. Каждую находку проверять глазами — вот законные случаи,
которые он не отличает и не должен:

  1. **ДОКУМЕНТИРОВАННЫЙ API.** Функция не вызывается из программы, но на ней стоит
     раздел методички («карту храним растрово — `save_npz`»). Удалять нельзя: исчезнет
     то, что объясняет решение.
  2. **УМОЛЧАНИЯ ВНУТРИ САМОГО КОНФИГА.** Константа читается только в `config.py` — она
     задаёт значение по умолчанию для поля `Params`. Такие помечаются отдельно: «не
     читается ВНЕ config».
  3. **ВЫЗОВ ЧЕРЕЗ ГЕТАТТР ИЛИ СТРОКУ.** `getattr(obj, name)`, диспетчеризация по
     словарю, Qt-слоты по имени — статически не видны.
  4. **ТОЧКА ВХОДА.** `main`, `run`, обработчики — их вызывает не код, а запуск.
  5. **ЭТАЛОННАЯ РЕАЛИЗАЦИЯ РЯДОМ С БЫСТРОЙ.** Точный, но медленный вариант держат,
     чтобы сверять с ним оптимизированный (в этом проекте — `_accumulate_polyline`
     против векторного `_rasterize_polylines`). Удалить его — потерять способ проверить
     быстрый.
  6. **КОД, СПЯЩИЙ ВМЕСТЕ С ВЫКЛЮЧЕННЫМ МЕХАНИЗМОМ.** Механизм отключён (у нас —
     «якоря», журнал п. 201), его вызов закомментирован, но функция нужна, если механизм
     вернут. Признак: имя встречается в тексте, а вызовов нет.

Первый прогон на этом проекте дал **5 находок, и все пять оказались законными** —
по случаям 1, 5 и 6. Это нормальный результат: скрипт сокращает выборку с 582 функций
до пяти, а решение принимает человек.

ПОЧЕМУ ЭТО ОТДЕЛЬНЫЙ ШАГ, А НЕ ЧАСТЬ check_names. `check_names.py` идёт от ТЕКСТОВ к
коду: «в документации написано имя, а в коде его нет». Здесь наоборот — от КОДА к коду:
имя есть, но им никто не пользуется. Первый ловит устаревшую документацию, второй —
устаревший код, и подменять их друг другом нельзя.
"""
import argparse
import ast
import io
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# корень ищем по маркеру, а не счётом уровней вверх: так скрипт переносится в любой
# проект и не ломается от переезда в другую подпапку
MARKERS = ("CLAUDE.md", "config.py", ".git")
SKIP_DIRS = {".git", ".claude", "__pycache__", "venv", ".venv", "node_modules",
             "tile_cache", "geo_cache", "offline_packages", "resurce", "docs",
             "теория", "dist", "build"}
# имена, которые вызывает не код, а запуск/фреймворк
ENTRY_POINTS = {"main", "run", "setup", "teardown"}


def find_root(start):
    d = os.path.abspath(start)
    while True:
        if any(os.path.exists(os.path.join(d, m)) for m in MARKERS):
            return d
        up = os.path.dirname(d)
        if up == d:
            return os.path.abspath(start)
        d = up


def py_files(root):
    out = []
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        out += [os.path.join(cur, f) for f in files if f.endswith(".py")]
    return out


def collect_defs(files):
    """{имя функции: [(файл, строка)]} — включая методы классов."""
    defs = {}
    for f in files:
        try:
            tree = ast.parse(io.open(f, encoding="utf-8", errors="replace").read())
        except SyntaxError as e:
            print("  ! не разобран: %s (%s)" % (f, e))
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.setdefault(node.name, []).append((f, node.lineno))
    return defs


def collect_uses(files):
    """{имя: сколько раз к нему обращаются} — ПО ДЕРЕВУ, а не по тексту.

    Учитываются `f(...)`, `obj.f(...)`, передача функции по имени (`key=f`), декораторы,
    **импорты** (`from .geometry import point_in_ellipse`) и **имена в `__all__`**.

    ⚠️ Два последних добавлены после ложной тревоги: без них скрипт объявил мёртвыми
    четыре функции, которые пакет `model/__init__.py` реэкспортирует наружу как свой
    публичный API. Вызовов внутри проекта у них действительно нет — но они и не для
    внутреннего употребления.

    Комментарии и прочие строки сюда не попадают намеренно: упоминание имени в
    комментарии рядом с определением («# save_npz больше не нужна») иначе маскировало бы
    мёртвую функцию — ровно тот случай, ради которого скрипт и написан."""
    uses = {}

    def add(name):
        if name:
            uses[name] = uses.get(name, 0) + 1

    for f in files:
        try:
            tree = ast.parse(io.open(f, encoding="utf-8", errors="replace").read())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                add(node.id)
            elif isinstance(node, ast.Attribute):
                add(node.attr)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for al in node.names:
                    add(al.name.split(".")[-1])
            elif isinstance(node, ast.Assign):
                # __all__ = ["make_arc", ...] — публичный список пакета
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if "__all__" in names and isinstance(node.value, (ast.List, ast.Tuple)):
                    for el in node.value.elts:
                        if isinstance(el, ast.Constant) and isinstance(el.value, str):
                            add(el.value)
    return uses


def unused_functions(files, defs):
    """Функции без единого обращения в коде. Возвращает (имя, места, упоминаний в тексте)."""
    text = "\n".join(io.open(f, encoding="utf-8", errors="replace").read() for f in files)
    uses = collect_uses(files)
    out = []
    for name, places in sorted(defs.items()):
        if name.startswith("__") or name in ENTRY_POINTS:
            continue
        if uses.get(name, 0) > 0:
            continue
        # имя нигде не вызывается; но если оно встречается в ТЕКСТЕ — это либо вызов
        # по строке (getattr, диспетчер, Qt-слот), либо комментарий: пометим числом
        mentions = len(re.findall(r"\b%s\b" % re.escape(name), text)) - len(places)
        out.append((name, places, max(0, mentions)))
    return out


def unused_config(root, cfg_path, files):
    """Константы конфигурации, не читаемые вне самого файла конфигурации."""
    if not os.path.exists(cfg_path):
        return []
    src = io.open(cfg_path, encoding="utf-8", errors="replace").read()
    names = [t.id for node in ast.parse(src).body if isinstance(node, ast.Assign)
             for t in node.targets if isinstance(t, ast.Name) and t.id.isupper()]
    other = "\n".join(io.open(f, encoding="utf-8", errors="replace").read()
                      for f in files if os.path.abspath(f) != os.path.abspath(cfg_path))
    out = []
    for n in names:
        if re.search(r"\b%s\b" % n, other):
            continue
        inside = len(re.findall(r"\b%s\b" % n, src)) - 1
        out.append((n, inside))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="функции без вызовов и непрочитанные параметры")
    ap.add_argument("--root", default=None, help="корень проекта (по умолчанию — по маркеру)")
    ap.add_argument("--config", default="config.py", help="файл параметров (по умолчанию %(default)s)")
    a = ap.parse_args(argv)

    root = a.root or find_root(os.path.dirname(os.path.abspath(__file__)))
    files = py_files(root)
    defs = collect_defs(files)
    dead = unused_functions(files, defs)
    cfg = unused_config(root, os.path.join(root, a.config), files)

    print("корень: %s;  файлов .py: %d;  функций: %d" % (root, len(files), len(defs)))
    print("\nФУНКЦИИ БЕЗ ЕДИНОГО ВЫЗОВА: %d" % len(dead))
    for name, places, mentions in dead:
        note = ("" if not mentions
                else "   ← но имя встречается в тексте %d раз: вызов по строке "
                     "(getattr/диспетчер) или комментарий" % mentions)
        for f, ln in places:
            print("  %-28s %s:%d%s" % (name, os.path.relpath(f, root), ln, note))
            note = ""
    print("\nПАРАМЕТРЫ, НЕ ЧИТАЕМЫЕ ВНЕ %s: %d" % (a.config, len(cfg)))
    for name, inside in cfg:
        note = "  ← умолчание внутри файла" if inside else "  ← не читается вообще"
        print("  %-28s упоминаний внутри: %d%s" % (name, inside, note))
    print("\n(каждую находку проверить глазами — четыре законных случая перечислены")
    print(" в шапке скрипта; что решено по прежним находкам — в журнале проекта)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
