# -*- coding: utf-8 -*-
"""
КАРТА КОДА — оглавление для исходников, как `docmap.py` для документов.

Собирает по каждому модулю таблицу «строка · что · зачем» и раскладывает в папку
`теория/карта_кода/`: README со сводкой + части `КАРТА_N.md`. Дальше файл кода
открывается НЕ целиком, а с нужной строки (`Read` с `offset`).

    python tools/переносимое/codemap.py             # пересобрать карту
    python tools/переносимое/codemap.py --check     # не менять; код 1, если устарела
    python tools/переносимое/codemap.py --stale     # что описано в документах и давно не сверялось

ЗАЧЕМ. Крупный модуль читать целиком нельзя: в этом проекте `model/threat_grid.py` —
около 3 000 строк ≈ 39 тыс. токенов, пятая часть окна (точное число — в самой карте). Без карты остаётся греп, а грепом видно
имя, но не видно сигнатуру целиком — и она достраивается по памяти. Так уже случалось:
`add_relief` вместо `set_relief`, `iter_batch(150)` вместо `iter_batch()`. Карта того же
модуля весит ≈2.6 тыс. токенов — в пятнадцать раз дешевле, и сигнатура в ней настоящая.

ЧТО ПОПАДАЕТ В КАРТУ:
  * классы и функции верхнего уровня, методы классов — со строкой и аргументами;
  * первая строка докстринга — «зачем оно», без пересказа кода;
  * ЯКОРЯ РАЗДЕЛОВ — комментарии-разделители вида `# ─── ИМЯ ───` или `# === ИМЯ ===`:
    они превращают список из сотни функций в структуру;
  * «отдаёт наружу» — какие имена модуля реально импортируются другими модулями.
    Это ответ на вопрос «что у него публичное», добытый из кода, а не из обещаний.

⚠️ КАРТА ГЕНЕРИРУЕТСЯ, РУКАМИ ЕЁ НЕ ПРАВЯТ. Ровно по той же причине, по которой не
правят оглавления документов: номера строк сдвигаются при каждой правке, и написанная
руками карта начинает врать — тихо и убедительно.
"""
import argparse
import ast
import io
import os
import re
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MARKERS = ("CLAUDE.md", "config.py", ".git")
SKIP_DIRS = {".git", ".claude", "__pycache__", "venv", ".venv", "node_modules",
             "tile_cache", "geo_cache", "offline_packages", "resurce", "dist", "build",
             "docs", "теория", "фото пример"}
OUT_DIR_REL = os.path.join("теория", "карта_кода")   # README + части КАРТА_N.md
PART_MAX_LINES = 1500      # порог деления карты на части — как у документов (docmap)
# ⚠️ ПОРОГ ПОДРОБНОГО ОПИСАНИЯ — тот же, что у оглавлений документов в docmap.py.
# Модуль короче 300 строк дешевле открыть целиком (≈4 тыс. токенов), чем расписывать
# по функциям: подробная таблица на такой файл только раздувает карту. Такие модули
# попадают в README одной строкой — со списком имён, чтобы было видно, что внутри.
MIN_LINES = 300
# якорь раздела внутри файла: строка-комментарий из повторяющихся символов вокруг имени
# отступ до 4 пробелов: разделитель верхнего уровня или внутри класса. Более глубокие
# (внутри функции) в карту НЕ идут — это пояснения к шагам алгоритма, а не разделы
ANCHOR = re.compile(r"^ {0,4}#\s*[─=—\-#]{2,}\s*(.+?)\s*[─=—\-#]{2,}\s*$")
HEAD = ("<!-- КАРТА КОДА · сгенерировано tools/переносимое/codemap.py — руками не править -->")


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
        out += [os.path.join(cur, f) for f in sorted(files) if f.endswith(".py")]
    return sorted(out)


def _first_doc_line(node):
    doc = ast.get_docstring(node) or ""
    line = doc.strip().split("\n")[0].strip()
    return line[:96]


def _sig(node):
    args = [a.arg for a in node.args.args if a.arg not in ("self", "cls")]
    if node.args.vararg:
        args.append("*" + node.args.vararg.arg)
    if node.args.kwarg:
        args.append("**" + node.args.kwarg.arg)
    short = ", ".join(args[:6]) + (", …" if len(args) > 6 else "")
    return "%s(%s)" % (node.name, short)


def scan_file(path):
    """[(строка, вид, подпись, назначение)] + список якорей разделов."""
    src = io.open(path, encoding="utf-8", errors="replace").read()
    lines = src.splitlines()
    anchors = []
    for i, ln in enumerate(lines, 1):
        m = ANCHOR.match(ln)
        if m and len(m.group(1)) > 2 and not m.group(1).startswith("-"):
            anchors.append((i, m.group(1)))
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [], anchors, len(lines), "НЕ РАЗОБРАН: %s" % e
    items = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            items.append((node.lineno, "класс", node.name, _first_doc_line(node)))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    items.append((sub.lineno, "метод", _sig(sub), _first_doc_line(sub)))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            items.append((node.lineno, "функция", _sig(node), _first_doc_line(node)))
    return items, anchors, len(lines), None


def module_name(root, path):
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    return rel


def public_names(root, files):
    """{модуль: {имя: [кто импортирует]}} — что реально берут снаружи."""
    used = {}
    for f in files:
        try:
            tree = ast.parse(io.open(f, encoding="utf-8", errors="replace").read())
        except SyntaxError:
            continue
        who = module_name(root, f)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module.replace(".", "/")
                for al in node.names:
                    used.setdefault(mod, {}).setdefault(al.name, []).append(who)
    return used


def git_counts(root, path, since_rev=None):
    """Сколько коммитов трогали путь (весь, либо после ревизии)."""
    cmd = ["git", "-C", root, "rev-list", "--count", "HEAD"]
    if since_rev:
        cmd = ["git", "-C", root, "rev-list", "--count", "%s..HEAD" % since_rev]
    cmd += ["--", path]
    try:
        return int(subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip())
    except Exception:
        return -1


def last_commit(root, path):
    try:
        out = subprocess.check_output(
            ["git", "-C", root, "log", "-1", "--format=%H %ad", "--date=short", "--", path],
            stderr=subprocess.DEVNULL).decode().strip()
        return out.split(" ", 1) if out else (None, None)
    except Exception:
        return (None, None)


def module_block(root, name, items, anchors, err, pub):
    """Раздел карты по одному модулю."""
    out = ["", "## %s" % name, ""]
    if err:
        return out + ["⚠️ %s" % err, ""]
    takers = pub.get(name[:-3] if name.endswith(".py") else name, {})
    if takers:
        out += ["**Отдаёт наружу:** " + ", ".join("`%s`" % k for k in sorted(takers)) + ".", ""]
    anc = dict(anchors)
    out += ["| стр. | что | зачем |", "|---:|---|---|"]
    for ln, kind, sig, doc in items:
        for a_ln in sorted(anc):
            if a_ln < ln:
                out.append("| | **· %s ·** | |" % anc.pop(a_ln))
        mark = "**%s**" % sig if kind == "класс" else "`%s`" % sig
        out.append("| %d | %s | %s |" % (ln, mark, doc.replace("|", r"\|")))
    out.append("")
    return out


def build(root):
    """{имя файла: текст} — README-указатель и части КАРТА_N.md.

    ⚠️ ДЕЛЕНИЕ НА ЧАСТИ — то же правило, что у документов: часть не длиннее
    PART_MAX_LINES строк. Один файл на весь код рос бы вместе с программой и однажды
    перестал бы помещаться в окно — ровно та беда, от которой карта и спасает.
    Модуль ЦЕЛИКОМ лежит в одной части: разрывать таблицу функций посередине нельзя."""
    files = py_files(root)
    pub = public_names(root, files)
    stat = []
    for f in files:
        items, anchors, nlines, err = scan_file(f)
        ncls = sum(1 for it in items if it[1] == "класс")
        nfun = sum(1 for it in items if it[1] != "класс")
        stat.append((nlines, module_name(root, f), ncls, nfun, items, anchors, err))
    stat.sort(reverse=True)

    # --- разложить модули по частям ---
    parts, cur, cur_len, cur_mods = [], [], 0, []
    empty, small = [], []
    for nlines, name, _c, _f, items, anchors, err in stat:
        if not items and not err:
            empty.append(name)
            continue
        if nlines < MIN_LINES:
            small.append((name, nlines, [it[2].split("(")[0] for it in items]))
            continue
        block = module_block(root, name, items, anchors, err, pub)
        if cur and cur_len + len(block) > PART_MAX_LINES:
            parts.append((cur, cur_mods)); cur, cur_len, cur_mods = [], 0, []
        # СМЕЩЕНИЕ раздела внутри части: блок начинается с пустой строки, заголовок
        # «## имя» — вторая. Высота шапки части прибавится ниже.
        cur_mods.append((name, cur_len + 2))
        cur += block; cur_len += len(block)
    if cur:
        parts.append((cur, cur_mods))

    where = {}                       # модуль -> (номер части, строка в этой части)
    for i, (_body, mods) in enumerate(parts, 1):
        for m, off in mods:
            where[m] = (i, off)

    out_files = {}
    for i, (body, mods) in enumerate(parts, 1):
        head = [HEAD, "", "# Карта кода — часть %d из %d" % (i, len(parts)), "",
                "> Указатель со сводкой — [README.md](README.md). Здесь: %s."
                % ", ".join("`%s`" % m for m, _o in mods), "",
                "> Найти строку → открыть файл `Read` с `offset`. Файл целиком не читать.", ""]
        # ⚠️ Строки в README считаются ВМЕСТЕ с шапкой части: читатель открывает файл
        # целиком. Ровно на этом однажды ошибся docmap — считал строки без блока
        # оглавления, и номера врали на его высоту (2_ОГЛАВЛЕНИЯ_И_ПОИСК §2).
        for m, off in mods:
            where[m] = (where[m][0], off + len(head))
        out_files["КАРТА_%d.md" % i] = "\n".join(head + body) + "\n"

    # --- README: краткое описание и характеристики ---
    total_l = sum(s0[0] for s0 in stat)
    map_lines = sum(len(t.splitlines()) for t in out_files.values())
    r = [HEAD, "", "# Карта кода — указатель", "",
         "> **Что это.** Оглавление ИСХОДНИКОВ: где какая функция лежит, с какой строки и",
         "> что делает. Читается вместо того, чтобы открывать модуль целиком.",
         "",
         "> **Как пользоваться.** Найти модуль в таблице → открыть его часть → взять номер",
         "> строки → `Read` с `offset`. Сигнатуру брать отсюда, а не по памяти.",
         "",
         "> **Сгенерировано** `tools/переносимое/codemap.py`, руками не править.",
         "> Пересобрать: `python tools/переносимое/codemap.py`;",
         "> проверить свежесть: `--check`; что устарело в документации: `--stale`.", "",
         "## Характеристики", "",
         "| | |", "|---|---|",
         "| файлов кода | %d |" % len(files),
         "| строк кода | %d ≈ %d тыс. токенов |" % (total_l, total_l * 13 // 1000),
         "| строк карты | %d ≈ %d тыс. токенов |" % (map_lines, map_lines * 9 // 1000),
         "| частей | %d (порог деления — %d строк) |" % (len(parts), PART_MAX_LINES),
         "",
         "## Модули: где искать", "",
         "| Модуль | Строк | ≈токенов | Классов | Функций | Где в карте |",
         "|---|---|---|---|---|---|"]
    for nlines, name, ncls, nfun, _i, _a, _e in stat:
        if name in where:
            part, off = where[name]
            r.append("| `%s` | %d | %d | %d | %d | [КАРТА_%d.md](КАРТА_%d.md) стр. **%d** |"
                     % (name, nlines, nlines * 13, ncls, nfun, part, part, off))
    if small:
        r += ["", "## Мелкие модули — читаются целиком", "",
              "Короче %d строк: открыть файл дешевле, чем расписывать по функциям."
              % MIN_LINES, "",
              "| Модуль | Строк | Что внутри |", "|---|---|---|"]
        for name, nlines, names in sorted(small, key=lambda x: -x[1]):
            lst = ", ".join("`%s`" % n for n in names[:12])
            if len(names) > 12:
                lst += ", … (%d всего)" % len(names)
            r.append("| `%s` | %d | %s |" % (name, nlines, lst or "—"))
    if empty:
        r += ["", "**Пустые модули** (задают пакет, содержимого нет): "
              + ", ".join("`%s`" % e for e in sorted(empty)) + ".", ""]
    out_files["README.md"] = "\n".join(r) + "\n"
    return out_files


def uncommitted(root):
    """Файлы, изменённые в рабочем дереве (правка есть, коммита ещё нет).

    ⚠️ Нужны, чтобы отчёт не врал. Он считает по КОММИТАМ: пока правка документа не
    закоммичена, git видит документ старым и требует сверить ровно то, что сейчас и
    переписывается. Такие строки помечаются «правится сейчас». Это НЕ повод коммитить
    чаще: ритм коммитов задаётся смыслом работы, а не удобством отчёта."""
    try:
        # ⚠️ core.quotepath=false ОБЯЗАТЕЛЕН: иначе git отдаёт кириллические пути
        # экранированными («"ÑÐµ..."»), и сравнение с путями документов
        # не совпадает никогда — пометка «правится сейчас» молча не появляется.
        out = subprocess.check_output(
            ["git", "-C", root, "-c", "core.quotepath=false", "status", "--porcelain"],
            stderr=subprocess.DEVNULL).decode("utf-8", "replace")
    except Exception:
        return set()
    # ⚠️ git печатает пути ОТ КОРНЯ РЕПОЗИТОРИЯ, а он может быть выше корня проекта
    # (у нас репозиторий — родительская папка, и пути идут как «uav_sensor_mvc/…»).
    # Без снятия этого префикса ни один путь не совпадёт, и пометка не появится.
    try:
        prefix = subprocess.check_output(
            ["git", "-C", root, "rev-parse", "--show-prefix"],
            stderr=subprocess.DEVNULL).decode("utf-8", "replace").strip()
    except Exception:
        prefix = ""
    files = set()
    for ln in out.splitlines():
        if len(ln) > 3:
            path = ln[3:].strip().strip('"')
            if prefix and path.startswith(prefix):
                path = path[len(prefix):]
            files.add(path)
    return files


def verify_offsets(out_files):
    """Сверить строки из README с содержимым частей: на указанной строке обязан стоять
    заголовок раздела модуля. Возвращает (совпало, список расхождений).

    ⚠️ ПРОВЕРЯЕМ РЕЗУЛЬТАТ, А НЕ НАМЕРЕНИЕ. Сверка «сгенерированное совпадает с тем, что
    сгенерировал бы я» пропускает систематический сдвиг: генератор согласен сам с собой,
    а номера при этом врут на высоту шапки. Так уже было с оглавлениями документов —
    врали на 20–44 строки во всех файлах сразу, и `--check` рапортовал «актуально»."""
    ok, bad = 0, []
    readme = out_files.get("README.md", "")
    for m in re.finditer(
            r"^\| `([^`]+)` \|.*\[(КАРТА_\d+\.md)\]\(КАРТА_\d+\.md\) стр\. \*\*(\d+)\*\* \|",
                         readme, re.M):
        name, part, ln = m.group(1), m.group(2), int(m.group(3))
        lines = out_files.get(part, "").splitlines()
        got = lines[ln - 1] if 0 < ln <= len(lines) else "<за пределами файла>"
        if got.strip() == "## " + name:
            ok += 1
        else:
            bad.append((name, part, ln, got.strip()[:50]))
    return ok, bad


def stale_report(root, docs_dir="теория"):
    """Документы против кода, КОТОРЫЙ ОНИ САМИ УПОМИНАЮТ.

    Связь берётся из текста: если документ пишет про `model/threat_grid.py` или просто
    `threat_grid.py`, считаем, что он этот модуль описывает. Дальше — сколько раз этот
    модуль правили ПОСЛЕ последней правки документа.

    ⚠️ Первая версия считала коммиты по ВСЕМУ коду и была бесполезна: у всех документов
    выходило одно и то же число, включая те, что кода не касаются вовсе («Стиль
    оформления»). Связь по упоминаниям делает отчёт адресным: видно не «документация
    стареет», а «вот эта методичка описывает вот этот модуль, и модуль ушёл вперёд»."""
    by_name = {}
    for f in py_files(root):
        by_name.setdefault(os.path.basename(f), []).append(os.path.relpath(f, root))
    docs = []
    for cur, dirs, files in os.walk(os.path.join(root, docs_dir)):
        dirs[:] = [d for d in dirs if d not in {"архив", "основа", "рис"}]
        docs += [os.path.join(cur, f) for f in files if f.endswith(".md")]

    rows = []
    for d in docs:
        rel = os.path.relpath(d, root)
        text = io.open(d, encoding="utf-8", errors="replace").read()
        named = sorted({m for m in re.findall(r"[\w/\\.-]*?([\w-]+\.py)", text)
                        if m in by_name})
        if not named:
            continue                      # документ про код не пишет — сверять нечего
        rev, date = last_commit(root, rel)
        if not rev:
            continue
        hits = []
        for base in named:
            for path in by_name[base]:
                k = git_counts(root, path, since_rev=rev)
                if k > 0:
                    hits.append((k, base))
        if hits:
            hits.sort(reverse=True)
            rows.append((sum(k for k, _b in hits), rel, date, hits[:4]))
    return sorted(rows, reverse=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="карта кода: оглавление исходников")
    ap.add_argument("--check", action="store_true", help="не менять файл; код 1, если устарела")
    ap.add_argument("--stale", action="store_true", help="отчёт «срок годности» документов")
    ap.add_argument("--root", default=None)
    a = ap.parse_args(argv)

    root = a.root or find_root(os.path.dirname(os.path.abspath(__file__)))
    if a.stale:
        rows = stale_report(root)
        dirty = uncommitted(root)
        print("СРОК ГОДНОСТИ ДОКУМЕНТАЦИИ")
        print("Сколько раз правили КОД, который документ сам упоминает, ПОСЛЕ последней")
        print("правки этого документа.\n")
        if not rows:
            print("Документов, отставших от упоминаемого кода, нет.")
            return 0
        for n, rel, date, hits in rows[:20]:
            live = rel.replace(os.sep, "/") in dirty
            mark = " " if live else ("!" if n >= 10 else ("~" if n >= 4 else " "))
            note = "  <- ПРАВИТСЯ СЕЙЧАС, коммита ещё нет" if live else ""
            print("%s %s   (правился %s)%s" % (mark, rel, date, note))
            print("    %s" % ", ".join("%s: %d" % (b, k) for k, b in hits))
        print("\nЧисло — не ошибка, а ПОВОД СВЕРИТЬ: механизм мог измениться, а описание")
        print("остаться прежним. Пустой список значит лишь, что документы свежее кода.")
        print("Что именно сверять — семь шагов в 3_СВЕРКА_С_КОДОМ.md.")
        return 0

    out_files = build(root)
    dst_dir = os.path.join(root, OUT_DIR_REL)
    ok_off, bad_off = verify_offsets(out_files)
    if bad_off:
        print("СТРОКИ В README НЕ СОВПАЛИ С ЧАСТЯМИ (%d):" % len(bad_off))
        for name, part, ln, got in bad_off[:10]:
            print("  %s -> %s:%d, а там: %r" % (name, part, ln, got))
        return 1
    if a.check:
        stale = []
        for fname, text in out_files.items():
            p = os.path.join(dst_dir, fname)
            old = io.open(p, encoding="utf-8").read() if os.path.exists(p) else ""
            if old != text:
                stale.append(fname)
        extra = ([f for f in os.listdir(dst_dir) if f.endswith(".md") and f not in out_files]
                 if os.path.isdir(dst_dir) else [])
        if stale or extra:
            print("КАРТА КОДА УСТАРЕЛА: %s" % ", ".join(stale + ["лишний " + e for e in extra]))
            print("пересобрать: python tools/переносимое/codemap.py")
            return 1
        print("карта кода актуальна (%d файлов, строк сверено %d)"
              % (len(out_files), ok_off))
        return 0
    if not os.path.isdir(dst_dir):
        os.makedirs(dst_dir)
    for fname in os.listdir(dst_dir):            # убрать части, которых больше нет
        if fname.endswith(".md") and fname not in out_files:
            os.remove(os.path.join(dst_dir, fname))
    total = 0
    for fname, text in out_files.items():
        io.open(os.path.join(dst_dir, fname), "w", encoding="utf-8").write(text)
        total += len(text.splitlines())
    print("записано: %s — %d файлов, %d строк; строк README сверено с частями: %d"
          % (OUT_DIR_REL, len(out_files), total, ok_off))
    return 0


if __name__ == "__main__":
    sys.exit(main())
