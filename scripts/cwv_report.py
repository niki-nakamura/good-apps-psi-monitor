#!/usr/bin/env python3
"""
Daily CWV report – Mobile/PC 件数＋割合＋28日推移を Slack 投稿（画像付き）
"""

from __future__ import annotations
import os, sys, io, xml.etree.ElementTree as ET, requests
import matplotlib.pyplot as plt
from math import inf

# ─── 環境変数 ────────────────────────────────────────────
API      = "https://chromeuxreport.googleapis.com/v1"
KEY      = os.getenv("CRUX_API_KEY")
WEBHOOK  = os.getenv("SLACK_WEBHOOK_URL")

BOT      = os.getenv("SLACK_BOT_TOKEN")     # 追加
CHAN     = os.getenv("SLACK_CHANNEL_ID")    # 追加

ORIGIN   = os.getenv("ORIGIN")
SITEMAP  = os.getenv("SITEMAP_URL")
if not all((KEY, WEBHOOK, BOT, CHAN, ORIGIN, SITEMAP)):
    sys.exit("env vars missing")

# ─── Core Web Vitals しきい値 ───────────────────────────
THRESHOLDS = {
    "largest_contentful_paint":  [(0, 2500, "good"), (2500, 4000, "ni"), (4000, inf, "poor")],
    "interaction_to_next_paint": [(0, 200,  "good"), (200,  500,  "ni"), (500,  inf, "poor")],
    "cumulative_layout_shift":   [(0, 0.1,  "good"), (0.1, 0.25, "ni"), (0.25, inf, "poor")],
}
CORE_METRICS = list(THRESHOLDS)

# ─── 共通ユーティリティ ─────────────────────────────────
def classify(hist: list[dict], metric: str) -> dict:
    out = {"good": 0, "ni": 0, "poor": 0}
    for b in hist or []:
        start = float(b.get("start", 0)); dens = float(b.get("density", 0))
        for s, e, lbl in THRESHOLDS[metric]:
            if s <= start < e:
                out[lbl] += dens; break
    return out

def accumulate(series: list[dict], buckets_ts: list[dict], metric: str) -> None:
    for bucket in buckets_ts or []:
        start = float(bucket.get("start", 0))
        label = next(lbl for s, e, lbl in THRESHOLDS[metric] if s <= start < e)
        for i, dens in enumerate(bucket.get("densities", [])):
            try:
                series[i][label] += float(dens)
            except (TypeError, ValueError):
                pass

def worst(a, b):
    return {"good": min(a["good"], b["good"]),
            "ni":   max(a["ni"],   b["ni"]),
            "poor": max(a["poor"], b["poor"])}

def url_total() -> int:
    xml = requests.get(SITEMAP, timeout=30).text
    return sum(1 for n in ET.fromstring(xml).iter() if n.tag.endswith("loc"))

def get_all_urls_from_sitemap() -> list[str]:
    xml = requests.get(SITEMAP, timeout=30).text
    return [n.text for n in ET.fromstring(xml).iter() if n.tag.endswith("loc")]

def is_url_poor(url: str) -> bool:
    body = {"url": url}
    try:
        r = requests.post(f"{API}/records:queryRecord?key={KEY}", json=body, timeout=30)
        r.raise_for_status()
        metrics = r.json().get("record", {}).get("metrics", {})
        for m, v in metrics.items():
            if m in THRESHOLDS and v.get("histogram"):
                classified = classify(v["histogram"], m)
                if classified["poor"] > 0.0:
                    return True
        return False
    except Exception:
        return False

# ─── CrUX API 呼び出し ────────────────────────────────
def query_daily(ff: str):
    body = {"origin": ORIGIN, "formFactor": ff}
    r = requests.post(f"{API}/records:queryRecord?key={KEY}", json=body, timeout=30)
    r.raise_for_status()
    return r.json()["record"]["metrics"]

def query_history(ff: str, periods: int = 4):
    body = {"origin": ORIGIN, "formFactor": ff,
            "metrics": CORE_METRICS, "collectionPeriodCount": periods}
    r = requests.post(f"{API}/records:queryHistoryRecord?key={KEY}", json=body, timeout=30)
    r.raise_for_status()
    return r.json()["record"]["metrics"]

# ─── Slack 送信用ヘルパ ──────────────────────────────
HDR = {"Authorization": f"Bearer {BOT}"}  # Content-Type は requests に任せる

def send_text(webhook: str, text: str) -> None:
    r = requests.post(webhook, json={"text": text}, timeout=30)
    r.raise_for_status()

def upload_png(buf: io.BytesIO, title: str = "CWV Trend") -> None:
    size = buf.getbuffer().nbytes
    meta = {"filename": "cwv.png", "length": size, "title": title}
    # 1) 一時 URL を取得
    res = requests.post("https://slack.com/api/files.getUploadURLExternal",
                        headers=HDR, data=meta, timeout=30).json()
    if not res.get("ok"):
        raise RuntimeError("getUploadURLExternal failed: " + res.get("error", ""))
    upload_url, file_id = res["upload_url"], res["file_id"]

    # 2) PUT でアップロード
    requests.put(upload_url, data=buf.getvalue(),
                 headers={"Content-Type": "image/png"}, timeout=30).raise_for_status()

    # 3) 完了 & チャンネル共有
    comp = {
        "files": [{"id": file_id}],
        "channel_id": CHAN,
        "initial_comment": title
    }
    res2 = requests.post("https://slack.com/api/files.completeUploadExternal",
                         headers=HDR, json=comp, timeout=30).json()  # 修正: data=comp → json=comp
    if not res2.get("ok"):
        raise RuntimeError("completeUploadExternal failed: " + res2.get("error", ""))

# ─── 集計 & グラフ ───────────────────────────────────
total_urls = url_total()
forms      = {"PHONE": "Mobile", "DESKTOP": "PC"}
today, trend = {}, {}

# 不良URL抽出
all_urls = get_all_urls_from_sitemap()
poor_urls = []
for url in all_urls:
    if is_url_poor(url):
        poor_urls.append(url)

for ff, label in forms.items():
    # 日次値
    daily = query_daily(ff)
    overall = {"good": 1, "ni": 0, "poor": 0}
    for m, v in daily.items():
        if m in THRESHOLDS and v.get("histogram"):
            overall = worst(overall, classify(v["histogram"], m))
    pct = {k: round(v * 100, 2) for k, v in overall.items()}
    cnt = {k: int(round(v * total_urls)) for k, v in overall.items()}
    today[label] = (pct, cnt)

    # 過去 4 週
    hist = query_history(ff)
    series = [{"good": 0, "ni": 0, "poor": 0} for _ in range(4)]
    for m, v in hist.items():
        if m in THRESHOLDS:
            accumulate(series, v.get("histogramTimeseries", []), m)
    trend[label] = series

# グラフ描画
plt.figure(figsize=(6, 4))
for label, ts in trend.items():
    w = range(1, len(ts) + 1)
    plt.plot(w, [d["poor"]*100 for d in ts], "-o", label=f"{label} Poor")
    plt.plot(w, [d["ni"]*100   for d in ts], "-.", label=f"{label} NI")
    plt.plot(w, [d["good"]*100 for d in ts],      label=f"{label} Good")
plt.xlabel("Weeks (last 4)")
plt.ylabel("% Users")
plt.title("Core Web Vitals Trend (28-day windows)")
plt.grid(True, ls="--", alpha=.3); plt.legend()
buf = io.BytesIO(); plt.savefig(buf, format="png"); buf.seek(0)

# ─── Slack へ投稿 ───────────────────────────────────
lines = [f"*Core Web Vitals — Daily* `{ORIGIN}`",
         f"_Indexed URLs (est.)_: *{total_urls}*"]
for label, (p, c) in today.items():
    lines.append(f"*{label}* → 良好 {p['good']} % ({c['good']}件) | "
                 f"改善 {p['ni']} % ({c['ni']}件) | "
                 f"不良 {p['poor']} % ({c['poor']}件)")

# 不良URLリストを添付（件数も明示）
if poor_urls:
    lines.append(f"\n*Poor URLs* ({len(poor_urls)}件, 一部抜粋):")
    lines.extend(poor_urls[:20])  # 長すぎる場合は20件まで表示

# テキスト → Webhook
send_text(WEBHOOK, "\n".join(lines))

# 画像 → Bot API
upload_png(buf, "Core Web Vitals Trend (28-day windows)")

print("Slack posted OK.")
