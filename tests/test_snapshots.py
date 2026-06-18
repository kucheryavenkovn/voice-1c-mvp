"""Snapshot tests for spoken answer phrasing (pytest-syrupy).

Locks the exact wording of voice replies so any unintentional change
(punctuation, plural regression, article formatting) is caught and reviewed.
Update intentionally with:  pytest tests/test_snapshots.py --snapshot-update
"""

import onec

# representative inputs → deterministic outputs
CASES = {
    "single_with_article": (
        [
            {
                "name": "Барбарис (конфеты)",
                "article": "Арт-7777",
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
        [{"name": "Сахар", "article": "", "quantity": 5, "warehouses": []}],
        "сахар",
    ),
    "single_unit_plural_1": (
        [{"name": "X", "article": "A", "quantity": 1, "warehouses": []}],
        "x",
    ),
    "single_many_warehouses": (
        [
            {
                "name": "Молоко",
                "article": "М-1",
                "quantity": 480,
                "warehouses": [{"name": f"Склад{i}", "quantity": i} for i in range(1, 7)],
            }
        ],
        "молоко",
    ),
    "multi_three_items": (
        [
            {"name": "Барбарис", "article": "Арт-7777", "quantity": 210.25, "warehouses": []},
            {
                "name": 'Молоко "Домик в деревне" 1.5%',
                "article": "Арт-777788",
                "quantity": 80,
                "warehouses": [],
            },
            {"name": "Соковыжималка", "article": "СО-77777", "quantity": 66, "warehouses": []},
        ],
        "7777",
    ),
    "multi_five_items": (
        [
            {"name": f"Товар{i}", "article": f"A{i}", "quantity": i, "warehouses": []}
            for i in range(1, 6)
        ],
        "запрос",
    ),
}


def test_voice_message_snapshots(snapshot):
    for key, (items, user_item) in CASES.items():
        msg = onec._build_message(items, user_item)
        assert msg == snapshot(name=key), f"wording drift for {key}"


LIST_CASES = {
    "list_three": (
        [
            {
                "name": "Сахарный песок (весовой)",
                "article": "45463728",
                "quantity": 385,
                "warehouses": [],
            },
            {
                "name": "Сахарный песок в пачках",
                "article": "Арт-88888",
                "quantity": 110,
                "warehouses": [],
            },
            {
                "name": "Сахарный песок (в упаковках)",
                "article": "",
                "quantity": 95,
                "warehouses": [],
            },
        ],
        "сахар",
    ),
    "list_empty": ([], "xyz"),
}


def test_list_message_snapshots(snapshot):
    for key, (items, user_item) in LIST_CASES.items():
        msg = onec._build_list_message(items, user_item)
        assert msg == snapshot(name=key), f"wording drift for {key}"
