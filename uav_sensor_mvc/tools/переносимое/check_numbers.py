# -*- coding: utf-8 -*-
"""ШАГ 2 сверки на достоверность: ЧИСЛА из документации — против `config.py`.

ЧТО ЛОВИТ. Значение параметра поменяли в коде, а в тексте осталось прежнее. Имя при этом
живое, поэтому проверка имён такое не видит — врёт только число.

ПОЧЕМУ РАНЬШЕ БЫЛО 50 «НАХОДОК» И НИ ОДНОЙ НАСТОЯЩЕЙ. Четыре причины, все учтены здесь:

  1. ОДНОБУКВЕННЫЕ ИМЕНА. В config.py есть N, R, k, T. Скрипт видел «замер при R = 3 км»
     и ругался, что в коде 12, — хотя это условие ЧУЖОГО замера, а не текущая настройка.
     Плюс буква ловилась внутри слов: в «LEGEND_PT = 7.8» находилось «T = 7.8».
     Лечение: имена короче MIN_NAME символов не проверяются.
  2. ОПИСАНИЕ ВЫКЛЮЧЕННОГО ВАРИАНТА. «`THREAT_VILLAGE_FLOOR = 0` возвращает прежнее
     поведение» — ноль тут иллюстрация, а не значение. Лечение: строки с такими
     оборотами пропускаются.
  3. ДОЛИ ПРОТИВ ПРОЦЕНТОВ. В коде 0.35, в тексте «35 %» — одно и то же.
  4. УСЛОВИЯ ЧУЖИХ ЗАМЕРОВ. «замер при POWER = 3», «было 1.5» — историческое.

ЖУРНАЛ И АРХИВ НЕ ПРОВЕРЯЮТСЯ вовсе: там числа исторические по определению
(«коридоров 9 944 → 10 950»), и сверять их с текущим конфигом бессмысленно.

ЗАПУСК:
    python tools/переносимое/check_numbers.py
"""
import io
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
HIST_DIRS = {"журнал", "архив"}
MIN_NAME = 4          # однобуквенные N, R, k, T дают только шум

# Обороты, после которых число описывает ВАРИАНТ, ЧУЖОЙ замер или ВЫЧИСЛЕННОЕ значение,
# а не текущую настройку. IGNORECASE обязателен: «Выключается» с заглавной буквы иначе
# не совпадает с шаблоном — на этом фильтр однажды и промахнулся.
VARIANT = re.compile(
    # «при `X` = 0 …» — число описывает ВАРИАНТ (обычно выключение механизма), а не
    # действующее значение. Имя берём в любом регистре: параметры Params — строчные
    # (`threat_N_big`), константы модулей — заглавные (`THREAT_DEM_ON_START`)
    r"прежн|возвраща|выключ|отключ|было\b|раньше|замер при|при\s+`?[A-Za-z_]+`?\s*=|"
    r"вместо|отвергнут|не применено|авто|✗|→|->|"
    # таблицы «что устарело в исходниках» (теория/пособие, теория/основа): там
    # СТАРОЕ значение приведено НАМЕРЕННО, рядом с текущим — это и есть их смысл
    r"устарел|в исходник|прогон при|перезамер|"
    # УСЛОВИЯ ЭТАЛОНА: «снят при `threat_N` = 15», «с поправкой: … = 15». Эталон
    # намеренно снимается не на всех умолчаниях — иначе его не сравнить с прежним,
    # снятым до смены умолчания. Число описывает УСЛОВИЕ ЗАМЕРА, а не текущее значение
    r"снят при|снят \d|с одной поправкой|с поправкой", re.IGNORECASE)


def config_values():
    """{имя параметра: значение} из config.py — и константы, и поля Params."""
    src = io.open(os.path.join(ROOT, "config.py"), encoding="utf-8").read()
    vals = {}
    for m in re.finditer(r"(?m)^([A-Z][A-Z_0-9]+)\s*=\s*([-\d.]+)\s*(?:#|$)", src):
        vals[m.group(1)] = float(m.group(2).rstrip("."))
    for m in re.finditer(r"(?m)^\s{4}(\w+)\s*:\s*\w+\s*=\s*([-\d.]+)", src):
        vals.setdefault(m.group(1), float(m.group(2).rstrip(".")))
    return {k: v for k, v in vals.items() if len(k) >= MIN_NAME}


def docs():
    out = []
    for root, dirs, files in os.walk(os.path.join(ROOT, "теория")):
        dirs[:] = [d for d in dirs if d not in HIST_DIRS]
        out += [os.path.join(root, f) for f in files if f.endswith(".md")]
    out.append(os.path.join(ROOT, "CLAUDE.md"))
    return [p for p in out if os.path.exists(p)]


def same(text_val, code_val, line):
    """Совпадает ли число из текста со значением в коде (с учётом процентов)."""
    if abs(text_val - code_val) < 1e-9:
        return True
    # доля в коде, проценты в тексте: 0.35 <-> 35 %
    if "%" in line and abs(text_val / 100.0 - code_val) < 1e-9:
        return True
    return False


def main():
    vals = config_values()
    print("числовых параметров в config.py (длиннее %d символов): %d" % (MIN_NAME, len(vals)))

    bad = []
    for d in docs():
        for line_no, line in enumerate(io.open(d, encoding="utf-8").read().split("\n"), 1):
            if VARIANT.search(line):
                continue                       # вариант, чужой замер или история
            for name, code_val in vals.items():
                if name not in line:
                    continue
                for m in re.finditer(re.escape(name) + r"`?\s*(?:=|—|:)\s*\*{0,2}([-\d.]+)",
                                     line):
                    try:
                        got = float(m.group(1).rstrip("."))
                    except ValueError:
                        continue
                    if not same(got, code_val, line):
                        bad.append((os.path.basename(d), line_no, name, code_val, got,
                                    line.strip()[:100]))

    print("\nрасхождений «текст против config.py»: %d" % len(bad))
    print("(каждое проверить глазами — возможен оборот, не учтённый фильтром)\n")
    for f, ln, name, code_val, got, line in bad:
        print("  %s:%d  %s: в коде %g, в тексте %g" % (f, ln, name, code_val, got))
        print("      %s" % line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
