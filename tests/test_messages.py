"""Tests for the spoken-message builder, quantity formatting and Russian plurals."""

import onec
import pytest


def _item(name="Молоко", article="Арт-1", qty=50, nwh=2):
    return {
        "name": name,
        "article": article,
        "quantity": qty,
        "warehouses": [{"name": f"Склад{i}", "quantity": i} for i in range(1, nwh + 1)],
    }


def test_single_item_message():
    m = onec._build_message([_item(qty=50, nwh=2)], "молоко")
    assert m.startswith("Молоко (арт. Арт-1): всего 50 единиц.")
    assert "Склад1 1" in m and "Склад2 2" in m


def test_single_item_no_article():
    it = _item()
    it["article"] = ""
    m = onec._build_message([it], "молоко")
    assert m.startswith("Молоко: всего 50 единиц.")


def test_single_item_many_warehouses_extra():
    m = onec._build_message([_item(nwh=6)], "молоко")
    assert "и ещё на 2 складах" in m


def test_multi_item_message_lists_per_item():
    items = [
        {"name": "Барбарис", "article": "Арт-7777", "quantity": 10, "warehouses": []},
        {"name": "Молоко", "article": "Арт-777788", "quantity": 5, "warehouses": []},
    ]
    m = onec._build_message(items, "7777")
    assert "найдено 2" in m
    assert "Барбарис арт Арт-7777 — 10" in m
    assert "Молоко арт Арт-777788 — 5" in m


def test_multi_item_more_than_three():
    items = [
        {"name": f"Т{i}", "article": f"A{i}", "quantity": i, "warehouses": []} for i in range(1, 6)
    ]
    m = onec._build_message(items, "q")
    assert "найдено 5" in m
    assert "и ещё 2 позиции" in m


@pytest.mark.parametrize(
    "n,word",
    [
        (1, "единица"),
        (2, "единицы"),
        (3, "единицы"),
        (4, "единицы"),
        (5, "единиц"),
        (11, "единиц"),
        (21, "единица"),
        (23, "единицы"),
        (25, "единиц"),
        (101, "единица"),
    ],
)
def test_plural_units(n, word):
    it = _item(qty=n, nwh=0)
    assert f"всего {n} {word}" in onec._build_message([it], "x")


@pytest.mark.parametrize(
    "val,exp",
    [
        (480, "480"),
        (0, "0"),
        (143.25, "143.25"),
        (143.0, "143"),
        (0.5, "0.5"),
    ],
)
def test_format_qty(val, exp):
    assert onec._format_qty(val) == exp


def test_format_qty_garbage_passthrough():
    assert onec._format_qty("n/a") == "n/a"
