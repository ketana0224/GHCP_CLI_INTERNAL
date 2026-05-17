# github Copilot CLI を Azure の VM　から完全閉域で動作させるための検証計画


## 目的
github Copilot CLI を Azure の VM　から完全閉域での動作確認と制約事項の確認

## 環境
サブスクリプション : 571e49d7-d4d6-4cb5-884f-2e14bfaa662c(既存)
リソースグループ : ketana-ext-private-ai(既存)
閉域内のAzure VMから利用 : ketana-ext-vm-private(既存)
閉域VMへはVPNで接続 : MyVNetGateway(既存)
閉域MS Foundry LLM : aif-ext-ketana-pe(既存)

## 検証内容
1.Azure VM に Github Copilot CLIをインストール
2.Azure から Github Copilot CLIを実行


