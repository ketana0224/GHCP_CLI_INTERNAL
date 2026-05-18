# github Copilot CLI を Azure の VM　から完全閉域で動作させるための検証計画


## 目的
github Copilot CLI を Azure の VM　から完全閉域での動作確認と制約事項の確認

## 環境
サブスクリプション : `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`（既存）
リソースグループ : `<your-rg>`（既存）
閉域内のAzure VMから利用 : `<your-vm>`（既存）
閉域VMへはVPNで接続 : `<your-vpn-gw>`（既存）
閉域MS Foundry LLM : `<your-foundry>`（既存）

## 検証内容
1.Azure VM に Github Copilot CLIをインストール
2.Azure から Github Copilot CLIを実行


