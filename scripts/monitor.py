import os
import requests
import json
from datetime import datetime

# 環境変数からAPIキーとWebhook URLを取得
CRUX_API_KEY = os.environ.get("CRUX_API_KEY")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

# 対象の全URLリストを定義（good-apps.jp配下のページを網羅）
urls = [
    "https://good-apps.jp/", 
    # ...必要に応じて他のページURLを追加
]

# デバイス種別と指標の設定
form_factors = ["PHONE", "DESKTOP"]  # PHONE=モバイル, DESKTOP=PC
metrics = ["largest_contentful_paint", "interaction_to_next_paint", "cumulative_layout_shift"]

# カテゴリ判定の補助関数（指標名と値からカテゴリを判定）
def categorize_metric(metric_name, p75_value):
    category = ""
    if metric_name == "largest_contentful_paint":
        # LCPはミリ秒(ms)単位で数値が返る（例: 2400ms = 2.4秒）
        if p75_value is None:
            category = "データ不足"
        elif p75_value <= 2500:
            category = "良好"
        elif p75_value <= 4000:
            category = "改善が必要"
        else:
            category = "不良"
    elif metric_name == "interaction_to_next_paint":
        # INPもms単位（FIDの後継指標）
        if p75_value is None:
            category = "データ不足"
        elif p75_value <= 200:
            category = "良好"
        elif p75_value <= 500:
            category = "改善が必要"
        else:
            category = "不良"
    elif metric_name == "cumulative_layout_shift":
        # CLSは小数（0.01など）の文字列として返る場合がある
        if p75_value is None:
            category = "データ不足"
        else:
            # CLS値は文字列の可能性があるため数値に変換
            cls_value = float(p75_value)
            if cls_value < 0.1:
                category = "良好"
            elif cls_value < 0.25:
                category = "改善が必要"
            else:
                category = "不良"
    return category

# 集計用のデータ構造を初期化
# 例: results[form_factor][metric][category] = カウント
results = {
    "PHONE": { "largest_contentful_paint": {"良好":0, "改善が必要":0, "不良":0},
               "interaction_to_next_paint": {"良好":0, "改善が必要":0, "不良":0},
               "cumulative_layout_shift": {"良好":0, "改善が必要":0, "不良":0} },
    "DESKTOP": { "largest_contentful_paint": {"良好":0, "改善が必要":0, "不良":0},
                 "interaction_to_next_paint": {"良好":0, "改善が必要":0, "不良":0},
                 "cumulative_layout_shift": {"良好":0, "改善が必要":0, "不良":0} }
}

# CrUX History APIエンドポイントURL
CRUX_API_ENDPOINT = f"https://chromeuxreport.googleapis.com/v1/records:queryHistoryRecord?key={CRUX_API_KEY}"

# 各URLに対してAPIを呼び出しデータ取得
for url in urls:
    for form in form_factors:
        # リクエストボディを構築（page URL単位のデータ取得）
        request_body = {
            "url": url,
            "formFactor": form,
            "metrics": metrics,
            "collectionPeriodCount": 13  # 直近13期間（週）分のデータを取得
        }
        try:
            response = requests.post(CRUX_API_ENDPOINT, headers={"Content-Type": "application/json"}, 
                                     data=json.dumps(request_body))
            data = response.json()
        except Exception as e:
            print(f"Error fetching data for {url} ({form}): {e}")
            continue

        # APIから正常なレスポンスが得られたか確認
        if "record" not in data:
            # データが無い (例: 該当URLに十分な利用実績データが無い場合など)
            continue

        # 各指標のタイムシリーズから最新（直近週）のp75値を取得してカテゴリ判定
        record = data["record"]
        for metric_name, metric_data in record.items():
            if metric_name not in metrics:
                continue  # 念のため指定外の指標は無視
            # percentilesTimeseries内のp75s配列から最新値を取得
            try:
                p75_values = metric_data["percentilesTimeseries"]["p75s"]
            except KeyError:
                continue  # 該当指標のデータが無い
            if not p75_values:
                continue
            latest_p75 = p75_values[-1]  # 最新週のp75値
            category = categorize_metric(metric_name, latest_p75)
            if category in ["良好", "改善が必要", "不良"]:
                results[form][metric_name][category] += 1

# Slack通知メッセージの作成
date_str = datetime.now().strftime("%Y-%m-%d")  # 本日の日付
total_urls = len(urls)
message_lines = []
message_lines.append(f"*{date_str} 時点のCore Web Vitals計測結果*（直近28日集計）")
# モバイル版結果
message_lines.append(f"*モバイル版（PHONE, 全{total_urls} URL）*")
for category in ["良好", "改善が必要", "不良"]:
    lcp_count = results["PHONE"]["largest_contentful_paint"][category]
    inp_count = results["PHONE"]["interaction_to_next_paint"][category]
    cls_count = results["PHONE"]["cumulative_layout_shift"][category]
    message_lines.append(f"{category}: LCP {lcp_count}件, INP {inp_count}件, CLS {cls_count}件")
# デスクトップ版結果
message_lines.append(f"\n*デスクトップ版（DESKTOP, 全{total_urls} URL）*")
for category in ["良好", "改善が必要", "不良"]:
    lcp_count = results["DESKTOP"]["largest_contentful_paint"][category]
    inp_count = results["DESKTOP"]["interaction_to_next_paint"][category]
    cls_count = results["DESKTOP"]["cumulative_layout_shift"][category]
    message_lines.append(f"{category}: LCP {lcp_count}件, INP {inp_count}件, CLS {cls_count}件")

# SlackへのPOST送信
payload = {"text": "\n".join(message_lines)}
try:
    resp = requests.post(SLACK_WEBHOOK_URL, headers={"Content-Type": "application/json"}, data=json.dumps(payload))
    if resp.status_code != 200:
        print(f"Slack通知エラー: ステータスコード {resp.status_code}")
except Exception as e:
    print(f"Slack通知のリクエスト中にエラー: {e}")
