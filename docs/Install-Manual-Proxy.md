# GitHub Copilot CLI 閉域導入マニュアル — Entra ID Bearer 注入プロキシ編

> Azure AI Foundry リソースで **API キー認証が無効化されている**（`disableLocalAuth=true` / Entra ID 専用）環境で、GitHub Copilot CLI を BYOK で動作させるための補足マニュアル。
>
> 通常の API キー方式は [Install-Manual.md](./Install-Manual.md) を参照すること。本書は **その差分のみ**を扱う。
>
> 対象読者: [Install-Manual.md](./Install-Manual.md) のセクション 1〜6（前提・ネットワーク・前提ソフト・CLI 導入）を完了済みの検証エンジニア

---

## 目次

1. [なぜプロキシが必要か](#1-なぜプロキシが必要か)
2. [構成図](#2-構成図)
3. [前提条件（差分）](#3-前提条件差分)
4. [プロキシのセットアップ](#4-プロキシのセットアップ)
5. [Managed Identity への RBAC 付与](#5-managed-identity-への-rbac-付与)
6. [Copilot CLI 側の環境変数](#6-copilot-cli-側の環境変数)
7. [動作確認](#7-動作確認)
8. [トラブルシューティング](#8-トラブルシューティング)
9. [運用上の注意](#9-運用上の注意)

---

## 1. なぜプロキシが必要か

| 項目 | 通常ルート（[Install-Manual.md](./Install-Manual.md)） | 本書（プロキシ経由） |
| --- | --- | --- |
| Foundry 側の認証 | API キー（`api-key` ヘッダ） | Microsoft Entra ID（`Authorization: Bearer <token>`） |
| Copilot CLI BYOK のサポート | ◎ ネイティブ対応 | ✕ 未対応（CLI は Bearer を直接付けられない） |
| 解決策 | そのまま使う | **ローカル HTTP リバースプロキシ**を `127.0.0.1` に立て、CLI からの `api-key` を捨てて Entra Bearer を注入し Foundry へ転送 |

本構成では Foundry リソース `<your-foundry>` のように API キー認証が無効化されているため、`tools/copilot-cli-proxy/proxy.py` を中継させる。

---

## 2. 構成図

```
┌──────────────┐  api-key: dummy-not-used    ┌─────────────────────┐  Authorization: Bearer <entra-token>   ┌─────────────────────────────┐
│ Copilot CLI  │ ───────────────────────────▶ │ proxy.py (127.0.0.1) │ ─────────────────────────────────────▶ │ Foundry (Private Endpoint)  │
│  (BYOK)      │                              │  - api-key を削除      │                                          │  <your-foundry>             │
│              │ ◀───────────────────────────  │  - Bearer を注入       │ ◀─────────────────────────────────────  │  /openai/v1/...             │
└──────────────┘  SSE / JSON                  │  - MI で token 取得    │                                          └─────────────────────────────┘
                                              └─────────────────────┘
                                                    ▲
                                                    │ DefaultAzureCredential
                                                    │ (Managed Identity)
                                              ┌─────┴───────┐
                                              │ VM IMDS     │
                                              └─────────────┘
```

すべて VNet 内で完結し、インターネット経由通信は発生しない。

---

## 3. 前提条件（差分）

[Install-Manual.md セクション 2](./Install-Manual.md#2-前提条件) に加え、以下を満たすこと。

| 項目 | 値 / 内容 |
| --- | --- |
| Foundry リソース | `<your-foundry>`（Cognitive Services / Azure OpenAI 互換） |
| Foundry の認証モード | API キー無効（`disableLocalAuth=true` / Entra 専用） |
| VM の Managed Identity | システム割り当て MI を有効化済み |
| MI のロール（Foundry スコープ） | `Cognitive Services OpenAI User`（または `Cognitive Services User`） |
| VM に Python 3.10+ | `python --version` で確認 |
| Python 依存パッケージ | `aiohttp`, `azure-identity`（後述） |

---

## 4. プロキシのセットアップ

### 4.1 ソース取得

リポジトリ `tools/copilot-cli-proxy/` を VM に配置（git clone もしくは zip 持ち込み）。構成:

```
tools/copilot-cli-proxy/
├─ proxy.py
└─ requirements.txt
```

### 4.2 仮想環境の作成と依存インストール

```powershell
cd C:\GitHub\GHCP_INTERNAL\tools\copilot-cli-proxy   # 実パスに合わせて
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# venv 直下に pip が無い場合
python -m ensurepip --upgrade
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 4.3 環境変数（プロキシ側で重要）

| 変数 | 既定値 | 必須の上書き | 用途 |
| --- | --- | --- | --- |
| `PROXY_HOST` | `127.0.0.1` | – | リッスンアドレス |
| `PROXY_PORT` | `8787` | – | リッスンポート |
| `FOUNDRY_BASE_URL` | `https://<your-foundry>.cognitiveservices.azure.com` | リソース名が違うなら上書き | 転送先 Foundry |
| `ENTRA_SCOPE` | `https://ai.azure.com/.default` | **必ず上書き** | Foundry OpenAI データプレーンに必要な audience |

> **重要**: `ENTRA_SCOPE` の既定値 `https://ai.azure.com/.default` は Foundry エージェント API 用。**OpenAI モデルのデータプレーン (`/openai/v1/chat/completions` 等) では 401** になる。必ず `https://cognitiveservices.azure.com/.default` に上書きすること。

### 4.4 起動

```powershell
$env:ENTRA_SCOPE = "https://cognitiveservices.azure.com/.default"
python .\proxy.py
```

期待ログ:

```
INFO copilot-proxy Listening on http://127.0.0.1:8787 -> https://<your-foundry>.cognitiveservices.azure.com
INFO copilot-proxy Entra scope: https://cognitiveservices.azure.com/.default
INFO azure.identity._credentials.chained DefaultAzureCredential acquired a token from ManagedIdentityCredential
INFO copilot-proxy Token acquired, valid for 86xxx seconds
```

`Entra scope:` の行が `cognitiveservices.azure.com/.default` であることを起動毎に確認する。

---

## 5. Managed Identity への RBAC 付与

VM の MI（システム割り当て）に Foundry の OpenAI データプレーン権限を付与する。

```powershell
# VM の MI principalId を取得
$miPid = az vm identity show `
  -g <your-rg> `
  -n <your-vm> `
  --query principalId -o tsv

# MI が未設定なら割り当て
if (-not $miPid) {
  az vm identity assign -g <your-rg> -n <your-vm>
  $miPid = az vm identity show -g <your-rg> -n <your-vm> --query principalId -o tsv
}

$scope = "/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/<your-rg>/providers/Microsoft.CognitiveServices/accounts/<your-foundry>"

az role assignment create `
  --assignee-object-id $miPid `
  --assignee-principal-type ServicePrincipal `
  --role "Cognitive Services OpenAI User" `
  --scope $scope
```

ロール反映には最大 5 分。反映後はプロキシを **Ctrl+C → 再起動**（トークンキャッシュ破棄）。

---

## 6. Copilot CLI 側の環境変数

[Install-Manual.md 7.4](./Install-Manual.md#74-環境変数の永続設定powershell) の代わりに以下を設定。**`BASE_URL` はプロキシのループバック**、**`API_KEY` はダミー値**（CLI を黙らせるためだけのもので、プロキシが破棄する）。

```powershell
[Environment]::SetEnvironmentVariable("COPILOT_OFFLINE","true","User")
[Environment]::SetEnvironmentVariable("COPILOT_PROVIDER_TYPE","azure","User")
[Environment]::SetEnvironmentVariable("COPILOT_PROVIDER_BASE_URL","http://127.0.0.1:8787/openai/v1","User")
[Environment]::SetEnvironmentVariable("COPILOT_PROVIDER_API_KEY","dummy-not-used","User")
[Environment]::SetEnvironmentVariable("COPILOT_MODEL","gpt-5.4-mini","User")

# gpt-5 系（gpt-5.4 / gpt-5.4-mini など）は Responses API 推奨
[Environment]::SetEnvironmentVariable("COPILOT_PROVIDER_WIRE_API","responses","User")
```

> **`COPILOT_PROVIDER_API_KEY` の意味**: 通常ルートでは Foundry の本物の API キーを入れるが、本構成では「CLI が必須としてチェックするので何か値を入れる必要がある」だけのダミー。実際の認証はプロキシが Entra ID で行う。

---

## 7. 動作確認

### 7.1 経路確認

```powershell
# VM 上で実行
Resolve-DnsName "<your-foundry>.cognitiveservices.azure.com"
# → CNAME 経由で privatelink ゾーン、A レコードが VNet 内プライベート IP

Test-NetConnection -ComputerName "<your-foundry>.cognitiveservices.azure.com" -Port 443
# → TcpTestSucceeded : True

Test-NetConnection -ComputerName api.githubcopilot.com -Port 443
# → TcpTestSucceeded : False
```

### 7.2 CLI 起動

新しい PowerShell 窓で（プロキシは別窓で起動済みの状態）:

```powershell
copilot
```

CLI 内で挨拶やプロンプトを試す。プロキシ側ログに次が出ていれば成功:

```
INFO copilot-proxy POST /openai/v1/chat/completions -> https://<your-foundry>.cognitiveservices.azure.com/openai/v1/chat/completions
INFO aiohttp.access ... "POST /openai/v1/chat/completions HTTP/1.1" 200 ...
```

### 7.3 複数モデルの切替

同じ Foundry に複数デプロイ（例: `gpt-5.4` と `gpt-5.4-mini`）がある場合、プロキシは素通しのため URL 変更不要:

```powershell
copilot --model gpt-5.4
copilot --model gpt-5.4-mini
# CLI 内では /model <deployment-name>
```

`COPILOT_MODEL` 値 = Foundry デプロイ名と完全一致させる（大文字小文字含む）。

---

## 8. トラブルシューティング

### 8.1 401 PermissionDenied がプロキシ側ログに出る

| 切り分け | 対処 |
| --- | --- |
| プロキシ起動ログの `Entra scope:` が `ai.azure.com/.default` のまま | `$env:ENTRA_SCOPE = "https://cognitiveservices.azure.com/.default"` を設定して再起動 |
| ログに `acquired a token from ManagedIdentityCredential` は出ているが 401 | MI に `Cognitive Services OpenAI User` 未付与。[セクション 5](#5-managed-identity-への-rbac-付与) を実施 |
| ロール付与直後に試した | 反映待ち（最大 5 分）。プロキシ再起動でトークンキャッシュも破棄 |
| Foundry 側で Entra 認証も拒否設定 | Foundry リソースのネットワーク／認証設定を確認 |

トークン audience の手動確認:

```powershell
$tok = (az account get-access-token --scope https://cognitiveservices.azure.com/.default --query accessToken -o tsv)
$payload = $tok.Split('.')[1]
$pad = $payload.Length % 4
if ($pad -gt 0) { $payload += '=' * (4 - $pad) }
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($payload.Replace('-','+').Replace('_','/'))) | ConvertFrom-Json | Select aud, appid, oid
```

`aud` が `https://cognitiveservices.azure.com` であること。

### 8.2 `Authentication failed with provider at http://127.0.0.1:8787/openai/v1 (HTTP 401)` が CLI 側に出る

→ プロキシ→Foundry 間が 401。原因は 8.1 と同じ。CLI に渡す `COPILOT_PROVIDER_API_KEY` は無関係（ダミーで OK）。

### 8.3 CLI 起動時に `Model "gpt-5.4-mini" works best with wireApi: "responses"` の警告

`COPILOT_PROVIDER_WIRE_API=responses` を設定。[セクション 6](#6-copilot-cli-側の環境変数) 参照。

### 8.4 プロキシ起動時 `No module named 'aiohttp'` または `No module named pip`

venv が壊れている／グローバル Python を見ている。再作成:

```powershell
deactivate
Remove-Item -Recurse -Force .venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m ensurepip --upgrade
python -m pip install -r requirements.txt
```

### 8.5 `ENTRA_SCOPE = ...` を PowerShell で打ったら構文エラー

それは Python コードの構文。PowerShell では `$env:ENTRA_SCOPE = "..."` を使う。

---

## 9. 運用上の注意

- **プロキシは VM のローカル**で動かす。リモートから 127.0.0.1 にアクセスはできないため、CLI を使う各 VM で `proxy.py` を常駐させる必要がある（Windows サービス化／タスクスケジューラ／pm2 等を検討）。
- **トークンキャッシュ**: プロキシは取得した Entra トークンを有効期限の 5 分前まで保持。ロール変更後は **必ず再起動**して古いトークンを捨てる。
- **ストリーミング (SSE)**: `proxy.py` は `iter_any()` でチャンク逐次転送する実装。Copilot CLI のストリーミング出力はそのまま機能する。
- **複数モデル**: プロキシは Foundry 直下にパススルー転送するので、同 Foundry 内の任意のデプロイを `COPILOT_MODEL` で切替可能（[7.3](#73-複数モデルの切替)）。
- **共有運用への発展**: 複数 VM／複数ユーザーで共有する規模になったら、ローカルプロキシではなく **APIM AI Gateway** で同じ Bearer 注入をやる構成に発展させるのが筋（APIM の MI に同等のロールを付け、`authentication-managed-identity` ポリシーで Bearer を注入）。

---

## 更新履歴

| 日付 | 内容 | 担当 |
| --- | --- | --- |
| 2026-05-17 | 初版作成（[Install-Manual.md](./Install-Manual.md) の付録 E 相当を独立化） | – |
| 2026-05-17 | `ENTRA_SCOPE` の正しい既定（`cognitiveservices.azure.com/.default`）を明記 | – |
| 2026-05-17 | VM の Managed Identity 利用と RBAC 付与手順を反映 | – |
