# START_MODULE_CONTRACT
#   PURPOSE: Строгое разрешение ссылочных полей: mention -> 1C -> EntityRef.
#   SCOPE: EntityResolver (lookup-инъекции, кандидаты, выбор, not_found/ambiguous).
#   DEPENDS: pydantic, scenarios.models
#   LINKS: M-ENTITY-RESOLVER, M-1C-ADAPTER
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ResolveOutcome - результат попытки разрешения (статус + кандидаты)
#   EntityResolver - реестр lookup-функций; resolve_mention/select_candidate
# END_MODULE_MAP

"""EntityResolver: mention -> 1C lookup -> точный EntityRef.

Строгое правило: поле заполняется (resolved) только идентичностью из 1С
(код/ссылка). 0 кандидатов -> not_found; >1 -> ambiguous (ждём выбора);
имя пользователя хранится как user_mention и никогда не подменяет ref.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, Field

from .models import EntityRef, ScenarioField

# entity_type -> lookup(mention) -> dict(found, entities=[{name, code?, article?}...], message)
LookupFn = Callable[[str], dict]


class ResolveOutcome(BaseModel):
    """Результат попытки разрешения mention (для формулировки ответа)."""

    status: str
    candidates: list[EntityRef] = Field(default_factory=list)
    message: str = ""


class EntityResolver:
    """Инъекция lookup-функций по типам сущностей (адаптер 1С/mock)."""

    def __init__(self, lookups: dict[str, LookupFn]):
        self._lookups = lookups

    def register(self, entity_type: str, fn: LookupFn) -> None:
        self._lookups[entity_type] = fn

    def lookup(self, entity_type: str, mention: str) -> list[EntityRef]:
        fn = self._lookups.get(entity_type)
        if fn is None:
            return []
        data = fn(mention) or {}
        out: list[EntityRef] = []
        for raw in data.get("entities", []) or []:
            ref = EntityRef(
                entity_type=entity_type,
                name=str(raw.get("name", "")).strip(),
                code=(str(raw["code"]).strip() if raw.get("code") not in (None, "") else None),
                metadata={k: v for k, v in raw.items() if k not in ("name", "code")},
            )
            if ref.name:
                out.append(ref)
        return out

    def resolve_mention(self, field: ScenarioField, mention: str) -> ResolveOutcome:
        """Установить user_mention и получить кандидатов. Статус по числу кандидатов."""
        field.set_mention(mention)
        candidates = self.lookup(field.entity_type or "", mention)
        if not candidates:
            field.set_candidates([])
            return ResolveOutcome(status="not_found", message="не найдено в 1С")
        field.set_candidates(candidates)
        if len(candidates) == 1 and not field.confirm_required:
            # единственный кандидат БЕЗ требования явного подтверждения:
            # resolved только при наличии идентичности
            if candidates[0].ref or candidates[0].code:
                field.resolve(candidates[0])
                return ResolveOutcome(status="resolved", candidates=candidates)
            return ResolveOutcome(status="ambiguous", candidates=candidates)
        return ResolveOutcome(
            status="ambiguous" if candidates else "not_found",
            candidates=candidates,
            message="уточните, какая именно",
        )

    @staticmethod
    def select_candidate(field: ScenarioField, index: int) -> EntityRef | None:
        """Выбор кандидата пользователем; resolved только при идентичности."""
        if not (0 <= index < len(field.candidates)):
            return None
        ref = field.candidates[index]
        if not (ref.ref or ref.code or ref.metadata.get("allow_name_identity")):
            return None
        field.resolve(ref)
        return ref


# GRACE: стабильный публичный экспорт (для точной проверки поверхности)
__all__ = [
    "EntityResolver",
    "ResolveOutcome",
]
