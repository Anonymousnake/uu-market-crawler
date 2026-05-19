import json
import os
import random
import sqlite3
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests


API_BASE = "https://api.youpin898.com"
MARKET_FILTER_URL = f"{API_BASE}/api/youpin/commodity/v2/commodity/tag/query/list"
QUERY_SALE_TEMPLATE_URL = f"{API_BASE}/api/homepage/pc/goods/market/querySaleTemplate"
QUERY_ON_SALE_COMMODITY_URL = f"{API_BASE}/api/homepage/pc/goods/market/queryOnSaleCommodityList"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "uk",
    "deviceuk",
    "deviceid",
    "acw_tc",
}


@dataclass
class ProbeResult:
    status_code: int
    json_body: Any | None
    text_preview: str
    headers: dict[str, str]


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def build_headers() -> dict[str, str]:
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://youpin898.com",
        "referer": "https://youpin898.com/",
        "apptype": "1",
        "platform": "pc",
        "appversion": "5.26.0",
        "app-version": "5.26.0",
        "secret-v": "h5_v1",
        "user-agent": env("UU_USER_AGENT", DEFAULT_USER_AGENT),
    }

    optional = {
        "authorization": env("UU_AUTHORIZATION"),
        "uk": env("UU_UK"),
        "deviceUk": env("UU_DEVICE_UK"),
        "deviceId": env("UU_DEVICE_ID"),
        "cookie": env("UU_COOKIE"),
    }
    headers.update({key: value for key, value in optional.items() if value})
    return {key: value for key, value in headers.items() if value}


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: "<redacted>" if key.lower() in SENSITIVE_HEADERS else value
        for key, value in headers.items()
    }


def sleep_jitter(min_seconds: float = 3.0, max_seconds: float = 10.0) -> None:
    time.sleep(random.uniform(min_seconds, max_seconds))


def post_json_once(url: str, payload: dict[str, Any], headers: dict[str, str]) -> ProbeResult:
    response = requests.post(url, json=payload, headers=headers, timeout=15)
    text = response.text

    if response.status_code == 403:
        raise RuntimeError(
            "403 anti-bot/gateway rejection. Stop now and cool down for 30-60 minutes."
        )
    if response.status_code == 429:
        raise RuntimeError(
            "429 rate limit or anti-bot throttling. Stop now and cool down for 30-60 minutes."
        )

    json_body = None
    try:
        json_body = response.json()
    except ValueError:
        pass

    return ProbeResult(
        status_code=response.status_code,
        json_body=json_body,
        text_preview=text[:1000],
        headers=dict(response.headers),
    )


def post_json_batch(
    url: str,
    payloads: list[dict[str, Any]],
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    results = []
    for index, payload in enumerate(payloads, start=1):
        if index > 1:
            sleep_jitter()

        result = post_json_once(url, payload, headers)
        parsed: Any = None
        if isinstance(result.json_body, dict):
            try:
                parsed = parse_on_sale_response(result.json_body)
            except ValueError as exc:
                parsed = {"parse_error": str(exc)}
        write_cache("onsale", parsed)

        results.append(
            {
                "payload": payload,
                "response": build_response_output(result, parsed),
            }
        )
    return results


def parse_sale_template_response(body: dict[str, Any]) -> list[dict[str, Any]]:
    code = body.get("Code", body.get("code"))
    if code != 0:
        message = body.get("Msg", body.get("msg", "unknown error"))
        raise ValueError(f"non-success response: code={code}, message={message}")

    rows = body.get("Data", body.get("data", []))
    if not isinstance(rows, list):
        raise ValueError("response Data/data is not a list")

    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed.append(
            {
                "id": row.get("id"),
                "name": row.get("commodityName"),
                "hash_name": row.get("commodityHashName"),
                "price": to_decimal(row.get("price")),
                "steam_price": to_decimal(row.get("steamPrice")),
                "steam_usd_price": to_decimal(row.get("steamUsdPrice")),
                "on_sale_count": row.get("onSaleCount"),
                "on_lease_count": row.get("onLeaseCount"),
                "rent": to_decimal(row.get("rent") or row.get("leaseUnitPrice")),
                "long_rent": to_decimal(row.get("longLeaseUnitPrice")),
                "lease_deposit": to_decimal(row.get("leaseDeposit")),
                "type_name": row.get("typeName"),
                "exterior": row.get("exterior"),
                "rarity": row.get("rarity"),
                "quality": row.get("quality"),
                "icon_url": row.get("iconUrl"),
                "icon_url_large": row.get("iconUrlLarge"),
                "list_type": row.get("listType"),
            }
        )
    return parsed


def first_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("Data", "data", "List", "list", "Rows", "rows", "Items", "items"):
            rows = first_list(value.get(key))
            if rows:
                return rows
    return []


def parse_on_sale_response(body: dict[str, Any]) -> list[dict[str, Any]]:
    code = body.get("Code", body.get("code"))
    if code != 0:
        message = body.get("Msg", body.get("msg", "unknown error"))
        raise ValueError(f"non-success response: code={code}, message={message}")

    rows = [row for row in first_list(body.get("Data", body.get("data"))) if isinstance(row, dict)]
    parsed = []
    for row in rows:
        parsed.append(
            {
                "id": row.get("id") or row.get("commodityId") or row.get("assetId"),
                "template_id": row.get("templateId") or row.get("commodityTemplateId"),
                "name": row.get("commodityName") or row.get("name"),
                "hash_name": row.get("commodityHashName") or row.get("hashName"),
                "price": to_decimal(row.get("price") or row.get("salePrice") or row.get("unitPrice")),
                "seller_id": row.get("sellerId") or row.get("userId"),
                "float_value": row.get("abrasion") or row.get("floatValue"),
                "paint_seed": row.get("paintSeed") or row.get("paintseed"),
                "inspect_url": row.get("inspectUrl") or row.get("inspectUrlSteam"),
                "status": row.get("status"),
                "raw_keys": sorted(row.keys()),
            }
        )
    return parsed


def to_decimal(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(Decimal(str(value)))


def output_limit() -> int:
    raw_value = env("UU_LIMIT", "5")
    try:
        return max(1, int(raw_value))
    except ValueError:
        return 5


def build_response_output(result: ProbeResult, parsed: Any) -> dict[str, Any]:
    output_mode = env("UU_OUTPUT", "summary").lower()
    limit = output_limit()
    output: dict[str, Any] = {
        "status_code": result.status_code,
    }

    if isinstance(result.json_body, dict):
        output["code"] = result.json_body.get("Code", result.json_body.get("code"))
        output["message"] = result.json_body.get("Msg", result.json_body.get("msg"))

    if isinstance(parsed, list):
        output["item_count_in_response"] = len(parsed)
        output["items"] = parsed[:limit]
    elif parsed is not None:
        output["parsed_items"] = parsed

    if output_mode == "raw":
        output["json_body"] = result.json_body
    elif result.json_body is None:
        output["text_preview"] = result.text_preview

    return output


def cache_path() -> Path | None:
    configured = env("UU_CACHE_DB")
    if configured:
        return Path(configured)
    if env("UU_WRITE_CACHE").lower() in {"1", "true", "yes", "on"}:
        return Path(__file__).with_name("uu_market_cache.sqlite3")
    return None


def init_cache(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sale_template_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            source TEXT NOT NULL,
            template_id TEXT,
            name TEXT,
            hash_name TEXT,
            price TEXT,
            steam_price TEXT,
            steam_usd_price TEXT,
            on_sale_count INTEGER,
            on_lease_count INTEGER,
            rent TEXT,
            long_rent TEXT,
            lease_deposit TEXT,
            type_name TEXT,
            exterior TEXT,
            rarity TEXT,
            quality TEXT,
            list_type INTEGER
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS on_sale_listing_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            source TEXT NOT NULL,
            template_id TEXT,
            commodity_id TEXT,
            name TEXT,
            hash_name TEXT,
            price TEXT,
            seller_id TEXT,
            float_value TEXT,
            paint_seed TEXT,
            inspect_url TEXT,
            status TEXT
        )
        """
    )
    connection.commit()


def write_cache(mode: str, parsed: Any) -> None:
    path = cache_path()
    if path is None or not isinstance(parsed, list):
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        init_cache(connection)
        if mode == "sale":
            connection.executemany(
                """
                INSERT INTO sale_template_snapshots (
                    source, template_id, name, hash_name, price, steam_price,
                    steam_usd_price, on_sale_count, on_lease_count, rent,
                    long_rent, lease_deposit, type_name, exterior, rarity,
                    quality, list_type
                ) VALUES (
                    :source, :template_id, :name, :hash_name, :price, :steam_price,
                    :steam_usd_price, :on_sale_count, :on_lease_count, :rent,
                    :long_rent, :lease_deposit, :type_name, :exterior, :rarity,
                    :quality, :list_type
                )
                """,
                [
                    {
                        "source": "querySaleTemplate",
                        "template_id": row.get("id"),
                        **row,
                    }
                    for row in parsed
                    if isinstance(row, dict)
                ],
            )
        elif mode == "onsale":
            connection.executemany(
                """
                INSERT INTO on_sale_listing_snapshots (
                    source, template_id, commodity_id, name, hash_name, price,
                    seller_id, float_value, paint_seed, inspect_url, status
                ) VALUES (
                    :source, :template_id, :commodity_id, :name, :hash_name, :price,
                    :seller_id, :float_value, :paint_seed, :inspect_url, :status
                )
                """,
                [
                    {
                        "source": "queryOnSaleCommodityList",
                        "commodity_id": row.get("id"),
                        **row,
                    }
                    for row in parsed
                    if isinstance(row, dict)
                ],
            )
        connection.commit()
    finally:
        connection.close()
    print(f"Cache written: {path}")


def on_sale_payload(template_id: str) -> dict[str, Any]:
    return {
        "gameId": env("UU_GAME_ID", "730"),
        "listType": env("UU_LIST_TYPE", "10"),
        "templateId": template_id,
        "listSortType": int(env("UU_LIST_SORT_TYPE", "1")),
        "sortType": int(env("UU_SORT_TYPE", "0")),
        "pageIndex": int(env("UU_PAGE_INDEX", "1")),
        "pageSize": int(env("UU_PAGE_SIZE", "10")),
    }


def template_ids_from_env() -> list[str]:
    raw = env("UU_TEMPLATE_IDS")
    if raw:
        return [part.strip() for part in raw.split(",") if part.strip()]
    watchlist_file = env("UU_WATCHLIST_FILE")
    if watchlist_file:
        with open(watchlist_file, "r", encoding="utf-8") as file:
            watchlist = json.load(file)
        rows = watchlist.get("items", watchlist if isinstance(watchlist, list) else [])
        return [
            str(row["template_id"])
            for row in rows
            if isinstance(row, dict) and row.get("template_id") is not None
        ]
    return [env("UU_TEMPLATE_ID", "102276")]


def main() -> int:
    mode = env("UU_MODE", "sale").lower()
    if mode == "filter":
        url = MARKET_FILTER_URL
        payload = {"pageType": env("UU_PAGE_TYPE", "pc_goods_market")}
    elif mode == "onsale":
        url = QUERY_ON_SALE_COMMODITY_URL
        payload = on_sale_payload(template_ids_from_env()[0])
    else:
        url = QUERY_SALE_TEMPLATE_URL
        payload = {
            "listSortType": int(env("UU_LIST_SORT_TYPE", "0")),
            "sortType": int(env("UU_SORT_TYPE", "0")),
            "pageSize": int(env("UU_PAGE_SIZE", "20")),
            "pageIndex": int(env("UU_PAGE_INDEX", "1")),
        }

    headers = build_headers()
    print("Request:")
    print(json.dumps({"url": url, "payload": payload, "headers": redact_headers(headers)}, indent=2))

    if mode == "onsale" and len(template_ids_from_env()) > 1:
        payloads = [on_sale_payload(template_id) for template_id in template_ids_from_env()]
        print("Batch Response:")
        batch_results = post_json_batch(url, payloads, headers)
        print(json.dumps(batch_results, ensure_ascii=True, indent=2))
        return 0

    result = post_json_once(url, payload, headers)
    parsed = None
    if mode == "sale" and isinstance(result.json_body, dict):
        try:
            parsed = parse_sale_template_response(result.json_body)
        except ValueError as exc:
            parsed = {"parse_error": str(exc)}
    elif mode == "onsale" and isinstance(result.json_body, dict):
        try:
            parsed = parse_on_sale_response(result.json_body)
        except ValueError as exc:
            parsed = {"parse_error": str(exc)}
    write_cache(mode, parsed)
    print("Response:")
    print(json.dumps(build_response_output(result, parsed), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        raise SystemExit(2)
