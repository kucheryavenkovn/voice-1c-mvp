"""Tests for LLM JSON extraction (markdown fences, prose, malformed) and answer builder."""

import app


def test_extract_plain_json():
    assert app.extract_json('{"action":"get_stock","item":"молоко"}') == {
        "action": "get_stock",
        "item": "молоко",
    }


def test_extract_fenced_json():
    assert app.extract_json('```json\n{"action":"get_stock","item":"молоко"}\n```') == {
        "action": "get_stock",
        "item": "молоко",
    }


def test_extract_json_among_prose():
    assert app.extract_json('Вот: {"action":"unknown","item":null} ок') == {
        "action": "unknown",
        "item": None,
    }


def test_extract_empty_and_garbage():
    assert app.extract_json("") is None
    assert app.extract_json("просто текст без JSON") is None


def test_build_answer_found_uses_message():
    ans = app.build_answer(
        "t", {"action": "get_stock", "item": "молоко"}, {"found": True, "message": "ОСТАТОК"}
    )
    assert ans == "ОСТАТОК"


def test_build_answer_not_found():
    ans = app.build_answer(
        "t", {"action": "get_stock", "item": "x"}, {"found": False, "message": "не найдено"}
    )
    assert ans == "не найдено"


def test_build_answer_get_stock_no_stock():
    ans = app.build_answer("t", {"action": "get_stock", "item": "x"}, None)
    assert "x" in ans and "не найден" in ans


def test_build_answer_unknown_action_help():
    ans = app.build_answer("t", {"action": "unknown", "item": None}, None)
    assert "остатк" in ans  # help text mentions остатки


def test_build_answer_list_stock_enumerates():
    stock = {
        "found": True,
        "items": [
            {
                "name": "Сахарный песок (весовой)",
                "article": "45463728",
                "quantity": 385,
                "warehouses": [],
            },
            {
                "name": "Сахарный песок в пачках",
                "article": "Арт-88888",
                "quantity": 110,
                "warehouses": [],
            },
            {
                "name": "Сахарный песок (в упаковках)",
                "article": "",
                "quantity": 95,
                "warehouses": [],
            },
        ],
    }
    ans = app.build_answer("t", {"action": "list_stock", "item": "сахар"}, stock)
    assert ans.startswith("Товары с остатком по 'сахар' (3 позиции):")
    assert "Сахарный песок (весовой) (арт. 45463728) — 385" in ans
    assert "Сахарный песок в пачках (арт. Арт-88888) — 110" in ans


def test_build_answer_list_stock_empty():
    stock = {"found": False, "items": []}
    ans = app.build_answer("t", {"action": "list_stock", "item": "xyz"}, stock)
    assert "нет" in ans
