# START_MODULE_CONTRACT
#   PURPOSE: Компактная проекция ScenarioFrame для маленького context window LLM.
#   SCOPE: Текстовая проекция полей/коллекций/focus/pending без истории чата.
#   DEPENDS: scenarios.models, scenarios.registry
#   LINKS: M-SCENARIO-MANAGER, M-COMMAND-INTERPRETER
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   frame_projection - компактный текст состояния frame для промпта LLM
# END_MODULE_MAP

"""Компактная проекция ScenarioFrame для маленького context window LLM.

LLM получает только факты frame (статусы, значения, focus, unresolved) —
не всю историю разговора. История чата остаётся разговорным контекстом,
но не источником бизнес-состояния.
"""

from __future__ import annotations

from .models import FieldKind, ScenarioField, ScenarioFrame
from .registry import ScenarioDefinition

_STATUS_RU = {
    "missing": "missing",
    "resolving": "resolving",
    "ambiguous": "ambiguous",
    "resolved": "resolved",
    "not_found": "not_found",
    "invalid": "invalid",
    "filled": "resolved",
}


def _field_lines(path: str, f: ScenarioField, out: list[str]) -> None:
    if f.kind == FieldKind.CALCULATED:
        if f.value is not None:
            out.append(f"{path}: {f.value}")
        return
    if f.kind == FieldKind.ENTITY:
        status = _STATUS_RU.get(f.status, f.status)
        if f.status == "resolved" and f.entity is not None:
            ident = f.entity.code or f.entity.ref or ""
            art = f.entity.metadata.get("article", "")
            extra = f" [{art}]" if art else ""
            code = f" ref={ident}" if ident else ""
            out.append(f"{path}: {status} {f.entity.name}{extra}{code}")
        elif f.status == "ambiguous":
            names = "; ".join(f"{c.name}" for c in f.candidates[:4])
            out.append(f"{path}: ambiguous [{names}]")
        elif f.user_mention:
            out.append(f"{path}: {status} (mention: {f.user_mention})")
        else:
            out.append(f"{path}: {status}")
        return
    # scalar
    if f.value is not None:
        out.append(f"{path}: {f.value}")
    else:
        out.append(f"{path}: missing")


def frame_projection(defn: ScenarioDefinition, frame: ScenarioFrame) -> str:
    out = [
        f"Сценарий: {frame.title or defn.title} ({frame.scenario_type}) статус={frame.status} v={frame.version}"
    ]
    for name, f in frame.fields.items():
        _field_lines(name, f, out)
    for col_name, items in frame.collections.items():
        if not items:
            out.append(f"{col_name}: пусто")
            continue
        out.append(f"{col_name}: {len(items)} строк(и)")
        for i, item in enumerate(items, 1):
            out.append(f"  строка {i} ({item.item_id}):")
            for fname, f in item.fields.items():
                sub: list[str] = []
                _field_lines(fname, f, sub)
                out.extend(f"    {line}" for line in sub)
    if frame.focus is not None and frame.focus.path:
        out.append(f"focus: {frame.focus.path}")
    if frame.pending_action is not None:
        out.append(
            f"pending_action: {frame.pending_action.type} (v={frame.pending_action.frame_version})"
        )
    unresolved = frame.unresolved_required()
    if unresolved:
        out.append(f"required unresolved: {', '.join(unresolved)}")
    return "\n".join(out)


# GRACE: стабильный публичный экспорт (для точной проверки поверхности)
__all__ = [
    "frame_projection",
]
