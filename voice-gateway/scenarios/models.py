"""ScenarioFrame models: persistent business-scenario state on the backend.

Источник бизнес-состояния — ScenarioFrame (поля, коллекции, зависимости,
pending-действия). История чата и LLM не являются владельцами состояния.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

# START_MODULE_CONTRACT
#   PURPOSE: Модели персистентного бизнес-состояния сценариев (ScenarioFrame).
#   SCOPE: ScenarioFrame/Field/CollectionItem, EntityRef, PendingAction, Focus, Session.
#   DEPENDS: pydantic
#   LINKS: M-SCENARIO-MANAGER, M-ENTITY-RESOLVER
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ENTITY_STATUSES - статусы ссылочного поля
#   SCALAR_STATUSES - статусы скалярного поля
#   FieldKind - вид поля (entity|scalar|calculated)
#   EntityRef - точная идентичность объекта 1С (ref/код; имя - не ссылка)
#   ScenarioField - поле frame: упоминание, статус, значение, кандидаты
#   CollectionItem - строка табличной части со стабильным item_id
#   PendingAction - отложенное write-действие, привязанное к версии frame
#   Focus - указатель на редактируемую часть frame (не этап FSM)
#   ScenarioFrame - персистентная структура бизнес-сценария
#   ScenarioSession - сессия чата: несколько frame + активный
#   now - текущее время (для отметок обновления)
#   new_item_id - генерация стабильного идентификатора строки
# END_MODULE_MAP

# --- статусы ссылочного поля (entity) ---------------------------------------

ENTITY_STATUSES = ("missing", "resolving", "ambiguous", "resolved", "not_found", "invalid")
SCALAR_STATUSES = ("missing", "filled", "invalid")


class FieldKind(StrEnum):
    ENTITY = "entity"
    SCALAR = "scalar"
    CALCULATED = "calculated"


class EntityRef(BaseModel):
    """Точная идентичность объекта 1С. Имя/артикул — НЕ ref.

    ref — стабильный идентификатор, полученный из 1С (код/ссылка).
    Для складов данной конфигурации (Справочник.Склады без Код) единственная
    доступная идентичность — точное Наименование (см. docs/1C_METADATA.md).
    """

    entity_type: str
    ref: str | None = None
    name: str = ""
    code: str | None = None
    metadata: dict = Field(default_factory=dict)

    @property
    def identity(self) -> str:
        """Стабильная строка-идентичность для логов и сравнения."""
        return self.ref or self.code or self.name


def now() -> datetime:
    return datetime.now()


class ScenarioField(BaseModel):
    """Поле frame: ссылочное (entity) либо скалярное."""

    name: str
    kind: FieldKind = FieldKind.SCALAR
    entity_type: str | None = None
    required: bool = False
    status: str = "missing"

    user_mention: str | None = None
    value: str | int | float | None = None
    entity: EntityRef | None = None
    candidates: list[EntityRef] = Field(default_factory=list)

    depends_on: list[str] = Field(default_factory=list)
    invalidates: list[str] = Field(default_factory=list)
    confirm_required: bool = False

    updated_at: datetime | None = None

    @property
    def filled(self) -> bool:
        if self.kind == FieldKind.ENTITY:
            return (
                self.status == "resolved"
                and self.entity is not None
                and bool(self.entity.ref or self.entity.code or self.entity.name)
            )
        if self.kind == FieldKind.CALCULATED:
            return self.value is not None
        return self.status == "filled" and self.value is not None

    def set_mention(self, mention: str) -> None:
        self.user_mention = mention
        self.status = "resolving"
        self.candidates = []
        self.entity = None
        self.updated_at = now()

    def set_candidates(self, candidates: list[EntityRef]) -> None:
        self.candidates = candidates
        self.status = "ambiguous" if candidates else "not_found"
        self.updated_at = now()

    def resolve(self, ref: EntityRef) -> None:
        """Поле заполнено только точной идентичностью из 1С."""
        if not (ref.ref or ref.code or (ref.name and self.entity_type == "warehouse")):
            self.status = "invalid"
            self.entity = None
            self.updated_at = now()
            return
        self.entity = ref
        self.value = None
        self.candidates = []
        self.status = "resolved"
        self.updated_at = now()

    def set_value(self, value) -> None:
        self.value = value
        self.status = "filled" if value is not None else "missing"
        self.updated_at = now()

    def clear(self) -> None:
        self.user_mention = None
        self.value = None
        self.entity = None
        self.candidates = []
        self.status = "missing"
        self.updated_at = now()


def new_item_id() -> str:
    return uuid.uuid4().hex[:12]


class CollectionItem(BaseModel):
    """Строка табличной части. item_id стабилен, позиция — не identity."""

    item_id: str = Field(default_factory=new_item_id)
    fields: dict[str, ScenarioField] = Field(default_factory=dict)

    def field(self, name: str) -> ScenarioField:
        return self.fields[name]


class PendingAction(BaseModel):
    """Отложенное write-действие, привязанное к конкретной версии frame."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    type: str
    frame_id: str
    frame_version: int
    payload: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now)

    def matches_version(self, frame: ScenarioFrame) -> bool:
        return self.frame_id == frame.id and self.frame_version == frame.version


class Focus(BaseModel):
    """Указатель на редактируемую часть frame. НЕ бизнес-истина и НЕ этап FSM."""

    scenario_id: str
    path: str
    history: list[str] = Field(default_factory=list)

    def move(self, path: str) -> None:
        if self.path != path:
            self.history.append(self.path)
            self.history = self.history[-8:]
            self.path = path


class ScenarioFrame(BaseModel):
    """Персистентная структура бизнес-сценария."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    scenario_type: str
    title: str = ""
    status: str = "active"
    version: int = 1

    fields: dict[str, ScenarioField] = Field(default_factory=dict)
    collections: dict[str, list[CollectionItem]] = Field(default_factory=dict)

    focus: Focus | None = None
    pending_resolution: str | None = None
    pending_action: PendingAction | None = None

    created_at: datetime = Field(default_factory=now)
    updated_at: datetime | None = None

    def bump(self) -> None:
        self.version += 1
        self.updated_at = now()

    def field(self, path: str) -> ScenarioField:
        """Доступ к полю по пути: 'vehicle' или 'items[item_id].quantity'."""
        if "[" not in path:
            return self.fields[path]
        col_part, field_name = path.rsplit(".", 1)
        col, item_id = col_part.split("[", 1)
        item = self.collection_item(col, item_id.rstrip("]"))
        return item.field(field_name)

    def collection_item(self, collection: str, item_id: str) -> CollectionItem:
        for it in self.collections.get(collection, []):
            if it.item_id == item_id:
                return it
        raise KeyError(f"no item {item_id!r} in {collection}")

    def append_item(self, collection: str, fields: dict[str, ScenarioField]) -> CollectionItem:
        item = CollectionItem(fields=fields)
        self.collections.setdefault(collection, []).append(item)
        self.bump()
        return item

    def remove_item(self, collection: str, item_id: str) -> bool:
        items = self.collections.get(collection, [])
        for i, it in enumerate(items):
            if it.item_id == item_id:
                del items[i]
                self.bump()
                return True
        return False

    def unresolved_required(self) -> list[str]:
        """Пути незаполненных обязательных полей (по схеме frame)."""
        out = []
        for name, f in self.fields.items():
            if f.required and not f.filled:
                out.append(name)
        for col, items in self.collections.items():
            for it in items:
                for fname, f in it.fields.items():
                    if f.required and not f.filled:
                        out.append(f"{col}[{it.item_id}].{fname}")
        return out


class ScenarioSession(BaseModel):
    """Сессия чата: несколько параллельных frame + активный + focus-стек."""

    chat_id: str
    frames: dict[str, ScenarioFrame] = Field(default_factory=dict)
    active_frame_id: str | None = None
    focus_stack: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now)

    @property
    def active(self) -> ScenarioFrame | None:
        return self.frames.get(self.active_frame_id) if self.active_frame_id else None


# GRACE: стабильный публичный экспорт (для точной проверки поверхности)
__all__ = [
    "ENTITY_STATUSES",
    "SCALAR_STATUSES",
    "CollectionItem",
    "EntityRef",
    "FieldKind",
    "Focus",
    "PendingAction",
    "ScenarioField",
    "ScenarioFrame",
    "ScenarioSession",
    "new_item_id",
    "now",
]
