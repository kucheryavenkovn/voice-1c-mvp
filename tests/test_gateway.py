"""Integration: voice-gateway HTTP endpoints with STT/TTS/LM/1C all mocked."""

import json
from urllib.parse import unquote

import app
from conftest import ONEC_MULTI, ONEC_SINGLE


def test_health(gw):
    r = gw.client.get("/health")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["stock_backend"] in ("1c", "mock")


def test_static_pages(gw):
    assert gw.client.get("/").status_code == 200
    assert gw.client.get("/diag").status_code == 200


def test_speak_returns_wav(gw):
    r = gw.client.post("/speak", json={"text": "привет"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content.startswith(b"RIFF")


def test_transcribe(gw):
    r = gw.client.post("/transcribe", files={"file": ("a.webm", b"audio", "audio/webm")})
    assert r.status_code == 200
    assert r.json()["text"] == "сколько молока?"


def test_ask_text_single_item(gw):
    gw.onec_data = ONEC_SINGLE
    gw.lm_raw = json.dumps({"action": "get_stock", "item": "молоко"})
    r = gw.client.post("/ask-text", json={"text": "сколько молока?"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content.startswith(b"RIFF")
    ans = unquote(r.headers["X-Answer"])
    assert "всего 50 шт" in ans and "По складам" in ans
    intent = json.loads(unquote(r.headers["X-Intent"]))
    assert intent["action"] == "get_stock"


def test_ask_text_article_multi(gw):
    gw.onec_data = ONEC_MULTI
    gw.lm_raw = json.dumps({"action": "get_stock", "item": "7777"})
    r = gw.client.post("/ask-text", json={"text": "остаток по 7777"})
    assert r.status_code == 200
    ans = unquote(r.headers["X-Answer"])
    # heterogeneous units (кг + шт) → per-unit subtotals + per-warehouse
    assert "10 кг" in ans and "7 шт" in ans and "По складам" in ans
    assert "всего" not in ans
    assert (
        r.headers["X-Question"]
        == "%D0%BE%D1%81%D1%82%D0%B0%D1%82%D0%BE%D0%BA%20%D0%BF%D0%BE%207777"
    )


def test_ask_text_unknown_intent(gw):
    gw.lm_raw = json.dumps({"action": "unknown", "item": None})
    r = gw.client.post("/ask-text", json={"text": "привет"})
    assert r.status_code == 200
    assert "остатк" in unquote(r.headers["X-Answer"])


def test_ask_full_voice_turn(gw):
    gw.stt_text = "сколько молока?"
    gw.onec_data = ONEC_SINGLE
    r = gw.client.post("/ask", files={"file": ("a.webm", b"audio", "audio/webm")})
    assert r.status_code == 200
    assert unquote(r.headers["X-Question"]) == "сколько молока?"
    assert "Молоко 3.2%" in unquote(r.headers["X-Answer"])


def test_ask_empty_stt_returns_400(gw):
    gw.stt_text = ""
    r = gw.client.post("/ask", files={"file": ("a.webm", b"audio", "audio/webm")})
    assert r.status_code == 400


def test_fallback_to_mock_when_1c_down(gw):
    gw.onec_fail = True  # 1C error → fallback to mock-api
    r = gw.client.post("/ask-text", json={"text": "сколько молока?"})
    assert r.status_code == 200
    assert "42" in unquote(r.headers["X-Answer"])


def test_tts_failure_502(gw):
    gw.tts_fail = True
    r = gw.client.post("/ask-text", json={"text": "сколько молока?"})
    assert r.status_code == 502


def test_ask_text_order_part(gw):
    gw.lm_raw = json.dumps({"action": "order_part", "item": "уплотнитель", "quantity": 3})
    r = gw.client.post("/ask-text", json={"text": "закажи три уплотнителя"})
    assert r.status_code == 200
    ans = unquote(r.headers["X-Answer"])
    assert "ТД00-000012" in ans and "Потребность зарегистрирована" in ans
    intent = json.loads(unquote(r.headers["X-Intent"]))
    assert intent["action"] == "order_part"


def test_order_part_fallback_to_mock_when_1c_blocked(gw):
    gw.onec_code_fail = True  # напр., «Записать» в чёрном списке execute_code
    gw.lm_raw = json.dumps({"action": "order_part", "item": "уплотнитель", "quantity": 3})
    r = gw.client.post("/ask-text", json={"text": "закажи три уплотнителя"})
    assert r.status_code == 200
    ans = unquote(r.headers["X-Answer"])
    assert "ЗР-0001234" in ans


def test_order_part_not_found(gw):
    gw.onec_code = "NOTFOUND"
    gw.lm_raw = json.dumps({"action": "order_part", "item": "кварцевый генератор", "quantity": 1})
    r = gw.client.post("/ask-text", json={"text": "закажи кварцевый генератор"})
    assert r.status_code == 200
    assert "не найден" in unquote(r.headers["X-Answer"])


def test_chat_general_question(gw):
    gw.lm_raw = json.dumps({"action": "chat", "answer": "Лев Толстой."})
    r = gw.client.post("/ask-text", json={"text": "кто написал войну и мир?"})
    assert r.status_code == 200
    assert unquote(r.headers["X-Answer"]) == "Лев Толстой."
    intent = json.loads(unquote(r.headers["X-Intent"]))
    assert intent["action"] == "chat"


def test_chat_without_answer_falls_back_to_help(gw):
    gw.lm_raw = json.dumps({"action": "chat", "answer": ""})
    r = gw.client.post("/ask-text", json={"text": "ммм"})
    assert r.status_code == 200
    assert "остатк" in unquote(r.headers["X-Answer"])


def test_request_part_flow(gw):
    gw.lm_raw = json.dumps(
        {"action": "request_part", "item": "диск задний", "vehicle": "кировец", "quantity": 1}
    )
    gw.onec_code = "B1|000000008|Трактор Кировец К-744Р|Диск колесный задний|DK-300|1"
    r = gw.client.post("/ask-text", json={"text": "нужен задний диск для кировца"})
    assert r.status_code == 200
    ans = unquote(r.headers["X-Answer"])
    assert "000000008" in ans and "складе инженера" in ans
    intent = json.loads(unquote(r.headers["X-Intent"]))
    assert intent["action"] == "request_part"
    assert intent["vehicle"] == "кировец"


def test_request_part_backend_error_still_answers(gw):
    gw.onec_code_fail = True
    gw.lm_raw = json.dumps(
        {"action": "request_part", "item": "диск", "vehicle": "кировец", "quantity": 1}
    )
    r = gw.client.post("/ask-text", json={"text": "нужен диск для кировца"})
    assert r.status_code == 200
    assert "Не удалось" in unquote(r.headers["X-Answer"])


# --- сущность «чат»: история + уточнения ---


def test_chat_history_stored(gw):
    gw.lm_raw = json.dumps({"action": "chat", "answer": "Привет."})
    gw.client.post("/ask-text", json={"text": "привет", "chat_id": "t1"})
    h = app._CHATS.get("t1")
    assert h is not None and len(h) == 2
    assert h[0]["role"] == "user" and h[0]["content"] == "привет"
    assert h[1]["role"] == "assistant" and h[1]["content"] == "Привет."
    # анонимный запрос историю не пишет
    gw.client.post("/ask-text", json={"text": "пока"})
    assert len(app._CHATS.get("t1")) == 2


def test_chat_history_trimmed_to_limit(gw):
    gw.lm_raw = json.dumps({"action": "chat", "answer": "ок"})
    for i in range(10):
        gw.client.post("/ask-text", json={"text": f"вопрос {i}", "chat_id": "t2"})
    assert len(app._CHATS["t2"]) == app._CHAT_LIMIT


def test_clarify_answer(gw):
    gw.lm_raw = json.dumps({"action": "clarify", "question": "Для какой техники нужна запчасть?"})
    r = gw.client.post("/ask-text", json={"text": "нужен диск", "chat_id": "t3"})
    assert r.status_code == 200
    ans = unquote(r.headers["X-Answer"])
    assert ans == "Для какой техники нужна запчасть?"
    # уточнение тоже попадает в историю (чтобы «да» резолвилось)
    assert app._CHATS["t3"][1]["content"] == ans


# --- шаги подтверждения: lookup_vehicle / lookup_parts ---


def test_lookup_vehicle_single_confirms(gw):
    gw.lm_raw = json.dumps({"action": "lookup_vehicle", "vehicle": "кировец"})
    gw.onec_data = (
        '[1]{"Наименование"}:\n  Трактор Кировец К-744Р Гос. № А123ВС04 VIN XTA00000000012345'
    )
    r = gw.client.post("/ask-text", json={"text": "кировец", "chat_id": "lv1"})
    ans = unquote(r.headers["X-Answer"])
    assert "Нашёл технику" in ans and "Это она?" in ans
    assert "VIN XTA" in ans  # VIN прозвучал


def test_lookup_vehicle_not_found_negative(gw):
    gw.lm_raw = json.dumps({"action": "lookup_vehicle", "vehicle": "камаз"})
    gw.onec_data = "[0]:"
    r = gw.client.post("/ask-text", json={"text": "камаз", "chat_id": "lv2"})
    ans = unquote(r.headers["X-Answer"])
    assert "не найдена" in ans and "не обрабатываем" in ans


def test_lookup_parts_variants(gw):
    gw.lm_raw = json.dumps({"action": "lookup_parts", "item": "диск", "vehicle": "кировец"})
    gw.onec_data = (
        '[3]{"Наименование","Артикул"}:\n'
        "  Диск колесный передний,DK-100\n"
        "  Диск колесный задний,DK-300\n"
        "  Диск колесный усиленный К-744,DK-744-02"
    )
    r = gw.client.post("/ask-text", json={"text": "диск", "chat_id": "lv3"})
    ans = unquote(r.headers["X-Answer"])
    assert "варианты" in ans and "DK-100" in ans and "Какой нужен?" in ans
