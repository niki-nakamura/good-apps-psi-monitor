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
import time
import logging

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
    data = res.json().get("record", {}).get("metrics", {})
    logging.info(f"CrUX API response for {form_factor}: {json.dumps(data, indent=2)}")
    return data

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

def aggregate_probabilistic_strict(metrics: dict) -> Tuple[float, float, float]:
    """
    3指標のヒストグラムから
      * 良好% = Π(good_i)
      * 不良% = 1 - Π(1 - poor_i)
      * 改善% = 100 - 良好% - 不良%
    を求める（通常のCore Web Vitals指標:LCP>4.0, INP>0.5, CLS>0.25）
    """
    goods = []
    poors = []
    for key in ("largest_contentful_paint", "interaction_to_next_paint", "cumulative_layout_shift"):
        metric = metrics.get(key, {})
        bins = metric.get("histogram", [])
        # good: 公式good bin
        g = bins[0]["density"] if len(bins) > 0 else 0
        goods.append(g)
        # poor: 公式poor bin
        p = bins[2]["density"] if len(bins) > 2 else 0
        poors.append(p)
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

def psi_field_status(url, key, strategy):
    """PSI API で指定 strategy の FieldData を取得し GSC 方式で判定。FieldData 無ければ 'nodata'"""
    psi = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    params = {
        "url": url,
        "key": key,
        "category": "performance",
        "strategy": strategy,
        "originFallback": "true"
    }
    try:
        data = requests.get(psi, params=params, timeout=30).json()
        if "loadingExperience" not in data or "metrics" not in data["loadingExperience"]:
            return "nodata"
        fd = data["loadingExperience"]["metrics"]
        lcp = fd["LARGEST_CONTENTFUL_PAINT_MS"]["percentile"] / 1000 if "LARGEST_CONTENTFUL_PAINT_MS" in fd else None
        cls = fd["CUMULATIVE_LAYOUT_SHIFT_SCORE"]["percentile"] / 100 if "CUMULATIVE_LAYOUT_SHIFT_SCORE" in fd else None
        inp = fd["INP"]["percentile"] / 1000 if "INP" in fd else None
        if lcp is None or cls is None or inp is None:
            return "nodata"
        status = "good"
        if lcp > 4 or inp > 0.5 or cls > 0.25:
            status = "poor"
        elif lcp > 2.5 or inp > 0.2 or cls > 0.1:
            status = "ni"
        return status
    except Exception as e:
        logging.warning(f"PSI error for {url} ({strategy}): {e}")
        return "nodata"

def crux_page_status(url, key, strategy):
    api = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"
    payload = {
        "url": url,
        "formFactor": "PHONE" if strategy == "mobile" else "DESKTOP"
    }
    try:
        r = requests.post(f"{api}?key={key}", json=payload, timeout=30)
        r.raise_for_status()
        metrics = r.json().get("record", {}).get("metrics", {})
        def get_p75(metric):
            return metric.get("percentiles", {}).get("p75")
        lcp = get_p75(metrics.get("largest_contentful_paint", {}))
        inp = get_p75(metrics.get("interaction_to_next_paint", {}))
        cls = get_p75(metrics.get("cumulative_layout_shift", {}))
        if lcp is None or inp is None or cls is None:
            return "nodata"
        lcp = lcp / 1000
        inp = inp / 1000
        cls = cls / 100
        status = "good"
        if lcp > 4 or inp > 0.5 or cls > 0.25:
            status = "poor"
        elif lcp > 2.5 or inp > 0.2 or cls > 0.1:
            status = "ni"
        return status
    except Exception as e:
        logging.warning(f"CrUX page error for {url} ({strategy}): {e}")
        return "nodata"

def ensure_dummy_png(path="empty.png"):
    if not pathlib.Path(path).exists():
        import matplotlib.pyplot as plt
        plt.figure(figsize=(1,1)); plt.axis("off"); plt.savefig(path); plt.close()

# --- Slack送信（旧API） ---
def post_slack(text: str, file_path: pathlib.Path):
    if SLACK_TOKEN:
        client = WebClient(token=SLACK_TOKEN)
        try:
            client.files_upload(
                channels=SLACK_CH,
                file=str(file_path),
                title="CWV Report",
                initial_comment=text,
            )
        except SlackApiError as e:
            print(f"Slack API error: {e.response['error']}", file=sys.stderr)
    elif SLACK_WEBHOOK:
        payload = {"text": text + "\n(画像アップロードには Bot Token が必要です)"}
        requests.post(SLACK_WEBHOOK, json=payload, timeout=10)

def main():
    today = datetime.date.today().isoformat()
    mob_metrics = fetch_crux("PHONE")
    pc_metrics  = fetch_crux("DESKTOP")

    logging.info("Using strict thresholds for aggregation.")
    mob_pct = aggregate_probabilistic_strict(mob_metrics)
    pc_pct  = aggregate_probabilistic_strict(pc_metrics)

    logging.debug(f"Mobile strict percentages: {mob_pct}")
    logging.debug(f"Desktop strict percentages: {pc_pct}")

    mob_vals = to_counts(mob_pct)
    pc_vals  = to_counts(pc_pct)
    mob_good, mob_ni, mob_poor = mob_pct
    pc_good, pc_ni, pc_poor = pc_pct

    msg = (
        f"*Core Web Vitals – {today}*\n"
        f"• モバイル:  良好 {mob_good:.1f}% / 改善 {mob_ni:.1f}% / 不良 {mob_poor:.1f}%\n"
        f"• デスクトップ: 良好 {pc_good:.1f}% / 改善 {pc_ni:.1f}% / 不良 {pc_poor:.1f}%\n"
        "CWV Report"
    )

    logging.info(f"Generated Slack message: {msg}")

    plot_chart(update_history(today, mob_vals, pc_vals))
    post_slack(msg, CHART_FILE)

if __name__ == "__main__":
    main()
