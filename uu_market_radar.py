import json
import os
import random
import sqlite3
import sys
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from uu_market_probe import (
    QUERY_SALE_TEMPLATE_URL,
    build_headers,
    init_cache,
    parse_sale_template_response,
    post_json_once,
    sleep_jitter,
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


def score_row(watch: dict[str, Any], row: dict[str, Any], min_on_sale_count: int) -> dict[str, Any]:
    uu_price = money(row.get("price"))
    steam_price = money(row.get("steam_price"))
    on_sale_count = int(row.get("on_sale_count") or 0)
    steam_net = steam_price / STEAM_FEE_DIVISOR if steam_price is not None else None
    edge = (steam_net - uu_price) / uu_price if steam_net is not None and uu_price else None

    liquidity_penalty = Decimal("0")
    if on_sale_count < min_on_sale_count:
        liquidity_penalty = Decimal("0.05")

    score = edge - liquidity_penalty if edge is not None else None
    return {
        "kind": watch.get("kind"),
        "template_id": row.get("id") or watch.get("template_id"),
        "query": watch.get("query"),
        "hash_name": row.get("hash_name") or watch.get("hash_name"),
        "name": row.get("name"),
        "uu_price": str(uu_price) if uu_price is not None else None,
        "steam_price": str(steam_price) if steam_price is not None else None,
        "steam_net_after_fee": str(steam_net.quantize(Decimal("0.0001"))) if steam_net is not None else None,
        "edge": str(edge.quantize(Decimal("0.0001"))) if edge is not None else None,
        "edge_percent": str((edge * Decimal("100")).quantize(Decimal("0.01"))) if edge is not None else None,
        "on_sale_count": on_sale_count,
        "score": str(score.quantize(Decimal("0.0001"))) if score is not None else None,
        "liquidity_penalty": str(liquidity_penalty),
    }


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
    connection.commit()


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
                    candidate = score_row(watch, exact, config.min_on_sale_count)
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
            "min_edge": str(config.min_edge),
            "candidates": candidates,
            "notifications": notifications,
            "errors": errors,
        }
        config.output_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        return output
    finally:
        cache_connection.close()


def main() -> int:
    output = run_radar()
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if not output["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
