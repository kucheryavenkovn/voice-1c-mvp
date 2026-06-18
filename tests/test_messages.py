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
    assert "По складам" in m
    assert "Склад1 1" in m and "Склад2 2" in m


def test_single_item_no_unit_falls_back_to_plural():
    it = _item(qty=50, nwh=2)
    it["unit"] = ""
    m = onec._build_message([it], "молоко")
    assert m.startswith("Молоко (арт. Арт-1): всего 50 единиц.")


def test_single_item_no_article():
    it = _item()
    it["article"] = ""
    assert onec._build_message([it], "молоко").startswith("Молоко: всего 50 шт.")


def test_single_item_many_warehouses_extra():
    assert "и ещё на 2 складах" in onec._build_message([_item(nwh=6)], "молоко")


def test_multi_homogeneous_grand_total():
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
    assert onec._build_message(items, "телевизор") == "Остаток по 'телевизор': всего 137 шт."


def test_multi_heterogeneous_subtotals():
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
    assert "385 кг" in m and "205 упак" in m
    assert "всего" not in m  # cross-unit sum is meaningless


def test_multi_with_warehouses_breakdown():
    items = [
        {
            "name": "Барбарис",
            "article": "Арт-7777",
            "unit": "кг",
            "quantity": 10,
            "warehouses": [{"name": "Склад1", "quantity": 10}],
        },
        {
            "name": "Соковыжималка",
            "article": "СО-1",
            "unit": "шт",
            "quantity": 2,
            "warehouses": [{"name": "Склад1", "quantity": 2}],
        },
    ]
    m = onec._build_message(items, "q")
    assert m.startswith("Остаток по 'q': 10 кг + 2 шт.")
    assert "По складам:" in m
    assert "Склад1 (10 кг, 2 шт)" in m  # per-unit inside the warehouse


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


def test_at_warehouse_message_single_item():
    items = [
        {"name": "Молоко 3.2%", "article": "Арт-1", "unit": "шт", "quantity": 20, "warehouses": []}
    ]
    m = onec._build_at_warehouse_message(items, "молоко", "Центральный склад")
    assert m == "Молоко 3.2% (арт. Арт-1) на складе 'Центральный склад': 20 шт."


def test_at_warehouse_message_multi_same_unit():
    items = [
        {
            "name": "Молоко Домик 1.5%",
            "article": "A",
            "unit": "шт",
            "quantity": 20,
            "warehouses": [],
        },
        {
            "name": "Молоко Домик 3.2%",
            "article": "B",
            "unit": "шт",
            "quantity": 20,
            "warehouses": [],
        },
    ]
    m = onec._build_at_warehouse_message(items, "молоко", "Центральный склад")
    assert m.startswith("Остаток 'молоко' на складе 'Центральный склад': 40 шт (2 позиции")


def test_at_warehouse_message_not_found():
    m = onec._build_at_warehouse_message([], "молоко", "Такого склада нет")
    assert "не найден" in m and "Такого склада нет" in m
