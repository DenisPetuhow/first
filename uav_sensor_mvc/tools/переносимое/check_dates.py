# -*- coding: utf-8 -*-
"""
ШАГ 8 СВЕРКИ · ОТМЕТКИ О ПРАВКЕ: шапка документа против git, мини-журнал папки.

Ловит класс ошибок, который не видит ни одна другая проверка: **файл правили, а записать
забыли**. Имена в нём живые, числа верные, ссылки целы — врёт только отметка о том, когда
и что менялось. Так 11.09.2026 обнаружилось, что §5.3 методички маршрутов полгода
описывала ЗАПАСНОЙ путь расстановки как основной: по документу этого было не видно
никак, потому что дата правки жила только в git.

    python tools/переносимое/check_dates.py            # отчёт, код 1 при расхождениях
    python tools/переносимое/check_dates.py --list      # заодно перечислить все отметки
    python tools/переносимое/check_dates.py --write-logs # пересобрать ЖУРНАЛ_ПАПКИ.md

⚠️ МИНИ-ЖУРНАЛ ПАПКИ — СГЕНЕРИРОВАННЫЙ ФАЙЛ, руками не править. Сперва его хотели вести
вручную, и это было бы ХУЖЕ, чем ничего: данные для него целиком лежат в git и в шапках
документов, то есть рукописный файл — копия уже имеющегося. Копия, которую обновляют
руками, устаревает первой — ровно так 11.09.2026 соврали указатель журнала (обещал 245
пунктов), план 7 (числил сделанное открытым) и §5.3 методички маршрутов. Поэтому файл
ПЕРЕСОБИРАЕТСЯ: `--write-logs` берёт даты из git и отметки «Правился» из шапок.

ЧТО СВЕРЯЕТСЯ, три разных вопроса:

  1. ОТМЕТКА ЕСТЬ?      у каждого живого документа в шапке строка «> **Правился:** ДАТА — …»;
  2. ОТМЕТКА СВЕЖАЯ?    её дата не старше даты последнего коммита файла. Старше — значит
                        файл правили, а шапку не тронули;
  3. ПАПКА ЗНАЕТ?       правка отражена в `ЖУРНАЛ_ПАПКИ.md` своей папки.

⚠️ ДАТА В ШАПКЕ НЕ ЗАМЕНЯЕТ GIT, А ДОПОЛНЯЕТ ЕГО. Её правят руками, поэтому врать она
будет чаще. Полезен именно РАЗРЫВ между ней и git: он и означает «правку внесли, а что
изменилось — не записали».

⚠️ ГДЕ ОТМЕТКИ НЕ ПРОВЕРЯЮТСЯ (сама отметка там безвредна и может стоять):
  * `карта_кода/` — СГЕНЕРИРОВАНО: дата означала бы «когда запускали скрипт». Здесь
    отметку не ставят вовсе — пересборка её затрёт;
  * `журнал/` — журнал сам себе хроника: требовать от него вторую отметку бессмысленно;
  * `архив/` — закрытые документы не правятся по определению, отставать им не от чего.

⚠️ МИНИ-ЖУРНАЛЫ ПАПОК заводятся ТОЛЬКО вместе с этой проверкой. Без неё они протухают:
общий журнал ведётся давно, и правило «раз в 5 пунктов обновить указатель» всё равно
отстаёт — тринадцать журналов будут отставать охотнее одного (замер 11.09.2026:
указатель обещал 245 пунктов и врал про длину части 5).
"""
import io
import os
import re
import subprocess
import sys

MARK_RE = re.compile(r">\s*\*\*Правился:\*\*\s*(\d{2}\.\d{2}\.\d{4})")
FOLDER_LOG = "ЖУРНАЛ_ПАПКИ.md"
# ⚠️ ИМЯ МОЖЕТ БЫТЬ С НОМЕРОМ ВПЕРЕДИ (`0.ЖУРНАЛ_ПАПКИ.md`): заказчик нумерует файлы, чтобы
# задать порядок в проводнике (13.09.2026, папка `код/`). Без этого генератор создал бы
# второй журнал под старым именем, а нумерованный счёл бы документом без отметки.
FOLDER_LOG_RE = re.compile(r"^(?:\d+[._])?ЖУРНАЛ_ПАПКИ\.md$")


def folder_log_name(folder):
    # Имя мини-журнала в папке: уже лежащий (в том числе с номером) либо стандартное.
    # Принимает: папку относительно корня. Отдаёт: имя файла.
    try:
        for f in os.listdir(os.path.join(ROOT, folder)):
            if FOLDER_LOG_RE.match(f):                 # журнал уже есть, возможно с номером
                return f                               # пишем в него же
    except OSError:                                    # папки нет
        pass
    return FOLDER_LOG

# папки, где отметка не нужна (см. шапку)
SKIP_DIRS = {"карта_кода", "журнал", "архив"}
# папки, где мини-журнал НЕ заводится: `переносимое` — решение заказчика 11.09.2026
# (правила общие для любого проекта, история у них общая), но отметка в файлах обязательна
NO_FOLDER_LOG = {"переносимое", "основа"}


def _find_root(markers=("CLAUDE.md", "config.py")):
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        if any(os.path.exists(os.path.join(d, m)) for m in markers):
            return d
        up = os.path.dirname(d)
        if up == d:
            return os.path.abspath(".")
        d = up


ROOT = _find_root()


def git_date(rel):
    """Дата последнего коммита файла, ДД.ММ.ГГГГ. Пусто — файл ещё не в истории."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ad", "--date=format:%d.%m.%Y", "--", rel],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        return (out.stdout or "").strip()
    except Exception:
        return ""


def _key(d):
    """ДД.ММ.ГГГГ -> сравнимый ключ."""
    p = d.split(".")
    return (p[2], p[1], p[0]) if len(p) == 3 else ("", "", "")


def docs():
    out = []
    for dp, dn, fn in os.walk(os.path.join(ROOT, "теория")):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f.endswith(".md") and not FOLDER_LOG_RE.match(f):
                out.append(os.path.relpath(os.path.join(dp, f), ROOT).replace("\\", "/"))
    return sorted(out)


GEN_HEAD = ("<!-- ЖУРНАЛ ПАПКИ · сгенерировано tools/переносимое/check_dates.py "
            "— руками не править -->")


def git_rows(folder, limit=14):
    """Последние правки папки: (дата, файл, сообщение коммита)."""
    out = subprocess.run(
        ["git", "log", "-n", str(limit), "--date=format:%d.%m.%Y",
         "--pretty=format:%ad\t%s", "--name-only", "--", folder],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    rows, date, subj, seen = [], "", "", set()
    for line in (out.stdout or "").split("\n"):
        if "\t" in line:
            date, subj = line.split("\t", 1)
        elif line.strip().endswith(".md"):
            name = os.path.basename(line.strip())
            if FOLDER_LOG_RE.match(name) or (date, name) in seen:
                continue
            seen.add((date, name))
            rows.append((date, name, subj))
    return rows[:20]


def write_folder_logs():
    """Пересобрать ЖУРНАЛ_ПАПКИ.md во всех живых папках теории."""
    folders = sorted({os.path.dirname(r) for r in docs()})
    made = 0
    for folder in folders:
        base = os.path.basename(folder)
        if base in NO_FOLDER_LOG or folder == "":
            continue
        rows = git_rows(folder)
        marks = []
        for rel in docs():
            if os.path.dirname(rel) != folder:
                continue
            text = io.open(os.path.join(ROOT, rel), encoding="utf-8").read()[:2000]
            m = MARK_RE.search(text)
            note = text[m.end():].split("\n")[0].strip(" —") if m else ""
            marks.append((os.path.basename(rel), m.group(1) if m else "—", note[:90]))
        marks.sort(key=lambda t: _key(t[1]), reverse=True)

        # ⚠️ путь до журнала проекта зависит от глубины: из `теория/` это `журнал/…`,
        # из `теория/карты/` — `../журнал/…`. Жёсткая строка давала битую ссылку в
        # корневой папке (поймано docmap 12.09.2026).
        up = "../" * (folder.replace("\\", "/").count("/"))
        jrn = up + "журнал/README.md"

        body = [GEN_HEAD, "", "# Журнал папки — `%s`" % (base or "теория"), "",
                "> **Что это.** Когда какой документ этой папки правили и что в нём",
                "> отмечено. Нужен для навигации: видно, что трогали последним.",
                ">",
                "> ⚠️ **СГЕНЕРИРОВАНО**, руками не править — пересобрать:",
                "> `python tools/переносимое/check_dates.py --write-logs`.",
                "> Источники: отметки «Правился» в шапках документов и история git.",
                ">",
                "> ⚠️ **Не замена [журналу проекта](%s):** там «почему" % jrn,
                "> меняли», с числами и разбором; здесь — «что в этой папке трогали».",
                "", "## Отметки в документах", "",
                "| Документ | Правился | Что отмечено |", "|---|---|---|"]
        for name, d, note in marks:
            body.append("| `%s` | %s | %s |" % (name, d, note or "—"))
        body += ["", "## Последние правки по git", "",
                 "| Дата | Файл | Коммит |", "|---|---|---|"]
        if rows:
            for d, f, s in rows:
                body.append("| %s | `%s` | %s |" % (d, f, s.replace("|", "/")[:100]))
        else:
            body.append("| — | — | правок в истории нет (папка новая) |")
        body.append("")
        io.open(os.path.join(ROOT, folder, folder_log_name(folder)), "w",
                encoding="utf-8").write("\n".join(body))
        made += 1
    print("пересобрано мини-журналов папок: %d" % made)
    return made


def main():
    if "--write-logs" in sys.argv:
        write_folder_logs()
    show = "--list" in sys.argv
    no_mark, stale, no_log = [], [], []
    folder_logs = {}

    for rel in docs():
        path = os.path.join(ROOT, rel)
        text = io.open(path, encoding="utf-8").read()
        m = MARK_RE.search(text[:2000])          # отметка живёт в шапке, не глубже
        gd = git_date(rel)
        if not m:
            no_mark.append(rel)
            continue
        mark = m.group(1)
        if show:
            print("  %-58s шапка %s   git %s" % (rel, mark, gd or "—"))
        if gd and _key(mark) < _key(gd):
            stale.append((rel, mark, gd))
        # знает ли папка о правке
        folder = os.path.dirname(rel)
        if os.path.basename(folder) in NO_FOLDER_LOG:
            continue
        if folder not in folder_logs:
            lp = os.path.join(ROOT, folder, folder_log_name(folder))
            folder_logs[folder] = (io.open(lp, encoding="utf-8").read()
                                   if os.path.exists(lp) else None)
        log = folder_logs[folder]
        if log is None:
            continue                              # нет мини-журнала — отдельная строка ниже
        name = os.path.basename(rel)
        if name not in log:
            no_log.append((rel, folder))

    print()
    if no_mark:
        print("НЕТ ОТМЕТКИ «Правился» (%d):" % len(no_mark))
        for r in no_mark:
            print("   ", r)
    if stale:
        print("ШАПКА ОТСТАЛА ОТ GIT (%d) — файл правили, а что изменилось, не записали:"
              % len(stale))
        for r, mark, gd in stale:
            print("    %-58s шапка %s < git %s" % (r, mark, gd))
    missing_logs = [f for f, v in folder_logs.items() if v is None]
    if missing_logs:
        print("ПАПКА БЕЗ %s (%d):" % (FOLDER_LOG, len(missing_logs)))
        for f in missing_logs:
            print("   ", f)
    if no_log:
        print("ФАЙЛ НЕ УПОМЯНУТ В МИНИ-ЖУРНАЛЕ ПАПКИ (%d):" % len(no_log))
        for r, f in no_log:
            print("    %-58s -> %s/%s" % (r, f, FOLDER_LOG))

    total = len(no_mark) + len(stale) + len(missing_logs) + len(no_log)
    print("\nпроверено документов: %d; расхождений: %d" % (len(docs()), total))
    if total:
        print("(каждое — повод ДОПИСАТЬ отметку, а не переписать дату: разрыв между "
              "шапкой и git и есть полезный сигнал)")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
