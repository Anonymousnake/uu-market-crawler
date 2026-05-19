import json
import os
import sqlite3
import tempfile
from pathlib import Path

from uu_market_probe import parse_sale_template_response, write_cache


def test_sample_query_sale_template():
    body = json.loads(Path("sample_query_sale_template.json").read_text(encoding="utf-8"))
    rows = parse_sale_template_response(body)

    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == 1672
    assert row["name"] == "Moto Gloves | Transport (Field-Tested)"
    assert row["hash_name"] == "Moto Gloves | Transport (Field-Tested)"
    assert row["price"] == "290"
    assert row["steam_price"] == "374.34"
    assert row["steam_usd_price"] == "46.07"
    assert row["on_sale_count"] == 317
    assert row["on_lease_count"] == 232
    assert row["rent"] == "0.05"
    assert row["long_rent"] == "0.05"
    assert row["lease_deposit"] == "344"
    assert row["list_type"] == 10


if __name__ == "__main__":
    test_sample_query_sale_template()
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = str(Path(temp_dir) / "cache.sqlite3")
        os.environ["UU_CACHE_DB"] = db_path
        body = json.loads(Path("sample_query_sale_template.json").read_text(encoding="utf-8"))
        write_cache("sale", parse_sale_template_response(body))
        connection = sqlite3.connect(db_path)
        try:
            count = connection.execute("SELECT COUNT(*) FROM sale_template_snapshots").fetchone()[0]
        finally:
            connection.close()
        assert count == 1
        os.environ.pop("UU_CACHE_DB", None)
    print("parser ok")
