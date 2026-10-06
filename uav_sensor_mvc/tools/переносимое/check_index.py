# -*- coding: utf-8 -*-
"""ШАГ 10 сверки: УКАЗАТЕЛИ против папок — каждый файл документации и кода назван в своём указателе.

ЧТО ЛОВИТ. Файл завели, а строку в указателе («что лежит в каждом файле») не дописали. Имена, числа и ссылки при этом
верные — врёт только полнота. Замер 06.10.2026: в СТРУКТУРА_ТЕОРИИ не было 24 файлов, в СТРУКТУРА_ПРОЕКТА — 4.
ЗАПУСК: `python tools/переносимое/check_index.py`. При переносе в другой проект менять только INDEXES и маркеры.
"""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):              # консоль Windows — кириллица без порчи
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _find_root(markers=("CLAUDE.md", "config.py")):
    """Корень проекта — ближайшая папка вверх с любым из маркеров. Вход: маркеры. Отдаёт: путь."""
    d = os.path.dirname(os.path.abspath(__file__))   # откуда начинаем подъём
    while True:                                      # вверх по папкам до маркера
        if any(os.path.exists(os.path.join(d, m)) for m in markers):   # корень найден
            return d
        up = os.path.dirname(d)                      # папка выше
        if up == d:                                  # дошли до диска — маркера нет
            raise SystemExit("не найден корень проекта: нет ни одного из %s" % list(markers))
        d = up


ROOT = _find_root()                                  # корень проекта
# (указатель, папки с файлами, расширение, папки-исключения, имена-исключения)
INDEXES = [
    ("теория/структура/СТРУКТУРА_ТЕОРИИ.md", ["теория"], ".md", {"основа"}, {"ЖУРНАЛ_ПАПКИ.md", "0.ЖУРНАЛ_ПАПКИ.md"}),
    ("теория/структура/СТРУКТУРА_ПРОЕКТА.md", ["model", "view_qt", "controller_qt", "tools"], ".py", {"__pycache__"},
     {"__init__.py"}),
]


def missing(index, dirs, ext, skip_dirs, skip_names):
    """Файлы без упоминания в указателе. Вход: путь указателя, папки, расширение, исключения. Отдаёт: [пути]."""
    text = open(os.path.join(ROOT, index), encoding="utf-8").read()   # указатель целиком
    out = []                                         # не названные в указателе
    for top in dirs:                                 # каждая папка, что описывает указатель
        for d, subs, files in os.walk(os.path.join(ROOT, top)):   # и её подпапки
            subs[:] = [s for s in subs if s not in skip_dirs]
            for f in files:                          # файлы папки
                if f.endswith(ext) and f not in skip_names and f not in text:   # нужного вида и не назван
                    out.append(os.path.relpath(os.path.join(d, f), ROOT).replace(os.sep, "/"))
    return sorted(out)


def main():
    """Сверить все указатели, напечатать пропуски. Вход: нет. Отдаёт: код — 1, если пропуски есть."""
    total = 0                                        # пропусков во всех указателях
    for index, dirs, ext, skip_dirs, skip_names in INDEXES:   # указатель за указателем
        miss = missing(index, dirs, ext, skip_dirs, skip_names)   # чего в нём нет
        total += len(miss)
        print("%s: не названо файлов %d" % (index, len(miss)))
        for m in miss:                               # каждый пропуск — строкой
            print("   " + m)
    print("УКАЗАТЕЛИ ПОЛНЫЕ" if not total else "ПРОПУСКОВ В УКАЗАТЕЛЯХ: %d — дописать строки" % total)
    return 1 if total else 0


if __name__ == "__main__":                           # запуск скриптом, не импорт
    sys.exit(main())
