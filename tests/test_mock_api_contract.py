"""Contract parity tests: mock-api must return the SAME shape as the 1C backend
(onec.query_stock), so build_answer and the 1C-down fallback work uniformly.
"""

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_module(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# mock-api/app.py is loaded under a unique name so `import app` stays the gateway.
mock_app = _load_module(ROOT / "mock-api" / "app.py", "mock_api_app")

import app  # gateway (voice-gateway is on sys.path via conftest)
from fastapi.testclient import TestClient

# the keys/types both backends must expose
CONTRACT_KEYS = {"item", "found", "quantity", "items", "warehouses", "message", "source"}


def test_mock_api_get_found():
    c = TestClient(mock_app.app)
    r = c.get("/api/stock", params={"item": "молоко"})
    assert r.status_code == 200
    j = r.json()
    assert CONTRACT_KEYS <= set(j)
    assert j["found"] is True
    assert j["quantity"] == 42
    assert j["source"] == "mock"
    assert len(j["items"]) == 1 and j["items"][0]["quantity"] == 42
    assert "42" in j["message"]


def test_mock_api_post_not_found():
    c = TestClient(mock_app.app)
    r = c.post("/api/stock", json={"item": "несуществующийтовар"})
    assert r.status_code == 200
    j = r.json()
    assert j["found"] is False
    assert j["items"] == []
    assert j["quantity"] is None


def test_mock_api_health():
    c = TestClient(mock_app.app)
    assert c.get("/health").json()["ok"] is True


def test_parity_keys_match_onec_shape():
    """mock-api JSON keys equal the 1C backend keys (no contract drift)."""
    c = TestClient(mock_app.app)
    mock_keys = set(c.get("/api/stock", params={"item": "молоко"}).json().keys())
    onec_keys = {"item", "found", "quantity", "items", "warehouses", "message", "source"}
    assert mock_keys == onec_keys


def test_build_answer_accepts_mock_shape():
    """build_answer must consume a real mock-api response without special-casing."""
    c = TestClient(mock_app.app)
    stock = c.get("/api/stock", params={"item": "молоко"}).json()
    ans = app.build_answer("текст", {"action": "get_stock", "item": "молоко"}, stock)
    assert ans == stock["message"]
