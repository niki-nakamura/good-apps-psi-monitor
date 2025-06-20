#!/usr/bin/env python3
"""
Daily CWV report (Good/NI/Poor 件数＋割合) モバイル/PC別 & 28日推移グラフ
環境変数:
  CRUX_API_KEY, SLACK_WEBHOOK_URL, ORIGIN, SITEMAP_URL
"""
from __future__ import annotations
import os, sys, time, xml.etree.ElementTree as ET, requests, io, math
import matplotlib.pyplot as plt

API      = "https://chromeuxreport.googleapis.com/v1"
KEY      = os.getenv("CRUX_API_KEY")
WEBHOOK  = os.getenv("SLACK_WEBHOOK_URL")
ORIGIN   = os.getenv("ORIGIN")
SITE_MAP = os.getenv("SITEMAP_URL")

if not all((KEY, WEBHOOK, ORIGIN, SITE_MAP)):
    sys.exit("env vars missing")

THRESHOLDS = {                         # CrUX = GSC 公式閾値:contentReference[oaicite:7]{index=7}
    "largest_contentful_paint":   [(0, 2500, "good"), (2500, 4000, "ni"), (4000, math.inf, "poor")],
    "interaction_to_next_paint":  [(0, 200,  "good"), (200,  500,  "ni"), (500,  math.inf, "poor")],
    "experimental_interaction_to_next_paint": [(0,200,"good"),(200,500,"ni"),(500, math.inf,"poor")],
    "cumulative_layout_shift":    [(0, 0.1, "good"), (0.1, 0.25, "ni"), (0.25, math.inf, "poor")],
}

def classify(hist, metric):
    cat = {"good":0,"ni":0,"poor":0}
    for b in hist:
        start,dens = float(b.get("start",0)), b["density"]
        for s,e,l in THRESHOLDS[metric]:
            if s <= start < e:
                cat[l]+=dens
                break
    return cat

def worst(a,b):
    return {"good":min(a["good"],b["good"]),
            "ni":  max(a["ni"],  b["ni"]),
            "poor":max(a["poor"],b["poor"])}

def url_count():
    xml = requests.get(SITE_MAP, timeout=30).text
    return len([_ for _ in ET.fromstring(xml).iter() if _.tag.endswith("loc")])

def query_record(ff):
    r = requests.post(f"{API}/records:queryRecord?key={KEY}",
                      json={"origin":ORIGIN,"formFactor":ff},timeout=30)
    r.raise_for_status()
    return r.json()["record"]["metrics"]

def query_history(ff):
    r = requests.post(f"{API}/records:queryHistoryRecord?key={KEY}",
                      json={"origin":ORIGIN,"formFactor":ff,"metrics":list(THRESHOLDS)},
                      timeout=30)
    r.raise_for_status()
    return r.json()["record"]["metrics"]

total_urls = url_count()              # --- 件数推定母数
forms = {"PHONE":"Mobile","DESKTOP":"PC"}
today_stats, history = {}, {}

for ff,label in forms.items():
    # ========== 最新28日 Good/NI/Poor ==========
    metrics = query_record(ff)
    overall = {"good":1,"ni":0,"poor":0}
    for m,v in metrics.items():
        if m not in THRESHOLDS: continue
        hist = v.get("histogram");  # ← histogram が無い metric は無視
        if not hist: continue
        overall = worst(overall, classify(hist,m))
    pct = {k:round(v*100,2) for k,v in overall.items()}
    cnt = {k:int(round(v*total_urls)) for k,v in overall.items()}
    today_stats[label] = (pct,cnt)

    # ========== 過去推移 ==========
    hist_metrics = query_history(ff)
    weeks = max(len(v.get("histogramTimeseries",[])) for v in hist_metrics.values())
    ts = [{"good":0,"ni":0,"poor":0} for _ in range(weeks)]
    for m,v in hist_metrics.items():
        if m not in THRESHOLDS: continue
        series = v.get("histogramTimeseries",[])
        for i,buckets in enumerate(series):
            ts[i] = worst(ts[i], classify(buckets,m))
    history[label] = ts[-4:]  # 直近4週 = 28日

# ========== グラフ描画 ==========
plt.figure(figsize=(6,4))
for label,ts in history.items():
    weeks = range(1,len(ts)+1)
    plt.plot(weeks,[d["poor"]*100 for d in ts], "-o", label=f"{label} Poor")
    plt.plot(weeks,[d["ni"]*100   for d in ts], "-.",label=f"{label} NI")
    plt.plot(weeks,[d["good"]*100 for d in ts], label=f"{label} Good")
plt.xlabel("Weeks (Last 4)")
plt.ylabel("% Users")
plt.title("Core Web Vitals 28-day trend")
plt.grid(True, linestyle="--", alpha=.3)
plt.legend()
buf = io.BytesIO(); plt.savefig(buf, format="png"); buf.seek(0)

# ========== Slack 投稿 ==========
lines=["*Core Web Vitals – Daily (`{}`)*".format(ORIGIN),
       f"_Total indexed est._: *{total_urls} URLs*"]
for label,(pct,cnt) in today_stats.items():
    lines.append(
      f"*{label}* → 良好 {pct['good']} % ({cnt['good']}件) | "
      f"改善 {pct['ni']} % ({cnt['ni']}件) | "
      f"不良 {pct['poor']} % ({cnt['poor']}件)"
    )
resp = requests.post(
    WEBHOOK,
    json={"text":"\n".join(lines)},
    files={"file":("cwv.png",buf,"image/png")},
    timeout=30
)
resp.raise_for_status()
print("Slack posted.")
