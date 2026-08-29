"""Закрытый набор typed-команд над ScenarioFrame.

LLM возвращает только эти команды (в JSON); права менять состояние frame или
исполнять 1С у модели нет — применение команд централизовано в ScenarioManager.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel

# START_MODULE_CONTRACT
#   PURPOSE: Typed-команды LLM над ScenarioFrame (закрытый vocabulary).
#   SCOPE: Command + parse_commands (валидация JSON от LLM).
#   DEPENDS: pydantic
#   LINKS: M-COMMAND-INTERPRETER, M-SCENARIO-MANAGER
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Command - одна typed-команда (kind/path/mention/value/item_ref/focus)
#   CommandKind - закрытый набор допустимых kind
#   parse_commands - JSON от LLM -> валидированный список Command
#   get_allowed_kinds - список допустимых kind
# END_MODULE_MAP

CommandKind = Literal[
    "start_scenario",
    "set_field",
    "clear_field",
    "append_collection_item",
    "select_collection_item",
    "set_collection_field",
    "remove_collection_item",
    "switch_focus",
    "query_scenario",
    "confirm_pending",
    "reject_pending",
    "cancel_scenario",
]


class Command(BaseModel):
    """Одна typed-команда. Поля path/mention/value валидируются менеджером."""

    kind: CommandKind
    scenario_type: str | None = None
    path: str | None = None
    mention: str | None = None
    value: Any = None
    item_ref: str | None = None
    focus: str | None = None
    query: str | None = None
    answer_text: str | None = None

    def describe(self) -> str:
        parts = [self.kind]
        for key in ("scenario_type", "path", "mention", "value", "item_ref", "focus", "query"):
            v = getattr(self, key)
            if v is not None:
                parts.append(f"{key}={v}")
        return " ".join(parts)


def get_allowed_kinds() -> list[str]:
    return list(get_args(CommandKind))


def parse_commands(data: Any) -> list[Command]:
    """JSON от LLM -> список валидированных Command. Некорректные kind отбрасываются."""
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    out: list[Command] = []
    allowed = set(get_allowed_kinds())
    for raw in data:
        if not isinstance(raw, dict):
            continue
        kind = raw.get("kind") or raw.get("action")
        if kind not in allowed:
            continue
        try:
            out.append(
                Command(
                    kind=kind,
                    **{k: v for k, v in raw.items() if k in Command.model_fields and k != "kind"},
                )
            )
        except Exception:
            continue
    return out


# GRACE: стабильный публичный экспорт (для точной проверки поверхности)
__all__ = [
    "Command",
    "CommandKind",
    "get_allowed_kinds",
    "parse_commands",
]
