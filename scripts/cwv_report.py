#!/usr/bin/env python3
"""
毎朝 GitHub Actions から呼ばれる Core Web Vitals レポート生成スクリプト
  * CrUX API でモバイル / デスクトップの LCP・INP・CLS ヒストグラムを取得
  * 良好 / 改善 / 不良 の割合を計算し、URL 件数に換算 (任意)
  * 28 日間履歴を CSV に保存し、折れ線グラフ (PNG) を生成
  * Slack Bot へ画像＋テキスト投稿
"""

import os, sys, json, datetime, pathlib, requests
import pandas as pd
import matplotlib
matplotlib.rcParams["font.family"] = "Noto Sans CJK JP"  # 日本語フォント指定
import matplotlib.pyplot as plt
from typing import Tuple
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import xml.etree.ElementTree as ET
import urllib.parse
import time
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

##### 環境変数 #####
CRUX_API_KEY  = os.getenv("CRUX_API_KEY")
ORIGIN_URL    = os.getenv("ORIGIN_URL", "https://good-apps.jp")
TOTAL_COUNT   = int(os.getenv("URL_TOTAL_COUNT", "0"))  # 0 なら割合のみ
SLACK_TOKEN   = os.getenv("SLACK_BOT_TOKEN")
SLACK_CH      = os.getenv("SLACK_CHANNEL_ID")
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL")

DATA_CSV      = pathlib.Path("data/cwv_history.csv")    # ← 先に宣言
CHART_FILE    = pathlib.Path("cwv_chart.png")

# --- 出力用ディレクトリを確実に作成 (定義後なので OK) ---
DATA_CSV.parent.mkdir(parents=True, exist_ok=True)

# ❶ 分母を動的に更新 -------------------------------------
def auto_total(df, mob_vals, pc_vals):
    """履歴と今日の値から最大総数を推定し環境変数に保存"""
    global TOTAL_COUNT
    today_total = max(sum(mob_vals), sum(pc_vals))
    if TOTAL_COUNT == 0:
        TOTAL_COUNT = max(today_total, df[["mobile_good","mobile_ni","mobile_poor",
                                           "desktop_good","desktop_ni","desktop_poor"]].sum(axis=1).max() if not df.empty else 0)
        TOTAL_COUNT = max(TOTAL_COUNT, today_total)
    elif TOTAL_COUNT < today_total:
        TOTAL_COUNT = today_total
    return TOTAL_COUNT

if not CRUX_API_KEY:
    print("ERROR: CRUX_API_KEY not set", file=sys.stderr)
    sys.exit(1)

def fetch_crux(form_factor: str) -> dict:
    """CrUX API から指定デバイスのレコードを取得"""
    url = f"https://chromeuxreport.googleapis.com/v1/records:queryRecord?key={CRUX_API_KEY}"
    payload = {
        "origin": ORIGIN_URL,
        "formFactor": form_factor,
        "metrics": [
            "largest_contentful_paint",
            "interaction_to_next_paint",
            "cumulative_layout_shift"
        ]
    }
    res = requests.post(url, json=payload, timeout=30)
    res.raise_for_status()
    return res.json().get("record", {}).get("metrics", {})

def parse_histogram(metric: dict) -> Tuple[float, float, float]:
    """
    CrUX ヒストグラム (3bin) から良好 / 改善 / 不良 (%) を返す
    bins: list[{'start':..., 'end':..., 'density':...}]
    順序: good, ni, poor で返ってくる想定
    """
    bins = metric.get("histogram", [])
    # density は 0〜1 の比率
    good = bins[0]["density"] * 100 if len(bins) > 0 else 0
    ni   = bins[1]["density"] * 100 if len(bins) > 1 else 0
    poor = bins[2]["density"] * 100 if len(bins) > 2 else 0
    return good, ni, poor

def aggregate(metrics: dict) -> Tuple[float, float, float]:
    """
    3指標のヒストグラムから
      * 良好% = min(LCP_good, INP_good, CLS_good)
      * 不良% = max(LCP_poor, INP_poor, CLS_poor)
      * 改善% = 100 - 良好% - 不良%
    を求める
    """
    g_list, p_list = [], []
    for key in ("largest_contentful_paint", "interaction_to_next_paint", "cumulative_layout_shift"):
        g, ni, p = parse_histogram(metrics.get(key, {}))
        g_list.append(g); p_list.append(p)
    good  = min(g_list)
    poor  = max(p_list)
    ni    = max(0.0, 100.0 - good - poor)  # 誤差吸収
    return round(good, 2), round(ni, 2), round(poor, 2)

def aggregate_probabilistic(metrics: dict) -> Tuple[float, float, float]:
    """
    3指標のヒストグラムから
      * 良好% = Π(good_i)
      * 不良% = 1 - Π(1 - poor_i)
      * 改善% = 100 - 良好% - 不良%
    を求める（確率論的近似）
    """
    goods = []
    poors = []
    for key in ("largest_contentful_paint", "interaction_to_next_paint", "cumulative_layout_shift"):
        g, ni, p = parse_histogram(metrics.get(key, {}))
        goods.append(g/100)
        poors.append(p/100)
    good = 1
    for g in goods:
        good *= g
    not_poor = 1
    for p in poors:
        not_poor *= (1 - p)
    poor = 1 - not_poor
    ni = max(0.0, 1.0 - good - poor)
    return round(good*100, 2), round(ni*100, 2), round(poor*100, 2)

def to_counts(percentages):
    if TOTAL_COUNT == 0:
        return percentages
    good, ni, poor = percentages
    return (round(good * TOTAL_COUNT / 100),
            round(ni   * TOTAL_COUNT / 100),
            round(poor * TOTAL_COUNT / 100))

def update_history(date_str, mob_vals, pc_vals):
    cols = ["date",
            "mobile_good","mobile_ni","mobile_poor",
            "desktop_good","desktop_ni","desktop_poor"]
    if DATA_CSV.exists():
        df = pd.read_csv(DATA_CSV)
    else:
        df = pd.DataFrame(columns=cols)
    new_row = [date_str, *mob_vals, *pc_vals]
    df = df[df["date"] != date_str]   # 同日重複を防止
    df.loc[len(df)] = new_row
    df = df.sort_values("date").tail(28)  # 28 日分だけ保持
    df.to_csv(DATA_CSV, index=False)
    return df

def plot_chart(df: pd.DataFrame):
    if df.empty:
        return  # データが無い場合はスキップ

    # --- 数値型に強制変換（object → float/Int） ---
    numeric_cols = df.columns.drop("date")
    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")

    x = pd.to_datetime(df["date"])
    plt.figure(figsize=(10, 6), dpi=150)          # ← 解像度も上げる
    styles = {"mobile_good":"o-", "mobile_ni":"^-", "mobile_poor":"s-",
              "desktop_good":"o--", "desktop_ni":"^--", "desktop_poor":"s--"}

    for col, style in styles.items():
        if df[col].notna().any():                 # 全て NaN の列は描かない
            plt.plot(x, df[col], style, linewidth=2, markersize=4, label=col.replace("_", " ").title())

    if len(df) == 1:                              # 1日分しか無い場合は散布図でも描く
        for col in numeric_cols:
            plt.scatter(x, df[col], s=40)

    plt.title("Core Web Vitals URL 状態 – 直近28日")
    plt.ylabel("URL 件数" if TOTAL_COUNT else "割合 (%)")
    plt.xlabel("Date")
    plt.xticks(rotation=45)
    plt.legend(fontsize=8, ncol=2)
    plt.tight_layout()
    plt.savefig(CHART_FILE)
    plt.close()

def post_slack(text: str, file_path: pathlib.Path):
    if SLACK_TOKEN:
        client = WebClient(token=SLACK_TOKEN)
        try:
            # files_upload_v2: channel_idはリストでIDを渡す
            client.files_upload_v2(
                channel_id=[SLACK_CH],
                initial_comment=text,
                file=str(file_path),
                title="CWV Report"
            )
        except SlackApiError as e:
            print(f"Slack API error: {e.response['error']}", file=sys.stderr)
            # Webhook fallback
            if SLACK_WEBHOOK:
                payload = {"text": text + "\n(画像アップロードには Bot Token が必要です)"}
                requests.post(SLACK_WEBHOOK, json=payload, timeout=10)
            else:
                raise
    elif SLACK_WEBHOOK:
        payload = {"text": text + "\n(画像アップロードには Bot Token が必要です)"}
        requests.post(SLACK_WEBHOOK, json=payload, timeout=10)
    else:
        print("No Slack credentials provided", file=sys.stderr)

def fetch_robot_sitemaps(origin):
    try:
        r = requests.get(urllib.parse.urljoin(origin, "/robots.txt"), timeout=10)
        r.raise_for_status()
        return [line.split(":",1)[1].strip()
                for line in r.text.splitlines()
                if line.lower().startswith("sitemap:")]
    except Exception:
        return []

# --- robust sitemap collector ---
def collect_all_sitemaps(origin: str, limit=4000):
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=Retry(
        total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])))
    def recurse(sitemap_url: str, seen: set) -> list:
        if sitemap_url in seen:
            return []
        seen.add(sitemap_url)
        try:
            r = session.get(sitemap_url, timeout=(10,30), allow_redirects=True)
            if r.status_code != 200 or "xml" not in r.headers.get("Content-Type",""):
                logging.warning("[sitemap skip] %s %s", sitemap_url, r.status_code)
                return []
            try:
                root = ET.fromstring(r.text)
            except ET.ParseError as e:
                logging.warning("[sitemap parse error] %s %s", sitemap_url, e)
                return []
        except Exception as e:
            logging.warning("[sitemap error] %s %s", sitemap_url, e)
            return []
        out = []
        if root.tag.endswith("sitemapindex"):
            for loc in root.iter("{*}loc"):
                out += recurse(loc.text.strip(), seen)
        else:
            for loc in root.iter("{*}loc"):
                u = loc.text.strip()
                ul = u.lower()
                if ul.endswith(".xml") or ".xml?" in ul:
                    continue
                out.append(u)
            if not out:
                logging.warning("empty sitemap %s", sitemap_url)
                return []
        return out
    # robots.txt からも探索
    robots_sitemaps = fetch_robot_sitemaps(origin)
    cand_index = ["/sitemap_index.xml", "/wp-sitemap.xml", "/sitemap.xml"]
    tried = set()
    urls = []
    for p in cand_index:
        idx = urllib.parse.urljoin(origin, p)
        if idx in tried:
            continue
        tried.add(idx)
        urls = recurse(idx, set())
        if urls:
            break
    # robots.txt のSitemapも追加
    for sm in robots_sitemaps:
        if sm not in tried:
            urls += recurse(sm, set())
    return urls[:limit]

def is_url_poor(url, form_factor):
    """CrUXページAPIで指定URLが不良か判定（form_factor=PHONE/DESKTOP）"""
    api = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"
    payload = {"url": url, "formFactor": form_factor}
    try:
        r = requests.post(f"{api}?key={CRUX_API_KEY}", json=payload, timeout=20)
        r.raise_for_status()
        metrics = r.json().get("record", {}).get("metrics", {})
        for m in ("largest_contentful_paint", "interaction_to_next_paint", "cumulative_layout_shift"):
            v = metrics.get(m, {})
            if v.get("histogram"):
                bins = v["histogram"]
                poor = bins[2]["density"] if len(bins) > 2 else 0
                if poor > 0.0:
                    return True
        return False
    except Exception as e:
        print(f"crux page error: {url} {e}", file=sys.stderr)
        return False

session = requests.Session()
session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])))

def main():
    today = datetime.date.today().isoformat()
    # 1. CrUX API からモバイル/デスクトップのヒストグラムを取得
    mob_metrics = fetch_crux("PHONE")
    pc_metrics  = fetch_crux("DESKTOP")
    mob_pct = aggregate(mob_metrics)
    pc_pct  = aggregate(pc_metrics)

    # 2. 件数換算（URL_TOTAL_COUNTが0なら割合のみ）
    mob_vals = to_counts(mob_pct)
    pc_vals  = to_counts(pc_pct)

    # 3. 不良URLリスト抽出（mobile/desktopいずれかpoor, 最大50件）
    urls = collect_all_sitemaps(ORIGIN_URL)
    if not urls:
        logging.warning("[sitemap skip] 取得 URL が 0 件。サイトマップ設定を確認してください。")
        post_slack("[CWV] サイトマップからURLを取得できませんでした。", CHART_FILE)
        return
    poor_urls = []
    for u in urls:
        try:
            mob_poor = is_url_poor(u, "PHONE")
            pc_poor  = is_url_poor(u, "DESKTOP")
            if mob_poor or pc_poor:
                logging.info("Poor URL detected: %s", u)
                poor_urls.append(u)
                if len(poor_urls) >= 50:
                    break
        except Exception as e:
            logging.warning("[poor check error] %s %s", u, e)
            continue

    # 4. 履歴更新 & グラフ生成
    df = update_history(today, mob_vals, pc_vals)
    plot_chart(df)

    # 5. Slack へ投稿
    def fmt(vals):
        if TOTAL_COUNT:
            return f"良好 {vals[0]} 件 / 改善 {vals[1]} 件 / 不良 {vals[2]} 件"
        return f"良好 {vals[0]:.1f}% / 改善 {vals[1]:.1f}% / 不良 {vals[2]:.1f}%"
    msg = (
        f"*Core Web Vitals – {today}*\n"
        f"• モバイル:  {fmt(mob_vals)}\n"
        f"• デスクトップ: {fmt(pc_vals)}"
    )
    if poor_urls:
        msg += f"\n\n*不良URL一覧 (最大50件)*:\n" + "\n".join(poor_urls[:50])
        if len(poor_urls) > 50:
            msg += f"\n…他 {len(poor_urls)-50} 件"
    post_slack(msg, CHART_FILE)

if __name__ == "__main__":
    main()
