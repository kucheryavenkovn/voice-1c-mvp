# START_MODULE_CONTRACT
#   PURPOSE: Централизованное применение typed-команд над ScenarioFrame.
#   SCOPE: Сессии, команды, focus, зависимость/инвалидация, PendingAction, проекция.
#   DEPENDS: pydantic, scenarios.models/commands/registry/resolver/projection
#   LINKS: M-SCENARIO-MANAGER, M-ENTITY-RESOLVER, M-SCENARIO-DEFINITIONS
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   CommandResult - исход применения команды (ok/status/changed/focus/data)
#   ScenarioManager - сессии, apply, подтверждения, инвалидация, проекция
# END_MODULE_MAP

"""ScenarioManager: централизованное применение typed-команд над ScenarioFrame.

Инварианты:
- любая мутация frame идёт через manager (LLM/state-потребители не мутируют сами);
- изменение влияющего поля инвалидирует зависимые поля по схеме;
- любое изменение frame после показа подтверждения инвалидирует PendingAction;
- «добавь строку»/адресация строк не запускают поиск номенклатуры;
- focus — указатель на часть frame, а не бизнес-истина.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from .commands import CONTRADICTORY_WITH_CONFIRM, MUTATING_KINDS, Command
from .models import (
    CollectionItem,
    FieldKind,
    Focus,
    PendingAction,
    ScenarioField,
    ScenarioFrame,
    ScenarioSession,
)
from .registry import ScenarioRegistry
from .resolver import EntityResolver

ExecutorFn = Callable[[str, ScenarioFrame, dict], dict]


class CommandResult(BaseModel):
    ok: bool
    status: str = ""
    message: str = ""
    changed: list[str] = Field(default_factory=list)
    focus: str | None = None
    data: dict = Field(default_factory=dict)

    @property
    def needs_resolution(self) -> bool:
        return self.status in ("ambiguous", "not_found", "resolving")


class ScenarioManager:
    def __init__(
        self,
        registry: ScenarioRegistry,
        resolver: EntityResolver,
        executor: ExecutorFn | None = None,
    ):
        self.registry = registry
        self.resolver = resolver
        self.executor = executor
        self._sessions: dict[str, ScenarioSession] = {}

    # --- session/frame lifecycle -------------------------------------------

    def session(self, chat_id: str | None) -> ScenarioSession | None:
        if not chat_id:
            return None
        return self._sessions.setdefault(chat_id, ScenarioSession(chat_id=chat_id))

    def start_scenario(self, session: ScenarioSession, scenario_type: str) -> ScenarioFrame:
        defn = self.registry.get(scenario_type)
        frame = ScenarioFrame(
            scenario_type=defn.scenario_type,
            title=defn.title,
            fields={
                name: ScenarioField(
                    name=name,
                    kind=spec.kind,
                    entity_type=spec.entity_type,
                    required=spec.required,
                    confirm_required=spec.confirm_required,
                    depends_on=list(spec.depends_on),
                    invalidates=list(spec.invalidates),
                )
                for name, spec in defn.fields.items()
            },
        )
        session.frames[frame.id] = frame
        session.active_frame_id = frame.id
        self._move_focus_to_first_unresolved(frame)
        return frame

    def cancel_scenario(self, session: ScenarioSession, frame: ScenarioFrame) -> None:
        frame.status = "cancelled"
        frame.pending_action = None
        frame.bump()
        session.focus_stack.clear()
        remaining = [f for f in session.frames.values() if f.status == "active"]
        session.active_frame_id = remaining[-1].id if remaining else None

    # --- command application -------------------------------------------------

    def apply(self, session: ScenarioSession, cmd: Command) -> CommandResult:
        handler: Callable[[ScenarioSession, Command], CommandResult] = {
            "start_scenario": self._cmd_start,
            "set_field": self._cmd_set_field,
            "clear_field": self._cmd_clear_field,
            "append_collection_item": self._cmd_append_item,
            "select_collection_item": self._cmd_select_item,
            "set_collection_field": self._cmd_set_collection_field,
            "remove_collection_item": self._cmd_remove_item,
            "switch_focus": self._cmd_switch_focus,
            "query_scenario": self._cmd_query,
            "propose_pending": self._cmd_propose,
            "confirm_pending": self._cmd_confirm,
            "reject_pending": self._cmd_reject,
            "cancel_scenario": self._cmd_cancel,
            # chitchat/clarify не мутируют frame — их обрабатывает оркестратор
            "chitchat": self._cmd_noop,
            "clarify": self._cmd_noop,
        }[cmd.kind]
        return handler(session, cmd)

    def _cmd_noop(self, session: ScenarioSession, cmd: Command) -> CommandResult:
        return CommandResult(ok=True, status=cmd.kind, message=cmd.answer_text or "")

    def apply_batch(self, session: ScenarioSession, commands: list[Command]) -> list[CommandResult]:
        """Транзакционное применение команд одной реплики.

        Правила безопасности:
        - batch, содержащий ОДНОВРЕМЕННО мутации/propose и confirm_pending,
          отклоняется целиком (лучше отказаться, чем создать неверный документ);
        - если в batch есть мутации, старый PendingAction инвалидируется
          до применения первой мутации;
        - при остановке на resolution/clarification (ambiguous/not_found)
          последующие entity-мутации и confirm пропускаются, скалярные
          set'ы применяются (количество к новой строке — безопасно).
        """
        kinds = [c.kind for c in commands]
        if "confirm_pending" in kinds and any(k in CONTRADICTORY_WITH_CONFIRM for k in kinds):
            return [
                CommandResult(
                    ok=False,
                    status="contradictory_batch",
                    message="нельзя одновременно менять данные и подтверждать создание",
                )
            ]
        if any(k in MUTATING_KINDS for k in kinds):
            frame = session.active
            if frame is not None and frame.pending_action is not None:
                frame.pending_action = None
                frame.bump()
        results: list[CommandResult] = []
        awaiting_resolution = False
        for cmd in commands:
            entity_set = cmd.kind in ("set_field", "set_collection_field") and bool(cmd.mention)
            if awaiting_resolution and (entity_set or cmd.kind == "confirm_pending"):
                results.append(
                    CommandResult(
                        ok=False,
                        status="skipped",
                        message=f"пропущено: ожидается разрешение предыдущего упоминания ({cmd.describe()})",
                    )
                )
                continue
            result = self.apply(session, cmd)
            results.append(result)
            if result.needs_resolution:
                awaiting_resolution = True
            if not result.ok and result.status in ("contradictory_batch",):
                break
        return results

    def _cmd_start(self, session: ScenarioSession, cmd: Command) -> CommandResult:
        frame = self.start_scenario(session, cmd.scenario_type or "stock_query")
        return CommandResult(
            ok=True, status="started", focus=self._focus_path(frame), data={"frame_id": frame.id}
        )

    def _cmd_set_field(self, session: ScenarioSession, cmd: Command) -> CommandResult:
        frame = self._require_frame(session)
        field = frame.field(cmd.path)
        if field.kind == FieldKind.ENTITY:
            outcome = self.resolver.resolve_mention(field, str(cmd.mention or cmd.value or ""))
            frame.pending_resolution = cmd.path if outcome.status == "ambiguous" else None
            self._after_mutation(frame, cmd.path)
            return CommandResult(
                ok=True,
                status=outcome.status,
                message=outcome.message,
                changed=[cmd.path],
                focus=self._focus_path(frame),
                data={"candidates": [c.model_dump() for c in field.candidates]},
            )
        value = cmd.value
        if field.name == "quantity" or (cmd.path.endswith(".quantity")):
            try:
                value = max(1, int(value))
            except (TypeError, ValueError):
                return CommandResult(
                    ok=False, status="invalid", message="количество должно быть целым числом"
                )
        field.set_value(value)
        self._after_mutation(frame, cmd.path)
        return CommandResult(
            ok=True,
            status="filled" if field.filled else "missing",
            changed=[cmd.path],
            focus=self._focus_path(frame),
        )

    def _cmd_clear_field(self, session: ScenarioSession, cmd: Command) -> CommandResult:
        frame = self._require_frame(session)
        frame.field(cmd.path).clear()
        self._after_mutation(frame, cmd.path)
        return CommandResult(
            ok=True, status="missing", changed=[cmd.path], focus=self._focus_path(frame)
        )

    def _cmd_append_item(self, session: ScenarioSession, cmd: Command) -> CommandResult:
        """Новая строка коллекции. НИКАКОГО поиска номенклатуры здесь нет."""
        frame = self._require_frame(session)
        defn = self.registry.get(frame.scenario_type)
        col_name = (
            cmd.collection
            or (cmd.path or "").split("[", 1)[0]
            or (next(iter(defn.collections)) if defn.collections else "items")
        )
        col_spec = defn.collections.get(col_name)
        if col_spec is None:
            return CommandResult(ok=False, status="invalid", message=f"нет коллекции {col_name!r}")
        fields = {
            fname: ScenarioField(
                name=fname,
                kind=fspec.kind,
                entity_type=fspec.entity_type,
                required=fspec.required,
                confirm_required=fspec.confirm_required,
                depends_on=list(fspec.depends_on),
                invalidates=list(fspec.invalidates),
            )
            for fname, fspec in col_spec.fields.items()
        }
        for fname, fspec in col_spec.fields.items():
            if fspec.default is not None:
                fields[fname].set_value(fspec.default)
        item: CollectionItem = frame.append_item(col_name, fields)
        first_required = next((f for f in fields.values() if f.required and not f.filled), None)
        target = (
            f"{col_name}[{item.item_id}].{first_required.name}"
            if first_required
            else f"{col_name}[{item.item_id}]"
        )
        frame.focus.move(target)
        self._after_mutation(frame, target)
        return CommandResult(
            ok=True,
            status="appended",
            changed=[target],
            focus=frame.focus.path,
            data={"item_id": item.item_id},
        )

    def _cmd_select_item(self, session: ScenarioSession, cmd: Command) -> CommandResult:
        frame = self._require_frame(session)
        collection = cmd.collection or (cmd.path or "").split("[", 1)[0] or "items"
        item = self._item_by_ref(frame, collection, cmd.item_ref)
        if item is None:
            return CommandResult(ok=False, status="invalid", message="строка не найдена")
        target = f"{collection}[{item.item_id}]"
        frame.focus.move(target)
        return CommandResult(
            ok=True, status="focused", focus=target, data={"item_id": item.item_id}
        )

    def _cmd_set_collection_field(self, session: ScenarioSession, cmd: Command) -> CommandResult:
        frame = self._require_frame(session)
        collection = cmd.collection or (cmd.path or "").split("[", 1)[0] or "items"
        field_name = cmd.field or cmd.focus or cmd.path
        item = self._item_by_ref(frame, collection, cmd.item_ref)
        if item is None:
            return CommandResult(ok=False, status="invalid", message="строка не найдена")
        if not field_name:
            return CommandResult(ok=False, status="invalid", message="не указано поле строки")
        field_path = f"{collection}[{item.item_id}].{field_name}"
        sub = Command(kind="set_field", path=field_path, mention=cmd.mention, value=cmd.value)
        return self._cmd_set_field(session, sub)

    def _cmd_remove_item(self, session: ScenarioSession, cmd: Command) -> CommandResult:
        frame = self._require_frame(session)
        collection = cmd.collection or (cmd.path or "").split("[", 1)[0] or "items"
        item = self._item_by_ref(frame, collection, cmd.item_ref)
        if item is None:
            return CommandResult(ok=False, status="invalid", message="строка не найдена")
        frame.remove_item(collection, item.item_id)
        if frame.focus and frame.focus.path.startswith(f"{collection}[{item.item_id}]"):
            self._move_focus_to_first_unresolved(frame)
        self._after_mutation(frame, f"{collection}[{item.item_id}]")
        return CommandResult(
            ok=True,
            status="removed",
            changed=[f"{collection}[{item.item_id}]"],
            focus=self._focus_path(frame),
            data={"item_id": item.item_id},
        )

    def _cmd_switch_focus(self, session: ScenarioSession, cmd: Command) -> CommandResult:
        frame = self._require_frame(session)
        target = cmd.focus or cmd.path or ""
        if cmd.collection or cmd.item_ref:
            collection = cmd.collection or "items"
            item = self._item_by_ref(frame, collection, cmd.item_ref)
            target = f"{collection}[{item.item_id}]" if item else target
        if target in ("back", "назад", "prev", "previous") and frame.focus.history:
            target = frame.focus.history[-1]
        frame.focus.move(target)
        return CommandResult(ok=True, status="focused", focus=frame.focus.path)

    def _cmd_query(self, session: ScenarioSession, cmd: Command) -> CommandResult:
        frame = self._require_frame(session)
        return CommandResult(
            ok=True,
            status="query",
            focus=self._focus_path(frame),
            data={"projection": self.compact_projection(frame)},
        )

    def _cmd_propose(self, session: ScenarioSession, cmd: Command) -> CommandResult:
        """Готов оформить документы: проверка готовности + показ подтверждения."""
        frame = self._require_frame(session)
        unresolved = frame.unresolved_required()
        if unresolved:
            return CommandResult(
                ok=False,
                status="incomplete",
                message=f"не заполнено: {', '.join(unresolved)}",
            )
        self.propose_pending_action(frame, "create_repair_documents", payload={"source": "llm"})
        return CommandResult(ok=True, status="proposed", focus=self._focus_path(frame))

    def _cmd_confirm(self, session: ScenarioSession, cmd: Command) -> CommandResult:
        frame = self._require_frame(session)
        # подтверждение разрешения ссылки (ambiguous): «да»/выбор кандидата
        if frame.pending_action is None and frame.pending_resolution:
            path = frame.pending_resolution
            try:
                index = int(cmd.value) if cmd.value is not None else 0
            except (TypeError, ValueError):
                index = 0
            return self.confirm_resolution(frame, path, index)
        pending = frame.pending_action
        if pending is None:
            return CommandResult(
                ok=False, status="no_pending", message="нет действия для подтверждения"
            )
        if not pending.matches_version(frame):
            frame.pending_action = None
            return CommandResult(
                ok=False,
                status="stale",
                message="данные изменились после подтверждения — нужно подтвердить заново",
            )
        unresolved = frame.unresolved_required()
        if unresolved:
            return CommandResult(
                ok=False, status="incomplete", message=f"не заполнено: {', '.join(unresolved)}"
            )
        if self.executor is None:
            return CommandResult(ok=False, status="no_executor", message="исполнение не настроено")
        result = self.executor(pending.type, frame, pending.payload)
        frame.pending_action = None
        frame.status = "completed"
        frame.bump()
        return CommandResult(ok=True, status="executed", data=result)

    def _cmd_reject(self, session: ScenarioSession, cmd: Command) -> CommandResult:
        frame = self._require_frame(session)
        frame.pending_action = None
        frame.bump()
        return CommandResult(ok=True, status="rejected", message="действие отменено")

    def _cmd_cancel(self, session: ScenarioSession, cmd: Command) -> CommandResult:
        frame = self._require_frame(session)
        self.cancel_scenario(session, frame)
        return CommandResult(ok=True, status="cancelled")

    # --- entity confirmation (ambiguous -> resolved) --------------------------

    def confirm_resolution(self, frame: ScenarioFrame, path: str, index: int) -> CommandResult:
        """Явный выбор/подтверждение кандидата пользователем."""
        field = frame.field(path)
        ref = self.resolver.select_candidate(field, index)
        if ref is None:
            return CommandResult(
                ok=False, status="invalid", message="кандидат не найден или без идентичности 1С"
            )
        frame.pending_resolution = None
        self._after_mutation(frame, path)
        return CommandResult(
            ok=True,
            status="resolved",
            changed=[path],
            focus=self._focus_path(frame),
            data={"ref": ref.model_dump()},
        )

    def propose_pending_action(
        self, frame: ScenarioFrame, action_type: str, payload: dict | None = None
    ) -> PendingAction:
        pending = PendingAction(
            type=action_type,
            frame_id=frame.id,
            frame_version=frame.version,
            payload=payload or {},
        )
        frame.pending_action = pending
        return pending

    # --- invalidation ----------------------------------------------------------

    def _after_mutation(self, frame: ScenarioFrame, changed_path: str) -> None:
        self.invalidate_dependents(frame, changed_path)
        frame.bump()
        if frame.pending_action and not frame.pending_action.matches_version(frame):
            frame.pending_action = None

    def invalidate_dependents(self, frame: ScenarioFrame, changed_path: str) -> list[str]:
        """Централизованная инвалидация зависимых полей по схеме определения.

        Срабатывает если: (a) у изменённого поля в схеме есть invalidates,
        попадающий в кандидата; (b) у кандидата depends_on попадает в изменённое.
        Паттерн 'items[].nomenclature' покрывает строки коллекции items.
        """
        defn = self.registry.get(frame.scenario_type)

        def split_path(path: str) -> tuple[str, str | None]:
            if "[" in path:
                root = path.split("[", 1)[0]
                leaf = path.rsplit(".", 1)[-1] if "." in path else None
                return root, leaf
            if "." in path:
                root, leaf = path.split(".", 1)
                return root, leaf
            return path, None

        def pattern_hits(pattern: str, target_path: str) -> bool:
            if pattern == target_path:
                return True
            pat_root, pat_leaf = split_path(pattern)
            tgt_root, tgt_leaf = split_path(target_path)
            if pat_root != tgt_root:
                return False
            if pat_leaf is None:
                return True
            return pat_leaf == tgt_leaf

        def spec_of(path: str):
            if "." not in path:
                return defn.fields.get(path)
            col = path.split("[", 1)[0]
            fname = path.rsplit(".", 1)[-1]
            col_spec = defn.collections.get(col)
            return col_spec.fields.get(fname) if col_spec else None

        candidates: list[tuple[str, Any]] = []
        for name, f in frame.fields.items():
            candidates.append((name, f))
        for col_name, items in frame.collections.items():
            for item in items:
                for fname, f in item.fields.items():
                    candidates.append((f"{col_name}[{item.item_id}].{fname}", f))

        changed_spec = spec_of(changed_path)
        invalidated: list[str] = []
        for path, f in candidates:
            if path == changed_path or path.startswith(changed_path + "."):
                continue
            spec = spec_of(path)
            if spec is None:
                continue
            hit = any(pattern_hits(p, path) for p in (spec.depends_on or []))
            if not hit and changed_spec is not None:
                hit = any(pattern_hits(p, path) for p in (changed_spec.invalidates or []))
            if hit and (f.filled or f.status != "missing"):
                f.clear()
                invalidated.append(path)
        return invalidated

    # --- helpers ----------------------------------------------------------------

    def _require_frame(self, session: ScenarioSession) -> ScenarioFrame:
        frame = session.active
        if frame is None:
            raise KeyError("нет активного сценария; сначала start_scenario")
        return frame

    def _item_by_ref(
        self, frame: ScenarioFrame, collection: str, item_ref: str | None
    ) -> CollectionItem | None:
        items = frame.collections.get(collection or "items", [])
        if not items:
            return None
        ref = (item_ref or "").strip().lower()
        if not ref or ref in ("new", "новая", "новый"):
            # 'new' — строка, добавленная последней в этом batch
            if ref:
                return items[-1]
            if frame.focus and frame.focus.path.startswith(f"{collection}["):
                item_id = frame.focus.path.split("[", 1)[1].split("]", 1)[0]
                return next((it for it in items if it.item_id == item_id), None)
            return items[-1]
        if ref in ("this", "эта", "этот", "текущая", "там"):
            return self._focused_item(frame, collection)
        if ref in ("prev", "previous", "предыдущая", "предыдущий", "прошлая"):
            # последняя запись focus-истории, указывающая на другую строку
            if frame.focus is not None:
                for hist in reversed(frame.focus.history):
                    if hist.startswith(f"{collection}["):
                        hid = hist.split("[", 1)[1].split("]", 1)[0]
                        found = next((it for it in items if it.item_id == hid), None)
                        if found is not None:
                            return found
            focused = self._focused_item(frame, collection)
            if focused is not None:
                idx = items.index(focused)
                return items[idx - 1] if idx > 0 else None
            return items[-1]
        if ref == "first":
            return items[0]
        if ref == "last":
            return items[-1]
        if ref.isdigit():
            n = int(ref)
            return items[n - 1] if 1 <= n <= len(items) else None
        return next((it for it in items if it.item_id == ref), None)

    def _focused_item(self, frame: ScenarioFrame, collection: str) -> CollectionItem | None:
        if frame.focus is None:
            return None
        path = frame.focus.path
        if not path.startswith(f"{collection}["):
            return None
        item_id = path.split("[", 1)[1].split("]", 1)[0]
        return next(
            (it for it in frame.collections.get(collection, []) if it.item_id == item_id), None
        )

    def _move_focus_to_first_unresolved(self, frame: ScenarioFrame) -> None:
        for name, f in frame.fields.items():
            if f.required and not f.filled:
                frame.focus = Focus(scenario_id=frame.id, path=name)
                return
        for col, items in frame.collections.items():
            for it in items:
                for fname, f in it.fields.items():
                    if f.required and not f.filled:
                        frame.focus = Focus(
                            scenario_id=frame.id, path=f"{col}[{it.item_id}].{fname}"
                        )
                        return
        frame.focus = Focus(scenario_id=frame.id, path="")

    def _focus_path(self, frame: ScenarioFrame) -> str:
        return frame.focus.path if frame.focus else ""

    def unresolved_paths(self, frame: ScenarioFrame) -> list[str]:
        return frame.unresolved_required()

    def compact_projection(self, frame: ScenarioFrame) -> str:
        """Компактная проекция frame для маленького context window LLM."""
        from .projection import frame_projection

        return frame_projection(self.registry.get(frame.scenario_type), frame)


# GRACE: стабильный публичный экспорт (для точной проверки поверхности)
__all__ = [
    "CommandResult",
    "ScenarioManager",
]
