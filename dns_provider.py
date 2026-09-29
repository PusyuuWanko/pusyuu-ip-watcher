from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
import os
import httpx

from dns_config import is_auto_update_a, normalize_domain, normalize_host
from xserver_dns import DnsTarget, XServerDnsClient, XServerDnsError

@dataclass
class ProviderSyncResult:
    provider: str
    ok: bool
    message: str
    records: list[dict] | None = None
    reason: str | None = None
    action: str | None = None

class DnsProvider(ABC):
    name: str

    @abstractmethod
    async def sync(self, ip: str) -> ProviderSyncResult: ...

class XServerProvider(DnsProvider):
    name = "XServer"

    def __init__(self, api_key: str, domains: list[dict]):
        self.client = XServerDnsClient(api_key)
        self.domains = domains

    def _targets(self) -> list[DnsTarget]:
        targets: list[DnsTarget] = []
        for domain in self.domains:
            name = normalize_domain(domain.get("domain", ""))
            for record in domain.get("records") or []:
                if is_auto_update_a(record) and name:
                    targets.append(DnsTarget(name, normalize_host(record.get("host", "@"), name), "A"))
        return targets

    async def sync(self, ip: str) -> ProviderSyncResult:
        targets = self._targets()
        if not targets:
            return ProviderSyncResult(self.name, True, "XServerの自動更新対象Aレコードはありません。")
        try:
            result = await self.client.sync_targets(targets, ip)
        except Exception as exc:  # IP不正など、全体が実行できなかった場合
            reason, action = _classify_xserver_error(str(exc), getattr(exc, "status_code", None), getattr(exc, "code", None))
            return ProviderSyncResult(self.name, False, str(exc), reason=reason, action=action)

        records = result["updated"] + result["unchanged"] + result["errors"]
        per_domain = " / ".join(
            f"{d['domain']}: {'OK' if d['ok'] else 'NG'}({d['message']})" for d in result["domains"]
        )
        head = (
            f"更新 {len(result['updated'])}件 / 変更なし {len(result['unchanged'])}件 / "
            f"エラー {len(result['errors'])}件"
        )
        message = f"{head} ［{per_domain}］"

        if not result["errors"]:
            return ProviderSyncResult(
                self.name, True, message, records,
                "登録された全ドメインの auto_update=true のAレコードを確認しました。",
                "通常は対応不要です。",
            )

        first = result["errors"][0]
        reason, action = _classify_xserver_error(first.get("error", ""), first.get("status_code"), first.get("code"))
        failed_names = ", ".join(sorted({e["fqdn"] for e in result["errors"]}))
        reason = f"{reason}（失敗: {failed_names}）"
        if result["updated"] or result["unchanged"]:
            action += " 他のドメイン・レコードは処理済みです。"
        return ProviderSyncResult(self.name, False, message, records, reason, action)


def _classify_xserver_error(msg: str, status: int | None, code: str | None) -> tuple[str, str]:
    if status == 401 or "HTTP 401" in msg:
        return ("APIキーの認証に失敗しました。",
                ".env の XSERVER_API_KEY とAPIキーの有効期限を確認してください。")
    if status == 403 or "HTTP 403" in msg:
        return ("APIキーの操作範囲外のドメイン、またはDNS変更権限がありません。",
                "XServerのAPIキー設定で、操作対象に全ドメイン（pusyuu.com / isami.moe 等）が含まれ、"
                "DNSが「読み取り・変更」になっているか確認してください。")
    if code == "RECORD_NOT_IN_LIST" or "対象Aレコードが見つかりません" in msg:
        return ("指定されたホストのAレコードがXServer側に存在しません。",
                "DNS管理画面で既存Aレコードを確認するか、dns_targets.json から外してください。このアプリは自動作成しません。")
    if status == 404 or "HTTP 404" in msg:
        return ("対象ドメインがXServer APIから見つかりません。",
                "dns_targets.json のドメイン名と、そのドメインがXServerドメインで管理されているか確認してください。")
    if status == 409 or "HTTP 409" in msg:
        return ("ドメイン側の制約（無効状態・移行中の編集制限など）で操作できませんでした。",
                "XServerの管理画面でドメインの状態を確認してください。")
    if status == 422 or "HTTP 422" in msg:
        return ("XServer APIが入力値を受け付けませんでした。", "メッセージの詳細を確認してください。")
    if status == 429 or "HTTP 429" in msg:
        return ("XServer APIのリクエスト制限（60回/分）に達しました。",
                "次回チェックで自動的に再試行します。")
    if "ConnectError" in msg or "Timeout" in msg or "timed out" in msg.lower():
        return ("XServer APIへ通信できませんでした。",
                "インターネット接続やファイアウォールを確認し、次回チェックを待ってください。")
    return ("XServer DNS更新中に予期しないエラーが発生しました。",
            "画面のメッセージと起動ターミナルのログを確認してください。")

class MyDNSProvider(DnsProvider):
    name = "MyDNS"

    def __init__(self):
        self.username = os.getenv("MYDNS_USERNAME", "").strip()
        self.password = os.getenv("MYDNS_PASSWORD", "").strip()
        self.ipv4_url = os.getenv("MYDNS_IPV4_URL", "https://ipv4.mydns.jp/login.html").strip()

    async def sync(self, ip: str) -> ProviderSyncResult:
        if not self.username or not self.password:
            return ProviderSyncResult(
                self.name, False, "MyDNSの認証情報が未設定です。",
                reason="MyDNSへIP通知するためのID/パスワードがありません。",
                action=".env の MYDNS_USERNAME / MYDNS_PASSWORD を設定してください。",
            )
        # MyDNS's official mechanism is an HTTP-BASIC login notification.
        # The account's registered domains are handled by MyDNS; this adapter
        # therefore does not invent per-record DNS mutations.
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                response = await client.get(self.ipv4_url, auth=(self.username, self.password))
            if response.status_code != 200:
                return ProviderSyncResult(
                    self.name, False, f"MyDNS HTTP {response.status_code}",
                    reason="MyDNSのIP通知に失敗しました。",
                    action="MyDNSのID/パスワードと通知先URLを確認してください。",
                )
            return ProviderSyncResult(
                self.name, True, "MyDNSへIPv4通知を送信しました。",
                reason="MyDNSはHTTP-BASICによるIPv4通知方式を使用しています。",
                action="通常は対応不要です。",
            )
        except Exception as exc:
            return ProviderSyncResult(
                self.name, False, f"MyDNSへの通信に失敗しました: {exc}",
                reason="MyDNSの通知先へ接続できませんでした。",
                action="ネットワーク接続を確認し、次回チェックを待ってください。",
            )
