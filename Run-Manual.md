# GitHub Copilot CLI 閉域実行手順書

> 既存マニュアル（[Install-Manual.md](./Install-Manual.md) / [Install-Manual-Proxy.md](./Install-Manual-Proxy.md) / [Run-Copilot-CLI-Ephemeral.md](./Run-Copilot-CLI-Ephemeral.md)）から **実際に動かすコマンドのみ** を抽出した実行手順書。
>
> 前提: 各マニュアルに従い、VM・Foundry・Private Endpoint・Copilot CLI のインストールが完了済みであること。

---

## 0. 認証方式の選択

| 方式 | 条件 | 該当セクション |
|---|---|---|
| **A. API キー方式** | Foundry が API キー認証有効 (`disableLocalAuth=false`) | [§A](#a-api-キー方式の実行手順) |
| **B. Entra ID プロキシ方式** | Foundry が API キー無効 (`disableLocalAuth=true`) | [§B](#b-entra-id-プロキシ方式の実行手順) |

---

## 共通: VM への接続

Azure Bastion (Developer SKU) 経由で VM に RDP 接続する。

```powershell
# クライアント PC から (Azure CLI 必須)
az network bastion rdp `
  --name "<your-vnet>-bastion" `
  --resource-group "<your-rg>" `
  --target-resource-id (az vm show -g "<your-rg>" -n "<your-vm>" --query id -o tsv)
```

または Azure Portal → 対象 VM → Bastion → RDP で接続。

VPN 経由を使う場合は [Install-Manual.md §4.1](./Install-Manual.md#41-vpn-接続) を参照。

---

## A. API キー方式の実行手順

### A.1 環境変数の確認（VM 上）

新規 PowerShell セッションを開き、永続化済みの設定が反映されていることを確認。

```powershell
$env:COPILOT_OFFLINE
$env:COPILOT_PROVIDER_TYPE
$env:COPILOT_PROVIDER_BASE_URL
$env:COPILOT_MODEL
$env:COPILOT_PROVIDER_API_KEY.Length  # 値は出さず長さのみ
```

未設定なら [Install-Manual.md §7.4](./Install-Manual.md#74-環境変数の永続設定powershell) を実施。

### A.2 ネットワーク疎通確認

```powershell
# Foundry エンドポイント（PE 経由でプライベート IP が返ること）
Resolve-DnsName "<your-foundry>.cognitiveservices.azure.com"
Test-NetConnection -ComputerName "<your-foundry>.cognitiveservices.azure.com" -Port 443
# → TcpTestSucceeded : True

# GitHub への通信が遮断されていること
Test-NetConnection -ComputerName api.githubcopilot.com -Port 443
# → TcpTestSucceeded : False を期待
```

### A.3 Copilot CLI 起動

```powershell
copilot
```

- スプラッシュ表示
- `/login` の促しが出ないこと
- プロンプト入力待ちになること

### A.4 サンプル実行

```powershell
# 対話モード内で
カレントディレクトリのファイル一覧を表示する PowerShell スクリプトを書いてください。

# 非対話モード（ワンショット）
copilot -p "今日の日付を ISO 8601 形式で表示する PowerShell ワンライナーを教えて"
```

### A.5 モデル切替（同 Foundry 内）

```
/model <deployment-name>
```

または起動時に `copilot --model <deployment-name>`。

---

## B. Entra ID プロキシ方式の実行手順

ターミナルを **2 つ** 使用する（プロキシ常駐 + CLI 実行）。

### B.1 セッション B: プロキシ起動

```powershell
cd C:\GitHub\GHCP_INTERNAL\tools\copilot-cli-proxy
.\.venv\Scripts\Activate.ps1

# Entra スコープを必ず上書き（既定値は誤り）
$env:ENTRA_SCOPE = "https://cognitiveservices.azure.com/.default"

python .\proxy.py
```

期待ログ:

```
INFO copilot-proxy Listening on http://127.0.0.1:8787 -> https://<your-foundry>.cognitiveservices.azure.com
INFO copilot-proxy Entra scope: https://cognitiveservices.azure.com/.default
INFO copilot-proxy Token acquired, valid for 86xxx seconds
```

セッション B はこのまま保持。

### B.2 セッション A: Copilot CLI 起動

新しい PowerShell を開く。

#### B.2.1 永続化済み環境変数を使う場合

```powershell
copilot
```

環境変数が未設定なら [Install-Manual-Proxy.md §6](./Install-Manual-Proxy.md#6-copilot-cli-側の環境変数) で永続化するか、§B.2.2 の一時起動を使う。

#### B.2.2 一時セッション起動（推奨・永続化しない）

```powershell
pwsh -NoProfile -File "$env:USERPROFILE\start-copilot-ephemeral.ps1"
```

スクリプト未配置なら [Run-Copilot-CLI-Ephemeral.md §4](./Run-Copilot-CLI-Ephemeral.md#4-起動スクリプト推奨) で作成。

または手動で設定:

```powershell
$env:COPILOT_OFFLINE           = "true"
$env:COPILOT_PROVIDER_TYPE     = "azure"
$env:COPILOT_PROVIDER_BASE_URL = "http://127.0.0.1:8787/openai/v1"
$env:COPILOT_PROVIDER_API_KEY  = "dummy-not-used"
$env:COPILOT_MODEL             = "gpt-5.4-mini"
$env:COPILOT_PROVIDER_WIRE_API = "responses"
copilot
```

### B.3 動作確認

セッション B のプロキシログに次が流れることを確認:

```
INFO copilot-proxy POST /openai/v1/chat/completions -> https://<your-foundry>.cognitiveservices.azure.com/openai/v1/chat/completions
INFO aiohttp.access ... "POST /openai/v1/chat/completions HTTP/1.1" 200 ...
```

セッション A で動作確認:

```powershell
copilot -p "今日の日付を ISO 8601 形式で表示する PowerShell ワンライナーを教えて"
```

### B.4 終了とクリーンアップ

| 対象 | 操作 |
|---|---|
| セッション A（CLI 側） | ウィンドウを閉じる、または `exit` |
| セッション B（プロキシ側） | `Ctrl+C` で停止 |
| Entra トークン | プロキシ終了で破棄 |
| Bastion セッション | RDP ウィンドウを閉じる |

---

## 共通: 閉域動作の最終確認

Azure Portal の以下で **インターネット向け通信が発生していない**ことを確認:

| ログ | 確認内容 |
|---|---|
| NSG フローログ | VM サブネット → `Internet` 宛て送信が Deny されていること |
| Foundry 診断ログ (`RequestResponse`) | PE 経由の推論リクエストが記録されていること |
| Bastion 接続履歴 | 想定ユーザーのみが接続していること |

---

## トラブルシューティング簡易表

| 症状 | 第一候補の対処 | 詳細リンク |
|---|---|---|
| `copilot` コマンド not found | `%APPDATA%\npm` を PATH に追加 | [Install-Manual.md §10.1](./Install-Manual.md#101-copilot-コマンドが見つからない) |
| 起動時にモデルエラー | tool calling / streaming 対応モデルへ差し替え | [Install-Manual.md §10.2](./Install-Manual.md#102-起動時にモデルエラー-model-does-not-support-tool-calling-等) |
| 接続タイムアウト | Private DNS Zone リンク / NSG / PE を確認 | [Install-Manual.md §10.3](./Install-Manual.md#103-接続タイムアウト) |
| 401 / 403 (API キー方式) | キー再取得、または Entra ID 専用なら §B へ切替 | [Install-Manual.md §10.4](./Install-Manual.md#104-401--403-認証エラー) |
| 401 (プロキシ方式) | `ENTRA_SCOPE` の上書き / MI の RBAC 付与確認 | [Install-Manual-Proxy.md §8.1](./Install-Manual-Proxy.md#81-401-permissiondenied-がプロキシ側ログに出る) |
| `Connection refused 127.0.0.1:8787` | セッション B のプロキシが起動しているか確認 | [Run-Copilot-CLI-Ephemeral.md §7.2](./Run-Copilot-CLI-Ephemeral.md#72-connection-refused--econnrefused-1270018787) |

---

## 参考

- [README.md](../README.md) — 検証テーマ・検証結果サマリ
- [Install-Manual.md](./Install-Manual.md) — API キー方式の導入手順（完全版）
- [Install-Manual-Proxy.md](./Install-Manual-Proxy.md) — Entra ID プロキシ方式の導入手順（完全版）
- [Run-Copilot-CLI-Ephemeral.md](./Run-Copilot-CLI-Ephemeral.md) — 一時セッション起動の詳細
