import os
import requests
import json

# 環境変数からSecretsを取得
PSI_API_KEY = os.environ.get("PSI_API_KEY")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
TARGET_URL = "https://good-apps.jp/"  # 監視対象のURL

if not PSI_API_KEY or not SLACK_WEBHOOK_URL:
    raise RuntimeError("Missing PSI_API_KEY or SLACK_WEBHOOK_URL environment variables.")

# PageSpeed Insights APIエンドポイントとパラメータ
API_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

# モバイル・デスクトップそれぞれのCore Web Vitalsカテゴリー判定結果を収集
issues = {"mobile": [], "desktop": []}
for strategy in ["mobile", "desktop"]:
    params = {
        "url": TARGET_URL,
        "strategy": strategy,
        "key": PSI_API_KEY
    }
    response = requests.get(API_ENDPOINT, params=params)
    # APIエラー時は処理を中断
    if response.status_code != 200:
        print(f"Error: PSI API request failed for {strategy} (status {response.status_code})")
        response.raise_for_status()
    data = response.json()
    # フィールドデータ（Chrome UX）から指標を取得（ページ単体のデータがなければオリジン全体のデータを使用）
    metrics = None
    # ページ単体のデータがある場合
    if data.get("loadingExperience", {}).get("metrics"):
        metrics = data["loadingExperience"]["metrics"]
    # オリジンレベルのデータがある場合（loadingExperienceになければこちらを使用）
    if not metrics and data.get("originLoadingExperience", {}).get("metrics"):
        metrics = data["originLoadingExperience"]["metrics"]
    if not metrics:
        print(f"No field data available for {TARGET_URL} ({strategy}).")
        continue

    # 各指標のカテゴリーを確認し、"SLOW"（不良）のものだけ記録
    # 対象指標: LCP, FID, CLS（存在すればチェック）
    # LCP = Largest Contentful Paint, FID = First Input Delay, CLS = Cumulative Layout Shift
    lcp = metrics.get("LARGEST_CONTENTFUL_PAINT_MS")
    fid = metrics.get("FIRST_INPUT_DELAY_MS")
    cls = metrics.get("CUMULATIVE_LAYOUT_SHIFT_SCORE")
    # LCPチェック
    if lcp and lcp.get("category") == "SLOW":
        # 値を秒単位に変換し小数1桁で整形
        lcp_seconds = lcp.get("percentile", 0) / 1000.0
        issues[strategy].append(f"LCP {lcp_seconds:.1f}s")
    # FIDチェック
    if fid and fid.get("category") == "SLOW":
        fid_ms = fid.get("percentile", 0)
        issues[strategy].append(f"FID {fid_ms}ms")
    # CLSチェック
    if cls and cls.get("category") == "SLOW":
        cls_val = cls.get("percentile", 0)
        # CLS値はそのまま（小数）取得できる場合と、整数で来る場合があるためfloatに変換
        try:
            cls_val = float(cls_val)
        except (TypeError, ValueError):
            cls_val = cls_val or 0
        issues[strategy].append(f"CLS {cls_val:.2f}")

# モバイル・デスクトップともに不良指標がなければ通知しないで終了
if not issues["mobile"] and not issues["desktop"]:
    print("No poor Core Web Vitals – no Slack notification sent.")
    exit(0)

# Slack通知メッセージの組み立て
message_lines = []
message_lines.append(f"Core Web Vitals issues for {TARGET_URL}")
# モバイルの結果
if issues["mobile"]:
    # 不良指標をカンマ区切りで列挙
    message_lines.append(f"Mobile: " + ", ".join(issues["mobile"]))
else:
    message_lines.append("Mobile: No poor metrics")
# デスクトップの結果
if issues["desktop"]:
    message_lines.append(f"Desktop: " + ", ".join(issues["desktop"]))
else:
    message_lines.append("Desktop: No poor metrics")

payload = {"text": "\n".join(message_lines)}

# Slack Incoming Webhook にPOST
try:
    resp = requests.post(SLACK_WEBHOOK_URL, data=json.dumps(payload),
                         headers={"Content-Type": "application/json"})
    if resp.status_code != 200:
        print(f"Slack webhook response: {resp.status_code}, body: {resp.text}")
except Exception as e:
    print(f"Failed to send Slack notification: {e}")
