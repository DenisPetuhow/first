# -*- coding: utf-8 -*-
"""ПРАВИЛО КОММЕНТАРИЕВ — проверка НОВОГО кода (шаг 9 сверки).

Ловит нарушения [8_КОММЕНТАРИИ_В_КОДЕ](../../теория/переносимое/8_КОММЕНТАРИИ_В_КОДЕ.md):
  1. пояснение функции длиннее 3 строк — потолок, не норма;
  2. функция без пояснения вовсе;
  3. в пояснении функции не названо, что она ПРИНИМАЕТ («Вход:») и что ОТДАЁТ («Отдаёт:»);
  4. новая переменная без пояснения (справа в той же строке либо строкой выше);
  5. цикл `for` / `while` без пояснения — что перебираем;
  6. ветвление `if` / `elif` / `except` без пояснения — что проверяем.

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
import re
import subprocess
import sys
import tokenize

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MAX_DOC_LINES = 3          # потолок пояснения функции — правило 8_КОММЕНТАРИИ §1
LINE = "=" * 68
# Имена, которым пояснение не нужно: и так ясно, что делают.
SKIP_FUNCS = {"__init__", "__repr__", "__str__", "__len__", "__enter__", "__exit__", "main"}
# Служебные имена переменных — пояснять нечего.
# ⚠️ ОДНОБУКВЕННЫХ ЗДЕСЬ БОЛЬШЕ НЕТ, кроме индексов `i`, `j` (26.09.2026, замечание заказчика):
# за `d`, `t`, `p`, `g` прятались расстояния и приросты без единиц — именно их и не пояснили.
SKIP_VARS = {"i", "j", "self", "_", "app", "fig", "ax",
             # ⚠️ ЗАКОННЫЕ ОБОРОТЫ (20.09.2026): содержимое видно из правой части, и
             # комментарий к ним был бы пересказом кода — `hint = QLabel(...)`, `row`,
             # `item`. Правило про ВЕЛИЧИНЫ с единицами, а не про служебные имена.
             "val", "text", "row", "rows", "item", "cls", "data", "name", "names",
             "col", "btn", "box", "hint", "line", "cur", "path", "files", "found",
             "types", "dlg", "sec", "base", "cmd", "args", "src", "node", "tgt",
             "st", "note", "end", "first", "out", "res", "tmp", "ok"}
# ⚠️ ИМЕНА ВИДЖЕТОВ Qt — законный оборот: `btn_close = QPushButton("Закрыть")` содержит
# и что это, и что делает. Комментарий к ним был бы пересказом правой части.
SKIP_PREFIXES = ("btn_", "lbl_", "chk_", "rb_", "spin_", "combo_", "grp_", "tbl_")
# Слова, которыми пояснение называет вход и выход (образец §2: «Вход: …», «Отдаёт: …»).
IN_WORDS = re.compile(r"вход|принима", re.I)     # назван вход
OUT_WORDS = re.compile(r"отда|возвра", re.I)     # назван выход
# Узлы, которые открывают новую область имён: их тело — чужая функция или класс.
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


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


def comment_lines(src):
    # Где в файле комментарии. Вход: текст файла. Отдаёт: (строки с «#» вообще, строки из одного «#»).
    # ⚠️ По токенам, а не поиском «#»: решётка в строке "#ff0000" — не комментарий.
    anywhere, alone = set(), set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):   # все токены файла
            if tok.type == tokenize.COMMENT:          # комментарий — запомнить строку
                anywhere.add(tok.start[0])
                if not tok.line[:tok.start[1]].strip():   # до «#» пусто — строка-комментарий
                    alone.add(tok.start[0])
    except (tokenize.TokenError, IndentationError):   # файл не разбирается на токены
        pass                                          # — что успели собрать, то и отдаём
    return anywhere, alone


def doc_text(node, src_lines):
    # Пояснение функции: докстринг ИЛИ комментарии сразу после def.
    # Вход: узел AST, строки файла. Отдаёт: (число непустых строк, текст; "" — пояснения нет).
    doc = ast.get_docstring(node, clean=False)
    if doc is not None:                               # докстринг — он и есть пояснение
        body = [s for s in doc.strip("\n").split("\n") if s.strip()]   # непустые строки
        return len(body), "\n".join(body)
    body = []                                         # комментарии под `def`
    i = node.body[0].lineno - 1 if node.body else node.lineno   # индекс строки после шапки, 0-based
    while i > 0 and src_lines[i - 1].strip().startswith("#"):   # шапка многострочная — отступить к её концу
        i -= 1
    i = max(i, node.lineno)                           # не выше самой строки `def`
    while i < len(src_lines) and src_lines[i].strip().startswith("#"):   # подряд идущие комментарии
        body.append(src_lines[i].strip())
        i += 1
    return len(body), "\n".join(body)


def head_span(node, n_doc):
    # Строки «шапки» функции: сам `def` и пояснение под ним. Отдаёт: (первая, последняя).
    # ⚠️ По шапке, а не по телу: правка внутри ДАВНО написанной функции иначе требовала бы
    # переписать её докстринг вопреки правилу (20.09.2026: `set_area`, `_build_ui`).
    first = node.body[0] if node.body else None       # первый оператор — возможный докстринг
    end = getattr(first, "end_lineno", 0) if isinstance(first, ast.Expr) else 0
    return node.lineno, max(end, node.lineno + n_doc)


def real_args(node):
    # Аргументы, которые стоит назвать во «Вход:». Вход: узел функции. Отдаёт: список имён.
    a = node.args                                     # все виды аргументов функции
    names = [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs]
    names += [x.arg for x in (a.vararg, a.kwarg) if x is not None]
    # self / cls и «_имя» (заведомо не используется) — называть нечего
    return [n for n in names if n not in ("self", "cls") and not n.startswith("_")]


def own_nodes(scope):
    # Узлы тела ОДНОЙ области имён — без вложенных функций и классов (у них своя проверка).
    # Вход: узел модуля, класса или функции. Отдаёт: список узлов по порядку обхода.
    out, stack = [], list(ast.iter_child_nodes(scope))
    while stack:                                      # обход в глубину без рекурсии
        n = stack.pop()                               # очередной узел
        out.append(n)
        if not isinstance(n, SCOPES):                 # вложенная область — внутрь не идём
            stack.extend(ast.iter_child_nodes(n))
    return sorted(out, key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)))


def new_names(node, in_init):
    # Какие ИМЕНА вводит присваивание. Вход: узел, внутри ли __init__. Отдаёт: список имён.
    if isinstance(node, ast.Assign):                  # a = …, a = b = …, a, b = …
        targets = node.targets                        # все цели, через «=»
    elif isinstance(node, ast.AnnAssign) and node.value is not None:   # a: int = …
        targets = [node.target]
    else:                                             # a += …, del, with — не новое имя
        return []
    out = []
    for t in targets:                                 # каждая цель присваивания
        parts = t.elts if isinstance(t, (ast.Tuple, ast.List)) else [t]   # распаковка a, b = …
        for p in parts:                               # каждое имя в распаковке
            if isinstance(p, ast.Name):               # простое имя
                out.append(p.id)
            elif (in_init and isinstance(p, ast.Attribute) and isinstance(p.value, ast.Name)
                  and p.value.id == "self"):          # поле объекта в __init__ — тоже новая величина
                out.append("self." + p.attr)
    return out


def header_end(node):
    # Последняя строка «шапки» составного оператора (без тела). Вход: узел. Отдаёт: номер строки.
    part = {ast.If: "test", ast.While: "test", ast.For: "iter", ast.AsyncFor: "iter",
            ast.ExceptHandler: "type"}.get(type(node))   # поле, на котором кончается шапка
    sub = getattr(node, part, None) if part else None   # узел условия; None — шапка в одну строку
    return sub.end_lineno if sub is not None else node.lineno


def check_file(path, lines_of_interest=None):
    # Проверить один файл. Вход: путь, множество изменённых строк (None — весь файл).
    # Отдаёт: список находок (строка, текст).
    try:
        src = io.open(path, encoding="utf-8").read()
        tree = ast.parse(src)
    except (OSError, SyntaxError):                    # не читается или не разбирается
        return []                                     # — не наше дело
    src_lines = src.split("\n")
    anywhere, alone = comment_lines(src)              # строки с комментарием и строки-комментарии
    found = []

    def touched(a, b):
        # Попал ли диапазон строк a…b в изменённые. Вход: номера строк. Отдаёт: bool.
        if lines_of_interest is None:                 # проверка файла целиком
            return True
        return any(a <= ln <= b for ln in lines_of_interest)

    def noted(a, b):
        # Есть ли пояснение у строк a…b: справа на любой из них или строкой-комментарием выше.
        # Вход: номера строк. Отдаёт: bool.
        return any(ln in anywhere for ln in range(a, b + 1)) or (a - 1) in alone

    for node in ast.walk(tree):                       # функции — пояснение и его содержание
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):   # не функция
            continue                                  # — её пропускаем
        n, text = doc_text(node, src_lines)           # строк в пояснении, его текст
        if not touched(*head_span(node, n)) or node.name in SKIP_FUNCS:   # функция чужая или служебная
            continue
        if not text:                                  # пояснения нет вовсе
            found.append((node.lineno, "функция `%s` без пояснения" % node.name))
            continue
        if n > MAX_DOC_LINES:                         # пояснение длиннее потолка
            found.append((node.lineno, "пояснение `%s` — %d строк, потолок %d"
                          % (node.name, n, MAX_DOC_LINES)))
        miss = []                                     # чего не хватает в пояснении
        if real_args(node) and not IN_WORDS.search(text):   # есть аргументы — назвать вход
            miss.append("«Вход:»")
        if not OUT_WORDS.search(text):                # выход называется всегда, хоть «ничего»
            miss.append("«Отдаёт:»")
        if miss:                                      # вход или выход не назван — находка
            found.append((node.lineno, "пояснение `%s`: нет %s" % (node.name, " и ".join(miss))))

    scopes = [tree] + [n for n in ast.walk(tree) if isinstance(n, SCOPES[:3])]   # модуль, классы, функции
    for scope in scopes:                              # переменные, циклы и ветвления — по областям имён
        in_init = getattr(scope, "name", "") == "__init__"   # поля self.x — новые только здесь
        seen = set()                                  # имена, уже введённые в этой области
        in_func = isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))   # «_имя» здесь — ненужное значение
        if in_func:                                   # аргумент — не новая переменная
            a = scope.args                            # все виды аргументов, с «_» тоже
            seen.update(x.arg for x in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg] if x)
        for node in own_nodes(scope):                 # все узлы области по порядку строк
            if isinstance(node, (ast.For, ast.AsyncFor)):   # переменная цикла — поясняется циклом
                seen.update(n.id for n in ast.walk(node.target) if isinstance(n, ast.Name))
            if isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.If, ast.ExceptHandler)):   # цикл или ветвление
                b = header_end(node)                  # конец шапки оператора
                if touched(node.lineno, b) and not noted(node.lineno, b):   # новая шапка без пояснения
                    what = {ast.For: "цикл `for`", ast.AsyncFor: "цикл `for`", ast.While: "цикл `while`",   # как назвать в отчёте
                            ast.If: "ветвление `if`", ast.ExceptHandler: "`except`"}[type(node)]
                    found.append((node.lineno, "%s без пояснения" % what))
                continue
            names = new_names(node, in_init)          # имена, которые вводит этот оператор
            fresh = [nm for nm in names if nm not in seen and nm not in SKIP_VARS
                     and not nm.split(".")[-1].startswith(SKIP_PREFIXES)   # виджет Qt — и у поля `self.btn_…`
                     and not (in_func and nm.startswith("_"))]   # впервые, не служебные, не «_ненужное»
            seen.update(names)
            if not fresh or not touched(node.lineno, node.lineno):   # старое имя или чужая строка
                continue
            if not noted(node.lineno, node.end_lineno):   # ни справа, ни строкой выше
                found.append((node.lineno, "переменная `%s` без пояснения" % ", ".join(fresh)))
    return found


def main(argv):
    # Разбор аргументов и отчёт. Вход: argv. Отдаёт: код возврата (1 — есть находки).
    if "--all" in argv:
        files = [a for a in argv[1:] if a.endswith(".py")]   # файлы из команды; argv[0] — сам скрипт, не проверять
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
        print("НАХОДОК: %d — правило [8_КОММЕНТАРИИ_В_КОДЕ]: пояснение функции до %d строк с «Вход:» и\n"
              "«Отдаёт:»; новая переменная, цикл, ветвление — с пояснением (справа или строкой выше)."
              % (total, MAX_DOC_LINES))
    else:
        print("ЧИСТО: новый код оформлен по правилу.")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
