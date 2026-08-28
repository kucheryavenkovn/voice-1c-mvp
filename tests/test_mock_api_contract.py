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


# --- заказ запчастей: POST /api/orders должен держать тот же контракт, что 1С-бэкенд ---

ORDER_KEYS = {"item", "found", "quantity", "order_number", "status", "message", "source"}


def test_order_created_contract():
    c = TestClient(mock_app.app)
    r = c.post("/api/orders", json={"item": "молоко", "quantity": 5})
    assert r.status_code == 200
    j = r.json()
    assert ORDER_KEYS <= set(j)
    assert j["found"] is True and j["quantity"] == 5
    assert j["source"] == "mock"
    assert __import__("re").fullmatch(r"ЗР-\d{7}", j["order_number"])
    assert "Потребность зарегистрирована" in j["message"]


def test_order_numbers_increment():
    c = TestClient(mock_app.app)
    n1 = c.post("/api/orders", json={"item": "молоко"}).json()["order_number"]
    n2 = c.post("/api/orders", json={"item": "молоко"}).json()["order_number"]
    assert n1 != n2


def test_order_unknown_item_not_found():
    c = TestClient(mock_app.app)
    j = c.post("/api/orders", json={"item": "несуществующийтовар", "quantity": 2}).json()
    assert j["found"] is False
    assert j["order_number"] is None
    assert "не найден" in j["message"]


def test_order_out_of_stock_goes_to_supplier():
    c = TestClient(mock_app.app)
    j = c.post("/api/orders", json={"item": "77777", "quantity": 4}).json()
    assert j["found"] is True
    assert "поставщику" in j["message"]


def test_order_bad_quantity_defaults_to_one():
    c = TestClient(mock_app.app)
    j = c.post("/api/orders", json={"item": "молоко", "quantity": -7}).json()
    assert j["quantity"] == 1


def test_orders_listed():
    c = TestClient(mock_app.app)
    c.post("/api/orders", json={"item": "молоко", "quantity": 1})
    j = c.get("/api/orders").json()
    assert j["count"] >= 1
    assert any(o["status"] == "Потребность зарегистрирована" for o in j["orders"])


def test_build_answer_accepts_mock_order_shape():
    c = TestClient(mock_app.app)
    order = c.post("/api/orders", json={"item": "молоко", "quantity": 5}).json()
    ans = app.build_answer(
        "текст", {"action": "order_part", "item": "молоко", "quantity": 5}, order
    )
    assert ans == order["message"]
