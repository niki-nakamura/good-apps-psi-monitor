import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from xml.etree import ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

# 環境変数からAPIキーとSlack Webhook URLを取得
PSI_API_KEY = os.environ.get("PSI_API_KEY")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

# 対象サイトの起点URL
START_URL = "https://good-apps.jp/"

def normalize_url(url: str) -> str:
    """
    指定したURLを正規化して、対象ドメイン内のURLのみを返す。
    - スキームやホスト名を補完・統一し、フラグメントやクエリを除去。
    - good-apps.jpドメイン外のリンクは除外。
    """
    parsed = urlparse(url)
    scheme = parsed.scheme
    netloc = parsed.netloc
    path = parsed.path

    # スキームが無い場合はHTTPSを仮定
    if scheme == "":
        scheme = "https"
    # HTTPはHTTPSに統一
    if scheme == "http":
        scheme = "https"
    # 許可するドメイン（good-apps.jp 本ドメインのみ）
    allowed_domains = ["good-apps.jp", "www.good-apps.jp"]
    if netloc == "":
        # 相対URLは除外（事前にurljoinで絶対URL化する想定）
        return None
    if netloc not in allowed_domains:
        return None

    # フラグメントとクエリを除去してURL再構築
    parsed = parsed._replace(scheme=scheme, netloc=netloc, fragment="", query="", params="")
    url_clean = parsed.geturl()
    # 末尾のスラッシュを統一処理（ルート以外のURLは末尾の「/」を削除）
    if url_clean.endswith("/") and parsed.path not in ["", "/"]:
        url_clean = url_clean[:-1]
    # パスが空（ドメイン直下）の場合は "/" を付加
    if urlparse(url_clean).path == "":
        url_clean = url_clean + "/"
    return url_clean

def gather_all_urls() -> tuple[set, dict]:
    """
    サイト内の全ページURLを取得する。まずサイトマップを試み、無い場合はクローリング。
    戻り値: (URLセット, タイトル辞書)
      - URLセット: 発見した全URLの集合
      - タイトル辞書: URLをキー、ページタイトルを値とする辞書（Slack出力用）
    """
    urls = set()
    titles = {}

    # 1. サイトマップの取得と解析
    sitemap_urls = [
        "https://good-apps.jp/sitemap.xml",
        "https://good-apps.jp/sitemap_index.xml",
        "https://good-apps.jp/wp-sitemap.xml"
    ]
    sitemap_found = False
    for sitemap_url in sitemap_urls:
        try:
            resp = requests.get(sitemap_url, timeout=10)
        except Exception:
            continue
        if resp.status_code == 200 and resp.content:
            content = resp.content
            # サイトマップXMLかどうか簡易チェック
            if b"<urlset" in content or b"<sitemapindex" in content:
                sitemap_found = True
                try:
                    root = ET.fromstring(content)
                except ET.ParseError:
                    continue
                # XML名前空間を考慮してタグ名を取得
                root_tag = root.tag.split("}", 1)[-1]  # namespaceを除去
                if root_tag == "urlset":
                    # 単一サイトマップ（直接URLリスト）
                    for url_elem in root.findall(".//{*}loc"):
                        if url_elem.text:
                            norm = normalize_url(url_elem.text.strip())
                            if norm:
                                urls.add(norm)
                elif root_tag == "sitemapindex":
                    # サイトマップインデックス（子サイトマップを辿る）
                    for sitemap_elem in root.findall(".//{*}sitemap"):
                        loc = sitemap_elem.find("{*}loc")
                        if loc is not None and loc.text:
                            sub_sitemap_url = loc.text.strip()
                            try:
                                sub_resp = requests.get(sub_sitemap_url, timeout=10)
                            except Exception:
                                continue
                            if sub_resp.status_code == 200 and sub_resp.content:
                                try:
                                    sub_root = ET.fromstring(sub_resp.content)
                                except ET.ParseError:
                                    continue
                                for url_elem in sub_root.findall(".//{*}loc"):
                                    if url_elem.text:
                                        norm = normalize_url(url_elem.text.strip())
                                        if norm:
                                            urls.add(norm)
                # サイトマップから取得できたURLについてタイトルを取得
                for page_url in list(urls):
                    try:
                        page_resp = requests.get(page_url, timeout=5)
                        if page_resp.status_code == 200 and "text/html" in page_resp.headers.get("Content-Type", ""):
                            soup = BeautifulSoup(page_resp.text, "html.parser")
                            title_tag = soup.find("title")
                            if title_tag and title_tag.text:
                                titles[page_url] = title_tag.text.strip()
                    except Exception:
                        continue
                break  # サイトマップ取得成功したのでループ離脱
    # 2. サイトマップが無かった場合、クローリングでURL収集
    if not sitemap_found:
        from collections import deque
        start = normalize_url(START_URL)
        if start:
            urls.add(start)
            titles[start] = ""  # タイトルは後で取得
            queue = deque([start])
        else:
            queue = deque()
        visited = set(urls)
        while queue:
            current_url = queue.popleft()
            try:
                resp = requests.get(current_url, timeout=10)
            except Exception:
                continue
            if resp.status_code != 200:
                continue
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                continue
            # HTMLをパースしてリンク抽出
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
            except Exception:
                continue
            # ページタイトル取得
            title_tag = soup.find("title")
            if title_tag and title_tag.text:
                titles[current_url] = title_tag.text.strip()
            # 全ての<a>タグを走査し、新しいリンクを追加
            for a in soup.find_all("a", href=True):
                href = a["href"]
                new_url = urljoin(current_url, href)
                norm = normalize_url(new_url)
                if not norm:
                    continue
                if norm in visited:
                    continue
                # 画像やアセット等のリンクは除外
                if norm.lower().endswith((
                    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
                    ".pdf", ".zip", ".rar", ".7z", ".tar", ".gz",
                    ".css", ".js", ".json", ".xml", ".mp4", ".mp3"
                )):
                    continue
                visited.add(norm)
                urls.add(norm)
                queue.append(norm)
    return urls, titles

# 収集したURL一覧を取得
all_urls, titles = gather_all_urls()

# 1ページでも見つからなかった場合終了
if not all_urls:
    print("対象URLが見つかりませんでした。")
    exit(0)

# Core Web Vitalsで「遅い」と判定されたページをチェック
issues = []  # (url, {strategy: {metric: value, ...}, ...}) のリスト

def check_url(url: str) -> tuple[str, dict]:
    """指定URLのモバイル・デスクトップのCore Web Vitalsをチェックし、遅い指標があれば結果を返す"""
    result = {}
    for strategy in ["mobile", "desktop"]:
        # PageSpeed Insights API 呼び出しURLを構築
        api_endpoint = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={url}&strategy={strategy}"
        if PSI_API_KEY:
            api_endpoint += f"&key={PSI_API_KEY}"
        try:
            res = requests.get(api_endpoint, timeout=30)
        except Exception as e:
            print(f"[Error] PSI API request failed: {url} ({strategy}) - {e}")
            continue
        if res.status_code != 200:
            print(f"[Warning] PSI API returned {res.status_code} for {url} ({strategy})")
            continue
        data = res.json()
        # フィールドデータの指標カテゴリーを確認
        metrics = data.get("loadingExperience", {}).get("metrics", {})
        if not metrics:
            continue  # フィールドデータなし（新しいページ等）
        slow_metrics = {}
        # LCP, FID, CLSについてカテゴリーを判定
        # カテゴリー値が "SLOW" の場合のみ記録
        # percentile値から人間読み可能な値（秒・ミリ秒）に変換
        if "LARGEST_CONTENTFUL_PAINT_MS" in metrics:
            cat = metrics["LARGEST_CONTENTFUL_PAINT_MS"].get("category")
            if cat == "SLOW":
                val_ms = metrics["LARGEST_CONTENTFUL_PAINT_MS"].get("percentile")
                if isinstance(val_ms, (int, float)):
                    slow_metrics["LCP"] = f"{val_ms/1000:.1f}秒"
                else:
                    slow_metrics["LCP"] = f"{val_ms}秒"
        if "FIRST_INPUT_DELAY_MS" in metrics:
            cat = metrics["FIRST_INPUT_DELAY_MS"].get("category")
            if cat == "SLOW":
                val_ms = metrics["FIRST_INPUT_DELAY_MS"].get("percentile")
                if isinstance(val_ms, (int, float)):
                    slow_metrics["FID"] = f"{int(val_ms)}ミリ秒"
                else:
                    slow_metrics["FID"] = f"{val_ms}ミリ秒"
        if "CUMULATIVE_LAYOUT_SHIFT_SCORE" in metrics:
            cat = metrics["CUMULATIVE_LAYOUT_SHIFT_SCORE"].get("category")
            if cat == "SLOW":
                val_cls = metrics["CUMULATIVE_LAYOUT_SHIFT_SCORE"].get("percentile")
                if isinstance(val_cls, (int, float)):
                    # CLSスコアは百分率表示の可能性があるため数値を0～1範囲に変換
                    cls_value = float(val_cls)
                    if cls_value > 1.0:
                        cls_value = cls_value / 100.0
                    slow_metrics["CLS"] = f"{cls_value:.2f}"
                else:
                    slow_metrics["CLS"] = str(val_cls)
        if slow_metrics:
            result[strategy] = slow_metrics
    return (url, result)

# マルチスレッドで全URLのチェックを実行（APIコールを並列化して時間短縮）
with ThreadPoolExecutor(max_workers=5) as executor:
    futures = [executor.submit(check_url, url) for url in all_urls]
    for future in futures:
        url, result = future.result()
        if result:
            issues.append((url, result))

# Slack通知用メッセージの組み立て
message_lines = []
message_lines.append("*Core Web Vitalsモニタリング結果*")
if not issues:
    # 問題のあるページが無い場合
    message_lines.append("全ページのCore Web Vitals指標は良好でした。🎉")
else:
    message_lines.append("以下のページで**「遅い」**と判定されたCore Web Vitals指標があります：")
    for url, result in issues:
        # Slack用のリンク書式（<URL|表示テキスト>）。タイトルがある場合はタイトルを使用
        title = titles.get(url, url)
        if title:
            # Slackの特殊文字 '|' を全角に置換（リンク表示の区切りと衝突しないようにする）
            title = title.replace("|", "｜")
        link_text = title if title else url
        link = f"<{url}|{link_text}>"
        # モバイル・デスクトップの各結果をまとめる
        parts = []
        if "mobile" in result and result["mobile"]:
            # 複数指標は読点で区切り
            metrics_list = [f"{m}遅い({val})" for m, val in result["mobile"].items()]
            parts.append("モバイル – " + "、".join(metrics_list))
        else:
            parts.append("モバイル – 良好")
        if "desktop" in result and result["desktop"]:
            metrics_list = [f"{m}遅い({val})" for m, val in result["desktop"].items()]
            parts.append("デスクトップ – " + "、".join(metrics_list))
        else:
            parts.append("デスクトップ – 良好")
        # 箇条書きの各行を構築
        message_lines.append(f"- {link}：{'; '.join(parts)}")

message_text = "\n".join(message_lines)

# Slackに通知を送信（Webhookを使用）
if SLACK_WEBHOOK_URL:
    try:
        resp = requests.post(SLACK_WEBHOOK_URL, json={"text": message_text})
        if resp.status_code != 200:
            print(f"[Error] Slack通知に失敗しました (status={resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"[Error] Slack通知中に例外発生: {e}")
else:
    # Webhook URLが設定されていない場合は標準出力に結果を出力
    print(message_text)
