# Good Apps Core Web Vitals Slack通知システム

## 概要

`good-apps.jp` 配下の全ページを対象に、Chrome UX Report の履歴 API（CrUX History API）から 13 週間分の Core Web Vitals（LCP, INP, CLS）データを取得し、集計結果をグラフ化して毎日 Slack に通知する自動化システムです。GitHub Actions 上で Python スクリプトを日次実行し、指定の Slack チャンネルに Bot 経由で投稿します。

## 機能

* CrUX History API から週次の p75 値を取得（LCP, INP, CLS）
* Google 指定の閾値で「良好／改善が必要／不良」を判定
* 指標×デバイス（モバイル／デスクトップ）ごとに週次ページ数を集計
* matplotlib で折れ線グラフを自動生成
* Slack Bot トークンで画像＋メッセージをファイルアップロード
* GitHub Actions で毎日定時に実行

## 前提条件

* Python 3.8 以上
* GitHub リポジトリの Secrets に以下を登録済み

  * `CRUX_API_KEY` （Chrome UX Report API キー）
  * `SLACK_BOT_TOKEN` （xoxb- で始まる Bot トークン）
  * `SLACK_CHANNEL_ID` （通知先チャンネル ID）
* Slack アプリに以下スコープを付与済み

  * `chat:write`
  * `files:write`
  * （公開チャンネル投稿時は `chat:write.public`）
* Bot ユーザーを通知先チャンネルに招待

## ディレクトリ構成例

```
├─ .github/workflows/
│   └─ crux_report.yml       # GitHub Actions ワークフロー
├─ scripts/
│   └─ cwv_report.py         # Core Web Vitals 取得＆Slack投稿スクリプト
└─ README.md
```

## セットアップ

1. リポジトリをクローン

   ```bash
   ```

git clone [https://github.com/your-org/good-apps-cwv-slack.git](https://github.com/your-org/good-apps-cwv-slack.git)
cd good-apps-cwv-slack

````

2. Python 仮想環境の作成・有効化
   ```bash
python -m venv venv
source venv/bin/activate  # macOS/Linux
venv\Scripts\activate   # Windows
````

3. 依存ライブラリをインストール

   ```bash
   ```

pip install -r requirements.txt

```

## 環境変数
環境変数は `.env` ファイルにまとめても構いませんが、GitHub Actions ではリポジトリ Secrets を利用します。
```

CRUX\_API\_KEY=あなたのCrUX\_APIキー
SLACK\_BOT\_TOKEN=xoxb-あなたのSlackBotトークン
SLACK\_CHANNEL\_ID=C1234567890

````

## スクリプトの実行
```bash
python scripts/cwv_report.py
````

* 成功すると `cwv_trends.png` が生成され、指定の Slack チャンネルに投稿されます。

## GitHub Actions

`.github/workflows/crux_report.yml` により、UTC 23:00（JST 08:00）に自動実行されます。

```yaml
name: CrUX Web Vitals Report
on:
  schedule:
    - cron: '0 23 * * *'
  workflow_dispatch:

jobs:
  report:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v3
        with: { python-version: '3.10' }
      - run: pip install -r requirements.txt
      - run: python scripts/cwv_report.py
        env:
          CRUX_API_KEY: ${{ secrets.CRUX_API_KEY }}
          SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
          SLACK_CHANNEL_ID: ${{ secrets.SLACK_CHANNEL_ID }}
```

## カスタマイズ

* `scripts/cwv_report.py` 内の `pages` リストに監視対象 URL を追加・変更可能
* 取得期間（週数）を変更する場合は `weeks_count` を調整
* グラフ種類（折れ線／積み上げ棒）や色設定は matplotlib コードを編集

## 参照

* Chrome UX Report API (History API): [https://developer.chrome.com/docs/crux/reference/history-api/records/queryHistoryRecord](https://developer.chrome.com/docs/crux/reference/history-api/records/queryHistoryRecord)
* Core Web Vitals 指標ガイド: [https://support.google.com/webmasters/answer/9205520](https://support.google.com/webmasters/answer/9205520)
* Slack API (files.upload): [https://api.slack.com/methods/files.upload](https://api.slack.com/methods/files.upload)

## ライセンス

MIT License
