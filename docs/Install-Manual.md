# GitHub Copilot CLI 閉域導入マニュアル

> Azure 閉域 Windows VM 上で、GitHub Copilot CLI を **BYOK + オフラインモード** により、閉域内 Azure AI Foundry Model のみをバックエンドとして動作させるための導入手順書です。
>
> 対象読者: 社内検証エンジニア（Azure / CLI 操作に習熟していることを前提）
> 関連計画書: [Plan.md](../Plan.md)

---

## 目次

1. [概要とアーキテクチャ](#1-概要とアーキテクチャ)
2. [前提条件](#2-前提条件)
3. [ネットワーク設計](#3-ネットワーク設計)
4. [VM への接続](#4-vm-への接続)
5. [前提ソフトのインストール](#5-前提ソフトのインストール)
6. [GitHub Copilot CLI のインストール](#6-github-copilot-cli-のインストール)
7. [認証と BYOK 設定](#7-認証と-byok-設定)
8. [動作確認](#8-動作確認)
9. [制約事項](#9-制約事項)
10. [トラブルシューティング](#10-トラブルシューティング)
- [付録 A: NSG / Azure Firewall ルール例](#付録-a-nsg--azure-firewall-ルール例)
- [付録 B: 環境変数チートシート](#付録-b-環境変数チートシート)
- [付録 C: Key Vault からの API キー取得（推奨）](#付録-c-key-vault-からの-api-キー取得推奨)
- [付録 D: 参照リンク](#付録-d-参照リンク)
- [付録 E: Entra ID Bearer 注入プロキシ（API キー無効化 Foundry 用）](#付録-e-entra-id-bearer-注入プロキシapi-キー無効化-foundry-用)

---

## 1. 概要とアーキテクチャ

### 1.1 目的

GitHub Copilot CLI (`@github/copilot`) を Azure 閉域 VM から **完全閉域**で動作させ、LLM バックエンドとして閉域 Azure AI Foundry Model（Private Endpoint で到達）を利用する。GitHub のクラウドサービス (`*.githubcopilot.com` 等) には一切通信を行わない構成を目指す。

### 1.2 採用方式

GitHub Copilot CLI は次の 2 つの公式機能を組み合わせることで、上記の要件を満たす。

| 機能 | 役割 |
| --- | --- |
| **BYOK (Bring Your Own Key)** | LLM バックエンドを GitHub ホスト型モデル (Claude / GPT-5) ではなく、ユーザー指定の Azure OpenAI / OpenAI 互換エンドポイント（= Foundry Model）へ向ける |
| **オフラインモード** (`COPILOT_OFFLINE=true`) | GitHub サーバーへの通信を抑止。ローカル／オンプレ／閉域内モデルプロバイダーのみと通信する分離環境向け公式モード |

公式ドキュメント: [GitHub Copilot CLI での独自の LLM モデルの使用](https://docs.github.com/ja/copilot/how-tos/copilot-cli/customize-copilot/use-byok-models)

### 1.3 アーキテクチャ

```
   ┌─────────────────────────────────────────────────────────────┐
   │                    オンプレミス / クライアント                  │
   │   ┌────────────┐                                             │
   │   │ PC + VPN   │── P2S / S2S ──┐                             │
   │   └────────────┘                │                             │
   └─────────────────────────────────│─────────────────────────────┘
                                     │
                              ┌──────▼─────────┐
                              │ MyVNetGateway  │  (既存)
                              └──────┬─────────┘
   ┌──────────────────────────────── │ ────────────────────────────┐
   │  Azure VNet (ketana-ext-private-ai RG)                       │
   │                                 │                            │
   │   ┌─────────────────────────────▼──────────────────────────┐ │
   │   │  ketana-ext-vm-private (Windows 10/11)                 │ │
   │   │  ─ Copilot CLI (オフラインモード + BYOK)               │ │
   │   │  ─ 環境変数: COPILOT_OFFLINE / COPILOT_PROVIDER_*      │ │
   │   └──────────────────┬─────────────────────────────────────┘ │
   │                      │ HTTPS (プライベート IP)                 │
   │   ┌──────────────────▼─────────────────────────────────────┐ │
   │   │ Private Endpoint → Azure AI Foundry Model              │ │
   │   │ (privatelink.openai.azure.com)                         │ │
   │   └────────────────────────────────────────────────────────┘ │
   │                                                              │
   │   ┌──────────────────────────────────────────────────────┐   │
   │   │ Azure Firewall / NAT GW  (インストール時のみ FQDN 限定)│  │
   │   │ ※ 運用時はインターネット経路は不要                       │   │
   │   └──────────────────────────────────────────────────────┘   │
   └──────────────────────────────────────────────────────────────┘
```

### 1.4 「完全閉域」の定義（本マニュアル）

| 局面 | インターネット | Foundry (PE) |
| --- | --- | --- |
| **インストール時**（CLI / Node.js 取得） | 一時許可（許可 FQDN のみ）または完全オフライン持ち込み | – |
| **運用時**（Copilot CLI 利用中） | **遮断** | 許可（Private Endpoint のみ） |

---

## 2. 前提条件

### 2.1 既存 Azure リソース

[Plan.md](../Plan.md) に記載のとおり、次のリソースは作成済みであること。

| 項目 | 値 |
| --- | --- |
| サブスクリプション ID | `571e49d7-d4d6-4cb5-884f-2e14bfaa662c` |
| リソースグループ | `ketana-ext-private-ai` |
| 検証用 VM | `ketana-ext-vm-private` (Windows 10 / 11) |
| VPN ゲートウェイ | `MyVNetGateway` |

### 2.2 Azure AI Foundry 側で必要な準備

| 項目 | 内容 |
| --- | --- |
| Foundry リソース | 上記 RG または別 RG に作成済み |
| デプロイ済みモデル | **Tool calling (関数呼び出し)** と **ストリーミング** の両方をサポート。コンテキストウィンドウ 128k トークン以上を推奨（例: `gpt-4o`, `gpt-4.1`, `gpt-5` 等） |
| API キー | デプロイ済みリソースの API キー (Key1 / Key2) |
| Private Endpoint | VM が所属する VNet（またはピアリング先）から到達可能な PE |
| Private DNS Zone | `privatelink.openai.azure.com`（Azure OpenAI の場合）または該当ゾーンが VNet にリンク済み |

### 2.3 VM 側の要件

- Windows 10 / 11
- **PowerShell 6 以上**（Copilot CLI の動作要件）
- 管理者権限（環境変数の永続化／winget 利用時）

### 2.4 クライアント側の準備物

- VPN クライアント（既存 `MyVNetGateway` 用の構成）
- Foundry の以下情報
  - リソース名 (例: `myfoundry`)
  - デプロイ名 (例: `gpt-4o-deploy`)
  - API キー
  - エンドポイント URL
- GitHub Copilot サブスクリプション（後述。実機検証で必要性確認）

### 2.5 ライセンス前提（要検証）

公式ドキュメントには「BYOK 利用時にも Copilot サブスクリプションは必要」かどうかの明示記載がない。本マニュアルでは **「Copilot サブスクリプションが必要」前提**で進める（[8. 動作確認](#8-動作確認)時に必要性を確認）。

---

## 3. ネットワーク設計

### 3.1 通信フロー

| フェーズ | 送信元 | 送信先 | 経路 | 用途 |
| --- | --- | --- | --- | --- |
| インストール時 | VM | `nodejs.org`, `*.npmjs.org`, winget CDN 等 | Azure Firewall / NAT GW → Internet | Node.js / `@github/copilot` パッケージ取得 |
| 運用時 | VM | Foundry エンドポイント (`*.openai.azure.com`) | **Private Endpoint** | LLM 推論リクエスト |
| 運用時 | VM | GitHub (`*.githubcopilot.com` 等) | **発生しない**（オフラインモード） | – |

### 3.2 インストール時に許可が必要な FQDN

オンライン方式でインストールする場合に、**一時的に** Azure Firewall アプリケーションルールで許可する。

| FQDN | 用途 | 必要なケース |
| --- | --- | --- |
| `nodejs.org` | Node.js LTS ダウンロード | npm 方式（または手動 MSI） |
| `*.nodejs.org` | Node.js 配信 | 同上 |
| `registry.npmjs.org` | npm レジストリ | npm 方式 |
| `npmjs.org` | npm 関連 | npm 方式 |
| `cdn.winget.microsoft.com` | winget CDN | winget 方式 |
| `*.delivery.mp.microsoft.com` | winget マニフェスト配信 | winget 方式 |
| `winget.azureedge.net` | winget CDN フォールバック | winget 方式 |
| `pkg-containers.githubusercontent.com` | Copilot CLI バイナリ配信 | winget 方式 |
| `code.visualstudio.com` | VS Code インストーラ配信 | VS Code を導入する場合 |
| `*.vo.msecnd.net` | VS Code CDN | VS Code を導入する場合 |

**運用開始後は上記ルールをすべて削除すること。** [付録 A](#付録-a-nsg--azure-firewall-ルール例) を参照。

### 3.3 運用時に必要な通信

| 項目 | 値 |
| --- | --- |
| 送信先 FQDN | `<foundry-resource-name>.openai.azure.com` |
| 名前解決 | Private DNS Zone `privatelink.openai.azure.com` でプライベート IP を返却 |
| ポート | 443/TCP |
| 経路 | VNet 内 Private Endpoint（Internet 経由なし） |

### 3.4 NSG の方針

VM サブネットの NSG では次の方針とする。

- **Outbound**: VNet 内通信 (Foundry PE への到達) のみ許可
- **Internet 向け Outbound**: 既定の `AllowInternetOutBound` を、運用時は Azure Firewall のルールで実質遮断
- **Inbound**: 管理用 RDP（VPN 経由）のみ

> 既存 NSG/Firewall の構成詳細は本マニュアルのスコープ外。設計指針として [付録 A](#付録-a-nsg--azure-firewall-ルール例) を参照。

### 3.5 完全オフラインインストールを選ぶ場合

社内ポリシーで「VM をインターネットへ一切出さない」場合は、別の踏み台 PC で次の成果物を取得して持ち込む。

- Node.js LTS の Windows MSI インストーラ
- `@github/copilot` パッケージ（`npm pack @github/copilot` で `.tgz` 化、または `npm install --prefix` のオフラインキャッシュ）

詳細は [6.3 完全オフラインでのインストール](#63-完全オフラインでのインストール) を参照。

---

## 4. VM への接続

### 4.1 VPN 接続

クライアント PC から既存 `MyVNetGateway` (Point-to-Site / Site-to-Site) で接続する。

```powershell
# Windows VPN クライアントから接続後、Azure VNet 内の名前解決が効くか確認
Test-NetConnection -ComputerName ketana-ext-vm-private -Port 3389
```

### 4.2 RDP 接続

```powershell
# プライベート IP に対して RDP
mstsc /v:<VM のプライベート IP>
```

### 4.3 PowerShell 起動と権限確認

VM ログイン後、PowerShell を **管理者として実行**。

```powershell
# 現在のユーザー権限を確認
[Security.Principal.WindowsIdentity]::GetCurrent().Name
([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
```

---

## 5. 前提ソフトのインストール

### 5.1 PowerShell バージョン確認

```powershell
$PSVersionTable.PSVersion
```

`Major` が **6 未満**の場合は PowerShell 7 をインストールする。

```powershell
# winget 経由（インターネット一時許可が必要）
winget install --id Microsoft.PowerShell --source winget --silent --accept-package-agreements --accept-source-agreements

# インストール後、新しい pwsh セッションを開始
pwsh
$PSVersionTable.PSVersion
```

完全オフライン環境では別端末で `PowerShell-7.x.x-win-x64.msi` を取得し、VM にコピーして MSI を実行する。

### 5.2 Node.js LTS のインストール

GitHub Copilot CLI は npm 経由でのインストールが推奨されるため、Node.js LTS（v20 以上推奨）を導入する。

#### 5.2.1 オンライン方式（winget）

```powershell
winget install --id OpenJS.NodeJS.LTS --source winget --silent --accept-package-agreements --accept-source-agreements
```

#### 5.2.2 オフライン方式（MSI 持ち込み）

別端末で <https://nodejs.org/en/download> から `node-v20.x.x-x64.msi` を取得し、VM にコピーしてダブルクリック実行。

#### 5.2.3 バージョン確認

```powershell
# 新規 pwsh セッションで PATH 反映を確認
node -v
npm -v
```

### 5.3 npm のプロキシ設定（必要時のみ）

社内プロキシを経由する場合は次を設定。直接インターネットへ出る構成では不要。

```powershell
npm config set proxy http://<proxy-host>:<port>
npm config set https-proxy http://<proxy-host>:<port>

# 社内 CA を信頼させる場合
npm config set cafile "C:\Path\To\corp-ca.pem"
```

### 5.4 Visual Studio Code（任意・エディタ用途のみ）

Copilot CLI の動作に VS Code は不要だが、VM 上でスクリプト・設定ファイルを編集するエディタとして導入する場合の手順。**GitHub Copilot 拡張は本構成では使用しないためインストールしない。**

> 注: VS Code に Copilot / Copilot Chat 拡張を入れると、CLI とは別の経路（`*.githubcopilot.com` など）で GitHub クラウドへ通信が発生する。閉域要件を破る恐れがあるため、本マニュアルでは拡張を入れない方針とする。

#### 5.4.1 オンライン方式（winget）

```powershell
winget install --id Microsoft.VisualStudioCode --source winget --silent --accept-package-agreements --accept-source-agreements --override "/SILENT /mergetasks=!runcode,addcontextmenufiles,addcontextmenufolders,addtopath"
```

インストール後、新規 PowerShell セッションで PATH 反映を確認。

```powershell
code --version
```

#### 5.4.2 オフライン方式（System Installer 持ち込み）

別端末で <https://code.visualstudio.com/Download> から **Windows x64 System Installer** (`VSCodeSetup-x64-<version>.exe`) を取得し、VM にコピーしてサイレントインストール。

```powershell
.\VSCodeSetup-x64-<version>.exe /VERYSILENT /MERGETASKS="!runcode,addcontextmenufiles,addcontextmenufolders,addtopath"
```

#### 5.4.3 自動更新の無効化（推奨）

閉域運用では VS Code 本体の自動更新通信も発生させたくないため、ユーザー設定で無効化する。

```powershell
$settingsDir = "$env:APPDATA\Code\User"
New-Item -ItemType Directory -Path $settingsDir -Force | Out-Null
$settingsPath = Join-Path $settingsDir "settings.json"
@'
{
  "update.mode": "none",
  "extensions.autoUpdate": false,
  "extensions.autoCheckUpdates": false,
  "telemetry.telemetryLevel": "off"
}
'@ | Set-Content -Path $settingsPath -Encoding UTF8
```

#### 5.4.4 拡張機能のインストールについて

本構成では **拡張機能は導入しない**ことを既定とする。どうしても必要な拡張（言語サポート等）がある場合は次のいずれか:

- 別端末で `.vsix` をダウンロードし、`code --install-extension <path>.vsix` で持ち込みインストール
- Marketplace 通信 (`marketplace.visualstudio.com`) はインストール時のみ一時許可

**GitHub Copilot / GitHub Copilot Chat 拡張はインストールしないこと。**

---

## 6. GitHub Copilot CLI のインストール

### 6.1 npm でのインストール（推奨）

```powershell
npm install -g @github/copilot
```

インストール直後にバージョン確認。

```powershell
copilot --version
```

### 6.2 winget でのインストール（代替）

```powershell
winget install GitHub.Copilot --silent --accept-package-agreements --accept-source-agreements
```

### 6.3 完全オフラインでのインストール

別端末（インターネット接続あり）で次を実行し、生成物を VM にコピーする。

```powershell
# 別端末で実行
mkdir copilot-offline
cd copilot-offline
npm pack @github/copilot
# → @github-copilot-<version>.tgz が生成される

# 依存も含めて取得したい場合は次のように一時ディレクトリで実行
mkdir tmp-install
cd tmp-install
npm init -y
npm install @github/copilot
# tmp-install\node_modules を丸ごと VM にコピーする方法もある
```

VM 側で次のように展開する。

```powershell
# .tgz を VM の作業ディレクトリにコピーした後
npm install -g .\github-copilot-<version>.tgz
copilot --version
```

> **注**: `@github/copilot` の依存パッケージも事前に取得する必要がある。可能な限り **5.1.1 オンライン方式（winget）** または **6.1 npm でのインストール** を選択することを推奨する。

---

## 7. 認証と BYOK 設定

### 7.1 設計方針

| 項目 | 値 |
| --- | --- |
| GitHub への `/login` (OAuth デバイスフロー) | **実行しない**（オフラインモードのため不要） |
| LLM プロバイダー | Azure AI Foundry Model（Azure OpenAI 互換） |
| `COPILOT_PROVIDER_TYPE` | `azure`（Azure OpenAI リソースの場合）／ `openai`（OpenAI Chat Completions API 互換エンドポイントの場合） |
| API キーの保管 | 推奨: Key Vault（[付録 C](#付録-c-key-vault-からの-api-キー取得推奨)）／ 簡易: ユーザー環境変数 |

### 7.2 設定すべき環境変数（Azure OpenAI 互換 / 既定）

本検証環境では Foundry リソース `aif-ext-ketana-pe` にデプロイした `gpt-5.4-mini` を使用する。エンドポイントは Azure AI Foundry の GA v1 ルート（`/openai/v1`）を採用し、モデル名はリクエストボディで渡される（从来の `/deployments/<NAME>` パスは不要）。Copilot CLI は v1.0.20 以降、`COPILOT_PROVIDER_API_VERSION` 未設定時はこの v1 ルートに自動適合する。

| 変数名 | 値の例 | 説明 |
| --- | --- | --- |
| `COPILOT_OFFLINE` | `true` | GitHub サーバーへの通信を抑止 |
| `COPILOT_PROVIDER_TYPE` | `azure` | プロバイダー種別 |
| `COPILOT_PROVIDER_BASE_URL` | `https://aif-ext-ketana-pe.cognitiveservices.azure.com/openai/v1` | Foundry リソースの v1 ルート |
| `COPILOT_PROVIDER_API_KEY` | `<Foundry の API キー>` | キー認証（下記注意参照） |
| `COPILOT_MODEL` | `gpt-5.4-mini` | デプロイ名（モデル識別子） |

> **重要：API キーが無効化されている場合**: `aif-ext-ketana-pe` で `disableLocalAuth=true`（または Entra ID 専用設定）の場合は `COPILOT_PROVIDER_API_KEY` にキーを設定しても 401 となる。Copilot CLI は BYOK で Entra ID 認証をネイティブサポートしないため、[付録 E](#付録-e-entra-id-bearer-注入プロキシapi-キー無効化-foundry-用) のローカル Bearer 注入プロキシを介させること。`COPILOT_PROVIDER_API_KEY` にはダミー値（例: `dummy-not-used`）を入れる。

### 7.3 OpenAI 互換エンドポイントの場合（補足）

Foundry にデプロイしたモデルが OpenAI Chat Completions API 互換で公開されている場合は次のように設定する。

```powershell
[Environment]::SetEnvironmentVariable("COPILOT_PROVIDER_TYPE","openai","User")
[Environment]::SetEnvironmentVariable("COPILOT_PROVIDER_BASE_URL","https://<foundry-endpoint>/v1","User")
[Environment]::SetEnvironmentVariable("COPILOT_PROVIDER_API_KEY","<API キー>","User")
[Environment]::SetEnvironmentVariable("COPILOT_MODEL","<モデル名>","User")
```

### 7.4 環境変数の永続設定（PowerShell）

ユーザー環境変数として永続化する。

```powershell
[Environment]::SetEnvironmentVariable("COPILOT_OFFLINE","true","User")
[Environment]::SetEnvironmentVariable("COPILOT_PROVIDER_TYPE","azure","User")
[Environment]::SetEnvironmentVariable("COPILOT_PROVIDER_BASE_URL","https://aif-ext-ketana-pe.cognitiveservices.azure.com/openai/v1","User")
[Environment]::SetEnvironmentVariable("COPILOT_PROVIDER_API_KEY","<YOUR_AZURE_API_KEY>","User")
[Environment]::SetEnvironmentVariable("COPILOT_MODEL","gpt-5.4-mini","User")
```

> Entra ID 認証込みを介す場合は、`COPILOT_PROVIDER_BASE_URL` を `http://127.0.0.1:8787/openai/v1`、`COPILOT_PROVIDER_API_KEY` を `dummy-not-used` に差し替える（[付録 E](#付録-e-entra-id-bearer-注入プロキシapi-キー無効化-foundry-用) 参照）。

> `setx` コマンドでも同様の永続化が可能だが、値の長さ制限（1024 文字）に注意。上記 `[Environment]::SetEnvironmentVariable` 方式を推奨。

### 7.5 設定の反映確認

**新規 PowerShell セッション** を開いて確認。

```powershell
$env:COPILOT_OFFLINE
$env:COPILOT_PROVIDER_TYPE
$env:COPILOT_PROVIDER_BASE_URL
$env:COPILOT_MODEL
# API キーは画面表示しない（マスクして長さだけ確認）
$env:COPILOT_PROVIDER_API_KEY.Length
```

### 7.6 セキュリティ上の推奨

- API キーは平文で環境変数に置かない方が望ましい。Key Vault + DefaultAzureCredential によるランタイム取得は [付録 C](#付録-c-key-vault-からの-api-キー取得推奨) を参照。
- API キーをコピー＆ペーストする際は、PowerShell の履歴 (`Get-PSReadLineOption | Select HistorySavePath`) に残らないよう、対話入力やスクリプト実行を併用する。

---

## 8. 動作確認

### 8.1 ネットワーク疎通確認

```powershell
# Foundry エンドポイントへの到達（Private Endpoint 経由）
Resolve-DnsName "<foundry-resource>.openai.azure.com"
# → プライベート IP (10.x.x.x など) が返ることを確認

Test-NetConnection -ComputerName "<foundry-resource>.openai.azure.com" -Port 443
# → TcpTestSucceeded : True

# GitHub への通信が遮断されていることを確認
Test-NetConnection -ComputerName api.githubcopilot.com -Port 443
# → TcpTestSucceeded : False を期待
```

### 8.2 Copilot CLI 起動

```powershell
copilot
```

- 初回起動時のスプラッシュ表示
- オフラインモードかつ BYOK 設定が読み込まれ、`/login` の促しが出ないこと
- プロンプト入力待ちになること

### 8.3 モデル設定の確認

CLI 内で次のスラッシュコマンドを実行。

```
/model
```

`COPILOT_MODEL` の値が選択されていることを確認。

### 8.4 サンプルプロンプト

対話モードで次のような簡単な指示を出し、応答が返ることを確認する。

```
カレントディレクトリのファイル一覧を表示する PowerShell スクリプトを書いてください。
```

非対話モード（ワンショット）でも確認。

```powershell
copilot -p "今日の日付を ISO 8601 形式で表示する PowerShell ワンライナーを教えて"
```

### 8.5 通信ログでの最終確認

Azure Firewall / NSG フローログ / Foundry の診断ログで次を確認する。

| 項目 | 期待値 |
| --- | --- |
| `*.githubcopilot.com` への HTTPS 通信 | **発生していない** |
| `<foundry-resource>.openai.azure.com` への HTTPS | プライベート IP 宛、発生している |
| `*.openai.azure.com` パブリック IP 宛通信 | **発生していない** |

### 8.6 ライセンス前提の検証

[2.5 ライセンス前提](#25-ライセンス前提要検証) で「Copilot サブスクリプション必要」と仮置きしているため、無サブスクリプション GitHub アカウントの API キー等ではなく **BYOK のみで起動できるか**を実機で確認する。エラーで起動できない場合は Copilot サブスクリプションを付与したアカウントでログイン情報を取得し、再試行する。

---

## 9. 制約事項

### 9.1 機能制約（オフラインモード起因）

- GitHub リポジトリ／Issue／Pull Request を自然言語で操作する機能は使用不可
- ホスト型 MCP サーバー（GitHub MCP 等の `api.mcp.github.com`）は使用不可
- 既定モデル（Claude Sonnet / GPT-5）は使用不可（BYOK モデルのみ）
- テレメトリ／使用状況レポートは GitHub 側に送信されない

### 9.2 モデル要件

- **Tool calling (関数呼び出し)** のサポート必須
- **ストリーミング応答** のサポート必須
- コンテキストウィンドウ **128k トークン以上を推奨**

サポートしないモデルを指定すると Copilot CLI はエラーを返す。

### 9.3 ライセンス／監査

- BYOK 利用時の Copilot サブスクリプション要否は公式ドキュメント上未明示（[8.6](#86-ライセンス前提の検証) で要検証）
- 利用ログは GitHub 側に残らない。**監査・利用状況の確認は Foundry 側の診断ログのみが対象**

### 9.4 アップデート

- Copilot CLI 本体（`@github/copilot`）の更新は npm からの再取得が必要。閉域運用中は更新できないため、定期的に「インストール時許可ルール」を一時開放して `npm update -g @github/copilot` を実行する運用を想定

---

## 10. トラブルシューティング

### 10.1 `copilot` コマンドが見つからない

```powershell
where.exe copilot
# 期待: %APPDATA%\npm\copilot.cmd など

# PATH 確認
$env:PATH -split ";" | Select-String npm
```

`%APPDATA%\npm` が PATH に含まれていない場合は追加する。

```powershell
[Environment]::SetEnvironmentVariable("PATH", "$env:PATH;$env:APPDATA\npm", "User")
```

### 10.2 起動時にモデルエラー (`model does not support tool calling` 等)

Foundry にデプロイしたモデルが tool calling／ストリーミング非対応。対応モデル（例: `gpt-4o`, `gpt-4.1`, `gpt-5` 系）を再デプロイし、`COPILOT_MODEL` を更新する。

### 10.3 接続タイムアウト

```powershell
# 名前解決の確認（Private DNS Zone が機能しているか）
Resolve-DnsName "<foundry-resource>.openai.azure.com"
# プライベート IP が返らない場合: Private DNS Zone のリンク／A レコード未登録
```

```powershell
# TCP 接続確認
Test-NetConnection -ComputerName "<foundry-resource>.openai.azure.com" -Port 443
```

確認ポイント:

- Private Endpoint が VM の VNet に所属または PE 側 VNet にピアリングされているか
- VNet と `privatelink.openai.azure.com` Private DNS Zone のリンクがあるか
- NSG の Outbound で VNet 内通信が許可されているか
- Foundry リソースのファイアウォール設定で "Allow Azure services" 以外の経路が無効になっていないか（PE 経由は別軸で許可される）

### 10.4 401 / 403 認証エラー

- `COPILOT_PROVIDER_API_KEY` の値が誤っている → Foundry の Keys ブレードから再取得
- Foundry リソース側で API キー認証が無効化されている（`disableLocalAuth=true` / Entra ID 専用）→ 現状の Copilot CLI BYOK は **API キー方式が前提**で Entra ID 認証は未サポート。回避策として [付録 E](#付録-e-entra-id-bearer-注入プロキシapi-キー無効化-foundry-用) のローカル Bearer 注入プロキシを使用する
- デプロイ名が `COPILOT_PROVIDER_BASE_URL` の `/deployments/<NAME>` 部分と `COPILOT_MODEL` で一致しているか確認

### 10.5 SSL 証明書エラー

社内 CA を介す場合の対処:

```powershell
# Node.js に追加 CA を指定
[Environment]::SetEnvironmentVariable("NODE_EXTRA_CA_CERTS","C:\Path\To\corp-ca.pem","User")
```

### 10.6 npm install が失敗する

- 社内プロキシ設定漏れ → `npm config get proxy` で確認
- レジストリ到達不可 → Azure Firewall アプリケーションルールで `registry.npmjs.org` が許可されているか
- 完全オフライン要件なら [6.3 完全オフラインでのインストール](#63-完全オフラインでのインストール) に切替

### 10.7 ログ収集

| 種類 | 場所 |
| --- | --- |
| Copilot CLI ローカル設定 | `%USERPROFILE%\.copilot\` |
| 詳細ログ | `copilot --help` で `--verbose` 等のオプションを確認 |
| Foundry 診断ログ | Azure Portal → Foundry リソース → Diagnostic settings |
| Azure Firewall ログ | Log Analytics ワークスペース（`AZFWApplicationRule` / `AZFWNetworkRule`） |

---

## 付録 A: NSG / Azure Firewall ルール例

### A.1 インストール時の Azure Firewall アプリケーションルール（一時）

| Name | Source | Protocol | Target FQDN |
| --- | --- | --- | --- |
| AllowNodeJS | VM サブネット | https:443 | `nodejs.org`, `*.nodejs.org` |
| AllowNpm | VM サブネット | https:443 | `registry.npmjs.org`, `npmjs.org`, `*.npmjs.com` |
| AllowWinget | VM サブネット | https:443 | `cdn.winget.microsoft.com`, `*.delivery.mp.microsoft.com`, `winget.azureedge.net` |
| AllowGhArtifacts | VM サブネット | https:443 | `pkg-containers.githubusercontent.com`, `objects.githubusercontent.com` |
| AllowVSCode | VM サブネット | https:443 | `code.visualstudio.com`, `*.vo.msecnd.net` |

**運用開始後に上記ルールはすべて削除する。**

### A.2 運用時の NSG（VM サブネット）

| 方向 | 優先度 | 名前 | 送信元 | 送信先 | プロトコル | 動作 |
| --- | --- | --- | --- | --- | --- | --- |
| Outbound | 100 | AllowFoundryPE | VM サブネット | Foundry PE サブネット | TCP/443 | Allow |
| Outbound | 4096 | DenyInternet | VM サブネット | Internet | * | Deny |
| Inbound | 100 | AllowRdpFromVPN | VPN クライアントレンジ | VM サブネット | TCP/3389 | Allow |

### A.3 Azure Firewall ルール（運用時）

運用時はインターネット向け FQDN ルールを **すべて Deny** とし、必要に応じて Private DNS Zone と Private Endpoint で VNet 内通信のみを許可する。

---

## 付録 B: 環境変数チートシート

| 変数名 | 必須 | 例 | 説明 |
| --- | --- | --- | --- |
| `COPILOT_OFFLINE` | ◎ | `true` | GitHub サーバーへの通信を抑止 |
| `COPILOT_PROVIDER_TYPE` | △ | `azure` / `openai` / `anthropic` | プロバイダー種別。既定は `openai` |
| `COPILOT_PROVIDER_BASE_URL` | ◎ | `https://myfoundry.openai.azure.com/openai/deployments/gpt-4o-deploy` | エンドポイントのベース URL |
| `COPILOT_PROVIDER_API_KEY` | ○ | `xxxxxxxx...` | API キー認証を使う場合 |
| `COPILOT_MODEL` | ◎ | `gpt-4o-deploy` | デプロイ名／モデル識別子。CLI 起動時 `--model` フラグでも指定可 |
| `NODE_EXTRA_CA_CERTS` | △ | `C:\path\corp-ca.pem` | 社内 CA 信頼用（必要時） |
| `HTTPS_PROXY` | △ | `http://proxy:8080` | プロキシ経由運用時 |

◎ = 必須 / ○ = 推奨 / △ = 環境次第

---

## 付録 C: Key Vault からの API キー取得（推奨）

API キーを Key Vault に保管し、Copilot CLI 起動前に取得して環境変数にロードする例。

### C.1 前提

- Key Vault `kv-ketana-ext-private` に `foundry-api-key` というシークレット名で保管
- VM のシステム割り当てマネージド ID（または UAI）に Key Vault の `Key Vault Secrets User` ロールを付与
- Key Vault の Private Endpoint が VM の VNet に存在
- VM に `Az.KeyVault` モジュールをインストール

```powershell
Install-Module Az.KeyVault -Scope CurrentUser -Force
```

### C.2 取得スクリプト

`%USERPROFILE%\start-copilot.ps1` として保存。

```powershell
# Managed Identity でサインイン
Connect-AzAccount -Identity | Out-Null

# Key Vault から API キーを取得
$apiKey = (Get-AzKeyVaultSecret `
    -VaultName "kv-ketana-ext-private" `
    -Name "foundry-api-key" `
    -AsPlainText)

# プロセス環境変数として一時設定（永続化しない）
$env:COPILOT_OFFLINE          = "true"
$env:COPILOT_PROVIDER_TYPE    = "azure"
$env:COPILOT_PROVIDER_BASE_URL = "https://myfoundry.openai.azure.com/openai/deployments/gpt-4o-deploy"
$env:COPILOT_PROVIDER_API_KEY = $apiKey
$env:COPILOT_MODEL            = "gpt-4o-deploy"

# Copilot CLI 起動
copilot
```

### C.3 実行

```powershell
pwsh -NoProfile -File "$env:USERPROFILE\start-copilot.ps1"
```

API キーは **プロセス内のみ**で保持され、ユーザー／システム環境変数として永続化されないため安全性が高い。

---

## 付録 D: 参照リンク

- GitHub Copilot CLI 公式リポジトリ: <https://github.com/github/copilot-cli>
- GitHub Copilot CLI BYOK ドキュメント (日本語): <https://docs.github.com/ja/copilot/how-tos/copilot-cli/customize-copilot/use-byok-models>
- GitHub Copilot CLI BYOK ドキュメント (英語): <https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/use-byok-models>
- Copilot Allowlist Reference: <https://docs.github.com/en/copilot/reference/allowlist-reference>
- Azure AI Foundry / Azure OpenAI ネットワーキング (Private Endpoint): <https://learn.microsoft.com/azure/ai-services/cognitive-services-virtual-networks>
- Azure Private DNS Zone for Cognitive Services / Azure OpenAI: <https://learn.microsoft.com/azure/private-link/private-endpoint-dns>

---

## 更新履歴

| 日付 | 内容 | 担当 |
| --- | --- | --- |
| 2026-05-17 | 初版作成 | – |
| 2026-05-17 | 付録 E（Entra Bearer 注入プロキシ）を追加 | – |
| 2026-05-17 | 5.4 VS Code（エディタ用途・拡張なし）を追加 | – |
