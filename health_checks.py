from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import calendar
from pathlib import Path
from typing import Any


DEFAULT_DB = Path(__file__).with_name("uu_market_cache.sqlite3")


def crawler_health(cache_db: Path | None = None) -> dict[str, Any]:
    cache_db = cache_db or Path(os.environ.get("UU_CACHE_DB", str(DEFAULT_DB)))
    timer_active = systemctl_is_active("uu-market-sampler.timer")
    service = systemctl_show("uu-market-sampler.service", ["Result", "ExecMainStatus", "InactiveEnterTimestamp"])
    latest_sample = latest_sqlite_value(
        cache_db,
        "SELECT max(created_at) FROM uu_price_history",
    )
    samples_90m = sqlite_count(
        cache_db,
        "SELECT count(*) FROM uu_price_history WHERE created_at >= datetime('now', '-90 minutes')",
    )
    errors_24h = sqlite_count(
        cache_db,
        "SELECT count(*) FROM sampler_error_history WHERE created_at >= datetime('now', '-24 hours')",
    )
    latest_age_min = sqlite_age_minutes(latest_sample)
    ok = bool(timer_active) and latest_age_min is not None and latest_age_min <= 90 and int(service.get("ExecMainStatus") or 0) == 0
    problems = []
    if not timer_active:
        problems.append("uu-market-sampler.timer 未运行")
    if latest_age_min is None:
        problems.append("还没有 UU 采样记录")
    elif latest_age_min > 90:
        problems.append(f"最近采样距今 {latest_age_min:.0f} 分钟")
    if int(service.get("ExecMainStatus") or 0) != 0:
        problems.append(f"上次采样退出码 {service.get('ExecMainStatus')}")
    return {
        "ok": ok,
        "title": "UU 爬虫健康",
        "problems": problems,
        "timer_active": timer_active,
        "last_service": service,
        "latest_sample_at": latest_sample,
        "latest_sample_age_minutes": latest_age_min,
        "samples_last_90m": samples_90m,
        "errors_last_24h": errors_24h,
        "message": format_crawler_health(ok, problems, latest_sample, latest_age_min, samples_90m, errors_24h),
    }


def bot_health() -> dict[str, Any]:
    services = {
        "astrbot": systemctl_is_active("astrbot.service"),
        "uu-market-radar": systemctl_is_active("uu-market-radar.service"),
        "uu-market-sampler.timer": systemctl_is_active("uu-market-sampler.timer"),
    }
    http = {
        "astrbot_6185": http_probe("http://127.0.0.1:6185/api/v1/status", expect_statuses={200, 401, 403}),
        "uu_radar_8765": http_probe("http://127.0.0.1:8765/health"),
        "napcat_6099": http_probe("http://127.0.0.1:6099", expect_statuses={200, 301, 302, 401, 403, 404}),
    }
    listening = {
        "6185": port_listening(6185),
        "6099": port_listening(6099),
        "6199": port_listening(6199),
        "8765": port_listening(8765),
    }
    ok = all(services.values()) and http["uu_radar_8765"]["ok"] and listening["6185"] and listening["8765"]
    problems = []
    for name, active in services.items():
        if not active:
            problems.append(f"{name} 未运行")
    for name, result in http.items():
        if name in {"astrbot_6185", "uu_radar_8765"} and not result["ok"]:
            problems.append(f"{name} 不可访问: {result.get('error') or result.get('status')}")
    for port in ("6185", "8765"):
        if not listening[port]:
            problems.append(f"端口 {port} 未监听")
    return {
        "ok": ok,
        "title": "Bot 服务健康",
        "problems": problems,
        "services": services,
        "http": http,
        "listening": listening,
        "message": format_bot_health(ok, problems, services, http, listening),
    }


def server_report() -> dict[str, Any]:
    mem = memory_usage()
    root_disk = disk_usage("/")
    home_disk = disk_usage("/home")
    services = {
        "astrbot": systemctl_is_active("astrbot.service"),
        "uu-market-radar": systemctl_is_active("uu-market-radar.service"),
        "uu-market-sampler.timer": systemctl_is_active("uu-market-sampler.timer"),
        "docker": systemctl_is_active("docker.service"),
    }
    docker = docker_ps()
    failed_units = run_text(["systemctl", "--failed", "--no-legend"], timeout=10).strip()
    loadavg = Path("/proc/loadavg").read_text(encoding="utf-8").split()[:3]
    uptime_seconds = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    ok = mem["used_percent"] < 90 and root_disk["used_percent"] < 85 and not failed_units
    return {
        "ok": ok,
        "title": "服务器维护日报",
        "time": time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime()),
        "uptime_hours": round(uptime_seconds / 3600, 1),
        "loadavg": loadavg,
        "memory": mem,
        "disk": {"root": root_disk, "home": home_disk},
        "services": services,
        "docker": docker,
        "failed_units": failed_units,
        "message": format_server_report(ok, mem, root_disk, home_disk, services, docker, failed_units, loadavg, uptime_seconds),
    }


def format_crawler_health(ok: bool, problems: list[str], latest_sample: str | None, age: float | None, samples: int, errors: int) -> str:
    status = "正常" if ok else "异常"
    lines = [
        f"UU 爬虫健康：{status}",
        f"最近采样: {latest_sample or '无'}" + (f"（{age:.0f}分钟前）" if age is not None else ""),
        f"90分钟采样数: {samples}",
        f"24小时采样错误提醒: {errors}",
    ]
    if problems:
        lines.append("问题: " + "；".join(problems))
    return "\n".join(lines)


def format_bot_health(ok: bool, problems: list[str], services: dict[str, bool], http: dict[str, dict[str, Any]], listening: dict[str, bool]) -> str:
    status = "正常" if ok else "异常"
    service_text = "，".join(f"{k}:{'ok' if v else 'bad'}" for k, v in services.items())
    port_text = "，".join(f"{k}:{'on' if v else 'off'}" for k, v in listening.items())
    lines = [f"Bot 服务健康：{status}", f"服务: {service_text}", f"端口: {port_text}"]
    if problems:
        lines.append("问题: " + "；".join(problems))
    return "\n".join(lines)


def format_server_report(
    ok: bool,
    mem: dict[str, Any],
    root_disk: dict[str, Any],
    home_disk: dict[str, Any],
    services: dict[str, bool],
    docker: list[dict[str, str]],
    failed_units: str,
    loadavg: list[str],
    uptime_seconds: float,
) -> str:
    service_text = "，".join(f"{k}:{'ok' if v else 'bad'}" for k, v in services.items())
    docker_text = "，".join(item["name"] for item in docker[:8]) or "无"
    lines = [
        f"服务器维护日报：{'正常' if ok else '需关注'}",
        f"运行: {uptime_seconds / 3600:.1f}小时；负载: {'/'.join(loadavg)}",
        f"内存: {mem['used_percent']:.1f}% ({mem['used_mb']}/{mem['total_mb']} MB)",
        f"磁盘 /: {root_disk['used_percent']:.1f}%；/home: {home_disk['used_percent']:.1f}%",
        f"服务: {service_text}",
        f"Docker: {docker_text}",
    ]
    if failed_units:
        lines.append("失败单元: " + failed_units.replace("\n", "；")[:500])
    return "\n".join(lines)


def systemctl_is_active(unit: str) -> bool:
    return run_text(["systemctl", "is-active", unit], timeout=8).strip() == "active"


def systemctl_show(unit: str, properties: list[str]) -> dict[str, str]:
    output = run_text(["systemctl", "show", unit, "-p", ",".join(properties)], timeout=8)
    result = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def latest_sqlite_value(db: Path, sql: str) -> str | None:
    if not db.exists():
        return None
    try:
        with sqlite3.connect(db) as connection:
            row = connection.execute(sql).fetchone()
    except sqlite3.Error:
        return None
    return str(row[0]) if row and row[0] is not None else None


def sqlite_count(db: Path, sql: str) -> int:
    value = latest_sqlite_value(db, sql)
    try:
        return int(value or 0)
    except ValueError:
        return 0


def sqlite_age_minutes(value: str | None) -> float | None:
    if not value:
        return None
    try:
        ts = calendar.timegm(time.strptime(value, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None
    return max(0.0, (time.time() - ts) / 60)


def http_probe(url: str, expect_statuses: set[int] | None = None) -> dict[str, Any]:
    expect_statuses = expect_statuses or {200}
    request = urllib.request.Request(url, headers={"User-Agent": "uu-market-health/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            status = response.status
            response.read(128)
    except urllib.error.HTTPError as exc:
        status = exc.code
        return {"ok": status in expect_statuses, "status": status}
    except Exception as exc:  # noqa: BLE001 - health output should capture failures.
        return {"ok": False, "error": str(exc)}
    return {"ok": status in expect_statuses, "status": status}


def port_listening(port: int) -> bool:
    output = run_text(["ss", "-ltn"], timeout=8)
    return f":{port} " in output or f":{port}\n" in output


def memory_usage() -> dict[str, Any]:
    values = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, raw = line.split(":", 1)
        values[key] = int(raw.strip().split()[0])
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    used = max(0, total - available)
    return {
        "total_mb": round(total / 1024),
        "used_mb": round(used / 1024),
        "available_mb": round(available / 1024),
        "used_percent": round(used / total * 100, 1) if total else 0,
    }


def disk_usage(path: str) -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    return {
        "path": path,
        "total_gb": round(usage.total / 1024**3, 1),
        "used_gb": round(usage.used / 1024**3, 1),
        "free_gb": round(usage.free / 1024**3, 1),
        "used_percent": round(usage.used / usage.total * 100, 1) if usage.total else 0,
    }


def docker_ps() -> list[dict[str, str]]:
    output = run_text(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"], timeout=12)
    rows = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            rows.append({"name": parts[0], "status": parts[1], "image": parts[2]})
    return rows


def run_text(command: list[str], timeout: int = 10) -> str:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception:
        return ""
    return (completed.stdout or "") + (completed.stderr or "")
