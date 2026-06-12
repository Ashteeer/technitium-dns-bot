"""Вспомогательная утилита для INSTALL.sh.

Подкоманды:
  init <example> <target>   создать config.yaml из примера, подставив значения
                            из переменных окружения TTBOT_ADMIN_ID / TTBOT_BOT_TOKEN
  show <path>               напечатать сводку (Bot ID / Admin ID / Config Path)
"""

from __future__ import annotations

import argparse
import os
import sys

import yaml


def _bot_id(token: str) -> str:
    prefix = token.split(":", 1)[0]
    return prefix if prefix.isdigit() else "не задан"


def cmd_init(example: str, target: str) -> int:
    with open(example, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    tg = data.setdefault("telegram", {})

    admin = os.environ.get("TTBOT_ADMIN_ID", "").strip()
    if admin:
        try:
            tg["whitelist"] = [int(admin)]
        except ValueError:
            print(
                f"! Некорректный admin ID '{admin}' — whitelist оставлен пустым",
                file=sys.stderr,
            )
            tg["whitelist"] = []
    else:
        tg["whitelist"] = []

    token = os.environ.get("TTBOT_BOT_TOKEN", "").strip()
    if token:
        tg["bot_token"] = token

    with open(target, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return 0


def cmd_show(path: str) -> int:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    tg = cfg.get("telegram", {}) or {}
    token = str(tg.get("bot_token", "") or "")
    wl = tg.get("whitelist") or []
    admin = ", ".join(str(x) for x in wl) if wl else "не задан"
    print("===============================")
    print(f"Bot ID: {_bot_id(token)}")
    print(f"Admin ID: {admin}")
    print(f"Config Path: {path}")
    print("===============================")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="config tool for INSTALL.sh")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init")
    p_init.add_argument("example")
    p_init.add_argument("target")
    p_show = sub.add_parser("show")
    p_show.add_argument("path")
    args = parser.parse_args(argv)

    if args.cmd == "init":
        return cmd_init(args.example, args.target)
    return cmd_show(args.path)


if __name__ == "__main__":
    sys.exit(main())
