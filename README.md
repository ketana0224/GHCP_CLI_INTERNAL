# GHCP_CLI_INTERNAL

GitHub Copilot CLI を **Azure 閉域環境** で動作させるための社内検証プロジェクト。

---

## 検証テーマ

GitHub Copilot CLI を、インターネット直結を最小化した Azure 閉域 Windows VM 上で、**バックエンド LLM として閉域内 Azure AI Foundry Model のみを使う構成（BYOM / BYOK）** で動作させ、以下を検証する。

- **データ平面の閉域性**: コード補完/チャット時の推論リクエストが Private Endpoint 経由で Foundry に到達し、インターネットに出ないこと
- **認証方式の選択肢**: 以下 2 パターンの導入手順・運用性・制約の差分整理
  - **API キー方式**（標準パス）
  - **Entra ID 専用方式**（`disableLocalAuth=true` の Foundry に対し、ローカル Bearer 注入プロキシ経由で利用）
- **Outbound の最小化**: VM の NSG で `Internet` 宛て送信を全 Deny した状態でも Copilot CLI / Foundry プロキシが動作可能かの確認
- **運用接続**: 公開 RDP を排し、Azure Bastion 経由で VM を運用する想定の妥当性確認
- **本番想定（ExpressRoute）への移植性**: 検証構成（VPN P2S + NSG）から本番構成（ER + Azure Firewall）への置き換え可能性の整理

---

## 検証結果（サマリ）

**結論: PE で閉域化された各 Azure リソース（Foundry / AI Search / Storage）に対して、NSG で `Internet` 宛て Outbound を全 Deny した閉域 VM から、GitHub Copilot CLI が正常動作することを確認した。**

| 検証項目 | 結果 |
|---|---|
| VM サブネットの NSG で `Internet` 宛て Outbound を全 Deny | ✅ 適用済み（`VirtualNetwork` 宛てのみ Allow） |
| `defaultOutboundAccess=false` 配下の VM からの外部直結遮断 | ✅ Public IP 経由の任意 outbound が発生しないことを確認 |
| Copilot CLI → ローカル Bearer 注入プロキシ → Foundry (PE) | ✅ 推論リクエストが Private Endpoint 経由で到達、応答取得 |
| マネージド ID (IMDS 169.254.169.254) でのトークン取得 | ✅ NSG の Internet Deny の影響を受けず動作 |
| `*.githubcopilot.com` 等 GitHub クラウドへの通信 | ✅ オフラインモード (`COPILOT_OFFLINE=true`) により発生せず |
| Bastion (Developer SKU) 経由の RDP 運用 | ✅ Public IP RDP を閉じた状態で運用可能 |
| AI Search / Storage の PE 経由アクセス | ✅ 同一 VNet 内で名前解決・到達確認 |

---

## 閉域動作を成立させる中核設定

本検証の閉域性は **「NW 側の閉域化」と「Copilot CLI 側のオフラインモード」の二段構え** で成立する。NW を閉じるだけでは CLI が `*.githubcopilot.com` を呼び続けるため、**`COPILOT_OFFLINE=true` が最重要スイッチ** となる。

| 環境変数 | 値 | 役割 |
|---|---|---|
| **`COPILOT_OFFLINE`** | `true` | **GitHub クラウドへの通信を抑止**し、CLI を BYOM モードに切り替える公式トグル |
| `COPILOT_PROVIDER_TYPE` | `azure` | 推論先プロバイダ種別（Foundry / Azure OpenAI 互換） |
| `COPILOT_PROVIDER_BASE_URL` | `https://<your-foundry>.cognitiveservices.azure.com/openai/v1` または `http://127.0.0.1:8787/openai/v1`（プロキシ方式） | 推論リクエスト宛先 |
| `COPILOT_PROVIDER_API_KEY` | API キー / `dummy-not-used`（プロキシ方式） | Foundry 認証（プロキシ方式ではプロキシ側が Bearer に差し替え） |
| `COPILOT_MODEL` | `<your-deployment-name>` | Foundry 上のデプロイ名 |
| `COPILOT_PROVIDER_WIRE_API` | `responses`（プロキシ方式で必要に応じて） | API 形式の指定 |

設定の永続化は [docs/Install-Manual.md §7.4](docs/Install-Manual.md#74-環境変数の永続設定powershell)、実行時の確認は [Run-Manual.md §A.1](Run-Manual.md#a1-環境変数の確認vm-上) を参照。

---

## 検証前提となる環境・Azure サービス

### Azure リソース

| カテゴリ | サービス / リソース | 用途 |
|---|---|---|
| Compute | **Azure Virtual Machine** (Windows 11, `<your-vm>`) | Copilot CLI 実行ホスト |
| Identity | **VM システム割り当てマネージド ID** | Foundry 認証（IMDS 経由でトークン取得） |
| AI | **Azure AI Foundry** (`<your-foundry>` / `<your-foundry-2>`) | バックエンド LLM (BYOM) |
| Networking | **Virtual Network** (`<your-vnet>`) + 複数 subnet (`pe-subnet` / `pe-vm-subnet`) | 閉域 NW 基盤 |
| Networking | **Private Endpoint** (Foundry / AI Search / Storage 各種) | データ平面の閉域化 |
| Networking | **Private DNS Zone** | PE 名前解決 |
| Networking | **Network Security Group** | Inbound/Outbound 制御（`DenyInternetOutBound` で閉域シミュレーション） |
| Access | **Azure Bastion** (Developer SKU, `<your-vnet>-bastion`) | VM 運用接続（RDP 3389） |
| Access | **VPN Gateway** (Basic SKU, P2S/SSTP, `<your-vpn-gw>`) | 検証目的の P2S 接続（任意） |
| Identity Platform | **Microsoft Entra ID** | OAuth2 / マネージド ID 認証基盤 |

### 想定する要件 / 制約

- VM サブネット (`pe-vm-subnet`) は **`defaultOutboundAccess=false`**（明示的にインターネット直 outbound を無効化）
- NSG 送信ルールで **`Internet` 宛て Deny / `VirtualNetwork` 宛て Allow** のみ
- VM の Inbound RDP は **Bastion 経由のみ**（Public IP からの RDP は塞ぐ運用を想定）
- Foundry リソース側は **Public Network Access 無効** + Private Endpoint で接続
- 認証は原則 **マネージド ID + Entra ID**（API キーは Install-Manual.md の標準パスのみ）

### クライアント側 / 開発者前提

- Windows 11 + PowerShell 7
- `winget` / `git` / `python (3.11+)` / `node.js (LTS)` 導入済み
- Azure CLI (`az`)、`gh` (GitHub CLI) ログイン済み
- GitHub Copilot サブスクリプション保有

---

## 手順へのリンク

| ドキュメント | 用途 |
|---|---|
| [docs/Install-Manual.md](docs/Install-Manual.md) | **標準パス**: API キー認証の Foundry を使った Copilot CLI 閉域導入マニュアル |
| [docs/Install-Manual-Proxy.md](docs/Install-Manual-Proxy.md) | **差分パス**: `disableLocalAuth=true` の Entra ID 専用 Foundry に対し、ローカル Bearer 注入プロキシ経由で接続する手順 |
| [Run-Manual.md](Run-Manual.md) | **実行手順書**: 導入済み環境で日々 Copilot CLI を起動・動作確認するための実行コマンド集 |
| [docs/Run-Copilot-CLI-Ephemeral.md](docs/Run-Copilot-CLI-Ephemeral.md) | 環境変数を永続化せず、一時 PowerShell セッションで Copilot CLI を起動する手順 |
| [Plan.md](Plan.md) | 検証計画書（スコープ・進捗・課題管理） |
| [tools/copilot-cli-proxy/](tools/copilot-cli-proxy/) | Entra ID Bearer 注入プロキシ（`proxy.py`）のソース |

---

## 推奨される読み進め方

1. [Plan.md](Plan.md) で検証スコープと進捗を把握
2. [docs/Install-Manual.md](docs/Install-Manual.md) を読み、Azure 側の前提リソース（VNet / PE / VM / Bastion / Foundry）を準備
3. Foundry が API キー有効なら **そのまま** Install-Manual.md の §7 以降に従い Copilot CLI を起動
4. Foundry が Entra ID 専用なら [docs/Install-Manual-Proxy.md](docs/Install-Manual-Proxy.md) の差分に従いローカルプロキシを構築
5. 導入後の **日常的な起動・動作確認** は [Run-Manual.md](Run-Manual.md)、環境変数を永続化したくない場合は [docs/Run-Copilot-CLI-Ephemeral.md](docs/Run-Copilot-CLI-Ephemeral.md) の一時起動スクリプトを利用
