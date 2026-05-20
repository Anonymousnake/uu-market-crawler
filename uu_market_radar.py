import json
import os
import random
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fx_rate import FxRateConfig, get_usd_cny_rate
from steam_market_api import SteamMarketConfig, get_market_snapshot
from uu_market_probe import (
    QUERY_SALE_TEMPLATE_URL,
    build_headers,
    init_cache,
    parse_sale_template_response,
    post_json_once,
    to_decimal,
    write_cache,
)


DEFAULT_WATCHLIST = Path(__file__).with_name("watchlist.json")
DEFAULT_OUTPUT = Path(__file__).with_name("radar.latest.json")
STEAM_FEE_DIVISOR = Decimal("1.15")


@dataclass
class RadarConfig:
    watchlist_file: Path
    output_file: Path
    cache_db: Path
    limit: int
    min_edge: Decimal
    push_cooldown_hours: int
    repush_delta_edge: Decimal
    min_on_sale_count: int
    page_size: int
    sleep_min: Decimal
    sleep_max: Decimal
    steam_market_cache_file: Path
    fx_cache_file: Path
    usd_cny_rate: Decimal
    fx_cache_ttl_seconds: int
    steam_cache_ttl_seconds: int
    steam_sleep_min: Decimal
    steam_sleep_max: Decimal


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def decimal_env(name: str, default: str) -> Decimal:
    try:
        return Decimal(env(name, default))
    except InvalidOperation:
        return Decimal(default)


def int_env(name: str, default: str) -> int:
    try:
        return int(env(name, default))
    except ValueError:
        return int(default)


def load_config() -> RadarConfig:
    cache_db = Path(env("UU_CACHE_DB", str(Path(__file__).with_name("uu_market_cache.sqlite3"))))
    return RadarConfig(
        watchlist_file=Path(env("UU_WATCHLIST_FILE", str(DEFAULT_WATCHLIST))),
        output_file=Path(env("UU_RADAR_OUTPUT", str(DEFAULT_OUTPUT))),
        cache_db=cache_db,
        limit=int_env("UU_RADAR_LIMIT", "12"),
        min_edge=decimal_env("UU_MIN_EDGE", "0.03"),
        push_cooldown_hours=int_env("UU_PUSH_COOLDOWN_HOURS", "12"),
        repush_delta_edge=decimal_env("UU_REPUSH_DELTA_EDGE", "0.05"),
        min_on_sale_count=int_env("UU_MIN_ON_SALE_COUNT", "100"),
        page_size=int_env("UU_PAGE_SIZE", "20"),
        sleep_min=decimal_env("UU_SLEEP_MIN", "2.5"),
        sleep_max=decimal_env("UU_SLEEP_MAX", "6.0"),
        steam_market_cache_file=Path(
            env("STEAM_MARKET_CACHE_FILE", str(Path(__file__).with_name("steam_market_cache.json")))
        ),
        fx_cache_file=Path(env("FX_CACHE_FILE", str(Path(__file__).with_name("fx_rate_cache.json")))),
        usd_cny_rate=decimal_env("USD_CNY_RATE", "7.20"),
        fx_cache_ttl_seconds=int_env("FX_CACHE_TTL_SECONDS", "21600"),
        steam_cache_ttl_seconds=int_env("STEAM_CACHE_TTL_SECONDS", "900"),
        steam_sleep_min=decimal_env("STEAM_SLEEP_MIN", "1.5"),
        steam_sleep_max=decimal_env("STEAM_SLEEP_MAX", "3.5"),
    )


def load_watchlist(path: Path, limit: int) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("items", payload.get("results", payload if isinstance(payload, list) else []))
    if not isinstance(rows, list):
        raise ValueError("watchlist must be a list or contain items/results")
    return [row for row in rows if isinstance(row, dict) and row.get("query")][:limit]


def money(value: Any) -> Decimal | None:
    parsed = to_decimal(value)
    if parsed is None:
        return None
    try:
        return Decimal(parsed)
    except InvalidOperation:
        return None


def query_sale_template(headers: dict[str, str], query: str, page_size: int) -> list[dict[str, Any]]:
    payload = {
        "listSortType": 0,
        "sortType": 0,
        "keyWords": query,
        "pageSize": page_size,
        "pageIndex": 1,
    }
    result = post_json_once(QUERY_SALE_TEMPLATE_URL, payload, headers)
    if not isinstance(result.json_body, dict):
        raise ValueError(f"non-json response while querying {query}: {result.text_preview[:160]}")
    parsed = parse_sale_template_response(result.json_body)
    write_cache("sale", parsed)
    return parsed


def choose_exact(query: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    lowered = query.lower()
    for row in rows:
        if str(row.get("hash_name") or "").lower() == lowered:
            return row
    for row in rows:
        if str(row.get("name") or "").lower() == lowered:
            return row
    return rows[0] if rows else None


def enrich_with_steam(row: dict[str, Any], config: RadarConfig) -> dict[str, Any]:
    hash_name = str(row.get("hash_name") or "").strip()
    if not hash_name:
        return row
    steam = get_market_snapshot(
        hash_name,
        cache_file=config.steam_market_cache_file,
        config=SteamMarketConfig(
            usd_cny_rate=current_usd_cny_rate(config),
            cache_ttl_seconds=config.steam_cache_ttl_seconds,
            sleep_min=float(config.steam_sleep_min),
            sleep_max=float(config.steam_sleep_max),
        ),
    )
    overview = steam.get("priceoverview") or {}
    listing = steam.get("listing") or {}
    row = dict(row)
    row["steam_snapshot"] = steam
    row["steam_price"] = overview.get("median_price")
    row["steam_median_price"] = overview.get("median_price")
    row["steam_lowest_price"] = overview.get("lowest_price")
    row["steam_volume"] = overview.get("volume")
    row["steam_lowest_sell_price"] = (listing.get("lowest_sell_order") or {}).get("price_cny")
    row["steam_highest_buy_price"] = (listing.get("highest_buy_order") or {}).get("price_cny")
    row["steam_lowest_sell_price_usd"] = (listing.get("lowest_sell_order") or {}).get("price_usd")
    row["steam_highest_buy_price_usd"] = (listing.get("highest_buy_order") or {}).get("price_usd")
    row["steam_sell_order_count"] = listing.get("sell_order_count")
    row["steam_buy_order_count"] = listing.get("buy_order_count")
    row["steam_orderbook_currency"] = listing.get("orderbook_currency")
    row["steam_orderbook_fx_rate"] = listing.get("orderbook_usd_cny_rate")
    row["history_stats"] = steam.get("history_stats")
    row["intraday_stats"] = steam.get("intraday_stats")
    return row


def current_usd_cny_rate(config: RadarConfig) -> Decimal:
    return get_usd_cny_rate(
        FxRateConfig(
            cache_file=config.fx_cache_file,
            fallback_usd_cny=config.usd_cny_rate,
            ttl_seconds=config.fx_cache_ttl_seconds,
        )
    )


def score_row(
    watch: dict[str, Any],
    row: dict[str, Any],
    min_on_sale_count: int,
    uu_intraday_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    uu_price = money(row.get("price"))
    steam_price = money(row.get("steam_median_price") or row.get("steam_price"))
    on_sale_count = int(row.get("on_sale_count") or 0)
    steam_net = steam_price / STEAM_FEE_DIVISOR if steam_price is not None else None
    edge = (steam_net - uu_price) / uu_price if steam_net is not None and uu_price else None
    balance_discount = uu_price / steam_net * Decimal("10") if steam_net is not None and uu_price else None
    conservative_steam_net = conservative_net_after_cooldown(steam_net, row.get("history_stats"))
    conservative_discount = (
        uu_price / conservative_steam_net * Decimal("10")
        if conservative_steam_net is not None and uu_price
        else None
    )

    liquidity_penalty = Decimal("0")
    if on_sale_count < min_on_sale_count:
        liquidity_penalty = Decimal("0.05")

    risk = risk_profile(
        kind=str(watch.get("kind") or ""),
        uu_price=uu_price,
        edge=edge,
        on_sale_count=on_sale_count,
        min_on_sale_count=min_on_sale_count,
        history_stats=row.get("history_stats"),
        intraday_stats=row.get("intraday_stats"),
        conservative_discount=conservative_discount,
    )
    risk_penalty = Decimal(risk["risk_penalty"])
    score = edge - liquidity_penalty - risk_penalty if edge is not None else None
    return {
        "kind": watch.get("kind"),
        "template_id": row.get("id") or watch.get("template_id"),
        "query": watch.get("query"),
        "hash_name": row.get("hash_name") or watch.get("hash_name"),
        "name": row.get("name"),
        "uu_price": str(uu_price) if uu_price is not None else None,
        "steam_price": str(steam_price) if steam_price is not None else None,
        "steam_median_price": str(steam_price) if steam_price is not None else None,
        "steam_lowest_price": row.get("steam_lowest_price"),
        "steam_lowest_sell_price": row.get("steam_lowest_sell_price"),
        "steam_highest_buy_price": row.get("steam_highest_buy_price"),
        "steam_lowest_sell_price_usd": row.get("steam_lowest_sell_price_usd"),
        "steam_highest_buy_price_usd": row.get("steam_highest_buy_price_usd"),
        "steam_orderbook_currency": row.get("steam_orderbook_currency"),
        "steam_orderbook_fx_rate": row.get("steam_orderbook_fx_rate"),
        "steam_volume": row.get("steam_volume"),
        "steam_sell_order_count": row.get("steam_sell_order_count"),
        "steam_buy_order_count": row.get("steam_buy_order_count"),
        "steam_net_after_fee": str(steam_net.quantize(Decimal("0.0001"))) if steam_net is not None else None,
        "balance_discount": str(balance_discount.quantize(Decimal("0.01"))) if balance_discount is not None else None,
        "conservative_steam_net_after_fee": (
            str(conservative_steam_net.quantize(Decimal("0.0001"))) if conservative_steam_net is not None else None
        ),
        "conservative_balance_discount": (
            str(conservative_discount.quantize(Decimal("0.01"))) if conservative_discount is not None else None
        ),
        "edge": str(edge.quantize(Decimal("0.0001"))) if edge is not None else None,
        "edge_percent": str((edge * Decimal("100")).quantize(Decimal("0.01"))) if edge is not None else None,
        "on_sale_count": on_sale_count,
        "score": str(score.quantize(Decimal("0.0001"))) if score is not None else None,
        "liquidity_penalty": str(liquidity_penalty),
        "risk_level": risk["risk_level"],
        "risk_penalty": risk["risk_penalty"],
        "risk_notes": risk["risk_notes"],
        "risk_dimensions": risk["risk_dimensions"],
        "steam_intraday_signal": (row.get("intraday_stats") or {}).get("current_signal"),
        "steam_intraday_current_vs_overall": (row.get("intraday_stats") or {}).get("current_vs_overall"),
        "steam_intraday_low_hours": (row.get("intraday_stats") or {}).get("low_hours"),
        "steam_intraday_high_hours": (row.get("intraday_stats") or {}).get("high_hours"),
        "uu_intraday_signal": (uu_intraday_stats or {}).get("current_signal"),
        "uu_intraday_current_vs_overall": (uu_intraday_stats or {}).get("current_vs_overall"),
        "uu_intraday_low_hours": (uu_intraday_stats or {}).get("low_hours"),
        "uu_intraday_high_hours": (uu_intraday_stats or {}).get("high_hours"),
        "uu_intraday_sample_count": (uu_intraday_stats or {}).get("sample_count"),
    }


def conservative_net_after_cooldown(
    steam_net: Decimal | None,
    history_stats: dict[str, Any] | None,
) -> Decimal | None:
    if steam_net is None or not isinstance(history_stats, dict):
        return None
    worst_change_7d = money(history_stats.get("worst_change_7d"))
    volatility_7d = money(history_stats.get("volatility_7d"))
    if worst_change_7d is not None and worst_change_7d < 0:
        return steam_net * (Decimal("1") + worst_change_7d)
    if volatility_7d is not None:
        return steam_net * max(Decimal("0.50"), Decimal("1") - volatility_7d * Decimal("2"))
    return None


def risk_profile(
    kind: str,
    uu_price: Decimal | None,
    edge: Decimal | None,
    on_sale_count: int,
    min_on_sale_count: int,
    history_stats: dict[str, Any] | None = None,
    intraday_stats: dict[str, Any] | None = None,
    conservative_discount: Decimal | None = None,
) -> dict[str, Any]:
    penalty = Decimal("0")
    notes = []
    dimensions = {
        "price": {"level": "low", "notes": []},
        "liquidity": {"level": "low", "notes": []},
        "cooldown": {"level": "low", "notes": []},
        "data": {"level": "low", "notes": []},
        "capital": {"level": "low", "notes": []},
        "timing": {"level": "low", "notes": []},
    }

    if edge is None:
        penalty += Decimal("0.20")
        notes.append("missing edge")
        dimensions["data"]["level"] = "high"
        dimensions["data"]["notes"].append("missing edge")
    elif edge < Decimal("0.08"):
        penalty += Decimal("0.06")
        notes.append("thin edge")
        dimensions["price"]["level"] = "medium"
        dimensions["price"]["notes"].append("thin edge")
    elif edge > Decimal("0.35"):
        penalty += Decimal("0.03")
        notes.append("wide edge")
        dimensions["data"]["level"] = max_level(dimensions["data"]["level"], "medium")
        dimensions["data"]["notes"].append("wide edge")

    if on_sale_count < min_on_sale_count:
        penalty += Decimal("0.08")
        notes.append("low UU depth")
        dimensions["liquidity"]["level"] = "high"
        dimensions["liquidity"]["notes"].append("low UU depth")
    elif on_sale_count < 1000:
        penalty += Decimal("0.04")
        notes.append("medium UU depth")
        dimensions["liquidity"]["level"] = "medium"
        dimensions["liquidity"]["notes"].append("medium UU depth")

    if uu_price is not None and uu_price > Decimal("50"):
        penalty += Decimal("0.03")
        notes.append("higher capital lockup")
        dimensions["capital"]["level"] = "medium"
        dimensions["capital"]["notes"].append("higher capital lockup")

    if kind == "capsule":
        penalty += Decimal("0.02")
        notes.append("capsule event-cycle risk")
        dimensions["cooldown"]["level"] = max_level(dimensions["cooldown"]["level"], "medium")
        dimensions["cooldown"]["notes"].append("capsule event-cycle risk")

    if isinstance(history_stats, dict):
        volatility_7d = money(history_stats.get("volatility_7d"))
        volatility_30d = money(history_stats.get("volatility_30d"))
        worst_change_7d = money(history_stats.get("worst_change_7d"))
        change_7d = money(history_stats.get("change_7d"))
        volume_24h = int(history_stats.get("volume_24h") or 0)
        if volatility_7d is not None:
            if volatility_7d >= Decimal("0.20"):
                penalty += Decimal("0.04")
                notes.append("high 7d volatility")
                dimensions["cooldown"]["level"] = "high"
                dimensions["cooldown"]["notes"].append("high 7d volatility")
            elif volatility_7d >= Decimal("0.10"):
                penalty += Decimal("0.02")
                notes.append("moderate 7d volatility")
                dimensions["cooldown"]["level"] = max_level(dimensions["cooldown"]["level"], "medium")
                dimensions["cooldown"]["notes"].append("moderate 7d volatility")
        if volatility_30d is not None and volatility_7d is not None and volatility_7d > volatility_30d * Decimal("1.4"):
            penalty += Decimal("0.01")
            notes.append("recent momentum spike")
            dimensions["cooldown"]["level"] = max_level(dimensions["cooldown"]["level"], "medium")
            dimensions["cooldown"]["notes"].append("recent momentum spike")
        if worst_change_7d is not None and worst_change_7d <= Decimal("-0.12"):
            penalty += Decimal("0.02")
            notes.append("large historical 7d drawdown")
            dimensions["cooldown"]["level"] = max_level(dimensions["cooldown"]["level"], "medium")
            dimensions["cooldown"]["notes"].append("large historical 7d drawdown")
        elif worst_change_7d is not None and worst_change_7d <= Decimal("-0.06"):
            penalty += Decimal("0.01")
            notes.append("moderate historical 7d drawdown")
            dimensions["cooldown"]["level"] = max_level(dimensions["cooldown"]["level"], "medium")
            dimensions["cooldown"]["notes"].append("moderate historical 7d drawdown")
        if change_7d is not None and change_7d <= Decimal("-0.08"):
            penalty += Decimal("0.01")
            notes.append("recent 7d downtrend")
            dimensions["price"]["level"] = max_level(dimensions["price"]["level"], "medium")
            dimensions["price"]["notes"].append("recent 7d downtrend")
        if volume_24h and volume_24h < 25:
            penalty += Decimal("0.02")
            notes.append("thin 24h volume")
            dimensions["liquidity"]["level"] = max_level(dimensions["liquidity"]["level"], "medium")
            dimensions["liquidity"]["notes"].append("thin 24h volume")

    if isinstance(intraday_stats, dict):
        signal = str(intraday_stats.get("current_signal") or "")
        current_vs = money(intraday_stats.get("current_vs_overall"))
        sample_count = int(intraday_stats.get("sample_count") or 0)
        if signal == "sell_window":
            penalty += Decimal("0.01")
            notes.append("current hour is relatively expensive")
            dimensions["timing"]["level"] = max_level(dimensions["timing"]["level"], "medium")
            dimensions["timing"]["notes"].append("current hour is relatively expensive")
        elif sample_count >= 72 and current_vs is not None and current_vs <= Decimal("-0.015"):
            dimensions["timing"]["notes"].append("current hour is relatively cheap")

    if conservative_discount is not None:
        if conservative_discount >= Decimal("9.30"):
            penalty += Decimal("0.01")
            notes.append("conservative discount too thin")
            dimensions["cooldown"]["level"] = max_level(dimensions["cooldown"]["level"], "medium")
            dimensions["cooldown"]["notes"].append("conservative discount too thin")
        elif conservative_discount >= Decimal("8.50"):
            penalty += Decimal("0.005")
            notes.append("conservative discount is thin")
            dimensions["cooldown"]["level"] = max_level(dimensions["cooldown"]["level"], "medium")
            dimensions["cooldown"]["notes"].append("conservative discount is thin")

    if penalty >= Decimal("0.18"):
        level = "high"
    elif penalty >= Decimal("0.08"):
        level = "medium"
    else:
        level = "low"

    return {
        "risk_level": level,
        "risk_penalty": str(penalty.quantize(Decimal("0.0001"))),
        "risk_notes": notes,
        "risk_dimensions": dimensions,
    }


def max_level(current: str, candidate: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return candidate if order.get(candidate, 0) > order.get(current, 0) else current


def ensure_history_schema(connection: Any) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS radar_alert_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            query TEXT NOT NULL,
            hash_name TEXT NOT NULL,
            template_id TEXT,
            edge TEXT,
            payload TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS uu_price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            query TEXT NOT NULL,
            hash_name TEXT NOT NULL,
            template_id TEXT,
            price TEXT NOT NULL,
            on_sale_count INTEGER
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_uu_price_history_item_time
        ON uu_price_history (hash_name, created_at)
        """
    )
    connection.commit()


def record_uu_price(connection: Any, watch: dict[str, Any], row: dict[str, Any]) -> None:
    price = money(row.get("price"))
    if price is None:
        return
    connection.execute(
        """
        INSERT INTO uu_price_history (query, hash_name, template_id, price, on_sale_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(watch.get("query") or ""),
            str(row.get("hash_name") or watch.get("hash_name") or ""),
            str(row.get("id") or watch.get("template_id") or ""),
            str(price),
            int(row.get("on_sale_count") or 0),
        ),
    )
    connection.commit()


def summarize_uu_intraday(connection: Any, hash_name: str, lookback_days: int = 30) -> dict[str, Any]:
    cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - lookback_days * 86400))
    rows = connection.execute(
        """
        SELECT created_at, price, on_sale_count
        FROM uu_price_history
        WHERE hash_name = ? AND created_at >= ?
        ORDER BY created_at ASC
        """,
        (hash_name, cutoff),
    ).fetchall()
    buckets: dict[int, dict[str, Any]] = {}
    prices = []
    latest_hour = None
    for created_at, price_text, on_sale_count in rows:
        price = money(price_text)
        if price is None:
            continue
        try:
            dt_utc = datetime.strptime(str(created_at), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        hour_bj = dt_utc.astimezone(timezone(timedelta(hours=8))).hour
        bucket = buckets.setdefault(hour_bj, {"prices": [], "depth": []})
        bucket["prices"].append(price)
        bucket["depth"].append(int(on_sale_count or 0))
        prices.append(price)
        latest_hour = hour_bj

    overall = median_decimal(prices)
    if overall is None:
        return empty_uu_intraday(lookback_days)

    hour_rows = []
    for hour, bucket in buckets.items():
        hour_median = median_decimal(bucket["prices"])
        if hour_median is None:
            continue
        vs_overall = (hour_median - overall) / overall if overall else Decimal("0")
        hour_rows.append(
            {
                "hour_bj": hour,
                "median_price": str(hour_median.quantize(Decimal("0.0001"))),
                "vs_overall": str(vs_overall.quantize(Decimal("0.0001"))),
                "sample_count": len(bucket["prices"]),
                "avg_depth": int(sum(bucket["depth"]) / len(bucket["depth"])) if bucket["depth"] else None,
            }
        )

    low_hours = sorted(hour_rows, key=lambda item: money(item["vs_overall"]) or Decimal("0"))[:3]
    high_hours = sorted(hour_rows, key=lambda item: money(item["vs_overall"]) or Decimal("0"), reverse=True)[:3]
    current_bucket = next((item for item in hour_rows if item["hour_bj"] == latest_hour), None)
    current_vs = money(current_bucket.get("vs_overall")) if current_bucket else None
    return {
        "source": "uu-local-history",
        "lookback_days": lookback_days,
        "sample_count": len(prices),
        "overall_median": str(overall.quantize(Decimal("0.0001"))),
        "low_hours": low_hours,
        "high_hours": high_hours,
        "current_hour_bj": latest_hour,
        "current_vs_overall": str(current_vs.quantize(Decimal("0.0001"))) if current_vs is not None else None,
        "current_signal": uu_intraday_signal(current_vs, len(prices)),
    }


def empty_uu_intraday(lookback_days: int) -> dict[str, Any]:
    return {
        "source": "uu-local-history",
        "lookback_days": lookback_days,
        "sample_count": 0,
        "low_hours": [],
        "high_hours": [],
        "current_signal": "insufficient",
    }


def median_decimal(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    sorted_values = sorted(values)
    midpoint = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[midpoint]
    return (sorted_values[midpoint - 1] + sorted_values[midpoint]) / Decimal("2")


def uu_intraday_signal(current_vs_overall: Decimal | None, sample_count: int) -> str:
    if sample_count < 24 or current_vs_overall is None:
        return "insufficient"
    if current_vs_overall <= Decimal("-0.01"):
        return "buy_window"
    if current_vs_overall >= Decimal("0.01"):
        return "expensive_window"
    return "neutral"


def last_alert(connection: Any, query: str, hash_name: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT created_at, edge, payload
        FROM radar_alert_history
        WHERE query = ? AND hash_name = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (query, hash_name),
    ).fetchone()
    if row is None:
        return None
    return {"created_at": row[0], "edge": row[1], "payload": row[2]}


def record_alert(connection: Any, item: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO radar_alert_history (query, hash_name, template_id, edge, payload)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            item.get("query") or "",
            item.get("hash_name") or "",
            str(item.get("template_id") or ""),
            str(item.get("edge") or ""),
            json.dumps(item, ensure_ascii=False),
        ),
    )
    connection.commit()


def should_notify(config: RadarConfig, connection: Any, item: dict[str, Any]) -> bool:
    current_edge = money(item.get("edge"))
    if current_edge is None:
        return False

    previous = last_alert(connection, str(item.get("query") or ""), str(item.get("hash_name") or ""))
    if previous is None:
        return True

    previous_edge = money(previous.get("edge"))
    if previous_edge is None:
        return True

    current_time = time.time()
    try:
        created_at = time.mktime(time.strptime(previous["created_at"], "%Y-%m-%d %H:%M:%S"))
    except Exception:
        created_at = 0

    cooldown_seconds = config.push_cooldown_hours * 3600
    if current_time - created_at >= cooldown_seconds:
        return True

    if current_edge - previous_edge >= config.repush_delta_edge:
        return True

    return False


def run_radar() -> dict[str, Any]:
    config = load_config()
    headers = build_headers()
    watchlist = load_watchlist(config.watchlist_file, config.limit)
    candidates = []
    notifications = []
    errors = []

    cache_connection = sqlite3.connect(config.cache_db)
    try:
        init_cache(cache_connection)
        ensure_history_schema(cache_connection)
    except Exception:
        cache_connection.close()
        raise

    try:
        for index, watch in enumerate(watchlist, start=1):
            if index > 1:
                time.sleep(float(random.uniform(float(config.sleep_min), float(config.sleep_max))))
            query = str(watch.get("query"))
            try:
                rows = query_sale_template(headers, query, config.page_size)
                exact = choose_exact(query, rows)
                if exact is not None:
                    record_uu_price(cache_connection, watch, exact)
                    uu_intraday_stats = summarize_uu_intraday(
                        cache_connection,
                        str(exact.get("hash_name") or watch.get("hash_name") or ""),
                    )
                    exact = enrich_with_steam(exact, config)
                    candidate = score_row(watch, exact, config.min_on_sale_count, uu_intraday_stats)
                    edge = money(candidate.get("edge"))
                    if edge is not None and edge >= config.min_edge:
                        candidates.append(candidate)
                        if should_notify(config, cache_connection, candidate):
                            notifications.append(candidate)
                            record_alert(cache_connection, candidate)
            except Exception as exc:  # noqa: BLE001 - CLI should collect per-item failures.
                errors.append({"query": query, "error": str(exc)})
                if "403" in str(exc) or "429" in str(exc):
                    break

        candidates.sort(key=lambda item: Decimal(item.get("score") or "-999"), reverse=True)
        notifications.sort(key=lambda item: Decimal(item.get("score") or "-999"), reverse=True)
        output = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "watchlist_checked": len(watchlist),
            "candidate_count": len(candidates),
            "notification_count": len(notifications),
            "steam_cache_file": str(config.steam_market_cache_file),
            "min_edge": str(config.min_edge),
            "candidates": candidates,
            "notifications": notifications,
            "errors": errors,
        }
        config.output_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        return output
    finally:
        cache_connection.close()


def sample_uu_prices() -> dict[str, Any]:
    config = load_config()
    headers = build_headers()
    watchlist = load_watchlist(config.watchlist_file, config.limit)
    sampled = []
    errors = []

    cache_connection = sqlite3.connect(config.cache_db)
    try:
        init_cache(cache_connection)
        ensure_history_schema(cache_connection)
    except Exception:
        cache_connection.close()
        raise

    try:
        for index, watch in enumerate(watchlist, start=1):
            if index > 1:
                time.sleep(float(random.uniform(float(config.sleep_min), float(config.sleep_max))))
            query = str(watch.get("query"))
            try:
                rows = query_sale_template(headers, query, config.page_size)
                exact = choose_exact(query, rows)
                if exact is None:
                    errors.append({"query": query, "error": "no matching UU item"})
                    continue
                record_uu_price(cache_connection, watch, exact)
                stats = summarize_uu_intraday(
                    cache_connection,
                    str(exact.get("hash_name") or watch.get("hash_name") or ""),
                )
                sampled.append(
                    {
                        "query": query,
                        "hash_name": exact.get("hash_name") or watch.get("hash_name"),
                        "name": exact.get("name"),
                        "uu_price": str(money(exact.get("price")) or ""),
                        "on_sale_count": int(exact.get("on_sale_count") or 0),
                        "uu_intraday_sample_count": stats.get("sample_count"),
                        "uu_intraday_signal": stats.get("current_signal"),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - collect per-item failures.
                errors.append({"query": query, "error": str(exc)})
                if "403" in str(exc) or "429" in str(exc):
                    break

        return {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mode": "uu_sample",
            "watchlist_checked": len(watchlist),
            "sampled_count": len(sampled),
            "sampled": sampled,
            "errors": errors,
        }
    finally:
        cache_connection.close()


def main() -> int:
    output = run_radar()
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if not output["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
