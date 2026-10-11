# -*- coding: utf-8 -*-
"""ШАГ 11 сверки: БЛОКИ КОДА В РАЗБОРАХ против самого кода.

ЧТО ДЕЛАЕТ. Разбор пишет человек (или Claude) прямо в .md; над блоком кода — пометка, ЧТО в нём показано:
`<!-- код: src/a.py::Класс.метод -->`, кусок — `<!-- код: src/a.py::функция | от: строка | до: строка -->` («до» не
входит), файл — `<!-- код: docs/b.py | от: … | до: … -->`. Скрипт берёт код через `ast` (без импорта модулей, без Qt)
и кладёт под пометку; текст объяснения не трогает. Код ушёл вперёд — `--check` называет блок: объяснение перечитать.
ЗАПУСК: `python tools/переносимое/code_blocks.py [файлы .md]` — обновить блоки · `--check` — только сверить (код 1) ·
`--list [файл кода]` — какой код в каких разборах · `--unmarked [папки]` — блоки ```python без пометки: совпадают ли с
кодом · `--stub путь::Имя` — заготовка раздела по эталону. При переносе в другой проект менять DOCS, UNMARKED, CODE_DIRS.
"""
import ast
import difflib
import os
import re
import sys
import textwrap

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
DOCS = ["теория"]                                    # где искать разборы с пометками
UNMARKED = ["теория/код"]                            # где искать блоки без пометки (--unmarked)
CODE_DIRS = ["src", "tools", "docs"]                 # где искать функцию по имени (--unmarked)
MARK = re.compile(r"^<!-- код: (.+?) -->$", re.M)   # строка пометки
# пометка — одна строка: при re.S «.+?» захватил бы перевод строки и склеил пометку без блока со следующей
BLOCK = re.compile(r"^<!-- код: ([^\n]+?) -->\n```python\n(.*?)^```$", re.S | re.M)   # пометка и блок сразу под ней
FENCE = re.compile(r"^```python\n(.*?)^```$", re.S | re.M)                         # любой блок Python
NAME = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+(\w+)", re.M)                # первое имя в блоке
DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)                       # узлы с исходником
_files = {}                                          # прочитанные файлы кода: путь → (текст, дерево)
# заготовка раздела по эталону (--stub): имя, «где», дерево вызовов, пометка, код
STUB = """## N. %s — ⬜ что делает

**Где:** `%s`.

**Суть простыми словами.** ⬜ 2–4 фразы без кода и без имён из кода — обычными словами предметной области.

**Взаимодействие** — кто кого зовёт и что передаёт (каркас — из кода, проверить и дописать):

```text
%s
```

**Как работает.** ⬜ 3–6 строк: порядок действий, почему именно так (не перечень функций — он в дереве).

<!-- код: %s -->
```python
%s
```

**Пример с расчётом.** ⬜ по шагам; числа — прогоном кода на настоящих данных (скрипт расчёта сохранить рядом).

| Где легко сломать | Почему |
|---|---|
| ⬜ | ⬜ |
"""


def _parsed(path):
    """Текст и дерево `ast` файла кода, один раз на файл. Вход: путь от корня. Отдаёт: (текст, дерево)."""
    if path not in _files:                           # файл ещё не читали
        text = open(os.path.join(ROOT, path), encoding="utf-8").read()   # файл целиком
        _files[path] = (text, ast.parse(text))
    return _files[path]


def _node(tree, qual):
    """Определения функции или класса по имени через точку. Вход: дерево, «Класс.метод».
    Отдаёт: (первое, последнее) с этим именем — у свойства это чтение и запись — или None."""
    body, found = tree.body, []                      # где искать очередную часть имени; найденные узлы
    for part in qual.split("."):                     # вглубь: класс → метод
        found = [n for n in body if isinstance(n, DEFS) and n.name == part]   # определения с этим именем
        if not found:                                # имени нет — переименовали или убрали
            return None
        body = found[-1].body                        # внутрь — последнего: оно действует в самом Python
    return found[0], found[-1]


def source(path, qual=None):
    """Исходник функции или класса с декораторами — либо файл целиком. Вход: путь от корня, имя или None.
    Отдаёт: текст; нет файла или имени — ValueError."""
    if not os.path.isfile(os.path.join(ROOT, path)):   # файла нет — перенесли или удалили
        raise ValueError("нет файла %s" % path)
    text, tree = _parsed(path)                       # файл и его дерево
    if not qual:                                     # без имени — файл целиком
        return text
    span = _node(tree, qual)                         # первое и последнее определение с этим именем
    if span is None:                                 # имени в файле нет
        raise ValueError("нет %s в %s" % (qual, path))
    first, node = span
    lines = text.splitlines(keepends=True)           # строки файла
    a = min([d.lineno for d in first.decorator_list] + [first.lineno]) - 1   # первая строка — с декораторами
    b = j = node.end_lineno                          # последняя строка тела; откуда смотреть дальше
    col = node.body[0].col_offset                    # отступ тела
    # комментарии сразу за телом с отступом тела — часть блока, как у inspect.getsource
    while j < len(lines) and (not lines[j].strip() or lines[j].lstrip().startswith("#")):
        if lines[j].strip():                         # строка-комментарий
            if len(lines[j]) - len(lines[j].lstrip()) < col:   # левее тела — блок кончился
                break
            b = j + 1
        j += 1
    return "".join(lines[a:b])


def piece(spec):
    """Код под пометкой. Вход: «путь::Имя | от: строка | до: строка» (границы — по порядку, «до» не входит).
    Отдаёт: текст без общего отступа и хвостовых пробелов; ошибка — ValueError."""
    parts = [p.strip() for p in spec.split(" | ")]   # цель и границы куска
    path, _, qual = parts[0].partition("::")         # файл и имя в нём
    t = textwrap.dedent(source(path.strip(), qual.strip() or None))   # код без общего отступа
    for p in parts[1:]:                              # границы — с начала строки, где стоит текст
        key, _, val = (s.strip() for s in p.partition(":"))   # «от» или «до» и текст границы
        if key not in ("от", "до"):                  # опечатка в пометке
            raise ValueError("непонятная граница «%s» — нужно «от:» или «до:»" % p)
        i = t.find(val)                              # где текст границы
        if i < 0:                                    # строки нет — код переписали
            raise ValueError("нет строки «%s» в %s" % (val, parts[0]))
        i = t.rfind("\n", 0, i) + 1                  # начало строки с границей
        t = t[i:] if key == "от" else t[:i]
    return textwrap.dedent(t).rstrip()


def docs(paths):
    """Файлы .md по списку папок и файлов. Вход: пути от корня. Отдаёт: [пути от корня через «/»]."""
    out = []                                         # найденные .md
    for top in paths:                                # каждая папка или файл
        full = os.path.join(ROOT, top)               # полный путь
        if os.path.isfile(full):                     # передан сам файл
            out.append(top.replace("\\", "/"))
            continue
        for d, dirs, names in os.walk(full):         # обход папки
            dirs.sort()
            out += [os.path.relpath(os.path.join(d, n), ROOT).replace("\\", "/") for n in sorted(names)
                    if n.endswith(".md")]
    return out


def _line(text, pos):
    """Номер строки по позиции в тексте. Вход: текст, позиция. Отдаёт: номер с 1."""
    return text.count("\n", 0, pos) + 1


def update(text):
    """Блоки под пометками — по коду. Вход: текст .md. Отдаёт: (новый текст, [разошлось], [битые])."""
    moved, broken = [], []                           # разошедшиеся блоки; битые пометки — (строка, сообщение)

    def fix(m):
        """Один блок: код совпал — как есть, ушёл — новый. Вход: совпадение. Отдаёт: текст блока."""
        line = _line(text, m.start())                # строка пометки
        try:
            code = piece(m.group(1))                 # код, который должен стоять
        except ValueError as e:                      # нет файла, имени или границы
            broken.append((line, str(e)))
            return m.group(0)
        if code + "\n" == m.group(2):                # блок совпадает с кодом
            return m.group(0)
        d = list(difflib.ndiff(m.group(2).splitlines(), code.splitlines()))   # построчная разница
        moved.append((line, "%s — код изменился (+%d −%d строк)" % (
            m.group(1).split(" | ")[0], sum(s[0] == "+" for s in d), sum(s[0] == "-" for s in d))))
        return "<!-- код: %s -->\n```python\n%s\n```" % (m.group(1), code)

    new = BLOCK.sub(fix, text)                       # текст с обновлёнными блоками
    good = {m.start() for m in BLOCK.finditer(text)}   # пометки, под которыми стоит блок
    for m in MARK.finditer(text):                    # пометка без блока сразу под ней — её никто не обновит
        if m.start() not in good:                    # блока нет — пометка битая
            broken.append((_line(text, m.start()), "под пометкой нет блока ```python"))
    return new, moved, broken


def refresh(paths, check):
    """Обновить или сверить блоки во всех разборах с пометками. Вход: пути, только сверить?
    Отдаёт: (разошлось, битых)."""
    n_moved = n_broken = n_blocks = n_files = 0      # итоги
    for path in docs(paths):                         # каждый .md
        full = os.path.join(ROOT, path)              # полный путь
        text = open(full, encoding="utf-8").read()   # разбор целиком
        if not MARK.search(text):                    # пометок нет — не наш файл
            continue
        new, moved, broken = update(text)            # текст с новыми блоками; разошлось; битые
        n_files += 1
        n_blocks += len(BLOCK.findall(text))
        n_moved += len(moved)
        n_broken += len(broken)
        print("%-52s блоков %3d · %s %d · битых %d" % (path, len(BLOCK.findall(text)),
                                                       "разошлось" if check else "обновлено", len(moved), len(broken)))
        for line, msg in sorted(moved + [(ln, "⚠️ " + s) for ln, s in broken]):   # подробности по строкам
            print("    стр. %d: %s" % (line, msg))
        if moved and not check:                      # блоки заменены — записать
            open(full, "w", encoding="utf-8", newline="\n").write(new)
    print("итого: разборов %d, блоков %d, %s %d, битых пометок %d" % (
        n_files, n_blocks, "разошлось с кодом" if check else "обновлено", n_moved, n_broken))
    if n_moved:                                      # код ушёл вперёд — текст рядом мог устареть
        print("⚠️ перечитать объяснение и пример у этих блоков; поправил — отметка «Правился» в шапке разбора")
    return n_moved, n_broken


def listing(code_file=None):
    """Какой код в каких разборах. Вход: файл кода для отбора или None — всё. Отдаёт: ничего (печать)."""
    by = {}                                          # файл кода → [(имя, разбор, строка)]
    want = code_file and os.path.normpath(code_file)   # отбор по файлу кода
    for path in docs(DOCS):                          # каждый .md
        text = open(os.path.join(ROOT, path), encoding="utf-8").read()   # разбор целиком
        for m in MARK.finditer(text):                # каждая пометка
            f, _, q = m.group(1).split(" | ")[0].partition("::")   # файл кода и имя
            if want and os.path.normpath(f.strip()) != want:   # не тот файл кода
                continue
            by.setdefault(f.strip(), []).append((q.strip() or "(файл)", path, _line(text, m.start())))
    for f in sorted(by):                             # по файлам кода
        print(f)
        for q, path, line in by[f]:                  # что из него и где показано
            print("    %-40s → %s:%d" % (q, path, line))
    print("итого: файлов кода %d, блоков %d" % (len(by), sum(map(len, by.values()))))


def _defs():
    """Все функции и классы в папках кода. Вход: ничего. Отдаёт: ({имя: [(путь, «Класс.метод»)]}, [пути файлов])."""
    idx, files = {}, []                              # имя → где определено; все файлы кода

    def walk(body, prefix, rel):
        """Обход определений вглубь. Вход: тело узла, приставка имени, файл. Отдаёт: ничего."""
        for n in body:                               # каждое определение на этом уровне
            if isinstance(n, DEFS):                  # функция или класс — запомнить и войти внутрь
                idx.setdefault(n.name, []).append((rel, prefix + n.name))
                walk(n.body, prefix + n.name + ".", rel)

    for top in CODE_DIRS:                            # каждая папка кода
        for d, dirs, names in os.walk(os.path.join(ROOT, top)):   # обход папки
            dirs[:] = [x for x in dirs if x != "__pycache__"]
            for n in names:                          # каждый файл .py
                if not n.endswith(".py"):            # не код
                    continue
                rel = os.path.relpath(os.path.join(d, n), ROOT).replace("\\", "/")   # путь от корня
                try:
                    walk(_parsed(rel)[1].body, "", rel)
                except (SyntaxError, UnicodeDecodeError):   # файл не разбирается — пропустить
                    continue
                files.append(rel)
    return idx, files


def _norm(t):
    """Строки кода без отступов, комментариев, пропусков «...» и пустых. Вход: текст. Отдаёт: [строки]."""
    # комментарий справа отрезается по «  #»: в разборе пояснения свои, сравнивается только код
    out = [s.split("  #")[0].strip() for s in t.splitlines()]   # строки без отступа и комментария
    return [s for s in out if s and not s.startswith("#") and s not in ("...", "…")]


def unmarked(paths):
    """Блоки ```python без пометки: совпадают ли с кодом. Вход: папки. Отдаёт: ничего (печать)."""
    idx, files = _defs()                             # где что определено; все файлы кода
    total = {}                                       # итог по видам
    for path in docs(paths):                         # каждый .md
        text = open(os.path.join(ROOT, path), encoding="utf-8").read()   # разбор целиком
        marked = {m.end() + 1 for m in MARK.finditer(text)}   # блоки сразу под пометкой
        rows = []                                    # находки по файлу
        for m in FENCE.finditer(text):               # каждый блок Python
            if m.start() in marked:                  # под пометкой — его ведёт скрипт
                continue
            kind, where = _classify(m.group(1), idx, files)   # вид совпадения и где в коде
            total[kind] = total.get(kind, 0) + 1
            rows.append("    стр. %d: %s%s" % (_line(text, m.start()), kind, " → " + where if where else ""))
        if rows:                                     # в файле есть блоки без пометки
            print("%s — блоков без пометки %d" % (path, len(rows)))
            print("\n".join(rows))
    print("итого: " + (" · ".join("%s — %d" % (k, total[k]) for k in KINDS if k in total) or "блоков без пометки нет"))


KINDS = ("совпадает", "почти (≥ 80 % строк)", "разошёлся (50–80 %)", "не найден (< 50 %)", "пустой")   # виды блоков


def _sub(blk, src):
    """Стоит ли блок в коде подряд. Вход: строки блока и кода (без отступов). Отдаёт: да/нет."""
    n = len(blk)                                     # строк в блоке
    return any(src[i:i + n] == blk for i in range(len(src) - n + 1) if src[i] == blk[0])


def _classify(block, idx, files):
    """Совпадает ли блок с кодом. Вход: текст блока, указатель определений, файлы кода.
    Отдаёт: (вид из KINDS, «где · доля»)."""
    blk = _norm(block)                               # строки блока без отступов
    if not blk:                                      # пустой блок
        return KINDS[4], ""
    m = NAME.search(block)                           # первое def/class в блоке
    named = idx.get(m.group(1), []) if m else []     # определения с тем же именем
    # есть имя — сравнить с его определениями; нет — со всеми файлами (кусок тела функции)
    pool = [("%s::%s" % (p, q), source(p, q)) for p, q in named] or [(p, _parsed(p)[0]) for p in files]
    best = (0.0, "")                                 # лучшее совпадение: доля строк, где
    for where, text in pool:                         # каждый кандидат
        src = _norm(text)                            # строки кода
        if _sub(blk, src):                           # блок — кусок кода подряд
            return KINDS[0], where
        share = sum(s in set(src) for s in blk) / len(blk)   # доля строк блока, что есть в коде
        if share > best[0]:                          # ближе прежнего
            best = (share, where)
    kind = KINDS[1] if best[0] >= 0.8 else KINDS[2] if best[0] >= 0.5 else KINDS[3]   # вид по доле
    return kind, "%s · %d %%" % (best[1], round(100 * best[0])) if best[1] else ""


def _root(f):
    """Имя в начале цепочки `a.b.c`. Вход: узел ast. Отдаёт: имя или None."""
    while isinstance(f, ast.Attribute):              # вниз по цепочке атрибутов
        f = f.value
    return f.id if isinstance(f, ast.Name) else None


def _called(node, external=()):
    """Имена вызовов внутри узла в порядке текста, без повторов. Вход: узел ast, имена внешних модулей файла
    (`np`, `math` — их вызовы не в счёт). Отдаёт: [имена]."""
    found = []                                       # (строка, столбец, имя) каждого вызова
    for c in ast.walk(node):                         # все узлы внутри
        if isinstance(c, ast.Call):                  # вызов: f(…) или obj.f(…)
            f = c.func                               # что вызывают
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None   # имя
            if isinstance(f, ast.Attribute) and _root(f.value) in external:   # np.linalg.norm — не наш
                continue
            if name:                                 # вызов по имени — запомнить
                found.append((c.lineno, c.col_offset, name))
    out = []                                         # имена без повторов
    for _l, _c, name in sorted(found):               # по порядку в тексте
        if name not in out:                          # первое появление
            out.append(name)
    return out


def _call_map():
    """Кто кого зовёт во всём коде. Вход: ничего. Отдаёт: ({(путь, «Класс.метод»): [имена]}, указатель _defs)."""
    idx, files = _defs()                             # где что определено; файлы кода
    ours = {(r.split("/")[1] if r.startswith("src/") else r.split("/")[-1]).replace(".py", "") for r in files}   # свои модули
    out = {}                                         # функция → вызванные ею имена
    for rel in files:                                # каждый файл кода
        tree = _parsed(rel)[1]                       # дерево файла
        external = {a.asname or a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                     for a in n.names if a.name.split(".")[0] not in ours}   # import numpy as np → «np»
        stack = [(n, "") for n in tree.body]         # узлы и приставка имени
        while stack:                                 # обход вглубь: класс → метод → вложенная
            n, prefix = stack.pop()                  # узел и приставка его имени
            if not isinstance(n, DEFS):              # не функция и не класс
                continue
            q = prefix + n.name                      # полное имя
            if not isinstance(n, ast.ClassDef):      # функция — её вызовы
                out[(rel, q)] = _called(n, external)
            stack += [(c, q + ".") for c in n.body]
    return out, idx


def stub(target):
    """Заготовка раздела по эталону; каркас дерева — из кода: кого зовёт функция, кто зовёт её.
    Вход: «путь::Имя». Отдаёт: текст Markdown."""
    code = piece(target)                             # код под пометкой
    path, _, qual = (s.strip() for s in target.split(" | ")[0].partition("::"))   # файл и имя
    calls, idx = _call_map()                         # кто кого зовёт; где что определено
    name = qual.split(".")[-1] if qual else os.path.basename(path)   # короткое имя
    inner = [n for n in calls.get((path, qual), []) if n in idx and n != name]   # функции проекта, что она зовёт
    outer = sorted(q for (p, q), v in calls.items() if name in v and (p, q) != (path, qual))   # кто зовёт её
    tree = ["%s(⬜ вход)  → ⬜ что отдаёт, единицы" % name]   # корень дерева
    tree += ["%s %s(…)  → ⬜" % ("└─" if i == len(inner) - 1 else "├─", n) for i, n in enumerate(inner)]
    tree.append("дальше: зовут %s → ⬜ куда уходит результат" % (", ".join(outer[:6]) or "⬜"))
    return STUB % (name, path + (" :: " + qual if qual else ""), "\n".join(tree), target, code)


def main(argv):
    """Разбор аргументов и запуск. Вход: аргументы командной строки. Отдаёт: код возврата."""
    flags = [a for a in argv if a.startswith("--")]  # ключи
    rest = [a for a in argv if not a.startswith("--")]   # пути
    if "--list" in flags:                            # учёт: какой код где показан
        listing(rest[0] if rest else None)
        return 0
    if "--unmarked" in flags:                        # блоки, скопированные руками
        unmarked(rest or UNMARKED)
        return 0
    if "--stub" in flags:                            # заготовка раздела
        print(stub(rest[0]))
        return 0
    check = "--check" in flags                       # только сверить
    moved, broken = refresh(rest or DOCS, check)     # разошедшихся блоков и битых пометок
    return 1 if broken or (check and moved) else 0


if __name__ == "__main__":                           # запуск скриптом, а не импорт
    sys.exit(main(sys.argv[1:]))
