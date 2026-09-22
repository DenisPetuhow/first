# -*- coding: utf-8 -*-
"""ХУК: не давать выполнять команду с приставкой `cd "путь" &&` или `VAR=… cmd`.

Зачем. Разрешения сверяются с НАЧАЛОМ строки команды, и такая приставка сбрасывает их:
правило `Bash(git status:*)` не срабатывает на `cd "…" && git status`, и запрос всплывает
на ровном месте. Замер 22.09.2026 (`/doctor`): 58 отказов из 130 за 23 дня — все на `cd`.
Рабочая директория и так корень проекта, приставка не нужна никогда.

Ставится хуком `PreToolUse` на Bash и PowerShell. Вход: JSON на stdin (как у всех хуков).
Отдаёт: пусто — команда идёт дальше; либо JSON с `deny` и пояснением.

⚠️ ЛЮБАЯ СВОЯ ОШИБКА = ПРОПУСТИТЬ КОМАНДУ. Хук висит на каждом вызове Bash, и падение
здесь остановило бы работу целиком — поэтому весь разбор в try, а в except — тихий выход.
"""
import json
import re
import sys

if hasattr(sys.stdout, "reconfigure"):     # консоль Windows отдаёт cp1251 и портит пояснение
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# приставки, которые сбивают сверку разрешений: смена папки и переменная окружения
BAD = (
    (re.compile(r"^\s*cd\s", re.I), "Приставка `cd` в начале команды"),
    (re.compile(r"^\s*Set-Location\s", re.I), "Приставка `Set-Location` в начале команды"),
    (re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*=\S*\s+\S", re.I), "Переменная окружения перед командой"),
    (re.compile(r"^\s*\$env:[A-Za-z_]", re.I), "Переменная окружения перед командой"),
)

REASON = (      # текст отказа: подставляется название приставки из BAD
    "%s — уберите её: разрешения сверяются с НАЧАЛОМ строки, и приставка их сбрасывает, "
    "запрос всплывает на ровном месте (правило CLAUDE.md, замер /doctor 22.09.2026: "
    "58 отказов из 130). Рабочая директория и так корень проекта. "
    "Нужна другая папка — передайте её аргументом команды (например `git -C <путь> …`)."
)


def main():
    """Прочитать вызов и решить, пропускать ли. Отдаёт: None (печатает JSON при отказе)."""
    try:
        data = json.load(sys.stdin)
        cmd = ((data.get("tool_input") or {}).get("command") or "")
        if not isinstance(cmd, str):
            return
        for rx, what in BAD:
            if rx.search(cmd):
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": REASON % what}}, ensure_ascii=False))
                return
    except Exception:       # разбор не удался — не наше дело решать
        return              # — пропускаем команду молча


if __name__ == "__main__":
    main()
