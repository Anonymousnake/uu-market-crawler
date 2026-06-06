from uu_market_radar import build_market_row_from_on_sale, choose_exact


def test_choose_exact_does_not_fall_back_to_first_unrelated_row():
    rows = [
        {"id": 2413, "hash_name": "★ M9 Bayonet | Lore (Field-Tested)", "name": "M9 刺刀 | 传说"},
        {"id": 45751, "hash_name": "FAMAS | Roll Cage (Factory New)", "name": "法玛斯 | 防滚架"},
    ]

    assert choose_exact("Recoil Case", rows, {"template_id": 102276, "hash_name": "Recoil Case"}) is None


def test_build_market_row_from_on_sale_uses_template_listing_and_total_count():
    watch = {"template_id": 102276, "hash_name": "Recoil Case", "query": "Recoil Case"}
    rows = [
        {
            "id": 1989710257,
            "template_id": 102276,
            "hash_name": "Recoil Case",
            "name": "反冲武器箱",
            "price": "2.36",
        }
    ]
    body = {"TotalCount": 10000}

    row = build_market_row_from_on_sale(watch, rows, body)

    assert row["id"] == 102276
    assert row["listing_id"] == 1989710257
    assert row["template_id"] == 102276
    assert row["hash_name"] == "Recoil Case"
    assert row["price"] == "2.36"
    assert row["on_sale_count"] == 10000
