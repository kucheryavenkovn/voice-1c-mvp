"""Pure-logic tests for the 1C text-table parser, sanitization and query builder."""

import onec


def test_parse_header_and_simple_rows():
    cols, rows = onec._parse_table(
        '[2]{"Склад","Остаток"}:\n  Торговый зал,150\n  Центральный склад,100'
    )
    assert cols == ["Склад", "Остаток"]
    assert rows == [["Торговый зал", 150], ["Центральный склад", 100]]


def test_parse_quoted_with_escaped_quotes():
    # real shape: "Магазин \"Продукты\""
    _, rows = onec._parse_table('[1]{"Склад","Остаток"}:\n  "Магазин \\"Продукты\\"",100')
    assert rows == [['Магазин "Продукты"', 100]]


def test_parse_empty_result():
    assert onec._parse_table("[0]:") == ([], [])
    assert onec._parse_table("") == ([], [])


def test_parse_single_column():
    cols, rows = onec._parse_table('[1]{"Результат"}:\n  1')
    assert cols == ["Результат"]
    assert rows == [[1]]


def test_parse_decimal_and_four_cols():
    cols, rows = onec._parse_table(
        '[1]{"Склад","Товар","Артикул","Остаток"}:\n  Западный склад,Барбарис,Арт-7777,143.25'
    )
    assert cols == ["Склад", "Товар", "Артикул", "Остаток"]
    assert rows[0][3] == 143.25
    assert rows[0][2] == "Арт-7777"


def test_sanitize_keeps_letters_digits_dash():
    assert onec._sanitize("молоко") == "молоко"
    assert onec._sanitize("Арт-7777") == "Арт-7777"
    assert onec._sanitize("45463728") == "45463728"


def test_sanitize_strips_dangerous_chars():
    # % _ " \ must go (would break ПОДОБНО / injection); hyphen kept
    assert onec._sanitize('a%b_c"d') == "a b c d"
    assert onec._sanitize("100%") == "100"
    assert onec._sanitize("a_b") == "a b"
    assert "_" not in onec._sanitize("a_b")
    assert "%" not in onec._sanitize("100%")


def test_sanitize_truncates():
    assert len(onec._sanitize("x" * 100)) <= 40


def test_like_pattern_single_and_multiword():
    assert onec._like_pattern("молоко") == "%молоко%"
    assert onec._like_pattern("Телевизор SHARP") == "%Телевизор%SHARP%"
    assert onec._like_pattern("a  b   c") == "%a%b%c%"


def test_build_query_matches_name_or_article():
    q = onec._build_query("7777")
    assert "Номенклатура.Наименование" in q
    assert "Номенклатура.Артикул" in q
    assert "ИЛИ" in q
    assert 'ВРЕГ("%7777%")' in q


def test_build_query_multiword_inserts_wildcard_between_words():
    q = onec._build_query("Телевизор SHARP")
    # each space -> %, Cyrilric lemma lowercased; 'Телевизор 21 дюйм Sharp' matches
    assert q.count('ВРЕГ("%телевизор%SHARP%")') == 2  # name + article


def test_build_query_no_injection():
    raw = '50%" AND _ 1=1 --'
    safe = onec._sanitize(raw)
    for bad in ("%", "_", '"', "\\"):
        assert bad not in safe
    q = onec._build_query(raw)
    like = onec._like_pattern(safe)  # only our own %...% wildcards
    assert q.count('ВРЕГ("' + like + '")') == 2
