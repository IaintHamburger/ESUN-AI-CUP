# ==============================================================================
# twse_extra.py — TWSE / TPEX / MOPS 進階 API
# 補充 fetcher.py 沒有涵蓋的高價值資料源
# ==============================================================================
"""
涵蓋以下資料（全部來自官方開放 API，authority=twse/tpex/mops）：

1. 融資融券（margin_short）      → 情緒 + 壓力分析
2. 月營收（monthly_revenue）     → 基本面事實
3. 本益比 / 殖利率（valuation）  → 估值因子
4. 外資持股比例（foreign_holding）
5. 集中市場成交量（market_volume）→ 大盤量能
6. 上市 / 上櫃盤前試撮（preopen_auction）← 僅盤前 08:30 前有效
"""

from __future__ import annotations
import re
from datetime import date, timedelta
from typing import Optional

import requests

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
_TIMEOUT = 15


# ── 1. 融資融券 (Margin / Short Selling) ──────────────────────────────────────

def fetch_margin_short(save: bool = True) -> dict[str, dict]:
    """
    融資融券餘額。
    authority: twse
    API: https://openapi.twse.com.tw/v1/exchangeReport/MARGIN_AND_SHORT
    回傳: {ticker: {margin_balance, short_balance, margin_quota_pct}}
    """
    url = "https://openapi.twse.com.tw/v1/exchangeReport/MARGIN_AND_SHORT"
    try:
        data = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT).json()
    except Exception as e:
        print(f"⚠️  融資融券 API 失敗：{e}")
        return {}

    result: dict[str, dict] = {}
    for row in data:
        ticker = row.get("Code", "").strip()
        if not ticker:
            continue
        try:
            result[ticker] = {
                "margin_balance":    int(str(row.get("MarginPurchaseAmount",    "0")).replace(",", "") or 0),
                "short_balance":     int(str(row.get("ShortSaleAmount",         "0")).replace(",", "") or 0),
                "margin_quota_pct":  float(str(row.get("MarginPurchaseQuota",   "0")).replace(",", "") or 0),
            }
        except (ValueError, TypeError):
            pass

    if save:
        _save("margin_short", result)
    return result


# ── 2. 月營收（MOPS 公開資訊觀測站）──────────────────────────────────────────

def fetch_monthly_revenue(year: int | None = None, month: int | None = None) -> dict[str, int]:
    """
    上市公司當月月營收（億元）。
    authority: mops
    注意：通常在次月 10 日前公告，抓取時請確認資料已更新。
    回傳: {ticker: revenue_100m}
    """
    if year is None or month is None:
        # 預設抓上個月（因為本月資料還沒公告）
        last_month = date.today().replace(day=1) - timedelta(days=1)
        year  = last_month.year
        month = last_month.month

    # MOPS API（民國年）
    roc_year = year - 1911
    url = (
        f"https://mops.twse.com.tw/nas/t21/sii/t21sc03_{roc_year}_{month:02d}_0.html"
    )
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.encoding = "big5"

        # 簡易解析：用正則找股票代號與營收數字
        # 格式：<td>2330</td>...<td>2,500,000</td>
        rows = re.findall(
            r"<td[^>]*>\s*(\d{4})\s*</td>.*?<td[^>]*>\s*([\d,]+)\s*</td>",
            resp.text,
            re.DOTALL,
        )
        result: dict[str, int] = {}
        for ticker, rev_str in rows[:500]:
            try:
                rev = int(rev_str.replace(",", ""))
                if rev > 0:
                    result[ticker] = rev
            except ValueError:
                pass

        if result:
            _save(f"monthly_revenue_{year}{month:02d}", result)
            print(f"✅ 月營收：取得 {len(result)} 檔（{year}/{month:02d}）")
        return result

    except Exception as e:
        print(f"⚠️  月營收抓取失敗：{e}")
        return {}


# ── 3. 本益比 / 殖利率 / 股價淨值比（估值因子）────────────────────────────

def fetch_valuation() -> dict[str, dict]:
    """
    本益比、殖利率、股價淨值比。
    authority: twse
    API: https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_d
    回傳: {ticker: {pe_ratio, yield_pct, pb_ratio}}
    """
    url = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_d"
    try:
        data = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT).json()
    except Exception as e:
        print(f"⚠️  估值 API 失敗：{e}")
        return {}

    result: dict[str, dict] = {}
    for row in data:
        ticker = row.get("Code", "").strip()
        if not ticker:
            continue
        try:
            result[ticker] = {
                "pe_ratio":  float(row.get("PEratio",      "0") or 0),
                "yield_pct": float(row.get("DividendYield", "0") or 0),
                "pb_ratio":  float(row.get("PBratio",      "0") or 0),
            }
        except (ValueError, TypeError):
            pass

    if result:
        _save("valuation", result)
    return result


# ── 4. 外資持股比例 ───────────────────────────────────────────────────────────

def fetch_foreign_holding() -> dict[str, float]:
    """
    外資及陸資持股比例（%）。
    authority: twse
    API: https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK
    回傳: {ticker: foreign_holding_pct}
    """
    url = "https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK"
    try:
        data = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT).json()
    except Exception as e:
        print(f"⚠️  外資持股 API 失敗：{e}")
        return {}

    result: dict[str, float] = {}
    for row in data:
        ticker = row.get("Code", "").strip()
        pct_str = row.get("ForeignAndChinaShareholders", "").replace(",", "").strip()
        if ticker and pct_str:
            try:
                result[ticker] = float(pct_str)
            except ValueError:
                pass

    if result:
        _save("foreign_holding", result)
    return result


# ── 5. 大盤成交量（量能強弱）────────────────────────────────────────────────

def fetch_market_volume() -> dict:
    """
    集中市場整體成交資訊（大盤量能）。
    authority: twse
    API: https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK
    回傳: {total_volume, total_value, up_count, down_count}
    """
    url = "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
    try:
        data = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT).json()
        # 取整體市場列
        for row in data:
            if row.get("Index", "").startswith("加權"):
                return {
                    "taiex_close":    float(str(row.get("ClosingIndex", "0")).replace(",", "") or 0),
                    "taiex_chg":      float(str(row.get("Change",        "0")).replace(",", "").replace("+", "") or 0),
                    "total_volume_b": float(str(row.get("TradeVolume",   "0")).replace(",", "") or 0) / 1e8,
                }
    except Exception as e:
        print(f"⚠️  大盤成交量 API 失敗：{e}")
    return {}


# ── 6. 盤前試撮（08:30 前才有，之後無效）────────────────────────────────────

def fetch_preopen_auction(tickers: list[str]) -> dict[str, dict]:
    """
    嘗試從多個來源取得盤前試撮價格。
    注意：此功能僅在每個交易日 08:30 前有效。
    08:30 後（正式開盤後）此函數回傳空字典。

    回傳: {ticker: {auction_price, chg_pct}}
    """
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8)))

    if now.hour >= 9:
        print("   ℹ️  已過盤前試撮時段（09:00 後），略過")
        return {}

    # 方法 1：嘗試 Fugle API（需申請 API Key）
    result = _try_fugle_preopen(tickers)
    if result:
        return result

    # 方法 2：嘗試 TWSE 盤前揭示（格式不穩定，僅備用）
    print("   ℹ️  試撮資料需設定 Fugle API Key（見 agent/fugle_client.py）")
    return {}


def _try_fugle_preopen(tickers: list[str]) -> dict[str, dict]:
    """嘗試用 Fugle API 取得試撮資訊"""
    import os
    api_key = os.environ.get("FUGLE_API_KEY", "")
    if not api_key:
        return {}
    # Fugle API 呼叫（有 key 時才執行）
    try:
        from agent.fugle_client import get_snapshot
        result = {}
        for ticker in tickers[:10]:  # 限制請求數量
            snap = get_snapshot(ticker, api_key)
            if snap:
                result[ticker] = snap
        return result
    except Exception:
        return {}


# ── 工具函數 ──────────────────────────────────────────────────────────────────

def _save(name: str, data: dict) -> None:
    import json
    from pathlib import Path
    snap_dir = Path("data/snapshots")
    snap_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().strftime("%Y-%m-%d")
    path = snap_dir / f"{today}_{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_source_entries(fetched_at: str) -> list[dict]:
    """
    為 twse_extra 的資料來源生成 D-Plan sources 條目（S6 起）。
    """
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%dT18:30:00+08:00")
    last_month = date.today().replace(day=1) - timedelta(days=1)
    return [
        {
            "source_id":    "S6",
            "authority":    "twse",
            "name":         "TWSE 融資融券餘額",
            "url":          "https://openapi.twse.com.tw/v1/exchangeReport/MARGIN_AND_SHORT",
            "content_as_of": yesterday,
            "fetched_at":   fetched_at,
        },
        {
            "source_id":    "S7",
            "authority":    "mops",
            "name":         f"公開資訊觀測站：月營收（{last_month.year}/{last_month.month:02d}）",
            "url":          "https://mops.twse.com.tw/mops/web/t21sc03",
            "content_as_of": last_month.strftime("%Y-%m-10T00:00:00+08:00"),
            "fetched_at":   fetched_at,
        },
        {
            "source_id":    "S8",
            "authority":    "twse",
            "name":         "TWSE 本益比 / 殖利率 / 股價淨值比",
            "url":          "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_d",
            "content_as_of": yesterday,
            "fetched_at":   fetched_at,
        },
    ]
