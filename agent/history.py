# ==============================================================================
# history.py — 歷史價格資料取得
# 用 yfinance 抓台股歷史資料（股票代號加 .TW 後綴）
# ==============================================================================

from __future__ import annotations
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd


def fetch_price_history(
    tickers: list[str],
    days: int = 90,
    save_cache: bool = True,
) -> pd.DataFrame:
    """
    取得多支股票的歷史收盤價 DataFrame。
    台股代號格式：2330 → 2330.TW

    回傳 DataFrame：index=日期，columns=台股代號（不帶 .TW）
    """
    try:
        import yfinance as yf
    except ImportError:
        print("❌ 請安裝 yfinance：pip install yfinance")
        return pd.DataFrame()

    end_date   = date.today()
    start_date = end_date - timedelta(days=days)
    tw_tickers = [f"{t}.TW" for t in tickers]

    print(f"📥 抓取 {len(tickers)} 檔歷史收盤價（{start_date} ~ {end_date}）...")
    try:
        raw = yf.download(
            tw_tickers,
            start=str(start_date),
            end=str(end_date),
            progress=False,
            auto_adjust=True,
        )["Close"]

        if isinstance(raw, pd.Series):   # 只有一支股票時是 Series
            raw = raw.to_frame(tw_tickers[0])

        # 去掉 .TW 後綴，還原成 4 位數代號
        raw.columns = [c.replace(".TW", "") for c in raw.columns]
        raw = raw.dropna(how="all")

        if save_cache:
            cache_path = Path(f"data/snapshots/price_history_{end_date}.parquet")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                raw.to_parquet(cache_path)
            except Exception:
                raw.to_csv(cache_path.with_suffix(".csv"))

        print(f"✅ 取得 {len(raw.columns)} 檔 × {len(raw)} 日")
        return raw

    except Exception as e:
        print(f"❌ 歷史資料抓取失敗：{e}")
        return pd.DataFrame()


def load_cached_history(max_age_days: int = 1) -> pd.DataFrame | None:
    """載入最近一次快取的歷史資料"""
    snap_dir = Path("data/snapshots")
    today = date.today()
    for d in range(max_age_days + 1):
        check_date = today - timedelta(days=d)
        for ext in [".parquet", ".csv"]:
            p = snap_dir / f"price_history_{check_date}{ext}"
            if p.exists():
                print(f"📂 使用快取歷史資料：{p.name}")
                if ext == ".parquet":
                    return pd.read_parquet(p)
                else:
                    return pd.read_csv(p, index_col=0, parse_dates=True)
    return None
