# ==============================================================================
# news_scraper.py — 全文新聞抓取 + 強化情緒分析
# 使用 trafilatura 擷取新聞全文，提供比 RSS 標題更豐富的情緒信號
# ==============================================================================

from __future__ import annotations
import re
import time
import json
from datetime import date
from pathlib import Path
from typing import Optional

import feedparser
import trafilatura
import requests

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
}

# ── RSS 新聞來源（全部免費，無需登入）──────────────────────────────────────

RSS_SOURCES = {
    # 台股專區
    "cnyes_tw":      "https://news.cnyes.com/rss/cat/tw_stock",
    "cnyes_macro":   "https://news.cnyes.com/rss/cat/macroeconomy",
    "cnyes_us":      "https://news.cnyes.com/rss/cat/us_stock",
    # 財經新聞
    "money_dj":      "https://www.moneydj.com/KMDJ/RssProxy.aspx?feedName=tw",
    # 政府公告（重大訊息）
    "twse_announce": "https://www.twse.com.tw/rss/zh/news.rss",
}


# ── Step 1: 抓取 RSS 標題列表 ────────────────────────────────────────────────

def fetch_rss_items(max_per_source: int = 20) -> list[dict]:
    """
    從多個 RSS 源抓取新聞條目。
    回傳：[{title, url, source, summary}]
    """
    items = []
    for source, url in RSS_SOURCES.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_per_source]:
                title = entry.get("title", "").strip()
                link  = entry.get("link",  "").strip()
                if title and link:
                    items.append({
                        "title":   title,
                        "url":     link,
                        "source":  source,
                        "summary": entry.get("summary", "")[:200],
                    })
        except Exception as e:
            print(f"   ⚠️  RSS {source} 失敗：{e}")

    print(f"   RSS 共抓取 {len(items)} 條")
    return items


# ── Step 2: 抓取新聞全文（trafilatura）───────────────────────────────────────

def fetch_article_text(url: str, timeout: int = 8) -> Optional[str]:
    """
    用 trafilatura 擷取指定 URL 的新聞正文。
    比 RSS summary 包含更多資訊，情緒分析更準確。
    """
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=False,
                no_fallback=False,
            )
            return text
    except Exception:
        pass
    return None


def enrich_with_fulltext(items: list[dict], max_articles: int = 10) -> list[dict]:
    """
    對最重要的新聞（通常是前幾條）抓取全文。
    限制數量避免請求過多。
    """
    enriched = []
    for i, item in enumerate(items):
        if i < max_articles:
            text = fetch_article_text(item["url"])
            item["fulltext"] = text or item["summary"]
            time.sleep(0.3)  # 避免過快請求
        else:
            item["fulltext"] = item["summary"]
        enriched.append(item)
    return enriched


# ── Step 3: 個股相關新聞篩選 ─────────────────────────────────────────────────

# 常見公司名稱 → 股票代號映射（可持續擴充）
COMPANY_NAME_MAP = {
    "台積電": "2330", "TSMC": "2330",
    "鴻海": "2317",   "富士康": "2317",
    "聯發科": "2454",
    "台達電": "2308",
    "廣達": "2382",
    "緯創": "3231",
    "英業達": "2356",
    "大立光": "3008",
    "聯電": "2303",   "UMC": "2303",
    "日月光": "3711",
    "瑞昱": "2379",
    "中信金": "2891",
    "富邦金": "2881",
    "國泰金": "2882",
    "台灣大": "3045",
    "中華電": "2412",
}

def filter_ticker_news(items: list[dict], ticker: str) -> list[dict]:
    """篩選與特定股票相關的新聞"""
    # 找出公司名稱
    company_names = [name for name, t in COMPANY_NAME_MAP.items() if t == ticker]
    keywords = [ticker] + company_names

    relevant = []
    for item in items:
        text = item["title"] + " " + item.get("fulltext", "")
        if any(kw in text for kw in keywords):
            relevant.append(item)
    return relevant


# ── Step 4: 增強版情緒分析 ────────────────────────────────────────────────────

# 擴充關鍵字字典（比 sentiment.py 更細緻）
SENTIMENT_DICT = {
    # 財報利多
    "上修": +2, "超預期": +2, "獲利創高": +2, "EPS 創新高": +2,
    "法說上調": +2, "訂單滿載": +2, "新訂單": +1, "拿下大單": +2,
    "漲價": +1, "產能滿載": +1, "供不應求": +1,
    # AI / 科技利多
    "AI 需求": +2, "CoWoS": +2, "先進封裝": +1, "HBM": +2,
    "伺服器拉貨": +1, "GB200": +2, "Blackwell": +2,
    # 總經利多
    "降息": +1, "鴿派": +1, "降息預期升溫": +2,
    "軟著陸": +1, "PMI 擴張": +1,
    # 法人動向利多
    "外資買超": +1, "投信買超": +1, "三大法人買超": +2,
    "外資加碼": +2, "大幅買超": +2,

    # 財報利空
    "下修": -2, "低於預期": -2, "虧損": -2, "砍單": -2,
    "庫存修正": -2, "出貨遞延": -1, "客戶取消": -2,
    "毛利壓縮": -1, "匯損": -1, "認列損失": -2,
    # 科技利空
    "去庫存": -1, "需求疲弱": -2, "市況轉淡": -1,
    "競爭加劇": -1, "失去訂單": -2,
    # 總經利空
    "升息": -1, "鷹派": -1, "通膨超預期": -2,
    "衰退疑慮": -2, "PMI 萎縮": -2,
    # 法人動向利空
    "外資賣超": -1, "大幅賣超": -2, "外資持續賣": -2,

    # 個股事件
    "法律訴訟": -1, "監管調查": -2, "遭罰": -2,
    "內線交易": -3, "財務造假": -3,
}

def score_text(text: str) -> float:
    """對一段文字計算情緒分數（加權關鍵字）"""
    score = 0
    matched = 0
    for kw, weight in SENTIMENT_DICT.items():
        count = text.count(kw)
        if count > 0:
            score += weight * min(count, 2)  # 同一關鍵字最多計 2 次
            matched += count
    if matched == 0:
        return 0.0
    return round(max(-1.0, min(1.0, score / (matched * 2))), 3)


def analyze_news_full(
    items: list[dict],
    holdings: list[str] | None = None,
) -> dict:
    """
    完整版新聞情緒分析。

    Args:
        items:    新聞條目列表（可選含 fulltext）
        holdings: 持股代號列表（用於個股情緒篩選）

    Returns:
        {
          market_score:   float,    # 整體市場情緒 (-1~1)
          ticker_scores:  dict,     # {ticker: score}（各持股情緒）
          key_events:     list,     # 重要事件摘要
          source_count:   int,
        }
    """
    if not items:
        return {
            "market_score": 0.0,
            "ticker_scores": {},
            "key_events": [],
            "source_count": 0,
        }

    # 整體市場情緒
    all_scores = []
    key_events = []
    for item in items:
        text  = item["title"] + " " + item.get("fulltext", "")
        score = score_text(text)
        all_scores.append(score)

        # 高分（±0.5 以上）列為重要事件
        if abs(score) >= 0.4:
            key_events.append({
                "title":  item["title"][:80],
                "score":  score,
                "source": item["source"],
            })

    market_score = round(sum(all_scores) / len(all_scores), 3) if all_scores else 0.0

    # 個股情緒
    ticker_scores: dict[str, float] = {}
    if holdings:
        for ticker in holdings:
            related = filter_ticker_news(items, ticker)
            if related:
                scores = [score_text(i["title"] + " " + i.get("fulltext", "")) for i in related]
                ticker_scores[ticker] = round(sum(scores) / len(scores), 3)

    # 按絕對值排序重要事件
    key_events.sort(key=lambda x: -abs(x["score"]))

    return {
        "market_score":  market_score,
        "ticker_scores": ticker_scores,
        "key_events":    key_events[:10],
        "source_count":  len(items),
    }


# ── 主要入口（整合抓取 + 分析）───────────────────────────────────────────────

def run_news_pipeline(
    holdings: list[str] | None = None,
    fetch_fulltext: bool = True,
    max_rss: int = 20,
    max_fulltext: int = 8,
) -> dict:
    """
    一鍵執行：RSS 抓取 → 全文擷取 → 情緒分析。

    Args:
        holdings:      持股代號（用於個股情緒篩選）
        fetch_fulltext: 是否抓全文（較慢但更準）
        max_rss:       每個 RSS 源最多取幾條
        max_fulltext:  最多抓幾篇全文

    Returns:
        analyze_news_full() 的輸出
    """
    print("   📰 開始新聞 Pipeline...")
    items = fetch_rss_items(max_per_source=max_rss)

    if fetch_fulltext and items:
        print(f"   📄 抓取前 {max_fulltext} 篇全文...")
        items = enrich_with_fulltext(items, max_articles=max_fulltext)

    result = analyze_news_full(items, holdings=holdings)

    # 快取
    _save_news_cache(items, result)
    print(f"   情緒分數：{result['market_score']:+.3f} | 重要事件：{len(result['key_events'])} 則")
    return result


def _save_news_cache(items: list[dict], analysis: dict) -> None:
    """把新聞快取到本地"""
    snap_dir = Path("data/snapshots")
    snap_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().strftime("%Y-%m-%d")

    # 只存標題和分析結果（不存全文，避免過大）
    cache = {
        "date":     today,
        "analysis": analysis,
        "headlines": [{"title": i["title"], "source": i["source"]} for i in items],
    }
    path = snap_dir / f"{today}_news_analysis.json"
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
