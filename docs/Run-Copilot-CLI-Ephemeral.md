# Copilot CLI 一時セッション起動手順（Entra ID 認証経由）

> 環境変数を**永続化せず**、現在の PowerShell セッション内だけで Copilot CLI を起動するための手順書です。Foundry リソースで API キー認証が無効化されている (`disableLocalAuth=true`) ケースを想定し、[付録 E のローカル Bearer 注入プロキシ](Install-Manual.md#付録-e-entra-id-bearer-注入プロキシapi-キー無効化-foundry-用) 経由で `dummy-not-used` を使う構成です。
>
> 関連: [Install-Manual.md](Install-Manual.md) §7.4（永続化版）

---

## 目次

1. [前提](#1-前提)
2. [構成図](#2-構成図)
3. [起動手順](#3-起動手順)
4. [起動スクリプト（推奨）](#4-起動スクリプト推奨)
5. [動作確認](#5-動作確認)
6. [終了とクリーンアップ](#6-終了とクリーンアップ)
7. [トラブルシューティング](#7-トラブルシューティング)

---

## 1. 前提

- [Install-Manual.md](Install-Manual.md) §5 / §6 に従い、Node.js と `@github/copilot` がインストール済み
- [tools/copilot-cli-proxy/](../tools/copilot-cli-proxy/) のセットアップが完了済み
  - `python -m venv .venv` / `pip install -r requirements.txt` 実施済み
- `az login --tenant yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy` 済み
- 対象 Foundry リソース (`<your-foundry>`) に対し、現在のユーザーに **Cognitive Services OpenAI User** ロールが付与済み
- ユーザー環境変数として `COPILOT_*` が **永続化されていない**こと（残っていると優先されるため）

永続化されていないことの確認:

```powershell
[Environment]::GetEnvironmentVariable("COPILOT_PROVIDER_BASE_URL","User")
[Environment]::GetEnvironmentVariable("COPILOT_PROVIDER_API_KEY","User")
[Environment]::GetEnvironmentVariable("COPILOT_MODEL","User")
# すべて空ならクリーン状態
```

残っている場合の削除（任意）:

```powershell
foreach ($v in "COPILOT_OFFLINE","COPILOT_PROVIDER_TYPE","COPILOT_PROVIDER_BASE_URL","COPILOT_PROVIDER_API_KEY","COPILOT_MODEL") {
    [Environment]::SetEnvironmentVariable($v, $null, "User")
}
```

---

## 2. 構成図

```
   現在の PowerShell セッション (A)               別 PowerShell セッション (B)
   ┌────────────────────────────────┐            ┌─────────────────────────────┐
   │ $env:COPILOT_*  (一時)         │            │ python proxy.py             │
   │   ↓                            │            │ → 127.0.0.1:8787            │
   │ copilot                        │ ─────────► │   Authorization: Bearer ... │
   └────────────────────────────────┘   HTTP     └──────────────┬──────────────┘
                                                                │ HTTPS (PE)
                                                                ▼
                                                <your-foundry>.cognitiveservices.azure.com
```

セッション A の `$env:XXX` 形式は **そのプロセス内のみ**で有効。新しいウィンドウを開くと失われる。

---

## 3. 起動手順

### 3.1 プロキシ起動（セッション B）

```powershell
cd C:\GitHub\GHCP_INTERNAL\tools\copilot-cli-proxy
.\.venv\Scripts\Activate.ps1
python proxy.py
```

起動ログに次が出れば OK:

```
INFO copilot-proxy Listening on http://127.0.0.1:8787 -> https://<your-foundry>.cognitiveservices.azure.com
INFO copilot-proxy Token acquired, valid for NNNN seconds
```

セッション B はこのまま起動したままにする（閉じるとプロキシが止まる）。

### 3.2 Copilot CLI 起動（セッション A）

新しい PowerShell を開き、次を順に実行。

```powershell
# プロセス内のみで有効（永続化しない）
$env:COPILOT_OFFLINE          = "true"
$env:COPILOT_PROVIDER_TYPE    = "azure"
$env:COPILOT_PROVIDER_BASE_URL = "http://127.0.0.1:8787/openai/v1"
$env:COPILOT_PROVIDER_API_KEY = "dummy-not-used"
$env:COPILOT_MODEL            = "gpt-5.4-mini"

# 反映確認
$env:COPILOT_PROVIDER_BASE_URL
$env:COPILOT_MODEL

# 起動
copilot
```

セッション A を閉じれば環境変数は破棄される。

---

## 4. 起動スクリプト（推奨）

毎回手で入力する代わりに、次のスクリプトを `%USERPROFILE%\start-copilot-ephemeral.ps1` として保存。

```powershell
<#
  .SYNOPSIS
    プロセス内のみで Copilot CLI を起動（Entra ID 経由・永続化なし）
  .NOTES
    別ターミナルで tools/copilot-cli-proxy/proxy.py が起動していること
#>
param(
    [string]$ProxyUrl = "http://127.0.0.1:8787/openai/v1",
    [string]$Model    = "gpt-5.4-mini"
)

# プロキシ生存確認
try {
    $null = Invoke-WebRequest -Uri ($ProxyUrl -replace "/openai/v1$","/") `
        -Method Head -TimeoutSec 3 -ErrorAction Stop
} catch {
    if ($_.Exception.Response.StatusCode.value__ -notin 200,404,405) {
        Write-Error "プロキシに到達できません: $ProxyUrl  (別ターミナルで proxy.py を起動してください)"
        exit 1
    }
}

# プロセス内のみで設定
$env:COPILOT_OFFLINE           = "true"
$env:COPILOT_PROVIDER_TYPE     = "azure"
$env:COPILOT_PROVIDER_BASE_URL = $ProxyUrl
$env:COPILOT_PROVIDER_API_KEY  = "dummy-not-used"
$env:COPILOT_MODEL             = $Model

Write-Host "Copilot CLI (ephemeral / Entra via proxy) starting..." -ForegroundColor Cyan
Write-Host "  BASE_URL : $env:COPILOT_PROVIDER_BASE_URL"
Write-Host "  MODEL    : $env:COPILOT_MODEL"

copilot @args
```

実行:

```powershell
pwsh -NoProfile -File "$env:USERPROFILE\start-copilot-ephemeral.ps1"
```

`copilot` への追加引数も渡せる:

```powershell
pwsh -NoProfile -File "$env:USERPROFILE\start-copilot-ephemeral.ps1" -p "ls の代わりに Get-ChildItem を使う理由"
```

---

## 5. 動作確認

### 5.1 環境変数がプロセス内のみであること

```powershell
# 現在のプロセスでは設定済み
$env:COPILOT_PROVIDER_BASE_URL
# → http://127.0.0.1:8787/openai/v1

# ユーザー環境変数には存在しない
[Environment]::GetEnvironmentVariable("COPILOT_PROVIDER_BASE_URL","User")
# → （空）
```

### 5.2 プロキシ経由で Foundry に到達していること

セッション B のプロキシログに次が流れること:

```
INFO copilot-proxy POST /openai/v1/chat/completions -> https://<your-foundry>.cognitiveservices.azure.com/openai/v1/chat/completions
```

### 5.3 Copilot CLI の動作

```powershell
copilot -p "今日の日付を ISO 8601 形式で表示する PowerShell ワンライナーを教えて"
```

正常応答が返れば成功。

---

## 6. 終了とクリーンアップ

| 対象 | 操作 |
| --- | --- |
| セッション A（CLI 側） | ウィンドウを閉じる、または `exit` |
| セッション B（プロキシ側） | `Ctrl+C` で停止 |
| Entra トークン | プロキシプロセス終了で破棄（永続キャッシュなし） |
| `az` のサインイン状態 | 不要なら `az logout` |

ユーザー環境変数には何も書き込んでいないため、追加のクリーンアップは不要。

---

## 7. トラブルシューティング

### 7.1 `copilot` 起動直後に 401 / 403

- プロキシのトークン取得が失敗 → セッション B のログで `Initial token acquisition failed` を確認
- Foundry の RBAC 未設定 → `<your-foundry>` リソースに **Cognitive Services OpenAI User** ロールを付与
- テナント違い → `az account show --query tenantId` が `yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy` か確認

### 7.2 `Connection refused` / `ECONNREFUSED 127.0.0.1:8787`

- セッション B でプロキシが起動していない、または別ポートで動いている
- ファイアウォール（ローカル）が `127.0.0.1` をブロックしていないか確認

### 7.3 永続値が残っていて意図と違う動作

```powershell
# 現在プロセス値と永続値を並べて確認
"OFFLINE","PROVIDER_TYPE","PROVIDER_BASE_URL","PROVIDER_API_KEY","MODEL" | ForEach-Object {
    [pscustomobject]@{
        Name    = "COPILOT_$_"
        Process = [Environment]::GetEnvironmentVariable("COPILOT_$_","Process")
        User    = [Environment]::GetEnvironmentVariable("COPILOT_$_","User")
    }
} | Format-Table -AutoSize
```

`User` 列に値があれば §1 のクリーンアップを実施。

### 7.4 プロキシのトークン期限切れ警告

通常は有効期限 5 分前に自動再取得される。長時間放置後にエラーになった場合は `az login` をやり直してプロキシを再起動。
