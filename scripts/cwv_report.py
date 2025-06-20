#!/usr/bin/env python3
"""
Daily CWV report – Mobile/PC 別 件数＋割合＋28日推移グラフを Slack へ投稿
"""
from __future__ import annotations
import os, sys, io, math, xml.etree.ElementTree as ET, requests
import matplotlib.pyplot as plt

API = "https://chromeuxreport.googleapis.com/v1"
KEY = os.getenv("CRUX_API_KEY")
WEBHOOK = os.getenv("SLACK_WEBHOOK_URL")
ORIGIN = os.getenv("ORIGIN")
SITEMAP = os.getenv("SITEMAP_URL")
if not all((KEY, WEBHOOK, ORIGIN, SITEMAP)):
    sys.exit("env vars missing")

# --- ★ experimental_ 接頭辞は公式に廃止（2024-08-08）→ 削除 ★ -------------
THRESHOLDS = {
    "largest_contentful_paint":  [(0, 2500, "good"), (2500, 4000, "ni"), (4000, math.inf, "poor")],
    "interaction_to_next_paint": [(0, 200,  "good"), (200,  500,  "ni"), (500,  math.inf, "poor")],
    "cumulative_layout_shift":   [(0, 0.1,  "good"), (0.1, 0.25, "ni"), (0.25, math.inf, "poor")],
}
CORE_METRICS = list(THRESHOLDS)               # ← History API に送る正式 3 指標だけ

def classify(hist, metric):
    res = {"good": 0, "ni": 0, "poor": 0}
    for b in hist:
        start, dens = float(b.get("start", 0)), b["density"]
        for s, e, lbl in THRESHOLDS[metric]:
            if s <= start < e:
                res[lbl] += dens
                break
    return res

def worst(a, b):
    return {"good": min(a["good"], b["good"]),
            "ni":   max(a["ni"],   b["ni"]),
            "poor": max(a["poor"], b["poor"])}

def url_total():
    xml = requests.get(SITEMAP, timeout=30).text
    return len([n for n in ET.fromstring(xml).iter() if n.tag.endswith("loc")])

def query_daily(ff):
    body = {"origin": ORIGIN, "formFactor": ff}
    return requests.post(f"{API}/records:queryRecord?key={KEY}", json=body, timeout=30).json()["record"]["metrics"]

def query_history(ff, weeks=4):
    body = {"origin": ORIGIN, "formFactor": ff,
            "metrics": CORE_METRICS,            # ★ experimental を除外
            "collectionPeriodCount": weeks}
    r = requests.post(f"{API}/records:queryHistoryRecord?key={KEY}", json=body, timeout=30)
    r.raise_for_status()
    return r.json()["record"]["metrics"]

total_urls = url_total()
forms = {"PHONE": "Mobile", "DESKTOP": "PC"}
today, trend = {}, {}

for ff, label in forms.items():
    # -------- 最新 28 日 Good/NI/Poor 件数・割合 ----------
    daily = query_daily(ff)
    overall = {"good": 1, "ni": 0, "poor": 0}
    for m, v in daily.items():
        if m not in THRESHOLDS: continue
        if not v.get("histogram"): continue
        overall = worst(overall, classify(v["histogram"], m))
    pct = {k: round(v * 100, 2) for k, v in overall.items()}
    cnt = {k: int(round(v * total_urls)) for k, v in overall.items()}
    today[label] = (pct, cnt)

    # -------- 直近 4 collectionPeriods（= 28 日）推移 ----------
    hist = query_history(ff, weeks=4)
    series = [{"good": 0, "ni": 0, "poor": 0} for _ in range(4)]
    for m, v in hist.items():
        if m not in THRESHOLDS: continue
        for i, buckets in enumerate(v.get("histogramTimeseries", [])):
            series[i] = worst(series[i], classify(buckets, m))
    trend[label] = series

# -------- グラフ PNG ----------
plt.figure(figsize=(6,4))
for label, ts in trend.items():
    x = range(1, len(ts)+1)
    plt.plot(x, [d["poor"]*100 for d in ts], "-o", label=f"{label} Poor")
    plt.plot(x, [d["ni"]*100   for d in ts], "-.",label=f"{label} NI")
    plt.plot(x, [d["good"]*100 for d in ts], label=f"{label} Good")
plt.xlabel("Weeks (last 4)"); plt.ylabel("% Users"); plt.title("Core Web Vitals Trend")
plt.grid(True, ls="--", alpha=.3); plt.legend()
buf = io.BytesIO(); plt.savefig(buf, format="png"); buf.seek(0)

# -------- Slack 投稿 ----------
lines = [f"*Core Web Vitals — Daily*  `{ORIGIN}`", f"_Indexed URLs (est.)_: *{total_urls}*"]
for label,(p,c) in today.items():
    lines.append(f"*{label}* ➜ 良好 {p['good']} % ({c['good']}件) | 改善 {p['ni']} % ({c['ni']}件) | 不良 {p['poor']} % ({c['poor']}件)")
requests.post(WEBHOOK, json={"text":"\n".join(lines)}, files={"file":("cwv.png",buf,"image/png")}, timeout=30).raise_for_status()
print("Slack posted OK.")
