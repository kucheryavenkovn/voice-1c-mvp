"""Snapshot tests for spoken answer phrasing (pytest-syrupy).

Locks the exact wording of voice replies so any unintentional change
(punctuation, plural regression, article/unit formatting) is caught and reviewed.
Update intentionally with:  pytest tests/test_snapshots.py --snapshot-update
"""

import onec

# get_stock ("сколько") — _build_message
CASES = {
    "single_with_article_unit": (
        [
            {
                "name": "Барбарис (конфеты)",
                "article": "Арт-7777",
                "unit": "кг",
                "quantity": 210.25,
                "warehouses": [
                    {"name": "Западный склад", "quantity": 143.25},
                    {"name": "Торговый зал", "quantity": 27},
                ],
            }
        ],
        "барбарис",
    ),
    "single_no_article": (
        [{"name": "Сахар", "article": "", "unit": "кг", "quantity": 5, "warehouses": []}],
        "сахар",
    ),
    "single_plural_no_unit": (
        [{"name": "X", "article": "A", "unit": "", "quantity": 1, "warehouses": []}],
        "x",
    ),
    "single_many_warehouses": (
        [
            {
                "name": "Молоко",
                "article": "М-1",
                "unit": "шт",
                "quantity": 480,
                "warehouses": [{"name": f"Склад{i}", "quantity": i} for i in range(1, 7)],
            }
        ],
        "молоко",
    ),
    "multi_homogeneous_total": (
        [
            {
                "name": "Телевизор SHARP",
                "article": "Т-1",
                "unit": "шт",
                "quantity": 127,
                "warehouses": [],
            },
            {
                "name": "Телевизор JVC",
                "article": "Т-1",
                "unit": "шт",
                "quantity": 10,
                "warehouses": [],
            },
        ],
        "телевизор",
    ),
    "multi_heterogeneous_subtotals": (
        [
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
        ],
        "сахар",
    ),
}

# list_stock ("по каким товарам") — _build_list_message
LIST_CASES = {
    "list_three": (
        [
            {
                "name": "Сахарный песок (весовой)",
                "article": "45463728",
                "unit": "кг",
                "quantity": 385,
                "warehouses": [],
            },
            {
                "name": "Сахарный песок в пачках",
                "article": "Арт-88888",
                "unit": "упак",
                "quantity": 110,
                "warehouses": [],
            },
            {
                "name": "Сахарный песок (в упаковках)",
                "article": "",
                "unit": "упак",
                "quantity": 95,
                "warehouses": [],
            },
        ],
        "сахар",
    ),
    "list_empty": ([], "xyz"),
}


def test_voice_message_snapshots(snapshot):
    for key, (items, user_item) in CASES.items():
        msg = onec._build_message(items, user_item)
        assert msg == snapshot(name=key), f"wording drift for {key}"


def test_list_message_snapshots(snapshot):
    for key, (items, user_item) in LIST_CASES.items():
        msg = onec._build_list_message(items, user_item)
        assert msg == snapshot(name=key), f"wording drift for {key}"
