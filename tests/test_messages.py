"""Tests for the spoken-message builder, quantity formatting and Russian plurals."""

import onec
import pytest


def _item(name="Молоко", article="Арт-1", qty=50, unit="шт", nwh=2):
    return {
        "name": name,
        "article": article,
        "unit": unit,
        "quantity": qty,
        "warehouses": [{"name": f"Склад{i}", "quantity": i} for i in range(1, nwh + 1)],
    }


def test_single_item_message_with_unit():
    m = onec._build_message([_item(qty=50, unit="шт", nwh=2)], "молоко")
    assert m.startswith("Молоко (арт. Арт-1): всего 50 шт.")
    assert "Склад1 1" in m and "Склад2 2" in m


def test_single_item_no_unit_falls_back_to_plural():
    it = _item(qty=50, nwh=2)
    it["unit"] = ""
    m = onec._build_message([it], "молоко")
    assert m.startswith("Молоко (арт. Арт-1): всего 50 единиц.")


def test_single_item_no_article():
    it = _item()
    it["article"] = ""
    m = onec._build_message([it], "молоко")
    assert m.startswith("Молоко: всего 50 шт.")


def test_single_item_many_warehouses_extra():
    m = onec._build_message([_item(nwh=6)], "молоко")
    assert "и ещё на 2 складах" in m


def test_multi_homogeneous_units_grand_total():
    items = [
        {
            "name": "Телевизор SHARP",
            "article": "Т-1",
            "unit": "шт",
            "quantity": 127,
            "warehouses": [],
        },
        {"name": "Телевизор JVC", "article": "Т-1", "unit": "шт", "quantity": 10, "warehouses": []},
    ]
    m = onec._build_message(items, "телевизор")
    assert m.startswith("Остаток по 'телевизор': всего 137 шт (2 позиции:")
    assert "Телевизор SHARP 127" in m and "Телевизор JVC 10" in m


def test_multi_heterogeneous_units_subtotals():
    items = [
        {
            "name": "Сахар весовой",
            "article": "454",
            "unit": "кг",
            "quantity": 385,
            "warehouses": [],
        },
        {
            "name": "Сахар в пачках",
            "article": "",
            "unit": "упак",
            "quantity": 110,
            "warehouses": [],
        },
        {
            "name": "Сахар в упаковках",
            "article": "",
            "unit": "упак",
            "quantity": 95,
            "warehouses": [],
        },
    ]
    m = onec._build_message(items, "сахар")
    assert "385 кг" in m and "205 упак" in m  # 110 + 95
    assert "всего" not in m  # no meaningless cross-unit sum


def test_multi_more_than_four():
    items = [
        {"name": f"Товар{i}", "article": f"A{i}", "unit": "шт", "quantity": i, "warehouses": []}
        for i in range(1, 6)
    ]
    m = onec._build_message(items, "x")
    assert "всего 15 шт (5 позиций" in m
    assert "и ещё 1 позиция" in m


@pytest.mark.parametrize(
    "n,word",
    [
        (1, "единица"),
        (2, "единицы"),
        (5, "единиц"),
        (11, "единиц"),
        (21, "единица"),
        (23, "единицы"),
        (25, "единиц"),
    ],
)
def test_plural_units_when_no_unit(n, word):
    it = _item(qty=n, nwh=0)
    it["unit"] = ""
    assert f"всего {n} {word}" in onec._build_message([it], "x")


@pytest.mark.parametrize(
    "val,exp", [(480, "480"), (0, "0"), (143.25, "143.25"), (143.0, "143"), (0.5, "0.5")]
)
def test_format_qty(val, exp):
    assert onec._format_qty(val) == exp


def test_format_qty_garbage_passthrough():
    assert onec._format_qty("n/a") == "n/a"
