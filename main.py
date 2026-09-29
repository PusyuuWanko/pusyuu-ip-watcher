import asyncio
import logging
import os
import re
import html
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from checker import run_check
from server_status_checker import evaluate_server_status, fetch_server_status
from state_store import StateStore
from dns_config import (
    config_mtime,
    is_auto_update_a,
    load_dns_config,
    migrate_legacy_env,
    server_status_settings,
    watch_targets,
)
from dns_provider import XServerProvider, MyDNSProvider, ProviderSyncResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ip-watcher")

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
load_dotenv(BASE_DIR / ".env")

CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))

# DNS provider configuration. v3 single-domain environment variables are
# migrated once into dns_targets.json so existing installations keep working.
migrate_legacy_env(dict(os.environ))
DNS_CONFIG: dict = {"xserver": {"enabled": False, "domains": []}, "mydns": {"enabled": False}}
DNS_CONFIG_ERROR: str | None = None
_DNS_CONFIG_MTIME: float | None = None


def reload_dns_config(force: bool = False) -> None:
    """dns_targets.json が変更されていれば読み直す (再起動不要)。
    壊れたJSONを保存した場合は、直前の正常な設定を使い続けてエラーを表示する。"""
    global DNS_CONFIG, DNS_CONFIG_ERROR, _DNS_CONFIG_MTIME
    mtime = config_mtime()
    if not force and mtime is not None and mtime == _DNS_CONFIG_MTIME:
        return
    try:
        DNS_CONFIG = load_dns_config()
        DNS_CONFIG_ERROR = None
        if _DNS_CONFIG_MTIME is not None:
            logger.info("dns_targets.json を再読み込みしました")
    except Exception as exc:
        DNS_CONFIG_ERROR = str(exc)
        logger.error("%s", exc)
    _DNS_CONFIG_MTIME = config_mtime()


reload_dns_config(force=True)

XSERVER_API_KEY = os.getenv("XSERVER_API_KEY", "").strip()
XSERVER_MIN_SYNC_INTERVAL_SECONDS = int(os.getenv("XSERVER_MIN_SYNC_INTERVAL_SECONDS", "300"))
# MyDNS.JP requires periodic IP notifications. Notify once per day by default.
MYDNS_NOTIFY_INTERVAL_SECONDS = int(os.getenv("MYDNS_NOTIFY_INTERVAL_SECONDS", "86400"))


def xserver_enabled() -> bool:
    return bool(DNS_CONFIG.get("xserver", {}).get("enabled", False))


DOMAIN_DEFAULT_STATE = {
    "timestamp": None,
    "global_ip": None,
    "domains": [],
    "domain_count": 0,
    "mismatched_domains": [],
    "domain_mismatch": False,
    "previous_global_ip": None,
    "ip_changed": False,
    "error": None,
    "dns_update_enabled": False,
    "dns_update_attempted": False,
    "dns_update_success": None,
    "dns_update_message": None,
    "dns_update_timestamp": None,
    "dns_update_last_ip": None,
    "dns_update_records": [],
    "dns_update_reason": None,
    "dns_update_action": None,
    "dns_update_providers": [],
    "mydns_last_notification_timestamp": None,
    "mydns_last_notification_ip": None,
    "mydns_notification_success": None,
    "mydns_notification_message": None,
    "history": [],
}

SERVER_DEFAULT_STATE = {
    "timestamp": None,
    "url": None,
    "http_ok": None,
    "http_status_code": None,
    "error": None,
    "power": None,
    "battery": None,
    "status": None,
    "remaining_hours": None,
    "cpu_percent": None,
    "memory_percent": None,
    "memory_used_gb": None,
    "memory_total_gb": None,
    "disk_percent": None,
    "disk_used_gb": None,
    "disk_total_gb": None,
    "power_on_battery": False,
    "battery_low": False,
    "remaining_hours_low": False,
    "cpu_high": False,
    "memory_high": False,
    "disk_high": False,
    "history": [],
}

domain_store = StateStore(BASE_DIR / "domain_state.json", DOMAIN_DEFAULT_STATE)
server_store = StateStore(BASE_DIR / "server_state.json", SERVER_DEFAULT_STATE)


def provider_targets() -> list[dict]:
    return DNS_CONFIG.get("xserver", {}).get("domains", [])


def xserver_configured() -> bool:
    return xserver_enabled() and bool(XSERVER_API_KEY) and any(
        is_auto_update_a(r) for d in provider_targets() for r in d.get("records", [])
    )


async def update_xserver_dns(global_ip: str, xserver_mismatch: bool, ip_changed: bool) -> dict:
    """Run XServer and MyDNS according to their different DDNS semantics."""
    timestamp = datetime.now(timezone.utc).isoformat()
    current = domain_store.get()

    last_xserver_ip = current.get("dns_update_last_ip")
    last_xserver_timestamp = current.get("dns_update_timestamp")
    mydns_last_timestamp = current.get("mydns_last_notification_timestamp")
    mydns_last_success = current.get("mydns_notification_success")

    xserver_on = bool(DNS_CONFIG.get("xserver", {}).get("enabled", False))
    mydns_on = bool(DNS_CONFIG.get("mydns", {}).get("enabled", False))
    any_provider_on = xserver_on or mydns_on

    result = {
        "dns_update_enabled": any_provider_on,
        "dns_update_attempted": False,
        "dns_update_success": current.get("dns_update_success"),
        "dns_update_message": current.get("dns_update_message"),
        "dns_update_timestamp": last_xserver_timestamp,
        "dns_update_last_ip": last_xserver_ip,
        "dns_update_records": current.get("dns_update_records", []),
        "dns_update_reason": current.get("dns_update_reason"),
        "dns_update_action": current.get("dns_update_action"),
        "dns_update_providers": [],
        "mydns_last_notification_timestamp": mydns_last_timestamp,
        "mydns_last_notification_ip": current.get("mydns_last_notification_ip"),
        "mydns_notification_success": mydns_last_success,
        "mydns_notification_message": current.get("mydns_notification_message"),
    }

    if DNS_CONFIG_ERROR:
        result.update(
            dns_update_success=False,
            dns_update_message=DNS_CONFIG_ERROR,
            dns_update_reason="DNS設定ファイルを読み込めません。",
            dns_update_action="dns_targets.json のJSON形式を確認してください。",
        )
        return result

    if not any_provider_on:
        result.update(
            dns_update_success=None,
            dns_update_message="DNS自動更新は無効です",
            dns_update_reason="安全のため、初期状態ではDNSを書き換えません。",
            dns_update_action="dns_targets.json で使用するProviderを有効化してください。",
        )
        return result

    # XServer: explicit DNS-record synchronization on IP change or mismatch.
    should_sync_xserver = xserver_on and (xserver_mismatch or ip_changed)
    if should_sync_xserver and last_xserver_ip == global_ip and current.get("dns_update_success") is True and last_xserver_timestamp:
        try:
            elapsed = (
                datetime.now(timezone.utc) - datetime.fromisoformat(last_xserver_timestamp)
            ).total_seconds()
            if elapsed < XSERVER_MIN_SYNC_INTERVAL_SECONDS:
                should_sync_xserver = False
        except ValueError:
            pass

    # MyDNS: HTTP-BASIC is an IP notification mechanism, not a record PUT.
    # It must be called periodically even when the IP has not changed.
    should_notify_mydns = False
    if mydns_on:
        if not mydns_last_timestamp or mydns_last_success is not True:
            should_notify_mydns = True
        else:
            try:
                elapsed = (
                    datetime.now(timezone.utc) - datetime.fromisoformat(mydns_last_timestamp)
                ).total_seconds()
                should_notify_mydns = elapsed >= MYDNS_NOTIFY_INTERVAL_SECONDS
            except ValueError:
                should_notify_mydns = True
        if ip_changed:
            should_notify_mydns = True

    if not should_sync_xserver and not should_notify_mydns:
        result["dns_update_success"] = True
        result["dns_update_message"] = (
            f"DNS Providerは待機中です。XServerはIP変更/不一致時、"
            f"MyDNSは{MYDNS_NOTIFY_INTERVAL_SECONDS}秒ごとに通知します。"
        )
        result["dns_update_reason"] = (
            "XServerはDNSレコード更新型、MyDNSは定期的なIP通知型で動作が異なります。"
        )
        result["dns_update_action"] = "現在は正常です。各Providerの次回実行条件を待ちます。"
        result["dns_update_providers"] = current.get("dns_update_providers", [])
        return result

    providers = []
    provider_results = []

    if should_sync_xserver:
        if XSERVER_API_KEY:
            providers.append(XServerProvider(XSERVER_API_KEY, provider_targets()))
        else:
            provider_results.append({
                "provider": "XServer",
                "ok": False,
                "message": "XSERVER_API_KEY が設定されていません",
                "reason": "XServer APIを呼び出す認証情報がありません。",
                "action": ".env の XSERVER_API_KEY にXServerで発行したAPIキーを設定してください。",
            })

    if should_notify_mydns:
        providers.append(MyDNSProvider())

    all_records = []
    all_ok = all(x["ok"] for x in provider_results)
    messages = [x["message"] for x in provider_results]

    for provider in providers:
        pr = await provider.sync(global_ip)
        provider_results.append({
            "provider": pr.provider,
            "ok": pr.ok,
            "message": pr.message,
            "reason": pr.reason,
            "action": pr.action,
        })
        if pr.records:
            all_records.extend(pr.records)
        messages.append(f"{pr.provider}: {pr.message}")
        all_ok = all_ok and pr.ok

        if pr.provider == "MyDNS":
            result["mydns_notification_success"] = pr.ok
            result["mydns_notification_message"] = pr.message
            if pr.ok:
                result["mydns_last_notification_timestamp"] = timestamp
                result["mydns_last_notification_ip"] = global_ip

    if should_sync_xserver:
        result["dns_update_attempted"] = True
        result["dns_update_timestamp"] = timestamp
        result["dns_update_last_ip"] = global_ip

    result["dns_update_success"] = all_ok
    result["dns_update_records"] = all_records
    result["dns_update_providers"] = provider_results
    result["dns_update_message"] = " / ".join(messages) if messages else "更新対象Providerがありません"

    failed = [x for x in provider_results if not x["ok"]]
    if failed:
        result["dns_update_reason"] = failed[0].get("reason")
        result["dns_update_action"] = failed[0].get("action")
    else:
        result["dns_update_reason"] = (
            "XServerは必要時のみDNSレコードを更新し、MyDNSは定期的にIPv4通知を送信します。"
        )
        result["dns_update_action"] = "通常は対応不要です。"

    logger.info("DNS provider sync: %s", result["dns_update_message"])
    return result


async def check_domain():
    reload_dns_config()
    previous_ip = domain_store.get().get("global_ip")
    targets = watch_targets(DNS_CONFIG)
    result = await run_check(targets, previous_ip)
    result["error"] = None

    # XServerが管理しているFQDNのどれか1つでも不一致なら XServer 同期を行う
    xserver_mismatch = any(
        d["mismatch"] and "XServer" in d.get("providers", []) for d in result["domains"]
    )
    dns_update = await update_xserver_dns(result["global_ip"], xserver_mismatch, result["ip_changed"])
    result.update(dns_update)

    # Keep the local DNS mismatch information. DNS caches can still show the
    # old value for a while after an XServer update.
    domain_store.update(
        result,
        {
            "timestamp": result["timestamp"],
            "global_ip": result["global_ip"],
            "domain_count": result["domain_count"],
            "mismatched_domains": result["mismatched_domains"],
            "domain_mismatch": result["domain_mismatch"],
            "ip_changed": result["ip_changed"],
            "dns_update_success": result["dns_update_success"],
            "dns_update_message": result["dns_update_message"],
            "dns_update_timestamp": result["dns_update_timestamp"],
            "dns_update_last_ip": result["dns_update_last_ip"],
            "dns_update_reason": result.get("dns_update_reason"),
            "dns_update_action": result.get("dns_update_action"),
            "dns_update_providers": result.get("dns_update_providers", []),
            "mydns_last_notification_timestamp": result.get("mydns_last_notification_timestamp"),
            "mydns_last_notification_ip": result.get("mydns_last_notification_ip"),
            "mydns_notification_success": result.get("mydns_notification_success"),
            "mydns_notification_message": result.get("mydns_notification_message"),
        },
    )
    if result["domain_mismatch"]:
        logger.warning(
            "Domain mismatch (%d/%d): global=%s names=%s",
            len(result["mismatched_domains"]), result["domain_count"],
            result["global_ip"], ", ".join(result["mismatched_domains"]),
        )
    if result["ip_changed"]:
        logger.warning(
            "Global IP changed: %s -> %s", result["previous_global_ip"], result["global_ip"]
        )


async def check_server_status():
    reload_dns_config()
    cfg = server_status_settings(DNS_CONFIG)
    if not cfg["enabled"]:
        return
    raw = await fetch_server_status(cfg["url"], cfg["timeout_seconds"])
    result = evaluate_server_status(raw)
    result["url"] = cfg["url"]
    server_store.update(
        result,
        {
            "timestamp": result["timestamp"],
            "url": result["url"],
            "http_ok": result["http_ok"],
            "power": result["power"],
            "cpu_percent": result["cpu_percent"],
            "memory_percent": result["memory_percent"],
            "disk_percent": result["disk_percent"],
        },
    )
    if not result["http_ok"]:
        logger.warning("Server status endpoint unhealthy: %s", result.get("error"))
    for flag in (
        "power_on_battery",
        "battery_low",
        "remaining_hours_low",
        "cpu_high",
        "memory_high",
        "disk_high",
    ):
        if result[flag]:
            logger.warning("Server status alert: %s", flag)


async def watch_loop():
    while True:
        try:
            await check_domain()
        except Exception:
            logger.exception("Domain check failed")
            domain_store.record_error("ドメインチェックに失敗しました。サーバーログを確認してください。")

        try:
            await check_server_status()
        except Exception:
            logger.exception("Server status check failed")
            server_store.record_error("サーバー状態チェックに失敗しました。サーバーログを確認してください。")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(watch_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass



def build_readme_html() -> str:
    """Create a dependency-free HTML view of README.md for the local guide."""
    readme = BASE_DIR / "README.md"
    if not readme.exists():
        return "<h1>README.md が見つかりません</h1>"
    lines = readme.read_text(encoding="utf-8").splitlines()
    out = []
    in_code = False
    table_mode = False
    for line in lines:
        if line.startswith("```"):
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                out.append("<pre><code>")
                in_code = True
            continue
        if in_code:
            out.append(html.escape(line) + "\n")
            continue
        if not line.strip():
            if table_mode:
                out.append("</table>")
                table_mode = False
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r"[-: ]+", c or " ") for c in cells):
                continue
            tag = "th" if not table_mode else "td"
            if not table_mode:
                out.append("<table><thead><tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in cells) + "</tr></thead><tbody>")
                table_mode = True
            else:
                out.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>")
            continue
        if table_mode:
            out.append("</tbody></table>")
            table_mode = False
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1)); text = inline_md(m.group(2))
            out.append(f'<h{level} id="{slugify(m.group(2))}">{text}</h{level}>')
            continue
        if re.match(r"^[-*]\s+", line):
            item = re.sub(r"^[-*]\s+", "", line)
            if not out or not out[-1].startswith("<ul>"):
                out.append("<ul>")
            out.append(f"<li>{inline_md(item)}</li>")
            continue
        if line.startswith("> "):
            out.append(f"<blockquote>{inline_md(line[2:])}</blockquote>")
            continue
        out.append(f"<p>{inline_md(line)}</p>")
    if in_code: out.append("</code></pre>")
    if table_mode: out.append("</tbody></table>")
    # Close any simple list left open.
    result = "\n".join(out).replace("</li>\n<p>", "</li></ul>\n<p>")
    if result.rstrip().endswith("</li>"):
        result += "</ul>"
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Pusyuu IP Watcher - 使い方</title><style>{GUIDE_CSS}</style></head><body><nav><a href="/">← ダッシュボードへ戻る</a></nav><article>{result}</article></body></html>"""


def inline_md(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    text = re.sub(r"(?<![\"=])(https?://[^\s<]+)", r'<a href="\1" target="_blank" rel="noopener">\1</a>', text)
    return text


def slugify(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-").lower() or "section"


GUIDE_CSS = """
body{font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.7;max-width:1000px;margin:0 auto;padding:24px;color:#222;background:#f7f7f7}
nav{position:sticky;top:0;background:#f7f7f7;padding:10px 0;border-bottom:1px solid #ddd}article{background:white;padding:28px;border-radius:12px;box-shadow:0 2px 10px #0001}h1,h2,h3{line-height:1.3;margin-top:1.6em}code{background:#eee;padding:2px 5px;border-radius:4px}pre{background:#171717;color:#eee;padding:16px;overflow:auto;border-radius:8px}pre code{background:transparent;padding:0}table{border-collapse:collapse;width:100%;margin:16px 0}th,td{border:1px solid #ccc;padding:8px;text-align:left}th{background:#eee}a{color:#06c}blockquote{border-left:4px solid #aaa;padding-left:12px;color:#555}
"""

app = FastAPI(title="IP Watcher", lifespan=lifespan)


@app.get("/api/status")
async def get_status():
    return {"domain": domain_store.get(), "server": server_store.get()}


@app.post("/api/check")
async def force_check():
    await check_domain()
    await check_server_status()
    return {"domain": domain_store.get(), "server": server_store.get()}


@app.get("/guide")
async def guide():
    from fastapi.responses import HTMLResponse
    return HTMLResponse(build_readme_html())


@app.get("/api/config")
async def get_config():
    reload_dns_config()
    return {
        "watch_domains": watch_targets(DNS_CONFIG),
        "check_interval_seconds": CHECK_INTERVAL_SECONDS,
        "config_error": DNS_CONFIG_ERROR,
        "server_status": server_status_settings(DNS_CONFIG),
        "dns": {
            "xserver_enabled": xserver_enabled(),
            "xserver_api_key_configured": bool(XSERVER_API_KEY),
            "xserver_domains": provider_targets(),
            "mydns_enabled": bool(DNS_CONFIG.get("mydns", {}).get("enabled", False)),
            "mydns_notify_interval_seconds": MYDNS_NOTIFY_INTERVAL_SECONDS,
        },
    }


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Pusyuu IP Watcher")
    parser.add_argument("--host", default=os.getenv("WATCHER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("WATCHER_PORT", "8000")))
    args = parser.parse_args()
    uvicorn.run("main:app", host=args.host, port=args.port)
