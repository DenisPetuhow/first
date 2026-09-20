# -*- coding: utf-8 -*-
"""ПРАВИЛО КОММЕНТАРИЕВ — проверка НОВОГО кода (шаг 9 сверки).

Ловит три нарушения [8_КОММЕНТАРИИ_В_КОДЕ](../../теория/переносимое/8_КОММЕНТАРИИ_В_КОДЕ.md):
  1. пояснение функции длиннее 3 строк — потолок, не норма;
  2. функция без пояснения вовсе;
  3. новая переменная без пояснения в той же строке справа.

⚠️ СМОТРИТ ТОЛЬКО ИЗМЕНЁННЫЕ СТРОКИ (`git diff`): правило действует на новый код, а уже
написанный не трогаем. Без git-диапазона проверять нечего — так и скажет.

ЗАПУСК:
    python tools/переносимое/check_comments.py              # против HEAD (что не закоммичено)
    python tools/переносимое/check_comments.py HEAD~3       # против другого коммита
    python tools/переносимое/check_comments.py --all файл.py  # весь файл, без git
"""
import ast
import io
import os
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MAX_DOC_LINES = 3          # потолок пояснения функции — правило 8_КОММЕНТАРИИ §1
LINE = "=" * 68
# Имена, которым пояснение не нужно: и так ясно, что делают.
SKIP_FUNCS = {"__init__", "__repr__", "__str__", "__len__", "__enter__", "__exit__", "main"}
# Короткие служебные имена переменных — пояснять нечего.
SKIP_VARS = {"i", "j", "k", "n", "x", "y", "z", "t", "d", "p", "s", "w", "h", "r", "c",
             "self", "_", "out", "res", "tmp", "ok", "app", "fig", "ax"}


def _find_root(markers=("CLAUDE.md", "config.py")):
    # Корень проекта — ближайшая папка ВВЕРХ, где лежит любой маркер.
    # Отдаёт: путь к корню. ⚠️ Не «N уровней вверх»: врёт при переносе файла.
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        if any(os.path.exists(os.path.join(d, m)) for m in markers):
            return d
        up = os.path.dirname(d)
        if up == d:
            raise SystemExit("не найден корень проекта")
        d = up


ROOT = _find_root()


def changed_lines(base):
    # Какие строки каких .py изменились относительно base.
    # Вход: коммит/ветка. Отдаёт: {путь: множество номеров строк}.
    cmd = ["git", "diff", "--unified=0", base, "--", "*.py"]
    try:
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout
    except OSError:
        return {}
    files, cur = {}, None
    for line in out.splitlines():
        if line.startswith("+++ b/"):                 # начался новый файл диффа
            cur = line[6:].strip()                    # — дальше его куски
            files.setdefault(cur, set())
        elif line.startswith("@@") and cur:
            # формат куска: @@ -старое +новое,сколько @@
            try:
                part = line.split("+")[1].split("@@")[0].strip()
                start = int(part.split(",")[0])
                count = int(part.split(",")[1]) if "," in part else 1
            except (IndexError, ValueError):          # необычная шапка куска
                continue                              # — пропускаем, не падаем
            files[cur].update(range(start, start + count))
    return {f: ls for f, ls in files.items() if ls}


def doc_lines(node, src_lines):
    # Сколько строк занимает пояснение функции: докстринг ИЛИ комментарии сразу после def.
    # Вход: узел AST, строки файла. Отдаёт: (число строк, есть ли пояснение вообще).
    doc = ast.get_docstring(node, clean=False)
    if doc is not None:
        return len([s for s in doc.strip("\n").split("\n") if s.strip()]), True
    n = 0                                             # строк пояснения, шт.
    i = node.lineno                                   # строка сразу после `def`
    while i < len(src_lines) and src_lines[i].strip().startswith("#"):
        n += 1
        i += 1
    return n, n > 0


def check_file(path, lines_of_interest=None):
    # Проверить один файл. Вход: путь, множество изменённых строк (None — весь файл).
    # Отдаёт: список находок (строка, текст).
    try:
        src = io.open(path, encoding="utf-8").read()
        tree = ast.parse(src)
    except (OSError, SyntaxError):                    # не читается или не разбирается
        return []                                     # — не наше дело
    src_lines = src.split("\n")
    found = []

    def touched(a, b):
        # Попал ли диапазон строк в изменённые. Отдаёт: bool.
        if lines_of_interest is None:
            return True
        return any(a <= ln <= b for ln in lines_of_interest)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            if not touched(node.lineno, end) or node.name in SKIP_FUNCS:
                continue
            n, has = doc_lines(node, src_lines)
            if not has:
                found.append((node.lineno, "функция `%s` без пояснения" % node.name))
            elif n > MAX_DOC_LINES:
                found.append((node.lineno,
                              "пояснение `%s` — %d строк, потолок %d"
                              % (node.name, n, MAX_DOC_LINES)))
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if not isinstance(tgt, ast.Name) or tgt.id in SKIP_VARS:
                continue
            if lines_of_interest is not None and node.lineno not in lines_of_interest:
                continue
            if lines_of_interest is None:             # без git проверяем только функции
                continue
            line = src_lines[node.lineno - 1] if node.lineno <= len(src_lines) else ""
            if "#" in line.split("=", 1)[-1]:         # пояснение справа есть
                continue
            prev = src_lines[node.lineno - 2].strip() if node.lineno >= 2 else ""
            if prev.startswith("#"):                  # пояснение строкой выше — тоже годится
                continue
            found.append((node.lineno, "переменная `%s` без пояснения" % tgt.id))
    return found


def main(argv):
    # Разбор аргументов и отчёт. Вход: argv. Отдаёт: код возврата (1 — есть находки).
    if "--all" in argv:
        files = [a for a in argv if a.endswith(".py")]
        targets = {f: None for f in files}
        print("проверяю целиком: %d файл(ов)" % len(files))
    else:
        base = next((a for a in argv[1:] if not a.startswith("-")), "HEAD")
        targets = changed_lines(base)
        print("изменения против %s: файлов с правками %d" % (base, len(targets)))
    print(LINE)
    total = 0
    for rel, lines in sorted(targets.items()):
        path = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
        if not os.path.exists(path):                  # файл удалён или переименован
            continue
        found = check_file(path, lines)
        if not found:
            continue
        print("\n%s" % rel)
        for ln, what in sorted(found):
            print("   :%-5d %s" % (ln, what))
        total += len(found)
    print(LINE)
    if total:
        print("НАХОДОК: %d — правило [8_КОММЕНТАРИИ_В_КОДЕ]: пояснение функции до %d строк,\n"
              "новая переменная — справа в той же строке, с единицей измерения."
              % (total, MAX_DOC_LINES))
    else:
        print("ЧИСТО: новый код оформлен по правилу.")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
