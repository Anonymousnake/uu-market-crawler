from __future__ import annotations

import json
import math
import random
import re
import time
import urllib.parse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import requests


STEAM_COMMUNITY = "https://steamcommunity.com"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)


@dataclass
class SteamMarketConfig:
    appid: int = 730
    currency: int = 23
    country: str = "CN"
    language: str = "schinese"
    usd_cny_rate: Decimal = Decimal("7.20")
    cache_ttl_seconds: int = 900
    sleep_min: float = 1.5
    sleep_max: float = 3.5


def load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"items": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"items": {}}
    if not isinstance(payload, dict):
        return {"items": {}}
    payload.setdefault("items", {})
    return payload


def save_cache(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def get_market_snapshot(
    market_hash_name: str,
    *,
    cache_file: Path,
    config: SteamMarketConfig | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    config = config or SteamMarketConfig()
    market_hash_name = str(market_hash_name or "").strip()
    if not market_hash_name:
        raise ValueError("market_hash_name is required")

    cache = load_cache(cache_file)
    items = cache.setdefault("items", {})
    cached = items.get(market_hash_name)
    now = time.time()
    if isinstance(cached, dict) and now - float(cached.get("_cached_at", 0)) <= config.cache_ttl_seconds:
        return {key: value for key, value in cached.items() if key != "_cached_at"}

    own_session = session is None
    session = session or requests.Session()
    try:
        overview = fetch_priceoverview(session, market_hash_name, config)
        sleep_jitter(config)
        listing = fetch_listing_snapshot(session, market_hash_name, config)
    finally:
        if own_session:
            session.close()

    snapshot = {
        "market_hash_name": market_hash_name,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "priceoverview": overview,
        "listing": listing,
        "history_stats": summarize_history(listing.get("price_history") or []),
    }
    items[market_hash_name] = {**snapshot, "_cached_at": now}
    cache["updated_at"] = snapshot["fetched_at"]
    save_cache(cache_file, cache)
    return snapshot


def fetch_priceoverview(
    session: requests.Session,
    market_hash_name: str,
    config: SteamMarketConfig,
) -> dict[str, Any]:
    response = session.get(
        f"{STEAM_COMMUNITY}/market/priceoverview/",
        params={
            "appid": config.appid,
            "currency": config.currency,
            "market_hash_name": market_hash_name,
        },
        headers=steam_headers(config),
        timeout=25,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("success"):
        raise RuntimeError(f"Steam priceoverview failed for {market_hash_name}: {body}")
    return {
        "median_price_text": body.get("median_price"),
        "median_price": decimal_to_str(parse_money(body.get("median_price"))),
        "lowest_price_text": body.get("lowest_price"),
        "lowest_price": decimal_to_str(parse_money(body.get("lowest_price"))),
        "volume": parse_int(body.get("volume")),
        "currency": config.currency,
    }


def fetch_listing_snapshot(
    session: requests.Session,
    market_hash_name: str,
    config: SteamMarketConfig,
) -> dict[str, Any]:
    url = f"{STEAM_COMMUNITY}/market/listings/{config.appid}/{urllib.parse.quote(market_hash_name)}"
    response = session.get(
        url,
        params={
            "l": config.language,
            "currency": config.currency,
            "cc": config.country,
        },
        headers=steam_headers(config),
        timeout=30,
    )
    response.raise_for_status()
    text = response.text
    return {
        "url": response.url,
        "sell_order_count": parse_listing_count(text, "sell"),
        "buy_order_count": parse_listing_count(text, "buy"),
        "lowest_sell_order": first_order_price(text, "sell", config),
        "highest_buy_order": first_order_price(text, "buy", config),
        "sell_orders": parse_order_table(text, "sell", config),
        "buy_orders": parse_order_table(text, "buy", config),
        "price_history": parse_price_history(text),
        "orderbook_currency": "USD",
        "orderbook_usd_cny_rate": decimal_to_str(config.usd_cny_rate),
    }


def steam_headers(config: SteamMarketConfig) -> dict[str, str]:
    return {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json,text/html,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": f"{STEAM_COMMUNITY}/market/",
    }


def sleep_jitter(config: SteamMarketConfig) -> None:
    time.sleep(random.uniform(config.sleep_min, config.sleep_max))


def parse_listing_count(text: str, side: str) -> int | None:
    compact_key = "cSellOrders" if side == "sell" else "cBuyOrders"
    compact_match = re.search(rf'{compact_key}\\+":(\d+)', text)
    if compact_match:
        return parse_int(compact_match.group(1))
    if side == "sell":
        match = re.search(r'([\d,]+)\s*个出售中，起价', text)
    else:
        match = re.search(r'([\d,]+)\s*份以\s*[^<]+?\s*或更低价格购买的请求', text)
    return parse_int(match.group(1)) if match else None


def first_order_price(text: str, side: str, config: SteamMarketConfig) -> dict[str, Any] | None:
    rows = parse_order_table(text, side, config)
    return rows[0] if rows else None


def parse_order_table(text: str, side: str, config: SteamMarketConfig) -> list[dict[str, Any]]:
    marker = "个出售中，起价" if side == "sell" else "或更低价格购买的请求"
    start = text.find(marker)
    if start < 0:
        return []
    table_start = text.find("<tbody>", start)
    table_end = text.find("</tbody>", table_start)
    if table_start < 0 or table_end < 0:
        return []
    tbody = text[table_start:table_end]
    rows = []
    for price_text, quantity_text in re.findall(
        r"<tr><td><span[^>]*>([^<]+)</span></td><td><span[^>]*>([^<]+)</span></td></tr>",
        tbody,
    ):
        price_usd = parse_money(price_text)
        price_cny = price_usd * config.usd_cny_rate if price_usd is not None else None
        rows.append(
            {
                "price_text": price_text.strip(),
                "price": decimal_to_str(price_usd),
                "price_usd": decimal_to_str(price_usd),
                "price_cny": decimal_to_str(quantize(price_cny, "0.01")),
                "quantity": parse_int(quantity_text),
            }
        )
    if side == "sell":
        rows.sort(key=lambda row: parse_money(row.get("price_usd")) or Decimal("0"))
    else:
        rows.sort(key=lambda row: parse_money(row.get("price_usd")) or Decimal("0"), reverse=True)
    return rows


def parse_price_history(text: str) -> list[dict[str, Any]]:
    marker = "prices"
    marker_index = text.find(marker)
    if marker_index < 0:
        return []
    start = text.find("[", marker_index)
    if start < 0:
        return []
    depth = 0
    end = None
    for index in range(start, len(text)):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        return []
    raw = text[start:end]
    for _ in range(4):
        raw = raw.replace("\\\\\\\"", '"').replace("\\\\\"", '"').replace("\\\"", '"')
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        return []
    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        price = row.get("price_median")
        timestamp = row.get("time")
        try:
            price_value = Decimal(str(price))
            timestamp_value = int(timestamp)
        except (InvalidOperation, TypeError, ValueError):
            continue
        parsed.append(
            {
                "time": timestamp_value,
                "price_median": decimal_to_str(price_value),
                "purchases": parse_int(row.get("purchases")) or 0,
            }
        )
    return parsed


def summarize_history(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [parse_decimal(row.get("price_median")) for row in rows]
    values = [value for value in values if value is not None]
    purchases = [parse_int(row.get("purchases")) or 0 for row in rows]
    vol_7d = volatility(values[-8:])
    vol_30d = volatility(values[-31:])
    change_7d = period_change(values, 7)
    worst_change_7d = worst_period_change(values[-31:], 7)
    last_price = values[-1] if values else None
    volume_24h = purchases[-1] if purchases else 0
    return {
        "source": "steamcommunity:listing",
        "volatility_7d": decimal_to_str(quantize(vol_7d, "0.0001")),
        "volatility_30d": decimal_to_str(quantize(vol_30d, "0.0001")),
        "change_7d": decimal_to_str(quantize(change_7d, "0.0001")),
        "worst_change_7d": decimal_to_str(quantize(worst_change_7d, "0.0001")),
        "volume_24h": volume_24h,
        "last_price": decimal_to_str(quantize(last_price, "0.01")),
        "sample_count": len(values),
    }


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
    mean = sum(changes) / Decimal(len(changes))
    variance = sum((item - mean) * (item - mean) for item in changes) / Decimal(len(changes))
    return Decimal(str(math.sqrt(float(variance))))


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


def parse_money(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = re.sub(r"[^\d.,-]", "", text)
    if "," in normalized and "." in normalized:
        normalized = normalized.replace(",", "")
    elif "," in normalized:
        normalized = normalized.replace(",", "")
    if not normalized:
        return None
    return parse_decimal(normalized)


def parse_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).replace(",", "").strip())
    except ValueError:
        return None


def quantize(value: Decimal | None, pattern: str) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(Decimal(pattern))


def decimal_to_str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
