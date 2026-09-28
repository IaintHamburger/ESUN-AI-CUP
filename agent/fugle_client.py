# ==============================================================================
# fugle_client.py — Fugle MarketData API 客戶端
# Fugle 提供台股最精準的即時 / 盤前報價（需申請 API Key）
# ==============================================================================
"""
Fugle MarketData API 說明：
  官網：https://developer.fugle.tw/
  定價：免費方案（每分鐘 60 次請求，歷史 60 天）
  申請：https://developer.fugle.tw/docs/data/getting-started

取得 API Key 後，設定環境變數：
  export FUGLE_API_KEY="your-api-key"

或在 .env 檔案中設定：
  FUGLE_API_KEY=your-api-key

此模組涵蓋：
  1. 即時 / 盤前報價（snapshot）
  2. 當日 Tick 成交明細（intraday trades）
  3. 當日分 K（intraday candles）
  4. 歷史日 K（historical candles）
  5. 當日委買委賣（order book）
"""

from __future__ import annotations
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests

# API Key 統一從 config 讀（config 會自動載入 .env）
from agent.config import FUGLE_API_KEY as _DEFAULT_KEY

_BASE_URL = "https://api.fugle.tw/marketdata/v1.0/stock"
_HEADERS = {
    "X-API-KEY": "",   # 由 _get_headers() 動態填入
    "Content-Type": "application/json",
}
_TIMEOUT = 10
_RATE_LIMIT_DELAY = 0.1  # 100ms between requests


def _get_key() -> str:
    key = _DEFAULT_KEY
    if not key or key == "your-fugle-api-key-here":
        raise EnvironmentError(
            "\n❌ 需要 Fugle API Key\n"
            "   1. 前往 https://developer.fugle.tw/ 免費註冊\n"
            "   2. 在 .env 檔案填入：FUGLE_API_KEY=your-key-here"
        )
    return key



def _get(endpoint: str, params: dict | None = None) -> dict | list | None:
    """統一 GET 請求，含 rate limit 保護"""
    try:
        resp = requests.get(
            f"{_BASE_URL}{endpoint}",
            headers={"X-API-KEY": _get_key()},
            params=params or {},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        time.sleep(_RATE_LIMIT_DELAY)
        return resp.json()
    except EnvironmentError as e:
        print(e)
        return None
    except requests.HTTPError as e:
        if e.response.status_code == 429:
            print("⚠️  Fugle API 請求過於頻繁，等待 1 秒...")
            time.sleep(1)
        else:
            print(f"⚠️  Fugle API 錯誤 {e.response.status_code}：{e.response.text[:100]}")
        return None
    except Exception as e:
        print(f"⚠️  Fugle API 失敗：{e}")
        return None


# ── 1. 即時 / 盤前報價（每天最重要的函數）────────────────────────────────────

def get_snapshot(ticker: str) -> dict | None:
    """
    取得股票即時報價快照（盤前試撮 / 盤中即時 / 盤後成交）。

    回傳（部分欄位）：
    {
      "lastPrice":    float,   # 最新成交價
      "changeRate":   float,   # 漲跌幅 (%)
      "totalVolume":  int,     # 成交量
      "bidPrice":     float,   # 最佳買價
      "askPrice":     float,   # 最佳賣價
      "openPrice":    float,   # 開盤價
      "highPrice":    float,   # 最高
      "lowPrice":     float,   # 最低
      "referencePrice": float, # 昨收（參考價）
    }
    """
    data = _get(f"/intraday/quote/{ticker}")
    if not data:
        return None
    return {
        "ticker":          ticker,
        "last_price":      data.get("lastPrice"),
        "change_pct":      data.get("changeRate"),
        "volume":          data.get("totalVolume"),
        "bid":             data.get("bidPrice"),
        "ask":             data.get("askPrice"),
        "open":            data.get("openPrice"),
        "high":            data.get("highPrice"),
        "low":             data.get("lowPrice"),
        "reference_price": data.get("referencePrice"),
    }


def get_snapshots_batch(tickers: list[str]) -> dict[str, dict]:
    """批次取得多支股票快照（注意 rate limit）"""
    result = {}
    for ticker in tickers:
        snap = get_snapshot(ticker)
        if snap:
            result[ticker] = snap
    return result


# ── 2. 當日成交明細（Tick）────────────────────────────────────────────────────

def get_intraday_trades(ticker: str, limit: int = 50) -> list[dict]:
    """
    取得當日最近 N 筆 Tick 成交資料。
    可用來偵測「大單買賣」（量大 + 價高 → 主力買進信號）。
    """
    data = _get(f"/intraday/trades/{ticker}", params={"limit": limit})
    if not data or not isinstance(data, dict):
        return []
    return data.get("data", [])


def detect_large_orders(trades: list[dict], threshold_shares: int = 500) -> dict:
    """
    從 Tick 資料偵測大單方向。
    threshold_shares: 單筆超過此張數視為大單（預設 500 張）
    """
    buy_vol = sum(t.get("volume", 0) for t in trades
                  if t.get("volume", 0) >= threshold_shares and t.get("side") == "Buy")
    sell_vol = sum(t.get("volume", 0) for t in trades
                   if t.get("volume", 0) >= threshold_shares and t.get("side") == "Sell")
    total = buy_vol + sell_vol
    return {
        "large_buy_vol":  buy_vol,
        "large_sell_vol": sell_vol,
        "large_order_direction": "buy" if buy_vol > sell_vol else "sell" if sell_vol > buy_vol else "neutral",
        "large_order_ratio": round(buy_vol / total, 3) if total > 0 else 0.5,
    }


# ── 3. 當日委買委賣（Order Book）─────────────────────────────────────────────

def get_order_book(ticker: str) -> dict | None:
    """
    取得五檔委買委賣報價。
    bid_ask_spread 可用來衡量流動性。
    """
    data = _get(f"/intraday/quote/{ticker}")
    if not data:
        return None
    bids = data.get("bids", [])
    asks = data.get("asks", [])
    if bids and asks:
        best_bid = bids[0].get("price", 0)
        best_ask = asks[0].get("price", 0)
        spread = best_ask - best_bid
        spread_pct = spread / best_bid * 100 if best_bid else 0
        return {
            "best_bid":    best_bid,
            "best_ask":    best_ask,
            "spread":      round(spread, 2),
            "spread_pct":  round(spread_pct, 3),
        }
    return None


# ── 4. 歷史日 K（作為 yfinance 備援）────────────────────────────────────────

def get_historical_candles(
    ticker: str,
    days: int = 90,
) -> list[dict]:
    """
    取得歷史日 K 資料（Fugle 提供 60 天免費）。
    格式：[{date, open, high, low, close, volume}]
    """
    end_date   = date.today().strftime("%Y-%m-%d")
    start_date = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

    data = _get(
        f"/historical/candles/{ticker}",
        params={"timeframe": "D", "from": start_date, "to": end_date},
    )
    if not data or not isinstance(data, dict):
        return []
    return data.get("data", [])


# ── 5. 盤前試撮彙整（批次）────────────────────────────────────────────────────

def get_preopen_prices(
    tickers: list[str],
    verbose: bool = False,
) -> dict[str, dict]:
    """
    取得多支股票的盤前試撮價格（08:30 前最有用）。
    建議在 08:00–08:30 之間呼叫。
    """
    results = {}
    print(f"   🔍 Fugle 盤前試撮：查詢 {len(tickers)} 檔...")
    for ticker in tickers:
        snap = get_snapshot(ticker)
        if snap and snap.get("last_price"):
            results[ticker] = snap
            if verbose:
                print(f"      {ticker}: {snap['last_price']} ({snap['change_pct']:+.2f}%)")
    print(f"   ✅ 取得 {len(results)} 檔試撮資料")
    return results


# ── 6. 檢查 API Key 是否有效 ──────────────────────────────────────────────────

def test_connection() -> bool:
    """測試 Fugle API 連線是否正常"""
    result = get_snapshot("2330")
    if result:
        print(f"✅ Fugle API 連線正常 | 台積電：{result.get('last_price')}")
        return True
    print("❌ Fugle API 連線失敗（請確認 FUGLE_API_KEY 已設定）")
    return False


# ── 整合：把 Fugle 資料轉換成 D-Plan observations 格式 ────────────────────

def to_observation_values(ticker: str, snap: dict) -> dict:
    """把 Fugle 快照轉成 D-Plan observations.values 格式"""
    return {
        f"fugle_last_{ticker}":    snap.get("last_price"),
        f"fugle_chg_pct_{ticker}": snap.get("change_pct"),
        f"fugle_vol_{ticker}":     snap.get("volume"),
    }
