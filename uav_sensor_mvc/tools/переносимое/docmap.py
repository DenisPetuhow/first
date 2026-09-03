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
SKIP = {"CLAUDE.md", "README.md", "теория/РАЗБОР_DOCMAP.md",
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
        if not any(os.path.basename(f) == "README.md" for f in files):
            problems.append("папка без README: %s (%d файлов)" % (d, len(files)))
    for rel in docs:
        n = len(io.open(os.path.join(ROOT, rel), encoding="utf-8").read().split("\n"))
        if n > MAX_LINES:
            problems.append("файл длиннее %d строк — пора делить: %s (%d)"
                            % (MAX_LINES, rel, n))
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

    print("\n── СТРУКТУРА ──")
    problems = check_structure(docs)
    for p in problems:
        print("  " + p)
    print("  папок и файлов в порядке" if not problems else "  замечаний: %d" % len(problems))

    if stale or bad or problems or wrong:
        print("\nтребуется вмешательство: оглавлений %d, неверных номеров строк %d, "
              "битых ссылок %d, замечаний %d" % (stale, len(wrong), len(bad), len(problems)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
