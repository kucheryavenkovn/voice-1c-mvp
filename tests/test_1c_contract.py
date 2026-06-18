"""Contract tests against golden 1C MCP Toolkit responses (tests/fixtures/1c/*.txt).

These guard the parser against any change in the toolkit's table serialization,
and query_stock's grouping/totals against real data. Fixtures are recorded from a
live 1C by scripts/record_1c_fixtures.ps1; the tests need no 1C and no network.
"""

import pathlib

import onec

FIX = pathlib.Path(__file__).parent / "fixtures" / "1c"


def _read(name):
    return (FIX / f"{name}.txt").read_text(encoding="utf-8")


PARSER_CASES = [
    ("select_1", ["Результат"], 1),
    ("milk_aggregated", ["Склад", "Товар", "Артикул", "Ед", "Остаток"], 21),
    ("article_7777", ["Склад", "Товар", "Артикул", "Ед", "Остаток"], 13),
    ("empty_result", [], 0),
    ("barbaris_decimal", ["Склад", "Товар", "Артикул", "Ед", "Остаток"], 5),
    ("catalog_name_article", ["Наименование", "Артикул"], 6),
    ("tv_sharp_multiword", ["Склад", "Товар", "Артикул", "Ед", "Остаток"], 3),
]


def test_fixtures_parse_columns_and_rows():
    for name, cols, nrows in PARSER_CASES:
        c, rows = onec._parse_table(_read(name))
        assert c == cols, f"{name}: cols {c} != {cols}"
        assert len(rows) == nrows, f"{name}: {len(rows)} != {nrows}"


def test_fixture_escaped_quotes():
    _, rows = onec._parse_table(_read("article_7777"))
    # 'Магазин \"Продукты\"' parses to Магазин "Продукты" as the Склад cell
    assert any(r[0] == 'Магазин "Продукты"' for r in rows)


def test_fixture_empty_article_is_string_not_none():
    _, rows = onec._parse_table(_read("catalog_name_article"))
    assert rows[-1][1] == ""
    assert rows[0][1] == "[OGRN-00001]"


def test_fixture_decimal_and_unit_columns():
    _, rows = onec._parse_table(_read("barbaris_decimal"))
    # [Склад, Товар, Артикул, Ед, Остаток]
    assert rows[0][3] == "кг"
    assert rows[0][4] == 143.25


def test_fixture_article_row_shape():
    _, rows = onec._parse_table(_read("article_7777"))
    assert rows[0] == ["Западный склад", "Барбарис (конфеты)", "Арт-7777", "кг", 143.25]


def test_query_article_7777_three_items_mixed_units(gw):
    gw.onec_data = _read("article_7777")
    res = onec.query_stock("7777")
    assert res["found"] is True
    assert len(res["items"]) == 3
    assert res["quantity"] is None  # heterogeneous units (кг + шт)
    assert "кг" in res["message"] and "шт" in res["message"]
    barb = next(it for it in res["items"] if it["name"].startswith("Барбарис"))
    assert barb["article"] == "Арт-7777"
    assert barb["unit"] == "кг"
    assert barb["quantity"] == 210.25


def test_query_barbaris_single_item_real(gw):
    gw.onec_data = _read("barbaris_decimal")
    res = onec.query_stock("барбарис")
    assert len(res["items"]) == 1
    assert res["items"][0]["quantity"] == 210.25
    assert res["items"][0]["unit"] == "кг"
    assert res["quantity"] == 210.25
    assert "210.25 кг" in res["message"]


def test_query_tv_sharp_multiword_real(gw):
    gw.onec_data = _read("tv_sharp_multiword")
    res = onec.query_stock("Телевизор SHARP")
    assert len(res["items"]) == 1
    it = res["items"][0]
    assert it["name"] == 'Телевизор "SHARP"'
    assert it["article"] == "Т-123456"
    assert it["unit"] == "шт"
    assert it["quantity"] == 127


def test_query_milk_homogeneous_grand_total(gw):
    """All milk items share unit шт → get_stock gives a meaningful total."""
    gw.onec_data = _read("milk_aggregated")
    res = onec.query_stock("молоко")
    assert res["found"] is True
    assert len(res["items"]) == 6
    assert {it["unit"] for it in res["items"]} == {"шт"}
    assert res["message"].startswith("Остаток по 'молоко': всего 480 шт (6 позиций")
