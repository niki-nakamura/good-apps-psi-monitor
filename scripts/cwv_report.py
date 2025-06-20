#!/usr/bin/env python3
"""
Daily CWV report – Mobile/PC 件数＋割合＋28日推移を Slack 投稿
"""
from __future__ import annotations
import os, sys, io, math, xml.etree.ElementTree as ET, requests, math
import matplotlib.pyplot as plt
from math import inf

# ─── 環境変数 ───────────────────────────────────────────────────────────
API      = "https://chromeuxreport.googleapis.com/v1"
KEY      = os.getenv("CRUX_API_KEY")
WEBHOOK  = os.getenv("SLACK_WEBHOOK_URL")
ORIGIN   = os.getenv("ORIGIN")
SITEMAP  = os.getenv("SITEMAP_URL")
if not all((KEY, WEBHOOK, ORIGIN, SITEMAP)):
    sys.exit("env vars missing")

# ─── Core Web Vitals 公式しきい値【web.dev】 ──────────────────────────
THRESHOLDS = {
    "largest_contentful_paint":  [(0, 2500, "good"), (2500, 4000, "ni"), (4000, inf, "poor")],   #:contentReference[oaicite:8]{index=8}
    "interaction_to_next_paint": [(0, 200,  "good"), (200,  500,  "ni"), (500,  inf, "poor")],   #:contentReference[oaicite:9]{index=9}
    "cumulative_layout_shift":   [(0, 0.1,  "good"), (0.1, 0.25, "ni"), (0.25, inf, "poor")],    #:contentReference[oaicite:10]{index=10}
}
CORE_METRICS = list(THRESHOLDS)   # History API へ渡す 3 指標

# ─── 共通ユーティリティ ──────────────────────────────────────────────
def classify(hist: list[dict], metric: str) -> dict:
    """daily API 用: histogram -> good/ni/poor 割合"""
    out = {"good": 0, "ni": 0, "poor": 0}
    for b in hist:
        if not isinstance(b, dict):                    # 型ガード
            continue
        start = float(b.get("start", 0))
        dens  = float(b.get("density", 0))
        for s, e, lbl in THRESHOLDS[metric]:
            if s <= start < e:
                out[lbl] += dens
                break
    return out

def accumulate_timeseries(series: list[dict], buckets_ts: list[dict], metric: str) -> None:
    """History API 用: バケット×densities 配列を週方向へ加算"""
    for bucket in buckets_ts:
        if not isinstance(bucket, dict):
            continue
        start = float(bucket.get("start", 0))
        label = next(lbl for s, e, lbl in THRESHOLDS[metric] if s <= start < e)
        for i, dens in enumerate(bucket.get("densities", [])):
            try:
                val = float(dens)
            except (TypeError, ValueError):
                continue          # None や "NaN"
            series[i][label] += val

def worst(a, b):
    return {"good": min(a["good"], b["good"]),
            "ni":   max(a["ni"],   b["ni"]),
            "poor": max(a["poor"], b["poor"])}

def url_total() -> int:
    xml = requests.get(SITEMAP, timeout=30).text
    return len([n for n in ET.fromstring(xml).iter() if n.tag.endswith("loc")])

# ─── API 呼び出し ─────────────────────────────────────────────────────
def query_daily(ff: str):
    body = {"origin": ORIGIN, "formFactor": ff}
    return requests.post(f"{API}/records:queryRecord?key={KEY}", json=body, timeout=30).json()["record"]["metrics"]

def query_history(ff: str, periods: int = 4):
    body = {"origin": ORIGIN, "formFactor": ff, "metrics": CORE_METRICS, "collectionPeriodCount": periods}
    r = requests.post(f"{API}/records:queryHistoryRecord?key={KEY}", json=body, timeout=30)
    r.raise_for_status()
    return r.json()["record"]["metrics"]

# ─── 集計 ─────────────────────────────────────────────────────────────
total_urls = url_total()
forms      = {"PHONE": "Mobile", "DESKTOP": "PC"}
today, trend = {}, {}

for ff, label in forms.items():
    # 日次値
    daily = query_daily(ff)
    overall = {"good": 1, "ni": 0, "poor": 0}
    for m, v in daily.items():
        if m not in THRESHOLDS or not v.get("histogram"):
            continue
        overall = worst(overall, classify(v["histogram"], m))
    pct = {k: round(v * 100, 2) for k, v in overall.items()}
    cnt = {k: int(round(v * total_urls)) for k, v in overall.items()}
    today[label] = (pct, cnt)

    # 過去 4 週 (28 日)
    hist = query_history(ff, periods=4)
    series = [{"good": 0, "ni": 0, "poor": 0} for _ in range(4)]
    for m, v in hist.items():
        if m not in THRESHOLDS:
            continue
        accumulate_timeseries(series, v.get("histogramTimeseries", []), m)   # ← 修正点
    trend[label] = series

# ─── グラフ作成 ──────────────────────────────────────────────────────
plt.figure(figsize=(6, 4))
for label, ts in trend.items():
    w = range(1, len(ts)+1)
    plt.plot(w, [d["poor"]*100 for d in ts], "-o", label=f"{label} Poor")
    plt.plot(w, [d["ni"]*100   for d in ts], "-.", label=f"{label} NI")
    plt.plot(w, [d["good"]*100 for d in ts],      label=f"{label} Good")
plt.xlabel("Weeks (last 4)")
plt.ylabel("% Users")
plt.title("Core Web Vitals Trend (28-day windows)")
plt.grid(True, ls="--", alpha=.3)
plt.legend()
buf = io.BytesIO(); plt.savefig(buf, format="png"); buf.seek(0)

# ─── Slack へ投稿 ───────────────────────────────────────────────────
lines = [f"*Core Web Vitals — Daily* `{ORIGIN}`", f"_Indexed URLs (est.)_: *{total_urls}*"]
for label, (p, c) in today.items():
    lines.append(f"*{label}* → 良好 {p['good']} % ({c['good']}件) | 改善 {p['ni']} % ({c['ni']}件) | 不良 {p['poor']} % ({c['poor']}件)")
requests.post(WEBHOOK, json={"text": "\n".join(lines)}, files={"file": ("cwv.png", buf, "image/png")}, timeout=30).raise_for_status()
print("Slack posted OK.")
