from datetime import datetime, timezone

import httpx

# 異常判定のしきい値
CPU_HIGH_THRESHOLD = 90.0
MEMORY_HIGH_THRESHOLD = 90.0
DISK_HIGH_THRESHOLD = 90.0
BATTERY_LOW_THRESHOLD = 20.0
REMAINING_HOURS_LOW_THRESHOLD = 1.0


async def fetch_server_status(url: str, timeout: float = 10.0) -> dict:
    timestamp = datetime.now(timezone.utc).isoformat()

    if not url:
        return {
            "timestamp": timestamp,
            "http_ok": False,
            "http_status_code": None,
            "error": "サーバー状態APIのURLが未設定です (dns_targets.json の server_status.url)",
            "data": {},
        }

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
    except httpx.RequestError as exc:
        return {
            "timestamp": timestamp,
            "http_ok": False,
            "http_status_code": None,
            "error": f"接続に失敗しました: {exc}",
            "data": {},
        }

    if resp.status_code != 200:
        return {
            "timestamp": timestamp,
            "http_ok": False,
            "http_status_code": resp.status_code,
            "error": f"HTTPステータス {resp.status_code} が返されました",
            "data": {},
        }

    try:
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except ValueError:
        return {
            "timestamp": timestamp,
            "http_ok": False,
            "http_status_code": resp.status_code,
            "error": "レスポンスをJSONとして解析できません",
            "data": {},
        }

    return {
        "timestamp": timestamp,
        "http_ok": True,
        "http_status_code": resp.status_code,
        "error": None,
        "data": data,
    }


def evaluate_server_status(raw: dict) -> dict:
    data = raw.get("data") or {}
    http_ok = raw["http_ok"]

    power = data.get("power")
    battery = data.get("battery")
    status = data.get("status")
    remaining_hours = data.get("remaining_hours")
    cpu_percent = data.get("cpu_percent")
    memory_percent = data.get("memory_percent")
    disk_percent = data.get("disk_percent")

    return {
        "timestamp": raw["timestamp"],
        "http_ok": http_ok,
        "http_status_code": raw["http_status_code"],
        "error": raw["error"],
        "power": power,
        "battery": battery,
        "status": status,
        "remaining_hours": remaining_hours,
        "cpu_percent": cpu_percent,
        "memory_percent": memory_percent,
        "memory_used_gb": data.get("memory_used_gb"),
        "memory_total_gb": data.get("memory_total_gb"),
        "disk_percent": disk_percent,
        "disk_used_gb": data.get("disk_used_gb"),
        "disk_total_gb": data.get("disk_total_gb"),
        "power_on_battery": http_ok and power is not None and power != "AC",
        "battery_low": http_ok and battery is not None and battery < BATTERY_LOW_THRESHOLD,
        "remaining_hours_low": (
            http_ok and remaining_hours is not None and remaining_hours < REMAINING_HOURS_LOW_THRESHOLD
        ),
        "cpu_high": http_ok and cpu_percent is not None and cpu_percent > CPU_HIGH_THRESHOLD,
        "memory_high": http_ok and memory_percent is not None and memory_percent > MEMORY_HIGH_THRESHOLD,
        "disk_high": http_ok and disk_percent is not None and disk_percent > DISK_HIGH_THRESHOLD,
    }
