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
             "self", "_", "out", "res", "tmp", "ok", "app", "fig", "ax",
             # ⚠️ ЗАКОННЫЕ ОБОРОТЫ (20.09.2026): содержимое видно из правой части, и
             # комментарий к ним был бы пересказом кода — `hint = QLabel(...)`, `row`,
             # `item`. Правило про ВЕЛИЧИНЫ с единицами, а не про служебные имена.
             "val", "text", "row", "rows", "item", "cls", "data", "name", "names",
             "col", "btn", "box", "hint", "line", "cur", "path", "files", "found",
             "types", "dlg", "sec", "base", "cmd", "args", "src", "node", "tgt",
             "v", "st", "note", "end", "first"}
# ⚠️ ИМЕНА ВИДЖЕТОВ Qt — законный оборот: `btn_close = QPushButton("Закрыть")` содержит
# и что это, и что делает. Комментарий к ним был бы пересказом правой части.
SKIP_PREFIXES = ("btn_", "lbl_", "chk_", "rb_", "spin_", "combo_", "grp_", "tbl_")


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


def _run(cmd):
    # Запустить git и отдать вывод. Вход: список аргументов. Отдаёт: строку ("" — не вышло).
    try:
        return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stdout
    except OSError:                                   # git не установлен
        return ""                                     # — работаем вхолостую, но не падаем


def repo_root():
    # Корень РЕПОЗИТОРИЯ: пути в `git diff` отсчитываются от него. Отдаёт: путь строкой.
    # ⚠️ Он не равен корню ПРОЕКТА, когда проект лежит подпапкой, — и это рушило проверку.
    out = _run(["git", "rev-parse", "--show-toplevel"]).strip()
    return os.path.normpath(out) if out else ROOT


def _abs(base_dir, rel):
    # Путь из вывода git — в путь на диске. Вход: корень репозитория, путь через «/».
    # Отдаёт: абсолютный путь.
    return os.path.join(base_dir, rel.strip().replace("/", os.sep))


def changed_lines(base):
    # Какие строки каких .py изменились относительно base, плюс новые файлы целиком.
    # Вход: коммит/ветка. Отдаёт: {абсолютный путь: множество номеров строк}.
    out = _run(["git", "diff", "--unified=0", base, "--", "*.py"])
    base_dir = repo_root()                            # от него отсчитаны пути в диффе
    files, cur = {}, None
    for line in out.splitlines():
        if line.startswith("+++ b/"):                 # начался новый файл диффа
            cur = _abs(base_dir, line[6:])            # — дальше его куски
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
    # ⚠️ НОВЫЙ ФАЙЛ, ЕЩЁ НЕ ДОБАВЛЕННЫЙ В GIT, `git diff` НЕ ВИДИТ — а правило именно про
    # новый код. Берём такие целиком: 20.09.2026 модуль типов БПЛА прошёл проверку
    # вхолостую («файлов с правками 1» при четырёх).
    # ⚠️ `--full-name` обязателен: без него пути идут от ТЕКУЩЕЙ папки, а у `git diff` —
    # от корня репозитория; смешаешь два вида — половина файлов не найдётся (и молча).
    for rel in _run(["git", "ls-files", "--others", "--exclude-standard",
                     "--full-name", "*.py"]).splitlines():
        path = _abs(base_dir, rel)
        files[path] = set(range(1, sum(                                # весь файл новый
            1 for _ in io.open(path, encoding="utf-8", errors="replace")) + 1))
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


def head_span(node, n_doc):
    # Строки «шапки» функции: сам `def` и пояснение под ним. Отдаёт: (первая, последняя).
    # ⚠️ По шапке, а не по телу: правка внутри ДАВНО написанной функции иначе требовала бы
    # переписать её докстринг вопреки правилу (20.09.2026: `set_area`, `_build_ui`).
    first = node.body[0] if node.body else None       # первый оператор — возможный докстринг
    end = getattr(first, "end_lineno", 0) if isinstance(first, ast.Expr) else 0
    return node.lineno, max(end, node.lineno + n_doc)


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
            n, has = doc_lines(node, src_lines)
            if not touched(*head_span(node, n)) or node.name in SKIP_FUNCS:
                continue
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
            if tgt.id.startswith(SKIP_PREFIXES):      # имя виджета Qt говорит само за себя
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
    missing = []                                      # что git назвал, а на диске нет
    for rel, lines in sorted(targets.items()):
        path = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
        if not os.path.exists(path):                  # файл удалён, переименован или путь врёт
            missing.append(rel)                       # — сказать вслух, а не пропустить молча
            continue
        found = check_file(path, lines)
        if not found:
            continue
        print("\n%s" % rel)
        for ln, what in sorted(found):
            print("   :%-5d %s" % (ln, what))
        total += len(found)
    print(LINE)
    if missing:                                       # молчание здесь и скрыло дефект путей
        print("⚠️ НЕ НАЙДЕНО НА ДИСКЕ: %d — %s" % (len(missing), "; ".join(missing[:3])))
    if total:
        print("НАХОДОК: %d — правило [8_КОММЕНТАРИИ_В_КОДЕ]: пояснение функции до %d строк,\n"
              "новая переменная — справа в той же строке, с единицей измерения."
              % (total, MAX_DOC_LINES))
    else:
        print("ЧИСТО: новый код оформлен по правилу.")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
