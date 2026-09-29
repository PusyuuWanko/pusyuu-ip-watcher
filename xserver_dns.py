from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from dns_config import fqdn, normalize_domain, normalize_host

logger = logging.getLogger("ip-watcher.xserver")


@dataclass(frozen=True)
class DnsTarget:
    domain: str
    host: str = "@"
    record_type: str = "A"


class XServerDnsError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, code: str | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retry_after = retry_after


class XServerDnsClient:
    """XServer ドメインAPI (DNSレコード) の最小クライアント。

    - ドメインごとにレコード一覧を 1回だけ取得する (旧版はレコードごとに取得していた)
    - 1つのドメイン/レコードで失敗しても、残りのドメインの処理を続ける
    - 既存Aレコードの更新のみ。自動作成・削除はしない
    """

    BASE_URL = "https://api.xserver.ne.jp/v1/domain"
    # 公式のレート制限: 60 リクエスト/分・同時5接続 (ユーザー単位)
    REQUEST_SPACING_SECONDS = 0.3
    MAX_RETRY_AFTER_SECONDS = 15

    def __init__(self, api_key: str, timeout: float = 15.0):
        if not api_key:
            raise XServerDnsError("XSERVER_API_KEY が設定されていません")
        self.api_key = api_key
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request(self, client: httpx.AsyncClient, method: str, url: str, **kwargs) -> dict[str, Any]:
        for attempt in range(2):
            try:
                response = await client.request(method, url, headers=self.headers, **kwargs)
            except httpx.TimeoutException as exc:
                raise XServerDnsError(f"XServer API Timeout: {exc}") from exc
            except httpx.RequestError as exc:
                raise XServerDnsError(f"XServer API ConnectError: {exc}") from exc
            await asyncio.sleep(self.REQUEST_SPACING_SECONDS)

            if response.status_code == 429 and attempt == 0:
                retry_after = _retry_after(response)
                if retry_after is not None and retry_after <= self.MAX_RETRY_AFTER_SECONDS:
                    logger.warning("XServer API 429: %s秒待って再試行します", retry_after)
                    await asyncio.sleep(retry_after)
                    continue
            if response.status_code != 200:
                raise _error_from_response(response)
            try:
                return response.json()
            except ValueError as exc:
                raise XServerDnsError(f"XServer API のレスポンスがJSONではありません: {response.text[:200]}") from exc
        raise XServerDnsError("XServer API HTTP 429: レート制限", status_code=429)

    async def list_records(self, client: httpx.AsyncClient, domain: str) -> list[dict[str, Any]]:
        payload = await self._request(client, "GET", f"{self.BASE_URL}/{_api_domain(domain)}/dns")
        return payload.get("records") or []

    async def update_record(self, client: httpx.AsyncClient, domain: str, dns_id: int, content: str) -> dict[str, Any]:
        return await self._request(
            client, "PUT", f"{self.BASE_URL}/{_api_domain(domain)}/dns/{dns_id}", json={"content": content}
        )

    async def sync_targets(self, targets: list[DnsTarget], new_ip: str) -> dict[str, Any]:
        try:
            ipaddress.IPv4Address(new_ip)
        except ValueError as exc:
            raise XServerDnsError(f"IPv4アドレスとして不正な値です: {new_ip}") from exc

        # ドメインごとにまとめる (重複ホストも除去)
        grouped: dict[str, list[str]] = {}
        for t in targets:
            if t.record_type.upper() != "A":
                continue
            domain = normalize_domain(t.domain)
            host = normalize_host(t.host, domain)
            hosts = grouped.setdefault(domain, [])
            if host not in hosts:
                hosts.append(host)

        updated: list[dict[str, Any]] = []
        unchanged: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        domain_results: list[dict[str, Any]] = []
        rate_limited = False

        async with httpx.AsyncClient(timeout=self.timeout, limits=httpx.Limits(max_connections=2)) as client:
            for domain, hosts in grouped.items():
                summary = {"domain": domain, "ok": True, "updated": 0, "unchanged": 0, "errors": 0, "message": ""}
                domain_results.append(summary)

                if rate_limited:
                    summary.update(ok=False, errors=len(hosts), message="レート制限のため今回はスキップ")
                    for host in hosts:
                        errors.append(_err_item(domain, host, "レート制限のため今回はスキップしました (次回チェックで再試行)", 429))
                    continue

                try:
                    records = await self.list_records(client, domain)
                except XServerDnsError as exc:
                    if exc.status_code == 429:
                        rate_limited = True
                    summary.update(ok=False, errors=len(hosts), message=str(exc))
                    for host in hosts:
                        errors.append(_err_item(domain, host, str(exc), exc.status_code, exc.code))
                    logger.error("XServer %s のレコード取得に失敗: %s", domain, exc)
                    continue

                for host in hosts:
                    candidates = [
                        r for r in records
                        if str(r.get("type", "")).upper() == "A" and normalize_host(r.get("host", ""), domain) == host
                    ]
                    if not candidates:
                        summary["errors"] += 1
                        summary["ok"] = False
                        errors.append(_err_item(
                            domain, host,
                            f"対象Aレコードが見つかりません: {fqdn(host, domain)}", 404, "RECORD_NOT_IN_LIST",
                        ))
                        continue

                    for record in candidates:
                        item = {
                            "id": record.get("id"),
                            "domain": domain,
                            "host": host,
                            "fqdn": fqdn(host, domain),
                            "old_ip": record.get("content"),
                            "new_ip": new_ip,
                        }
                        if str(record.get("content", "")).strip() == new_ip:
                            unchanged.append({**item, "status": "unchanged"})
                            summary["unchanged"] += 1
                            continue
                        if rate_limited:
                            summary["errors"] += 1
                            summary["ok"] = False
                            errors.append({**item, "status": "error", "error": "レート制限のためスキップ", "status_code": 429})
                            continue
                        try:
                            result = await self.update_record(client, domain, int(record["id"]), new_ip)
                            updated.append({**item, "status": "updated", "message": result.get("message")})
                            summary["updated"] += 1
                            logger.info("XServer更新: %s %s -> %s", item["fqdn"], item["old_ip"], new_ip)
                        except XServerDnsError as exc:
                            if exc.status_code == 429:
                                rate_limited = True
                            summary["errors"] += 1
                            summary["ok"] = False
                            errors.append({**item, "status": "error", "error": str(exc),
                                           "status_code": exc.status_code, "code": exc.code})
                            logger.error("XServer更新失敗: %s: %s", item["fqdn"], exc)

                summary["message"] = (
                    f"更新 {summary['updated']} / 変更なし {summary['unchanged']} / エラー {summary['errors']}"
                )

        return {
            "updated": updated,
            "unchanged": unchanged,
            "errors": errors,
            "domains": domain_results,
            "rate_limited": rate_limited,
        }


def _api_domain(domain: str) -> str:
    domain = normalize_domain(domain)
    try:
        return domain.encode("idna").decode("ascii")  # 日本語ドメイン → Punycode
    except UnicodeError:
        return domain


def _err_item(domain: str, host: str, message: str, status_code: int | None = None, code: str | None = None) -> dict:
    return {
        "domain": domain,
        "host": host,
        "fqdn": fqdn(host, domain),
        "status": "error",
        "error": message,
        "status_code": status_code,
        "code": code,
    }


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _error_from_response(response: httpx.Response) -> XServerDnsError:
    code = None
    message = None
    try:
        payload = response.json()
        err = payload.get("error")
        if isinstance(err, dict):  # 公式形式: {"error": {"code", "message", "errors": [...]}}
            code = err.get("code")
            message = err.get("message")
            details = err.get("errors")
            if details:
                message = f"{message} ({'; '.join(map(str, details))})"
        else:
            message = payload.get("message") or err
    except ValueError:
        pass
    if not message:
        message = response.text.strip()[:500]
    label = f"{code}: " if code else ""
    return XServerDnsError(
        f"XServer API HTTP {response.status_code}: {label}{message}",
        status_code=response.status_code,
        code=code,
        retry_after=_retry_after(response),
    )
