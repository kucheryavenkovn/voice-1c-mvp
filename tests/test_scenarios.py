"""ScenarioFrame / ScenarioManager: архитектурные инварианты новой сценарной модели.

Покрывает обязательные кейсы 1-10 из C-SCENARIO-FRAMES:
collections/item_id, адресные правки, инвалидация, ambiguous/not_found,
параллельные frame, small-context projection, строгий EntityRef, execution gate.
"""

import sys

import pytest
from conftest import ROOT

sys.path.insert(0, str(ROOT / "voice-gateway"))

from scenarios import (
    Command,
    EntityRef,
    EntityResolver,
    ScenarioField,
    ScenarioManager,
    ScenarioSession,
    build_repair_payload,
    execution_guard,
    load_definitions,
    parse_commands,
)


@pytest.fixture
def registry():
    return load_definitions(ROOT / "voice-gateway" / "scenarios" / "definitions")


@pytest.fixture
def resolver():
    r = EntityResolver({})

    def vehicle_lookup(mention):
        if "киров" in mention.lower():
            return {
                "found": True,
                "entities": [{"name": "Трактор Кировец К-744Р", "code": "000000008"}],
            }
        if "мтз" in mention.lower():
            return {
                "found": True,
                "entities": [{"name": "Трактор МТЗ-82", "code": "000000010"}],
            }
        if "трактор" in mention.lower():
            return {
                "found": True,
                "entities": [
                    {"name": "Трактор Кировец К-744Р", "code": "000000008"},
                    {"name": "Трактор МТЗ-82", "code": "000000010"},
                ],
            }
        return {"found": False, "entities": []}

    def part_lookup(mention):
        if "диск" in mention.lower():
            return {
                "found": True,
                "entities": [
                    {"name": "Диск колесный задний", "code": "000000100", "article": "DK-300"}
                ],
            }
        return {"found": False, "entities": []}

    r.register("vehicle", vehicle_lookup)
    r.register("part", part_lookup)
    return r


@pytest.fixture
def manager(registry, resolver):
    return ScenarioManager(registry, resolver)


def repair_session(manager) -> ScenarioSession:
    s = manager.session("t1")
    manager.apply(s, Command(kind="start_scenario", scenario_type="repair_order"))
    return s


def resolve_vehicle(manager, s, mention: str, confirm: bool = True):
    r = manager.apply(s, Command(kind="set_field", path="vehicle", mention=mention))
    if confirm and r.status == "ambiguous":
        frame = s.active
        r = manager.confirm_resolution(frame, "vehicle", 0)
    return r


def add_item(manager, s, part_mention: str, qty: int = 2, confirm: bool = True):
    manager.apply(s, Command(kind="append_collection_item", path="items"))
    frame = s.active
    row = frame.collections["items"][-1]
    manager.apply(
        s,
        Command(
            kind="set_collection_field",
            path="items",
            item_ref=row.item_id,
            focus="quantity",
            value=qty,
        ),
    )
    r = manager.apply(
        s,
        Command(
            kind="set_collection_field",
            path="items",
            item_ref=row.item_id,
            focus="nomenclature",
            mention=part_mention,
        ),
    )
    if confirm and r.status == "ambiguous":
        r = manager.confirm_resolution(frame, f"items[{row.item_id}].nomenclature", 0)
    return r


# --- Case 1: «добавь строку» не запускает поиск номенклатуры ----------------


def test_append_item_does_not_search_nomenclature(manager, resolver):
    s = repair_session(manager)
    resolve_vehicle(manager, s, "кировец")
    calls = []

    original = resolver.lookup

    def spy(entity_type, mention):
        calls.append((entity_type, mention))
        return original(entity_type, mention)

    resolver.lookup = spy
    r = manager.apply(s, Command(kind="append_collection_item", path="items"))
    assert r.ok and r.status == "appended"
    assert calls == [], "append строки не должен вызывать lookup номенклатуры"
    frame = s.active
    assert len(frame.collections["items"]) == 1
    assert frame.focus.path.startswith("items[")
    assert frame.focus.path.endswith(".nomenclature")


# --- Case 2: «во второй строке поставь три штуки» ----------------------------


def test_set_quantity_of_second_row_only(manager):
    s = repair_session(manager)
    resolve_vehicle(manager, s, "кировец")
    add_item(manager, s, "диск", qty=5)
    add_item(manager, s, "диск", qty=2)
    frame = s.active
    rows = frame.collections["items"]
    ids_before = [it.item_id for it in rows]

    r = manager.apply(
        s,
        Command(kind="set_collection_field", path="items", item_ref="2", focus="quantity", value=3),
    )
    assert r.ok
    assert [it.fields["quantity"].value for it in rows] == [5, 3]
    assert [it.item_id for it in rows] == ids_before, "item_id остальных строк не меняются"
    assert rows[0].fields["nomenclature"].status == "resolved"


# --- Case 3: «удали первую строку» -------------------------------------------


def test_remove_first_row_keeps_identity_of_others(manager):
    s = repair_session(manager)
    resolve_vehicle(manager, s, "кировец")
    add_item(manager, s, "диск", qty=5)
    add_item(manager, s, "диск", qty=2)
    frame = s.active
    ids = [it.item_id for it in frame.collections["items"]]

    r = manager.apply(s, Command(kind="remove_collection_item", path="items", item_ref="1"))
    assert r.ok
    rows = frame.collections["items"]
    assert [it.item_id for it in rows] == [ids[1]]
    assert rows[0].fields["quantity"].value == 2


# --- Case 4: смена техники инвалидирует зависимые, сохраняет независимые -----


def test_vehicle_change_invalidates_dependent_part_only(manager):
    s = repair_session(manager)
    resolve_vehicle(manager, s, "кировец")
    add_item(manager, s, "диск", qty=5)
    frame = s.active
    assert frame.fields["vehicle"].status == "resolved"

    resolve_vehicle(manager, s, "мтз")
    frame = s.active
    assert frame.fields["vehicle"].entity.code == "000000010"
    row = frame.collections["items"][0]
    assert row.fields["nomenclature"].status == "missing", (
        "номенклатура инвалидируется сменой техники"
    )
    assert row.fields["quantity"].value == 5, "независимый quantity сохраняется"


# --- Case 5: параллельный STOCK_QUERY не ломает REPAIR_ORDER -----------------


def test_parallel_stock_query_keeps_repair_frame(manager):
    s = repair_session(manager)
    resolve_vehicle(manager, s, "кировец")
    add_item(manager, s, "диск", qty=5)
    repair_id = s.active.id

    manager.apply(s, Command(kind="start_scenario", scenario_type="stock_query"))
    stock_frame = s.active
    assert stock_frame.id != repair_id and stock_frame.scenario_type == "stock_query"
    manager.apply(s, Command(kind="set_field", path="nomenclature", mention="диск"))
    manager.cancel_scenario(s, stock_frame)

    assert s.active_frame_id == repair_id
    frame = s.active
    assert frame.fields["vehicle"].status == "resolved"
    assert len(frame.collections["items"]) == 1


# --- Case 6: изменение данных инвалидирует PendingAction ---------------------


def test_pending_action_invalidated_by_late_correction(manager):
    s = repair_session(manager)
    resolve_vehicle(manager, s, "кировец")
    add_item(manager, s, "диск", qty=5)
    frame = s.active
    pending = manager.propose_pending_action(frame, "create_repair_documents")
    assert pending.matches_version(frame)

    add_item(manager, s, "диск", qty=2)
    frame = s.active
    assert frame.pending_action is None, (
        "изменение строки после вопроса «создаём?» инвалидирует подтверждение"
    )
    assert not pending.matches_version(frame)

    executed = []

    def executor(action_type, fr, payload):
        executed.append(action_type)
        return {}

    manager.executor = executor
    r = manager.apply(s, Command(kind="confirm_pending"))
    assert not executed, "после инвалидации подтверждение не должно создавать документы"
    assert r.status == "no_pending"


def test_confirm_executes_only_for_matching_version(manager):
    s = repair_session(manager)
    resolve_vehicle(manager, s, "кировец")
    add_item(manager, s, "диск", qty=5)
    frame = s.active
    executed = []

    def executor(action_type, fr, payload):
        executed.append((action_type, payload))
        return {"docs": {"repair": "1"}}

    manager.executor = executor
    manager.propose_pending_action(frame, "create_repair_documents", payload={"marker": 1})
    r = manager.apply(s, Command(kind="confirm_pending"))
    assert r.ok and r.status == "executed"
    assert executed == [("create_repair_documents", {"marker": 1})]


# --- Case 7: ambiguous не считается заполненным -------------------------------


def test_ambiguous_field_is_not_filled(manager):
    s = repair_session(manager)
    r = manager.apply(s, Command(kind="set_field", path="vehicle", mention="трактор"))
    assert r.status == "ambiguous"
    frame = s.active
    f = frame.fields["vehicle"]
    assert not f.filled and f.value is None and f.entity is None
    assert len(f.candidates) == 2
    assert frame.unresolved_required() == ["vehicle"]

    manager.confirm_resolution(frame, "vehicle", 1)
    assert frame.fields["vehicle"].status == "resolved"
    assert frame.fields["vehicle"].entity.code == "000000010"
    assert frame.unresolved_required() == []


# --- Case 8: выдуманное LLM имя не создаёт EntityRef --------------------------


def test_not_found_creates_no_entity_ref(manager):
    s = repair_session(manager)
    r = manager.apply(s, Command(kind="set_field", path="vehicle", mention="левиафан"))
    assert r.status == "not_found"
    f = s.active.fields["vehicle"]
    assert f.entity is None and not f.filled
    assert s.active.unresolved_required() == ["vehicle"]


# --- Case 9: projection достаточна без истории чата ---------------------------


def test_compact_projection_small_context(manager):
    s = repair_session(manager)
    resolve_vehicle(manager, s, "кировец")
    add_item(manager, s, "диск", qty=5)
    manager.apply(s, Command(kind="append_collection_item", path="items"))
    frame = s.active
    row2 = frame.collections["items"][-1]
    manager.apply(
        s,
        Command(
            kind="set_collection_field",
            path="items",
            item_ref=row2.item_id,
            focus="quantity",
            value=2,
        ),
    )
    proj = manager.compact_projection(frame)
    assert "Кировец" in proj and "resolved" in proj
    assert "missing" in proj  # незаполненная номенклатура второй строки
    assert "focus: items[" in proj
    assert len(proj) < 800, "проекция должна быть компактной для маленького окна"


# --- Case 10: строгий EntityRef — identity, а не имя --------------------------


def test_entity_ref_requires_identity_not_name(manager, registry):
    # кандидат без кода/ref не может разрешить поле
    r = EntityResolver({})
    r.register(
        "vehicle",
        lambda mention: {"found": True, "entities": [{"name": "Трактор без кода"}]},
    )
    m = ScenarioManager(registry, r)
    s = m.session("t2")
    m.apply(s, Command(kind="start_scenario", scenario_type="repair_order"))
    out = m.apply(s, Command(kind="set_field", path="vehicle", mention="без кода"))
    frame = s.active
    f = frame.fields["vehicle"]
    # единственный кандидат без confirm_required, но БЕЗ идентичности -> не resolved
    assert out.status == "ambiguous"
    assert EntityResolver.select_candidate(f, 0) is None
    assert not f.filled

    # payload для 1С строится из identity, а не из упоминаний пользователя
    m2 = ScenarioManager(registry, EntityResolver({}))
    s2 = m2.session("t3")
    m2.apply(s2, Command(kind="start_scenario", scenario_type="repair_order"))
    fr = s2.active
    fr.fields["vehicle"].resolve(EntityRef(entity_type="vehicle", ref="000000008", name="Кировец"))
    row = fr.append_item(
        "items",
        {
            "nomenclature": ScenarioField(
                name="nomenclature", kind="entity", entity_type="part", required=True
            ),
            "quantity": ScenarioField(name="quantity", required=True),
        },
    )
    row.fields["nomenclature"].resolve(
        EntityRef(entity_type="part", ref="000000100", name="Диск", metadata={"article": "DK-300"})
    )
    row.fields["quantity"].set_value(5)
    payload = build_repair_payload(fr)
    assert payload["vehicle_ref"] == "000000008"
    assert payload["items"][0]["code"] == "000000100" and payload["items"][0]["qty"] == 5


# --- инфраструктура: definitions, команды, execution gate ---------------------


def test_definitions_load_and_validate(registry):
    assert registry.types() == ["repair_order", "stock_query"]
    ro = registry.get("repair_order")
    assert ro.fields["vehicle"].required and ro.fields["vehicle"].entity_type == "vehicle"
    assert ro.collections["items"].fields["nomenclature"].required
    assert ro.execution.confirmation_phrase == "Создаём документы?"
    with pytest.raises(FileNotFoundError):
        load_definitions(ROOT / "nonexistent")
    assert ro.required_paths() == ["vehicle", "items[].nomenclature", "items[].quantity"]


def test_invalid_definition_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "scenario_type: broken\ntitle: x\nfields:\n  a:\n    kind: entity\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="entity_type"):
        load_definitions(tmp_path)


def test_parse_commands_rejects_unknown_kinds():
    cmds = parse_commands(
        [
            {"kind": "set_field", "path": "vehicle", "mention": "мтз"},
            {"kind": "run_arbitrary_sql"},  # неизвестная -> отброшена
            {"action": "append_collection_item", "path": "items"},
            "мусор",
        ]
    )
    assert [c.kind for c in cmds] == ["set_field", "append_collection_item"]


def test_execution_guard_blocks_incomplete_frame(manager):
    s = repair_session(manager)
    frame = s.active
    assert execution_guard(frame) is not None  # нет vehicle/строк/pending
    resolve_vehicle(manager, s, "кировец")
    add_item(manager, s, "диск", qty=5)
    frame = s.active
    assert execution_guard(frame) is not None  # нет pending_action
    manager.propose_pending_action(frame, "create_repair_documents")
    assert execution_guard(frame) is None
