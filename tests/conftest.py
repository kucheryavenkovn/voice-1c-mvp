"""Shared pytest fixtures.

The gateway code lives in ``voice-gateway/`` (a hyphenated folder, not a package),
so we put it on sys.path and import modules directly: ``import app``, ``import onec``.

All external dependencies (STT, TTS, LM Studio, 1C MCP Toolkit) are replaced by a
single FakeRequests transport routed by URL, so the full /ask and /ask-text flow can
be tested in-process — no GPU, no Docker, no 1С, no LM Studio.
"""

import json
import pathlib
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "voice-gateway"))

import pytest


def wav_bytes(seconds: float = 0.1, rate: int = 22050) -> bytes:
    """A tiny but valid silent WAV — stands in for Piper/answer output."""
    n = int(rate * seconds)
    data = b"\x00\x00" * n
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


# --- canned 1C table responses (real shapes observed from the 1C MCP Toolkit) ---
ONEC_SINGLE = (
    '[1]{"Склад","Товар","Артикул","Ед","Остаток"}:\n'
    "  Центральный склад,Молоко 3.2%,Арт-1,шт,20\n"
    "  Торговый зал,Молоко 3.2%,Арт-1,шт,30"
)
ONEC_MULTI = (
    '[3]{"Склад","Товар","Артикул","Ед","Остаток"}:\n'
    "  Склад1,Барбарис,Арт-7777,кг,10\n"
    '  "Магазин \\"Продукты\\"",Молоко,Арт-777788,шт,5\n'
    "  Склад1,Соковыжималка,СО-77777,шт,2"
)
ONEC_EMPTY = "[0]:"
ONEC_DECIMAL = (
    '[1]{"Склад","Товар","Артикул","Ед","Остаток"}:\n  Западный склад,Барбарис,Арт-7777,кг,143.25'
)


class FakeResp:
    def __init__(self, status=200, json_data=None, content=b""):
        self.status_code = status
        self._json = json_data if json_data is not None else {}
        self.content = content
        self.headers = {}

    def json(self):
        return self._json

    @property
    def text(self):
        if self.content:
            return self.content.decode("utf-8", "ignore")
        return json.dumps(self._json, ensure_ascii=False)

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests as _r

            raise _r.HTTPError(f"fake HTTP {self.status_code}")


class State:
    """Mutable per-test state consumed by FakeRequests routes."""


class FakeRequests:
    """Replaces `requests` in app.py and onec.py. Routes by URL substring."""

    def __init__(self, st: State):
        self.st = st

    def _do(self, method, url, **kw):
        st, u = self.st, url.lower()
        import requests as _r

        if "/health" in u:
            return FakeResp(200, {"ok": True})
        if method == "GET" and "/models" in u:
            return FakeResp(200, {"data": [{"id": "test-model"}]})
        if method == "POST" and "/chat/completions" in u:
            if getattr(st, "lm_timeout", False):
                raise _r.exceptions.ReadTimeout("fake LM timeout")
            return FakeResp(200, {"choices": [{"message": {"content": st.lm_raw}}]})
        if method == "POST" and "/stt" in u:
            if getattr(st, "stt_fail", False):
                return FakeResp(500, {"error": "stt down"})
            return FakeResp(200, {"text": st.stt_text, "language": "ru"})
        if method == "POST" and "/tts" in u:
            if getattr(st, "tts_fail", False):
                return FakeResp(500, {"error": "tts down"})
            return FakeResp(200, content=st.tts_bytes)
        if method == "POST" and "/execute_query" in u:
            if getattr(st, "onec_timeout", False):
                raise _r.exceptions.ReadTimeout("fake 1C timeout")
            if getattr(st, "onec_fail", False):
                return FakeResp(200, {"success": False, "error": "fake 1c error"})
            return FakeResp(200, {"success": True, "data": st.onec_data})
        if method == "GET" and "/api/stock" in u:
            return FakeResp(200, st.mock_stock)
        return FakeResp(404, {"error": f"no fake route {method} {url}"})

    def post(self, url, **kw):
        return self._do("POST", url, **kw)

    def get(self, url, **kw):
        return self._do("GET", url, **kw)


@pytest.fixture
def gw(monkeypatch):
    """A live FastAPI TestClient with all externals mocked. Mutate `state` per test."""
    import app
    import onec

    st = State()
    st.stt_text = "сколько молока?"
    st.stt_fail = False
    st.lm_raw = json.dumps({"action": "get_stock", "item": "молоко"})
    st.onec_data = ONEC_SINGLE
    st.onec_fail = False
    st.tts_bytes = wav_bytes()
    st.tts_fail = False
    st.mock_stock = {
        "item": "молоко",
        "found": True,
        "quantity": 42,
        "message": "Остаток mock: 42.",
    }

    fake = FakeRequests(st)
    monkeypatch.setattr(onec, "requests", fake)
    monkeypatch.setattr(app, "requests", fake)
    monkeypatch.setattr(app, "LM_MODEL", "test-model")

    from fastapi.testclient import TestClient

    with TestClient(app.app) as client:
        st.client = client
        yield st
