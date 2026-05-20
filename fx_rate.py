from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import requests


@dataclass
class FxRateConfig:
    cache_file: Path
    fallback_usd_cny: Decimal
    ttl_seconds: int = 21600
    timeout_seconds: int = 10


def get_usd_cny_rate(config: FxRateConfig) -> Decimal:
    cached = load_cached_rate(config.cache_file, config.ttl_seconds)
    if cached is not None:
        return cached

    for fetcher in (fetch_frankfurter, fetch_er_api):
        try:
            rate = fetcher(config.timeout_seconds)
        except Exception:
            continue
        if rate is not None and rate > 0:
            save_cached_rate(config.cache_file, rate)
            return rate

    return config.fallback_usd_cny


def load_cached_rate(path: Path, ttl_seconds: int) -> Decimal | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = float(payload.get("fetched_at", 0))
        if time.time() - fetched_at > ttl_seconds:
            return None
        return parse_decimal(payload.get("usd_cny"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def save_cached_rate(path: Path, rate: Decimal) -> None:
    payload = {
        "fetched_at": time.time(),
        "fetched_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "usd_cny": str(rate),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_frankfurter(timeout_seconds: int) -> Decimal | None:
    response = requests.get(
        "https://api.frankfurter.app/latest",
        params={"from": "USD", "to": "CNY"},
        headers={"User-Agent": "uu-market-crawler/1.0"},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    body = response.json()
    return parse_decimal((body.get("rates") or {}).get("CNY"))


def fetch_er_api(timeout_seconds: int) -> Decimal | None:
    response = requests.get(
        "https://open.er-api.com/v6/latest/USD",
        headers={"User-Agent": "uu-market-crawler/1.0"},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    body: dict[str, Any] = response.json()
    if body.get("result") != "success":
        return None
    return parse_decimal((body.get("rates") or {}).get("CNY"))


def parse_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
