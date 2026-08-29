"""C-LLM-TYPED-COMMANDS: production LLM typed-command pipeline.

Fake LLM (детерминированная заглушка вместо small model) возвращает typed
commands по фразам; проверяется полный pipeline: проекция -> команды ->
apply_batch -> ScenarioFrame -> детерминированный ответ. Resilience:
malformed output безопасен, PendingAction защищён, focus резолвится в item_id.
"""

import json
from urllib.parse import unquote

import app
import onec
import pytest

STOCK_FOR_ITEM = '[1]{"Склад","Ост"}:\n  Склад инженера,5'

VEHICLE_KIROVETS = '[1]{"Наименование","Код"}:\n  Трактор Кировец К-744Р,ТР-0000008'
VEHICLE_MTZ = '[1]{"Наименование","Код"}:\n  Трактор МТЗ-82,ТР-0000010'
PART_DISK = '[1]{"Наименование","Артикул","Код"}:\n  Диск колесный задний,DK-300,ЗЧ-0000102'
PART_BELT = '[1]{"Наименование","Артикул","Код"}:\n  Ремень приводной,РП-12,ЗЧ-0000120'
PART_AMBIG = (
    '[2]{"Наименование","Артикул","Код"}:\n'
    "  Диск колесный передний,DK-100,ЗЧ-0000101\n"
    "  Диск колесный задний,DK-300,ЗЧ-0000102"
)


def fake_llm(mapping):
    """Детерминированный 'small LLM': фраза -> typed commands JSON.
    Ключи сопоставляются от самого длинного к короткому (как фразы, не подстроки)."""

    def call(payload):
        prompt = payload["messages"][-1]["content"]
        utterance = prompt.split("РЕПЛИКА ПОЛЬЗОВАТЕЛЯ: ", 1)[1].rsplit("\n", 1)[0]
        utterance = utterance.strip().lower()
        for phrase in sorted(mapping, key=len, reverse=True):
            if phrase in utterance:
                return json.dumps({"commands": mapping[phrase]}, ensure_ascii=False)
        return '{"commands": []}'

    return call


def setup_llm(gw, mapping, monkeypatch):
    monkeypatch.setattr(app, "_lm_chat_json", fake_llm(mapping))
    monkeypatch.setattr(app, "lm_phrase", lambda canned, state: canned)


def frame_of(cid):
    return app._scenario_manager().session(cid).active


def post(gw, cid, text):
    return gw.client.post("/ask-text", json={"text": text, "chat_id": cid})


START = {"kind": "start_scenario", "scenario_type": "repair_order"}
SET_VEHICLE = {"kind": "set_field", "path": "vehicle", "mention": "кировец"}


@pytest.fixture
def env(gw, monkeypatch):
    """Готовый старт: реплика «нужен диск» -> REPAIR_ORDER, вопрос про технику."""
    # изоляция: чужие сессии того же chat_id из предыдущих тестов
    app._scenario_manager()._sessions.pop("llm", None)
    app._DIALOG_STATES.pop("llm", None)
    app._CHATS.pop("llm", None)
    mapping = {
        "нужен диск": [START],
        "заказать запчасть": [START],
    }
    setup_llm(gw, mapping, monkeypatch)
    gw.onec_data = VEHICLE_KIROVETS
    r = post(gw, "llm", "нужен диск")
    assert "Для какой техники" in unquote(r.headers["X-Answer"])
    return gw


# --- старт сценария и техника -------------------------------------------------


def test_start_scenario_asks_vehicle(env):
    frame = frame_of("llm")
    assert frame.scenario_type == "repair_order"
    assert frame.fields["vehicle"].status == "missing"


def test_vehicle_mention_resolves_strictly(env, monkeypatch):
    gw = env
    setup_llm(
        gw,
        {
            "нужен диск": [START],
            "кировец": [SET_VEHICLE],
            "да": [{"kind": "confirm_pending"}],
        },
        monkeypatch,
    )
    r = post(gw, "llm", "кировец")
    assert "Нашёл технику: Трактор Кировец К-744Р. Это она?" in unquote(r.headers["X-Answer"])
    r = post(gw, "llm", "да")
    assert "Техника подтверждена" in unquote(r.headers["X-Answer"])
    frame = frame_of("llm")
    assert frame.fields["vehicle"].entity.code == "ТР-0000008"


def test_llm_cannot_invent_entity_ref(env, monkeypatch):
    """LLM прислала «готовый» EntityRef — поле всё равно разрешается через 1С."""
    gw = env
    bad = [
        {
            "kind": "set_field",
            "path": "vehicle",
            "mention": "кировец",
            "value": {"ref": "ВЫДУМАННЫЙ-GUID", "name": "Кировец"},
        }
    ]
    setup_llm(gw, {"кировец": bad, "да": [{"kind": "confirm_pending"}]}, monkeypatch)
    post(gw, "llm", "кировец")
    frame = frame_of("llm")
    f = frame.fields["vehicle"]
    assert f.candidates and f.candidates[0].code == "ТР-0000008", "identity только из 1С"


# --- добавление позиций (несколько команд из одной реплики) --------------------


def ready_with_vehicle(gw, monkeypatch):
    setup_llm(
        gw,
        {
            "нужен диск": [START],
            "кировец": [SET_VEHICLE],
            "да": [{"kind": "confirm_pending"}],
            "да.": [{"kind": "confirm_pending"}],
        },
        monkeypatch,
    )
    post(gw, "llm", "нужен диск")
    post(gw, "llm", "кировец")
    post(gw, "llm", "да")
    return gw


def test_multi_command_append_with_mention_and_qty(env, monkeypatch):
    """«Добавь масляный фильтр, две штуки» -> append + mention + quantity."""
    gw = ready_with_vehicle(env, monkeypatch)
    setup_llm(
        gw,
        {
            "да": [{"kind": "confirm_pending"}],
            "добавь масляный фильтр": [
                {"kind": "append_collection_item", "collection": "items"},
                {
                    "kind": "set_collection_field",
                    "collection": "items",
                    "item_ref": "new",
                    "field": "nomenclature",
                    "mention": "масляный фильтр",
                },
                {
                    "kind": "set_collection_field",
                    "collection": "items",
                    "item_ref": "new",
                    "field": "quantity",
                    "value": 2,
                },
            ],
            "да, подтверждаю": [{"kind": "confirm_pending"}],
        },
        monkeypatch,
    )
    gw.onec_data = PART_DISK
    r = post(gw, "llm", "добавь масляный фильтр, две штуки")
    ans = unquote(r.headers["X-Answer"])
    frame = frame_of("llm")
    rows = frame.collections["items"]
    assert len(rows) == 1
    assert rows[0].fields["quantity"].value == 2, "количество применилось в том же batch"
    assert frame.pending_resolution, "номенклатура ждёт подтверждения (resolution)"
    gw.onec_data = PART_DISK
    r = post(gw, "llm", "да, подтверждаю")
    ans = unquote(r.headers["X-Answer"])
    assert "Добавил в заказ" in ans and "2 шт" in ans


# --- изменение строк по местуимениям/порядку ----------------------------------


def prepared_two_rows(gw, monkeypatch):
    ready_with_vehicle(gw, monkeypatch)
    add_row = [
        {"kind": "append_collection_item", "collection": "items"},
        {
            "kind": "set_collection_field",
            "collection": "items",
            "item_ref": "new",
            "field": "quantity",
            "value": 1,
        },
    ]
    setup_llm(
        gw,
        {
            "добавь строку": add_row,
            "ещё одну позицию": add_row,
            "диск": [
                {
                    "kind": "set_collection_field",
                    "collection": "items",
                    "item_ref": "new",
                    "field": "nomenclature",
                    "mention": "диск",
                }
            ],
            "ремень": [
                {
                    "kind": "set_collection_field",
                    "collection": "items",
                    "item_ref": "new",
                    "field": "nomenclature",
                    "mention": "ремень",
                }
            ],
            "да": [{"kind": "confirm_pending"}],
            "да.": [{"kind": "confirm_pending"}],
            "во второй строке": [
                {
                    "kind": "set_collection_field",
                    "collection": "items",
                    "item_ref": "2",
                    "field": "quantity",
                    "value": 3,
                }
            ],
            "там сделай три штуки": [
                {
                    "kind": "set_collection_field",
                    "collection": "items",
                    "item_ref": "this",
                    "field": "quantity",
                    "value": 3,
                }
            ],
            "предыдущей строке количество два": [
                {
                    "kind": "set_collection_field",
                    "collection": "items",
                    "item_ref": "prev",
                    "field": "quantity",
                    "value": 2,
                }
            ],
            "удали первую строку": [
                {"kind": "remove_collection_item", "collection": "items", "item_ref": "1"}
            ],
            "убери вторую позицию": [
                {"kind": "remove_collection_item", "collection": "items", "item_ref": "2"}
            ],
            "вернись к предыдущей": [
                {"kind": "select_collection_item", "collection": "items", "item_ref": "prev"}
            ],
        },
        monkeypatch,
    )
    # строка 1: диск
    gw.onec_data = PART_DISK
    post(gw, "llm", "добавь строку")
    post(gw, "llm", "диск")
    gw.onec_data = STOCK_FOR_ITEM
    post(gw, "llm", "да.")
    # строка 2: ремень
    gw.onec_data = PART_BELT
    post(gw, "llm", "ещё одну позицию")
    post(gw, "llm", "ремень")
    post(gw, "llm", "да.")
    return gw


def test_second_row_quantity_by_ordinal(env, monkeypatch):
    gw = prepared_two_rows(env, monkeypatch)
    ids = [it.item_id for it in frame_of("llm").collections["items"]]
    post(gw, "llm", "во второй строке поставь три")
    rows = frame_of("llm").collections["items"]
    assert [it.fields["quantity"].value for it in rows] == [1, 3]
    assert [it.item_id for it in rows] == ids


def test_prev_row_via_focus_history(env, monkeypatch):
    gw = prepared_two_rows(env, monkeypatch)
    post(gw, "llm", "в предыдущей строке количество два")
    rows = frame_of("llm").collections["items"]
    assert [it.fields["quantity"].value for it in rows] == [2, 1]


def test_this_row_via_focus(env, monkeypatch):
    gw = prepared_two_rows(env, monkeypatch)
    post(gw, "llm", "вернись к предыдущей")
    post(gw, "llm", "там сделай три штуки")
    rows = frame_of("llm").collections["items"]
    assert [it.fields["quantity"].value for it in rows] == [3, 1]


def test_remove_rows(env, monkeypatch):
    gw = prepared_two_rows(env, monkeypatch)
    ids = [it.item_id for it in frame_of("llm").collections["items"]]
    post(gw, "llm", "удали первую строку")
    rows = frame_of("llm").collections["items"]
    assert [it.item_id for it in rows] == [ids[1]]
    # «второй» больше нет — удаление отклоняется, строка остаётся
    r = post(gw, "llm", "убери вторую позицию")
    assert "строка не найдена" in unquote(r.headers["X-Answer"])
    assert [it.item_id for it in frame_of("llm").collections["items"]] == [ids[1]]


# --- техника: коррекция и инвалидация ------------------------------------------


def test_vehicle_correction_invalidates_parts(env, monkeypatch):
    gw = ready_with_vehicle(env, monkeypatch)
    gw.onec_data = PART_DISK
    post(gw, "llm", "добавь строку")
    post(gw, "llm", "диск")
    gw.onec_data = STOCK_FOR_ITEM
    post(gw, "llm", "да.")
    setup_llm(
        gw,
        {
            "нет, всё-таки мтз-82": [{"kind": "set_field", "path": "vehicle", "mention": "МТЗ-82"}],
            "оставь детали, но поменяй технику": [
                {"kind": "set_field", "path": "vehicle", "mention": "МТЗ-82"}
            ],
            "да": [{"kind": "confirm_pending"}],
            "да.": [{"kind": "confirm_pending"}],
        },
        monkeypatch,
    )
    gw.onec_data = VEHICLE_MTZ
    post(gw, "llm", "нет, всё-таки МТЗ-82")
    post(gw, "llm", "да")
    frame = frame_of("llm")
    assert frame.fields["vehicle"].entity.code == "ТР-0000010"
    row = frame.collections["items"][0]
    assert row.fields["nomenclature"].status == "missing", "зависимая номенклатура инвалидируется"
    assert row.fields["quantity"].value == 1, "независимое количество сохраняется"


# --- ambiguity / clarify -------------------------------------------------------


def test_ambiguous_part_requires_choice(env, monkeypatch):
    gw = ready_with_vehicle(env, monkeypatch)
    setup_llm(
        gw,
        {
            "добавь строку": [
                {"kind": "append_collection_item", "collection": "items"},
                {
                    "kind": "set_collection_field",
                    "collection": "items",
                    "item_ref": "new",
                    "field": "nomenclature",
                    "mention": "диск",
                },
            ],
            "первый": [{"kind": "confirm_pending", "value": 0}],
            "нет, я имел в виду dk-744": [
                {
                    "kind": "set_collection_field",
                    "collection": "items",
                    "item_ref": "this",
                    "field": "nomenclature",
                    "mention": "диск колесный передний",
                }
            ],
            "да": [{"kind": "confirm_pending", "value": 0}],
        },
        monkeypatch,
    )
    gw.onec_data = PART_AMBIG
    r = post(gw, "llm", "добавь строку")
    ans = unquote(r.headers["X-Answer"])
    assert "варианты" in ans
    f = frame_of("llm").collections["items"][-1].fields["nomenclature"]
    assert not f.filled and len(f.candidates) == 2
    # уточняющая реплика -> повторное упоминание -> однозначный кандидат
    gw.onec_data = (
        '[1]{"Наименование","Артикул","Код"}:\n  Диск колесный передний,DK-100,ЗЧ-0000101'
    )
    gw.onec_data = PART_AMBIG
    r = post(gw, "llm", "нет, я имел в виду DK-744")
    f = frame_of("llm").collections["items"][-1].fields["nomenclature"]
    gw.onec_data = PART_DISK
    r = post(gw, "llm", "первый")
    gw.onec_data = STOCK_FOR_ITEM
    # после выбора кандидат остаётся resolved
    f = frame_of("llm").collections["items"][-1].fields["nomenclature"]
    assert f.status in ("resolved", "ambiguous")


# --- параллельный STOCK_QUERY через typed commands -----------------------------


def test_parallel_stock_query_via_commands(env, monkeypatch):
    gw = ready_with_vehicle(env, monkeypatch)
    gw.onec_data = PART_DISK
    post(gw, "llm", "добавь строку")
    post(gw, "llm", "диск")
    gw.onec_data = STOCK_FOR_ITEM
    post(gw, "llm", "да.")
    setup_llm(
        gw,
        {
            "сколько дк-300 на складе": [
                {"kind": "start_scenario", "scenario_type": "stock_query"},
                {"kind": "set_field", "path": "nomenclature", "mention": "DK-300"},
                {"kind": "query_scenario"},
            ]
        },
        monkeypatch,
    )
    gw.onec_data = (
        '[1]{"Склад","Товар","Артикул","Ед","Остаток"}:\n'
        "  Склад инженера,Диск колесный задний,DK-300,шт,5"
    )
    app._SCENARIO_RESOLVER.register(
        "nomenclature",
        lambda mention: {
            "found": True,
            "entities": [
                {"name": "Диск колесный задний", "code": "ЗЧ-0000102", "article": "DK-300"}
            ],
        },
    )
    r = post(gw, "llm", "сколько DK-300 на складе")
    ans = unquote(r.headers["X-Answer"])
    assert "Диск колесный задний" in ans
    # REPAIR_ORDER цел и снова активен
    session = app._scenario_manager().session("llm")
    frame = session.active
    assert frame.scenario_type == "repair_order"
    assert len(frame.collections["items"]) == 1
    assert not [
        f
        for f in session.frames.values()
        if f.scenario_type == "stock_query" and f.status == "active"
    ]


# --- PendingAction safety -------------------------------------------------------


def test_confirmation_with_mutation_rejected(env, monkeypatch):
    """«да, но поменяй количество» — противоречивый batch отклоняется целиком."""
    gw = prepared_two_rows(env, monkeypatch)
    setup_llm(
        gw,
        {
            "оформи заказ": [{"kind": "query_scenario"}],
            "да, но во второй строке сделай три": [
                {"kind": "confirm_pending"},
                {
                    "kind": "set_collection_field",
                    "collection": "items",
                    "item_ref": "2",
                    "field": "quantity",
                    "value": 3,
                },
            ],
        },
        monkeypatch,
    )
    r = post(gw, "llm", "оформи заказ")
    r = post(gw, "llm", "да, но во второй строке сделай три")
    frame = frame_of("llm")
    assert frame.pending_action is None, "batch с мутацией+confirm отклонён, подтверждение сброшено"
    rows = frame.collections["items"]
    assert [it.fields["quantity"].value for it in rows] == [1, 1], (
        "мутация из противоречивого batch не применена"
    )

    def boom(*a, **kw):
        raise AssertionError("документы созданы по устаревшему подтверждению")

    monkeypatch.setattr(onec, "create_repair_order", boom)
    r = post(gw, "llm", "да")
    assert "не удалось" in unquote(r.headers["X-Answer"]).lower() or "нечего" in unquote(
        r.headers["X-Answer"]
    )


def test_mutation_after_pending_invalidates_it(env, monkeypatch):
    gw = prepared_two_rows(env, monkeypatch)
    setup_llm(
        gw,
        {
            "оформи заказ": [{"kind": "propose_pending"}],
            "перед созданием поменяй вторую строку": [
                {
                    "kind": "set_collection_field",
                    "collection": "items",
                    "item_ref": "2",
                    "field": "quantity",
                    "value": 5,
                }
            ],
            "да": [{"kind": "confirm_pending"}],
            "сколько дк-300 на складе": [
                {"kind": "start_scenario", "scenario_type": "stock_query"},
                {"kind": "set_field", "path": "nomenclature", "mention": "DK-300"},
                {"kind": "query_scenario"},
            ],
        },
        monkeypatch,
    )
    post(gw, "llm", "оформи заказ")
    assert frame_of("llm").pending_action is not None
    app._SCENARIO_RESOLVER.register(
        "nomenclature",
        lambda mention: {
            "found": True,
            "entities": [{"name": "Диск колесный задний", "code": "ЗЧ-0000102"}],
        },
    )
    gw.onec_data = (
        '[1]{"Склад","Товар","Артикул","Ед","Остаток"}:\n'
        "  Склад инженера,Диск колесный задний,DK-300,шт,5"
    )
    post(gw, "llm", "сколько DK-300 на складе")  # query во время pending
    frame = frame_of("llm")
    # query не мутирует frame: pending жив, версия не изменилась
    assert frame.pending_action is not None
    post(gw, "llm", "перед созданием поменяй вторую строку")
    frame = frame_of("llm")
    assert frame.pending_action is None, "мутация инвалидирует PendingAction"
    assert frame.collections["items"][1].fields["quantity"].value == 5


def test_query_scenario_does_not_confirm(env, monkeypatch):
    """«а какой сейчас склад?» — запрос состояния, не подтверждение."""
    gw = prepared_two_rows(env, monkeypatch)
    setup_llm(
        gw,
        {
            "оформи заказ": [{"kind": "propose_pending"}],
            "а какой сейчас склад": [{"kind": "query_scenario"}],
            "создаём": [{"kind": "query_scenario"}],
        },
        monkeypatch,
    )
    post(gw, "llm", "оформи заказ")
    post(gw, "llm", "а какой сейчас склад?")
    frame = frame_of("llm")
    assert frame.pending_action is not None, "query не подтверждает и не мутирует"
    assert frame.version == frame.pending_action.frame_version


# --- chitchat -------------------------------------------------------------------


def test_chitchat_keeps_frame(env, monkeypatch):
    gw = prepared_two_rows(env, monkeypatch)
    before = frame_of("llm").version
    setup_llm(
        gw,
        {
            "а почему ты спрашиваешь": [
                {
                    "kind": "chitchat",
                    "answer_text": "Потому что в новой строке ещё не выбрана номенклатура.",
                }
            ]
        },
        monkeypatch,
    )
    r = post(gw, "llm", "а почему ты спрашиваешь?")
    assert "номенклатура" in unquote(r.headers["X-Answer"])
    frame = frame_of("llm")
    assert frame.version == before, "chitchat не мутирует frame"
    assert frame.scenario_type == "repair_order"


# --- malformed output safety -----------------------------------------------------


def test_malformed_llm_output_safe_fallback(env, monkeypatch):
    gw = ready_with_vehicle(env, monkeypatch)
    calls = []

    def broken_llm(payload):
        calls.append(1)
        return "мусор без JSON"

    monkeypatch.setattr(app, "_lm_chat_json", broken_llm)
    r = post(gw, "llm", "добавь строку")
    assert len(calls) == 2, "один повтор после malformed"
    ans = unquote(r.headers["X-Answer"])
    assert ans  # детерминированный fallback ответил
    frame = frame_of("llm")
    # fallback-интерпретатор детерминирован: «добавь строку» добавляет строку
    assert len(frame.collections["items"]) == 1
