"""Integration: voice-gateway HTTP endpoints with STT/TTS/LM/1C all mocked."""

import json
from urllib.parse import unquote

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
    assert "всего 50" in ans and "Молоко 3.2%" in ans
    intent = json.loads(unquote(r.headers["X-Intent"]))
    assert intent["action"] == "get_stock"


def test_ask_text_article_multi(gw):
    gw.onec_data = ONEC_MULTI
    gw.lm_raw = json.dumps({"action": "get_stock", "item": "7777"})
    r = gw.client.post("/ask-text", json={"text": "остаток по 7777"})
    assert r.status_code == 200
    ans = unquote(r.headers["X-Answer"])
    # heterogeneous units (кг + шт) → per-unit subtotals, no 'всего' sum
    assert "10 кг" in ans and "7 шт" in ans
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
