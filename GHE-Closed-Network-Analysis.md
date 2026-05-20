# GHE.com で「悪意ある Public Repo への流出」を防げるか

> GitHub Copilot CLI を閉域で運用する際の認証経路・ガバナンス検討メモ。
> 関連: [README.md](../README.md) / [Run-Manual.md](./Run-Manual.md)

## 結論

**大きく防げますが、100% ではありません**。GHE.com (GitHub Enterprise Cloud with data residency / Enterprise Managed Users = EMU) の **構造的分離** が効くため、内部実装の流出経路を大幅に削れます。ただし「ユーザが意図的に外部へ流すルート」が完全にゼロになるわけではないため、**追加の DLP / ネットワーク制御との併用** が前提です。

---

## 1. GHE.com / EMU の構造的メリット

| 仕組み | 効果 |
|---|---|
| **テナント専用エンドポイント** (`<tenant>.ghe.com`) | API 呼び出し先が物理的に分離。`api.github.com`（パブリック）には**そもそも到達しない構成にできる** |
| **Enterprise Managed Users (EMU)** | ユーザは Entra ID 管理。**個人 GitHub.com アカウントとは完全別人格**。EMU ユーザは Public Repo を持てない / fork できない / star もできない |
| **OAuth App / GitHub App 制限** | 管理者承認したアプリ以外は API トークンを発行できない |
| **SAML SSO 強制** | PAT も SSO 認可必須 → 退職時に即無効化 |
| **IP Allow List** | テナントへのアクセス元 IP を制限。社外からの直接アクセス遮断 |
| **Audit Log Streaming** | 全 API 呼び出しを Splunk / Sentinel 等へリアルタイム送信 |

→ EMU ユーザは **そもそも `github.com/<個人>/repo` を作れない**ため、「悪意ある個人 Public Repo に push」という古典的な流出パターンは**構造的に不可能**。

---

## 2. それでも残る流出経路

| 残存リスク | 内容 | 対策 |
|---|---|---|
| **テナント内 Internal/Public リポジトリ** | EMU でもテナント配下に Public Repo を作れる設定があり得る | エンタープライズポリシーで **「Public リポジトリ作成禁止」** を強制 |
| **別エンドポイントへの POST** | `api.github.com` を塞いでも、攻撃者が `pastebin.com` / `webhook.site` / 自前 VPS に POST すれば流出 | **TLS 復号 + URL allowlist で `<tenant>.ghe.com` 以外を全 Deny** |
| **DNS over HTTPS / トンネル** | DoH や独自プロトコルで穴を作る | DNS は内部 Resolver 強制、egress は forward proxy 必須化 |
| **Copilot Chat への貼付** | プロンプトに機密を貼り Copilot 経由で外部 Foundry へ → ただし PE 経由なら閉域内に留まる | プロンプトログ監査・Content Filter |
| **ブラウザ / クリップボード経由** | Bastion 接続元 PC へコピー | Bastion Standard 以上で **クリップボード/ファイル転送制限**、DLP エージェント |
| **AI 出力の Public Repo PR** | EMU ユーザでも他組織の Public Repo へ PR 送信できる可能性 | OAuth App 制限 + IP Allow List + 監査 |
| **悪意あるインサイダー** | 正規権限を悪用したコピペ・スクショ | 行動ログ / UEBA / Privileged Access Management |

---

## 3. 推奨レイヤード防御（GHE.com を中核に）

```
[Layer 1] アイデンティティ
  Entra ID + EMU + SAML SSO + Conditional Access
       ↓
[Layer 2] ネットワーク
  TLS 復号 forward proxy
    Allow: <tenant>.ghe.com (パス制限あり)
           <your-foundry>.cognitiveservices.azure.com (PE)
    Deny : api.github.com, github.com, *.githubusercontent.com,
           pastebin.*, *.webhook.site, 全未承認 FQDN
       ↓
[Layer 3] エンドポイント
  Bastion Standard (クリップボード/ファイル転送禁止)
  VM に DLP エージェント
  ローカルディスク暗号化
       ↓
[Layer 4] アプリ
  GHE.com Policy:
    - Public Repo 作成禁止
    - 外部コラボレータ招待禁止
    - OAuth App は管理者承認制
    - Copilot Chat ログ収集 ON
       ↓
[Layer 5] 監査
  GHE Audit Log Streaming → Sentinel
  Foundry Diagnostic Logs → Log Analytics
  Forward Proxy アクセスログ → Sentinel
  → UEBA で異常検知
```

---

## 4. 「これで止まるか」判定マトリクス

| 攻撃シナリオ | パブリック GitHub + FW 開放のみ | GHE.com 単体 | GHE.com + Layer 1-5 |
|---|---|---|---|
| 個人アカウントで Public Repo に push | ✗ 通る | **○ 構造的に不可** | ○ |
| テナント内 Public Repo に push | ✗ 通る | △ 設定次第 | ○（ポリシーで禁止） |
| `pastebin.com` に curl で送信 | ✗ 通る | ✗ 通る | **○ FW で Deny** |
| Copilot プロンプト経由で外部 LLM 送信 | ✗ 通る | ✗ 通る | ○（PE で閉域、Content Filter） |
| Bastion からローカル PC にコピペ | ✗ 通る | ✗ 通る | ○（Bastion 制限 + DLP） |
| 退職者のトークン継続利用 | ✗ 通る | ○（Entra 連動失効） | ○ |
| 悪意あるインサイダーの物理撮影 | ✗ 通る | ✗ 通る | △ 検知のみ |

---

## 5. ライセンス・コスト面の現実

| 項目 | 概算 |
|---|---|
| GitHub Enterprise Cloud (EMU) | ユーザあたり $21/月 (Enterprise) + Copilot Enterprise $39/月 = **$60/月/人** |
| Azure Firewall Premium | 約 $1,300/月 + データ処理量課金 |
| Entra ID P2 (Conditional Access フル機能) | $9/月/人 |
| Sentinel | データ取り込み量課金 |

→ **小規模 PoC で導入する代物ではない**。エンタープライズ統制が必須な業種（金融・公共・防衛・医療）向け。

---

## 6. 最終的な答え

> **GHE.com なら API を叩けても悪意ある Public Repo への流出は防げる？**

| 観点 | 回答 |
|---|---|
| 「個人 Public Repo への流出」 | **構造的に防げる**（EMU で個人 Repo を持てない） |
| 「テナント内の Public 化」 | **ポリシーで禁止すれば防げる** |
| 「`api.github.com` 以外への流出」 | **GHE.com 単独では防げない** → ネットワーク制御（TLS 復号 forward proxy）必須 |
| 「インサイダーの意図的流出全般」 | **DLP / Bastion 制限 / 監査の併用が必須** |

つまり GHE.com は **「GitHub 内の流出経路を構造的に塞ぐ」最強の手段** ですが、それ単独では万能ではなく、**ネットワーク・エンドポイント・監査と組み合わせて初めて完成する** ものです。逆に、これらを揃えれば「閉域 + Copilot 活用 + ガバナンス両立」という当初のゴールにかなり近づけます。

---

## 付録: 前提となる議論の経緯

本書は以下の検討フローの結論として作成されました:

1. `COPILOT_OFFLINE=true` でも `/login` は必要 → `api.github.com` 通信が発生
2. `api.github.com` を開けると Copilot 用 API 以外（`/repos`, `/issues` 等）も叩けてしまう
3. ホスト単位（FQDN）では auth とデータ系 API を分離できない（同居）
4. パス単位制御には TLS 復号 + URL allowlist が必要（Azure Firewall Premium / Squid SSL Bump 等）
5. デバイスフローに変えても定期エンタイトルメント検証 (`/copilot_internal/v2/token`) は残るため解決にならない
6. **構造的分離には GHE.com (EMU) が必要** ← 本書

## 参考リンク

- [GitHub Enterprise Cloud with data residency](https://docs.github.com/enterprise-cloud@latest/admin/data-residency)
- [About Enterprise Managed Users](https://docs.github.com/enterprise-cloud@latest/admin/managing-iam/understanding-iam-for-enterprises/about-enterprise-managed-users)
- [Restricting OAuth apps and GitHub Apps](https://docs.github.com/enterprise-cloud@latest/organizations/managing-oauth-access-to-your-organizations-data)
- [Audit log streaming](https://docs.github.com/enterprise-cloud@latest/admin/monitoring-activity-in-your-enterprise/reviewing-audit-logs-for-your-enterprise/streaming-the-audit-log-for-your-enterprise)
- [Azure Firewall Premium - URL filtering](https://learn.microsoft.com/azure/firewall/premium-features#url-filtering)
