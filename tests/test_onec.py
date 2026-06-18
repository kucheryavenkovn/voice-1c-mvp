"""Integration: onec.query_stock end-to-end with the 1C HTTP transport faked."""

import onec
import pytest
from conftest import ONEC_DECIMAL, ONEC_EMPTY, ONEC_MULTI, ONEC_SINGLE


def test_query_single_groups_warehouses(gw):
    gw.onec_data = ONEC_SINGLE
    res = onec.query_stock("молоко")
    assert res["found"] is True
    assert res["source"] == "1c"
    assert len(res["items"]) == 1
    it = res["items"][0]
    assert it["name"] == "Молоко 3.2%"
    assert it["unit"] == "шт"
    assert it["quantity"] == 50
    assert res["quantity"] == 50
    assert "всего 50 шт" in res["message"] and "По складам" in res["message"]


def test_query_multi_heterogeneous_units(gw):
    gw.onec_data = ONEC_MULTI
    res = onec.query_stock("7777")
    assert res["found"] is True
    assert len(res["items"]) == 3
    assert res["quantity"] is None  # mixed units (кг + шт) → no sum
    assert "10 кг" in res["message"] and "7 шт" in res["message"]
    assert "По складам" in res["message"]


def test_query_empty_not_found(gw):
    gw.onec_data = ONEC_EMPTY
    res = onec.query_stock("xyz")
    assert res["found"] is False
    assert res["items"] == []
    assert "не найден" in res["message"]


def test_query_decimal_quantity(gw):
    gw.onec_data = ONEC_DECIMAL
    res = onec.query_stock("барбарис")
    assert res["items"][0]["quantity"] == 143.25
    assert res["items"][0]["unit"] == "кг"
    assert "143.25" in res["message"]


def test_query_1c_business_error_raises(gw):
    gw.onec_fail = True
    with pytest.raises(RuntimeError):
        onec.query_stock("молоко")


def test_query_empty_input():
    res = onec.query_stock("   ")
    assert res["found"] is False
    assert res["items"] == []
