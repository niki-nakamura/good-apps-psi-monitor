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
import matplotlib.pyplot as plt
from typing import Tuple
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

##### 環境変数 #####
CRUX_API_KEY  = os.getenv("CRUX_API_KEY")
ORIGIN_URL    = os.getenv("ORIGIN_URL", "https://good-apps.jp")
TOTAL_COUNT   = int(os.getenv("URL_TOTAL_COUNT", "0"))  # 0 なら割合のみ
SLACK_TOKEN   = os.getenv("SLACK_BOT_TOKEN")
SLACK_CH      = os.getenv("SLACK_CHANNEL_ID")
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL")  # トークンが無い場合のフォールバック
DATA_CSV      = pathlib.Path("data/cwv_history.csv")
CHART_FILE    = pathlib.Path("cwv_chart.png")

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
    plt.figure(figsize=(10, 6))
    x = pd.to_datetime(df["date"])
    for label, col in [("良好 (Mobile)", "mobile_good"),
                       ("改善 (Mobile)", "mobile_ni"),
                       ("不良 (Mobile)", "mobile_poor"),
                       ("良好 (Desktop)", "desktop_good"),
                       ("改善 (Desktop)", "desktop_ni"),
                       ("不良 (Desktop)", "desktop_poor")]:
        plt.plot(x, df[col], label=label, linewidth=2)
    plt.title("Core Web Vitals URL 状態 – 直近28日")
    plt.ylabel("URL 件数" if TOTAL_COUNT else "割合 (%)")
    plt.xlabel("Date")
    plt.xticks(rotation=45)
    plt.legend(ncol=2, fontsize=8)
    plt.tight_layout()
    plt.savefig(CHART_FILE)
    plt.close()

def post_slack(text: str, file_path: pathlib.Path):
    if SLACK_TOKEN:
        client = WebClient(token=SLACK_TOKEN)
        try:
            client.files_upload(
                channels=SLACK_CH,
                file=str(file_path),
                title="CWV Report",
                initial_comment=text
            )
        except SlackApiError as e:
            print(f"Slack API error: {e.response['error']}", file=sys.stderr)
            raise
    elif SLACK_WEBHOOK:
        # Webhook には画像をアップできないので QuickChart などに切り替え必要
        payload = {"text": text + "\n(画像アップロードには Bot Token が必要です)"}
        requests.post(SLACK_WEBHOOK, json=payload, timeout=10)
    else:
        print("No Slack credentials provided", file=sys.stderr)

def main():
    today = datetime.date.today().isoformat()
    # 1. データ取得
    mob_metrics = fetch_crux("PHONE")
    pc_metrics  = fetch_crux("DESKTOP")
    mob_pct = aggregate(mob_metrics)
    pc_pct  = aggregate(pc_metrics)

    mob_vals = to_counts(mob_pct)
    pc_vals  = to_counts(pc_pct)

    # 2. 履歴更新 & グラフ生成
    df = update_history(today, mob_vals, pc_vals)
    plot_chart(df)

    # 3. Slack へ投稿
    def fmt(vals):
        if TOTAL_COUNT:  # 件数表示
            return f"良好 {vals[0]} 件 / 改善 {vals[1]} 件 / 不良 {vals[2]} 件"
        return f"{vals[0]:.1f}% good, {vals[1]:.1f}% needs‑improve, {vals[2]:.1f}% poor"
    msg = (
        f"*Core Web Vitals – {today}*\n"
        f"• モバイル:  {fmt(mob_vals)}\n"
        f"• デスクトップ: {fmt(pc_vals)}"
    )
    post_slack(msg, CHART_FILE)

if __name__ == "__main__":
    main()
