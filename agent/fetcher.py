# ==============================================================================
# fetcher.py — 市場資料抓取
# 所有外部資料來源集中在這裡管理
# ==============================================================================

from __future__ import annotations
import json
import time
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

# 台北時區
TZ_TAIPEI = timezone(timedelta(hours=8))


# ── TWSE（台灣證券交易所）────────────────────────────────────────────────────

def fetch_twse_prices(save_snapshot: bool = True) -> dict[str, float]:
    """
    抓取所有上市股票前一日收盤價。
    source authority: twse
    API: https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL
    """
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    prices: dict[str, float] = {}
    for row in data:
        ticker = row.get("Code", "").strip()
        close_str = row.get("ClosingPrice", "").replace(",", "").strip()
        if ticker and close_str and close_str != "--":
            try:
                prices[ticker] = float(close_str)
            except ValueError:
                pass

    if save_snapshot:
        _save_snapshot("twse_prices", prices)

    return prices


def fetch_twse_institutional(save_snapshot: bool = True) -> dict[str, dict]:
    """
    抓取三大法人（外資、投信、自營商）各股買賣超。
    source authority: twse
    API: https://openapi.twse.com.tw/v1/fund/TWT38U
    回傳: {ticker: {"foreign_net": float, "trust_net": float, "dealer_net": float}}
    """
    url = "https://openapi.twse.com.tw/v1/fund/TWT38U"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    result: dict[str, dict] = {}
    for row in data:
        ticker = row.get("Code", "").strip()
        if not ticker:
            continue
        try:
            result[ticker] = {
                "foreign_net": float(row.get("Foreign_Investor_Diff", "0").replace(",", "") or 0),
                "trust_net":   float(row.get("Investment_Trust_Diff", "0").replace(",", "") or 0),
                "dealer_net":  float(row.get("Dealer_Diff", "0").replace(",", "") or 0),
            }
        except (ValueError, KeyError):
            pass

    if save_snapshot:
        _save_snapshot("twse_institutional", result)

    return result


# ── TAIFEX（台灣期貨交易所）─────────────────────────────────────────────────

def fetch_taifex_night_close(save_snapshot: bool = True) -> Optional[dict]:
    """
    抓取台指期夜盤收盤行情。
    source authority: taifex
    注意：夜盤資料通常在 05:00 前後才出現，過早抓可能沒資料。
    回傳: {"close": float, "change_pct": float} 或 None（無資料）
    """
    url = "https://www.taifex.com.tw/cht/3/futDailyMarketReport"
    # TAIFEX 的夜盤資料需解析 HTML，這裡示範取得頁面
    # 實際解析需搭配 BeautifulSoup
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        # TODO: 搭配 BeautifulSoup 解析 HTML 表格
        # 暫時回傳 None，待實作
        print("⚠️  TAIFEX 夜盤解析尚未實作，請手動輸入")
        return None
    except Exception as e:
        print(f"⚠️  TAIFEX 夜盤抓取失敗：{e}")
        return None


# ── Yahoo Finance（美股 / ADR / VIX）────────────────────────────────────────

def fetch_us_market(save_snapshot: bool = True) -> dict[str, float]:
    """
    抓取美股收盤行情（SOX / NDX / SPX / TSM ADR / UMC ADR / VIX）。
    source authority: vendor
    需要安裝 yfinance：pip install yfinance
    回傳: {name: change_pct}
    """
    try:
        import yfinance as yf
    except ImportError:
        print("❌ 請先安裝 yfinance：pip install yfinance")
        return {}

    symbols = {
        "^SOX":  "SOX",
        "^NDX":  "NDX",
        "^GSPC": "SPX",
        "TSM":   "TSM_adr",
        "UMC":   "UMC_adr",
        "^VIX":  "VIX",
    }

    result: dict[str, float] = {}
    for symbol, name in symbols.items():
        try:
            hist = yf.Ticker(symbol).history(period="5d")
            if len(hist) >= 2:
                prev  = hist["Close"].iloc[-2]
                last  = hist["Close"].iloc[-1]
                chg   = round((last - prev) / prev * 100, 2)
                result[name] = chg
                result[f"{name}_close"] = round(float(last), 4)
        except Exception as e:
            print(f"⚠️  {symbol} 抓取失敗：{e}")

    if save_snapshot:
        _save_snapshot("us_market", result)

    return result


# ── 工具函數 ─────────────────────────────────────────────────────────────────

def _save_snapshot(name: str, data: dict) -> None:
    """將抓取的原始資料存到 data/snapshots/ 備查"""
    snap_dir = Path("data/snapshots")
    snap_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().strftime("%Y-%m-%d")
    path = snap_dir / f"{today}_{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def now_taipei() -> str:
    """回傳台北時區的現在時間，格式符合 D-Plan timestamp"""
    return datetime.now(TZ_TAIPEI).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def build_sources_block(fetched_at: str) -> list[dict]:
    """
    根據今天抓取的資料，自動組裝 D-Plan 的 sources 區塊。
    fetched_at: 抓取時間（統一用同一個時間戳）
    """
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%dT18:30:00+08:00")
    us_close = (date.today() - timedelta(days=1)).strftime("%Y-%m-%dT04:00:00+08:00")

    return [
        {
            "source_id": "S1",
            "authority": "twse",
            "name": "TWSE 盤後資訊：上市個股日收盤價量（參賽者自抓官方 OpenAPI）",
            "url": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            "content_as_of": yesterday,
            "fetched_at": fetched_at,
        },
        {
            "source_id": "S2",
            "authority": "twse",
            "name": "TWSE 三大法人買賣超（參賽者自抓官方 OpenAPI）",
            "url": "https://openapi.twse.com.tw/v1/fund/TWT38U",
            "content_as_of": yesterday,
            "fetched_at": fetched_at,
        },
        {
            "source_id": "S3",
            "authority": "taifex",
            "name": "台指期夜盤收盤（TAIFEX 盤後行情表）",
            "url": "https://www.taifex.com.tw/cht/3/futDailyMarketReport",
            "content_as_of": date.today().strftime("%Y-%m-%dT05:00:00+08:00"),
            "fetched_at": fetched_at,
        },
        {
            "source_id": "S4",
            "authority": "vendor",
            "name": "美股收盤行情（SOX / NDX / SPX / TSM ADR / UMC ADR / VIX）",
            "url": "https://finance.yahoo.com",
            "content_as_of": us_close,
            "fetched_at": fetched_at,
        },
    ]
