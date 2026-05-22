from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any


DEFAULT_ARCHIVE_DB = Path(__file__).with_name("bot_log_archive.sqlite3")

IMPORTANT_WARNING_MARKERS = (
    "failed",
    "failure",
    "timeout",
    "exception",
    "traceback",
    "error",
    "api",
    "websocket",
    "失败",
    "异常",
    "错误",
    "超时",
    "连接意外关闭",
)

IGNORE_PATTERNS = (
    re.compile(r"Ascii2d session_id 未配置"),
    re.compile(r"Plugin .* is disabled"),
    re.compile(r"Config key missing; added default"),
    re.compile(r"Config key order fixed"),
    re.compile(r"PIL\.PngImagePlugin"),
    re.compile(r"STREAM b'"),
)

REDACTION_PATTERNS = (
    re.compile(r"abk_[A-Za-z0-9_\-.]+"),
    re.compile(r"sk_live_[A-Za-z0-9_\-.]+"),
    re.compile(r"sk-[A-Za-z0-9_\-.]+"),
    re.compile(r"(?i)(authorization|cookie|token|password|api[_-]?key)(['\"]?\s*[:=]\s*['\"]?)[^,'\"\s}]+"),
)


def bot_log_alerts(
    archive_db: Path | None = None,
    *,
    lookback_minutes: int | None = None,
    max_lines: int | None = None,
    cooldown_minutes: int | None = None,
    alert_limit: int | None = None,
) -> dict[str, Any]:
    archive_db = archive_db or Path(os.environ.get("BOT_LOG_ARCHIVE_DB", str(DEFAULT_ARCHIVE_DB)))
    lookback_minutes = lookback_minutes or int(os.environ.get("BOT_LOG_LOOKBACK_MINUTES", "30"))
    max_lines = max_lines or int(os.environ.get("BOT_LOG_MAX_LINES", "800"))
    cooldown_minutes = cooldown_minutes or int(os.environ.get("BOT_LOG_ALERT_COOLDOWN_MINUTES", "360"))
    alert_limit = alert_limit or int(os.environ.get("BOT_LOG_ALERT_LIMIT", "6"))
    unit = os.environ.get("BOT_LOG_JOURNAL_UNIT", "astrbot.service")

    command_result = read_journal_lines(unit=unit, lookback_minutes=lookback_minutes, max_lines=max_lines)
    groups = group_log_events(command_result["lines"])
    if not command_result["ok"]:
        groups.insert(
            0,
            {
                "signature": signature_for("journalctl_error", command_result["error"] or "unknown"),
                "category": "journalctl_error",
                "count": 1,
                "sample": f"读取 AstrBot 日志失败：{command_result['error'] or 'unknown'}",
                "normalized": command_result["error"] or "unknown",
            },
        )

    ensure_archive_db(archive_db)
    alerts = record_and_select_alerts(
        archive_db,
        groups,
        cooldown_minutes=cooldown_minutes,
        alert_limit=alert_limit,
    )
    ok = command_result["ok"]
    return {
        "ok": ok,
        "title": "Bot 异常归档",
        "lookback_minutes": lookback_minutes,
        "scanned_lines": command_result["scanned_lines"],
        "matched_event_count": sum(group["count"] for group in groups),
        "group_count": len(groups),
        "notification_count": len(alerts),
        "alerts": alerts,
        "message": format_bot_log_alerts(lookback_minutes, alerts, groups, ok=ok),
    }


def read_journal_lines(*, unit: str, lookback_minutes: int, max_lines: int) -> dict[str, Any]:
    command = [
        "journalctl",
        "-u",
        unit,
        "--since",
        f"{lookback_minutes} minutes ago",
        "-n",
        str(max_lines),
        "--no-pager",
        "-o",
        "short-iso",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    except Exception as exc:  # noqa: BLE001 - alert endpoint should report log read failures.
        return {"ok": False, "lines": [], "scanned_lines": 0, "error": str(exc)}
    lines = completed.stdout.splitlines()
    if completed.returncode != 0:
        return {
            "ok": False,
            "lines": lines,
            "scanned_lines": len(lines),
            "error": (completed.stderr or completed.stdout or f"journalctl exited {completed.returncode}").strip()[:500],
        }
    return {"ok": True, "lines": lines, "scanned_lines": len(lines), "error": ""}


def group_log_events(lines: list[str]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for raw in lines:
        clean = clean_journal_line(raw)
        category = classify_line(clean)
        if not category:
            continue
        sample = redact(clean)
        normalized = normalize_for_signature(sample)
        sig = signature_for(category, normalized)
        entry = grouped.setdefault(
            sig,
            {
                "signature": sig,
                "category": category,
                "count": 0,
                "sample": sample,
                "normalized": normalized,
            },
        )
        entry["count"] += 1
    return sorted(grouped.values(), key=lambda item: (-int(item["count"]), item["category"], item["sample"]))


def classify_line(line: str) -> str | None:
    if not line.strip():
        return None
    if any(pattern.search(line) for pattern in IGNORE_PATTERNS):
        return None
    lowered = line.lower()
    if "traceback" in lowered:
        return "traceback"
    if "websocket api call timeout" in lowered:
        return "websocket_timeout"
    if "failed_precondition" in lowered or "user location is not supported" in lowered:
        return "llm_region_error"
    if "模型生成内容未通过" in line:
        return "llm_safety_block"
    if "[erro]" in lowered or "[error]" in lowered:
        return "error"
    if "exception" in lowered:
        return "exception"
    if "[warn]" in lowered and any(marker in lowered or marker in line for marker in IMPORTANT_WARNING_MARKERS):
        return "warning"
    return None


def clean_journal_line(line: str) -> str:
    line = re.sub(r"^\S+\s+\S+\s+\S+\[\d+\]:\s*", "", line)
    line = re.sub(r"^\w{3}\s+\d+\s+\d+:\d+:\d+\s+\S+\s+\S+\[\d+\]:\s*", "", line)
    return line.strip()


def normalize_for_signature(line: str) -> str:
    line = re.sub(r"\[[0-9:.+\- TZ]+\]\s*", "", line)
    line = re.sub(r"\b\d{6,}\b", "<num>", line)
    line = re.sub(r"0x[0-9A-Fa-f]+", "<hex>", line)
    line = re.sub(r"id=[^,\s)]+", "id=<id>", line)
    line = re.sub(r"group:[^:\s]+:user:[^:\s]+", "group:<id>:user:<id>", line)
    line = re.sub(r"/home/ubuntu/[^,\s)]+", "<path>", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip()[:500]


def redact(line: str) -> str:
    value = line
    for pattern in REDACTION_PATTERNS:
        if pattern.groups:
            value = pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", value)
        else:
            value = pattern.sub("<redacted>", value)
    return value


def signature_for(category: str, normalized: str) -> str:
    digest = hashlib.sha256(f"{category}\n{normalized}".encode("utf-8", errors="ignore")).hexdigest()
    return digest[:20]


def ensure_archive_db(db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_log_alert_history (
                signature TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                last_alert_at TEXT NOT NULL,
                alert_count INTEGER NOT NULL DEFAULT 0,
                total_count INTEGER NOT NULL DEFAULT 0,
                sample TEXT NOT NULL,
                normalized TEXT NOT NULL
            )
            """,
        )


def record_and_select_alerts(
    db: Path,
    groups: list[dict[str, Any]],
    *,
    cooldown_minutes: int,
    alert_limit: int,
) -> list[dict[str, Any]]:
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    alerts: list[dict[str, Any]] = []
    with sqlite3.connect(db) as connection:
        for group in groups:
            existing = connection.execute(
                "SELECT last_alert_at, alert_count, total_count FROM bot_log_alert_history WHERE signature = ?",
                (group["signature"],),
            ).fetchone()
            should_alert = False
            if existing is None:
                should_alert = True
                connection.execute(
                    """
                    INSERT INTO bot_log_alert_history (
                        signature, category, first_seen, last_seen, last_alert_at,
                        alert_count, total_count, sample, normalized
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        group["signature"],
                        group["category"],
                        now,
                        now,
                        now,
                        1,
                        int(group["count"]),
                        group["sample"],
                        group["normalized"],
                    ),
                )
            else:
                last_alert_at, alert_count, total_count = existing
                age_minutes = minutes_since(last_alert_at)
                should_alert = age_minutes is None or age_minutes >= cooldown_minutes
                connection.execute(
                    """
                    UPDATE bot_log_alert_history
                    SET last_seen = ?,
                        last_alert_at = CASE WHEN ? THEN ? ELSE last_alert_at END,
                        alert_count = alert_count + CASE WHEN ? THEN 1 ELSE 0 END,
                        total_count = ?,
                        sample = ?,
                        normalized = ?
                    WHERE signature = ?
                    """,
                    (
                        now,
                        1 if should_alert else 0,
                        now,
                        1 if should_alert else 0,
                        int(total_count or 0) + int(group["count"]),
                        group["sample"],
                        group["normalized"],
                        group["signature"],
                    ),
                )
            if should_alert and len(alerts) < alert_limit:
                alerts.append(
                    {
                        "signature": group["signature"],
                        "category": group["category"],
                        "count": int(group["count"]),
                        "sample": group["sample"][:360],
                    },
                )
    return alerts


def minutes_since(value: str | None) -> float | None:
    if not value:
        return None
    try:
        ts = time.mktime(time.strptime(value, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None
    return max(0.0, (time.time() - ts) / 60)


def format_bot_log_alerts(
    lookback_minutes: int,
    alerts: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    *,
    ok: bool,
) -> str:
    if not alerts:
        return f"Bot 异常归档：最近 {lookback_minutes} 分钟没有新增异常。"
    total = sum(int(item["count"]) for item in groups)
    lines = [
        "Bot 异常归档",
        f"窗口: 最近 {lookback_minutes} 分钟",
        f"新增: {len(alerts)} 类；匹配日志: {total} 条",
    ]
    if not ok:
        lines.append("状态: 日志读取异常")
    for index, alert in enumerate(alerts, 1):
        lines.append(f"{index}. {alert['category']} x{alert['count']}: {alert['sample']}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(json.dumps(bot_log_alerts(), ensure_ascii=False, indent=2))
