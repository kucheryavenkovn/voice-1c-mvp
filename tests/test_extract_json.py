"""Tests for LLM JSON extraction (markdown fences, prose, malformed) and answer builder."""
import app


def test_extract_plain_json():
    assert app.extract_json('{"action":"get_stock","item":"молоко"}') == {
        "action": "get_stock", "item": "молоко"
    }


def test_extract_fenced_json():
    assert app.extract_json('```json\n{"action":"get_stock","item":"молоко"}\n```') == {
        "action": "get_stock", "item": "молоко"
    }


def test_extract_json_among_prose():
    assert app.extract_json('Вот: {"action":"unknown","item":null} ок') == {
        "action": "unknown", "item": None
    }


def test_extract_empty_and_garbage():
    assert app.extract_json("") is None
    assert app.extract_json("просто текст без JSON") is None


def test_build_answer_found_uses_message():
    ans = app.build_answer("t", {"action": "get_stock", "item": "молоко"},
                           {"found": True, "message": "ОСТАТОК"})
    assert ans == "ОСТАТОК"


def test_build_answer_not_found():
    ans = app.build_answer("t", {"action": "get_stock", "item": "x"},
                           {"found": False, "message": "не найдено"})
    assert ans == "не найдено"


def test_build_answer_get_stock_no_stock():
    ans = app.build_answer("t", {"action": "get_stock", "item": "x"}, None)
    assert "x" in ans and "не найден" in ans


def test_build_answer_unknown_action_help():
    ans = app.build_answer("t", {"action": "unknown", "item": None}, None)
    assert "остатк" in ans  # help text mentions остатки
