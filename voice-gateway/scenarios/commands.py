"""Закрытый набор typed-команд над ScenarioFrame.

LLM возвращает только эти команды (в JSON); права менять состояние frame или
исполнять 1С у модели нет — применение команд централизовано в ScenarioManager.
LLM оперирует упоминаниями (mention) и ссылками на строки (item_ref/порядок),
но НЕ EntityRef: идентичность объекта всегда получает EntityResolver из 1С.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel

# START_MODULE_CONTRACT
#   PURPOSE: Typed-команды LLM над ScenarioFrame (закрытый vocabulary).
#   SCOPE: Command + parse_commands (валидация JSON от LLM) + batch-хелперы.
#   DEPENDS: pydantic
#   LINKS: M-COMMAND-INTERPRETER, M-SCENARIO-MANAGER
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Command - одна typed-команда (kind/path/collection/field/mention/value/item_ref)
#   CommandKind - закрытый набор допустимых kind
#   MUTATING_KINDS - команды, мутирующие frame (инвалидируют PendingAction)
#   NON_MUTATING_KINDS - немутирующие команды
#   CONTRADICTORY_WITH_CONFIRM - команды, недопустимые в одном batch с confirm
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
    "propose_pending",
    "confirm_pending",
    "reject_pending",
    "cancel_scenario",
    "chitchat",
    "clarify",
]


class Command(BaseModel):
    """Одна typed-команда.

    Поля:
    - path         — путь к полю frame ('vehicle' или 'items[].quantity')
    - collection   — имя коллекции (для строковых операций)
    - field        — имя поля строки (для set_collection_field)
    - item_ref     — ссылка на строку: '1'/'2'/..., 'first'/'last', 'this',
                     'prev'/'previous', 'new' (только что добавленная) или item_id
    - mention      — сырое пользовательское упоминание (для entity-полей);
                     LLM НЕ передаёт EntityRef — его даёт только EntityResolver
    - value        — скалярное значение (quantity и т.п.)
    - focus        — новый focus ('vehicle', 'items[...]') либо 'back'
    - scenario_type— для start_scenario ('repair_order', 'stock_query')
    - query        — текст поискового запроса (query_scenario)
    - answer_text  — текст ответа для chitchat/clarify (подсказка оркестратору)
    """

    kind: CommandKind
    scenario_type: str | None = None
    path: str | None = None
    collection: str | None = None
    field: str | None = None
    item_ref: str | None = None
    mention: str | None = None
    value: Any = None
    focus: str | None = None
    query: str | None = None
    answer_text: str | None = None

    def describe(self) -> str:
        parts = [self.kind]
        for key in (
            "scenario_type",
            "path",
            "collection",
            "field",
            "mention",
            "value",
            "item_ref",
            "focus",
            "query",
        ):
            v = getattr(self, key)
            if v is not None:
                parts.append(f"{key}={v}")
        return " ".join(parts)


# команды-мутации frame (влияют на версию и инвалидируют PendingAction)
MUTATING_KINDS = frozenset(
    {
        "set_field",
        "clear_field",
        "append_collection_item",
        "set_collection_field",
        "remove_collection_item",
        "cancel_scenario",
    }
)
# немутации: выбор строки/focus/чтение/подтверждения/разговор
NON_MUTATING_KINDS = frozenset(
    {
        "start_scenario",
        "select_collection_item",
        "switch_focus",
        "query_scenario",
        "propose_pending",
        "confirm_pending",
        "reject_pending",
        "chitchat",
        "clarify",
    }
)
# propose+confirm в одном batch запрещены: подтверждение обязано быть
# отдельным ходом пользователя (execution gate)
CONTRADICTORY_WITH_CONFIRM = frozenset(MUTATING_KINDS | {"propose_pending"})


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
        kind = raw.get("kind") or raw.get("type") or raw.get("action")
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
    "CONTRADICTORY_WITH_CONFIRM",
    "MUTATING_KINDS",
    "NON_MUTATING_KINDS",
    "Command",
    "CommandKind",
    "get_allowed_kinds",
    "parse_commands",
]
