import json
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests


API_BASE = "https://api.cs2c.app/v1"
DEFAULT_WATCHLIST = Path(__file__).with_name("watchlist.json")
DEFAULT_OUTPUT = Path(__file__).with_name("steam_history_cache.json")


@dataclass
class Config:
    api_key: str
    watchlist_file: Path
    output_file: Path
    limit: int
    currency: str
    sleep_min: float
    sleep_max: float


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def load_config() -> Config:
    key = env("CS2CAP_API_KEY")
    if not key:
        raise ValueError("Missing CS2CAP_API_KEY")
    return Config(
        api_key=key,
        watchlist_file=Path(env("UU_WATCHLIST_FILE", str(DEFAULT_WATCHLIST))),
        output_file=Path(env("UU_HISTORY_CACHE_FILE", str(DEFAULT_OUTPUT))),
        limit=max(1, int(env("CS2CAP_HISTORY_LIMIT", "30"))),
        currency=env("CS2CAP_CURRENCY", "CNY"),
        sleep_min=float(env("CS2CAP_SLEEP_MIN", "1.5")),
        sleep_max=float(env("CS2CAP_SLEEP_MAX", "3.5")),
    )


def load_watchlist(path: Path, limit: int) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("items", payload.get("results", payload if isinstance(payload, list) else []))
    if not isinstance(rows, list):
        raise ValueError("watchlist must be a list or contain items/results")
    return [row for row in rows if isinstance(row, dict) and row.get("query")][:limit]


def load_existing(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"items": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {"items": []}
    payload.setdefault("items", [])
    return payload


def request_current_price(config: Config, market_hash_name: str) -> dict[str, Any]:
    response = requests.get(
        f"{API_BASE}/prices",
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 uu-market-crawler/1.0",
        },
        params={
            "market_hash_name": market_hash_name,
            "providers": "steam",
            "currency": config.currency,
            "limit": "1",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def price_from_minor_units(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)) / Decimal("100")


def pct_changes(values: list[Decimal]) -> list[Decimal]:
    changes = []
    for previous, current in zip(values, values[1:]):
        if previous:
            changes.append((current - previous) / previous)
    return changes


def volatility(values: list[Decimal]) -> Decimal | None:
    changes = pct_changes(values)
    if len(changes) < 2:
        return None
    return Decimal(str(statistics.pstdev([float(item) for item in changes])))


def item_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapped = {}
    for row in payload.get("items", []):
        if not isinstance(row, dict):
            continue
        key = str(row.get("hash_name") or "").lower()
        if key:
            mapped[key] = row
    return mapped


def normalize_current(market_hash_name: str, body: dict[str, Any]) -> dict[str, Any]:
    rows = body.get("items") or []
    row = rows[0] if rows else {}
    price = price_from_minor_units(row.get("lowest_ask"))
    quantity = row.get("quantity")
    return {
        "hash_name": market_hash_name,
        "price": str(price.quantize(Decimal("0.01"))) if price is not None else None,
        "quantity": int(quantity or 0),
        "timestamp": row.get("timestamp") or row.get("last_updated"),
    }


def summarize_history(row: dict[str, Any]) -> dict[str, Any]:
    snapshots = row.get("snapshots", [])
    values = []
    for snap in snapshots[-30:]:
        price = snap.get("price") if isinstance(snap, dict) else None
        if price is not None:
            values.append(Decimal(str(price)))
    vol_7d = volatility(values[-8:])
    vol_30d = volatility(values)
    last = snapshots[-1] if snapshots else {}
    return {
        "hash_name": row.get("hash_name"),
        "volatility_7d": str(vol_7d.quantize(Decimal("0.0001"))) if vol_7d is not None else None,
        "volatility_30d": str(vol_30d.quantize(Decimal("0.0001"))) if vol_30d is not None else None,
        "volume_24h": last.get("quantity", 0),
        "listing_count": last.get("quantity", 0),
        "last_price": last.get("price"),
        "sample_count": len(values),
        "snapshots": snapshots[-30:],
    }


def run() -> dict[str, Any]:
    config = load_config()
    existing = load_existing(config.output_file)
    mapped = item_map(existing)
    rows = load_watchlist(config.watchlist_file, config.limit)
    errors = []
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    for index, row in enumerate(rows, start=1):
        if index > 1:
            time.sleep(random.uniform(config.sleep_min, config.sleep_max))
        query = str(row.get("query"))
        try:
            current = normalize_current(query, request_current_price(config, query))
            target = mapped.setdefault(query.lower(), {"hash_name": query, "snapshots": []})
            snapshots = target.setdefault("snapshots", [])
            if current["price"] is not None:
                snapshots.append(
                    {
                        "fetched_at": now,
                        "price": current["price"],
                        "quantity": current["quantity"],
                        "source_timestamp": current["timestamp"],
                    }
                )
                target["snapshots"] = snapshots[-30:]
        except Exception as exc:  # noqa: BLE001 - collect per-item failures for cron.
            errors.append({"hash_name": query, "error": str(exc)})
            if "429" in str(exc):
                break

    items = [summarize_history(row) for row in mapped.values()]
    output = {
        "generated_at": now,
        "source": "cs2cap:/v1/prices daily snapshots",
        "currency": config.currency,
        "count": len(items),
        "items": sorted(items, key=lambda item: str(item.get("hash_name") or "")),
        "errors": errors,
    }
    config.output_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def main() -> int:
    output = run()
    print(json.dumps({k: output[k] for k in ("generated_at", "count", "errors")}, ensure_ascii=False, indent=2))
    return 0 if not output["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
