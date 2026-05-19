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
    lookback: str
    fill: str
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
        lookback=env("CS2CAP_LOOKBACK", "30d"),
        fill=env("CS2CAP_FILL", "false"),
        sleep_min=float(env("CS2CAP_SLEEP_MIN", "1.5")),
        sleep_max=float(env("CS2CAP_SLEEP_MAX", "3.5")),
    )


def load_watchlist(path: Path, limit: int) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("items", payload.get("results", payload if isinstance(payload, list) else []))
    if not isinstance(rows, list):
        raise ValueError("watchlist must be a list or contain items/results")
    return [row for row in rows if isinstance(row, dict) and row.get("query")][:limit]


def request_candles(config: Config, market_hash_name: str) -> dict[str, Any]:
    response = requests.get(
        f"{API_BASE}/prices/candles",
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 uu-market-crawler/1.0",
        },
        params={
            "market_hash_name": market_hash_name,
            "lookback": config.lookback,
            "interval": "1d",
            "fill": config.fill,
            "currency": config.currency,
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


def period_change(values: list[Decimal], days: int) -> Decimal | None:
    if len(values) <= days:
        return None
    previous = values[-days - 1]
    current = values[-1]
    if not previous:
        return None
    return (current - previous) / previous


def worst_period_change(values: list[Decimal], days: int) -> Decimal | None:
    if len(values) <= days:
        return None
    changes = []
    for start in range(0, len(values) - days):
        previous = values[start]
        current = values[start + days]
        if previous:
            changes.append((current - previous) / previous)
    return min(changes) if changes else None


def summarize_candles(market_hash_name: str, body: dict[str, Any]) -> dict[str, Any]:
    candles = [row for row in body.get("data", []) if isinstance(row, dict)]
    values = [price_from_minor_units(row.get("c")) for row in candles]
    values = [value for value in values if value is not None]
    vol_7d = volatility(values[-8:])
    vol_30d = volatility(values)
    change_7d = period_change(values, 7)
    worst_change_7d = worst_period_change(values, 7)
    last = candles[-1] if candles else {}
    last_price = values[-1] if values else None
    volume_24h = int(last.get("v") or 0) if isinstance(last, dict) else 0
    listing_count = int(last.get("q") or 0) if isinstance(last, dict) and last.get("q") is not None else None
    return {
        "hash_name": market_hash_name,
        "volatility_7d": str(vol_7d.quantize(Decimal("0.0001"))) if vol_7d is not None else None,
        "volatility_30d": str(vol_30d.quantize(Decimal("0.0001"))) if vol_30d is not None else None,
        "change_7d": str(change_7d.quantize(Decimal("0.0001"))) if change_7d is not None else None,
        "worst_change_7d": str(worst_change_7d.quantize(Decimal("0.0001"))) if worst_change_7d is not None else None,
        "volume_24h": volume_24h,
        "listing_count": listing_count,
        "last_price": str(last_price.quantize(Decimal("0.01"))) if last_price is not None else None,
        "sample_count": len(values),
    }


def run() -> dict[str, Any]:
    config = load_config()
    rows = load_watchlist(config.watchlist_file, config.limit)
    items = []
    errors = []
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    for index, row in enumerate(rows, start=1):
        if index > 1:
            time.sleep(random.uniform(config.sleep_min, config.sleep_max))
        query = str(row.get("query"))
        try:
            items.append(summarize_candles(query, request_candles(config, query)))
        except Exception as exc:  # noqa: BLE001 - collect per-item failures for cron.
            errors.append({"hash_name": query, "error": str(exc)})
            if "429" in str(exc):
                break

    output = {
        "generated_at": now,
        "source": "cs2cap:/v1/prices/candles",
        "currency": config.currency,
        "lookback": config.lookback,
        "fill": config.fill,
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
