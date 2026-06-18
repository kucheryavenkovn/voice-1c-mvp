"""Integration: onec.query_stock end-to-end with the 1C HTTP transport faked."""
import pytest

import onec
from conftest import ONEC_SINGLE, ONEC_MULTI, ONEC_EMPTY, ONEC_DECIMAL


def test_query_single_groups_warehouses(gw):
    gw.onec_data = ONEC_SINGLE
    res = onec.query_stock("молоко")
    assert res["found"] is True
    assert res["source"] == "1c"
    assert len(res["items"]) == 1               # both rows → one Товар+Артикул
    it = res["items"][0]
    assert it["name"] == "Молоко 3.2%"
    assert it["quantity"] == 50                 # 20 + 30
    assert len(it["warehouses"]) == 2
    assert res["quantity"] == 50               # single item → top quantity
    assert "всего 50" in res["message"]


def test_query_multi_no_cross_item_sum(gw):
    gw.onec_data = ONEC_MULTI
    res = onec.query_stock("7777")
    assert res["found"] is True
    assert len(res["items"]) == 3
    assert res["quantity"] is None              # units may differ → no sum
    assert "найдено 3" in res["message"]


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
    assert "143.25" in res["message"]


def test_query_1c_business_error_raises(gw):
    gw.onec_fail = True                          # 1C returns success=False
    with pytest.raises(RuntimeError):
        onec.query_stock("молоко")


def test_query_empty_input():
    res = onec.query_stock("   ")
    assert res["found"] is False
    assert res["items"] == []
