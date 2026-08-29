# START_MODULE_CONTRACT
#   PURPOSE: Декларативные YAML-определения сценариев + строгая валидация.
#   SCOPE: FieldSpec/CollectionSpec/ScenarioDefinition, загрузка каталога definitions.
#   DEPENDS: pyyaml, pydantic, scenarios.models
#   LINKS: M-SCENARIO-DEFINITIONS, M-SCENARIO-MANAGER
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   FieldSpec - схема поля (kind/entity_type/required/depends_on/invalidates)
#   CollectionSpec - схема табличной части (min_items, поля строки)
#   ExecutionSpec - тип write-действия и фраза подтверждения
#   ScenarioDefinition - полное определение сценария
#   ScenarioRegistry - scenario_type -> определение
#   load_definitions - загрузка и валидация *.yaml каталога
# END_MODULE_MAP

"""Декларативные определения сценариев (YAML) + строгая валидация при загрузке.

Определения хранятся отдельно от orchestration-кода (scenarios/definitions/*.yaml),
чтобы сценарий можно было изменить/расширить без чтения app.py. Некорректная
схема НЕ принимается runtime молча: load_definitions() падает с понятной ошибкой.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from .models import FieldKind


class FieldSpec(BaseModel):
    """Схема одного поля сценария."""

    kind: FieldKind = FieldKind.SCALAR
    entity_type: str | None = None
    required: bool = False
    default: int | float | str | None = None
    min: float | None = None
    confirm_required: bool = False
    resolve: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    invalidates: list[str] = Field(default_factory=list)
    title: str = ""

    @model_validator(mode="after")
    def _entity_needs_type(self) -> FieldSpec:
        if self.kind == FieldKind.ENTITY and not self.entity_type:
            raise ValueError(f"field kind=entity requires entity_type (field title={self.title!r})")
        return self


class CollectionSpec(BaseModel):
    """Схема табличной части (коллекции строк со стабильными item_id)."""

    item_label: str = "строка"
    min_items: int = 0
    fields: dict[str, FieldSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _has_fields(self) -> CollectionSpec:
        if not self.fields:
            raise ValueError("collection must declare fields")
        return self


class ExecutionSpec(BaseModel):
    action_type: str
    confirmation_phrase: str = "Создаём документы?"


class ScenarioDefinition(BaseModel):
    """Полное определение бизнес-сценария."""

    scenario_type: str
    title: str = ""
    fields: dict[str, FieldSpec] = Field(default_factory=dict)
    collections: dict[str, CollectionSpec] = Field(default_factory=dict)
    execution: ExecutionSpec

    @model_validator(mode="after")
    def _meaningful(self) -> ScenarioDefinition:
        if not self.fields and not self.collections:
            raise ValueError(f"scenario {self.scenario_type!r} must declare fields or collections")
        return self

    def required_paths(self) -> list[str]:
        out = [n for n, f in self.fields.items() if f.required]
        for col, spec in self.collections.items():
            for fname, fspec in spec.fields.items():
                if fspec.required:
                    out.append(f"{col}[].{fname}")
        return out


class ScenarioRegistry:
    """scenario_type -> ScenarioDefinition (из YAML)."""

    def __init__(self, definitions: dict[str, ScenarioDefinition]):
        self._definitions = definitions

    def get(self, scenario_type: str) -> ScenarioDefinition:
        if scenario_type not in self._definitions:
            raise KeyError(
                f"unknown scenario_type {scenario_type!r}; known: {sorted(self._definitions)}"
            )
        return self._definitions[scenario_type]

    def types(self) -> list[str]:
        return sorted(self._definitions)


def load_definitions(directory: str | Path) -> ScenarioRegistry:
    """Загрузить все *.yaml из каталога определений с валидацией схем."""
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"scenario definitions directory not found: {directory}")
    definitions: dict[str, ScenarioDefinition] = {}
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name}: definition must be a YAML mapping")
        try:
            defn = ScenarioDefinition.model_validate(raw)
        except Exception as e:
            raise ValueError(f"{path.name}: invalid scenario definition: {e}") from e
        if defn.scenario_type in definitions:
            raise ValueError(f"{path.name}: duplicate scenario_type {defn.scenario_type!r}")
        definitions[defn.scenario_type] = defn
    if not definitions:
        raise ValueError(f"no scenario definitions found in {directory}")
    return ScenarioRegistry(definitions)


# GRACE: стабильный публичный экспорт (для точной проверки поверхности)
__all__ = [
    "CollectionSpec",
    "ExecutionSpec",
    "FieldSpec",
    "ScenarioDefinition",
    "ScenarioRegistry",
    "load_definitions",
]
