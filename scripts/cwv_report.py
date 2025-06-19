import os
import sys
import requests

API_KEY  = os.getenv("CRUX_API_KEY")
WEBHOOK  = os.getenv("SLACK_WEBHOOK_URL")
ORIGIN   = os.getenv("ORIGIN", "https://good-apps.jp")

if not (API_KEY and WEBHOOK):
    sys.exit("CRUX_API_KEY or SLACK_WEBHOOK_URL is missing.")

CRUX_URL = f"https://chromeuxreport.googleapis.com/v1/records:queryRecord?key={API_KEY}"

# CrUX/サーチコンソールと同じ閾値
THRESHOLDS = {
    "largest_contentful_paint": [(0, 2500, "good"), (2500, 4000, "ni"), (4000, float("inf"), "poor")],
    "interaction_to_next_paint": [(0, 200, "good"), (200, 500, "ni"), (500, float("inf"), "poor")],
    "cumulative_layout_shift": [(0, 0.1, "good"), (0.1, 0.25, "ni"), (0.25, float("inf"), "poor")],
}

def classify(metric_name, histogram):
    out = {"good": 0, "ni": 0, "poor": 0}
    for bucket in histogram:
        start = bucket.get("start", 0)
        density = bucket["density"]
        for s, e, label in THRESHOLDS.get(metric_name, []):
            if s <= start < e:
                out[label] += density
                break
    return out

def worst_case(a, b):
    return {
        "poor":  max(a["poor"], b["poor"]),
        "ni":    max(a["ni"],   b["ni"]),
        "good":  min(a["good"], b["good"]),
    }

def fetch():
    resp = requests.post(CRUX_URL, json={"origin": ORIGIN}, timeout=30)
    resp.raise_for_status()
    return resp.json()["record"]["metrics"]

def main():
    metrics = fetch()
    overall = {"good": 1, "ni": 0, "poor": 0}
    for name, data in metrics.items():
        hist = data.get("histogram")
        if not hist:
            continue  # histogramが無い場合はスキップ
        overall = worst_case(overall, classify(name, hist))

    pct = {k: round(v * 100, 2) for k, v in overall.items()}

    slack_msg = (
        f"*Core Web Vitals – Daily Report*\n"
        f"`{ORIGIN}` (過去 28 日実測, CrUX)\n"
        f"• 良好 URL 比率 : {pct['good']} %\n"
        f"• 改善が必要    : {pct['ni']} %\n"
        f"• 不良          : {pct['poor']} %\n"
    )
    r = requests.post(WEBHOOK, json={"text": slack_msg})
    r.raise_for_status()

if __name__ == "__main__":
    main()
