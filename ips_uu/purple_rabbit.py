"""PurpleRabbit resource analysis helpers for iPS-UU."""

from __future__ import annotations

import argparse
import csv
import json
import plistlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .banner import print_intro

DEFAULT_CONTENTS_PR = Path("ContentsPR")


class PurpleRabbitError(RuntimeError):
    pass


@dataclass(frozen=True)
class TranslationRule:
    input_name: str
    condition: str
    input_string: str
    action: str
    output_string: str

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "TranslationRule":
        return cls(
            input_name=row.get("Input", ""),
            condition=row.get("Condition", ""),
            input_string=row.get("InputString", ""),
            action=row.get("Action", ""),
            output_string=row.get("OutputString", ""),
        )

    def matches(self, message: str) -> bool:
        if self.condition.lower() == "contains":
            return self.input_string in message
        if self.condition.lower() == "equals":
            return self.input_string == message
        return False


def contents_pr_path(value: str | None) -> Path:
    return Path(value) if value else DEFAULT_CONTENTS_PR


def resource_path(contents_pr: Path, name: str) -> Path:
    return contents_pr / "Resources" / name


def load_config(contents_pr: Path) -> dict[str, Any]:
    path = resource_path(contents_pr, "Config.plist")
    try:
        with path.open("rb") as f:
            value = plistlib.load(f)
    except FileNotFoundError as exc:
        raise PurpleRabbitError(f"PurpleRabbit Config.plist not found at {path}") from exc
    if not isinstance(value, dict):
        raise PurpleRabbitError(f"Expected plist dictionary in {path}")
    return value


def restore_settings_template(config: dict[str, Any]) -> dict[str, Any]:
    module_map = config.get("ModuleMap") or {}
    restore_manager = module_map.get("RestoreManager") or {}
    settings = restore_manager.get("SettingsTemplate") or {}
    if not isinstance(settings, dict):
        raise PurpleRabbitError("RestoreManager SettingsTemplate is not a dictionary")
    return settings


def command_summary(config: dict[str, Any]) -> list[dict[str, Any]]:
    command_map = config.get("CommandMap") or {}
    if not isinstance(command_map, dict):
        return []
    rows: list[dict[str, Any]] = []
    for name, entry in sorted(command_map.items()):
        if not isinstance(entry, dict):
            continue
        command = ((entry.get("CommandTemplate") or {}).get("Command") or {})
        task = entry.get("TaskListConfig") or {}
        rows.append(
            {
                "name": name,
                "module": entry.get("ModuleName"),
                "command_name": command.get("CommandName"),
                "command_option": command.get("CommandOption"),
                "description": task.get("Description"),
                "verbose_name": task.get("VerboseName"),
            }
        )
    return rows


def load_translation_rules(contents_pr: Path) -> list[TranslationRule]:
    path = resource_path(contents_pr, "TranslationTable.csv")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise PurpleRabbitError(f"PurpleRabbit TranslationTable.csv not found at {path}") from exc
    return [TranslationRule.from_row(row) for row in csv.DictReader(text.splitlines())]


def translate_message(message: str, rules: list[TranslationRule]) -> list[dict[str, str]]:
    matches = []
    for rule in rules:
        if rule.matches(message):
            matches.append(
                {
                    "input": rule.input_name,
                    "condition": rule.condition,
                    "matched": rule.input_string,
                    "action": rule.action,
                    "output": rule.output_string,
                }
            )
    return matches


def analyze_command(args: argparse.Namespace) -> int:
    contents_pr = contents_pr_path(args.contents_pr)
    config = load_config(contents_pr)
    payload = {
        "contents_pr": str(contents_pr),
        "restore_settings_template": restore_settings_template(config),
        "restore_related_commands": [
            row
            for row in command_summary(config)
            if any(
                token in str(row).lower()
                for token in ("restore", "recovery", "dfu", "nvram", "bundle")
            )
        ],
        "translation_rules": [rule.__dict__ for rule in load_translation_rules(contents_pr)],
        "notes": [
            "PurpleRabbit imports MobileDevice restore APIs, but this module only imports resource/config logic.",
            "No unsigned restore, nonce manipulation, or signing bypass behavior is implemented.",
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def translate_command(args: argparse.Namespace) -> int:
    rules = load_translation_rules(contents_pr_path(args.contents_pr))
    matches = translate_message(args.message, rules)
    print(json.dumps({"message": args.message, "matches": matches}, indent=2, sort_keys=True))
    return 0 if matches else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze PurpleRabbit restore resources")
    parser.add_argument("--contents-pr", help="Path to ContentsPR; defaults to ./ContentsPR")
    subcommands = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subcommands.add_parser("analyze", help="Print restore-relevant PurpleRabbit config")
    analyze_parser.set_defaults(func=analyze_command)

    translate_parser = subcommands.add_parser("translate-error", help="Translate a restore log/error message")
    translate_parser.add_argument("message", help="Restore log or error message")
    translate_parser.set_defaults(func=translate_command)
    return parser


def main(argv: list[str] | None = None, show_intro: bool = False) -> int:
    if show_intro:
        print_intro()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PurpleRabbitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(show_intro=True))
