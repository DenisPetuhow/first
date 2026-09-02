# -*- coding: utf-8 -*-
"""ШАГ 1 сверки на достоверность: ИМЕНА из документации — против кода.

ЧТО ЛОВИТ. Текст ссылается на функцию, параметр или класс, которых в коде уже нет:
переименовали или удалили, а документацию не поправили. Имя выглядит живым, поэтому
глазами это не находится — только сверкой со списком имён программы.

ПОЧЕМУ РАНЬШЕ БЫЛО 146 «НАХОДОК», А НАСТОЯЩИХ НОЛЬ. Первая версия собирала из кода
только функции и классы, а моноширинным шрифтом в тексте набирают что угодно: чужие
библиотеки, классы Qt, встроенные ошибки Python, строковые ЗНАЧЕНИЯ (теги OSM, режимы),
имена папок. Здесь всё это учтено — выборка сократилась примерно в семь раз.

ЧТО СКРИПТ НЕ РЕШАЕТ. Он сокращает выборку, а не выносит приговор: каждую оставшуюся
находку смотреть глазами. Из его же истории: `smoke_all` — не ошибка, а ключевое слово
для поиска исторического пункта журнала.

ЗАПУСК:
    python tools/переносимое/check_names.py           # методички, ОГРАНИЧЕНИЯ, CLAUDE
    python tools/переносимое/check_names.py --all     # плюс журнал и архив
"""
import ast
import builtins
import io
import keyword
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def _find_root(markers=("CLAUDE.md", "config.py")):
    """Корень проекта — ближайшая папка ВВЕРХ, содержащая любой из маркеров.

    Не «посчитать уровни»: `dirname` N раз означает «лежу ровно на N уровней ниже
    корня» и врёт при каждом переносе папки, причём МОЛЧА — скрипт запускается и
    просто смотрит не туда. Так уже было при переезде в tools/docs.
    При переносе в другой проект менять только список маркеров.
    """
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        if any(os.path.exists(os.path.join(d, m)) for m in markers):
            return d
        up = os.path.dirname(d)
        if up == d:
            raise SystemExit("не найден корень проекта: вверх от %s нет ни одного из %s"
                             % (os.path.dirname(os.path.abspath(__file__)), list(markers)))
        d = up


ROOT = _find_root()
CODE_SKIP = {".git", ".claude", "теория", "__pycache__", "tile_cache", "geo_cache",
             "offline_packages", "фото пример"}
# ⚠️ Папка `docs` исключается ТОЛЬКО в корне (там генераторы .docx для заказчика).
# По имени исключать нельзя: `tools/docs` — эти самые скрипты, и вместе с ними из
# проверки выпадали бы их собственные имена (проверка выдавала 18 ложных находок).
CODE_SKIP_ROOT = {"docs"}
# журнал и архив описывают ПРОШЛОЕ: там имена удалённых функций законны и нужны
HIST_DIRS = {"журнал", "архив"}

# ЧУЖИЕ ТЕРМИНЫ — они и не должны быть в нашем коде. Держим списком, а не выдумываем
# правило: любое «умное» правило либо пропустит настоящую ошибку, либо съест лишнее.
EXTERNAL = {
    # PyInstaller: поля рецепта сборки и папки результата (СБОРКА_EXE.md)
    "hiddenimports", "datas", "excludes", "collect_submodules", "collect_data_files",
    "_internal", "venv", "work", "diag", "test", "spec", "onefile", "noconsole",
    # библиотеки, УПОМЯНУТЫЕ как варианты, но в проект не взятые
    "folium", "mercantile", "osmium", "pyqtlet2", "QtLocation", "QtWebEngine",
    "Leaflet", "MapLibre", "geopandas", "Pillow",
    # инструменты чтения файлов помощником (CLAUDE.md), не код проекта
    "limit", "offset",
    # параметры чужих библиотек, встречающиеся в примерах вызовов
    "bounding_box", "overlay",
    # обозначения ИЗ ФОРМУЛ методички — это математика, а не имена в коде
    "popcount", "sign_i", "segmask",
    # то же из пособия: S_greedy — жадное решение в теореме, weight_layer — вес слоя
    # в формуле вклада (в коде это ключ `weight` внутри словаря THREAT_LAYERS)
    "S_greedy", "weight_layer",
    # ПРАВИЛА РАБОТЫ (теория/переносимое/5_РАЗРЕШЕНИЯ_КОМАНД, 6_КОММИТЫ_GIT) говорят
    # про инструменты помощника, ключи settings.json и подкоманды git — это внешние
    # имена, в коде проекта их нет и быть не должно
    "Bash", "Glob", "Grep", "PowerShell", "Read", "Write", "Edit",
    "allow", "ask", "defaultMode", "permissions",
    "status", "branch", "checkout", "switch", "commit", "push", "rebase", "amend",
    "grep", "sed", "awk", "head", "tail",
    # ПЛАНЫ описывают то, чего в коде ЕЩЁ НЕТ: имена будущих параметров и библиотеки,
    # которые предстоит подключить. До реализации это не расхождение, а замысел —
    # после реализации имена появятся в коде и фильтр перестанет быть нужен.
    # `threat_N_big` / `threat_R_big` из этого списка УБРАНЫ: реализованы 29.08.2026.
    "threat_k_big",
    # план 7 (адаптивный порог «город» по данным участка) — ещё не реализован
    "city_area_threshold", "THREAT_CITY_MIN_KM2", "THREAT_CITY_GAP_MIN",
    "pyshp", "fiona", "geopandas", "fastkml", "ezdxf", "shapefile", "json",
    "cp1251", "d_min",          # кодировка и обозначение из формулы плана
}
# ВЕТКА demo-build / exe-build: этих имён в рабочей ветке нет намеренно — не расхождение.
# ⚠️ `UAV_SELFTEST` из списка УБРАН 02.09.2026: режим перенесён в общий код
# (`main_qt.py` :: `selftest`), и теперь скрипт обязан его находить. Фильтр, оставленный
# после переноса, молча скрыл бы настоящую пропажу имени.
DEMO_BRANCH = {"HELP_HTML", "HelpDialog", "DEMO_ONLY_THREAT", "DEMO_MAP_LAYERS",
               "DEMO_FORCE_OFFLINE", "DEMO_CONSOLE"}


def collect_code_names():
    """Всё, что в коде существует: имена, значения строк, импорты, папки проекта."""
    names, strings, modules = set(), set(), set()

    for root, dirs, files in os.walk(ROOT):
        skip = CODE_SKIP | (CODE_SKIP_ROOT if os.path.abspath(root) == ROOT else set())
        dirs[:] = [d for d in dirs if d not in skip]
        # имена папок проекта тоже «существуют»: их упоминают как tile_cache, view_qt
        names.update(dirs)
        for f in files:
            if f.endswith(".py"):
                names.add(f[:-3])                       # имя модуля: geomap, docmap
            if not f.endswith(".py"):
                continue
            src = io.open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        names.update(a.arg for a in node.args.args)
                        names.update(a.arg for a in node.args.kwonlyargs)
                elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr)          # self.weight, grid.layers
                elif isinstance(node, ast.arg):
                    names.add(node.arg)
                # СТРОКОВЫЕ ЗНАЧЕНИЯ: "town", "road_major", "late" — это не имена
                # переменных, а данные; в текстах они тоже набраны кодом
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for part in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", node.value):
                        strings.add(part)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    if isinstance(node, ast.Import):
                        for a in node.names:
                            modules.add(a.name.split(".")[0])
                            if a.asname:
                                modules.add(a.asname)
                    else:
                        if node.module:
                            modules.add(node.module.split(".")[0])
                        modules.update(a.name for a in node.names)
            names.update(re.findall(r'environ(?:\.get)?\(?\[?["\'](\w+)', src))
            names.update(re.findall(r'getenv\(\s*["\'](\w+)', src))
    return names, strings, modules


def is_noise(tok, names, strings, modules):
    """Заведомо не наше имя: язык, библиотеки, Qt, значения-строки."""
    if tok in names or tok in strings or tok in modules:
        return True
    if tok in EXTERNAL or tok in DEMO_BRANCH:
        return True                                   # чужие термины и ветка demo-build
    if tok in dir(builtins) or keyword.iskeyword(tok):
        return True                                   # True, None, except, ValueError
    if re.match(r"^Q[A-Z]", tok):
        return True                                   # QGraphicsScene, QWebEngineView
    if re.match(r"^[NS]\d{2}[EW]\d{3}$", tok):
        return True     # имя тайла рельефа SRTM (N62E040) — файл, а не имя в коде
    if re.fullmatch(r"[0-9a-f]{7,40}", tok) and re.search(r"\d", tok):
        return True     # ХЭШ КОММИТА (c547ccc, 33e86a0): журнал ссылается на точки
                        # восстановления, и это не имя в коде. Обязательна цифра —
                        # иначе под правило попало бы обычное слово из букв a…f
    return False


def docs(include_history):
    out = []
    for root, dirs, files in os.walk(os.path.join(ROOT, "теория")):
        if not include_history:
            dirs[:] = [d for d in dirs if d not in HIST_DIRS]
        out += [os.path.join(root, f) for f in files if f.endswith(".md")]
    out += [os.path.join(ROOT, "CLAUDE.md"), os.path.join(ROOT, "README.md")]
    return [p for p in out if os.path.exists(p)]


def main():
    include_history = "--all" in sys.argv
    names, strings, modules = collect_code_names()
    print("из кода собрано: имён %d, слов в строковых значениях %d, модулей %d"
          % (len(names), len(strings), len(modules)))

    found = {}
    for d in docs(include_history):
        lines = io.open(d, encoding="utf-8").read().split("\n")
        for line_no, line in enumerate(lines, 1):
            # ОКНО ±2 СТРОКИ, а не одна: пометка «удалён из кода» часто стоит на
            # соседней строке из-за переноса — по одной строке она не находилась
            near = "\n".join(lines[max(0, line_no - 3):line_no + 2])
            # «вместо» — разбор УЖЕ найденной ошибки: «в тексте X, в коде Y».
            # Без этого маркера собственный отчёт о находке всплывает как находка.
            if re.search(r"удал|НЕ АКТУАЛ|не актуал|больше нет|прежн|было\b|вместо|→|✗", near):
                continue
            for tok in re.findall(r"`([A-Za-z_][A-Za-z_0-9]*)`", line):
                if len(tok) < 4 or is_noise(tok, names, strings, modules):
                    continue
                found.setdefault(tok, []).append("%s:%d" % (os.path.basename(d), line_no))

    print("\nупоминается в текстах, но в коде НЕ НАЙДЕНО: %d" % len(found))
    print("(каждую находку проверить глазами — скрипт только сокращает выборку)\n")
    for tok in sorted(found):
        print("  %-30s %s" % (tok, ", ".join(found[tok][:3])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
