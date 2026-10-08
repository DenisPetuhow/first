# -*- coding: utf-8 -*-
"""НОВЫЙ ПРОЕКТ ИЗ СТАРТОВОГО КОМПЛЕКТА — правила, скрипты, шаблоны одной командой (журнал 367).

Копирует `теория/переносимое/` (правила), `tools/переносимое/` (проверки), `теория/Стиль оформления/` и разворачивает
шаблоны `теория/переносимое/шаблон_проекта/**/*.tmpl` (подставляя {{ПРОЕКТ}}, {{ДАТА}}, {{ОПИСАНИЕ}}). Чужие файлы не
перезаписывает. ЗАПУСК: `python tools/переносимое/new_project.py <папка> --name "Имя" [--about "Что делает"]
[--memory <папка памяти Claude Code>] [--dry]`.
"""
import argparse
import datetime
import os
import re
import shutil
import sys

if hasattr(sys.stdout, "reconfigure"):               # консоль Windows — кириллица без порчи
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _find_root(markers=("CLAUDE.md",)):
    """Корень проекта-источника — ближайшая папка вверх с маркером. Вход: маркеры. Отдаёт: путь."""
    d = os.path.dirname(os.path.abspath(__file__))   # откуда начинаем подъём
    while True:                                      # вверх до маркера
        if any(os.path.exists(os.path.join(d, m)) for m in markers):   # корень найден
            return d
        up = os.path.dirname(d)                      # папка выше
        if up == d:                                  # дошли до диска
            raise SystemExit("не найден корень проекта-источника")
        d = up


SRC = _find_root()                                   # проект-источник
KIT = os.path.join(SRC, "теория", "переносимое", "шаблон_проекта")   # шаблоны
COPY_DIRS = [("теория/переносимое", ("шаблон_проекта", "ЖУРНАЛ_ПАПКИ.md")),   # (папка, что пропустить)
             ("tools/переносимое", ("__pycache__",)),
             ("теория/Стиль оформления", ("ЖУРНАЛ_ПАПКИ.md",))]
EMPTY_DIRS = ["src/model", "src/view", "src/controller", "теория/архив", "docs"]   # пустой каркас


def plan(dst, subst, memory):
    """Что куда скопировать. Вход: папка нового проекта, подстановки, папка памяти. Отдаёт: [(откуда, куда, рендер)]."""
    out = []                                         # действия копирования
    for rel, skip in COPY_DIRS:                      # папки комплекта — как есть
        base = os.path.join(SRC, rel)                # папка-источник
        for d, subs, files in os.walk(base):         # её файлы
            subs[:] = [s for s in subs if s not in skip]
            for f in files:                          # файл за файлом
                if f in skip or f.endswith(".pyc"):  # служебное — не нужно
                    continue
                p = os.path.join(d, f)               # путь в источнике
                out.append((p, os.path.join(dst, rel, os.path.relpath(p, base)), False))
    for d, _subs, files in os.walk(KIT):             # шаблоны — с подстановкой
        for f in files:                              # шаблон за шаблоном
            if not f.endswith(".tmpl"):              # не шаблон (README комплекта)
                continue
            rel = os.path.relpath(os.path.join(d, f), KIT)[:-5]   # путь без .tmpl
            if rel.split(os.sep)[0] == "память":     # заметки памяти — только в папку памяти
                if memory:                           # папка памяти задана
                    out.append((os.path.join(d, f), os.path.join(memory, os.path.basename(rel)), True))
                continue
            out.append((os.path.join(d, f), os.path.join(dst, rel), True))
    return out


def index_rows():
    """Строки указателей для копируемых файлов. Вход: нет. Отдаёт: (строки СТРУКТУРА_ТЕОРИИ, строки дерева скриптов)."""
    rows = []                                        # строки таблицы документов
    for rel, skip in COPY_DIRS:                      # папки комплекта
        if not rel.startswith("теория/"):            # документы — только из теории
            continue
        base = os.path.join(SRC, rel)                # папка-источник
        for d, subs, files in os.walk(base):         # её документы
            subs[:] = [s for s in subs if s not in skip]
            for f in sorted(files):                  # документ за документом
                if not f.endswith(".md") or f in skip:   # не документ или служебный
                    continue
                p = os.path.join(d, f)               # путь в источнике
                head = open(p, encoding="utf-8").readline().lstrip("# ").strip()   # заголовок документа
                link = "../" + os.path.relpath(p, os.path.join(SRC, "теория")).replace(os.sep, "/")   # от структура/
                rows.append("| [%s](%s) | %s |" % (os.path.relpath(p, base).replace(os.sep, "/"),
                                                   link.replace(" ", "%20"), head[:110]))
    tools = []                                       # строки дерева скриптов
    for f in sorted(os.listdir(os.path.join(SRC, "tools", "переносимое"))):   # скрипт за скриптом
        if f.endswith(".py"):                        # только скрипты
            doc = open(os.path.join(SRC, "tools", "переносимое", f), encoding="utf-8").read(600)   # начало файла
            m = re.search(r'"""(.+?)(?:\n|""")', doc)   # первая строка пояснения
            tools.append("│       ├── %-20s %s" % (f, (m.group(1).strip() if m else "")[:80]))
    return "\n".join(rows), "\n".join(tools)


def unlink_missing(path, dst):
    """Ссылки на то, чего в новом проекте нет, — в текст с пометкой. Вход: файл .md, корень проекта. Отдаёт: число замен."""
    with open(path, encoding="utf-8") as f:
        s = f.read()                                 # документ
    base = os.path.dirname(path)                     # от него считаются ссылки
    n = [0]                                          # замен, шт.

    def fix(m):
        """Одна ссылка: цель есть — как была, нет — текст с пометкой. Вход: совпадение. Отдаёт: строку."""
        t = m.group(2)                               # цель ссылки
        if re.match(r"[a-z]+:", t):                  # внешняя (http, mailto)
            return m.group(0)
        if os.path.exists(os.path.normpath(os.path.join(base, t.replace("%20", " ")))):   # цель есть
            return m.group(0)
        n[0] += 1
        return "%s (в проекте-источнике: `%s`)" % (m.group(1), t)
    s2 = re.sub(r"\[([^\]]+)\]\(([^)\s#]+)(?:#[^)]*)?\)", fix, s)   # ссылки Markdown
    if n[0]:                                         # что-то заменено — записать
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(s2)
    return n[0]


def mark_copied(path, date):
    """Отметка «взят из комплекта» в шапке скопированного документа — иначе check_dates сочтёт шапку отставшей от git.
    Вход: файл .md, дата ДД.ММ.ГГГГ. Отдаёт: ничего."""
    with open(path, encoding="utf-8") as f:
        s = f.read()                                 # документ
    if "> **Правился:** " in s and "взят из стартового комплекта" not in s:   # шапка есть, отметки нет
        s = s.replace("> **Правился:** ", "> **Правился:** %s — взят из стартового комплекта.\n> " % date, 1)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(s)


def render(text, subst):
    """Подставить метки {{…}}. Вход: текст, {метка: значение}. Отдаёт: текст."""
    for k, v in subst.items():                       # метка за меткой
        text = text.replace("{{%s}}" % k, v)
    return text


def main():
    """Развернуть комплект в новую папку. Вход: argv. Отдаёт: код возврата."""
    ap = argparse.ArgumentParser(description="новый проект из стартового комплекта")   # разбор аргументов
    ap.add_argument("dst"); ap.add_argument("--name", required=True); ap.add_argument("--about", default="")
    ap.add_argument("--memory", default=""); ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()                              # аргументы запуска
    dst = os.path.abspath(a.dst)                     # папка нового проекта
    if os.path.abspath(dst).startswith(SRC + os.sep) or dst == SRC:   # внутрь источника — нельзя
        raise SystemExit("папка нового проекта не должна лежать внутри проекта-источника")
    subst = {"ПРОЕКТ": a.name, "ДАТА": datetime.date.today().strftime("%d.%m.%Y"),
             "ОПИСАНИЕ": a.about or "[описание программы — заполнить: что решает, вход, результат]"}   # метки
    subst["ПЕРЕНОСИМОЕ"], subst["СКРИПТЫ"] = index_rows()   # строки указателей для копируемых файлов
    acts = plan(dst, subst, os.path.abspath(a.memory) if a.memory else "")   # что делать
    made, kept = 0, 0                                # создано, оставлено чужих
    for src, out, is_tmpl in acts:                   # действие за действием
        if os.path.exists(out):                      # файл уже есть — не трогаем
            kept += 1; continue
        made += 1
        if a.dry:                                    # только показать
            continue
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if is_tmpl:                                  # шаблон — с подстановкой
            with open(src, encoding="utf-8") as f:
                txt = render(f.read(), subst)        # текст после подстановки
            with open(out, "w", encoding="utf-8", newline="\n") as f:
                f.write(txt)
        else:                                        # остальное — как есть
            shutil.copy2(src, out)
    if not a.dry:                                    # каркас, ссылки, режим по умолчанию, индекс памяти
        for d in EMPTY_DIRS:                         # пустые папки каркаса
            os.makedirs(os.path.join(dst, d), exist_ok=True)
        fixed = sum(unlink_missing(out, dst) for _s, out, t in acts if not t and out.endswith(".md"))   # ссылок в текст
        for _s, out, t in acts:                      # скопированные документы — отметка «из комплекта»
            if not t and out.endswith(".md"):        # документ, а не шаблон
                mark_copied(out, subst["ДАТА"])
        print("ссылок на документы проекта-источника переведено в текст: %d" % fixed)
        import subprocess
        r = subprocess.run([sys.executable, os.path.join("tools", "переносимое", "check_dates.py"), "--write-logs"],
                           cwd=dst, capture_output=True)   # мини-журналы папок
        print("мини-журналы папок: %s" % ("собраны" if r.returncode in (0, 1) else "не собраны — после git init: "
                                          "python tools/переносимое/check_dates.py --write-logs"))
        st = os.path.join(dst, ".claude", "settings.json")   # действующий режим
        if not os.path.exists(st):                   # режима нет — «с подтверждением»
            shutil.copy2(os.path.join(dst, ".claude", "режимы", "с_подтверждением.json"), st)
        if a.memory:                                 # индекс заметок памяти
            idx = os.path.join(a.memory, "MEMORY.md")   # файл индекса
            lines = []                               # строки индекса
            for f in sorted(os.listdir(a.memory)):   # заметка за заметкой
                if f.endswith(".md") and f != "MEMORY.md":   # заметка, не индекс
                    head = open(os.path.join(a.memory, f), encoding="utf-8").read(1500)   # шапка заметки
                    m = re.search(r'description:\s*"?(.+?)"?\s*$', head, re.M)   # описание
                    lines.append("- [%s](%s) — %s" % (f[:-3], f, m.group(1)[:120] if m else ""))
            if not os.path.exists(idx):              # свой индекс не затираем
                with open(idx, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
    print("%s: файлов %s %d, уже были (оставлены) %d → %s" % ("ПРОБА" if a.dry else "ГОТОВО",
          "к созданию" if a.dry else "создано", made, kept, dst))
    if not a.dry:                                    # что дальше
        print("Дальше: git init; заполнить «Что делает программа» в CLAUDE.md; python tools/check_all.py --docs; "
              "план внедрения — теория/переносимое/4_ПЛАН_ВНЕДРЕНИЯ.md")
    return 0


if __name__ == "__main__":                           # запуск скриптом, не импорт
    sys.exit(main())
