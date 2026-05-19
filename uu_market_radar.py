import json
import os
import random
import sys
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from uu_market_probe import (
    QUERY_SALE_TEMPLATE_URL,
    build_headers,
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
    limit: int
    min_edge: Decimal
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
    return RadarConfig(
        watchlist_file=Path(env("UU_WATCHLIST_FILE", str(DEFAULT_WATCHLIST))),
        output_file=Path(env("UU_RADAR_OUTPUT", str(DEFAULT_OUTPUT))),
        limit=int_env("UU_RADAR_LIMIT", "12"),
        min_edge=decimal_env("UU_MIN_EDGE", "0.03"),
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


def run_radar() -> dict[str, Any]:
    config = load_config()
    headers = build_headers()
    watchlist = load_watchlist(config.watchlist_file, config.limit)
    candidates = []
    errors = []

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
        except Exception as exc:  # noqa: BLE001 - CLI should collect per-item failures.
            errors.append({"query": query, "error": str(exc)})
            if "403" in str(exc) or "429" in str(exc):
                break

    candidates.sort(key=lambda item: Decimal(item.get("score") or "-999"), reverse=True)
    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "watchlist_checked": len(watchlist),
        "candidate_count": len(candidates),
        "min_edge": str(config.min_edge),
        "candidates": candidates,
        "errors": errors,
    }
    config.output_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def main() -> int:
    output = run_radar()
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if not output["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
