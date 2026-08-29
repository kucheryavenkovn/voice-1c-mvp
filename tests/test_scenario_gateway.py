"""Регресс C-SCENARIO-FRAMES на уровне HTTP API (/ask-text): кейсы 1-6, 10.

Диалог ведётся ScenarioFrame'ом; stage — только compat-проекция для UI/тестов.
"""

import json
from urllib.parse import unquote

import app
import onec

VEHICLE_KIROVETS = '[1]{"Наименование","Код"}:\n  Трактор Кировец К-744Р Гос. № А123ВС04,ТР-0000008'
VEHICLE_MTZ = '[1]{"Наименование","Код"}:\n  Трактор МТЗ-82,ТР-0000010'
PART_DISK = '[1]{"Наименование","Артикул","Код"}:\n  Диск колесный задний,DK-300,ЗЧ-0000102'
PART_FILTER = '[1]{"Наименование","Артикул","Код"}:\n  Фильтр масляный,ФЛ-40,ЗЧ-0000110'
STOCK_DISK = '[1]{"Склад","Товар","Артикул","Ед","Остаток"}:\n  Склад инженера,Диск колесный задний,DK-300,шт,5'
STOCK_FOR_ITEM = '[1]{"Склад","Ост"}:\n  Склад инженера,5'
STOCK_EMPTY = "[0]:"


def ladder_ready(gw, cid: str):
    """Техника подтверждена, одна позиция (Диск, 1 шт, склад инженера) в корзине."""
    gw.lm_raw = json.dumps({"action": "request_part", "item": "диск", "vehicle": None})
    gw.onec_data = VEHICLE_KIROVETS
    gw.client.post("/ask-text", json={"text": "нужен диск", "chat_id": cid})
    gw.client.post("/ask-text", json={"text": "кировец", "chat_id": cid})
    gw.onec_data = PART_DISK
    gw.client.post("/ask-text", json={"text": "да", "chat_id": cid})
    gw.onec_data = STOCK_FOR_ITEM
    r = gw.client.post("/ask-text", json={"text": "да", "chat_id": cid})
    assert "Добавил в заказ" in unquote(r.headers["X-Answer"])


def frame_of(cid: str):
    session = app._scenario_manager().session(cid)
    return session.active


def add_second_row(gw, cid: str):
    gw.onec_data = PART_FILTER
    gw.client.post("/ask-text", json={"text": "добавь строку", "chat_id": cid})
    gw.client.post("/ask-text", json={"text": "фильтр", "chat_id": cid})
    gw.onec_data = STOCK_EMPTY
    gw.client.post("/ask-text", json={"text": "да", "chat_id": cid})


def test_case1_add_row_does_not_search_parts(gw, monkeypatch):
    cid = "sc1"
    ladder_ready(gw, cid)
    calls = []
    original = app.call_lookup_parts

    def spy(item, vehicle=None):
        calls.append(item)
        return original(item, vehicle)

    monkeypatch.setattr(app, "call_lookup_parts", spy)
    r = gw.client.post("/ask-text", json={"text": "добавь строку", "chat_id": cid})
    ans = unquote(r.headers["X-Answer"])
    assert "Добавил новую строку" in ans
    assert calls == [], "«добавь строку» не должно искаться как номенклатура"
    frame = frame_of(cid)
    assert len(frame.collections["items"]) == 2
    assert frame.focus.path.endswith(".nomenclature")


def test_case2_second_row_quantity(gw):
    cid = "sc2"
    ladder_ready(gw, cid)
    add_second_row(gw, cid)
    ids = [it.item_id for it in frame_of(cid).collections["items"]]
    r = gw.client.post(
        "/ask-text", json={"text": "во второй строке поставь три штуки", "chat_id": cid}
    )
    assert "в строке 2" in unquote(r.headers["X-Answer"])
    frame = frame_of(cid)
    rows = frame.collections["items"]
    assert [it.fields["quantity"].value for it in rows] == [1, 3]
    assert [it.item_id for it in rows] == ids, "item_id стабильны"


def test_case3_remove_first_row(gw):
    cid = "sc3"
    ladder_ready(gw, cid)
    add_second_row(gw, cid)
    ids = [it.item_id for it in frame_of(cid).collections["items"]]
    r = gw.client.post("/ask-text", json={"text": "удали первую строку", "chat_id": cid})
    assert "Удалил строку" in unquote(r.headers["X-Answer"])
    rows = frame_of(cid).collections["items"]
    assert [it.item_id for it in rows] == [ids[1]]
    assert rows[0].fields["nomenclature"].entity.name == "Фильтр масляный"
    st = app._DIALOG_STATES[cid]
    assert [it["part"]["name"] for it in st["items"]] == ["Фильтр масляный"]


def test_case4_vehicle_correction_invalidates_dependent_part(gw):
    cid = "sc4"
    ladder_ready(gw, cid)
    gw.onec_data = VEHICLE_MTZ
    r = gw.client.post("/ask-text", json={"text": "нет, техника всё-таки МТЗ-82", "chat_id": cid})
    assert "Нашёл технику: Трактор МТЗ-82. Это она?" in unquote(r.headers["X-Answer"])
    gw.client.post("/ask-text", json={"text": "да", "chat_id": cid})
    frame = frame_of(cid)
    assert frame.fields["vehicle"].entity.code == "ТР-0000010"
    row = frame.collections["items"][0]
    assert row.fields["nomenclature"].status == "missing", "номенклатура инвалидируется"
    assert row.fields["quantity"].value == 1, "независимое количество сохраняется"


def test_case5_parallel_stock_query_keeps_repair(gw, monkeypatch):
    cid = "sc5"
    ladder_ready(gw, cid)
    app._SCENARIO_RESOLVER.register(
        "nomenclature",
        lambda mention: {
            "found": True,
            "entities": [
                {"name": "Диск колесный задний", "code": "ЗЧ-0000102", "article": "DK-300"}
            ],
        },
    )
    gw.lm_raw = json.dumps(
        {"action": "get_stock", "item": "диск колесный задний", "warehouse": None}
    )
    gw.onec_data = STOCK_DISK
    r = gw.client.post(
        "/ask-text", json={"text": "сколько диск колесный задний на складе?", "chat_id": cid}
    )
    ans = unquote(r.headers["X-Answer"])
    assert "Диск колесный задний" in ans
    # REPAIR_ORDER не потерян: позиция на месте, сценарий продолжается
    st = app._DIALOG_STATES[cid]
    assert st["stage"] == "await_part"
    assert [it["part"]["name"] for it in st["items"]] == ["Диск колесный задний"]
    session = app._scenario_manager().session(cid)
    assert not [
        f
        for f in session.frames.values()
        if f.scenario_type == "stock_query" and f.status == "active"
    ]
    # продолжение сценария: добавляем ещё позицию
    gw.lm_raw = json.dumps({"action": "chat", "answer": "хорошо"})
    gw.onec_data = PART_FILTER
    r = gw.client.post("/ask-text", json={"text": "фильтр", "chat_id": cid})
    assert "Нашёл запчасть: Фильтр масляный" in unquote(r.headers["X-Answer"])


def test_case6_correction_after_confirm_invalidates_documents(gw, monkeypatch):
    cid = "sc6"
    ladder_ready(gw, cid)
    r = gw.client.post("/ask-text", json={"text": "оформляй", "chat_id": cid})
    assert "Создаём документы?" in unquote(r.headers["X-Answer"])

    def boom(*a, **kw):
        raise AssertionError("создание документов запрещено после изменения данных")

    monkeypatch.setattr(onec, "create_repair_order", boom)
    r = gw.client.post("/ask-text", json={"text": "поставь три штуки", "chat_id": cid})
    assert "Количество: 3 шт." in unquote(r.headers["X-Answer"])
    frame = frame_of(cid)
    assert frame.pending_action is None, "PendingAction инвалидируется изменением данных"
    assert frame.collections["items"][0].fields["quantity"].value == 3
    # «да» без актуального подтверждения документы не создаёт
    r = gw.client.post("/ask-text", json={"text": "да", "chat_id": cid})
    assert "не удалось" in unquote(r.headers["X-Answer"]).lower() or "подтвер" in unquote(
        r.headers["X-Answer"]
    )


def test_scenario_debug_endpoint(gw):
    cid = "sc7"
    ladder_ready(gw, cid)
    r = gw.client.get("/scenario", params={"chat_id": cid})
    data = r.json()
    assert data["chat_id"] == cid
    active = [f for f in data["frames"] if f["active"]]
    assert len(active) == 1
    frame = active[0]
    assert frame["scenario_type"] == "repair_order"
    assert frame["status"] == "active"
    assert frame["version"] > 1
    assert "resolved" in frame["projection"]
    assert "Кировец" in frame["projection"]


def test_stock_question_on_llm_miss_creates_no_row(gw, monkeypatch):
    """«сколько дисков…» при промахе LLM — детерминированный остаток, без строк."""
    cid = "sc8"
    ladder_ready(gw, cid)
    app._SCENARIO_RESOLVER.register(
        "nomenclature",
        lambda mention: {
            "found": True,
            "entities": [
                {"name": "Диск колесный задний", "code": "ЗЧ-0000102", "article": "DK-300"}
            ],
        },
    )
    gw.lm_raw = "не JSON"  # LLM не распознал -> детерминированная ветка
    monkeypatch.setattr(app, "lm_phrase", lambda canned, state: canned)
    gw.onec_data = STOCK_DISK
    r = gw.client.post("/ask-text", json={"text": "сколько дисков на моём складе?", "chat_id": cid})
    ans = unquote(r.headers["X-Answer"])
    assert "Диск колесный задний" in ans
    frame = frame_of(cid)
    assert len(frame.collections["items"]) == 1, "мусорные строки не создаются"
    st = app._DIALOG_STATES[cid]
    assert st["stage"] == "await_part"


def test_finalize_word_inside_resolution_context(gw):
    """«оформи заказ» при неподтверждённом кандидате — это финализация, а не поиск."""
    cid = "sc9"
    ladder_ready(gw, cid)
    gw.onec_data = PART_DISK
    gw.client.post("/ask-text", json={"text": "диск", "chat_id": cid})  # pending_resolution
    r = gw.client.post("/ask-text", json={"text": "оформи заказ", "chat_id": cid})
    ans = unquote(r.headers["X-Answer"])
    assert "Создаём документы?" in ans
    frame = frame_of(cid)
    assert frame.pending_action is not None
    assert frame.pending_resolution is None or frame.pending_resolution.startswith("items[")
    # вторая строка-черновик осталась без подтверждения -> в корзине одна позиция
    assert len([it for it in frame.collections["items"] if it.fields["nomenclature"].filled]) == 1
