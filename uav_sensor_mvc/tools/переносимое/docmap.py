# -*- coding: utf-8 -*-
"""Обслуживание документации: оглавления, ссылки, структура папок.

ЗАЧЕМ. Документация — ≈160 тыс. токенов (80 % окна): читать её целиком нельзя.
Работает трёхуровневая схема — CLAUDE.md (навигация) → README папки → раздел документа.
Чтобы схема не разъехалась, три вещи проверяются машиной, а не глазами:

    оглавления   номера строк сдвигаются при каждой правке середины файла;
    ссылки       221 ссылка между документами — после переноса файла рвутся молча;
    структура    папка без README выпадает из навигации.

ЗАПУСК:
    python tools/переносимое/docmap.py             оглавления + отчёт по ссылкам и структуре
    python tools/переносимое/docmap.py --check     ничего не меняет; код возврата 1, если что-то не так
    python tools/переносимое/docmap.py файл.md     только этот файл

Блок оглавления заключён в маркеры и переписывается целиком; остальной текст не трогается.
Скрипт ИДЕМПОТЕНТЕН: повторный запуск не меняет ни одного файла.
"""
import io
import os
import re
import sys

# Консоль Windows по умолчанию cp1251/cp866 — печать русского текста роняла скрипт
# с UnicodeEncodeError. Переключаем поток на utf-8; errors="replace" страхует случай,
# когда терминал всё же не умеет часть символов.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BEGIN = "<!-- ОГЛАВЛЕНИЕ · сгенерировано tools/переносимое/docmap.py — руками не править -->"
END = "<!-- /ОГЛАВЛЕНИЕ -->"

MIN_LINES = 300          # ниже этого порога оглавление только мешает
MAX_LINES = 1500         # выше — файл пора делить на части ИМЯ_1.md, ИМЯ_2.md, …

# ⚠️ ФАЙЛЫ, КОТОРЫЕ РЕШЕНО НЕ ДЕЛИТЬ. Порог — сигнал «проверь навигацию», а не команда
# резать: связный документ, разрезанный ради цифры, читать труднее, а не легче, и все
# ссылки вида «§14.1» приходится переписывать (замер: на одну методичку ведут 137 ссылок).
#
# Без этого списка `--check` возвращал бы 1 ВСЕГДА — и правило «проверка пройдена по коду
# возврата» переставало работать: красный код перестаёт что-либо значить, если он горит
# постоянно. Решение заказчика 11.09.2026.
#
# Заводя исключение, писать ПРИЧИНУ: через месяц «почему этот можно» не восстановить.
LONG_OK = {
    "теория/вкладка_3/МЕТОДИЧКА_КАРТА_УГРОЗ.md":
        "связный разбор механики; 137 ссылок на её §-разделы",
    "теория/планы/8_ПЛАН_КАРТА_И_ИНТЕРФЕЙС.md":
        "закрытый план — хроника, не список дел; 60 ссылок",
    "теория/архив/журнал/ЖУРНАЛ_5.md":
        "закрытая часть журнала в архиве — хроника, делить нечего (план 9, задача 9.2)",
}
# скрипт лежит в tools/переносимое/, корень проекта — на два уровня выше
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

SKIP_DIRS = {".git", ".claude", "tile_cache", "geo_cache", "offline_packages",
             "__pycache__", "фото пример", "docs"}
# файлы-указатели: у них своя структура, оглавление по заголовкам им не нужно
# РАЗБОР_DOCMAP.md — личный документ заказчика (разбор скрипта для чтения),
# в систему навигации не входит: ни оглавления, ни упоминаний в указателях
# ⚠️ ПАПКА КАРТЫ КОДА — ЧУЖАЯ ТЕРРИТОРИЯ. Её файлы генерирует codemap.py, и если
# вставить туда своё оглавление, два генератора начнут переписывать один файл по кругу:
# docmap добавит блок, codemap затрёт его при пересборке, и `--check` обоих будет вечно
# ругаться. Проверено на деле — оглавление успело попасть в КАРТА_КОДА.md (журнал п. 213).
SKIP_GENERATED = "теория/карта_кода/"
SKIP = {"CLAUDE.md", "README.md", "теория/код/РАЗБОР_DOCMAP.md",
        "теория/README.md", "теория/журнал/README.md",
        "теория/планы/README.md", "теория/архив/README.md",
        "теория/вкладка_3/README.md", "теория/карты/README.md",
        "теория/интерфейс/README.md"}


def norm(p):
    return p.replace("\\", "/")


def walk_md():
    """Все документы проекта (относительные пути от корня)."""
    out = []
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".md"):
                out.append(norm(os.path.relpath(os.path.join(root, f), ROOT)))
    return sorted(out)


# ── ОГЛАВЛЕНИЯ ──────────────────────────────────────────────────────────────────
def build_toc(lines, pos):
    """Заголовки 2–3 уровня с номерами строк. Внутри ``` не считаются.

    ⚠️ НОМЕРА СЧИТАЮТСЯ ДЛЯ ФАЙЛА С УЖЕ ВСТАВЛЕННЫМ БЛОКОМ. Заголовки ищутся по тексту
    БЕЗ оглавления (иначе блок сдвигал бы сам себя), но читатель открывает файл ВМЕСТЕ с
    блоком — и всё, что ниже места вставки, уезжает вниз ровно на его высоту. Забыл этот
    сдвиг — и оглавление врёт на 20–44 строки во всех файлах сразу (журнал п. 181).

    Высота блока известна заранее: 4 строки шапки + по строке на заголовок + строка
    закрытия, плюс пустая строка после блока.
    """
    items, fenced = [], False
    for i, s in enumerate(lines, 1):
        if s.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = re.match(r"^(#{2,3})\s+(.+?)\s*$", s)
        if m:
            title = re.sub(r"[*`]|⚠️|✅|📌", "", m.group(2)).strip()
            items.append((len(m.group(1)), i, title))
    if not items:
        return None

    shift = 4 + len(items) + 1 + 1          # шапка + строки + END + пустая строка
    out = [BEGIN,
           "> **Оглавление** — номер строки · раздел. Открывать нужный раздел, а не файл",
           "> целиком (`Read` с `offset`). Пересобрать: `python tools/переносимое/docmap.py`.",
           ">"]
    for lvl, ln, title in items:
        # pos — номер строки вставки (0-based) в тексте без блока; всё, что на этой
        # позиции и ниже, после вставки сдвинется
        out.append("> %s`%4d` %s" % ("  " if lvl == 3 else "",
                                     ln + shift if ln > pos else ln, title))
    out.append(END)
    return out


def apply_toc(rel, check_only=False):
    path = os.path.join(ROOT, rel)
    lines = io.open(path, encoding="utf-8").read().split("\n")
    try:
        a, b = lines.index(BEGIN), lines.index(END)
        had = lines[a:b + 1]
        tail = b + 2 if b + 1 < len(lines) and not lines[b + 1].strip() else b + 1
        clean = lines[:a] + lines[tail:]
    except ValueError:
        had, clean = None, lines

    pos = next((i for i, s in enumerate(clean) if s.startswith("## ")), None)
    toc = build_toc(clean, pos) if pos is not None else None
    if toc is None:
        return "нет разделов"
    if had == toc:
        return "актуально"
    if check_only:
        return "УСТАРЕЛО" if had is not None else "НЕТ ОГЛАВЛЕНИЯ"
    io.open(path, "w", encoding="utf-8").write(
        "\n".join(clean[:pos] + toc + [""] + clean[pos:]))
    return "обновлено" if had is not None else "создано"


def verify_toc(docs):
    """Сверка обещанного номера строки с фактическим — по готовому файлу.

    Проверяет РЕЗУЛЬТАТ, а не намерение: открывает файл так же, как его увидит читатель,
    и смотрит, лежит ли раздел на обещанной строке. Именно эта проверка поймала бы сдвиг
    на высоту блока (п. 181), который сам генератор считал правильной работой.
    """
    wrong = []
    for rel in docs:
        L = io.open(os.path.join(ROOT, rel), encoding="utf-8").read().split("\n")
        if BEGIN not in L:
            continue
        promised = {}
        for s in L:
            m = re.match(r">\s*`\s*(\d+)`\s+(.+)$", s)
            if m:
                promised[m.group(2).strip()] = int(m.group(1))
        fenced = False
        for i, s in enumerate(L, 1):
            if s.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                continue
            m = re.match(r"^(#{2,3})\s+(.+?)\s*$", s)
            if m:
                title = re.sub(r"[*`]|⚠️|✅|📌", "", m.group(2)).strip()
                if title in promised and promised[title] != i:
                    wrong.append((rel, title, promised[title], i))
    return wrong


# ── ССЫЛКИ ──────────────────────────────────────────────────────────────────────
def check_links(docs):
    """Битые ссылки между документами. Возвращает список (файл, цель)."""
    bad = []
    for rel in docs:
        base = os.path.dirname(os.path.join(ROOT, rel))
        text = io.open(os.path.join(ROOT, rel), encoding="utf-8").read()
        for m in re.finditer(r"\[([^\]]*)\]\(([^)\s]+)\)", text):
            target = m.group(2).split("#")[0].strip()
            # пропускаем внешние и то, что ссылкой не является (запятая — координаты
            # Overpass-запроса внутри блока кода)
            if not target or target.startswith(("http", "mailto:")) or "," in target:
                continue
            # %20 — законная запись пробела в ссылке; без раскодирования папка с
            # пробелом в имени («Стиль оформления») ложно числится битой ссылкой
            target_fs = target.replace("%20", " ")
            if not os.path.exists(os.path.normpath(os.path.join(base, target_fs))):
                bad.append((rel, target))
    return bad


# ── ТЕКСТ ССЫЛКИ ПРОТИВ ЕЁ ПУТИ ─────────────────────────────────────────────────
# ⚠️ ЕЩЁ ОДИН МОЛЧАЛИВЫЙ КЛАСС ОШИБОК. Ссылка вида `[пособие/5_ТЕОРИЯ_В_КОДЕ.md](
# ../код/5_ТЕОРИЯ_В_КОДЕ.md)` рабочая: путь верен, файл существует, проверка ссылок
# довольна. Но ТЕКСТ называет папку, в которой файла давно нет, — и читатель идёт искать
# руками не туда. Появляется это само собой при переносе файлов: пути правит скрипт,
# подписи остаются прежними. Поймано 12.09.2026 на себе: после разбора корня теории
# так соврали пять ссылок.
LABEL_PATH = re.compile(r"([\wА-Яа-яЁё_-]+)/([\wА-Яа-яЁё_.-]+\.md)")


def check_labels(docs):
    """Ссылки, где текст называет папку, отличную от настоящей. Возвращает (файл, текст, путь)."""
    bad = []
    for rel in docs:
        base = os.path.dirname(rel)
        text = io.open(os.path.join(ROOT, rel), encoding="utf-8").read()
        for m in re.finditer(r"\[([^\]]*)\]\(([^)\s]+)\)", text):
            label, target = m.group(1), m.group(2).split("#")[0].strip()
            if not target.endswith(".md") or target.startswith(("http", "mailto:")):
                continue
            lm = LABEL_PATH.search(label)
            if not lm:
                continue                      # в тексте папка не названа — сверять нечего
            # настоящий путь цели ОТ КОРНЯ проекта
            real = norm(os.path.normpath(os.path.join(base, target.replace("%20", " "))))
            said = "%s/%s" % (lm.group(1), lm.group(2))
            # ⚠️ «журнал/README.md» и «теория/журнал/README.md» — одно и то же: в тексте
            # часто пишут хвост пути, а не полный. Поэтому сравнение по СУФФИКСУ.
            if not real.endswith(said):
                bad.append((rel, said, real))
    return bad


# ── §-РАЗДЕЛЫ В ССЫЛКАХ ─────────────────────────────────────────────────────────
# ⚠️ ЦЕЛЫЙ КЛАСС ОШИБОК, КОТОРЫЙ НЕ ЛОВИЛСЯ НИЧЕМ. Ссылка вида
# `[МЕТОДИЧКА §14.1](путь/МЕТОДИЧКА.md)` ведёт на СУЩЕСТВУЮЩИЙ файл, поэтому битой не
# считается, — а раздела §14.1 в нём может уже не быть. Такие ссылки уводят молча, и
# «битых ссылок 0» ничего о них не говорит. Замер 11.09.2026: только на одну методичку
# ведут 137 ссылок, почти все с номером раздела.
#
# Номер раздела в этом проекте живёт в ДВУХ видах, и оба законны:
#   * заголовком —      `## 14. Маршруты…`, `### 8.1.1. Что требуется`;
#   * строкой таблицы — `| 5.1 | Участок берётся из реестра | …` (так устроены
#     ОГРАНИЧЕНИЯ: там разделы — это пронумерованные правила, а не заголовки).
# Поэтому ищем номер в обоих положениях: иначе половина ссылок на ОГРАНИЧЕНИЯ окажется
# «битой» на ровном месте.
SEC_RE = re.compile(r"§\s*(\d+(?:\.\d+)*[а-яё]?)")


def _has_section(text, num):
    """Есть ли в документе раздел с таким номером — заголовком либо строкой таблицы."""
    n = re.escape(num)
    if re.search(r"(?m)^#{1,6}[^\n]*?(?<![\d.])" + n + r"(?![\d])", text):
        return True
    return bool(re.search(r"(?m)^\|\s*\**\s*" + n + r"\s*\**\s*\|", text))


def check_sections(docs):
    """Ссылки с §-номером, ведущие на файл, где такого раздела нет."""
    bad, cache = [], {}
    for rel in docs:
        base = os.path.dirname(os.path.join(ROOT, rel))
        text = io.open(os.path.join(ROOT, rel), encoding="utf-8").read()
        for m in re.finditer(r"\[([^\]]*)\]\(([^)\s]+)\)", text):
            label, target = m.group(1), m.group(2).split("#")[0].strip()
            nums = SEC_RE.findall(label)
            if not nums or not target.endswith(".md"):
                continue
            if target.startswith(("http", "mailto:")) or "," in target:
                continue
            path = os.path.normpath(os.path.join(base, target.replace("%20", " ")))
            if not os.path.exists(path):
                continue                       # это уже поймает check_links
            if path not in cache:
                cache[path] = io.open(path, encoding="utf-8").read()
            for num in nums:
                if not _has_section(cache[path], num):
                    bad.append((rel, target, num))
    return bad


# ── СТРУКТУРА ───────────────────────────────────────────────────────────────────
def check_structure(docs):
    """Папка с документами обязана иметь README, файл — укладываться в MAX_LINES."""
    problems = []
    dirs = {}
    for rel in docs:
        dirs.setdefault(os.path.dirname(rel), []).append(rel)
    for d, files in sorted(dirs.items()):
        if not d:
            continue
        # ⚠️ README может идти с номером (`01.README.md`) — заказчик нумерует файлы
        # папки ради порядка в проводнике (13.09.2026, `теория/код/`)
        if not any(re.match(r"^(?:\d+[._])?README\.md$", os.path.basename(f))
                   for f in files):
            problems.append("папка без README: %s (%d файлов)" % (d, len(files)))
    allowed = []
    for rel in docs:
        n = len(io.open(os.path.join(ROOT, rel), encoding="utf-8").read().split("\n"))
        if n <= MAX_LINES:
            continue
        key = rel.replace("\\", "/")
        if key in LONG_OK:                      # решено не делить — см. LONG_OK
            allowed.append("  разрешено: %s (%d) — %s" % (key, n, LONG_OK[key]))
            continue
        problems.append("файл длиннее %d строк — пора делить: %s (%d)"
                        % (MAX_LINES, rel, n))
    for line in allowed:
        print(line)
    return problems


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    check = "--check" in sys.argv
    docs = walk_md()

    targets = [norm(args[0])] if args else [
        d for d in docs
        if d not in SKIP
        and not d.startswith(SKIP_GENERATED)          # чужой генератор — см. комментарий
        and len(io.open(os.path.join(ROOT, d), encoding="utf-8").read().split("\n")) >= MIN_LINES]

    print("── ОГЛАВЛЕНИЯ ──")
    stale = 0
    for rel in targets:
        res = apply_toc(rel, check)
        if res in ("УСТАРЕЛО", "НЕТ ОГЛАВЛЕНИЯ"):
            stale += 1
        print("  %-52s %s" % (rel, res))

    print("\n── СВЕРКА НОМЕРОВ СТРОК ──")
    wrong = verify_toc(docs)
    for rel, title, p, a in wrong[:10]:
        print("  %-40s «%s»: обещано %d, на деле %d" % (rel[:40], title[:34], p, a))
    print("  расхождений: %d" % len(wrong))

    print("\n── ССЫЛКИ ──")
    bad = check_links(docs)
    for f, t in bad:
        print("  БИТАЯ  %-46s -> %s" % (f, t))
    print("  проверено файлов: %d, битых ссылок: %d" % (len(docs), len(bad)))

    print("\n── ТЕКСТ ССЫЛКИ ПРОТИВ ПУТИ ──")
    labels = check_labels(docs)
    for f, said, real in labels[:20]:
        print("  ВРЁТ   %-38s текст «%s» -> %s" % (f[:38], said, real))
    print("  подписей, называющих не ту папку: %d" % len(labels))

    print("\n── §-РАЗДЕЛЫ В ССЫЛКАХ ──")
    secs = check_sections(docs)
    for f, t, num in secs[:25]:
        print("  НЕТ §%-8s %-42s -> %s" % (num, f[:42], t))
    if len(secs) > 25:
        print("  … и ещё %d" % (len(secs) - 25))
    print("  ссылок с номером раздела не найдено: %d" % len(secs))

    print("\n── СТРУКТУРА ──")
    problems = check_structure(docs)
    for p in problems:
        print("  " + p)
    print("  папок и файлов в порядке" if not problems else "  замечаний: %d" % len(problems))

    if stale or bad or problems or wrong or secs or labels:
        print("\nтребуется вмешательство: оглавлений %d, неверных номеров строк %d, "
              "битых ссылок %d, врущих подписей %d, §-разделов %d, замечаний %d"
              % (stale, len(wrong), len(bad), len(labels), len(secs), len(problems)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
