from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "dns_targets.json"
EXAMPLE_PATH = BASE_DIR / "dns_targets.example.json"

DEFAULT_SERVER_STATUS_URL = "https://pusyuuwanko.com/pusyuusystem/apis/server_status"

DEFAULT_CONFIG: dict[str, Any] = {
    "server_status": {
        "enabled": True,
        "url": DEFAULT_SERVER_STATUS_URL,
        "timeout_seconds": 10,
    },
    "xserver": {
        "enabled": False,
        "domains": [
            {"domain": "pusyuuwanko.com", "records": [{"host": "@", "type": "A", "auto_update": True}]},
            {"domain": "pusyuu.com", "records": [{"host": "@", "type": "A", "auto_update": True}]},
            {"domain": "isami.moe", "records": [{"host": "@", "type": "A", "auto_update": True}]},
        ],
    },
    "mydns": {"enabled": False, "watch_domains": []},
}


def load_dns_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("DNS設定のルートはJSONオブジェクトである必要があります")
        return data
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"dns_targets.json の読み込みに失敗しました: {exc}") from exc


def config_mtime() -> float | None:
    try:
        return CONFIG_PATH.stat().st_mtime
    except OSError:
        return None


# ---------------------------------------------------------------------------
# ホスト名の正規化
# ---------------------------------------------------------------------------

def normalize_domain(domain: str) -> str:
    return str(domain or "").strip().rstrip(".").lower()


def normalize_host(host: str, domain: str) -> str:
    """"@" / "" / ドメイン名そのもの → "@"。 "www.example.com" → "www"。小文字化。"""
    d = normalize_domain(domain)
    h = str(host if host is not None else "@").strip().rstrip(".").lower()
    if h in ("", "@", d):
        return "@"
    if d and h.endswith("." + d):
        h = h[: -(len(d) + 1)]
    return h or "@"


def fqdn(host: str, domain: str) -> str:
    h = normalize_host(host, domain)
    d = normalize_domain(domain)
    return d if h == "@" else f"{h}.{d}"


def is_auto_update_a(record: dict) -> bool:
    return record.get("auto_update") is True and str(record.get("type", "A")).upper() == "A"


# ---------------------------------------------------------------------------
# 監視対象ドメイン (JSONに登録されたすべて)
# ---------------------------------------------------------------------------

def watch_targets(config: dict[str, Any]) -> list[dict[str, Any]]:
    """名前解決して自宅グローバルIPと比較する FQDN の一覧を返す。

    - xserver.domains の auto_update=true な A レコード (xserver.enabled に関係なく)
    - mydns.watch_domains に書いた FQDN
    - 任意: ルートの watch_domains に書いた FQDN
    auto_update=false のレコードは「意図的に別IPを向けている」ものなので比較しない。
    """
    seen: dict[str, dict[str, Any]] = {}

    def add(name: str, provider: str):
        name = normalize_domain(name)
        if not name:
            return
        if name not in seen:
            seen[name] = {"fqdn": name, "providers": [provider]}
        elif provider not in seen[name]["providers"]:
            seen[name]["providers"].append(provider)

    for d in (config.get("xserver") or {}).get("domains") or []:
        domain = normalize_domain(d.get("domain", ""))
        if not domain:
            continue
        for r in d.get("records") or []:
            if is_auto_update_a(r):
                add(fqdn(r.get("host", "@"), domain), "XServer")

    for name in (config.get("mydns") or {}).get("watch_domains") or []:
        add(str(name), "MyDNS")

    for name in config.get("watch_domains") or []:
        add(str(name), "監視のみ")

    return list(seen.values())


# ---------------------------------------------------------------------------
# サーバー状態API
# ---------------------------------------------------------------------------

def server_status_settings(config: dict[str, Any]) -> dict[str, Any]:
    """優先順位: dns_targets.json の server_status.url → .env の SERVER_STATUS_URL → 既定値"""
    section = config.get("server_status")
    if isinstance(section, str):  # "server_status": "https://..." の省略記法も許可
        section = {"url": section}
    if not isinstance(section, dict):
        section = {}
    url = str(section.get("url") or os.getenv("SERVER_STATUS_URL") or DEFAULT_SERVER_STATUS_URL).strip()
    try:
        timeout = float(section.get("timeout_seconds", 10))
    except (TypeError, ValueError):
        timeout = 10.0
    return {
        "enabled": section.get("enabled", True) is not False,
        "url": url,
        "timeout_seconds": timeout,
        "source": "dns_targets.json" if section.get("url") else (".env" if os.getenv("SERVER_STATUS_URL") else "既定値"),
    }


def migrate_legacy_env(env: dict[str, str]) -> bool:
    """One-time migration from v3's single XSERVER_DOMAIN/HOSTS settings."""
    if CONFIG_PATH.exists():
        return False
    enabled = env.get("XSERVER_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    domain = env.get("XSERVER_DOMAIN", env.get("WATCH_DOMAIN", "pusyuuwanko.com")).strip()
    hosts = [h.strip() for h in env.get("XSERVER_DNS_HOSTS", "@").split(",") if h.strip()]
    domains = [{"domain": domain, "records": [{"host": h, "type": "A", "auto_update": True} for h in hosts]}]
    data = {
        "server_status": {
            "enabled": True,
            "url": env.get("SERVER_STATUS_URL", DEFAULT_SERVER_STATUS_URL),
            "timeout_seconds": 10,
        },
        "xserver": {"enabled": enabled, "domains": domains},
        "mydns": {"enabled": False, "watch_domains": []},
    }
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True
