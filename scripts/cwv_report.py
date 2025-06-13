import os, json, requests
import matplotlib.pyplot as plt

# 環境変数からAPIキーやトークンを取得
CRUX_API_KEY    = os.environ.get("CRUX_API_KEY")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL   = os.environ.get("SLACK_CHANNEL_ID")

# 対象ページURLのリスト（good-apps.jp配下の全ページURLを列挙）
pages = [
    "https://good-apps.jp/", 
    # ...必要に応じて他のページURLも追加...
]

# コアウェブバイタル指標の閾値定義（単位統一: LCPとINPはms, CLSは値そのまま）
THRESHOLDS = {
    "largest_contentful_paint": {"good": 2500, "ni": 4000},      # ms
    "interaction_to_next_paint": {"good": 200, "ni": 500},       # ms
    "cumulative_layout_shift": {"good": 0.1, "ni": 0.25}         # unitless
}

# 集計用のデータ構造初期化
# metrics_data[metric][form_factor][week_index]["good/ni/poor"] = count
metrics = ["largest_contentful_paint", "interaction_to_next_paint", "cumulative_layout_shift"]
form_factors = ["PHONE", "DESKTOP"]
weeks_count = 13
# 初期化：指標ごと・デバイスごとの週次集計を0で埋める
metrics_data = {
    m: {
        ff: [ {"good": 0, "ni": 0, "poor": 0} for _ in range(weeks_count) ]
        for ff in form_factors
    }
    for m in metrics
}

# CrUX APIから各ページの履歴データを取得
api_endpoint = f"https://chromeuxreport.googleapis.com/v1/records:queryHistoryRecord?key={CRUX_API_KEY}"
headers = {"Content-Type": "application/json", "Accept": "application/json"}

for url in pages:
    for ff in form_factors:
        # リクエストボディを構築
        request_body = {
            "url": url,
            "formFactor": ff,
            "metrics": metrics,
            "collectionPeriodCount": weeks_count
        }
        response = requests.post(api_endpoint, headers=headers, json=request_body)
        if response.status_code != 200:
            print(f"CrUX API error for {url} ({ff}): {response.status_code}")
            continue
        data = response.json()
        # レスポンスから各メトリクスのタイムシリーズを取得
        if "record" not in data or "metrics" not in data["record"]:
            # データが無い場合はスキップ
            continue
        metrics_ts = data["record"]["metrics"]
        for metric, values in metrics_ts.items():
            # 念のため、要求したmetricsのみ処理
            if metric not in THRESHOLDS:
                continue
            # p75のタイムシリーズを取得（存在しない場合はスキップ）
            if "percentilesTimeseries" not in values or "p75s" not in values["percentilesTimeseries"]:
                continue
            p75_series = values["percentilesTimeseries"]["p75s"]

            for week_idx, entry in enumerate(p75_series):
                # ① データがない週はスキップ
                if entry is None:
                    continue
+                # 値の取り出し（辞書形式 or 純数値 or 文字列）
+                raw = (
+                    entry.get("percentile")              # 辞書形式 {"percentile": ...}
+                    if isinstance(entry, dict)
+                    else entry                           # 旧仕様
+                )
+                try:
+                    p75_value = float(raw)               # 文字列なら数値へ変換
+                except (TypeError, ValueError):
+                    # 予期しない形式はスキップ & ログ
+                    print(f"Skip invalid value: {raw!r} for {metric} {ff} week{week_idx}")
+                    continue
                thr = THRESHOLDS[metric]

                if metric != "cumulative_layout_shift":
                    if p75_value <= thr["good"]:
                        metrics_data[metric][ff][week_idx]["good"] += 1
                    elif p75_value <= thr["ni"]:
                        metrics_data[metric][ff][week_idx]["ni"] += 1
                    else:
                        metrics_data[metric][ff][week_idx]["poor"] += 1
                else:
                    # CLSはそのまま比較
                    if p75_value <= thr["good"]:
                        metrics_data[metric][ff][week_idx]["good"] += 1
                    elif p75_value <= thr["ni"]:
                        metrics_data[metric][ff][week_idx]["ni"] += 1
                    else:
                        metrics_data[metric][ff][week_idx]["poor"] += 1

# 集計データに基づきグラフを作成（指標×デバイスのサブプロット）
fig, axes = plt.subplots(len(metrics), len(form_factors), figsize=(10, 12))
category_colors = {"good": "#4caf50", "ni": "#ffc107", "poor": "#f44336"}
category_labels = {"good": "Good", "ni": "Needs Improvement", "poor": "Poor"}

weeks = list(range(1, weeks_count+1))
for i, metric in enumerate(metrics):
    metric_name = metric.upper()  # 指標名を大文字略称に（LCP, INP, CLSなど）
    for j, ff in enumerate(form_factors):
        ax = axes[i, j]
        data_series = metrics_data[metric][ff]
        # カテゴリ毎に折れ線プロット
        ax.plot(weeks, [d["good"] for d in data_series], label="Good", color=category_colors["good"], marker='o')
        ax.plot(weeks, [d["ni"]   for d in data_series], label="Needs Improvement", color=category_colors["ni"], marker='o')
        ax.plot(weeks, [d["poor"] for d in data_series], label="Poor", color=category_colors["poor"], marker='o')
        # 軸ラベル・タイトル設定
        ax.set_title(f"{metric_name} - {'Mobile' if ff=='PHONE' else 'Desktop'}")
        ax.set_xlabel("Week")
        ax.set_ylabel("Page count")
        ax.set_xticks([1, max(1, weeks_count//2), weeks_count])  # 1, 中間, 最終週あたりを目盛り表示
        ax.set_ylim(0, len(pages))  # 縦軸スケール：0～総ページ数
        ax.grid(True, linestyle='--', alpha=0.5)
# 凡例は図全体の下部にまとめて配置
handles, labels = axes[0,0].get_legend_handles_labels()
fig.legend(handles, labels, loc='lower center', ncol=3)
fig.tight_layout(rect=[0, 0.05, 1, 1])  # 下部に凡例分の余白を確保

# グラフを一時ファイルに保存
chart_path = "cwv_trends.png"
fig.savefig(chart_path)

# Slackに画像ファイルをアップロードしてメッセージ送信
message_text = f"*{len(pages)} pages* - 過去13週間のCore Web Vitalsカテゴリー推移レポート（モバイル/PC）"
with open(chart_path, "rb") as f:
    file_data = {
        "channels": SLACK_CHANNEL,
        "initial_comment": message_text,
        "filename": "cwv_trends.png"
    }
    response = requests.post(
        "https://slack.com/api/files.upload",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        params=file_data,
        files={"file": f}
    )
    res = response.json()
    if not res.get("ok"):
        print("Slack API error:", res.get("error", "unknown error"))
