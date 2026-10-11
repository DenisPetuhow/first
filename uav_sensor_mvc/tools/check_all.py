# -*- coding: utf-8 -*-
"""Все проверки одной командой: полный вывод — в файлы, на экран — одна сводная таблица.

Зачем: десять команд с выводом целиком съедают контекст сессии, а нужна одна строка на проверку — код возврата и итог.
Запуск: `python tools/check_all.py` (быстрый набор) · `--all` (плюс `reference_run`, `motion_check`) · `--docs` (только
документы) · `--prog` (только программа). Полный вывод каждой проверки — в `%TEMP%/uav_checks/<имя>.txt`.
"""
import os
import re
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # корень проекта
LOGS = os.path.join(tempfile.gettempdir(), "uav_checks")              # куда класть полный вывод
P = "tools/переносимое/"                                              # переносимые проверки документов
# (имя, команда, набор): prog — программа, docs — документы, slow — только с --all
CHECKS = [
    ("flow_check", ["tools/flow_check.py"], "prog"),
    ("metrics_check", ["tools/metrics_check.py"], "prog"),
    ("gui_check", ["tools/gui_check.py"], "prog"),
    ("area_check", ["tools/area_check.py"], "prog"),
    ("tiles_check", ["tools/tiles_check.py"], "prog"),
    ("reference_run", ["tools/reference_run.py"], "slow"),
    ("motion_check", ["tools/motion_check.py"], "slow"),
    ("codemap", [P + "codemap.py"], "docs"),                         # пересборка карты кода — до сверки с ней
    ("codemap --stale", [P + "codemap.py", "--stale"], "docs"),
    ("check_names", [P + "check_names.py"], "docs"),
    ("check_numbers", [P + "check_numbers.py"], "docs"),
    ("check_claims", [P + "check_claims.py"], "docs"),
    ("check_dead_code", [P + "check_dead_code.py"], "docs"),
    ("check_dates", [P + "check_dates.py"], "docs"),
    ("check_comments", [P + "check_comments.py"], "docs"),
    ("check_index", [P + "check_index.py"], "docs"),                 # указатели полные
    ("code_blocks", [P + "code_blocks.py", "--check"], "docs"),      # код под пометками в разборах = модули
    ("docmap --check", [P + "docmap.py", "--check"], "docs"),
]
WIDTH = 96                                                            # ширина итоговой строки в таблице, знаков
# итог проверки узнаётся по словам; нет такой строки — берётся последняя содержательная
VERDICT = re.compile(r"ВСЁ|ВСЕ |ПРОШЛ|РАСХОЖДЕН|НАХОДОК|НЕ НАЙДЕНО|БЕЗ ЕДИНОГО|УПАЛО|расхождений|требуется|"
                     r"битых ссылок|отстал|ОТСТАЛ|записано:", re.IGNORECASE)


def last_line(text):
    """Итог проверки — строка со словом-вердиктом, иначе последняя содержательная. Вход: вывод. Отдаёт: строка ≤ WIDTH."""
    lines = [ln.strip() for ln in text.splitlines()]                 # строки без отступов
    hit = [ln for ln in lines if VERDICT.search(ln)]                 # строки-вердикты
    if hit:                                                          # есть вердикт — последний из них
        s = hit[-1]                                                  # итог
        return s if len(s) <= WIDTH else s[:WIDTH - 1] + "…"
    for ln in reversed(text.splitlines()):                           # с конца: итог пишут последним
        s = ln.strip()                                               # строка без отступов
        if s and set(s) - set("─=-—_ "):                             # не пустая и не черта-разделитель
            return s if len(s) <= WIDTH else s[:WIDTH - 1] + "…"
    return "(вывода нет)"


def run(name, args):
    """Прогнать одну проверку, вывод — в файл. Вход: имя, аргументы python. Отдаёт: (код, секунды, итоговая строка)."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")                 # кириллица в выводе без порчи
    t0 = time.time()                                                 # начало, с
    pr = subprocess.run([sys.executable] + args, cwd=ROOT, env=env, capture_output=True)   # процесс проверки
    out = (pr.stdout + pr.stderr).decode("utf-8", "replace")         # вывод целиком
    fn = os.path.join(LOGS, name.replace(" ", "_").replace("-", "") + ".txt")   # файл полного вывода
    with open(fn, "w", encoding="utf-8") as f:
        f.write(out)
    return pr.returncode, time.time() - t0, last_line(out)


def main():
    """Выбрать набор по ключам, прогнать, напечатать таблицу. Вход: argv. Отдаёт: код — число упавших проверок."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    a = set(sys.argv[1:])                                            # ключи запуска
    want = ({"prog", "docs", "slow"} if "--all" in a else {"docs"} if "--docs" in a
            else {"prog"} if "--prog" in a else {"prog", "docs"})    # какие наборы гнать
    os.makedirs(LOGS, exist_ok=True)
    bad = 0                                                          # упавших проверок
    print("%-16s %4s %7s  %s" % ("проверка", "код", "сек", "итог (полный вывод — %s)" % LOGS))
    for name, args, kind in CHECKS:                                  # проверки по порядку списка
        if kind not in want:                                         # не из выбранного набора
            continue
        code, sec, line = run(name, args)                            # код возврата, секунды, итог
        bad += code != 0
        print("%-16s %4d %7.1f  %s" % (name, code, sec, line), flush=True)
    print("ВСЕ ПРОШЛИ" if not bad else "УПАЛО ПРОВЕРОК: %d — смотреть файлы вывода" % bad)
    return min(bad, 100)


if __name__ == "__main__":                                       # запуск скриптом, не импорт
    sys.exit(main())
