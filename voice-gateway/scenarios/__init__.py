"""Сценарная подсистема: персистентные ScenarioFrame вместо stage-driven FSM.

Модули:
- models      — ScenarioFrame/ScenarioField/EntityRef/PendingAction/ScenarioSession
- commands    — закрытый набор typed-команд LLM
- registry    — YAML-определения сценариев + строгая валидация
- manager     — централизованное применение команд, focus, инвалидация
- resolver    — mention -> 1C -> точный EntityRef
- projection  — компактная проекция frame для маленького context window
- execution   — execution gate для write-эффектов
"""

from .commands import Command, get_allowed_kinds, parse_commands
from .execution import build_repair_payload, execution_guard
from .manager import CommandResult, ScenarioManager
from .models import (
    CollectionItem,
    EntityRef,
    Focus,
    PendingAction,
    ScenarioField,
    ScenarioFrame,
    ScenarioSession,
)
from .projection import frame_projection
from .registry import ScenarioDefinition, ScenarioRegistry, load_definitions
from .resolver import EntityResolver, ResolveOutcome

__all__ = [
    "CollectionItem",
    "Command",
    "CommandResult",
    "EntityRef",
    "EntityResolver",
    "Focus",
    "PendingAction",
    "ResolveOutcome",
    "ScenarioDefinition",
    "ScenarioField",
    "ScenarioFrame",
    "ScenarioManager",
    "ScenarioRegistry",
    "ScenarioSession",
    "build_repair_payload",
    "execution_guard",
    "frame_projection",
    "get_allowed_kinds",
    "load_definitions",
    "parse_commands",
]
