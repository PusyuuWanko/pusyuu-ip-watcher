import asyncio
from datetime import datetime, timezone

import dns.asyncresolver
import dns.exception
import dns.resolver
import httpx

IP_ECHO_URL = "https://api.ipify.org?format=json"
DNS_SERVERS = ["8.8.8.8", "1.1.1.1"]
RESOLVE_CONCURRENCY = 8


async def _fetch_global_ip() -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(IP_ECHO_URL)
        resp.raise_for_status()
        return resp.json()["ip"]


async def _resolve_one(resolver: dns.asyncresolver.Resolver, name: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        try:
            answer = await resolver.resolve(name, "A")
            return {"ips": sorted({str(r) for r in answer}), "error": None}
        except dns.resolver.NXDOMAIN:
            return {"ips": [], "error": "NXDOMAIN (名前が存在しません)"}
        except dns.resolver.NoAnswer:
            return {"ips": [], "error": "Aレコードがありません"}
        except dns.exception.Timeout:
            return {"ips": [], "error": "DNS問い合わせがタイムアウトしました"}
        except Exception as exc:  # 1件の失敗で全体を止めない
            return {"ips": [], "error": f"{type(exc).__name__}: {exc}"}


async def run_check(targets: list[dict], previous_global_ip: str | None) -> dict:
    """targets: [{"fqdn": "www.example.com", "providers": ["XServer"]}, ...]"""
    global_ip = await _fetch_global_ip()

    resolver = dns.asyncresolver.Resolver(configure=False)
    resolver.nameservers = DNS_SERVERS
    resolver.lifetime = 5
    sem = asyncio.Semaphore(RESOLVE_CONCURRENCY)

    results = await asyncio.gather(*(_resolve_one(resolver, t["fqdn"], sem) for t in targets))

    domains = []
    for t, r in zip(targets, results):
        domains.append({
            "fqdn": t["fqdn"],
            "providers": t.get("providers", []),
            "ips": r["ips"],
            "error": r["error"],
            # 解決失敗も「一致を確認できない」ので不一致扱いにする
            "mismatch": global_ip not in r["ips"],
        })

    mismatched = [d["fqdn"] for d in domains if d["mismatch"]]
    ip_changed = previous_global_ip is not None and previous_global_ip != global_ip

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "global_ip": global_ip,
        "domains": domains,
        "domain_count": len(domains),
        "mismatched_domains": mismatched,
        "domain_mismatch": bool(mismatched),
        "previous_global_ip": previous_global_ip,
        "ip_changed": ip_changed,
    }
