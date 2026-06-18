"""Contract tests against golden 1C MCP Toolkit responses (tests/fixtures/1c/*.txt).

These guard the parser against any change in the toolkit's table serialization.
Fixtures are recorded from a live 1C by scripts/record_1c_fixtures.ps1; the tests
themselves need no 1C and no network.
"""

import pathlib

import onec

FIX = pathlib.Path(__file__).parent / "fixtures" / "1c"


def _read(name):
    return (FIX / f"{name}.txt").read_text(encoding="utf-8")


PARSER_CASES = [
    ("select_1", ["Результат"], 1),
    ("milk_aggregated", ["Склад", "Остаток"], 5),
    ("article_7777", ["Склад", "Товар", "Артикул", "Остаток"], 13),
    ("empty_result", [], 0),
    ("barbaris_decimal", ["Склад", "Товар", "Артикул", "Остаток"], 5),
    ("catalog_name_article", ["Наименование", "Артикул"], 6),
]


def test_fixtures_parse_columns_and_rows():
    for name, cols, nrows in PARSER_CASES:
        c, rows = onec._parse_table(_read(name))
        assert c == cols, f"{name}: cols {c} != {cols}"
        assert len(rows) == nrows, f"{name}: {len(rows)} != {nrows}"


def test_fixture_escaped_quotes():
    _, rows = onec._parse_table(_read("milk_aggregated"))
    assert ['Магазин "Продукты"', 100] in rows


def test_fixture_empty_article_is_string_not_none():
    _, rows = onec._parse_table(_read("catalog_name_article"))
    assert rows[-1][1] == ""  # Артикул "" stays empty string
    assert rows[0][1] == "[OGRN-00001]"  # anonymization token as plain string


def test_fixture_decimal_value():
    _, rows = onec._parse_table(_read("barbaris_decimal"))
    assert rows[0][3] == 143.25
    assert isinstance(rows[0][3], float)


def test_fixture_decimal_in_multi():
    _, rows = onec._parse_table(_read("article_7777"))
    assert rows[0] == ["Западный склад", "Барбарис (конфеты)", "Арт-7777", 143.25]


def test_query_article_7777_groups_three_items(gw):
    gw.onec_data = _read("article_7777")
    res = onec.query_stock("7777")
    assert res["found"] is True
    assert len(res["items"]) == 3
    assert res["quantity"] is None  # mixed units → no cross-item sum
    names = {it["name"] for it in res["items"]}
    assert {
        "Барбарис (конфеты)",
        'Молоко "Домик в деревне" 1.5%',
        "Соковыжималка  [SWIFT-00006] JE 102",
    } <= names
    barb = next(it for it in res["items"] if it["name"].startswith("Барбарис"))
    assert barb["article"] == "Арт-7777"
    assert barb["quantity"] == 210.25  # 143.25 + 27 + 20 + 15 + 5


def test_query_barbaris_single_item_real(gw):
    gw.onec_data = _read("barbaris_decimal")
    res = onec.query_stock("барбарис")
    assert len(res["items"]) == 1
    assert res["items"][0]["quantity"] == 210.25
    assert res["quantity"] == 210.25
    assert "210.25" in res["message"]
