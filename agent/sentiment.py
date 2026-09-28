# ==============================================================================
# sentiment.py — 新聞情緒分析
# 使用 feedparser 抓 RSS + 關鍵字規則作為輕量情緒分析
# （FinBERT 需要 PyTorch，在 Python 3.14 上安裝複雜，故提供兩種實作）
# ==============================================================================

from __future__ import annotations
import re
from datetime import date, timedelta
from typing import Optional

import feedparser


# ── 關鍵字情緒字典（輕量版，無需 GPU/torch）────────────────────────────────

_BULLISH_KEYWORDS = [
    # 財報正向
    "上修", "超車", "爆量", "法說", "獲利創高", "EPS 超預期",
    "訂單滿載", "拿下訂單", "新客戶", "漲價", "供不應求",
    # 技術/產業
    "AI 需求", "CoWoS", "先進封裝", "HBM", "伺服器拉貨",
    "降息", "聯準會鴿派", "買超", "外資加碼",
    # 一般多頭
    "突破", "強勢", "創新高", "拉升", "買進",
]

_BEARISH_KEYWORDS = [
    # 財報負向
    "下修", "虧損", "庫存修正", "砍單", "出貨遞延",
    "客戶取消", "毛利壓縮", "匯損",
    # 技術/產業
    "過熱", "泡沫", "賣超", "外資賣", "調節",
    # 總經利空
    "升息", "通膨超預期", "衰退", "PMI 萎縮",
    # 一般空頭
    "破底", "弱勢", "跌破", "賣出", "看空",
]


def _keyword_score(text: str) -> float:
    """
    對一段文字計算情緒分數（-1 到 1）。
    正值=偏多，負值=偏空。
    """
    text_lower = text.lower()
    bull = sum(1 for kw in _BULLISH_KEYWORDS if kw in text)
    bear = sum(1 for kw in _BEARISH_KEYWORDS if kw in text)
    total = bull + bear
    if total == 0:
        return 0.0
    return round((bull - bear) / total, 3)


# ── RSS 新聞抓取 ──────────────────────────────────────────────────────────────

RSS_FEEDS = {
    "cnyes_tw":      "https://news.cnyes.com/rss/cat/tw_stock",
    "cnyes_macro":   "https://news.cnyes.com/rss/cat/macroeconomy",
    "yahoo_finance": "https://tw.stock.yahoo.com/rss",
}

def fetch_headlines(max_items: int = 30, days_back: int = 1) -> list[dict]:
    """
    從多個 RSS Feed 抓取最新財經新聞標題。
    回傳 [{title, source, published_at}]
    """
    headlines = []
    cutoff = date.today() - timedelta(days=days_back)

    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_items]:
                title = entry.get("title", "").strip()
                if title:
                    headlines.append({
                        "title":      title,
                        "source":     source,
                        "url":        entry.get("link", url),
                    })
        except Exception as e:
            print(f"⚠️  RSS {source} 抓取失敗：{e}")

    return headlines[:max_items]


# ── 主要情緒分析函數 ──────────────────────────────────────────────────────────

def analyze_sentiment(headlines: list[dict]) -> dict:
    """
    對新聞標題集合計算整體市場情緒。

    回傳:
        {
            "score":        float,   # -1（極空）到 1（極多）
            "bullish_count": int,
            "bearish_count": int,
            "neutral_count": int,
            "top_bullish":  list,   # 最看多的幾條標題
            "top_bearish":  list,   # 最看空的幾條標題
        }
    """
    if not headlines:
        return {
            "score": 0.0, "bullish_count": 0,
            "bearish_count": 0, "neutral_count": 0,
            "top_bullish": [], "top_bearish": [],
        }

    scored = []
    for h in headlines:
        s = _keyword_score(h["title"])
        scored.append({**h, "score": s})

    bullish = [s for s in scored if s["score"] > 0]
    bearish = [s for s in scored if s["score"] < 0]
    neutral = [s for s in scored if s["score"] == 0]

    avg_score = sum(s["score"] for s in scored) / len(scored)

    return {
        "score":          round(avg_score, 3),
        "bullish_count":  len(bullish),
        "bearish_count":  len(bearish),
        "neutral_count":  len(neutral),
        "top_bullish":    [s["title"] for s in sorted(bullish, key=lambda x: -x["score"])[:3]],
        "top_bearish":    [s["title"] for s in sorted(bearish, key=lambda x:  x["score"])[:3]],
    }


def ticker_sentiment(headlines: list[dict], ticker: str, company_name: str = "") -> float:
    """
    對特定股票/公司的新聞情緒分數。
    ticker: 4 位數字（例 "2330"）
    company_name: 可選（例 "台積電"）
    """
    relevant = [
        h for h in headlines
        if ticker in h["title"] or (company_name and company_name in h["title"])
    ]
    if not relevant:
        return 0.0
    scores = [_keyword_score(h["title"]) for h in relevant]
    return round(sum(scores) / len(scores), 3)


def to_observation_values(sentiment: dict) -> dict:
    """把情緒分析結果轉成 D-Plan observations.values 格式"""
    return {
        "news_sentiment_score": sentiment["score"],
        "news_bullish_count":   sentiment["bullish_count"],
        "news_bearish_count":   sentiment["bearish_count"],
    }
