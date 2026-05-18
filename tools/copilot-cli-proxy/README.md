# Copilot CLI Entra Bearer Injection Proxy

GitHub Copilot CLI の BYOK は API キー前提のため、API キーが無効化された Azure AI Foundry (PE) を呼び出せません。本プロキシは `127.0.0.1:8787` で受けたリクエストに Entra ID Bearer トークンを差し込んで Foundry へ転送します。

## セットアップ

```powershell
cd C:\GitHub\GHCP_INTERNAL\tools\copilot-cli-proxy
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 起動

```powershell
# 事前に: az login --tenant yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy
python proxy.py
```

起動ログに `Token acquired, valid for NNNN seconds` が出れば認証 OK。

## Copilot CLI 側の環境変数

別ターミナルで一度だけ実行（ユーザースコープ）:

```powershell
[Environment]::SetEnvironmentVariable("COPILOT_PROVIDER_TYPE","openai","User")
[Environment]::SetEnvironmentVariable("COPILOT_PROVIDER_BASE_URL","http://127.0.0.1:8787/openai/v1","User")
[Environment]::SetEnvironmentVariable("COPILOT_PROVIDER_API_KEY","dummy-not-used","User")
[Environment]::SetEnvironmentVariable("COPILOT_MODEL","gpt-5.4-mini","User")
```

新しいターミナルを開いて `copilot` を実行。

## VS Code Copilot Chat 側の設定（任意）

VS Code 本体の Copilot Chat は Azure 組み込みプロバイダが Entra ID をネイティブサポートしているため**通常は本プロキシ不要**です。ただし、

- 同じ PE Foundry を **OpenAI 互換ルート経由で別エントリとして登録したい**
- 既存の `chatLanguageModels.json` の Azure エントリと並べて A/B テストしたい
- 認証フローを CLI と完全に揃えたい

といったケースでは、プロキシを「OpenAI 互換カスタムモデル」として登録できます。

### 手順

1. プロキシを起動した状態にする（`python proxy.py`）
2. VS Code: コマンドパレット → **Chat: Manage Language Models** → **Add Models** → **OpenAI Compatible** を選択
3. 次の値を入力:

   | 項目 | 値 |
   | --- | --- |
   | Base URL | `http://127.0.0.1:8787/openai/v1` |
   | API Key | `dummy-not-used`（任意の文字列で可） |
   | Model ID | `gpt-5.4-mini` |

4. 表示名は任意（例: `gpt-5.4-mini (PE via proxy)`）

`settings.json` に直接書く場合:

```jsonc
"github.copilot.chat.customOAIModels": {
  "gpt-5.4-mini": {
    "name": "gpt-5.4-mini (PE via proxy)",
    "url": "http://127.0.0.1:8787/openai/v1/chat/completions",
    "toolCalling": true,
    "vision": true,
    "maxInputTokens": 128000,
    "maxOutputTokens": 16000
  }
}
```

API キーはモデル追加 UI でのみ受け付けられ Secret Storage に保存されます（`settings.json` には書かない）。

> 既存の `chatLanguageModels.json` の Azure エントリ（`tenantId` 付き）はそのまま残して問題ありません。OpenAI 互換エントリは別物として共存します。

## カスタマイズ

環境変数で上書き可:

| 変数 | デフォルト |
| --- | --- |
| `PROXY_HOST` | `127.0.0.1` |
| `PROXY_PORT` | `8787` |
| `FOUNDRY_BASE_URL` | `https://<your-foundry>.cognitiveservices.azure.com` |
| `ENTRA_SCOPE` | `https://ai.azure.com/.default` |

## セキュリティ

- 127.0.0.1 限定 listen（外部公開禁止）
- ダミー `Authorization` / `api-key` ヘッダは破棄してから Bearer を注入
- トークンは有効期限 5 分前まで再利用、それ以降は `DefaultAzureCredential` で自動再取得
