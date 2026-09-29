# Pusyuu IP Watcher

ipアドレスやサーバー状態を常時確認し、変更があればプロバイダに変更を申し出するアプリケーションです
これによりサーバー端末上でなくともその辺にあるWinやLinuxなどマルチプラットフォーム上で、サーバー状態の確認などが行えます

## 1. 初回セットアップ

### Windows

`start_windows.cmd` を実行してください。

1. `py` または `python` を探す
2. 見つからなければPythonをインストールするか確認
3. `Y`なら `winget` が利用できる場合にPythonをインストール
4. `.venv` を作成
5. `requirements.txt` をインストール
6. `.env` がなければ `.env.example` から作成
7. アプリを起動

Pythonを手動インストールする場合:

https://www.python.org/downloads/windows/

インストール時は **Add python.exe to PATH** を有効にしてください。

### Linux / macOS

```bash
./start_linux_mac.sh
```

Pythonが無ければ、インストールするか確認します。

- Debian / Ubuntu: `apt-get`
- macOS: Homebrew

---

## 2. XServer APIキーを作る

XServerのDomain APIでAPIキーを発行します。

今回の用途では、**ドメインの取得・移管・契約更新などの権限は不要**です。

推奨する考え方は:

| 項目 | 設定 |
|---|---|
| XServerドメイン | 有効 |
| 操作対象 | 指定ドメインのみ |
| 対象 | `pusyuuwanko.com` / `pusyuu.com` / `isami.moe` など実際に必要なものだけ |
| ドメイン情報 | 必要最小限の読み取り |
| ネームサーバー | 不要 |
| Whois | 不要 |
| レジストラロック | 不要 |
| DNSレコード設定 | 読み取り・変更 |
| ドメイン取得・移管・更新 | 不要 |

XServerのAPIキーで「指定ドメインのみ」を選んだ場合、許可外ドメインへの操作はAPI側で拒否されます。citeturn0search0

### IP制限について

自宅回線のグローバルIPが変わる環境で、その現在IPだけをAPIキーの接続元制限にすると、IP変更後に自分自身がAPIを呼べなくなる可能性があります。

そのため、動的IP環境ではAPIキーのIP制限を慎重に設定してください。

---

## 3. `.env` を設定

`.env.example` から `.env` が自動生成されます。

最低限:

```dotenv
XSERVER_API_KEY=ここにAPIキー
```

を設定します。

`XSERVER_MIN_SYNC_INTERVAL_SECONDS` はDNSキャッシュによる連続同期を避けるための間隔で、初期値は300秒です。

APIキー本体は `dns_targets.json` に保存しません。

---

## 4. `dns_targets.json` を設定

ここがの中心です。

### サーバー状態APIのURLをJSONで指定

サーバーに設置するAPIのJSON形式は以下の様な形式にしてください`※このサーバー状態APIそのものはこのプロジェクトにはありません、ご自身のサーバーにこの形式で設置してください`。

```json
{
  "power": "AC",
  "battery": null,
  "status": null,
  "remaining_hours": null,
  "cpu_percent": 5,
  "memory_percent": 78.38,
  "memory_used_gb": 2.24,
  "memory_total_gb": 2.86,
  "disk_percent": 18.93,
  "disk_used_gb": 86.47,
  "disk_total_gb": 456.88
}
```

APIを設置済みのサーバーでは、例えばこのようにURLの部分はあなたの物を指定してください。
```json
"server_status": {
  "enabled": true,
  "url": "https://pusyuuwanko.com/server_status",
  "timeout_seconds": 10
}
```

優先順位は `dns_targets.json` の `server_status.url` → `.env` の `SERVER_STATUS_URL` → 既定値 です。`enabled: false` でサーバー状態チェックを止められます。

初期状態は安全のためXServerが無効です。

```json
{
  "xserver": {
    "enabled": false,
    "domains": [
      {
        "domain": "pusyuuwanko.com",
        "records": [
          {"host": "@", "type": "A", "auto_update": true},
          {"host": "yesReIP", "type": "A", "auto_update": true},
          {"host": "noenReIP", "type": "A", "auto_update": false}
        ]
      },
      {
        "domain": "pusyuu.com",
        "records": [
          {"host": "@", "type": "A", "auto_update": true}
        ]
      },
      {
        "domain": "isami.moe",
        "records": [
          {"host": "@", "type": "A", "auto_update": true}
        ]
      }
    ]
  },
  "mydns": {
    "enabled": false
  }
}
```

設定したら:

```json
"enabled": true
```

に変更します。

### レコードの意味

| 設定 | 意味 |
|---|---|
| `host: "@"` | `pusyuuwanko.com` 本体 |
| `host: "yesReIP"` | `yesReIP.pusyuuwanko.com` |
| `host: "noenReIP"` | `noenReIP.pusyuuwanko.com` |
| `type: "A"` | IPv4アドレス |
| `auto_update: true` | IP Watcherが変更してよい |
| `auto_update: false` | IP Watcherは変更しない |

**重要:** `auto_update=false` のレコードは、同じドメインに存在していても触りません。

また、このアプリは自動でAレコードを新規作成しません。対象レコードはあらかじめXServer側に存在している必要があります。

---

## 5. 3ドメインを同じIPにする

例えば次のように設定します。

```text
pusyuuwanko.com        → 自宅グローバルIP
pusyuu.com             → 自宅グローバルIP
isami.moe              → 自宅グローバルIP
```

さらに:

```text
yesReIP.pusyuuwanko.com  → 自宅グローバルIP
noenReIP.pusyuuwanko.com → 変更しない
```

という組み合わせも可能です。

アプリがIPを変更すると、設定された各Providerに**同じ取得済みIPv4アドレス**を渡します。

---

## 6. XServer DNSの前提

XServer公式仕様では、Domain APIのDNSレコードAPIはXServerのネームサーバーを利用している契約ドメイン、または外部ドメイン連携でDNS編集可能な状態が対象です。

他社のネームサーバーを使っている場合、XServer APIでレコードを変更しても名前解決には反映されません。citeturn0search0

そのため、3ドメインそれぞれについて「実際にDNSを回答しているのはどこか」を確認してください。

---

## 7. MyDNS

MyDNS.JPはXServerとは別Providerです。

`.env`:

```dotenv
MYDNS_USERNAME=MyDNSのID
MYDNS_PASSWORD=MyDNSのパスワード
MYDNS_IPV4_URL=https://ipv4.mydns.jp/login.html
MYDNS_NOTIFY_INTERVAL_SECONDS=86400
```

`dns_targets.json`:

```json
{
  "mydns": {
    "enabled": true
  }
}
```

とします。

MyDNSはXServerのような「このレコードIDをPUTする」というモデルではなく、**接続元のIPv4アドレスをMyDNSへ通知するDDNS方式**です。HTTP-BASICでは `ipv4.mydns.jp` へ認証付きでアクセスし、そのアクセス自体がIP通知になります。

ここがXServerとの重要な違いです。

- **XServer**: IP変更やDNS不一致を検知したときに、既存Aレコードを書き換える
- **MyDNS**: IPが変わらなくても、定期的にIP通知を続ける必要がある
- 初回起動時はMyDNSへ通知
- グローバルIPが変わった場合は直ちに通知
- その後は既定で24時間ごとに通知
- 通知に失敗した場合は、成功するまで次回チェックで再試行

MyDNSのDNS情報は、最新のIPアドレス通知から1週間が生成期間です。そのため、IPが変化したときだけ通知する設計では定期更新条件を満たせず、定期通知が必要です。

なお、HTTP-BASIC通知では `ip` の値をURLパラメータとして送るのではありません。MyDNS側が通知元の接続元IPを取得します。そのため、IP Watcher自身が実際の自宅回線からMyDNSへ接続する必要があります。VPNやHTTPプロキシ等を経由すると、ipifyで取得したIPとMyDNSが認識するIPが異なる可能性があります。

MyDNS ProviderではDNSレコードを作成・削除・個別選択することはせず、MyDNS側で登録・設定されたドメインへのIPv4通知だけを行います。
---

## 8. 起動後の画面

```text
http://127.0.0.1:8000/
```

設定ガイド:

```text
http://127.0.0.1:8000/guide
```

画面には、例えば次のように更新対象が表示されます。

```text
DNS Provider / 自動更新対象

XServer: 有効 / APIキー 設定済み

pusyuuwanko.com
  @ / A — 自動更新
  yesReIP / A — 自動更新
  noenReIP / A — 更新しない

pusyuu.com
  @ / A — 自動更新

isami.moe
  @ / A — 自動更新

MyDNS: 無効
```

これにより「APIキーは使えるが、このレコードは変更対象ではない」という状態も確認できます。

---

## 9. DNS更新の動作

```text
グローバルIP取得
      ↓
監視対象ドメインを確認
      ↓
IP変更 / DNS不一致を検知
      ↓
DNS Providerを順番に処理
      ├─ XServer
      │    ↓
      │  指定ドメインのDNS一覧を取得
      │    ↓
      │  auto_update=true のAレコードだけ探す
      │    ↓
      │  現在IPと違えばPUT
      │
      └─ MyDNS
           ↓
         IPv4通知
```

XServerのDNS APIはレコード一覧から `id`, `type`, `host`, `content`, `ttl` などを取得し、既存レコードを `PUT /v1/domain/{domain_name}/dns/{dns_id}` で変更できます。v4はこの仕組みに合わせ、毎回現在のDNS一覧からレコードIDを探します。citeturn0search0

---

## 10. Providerごとの更新条件

v4.1ではXServerとMyDNSを同じ「IP変更時のDNS更新」として扱っていましたが、これはMyDNSの仕様とは一致しません。

現在はProviderごとに実行条件を分離しています。

```text
グローバルIP取得
      │
      ├─ IP変更 / 監視ドメイン不一致
      │      └─ XServer: Aレコードを更新
      │
      └─ MyDNS
             ├─ 初回通知
             ├─ IP変更時に通知
             └─ 既定24時間ごとに通知
```

`MYDNS_NOTIFY_INTERVAL_SECONDS` で定期通知間隔を変更できます。MyDNSのDNS情報は最新通知から1週間が生成期間なので、1週間ぎりぎりではなく、余裕を持った短い間隔にしています。

## 11. エラー表示

エラーはできるだけ「状態 → なぜ → 対処」の順に表示します。

例:

### APIキー未設定

**状態:** XServer APIキー未設定

**なぜ:** XServer APIを認証できません。

**対処:** `.env` の `XSERVER_API_KEY` を設定してください。

### HTTP 403

**状態:** XServer API操作拒否

**なぜ:** APIキーの対象ドメインまたはDNS権限が不足している可能性があります。

**対処:** XServerのAPIキー設定を確認してください。

### 対象Aレコードなし

**状態:** `yesReIP` のAレコードが見つからない

**なぜ:** `dns_targets.json` に書いたホストがXServer側に存在しません。

**対処:** 既存レコードを確認してください。このアプリは勝手に作成しません。

---

## 12. API

| Method | Path | 内容 |
|---|---|---|
| GET | `/` | ダッシュボード |
| GET | `/guide` | READMEをHTML化したガイド |
| GET | `/api/status` | IP/DNS/サーバー状態 |
| GET | `/api/config` | APIキー本体を含まないDNS設定情報 |
| POST | `/api/check` | 即時チェック |

---

## 13. 状態ファイル

- `domain_state.json`: IP/DNS/Provider更新履歴
- `server_state.json`: 外部サーバー状態履歴

既存のv3状態ファイルはv4でも読み込みます。

---

## 14. セキュリティ

- APIキーは `.env` に保存
- APIキーをWeb UIへ表示しない
- `dns_targets.json` は「どのレコードを更新してよいか」だけを持つ
- XServerのAレコード新規作成はしない
- XServerのDNSレコード削除はしない
- XServerのネームサーバー変更はしない
- `auto_update=true` のAレコード以外は変更しない

---

## 15. v3の設定を使い続けたい場合

v4はv3の以下の設定を一度だけ移行します。

```dotenv
XSERVER_ENABLED=true
XSERVER_DOMAIN=pusyuuwanko.com
XSERVER_DNS_HOSTS=@,yesReIP
```

初回起動時に `dns_targets.json` が生成され、次回からはこちらが優先されます。

v3の `.env` を残しておいても構いませんが、v4ではDNS対象の変更は `dns_targets.json` で行うことを推奨します。

---

## 16. 停止

起動したターミナルで `Ctrl+C` を押してください。

---

## 参考資料

- XServer Domain API: https://developer.xserver.ne.jp/api/domain/
- MyDNS.JP: https://www.mydns.jp/
