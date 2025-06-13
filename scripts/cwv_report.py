import os
import time
import json
import logging
import requests
import matplotlib.pyplot as plt

# ──────────────────────────────
#  ログ設定
# ──────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ──────────────────────────────
#  環境変数
# ──────────────────────────────
CRUX_API_KEY    = os.environ.get("CRUX_API_KEY")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL   = os.environ.get("SLACK_CHANNEL_ID")

for name, val in {"CRUX_API_KEY": CRUX_API_KEY,
                  "SLACK_BOT_TOKEN": SLACK_BOT_TOKEN,
                  "SLACK_CHANNEL": SLACK_CHANNEL}.items():
    if not val:
        logging.error("%s is not set", name)
        raise SystemExit(1)

# ──────────────────────────────
#  対象 URL
# ──────────────────────────────
pages = [
    "https://good-apps.jp/",
    # 追加入力可
]

# ──────────────────────────────
#  閾値定義
# ──────────────────────────────
THRESHOLDS = {
    "largest_contentful_paint": {"good": 2500, "ni": 4000},   # ms
    "interaction_to_next_paint": {"good": 200, "ni": 500},    # ms
    "cumulative_layout_shift":   {"good": 0.1, "ni": 0.25},   # unitless
}

metrics       = list(THRESHOLDS.keys())          # LCP / INP / CLS
form_factors  = ["PHONE", "DESKTOP"]
weeks_count   = 13

metrics_data = {
    m: {ff: [{"good": 0, "ni": 0, "poor": 0} for _ in range(weeks_count)]
        for ff in form_factors}
    for m in metrics
}

# ──────────────────────────────
#  CrUX History API 呼び出し
# ──────────────────────────────
api_endpoint = (
    "https://chromeuxreport.googleapis.com/v1/records:queryHistoryRecord"
    f"?key={CRUX_API_KEY}"
)
headers = {"Content-Type": "application/json", "Accept": "application/json"}

for url in pages:
    for ff in form_factors:
        body = {
            "url": url,
            "formFactor": ff,
            "metrics": metrics,
            "collectionPeriodCount": weeks_count,
        }
        res = requests.post(api_endpoint, headers=headers, json=body)
        if res.status_code != 200:
            logging.warning("CrUX API error %s (%s): %s", url, ff, res.status_code)
            continue

        data = res.json()
        if "record" not in data or "metrics" not in data["record"]:
            logging.warning("No data for %s (%s)", url, ff)
            continue

        for metric, values in data["record"]["metrics"].items():
            if metric not in THRESHOLDS:
                continue
            ts = values.get("percentilesTimeseries", {}).get("p75s", [])
            for week_idx, entry in enumerate(ts):
                if entry is None:
                    continue
                raw = entry.get("percentile") if isinstance(entry, dict) else entry
                try:
                    p75 = float(raw)
                except (TypeError, ValueError):
                    logging.warning("Skip invalid value %r for %s %s week%s",
                                    raw, metric, ff, week_idx)
                    continue

                thr = THRESHOLDS[metric]
                cat = (
                    "good" if p75 <= thr["good"]
                    else "ni" if p75 <= thr["ni"]
                    else "poor"
                )
                metrics_data[metric][ff][week_idx][cat] += 1

        # レート制限回避（60 req/min）
        time.sleep(1)

# ──────────────────────────────
#  可視化
# ──────────────────────────────
fig, axes = plt.subplots(len(metrics), len(form_factors), figsize=(10, 12))
colors = {"good": "#4caf50", "ni": "#ffc107", "poor": "#f44336"}
weeks = list(range(1, weeks_count + 1))

for i, metric in enumerate(metrics):
    name = metric.upper()
    for j, ff in enumerate(form_factors):
        ax = axes[i, j]
        series = metrics_data[metric][ff]
        for cat in ("good", "ni", "poor"):
            ax.plot(weeks, [d[cat] for d in series],
                    label=cat.capitalize(),
                    color=colors[cat],
                    marker="o")
        ax.set_title(f"{name} - {'Mobile' if ff == 'PHONE' else 'Desktop'}")
        ax.set_xlabel("Week")
        ax.set_ylabel("Page count")
        ax.set_xticks([1, max(1, weeks_count // 2), weeks_count])
        ax.set_ylim(0, len(pages))
        ax.grid(True, linestyle="--", alpha=0.5)

handles, labels = axes[0, 0].get_legend_handles_labels()
fig.legend(handles, labels, loc="lower center", ncol=3)
fig.tight_layout(rect=[0, 0.05, 1, 1])

chart_path = "cwv_trends.png"
fig.savefig(chart_path)
plt.close(fig)

# ──────────────────────────────
#  Slack へアップロード
# ──────────────────────────────
with open(chart_path, "rb") as f:
    payload = {
        "channels": SLACK_CHANNEL,
        "initial_comment": f"*{len(pages)} pages* - 過去13週間のCore Web Vitalsカテゴリー推移レポート（モバイル/PC）",
        "filename": chart_path,
    }
    resp = requests.post(
        "https://slack.com/api/files.upload",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        params=payload,
        files={"file": f},
    )
    if not resp.ok or not resp.json().get("ok"):
        logging.error("Slack upload failed: %s", resp.json().get("error"))
        raise SystemExit(1)

logging.info("Report successfully sent to Slack.")
