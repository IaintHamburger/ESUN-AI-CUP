# ==============================================================================
# technicals.py — 技術指標計算（純 pandas 實作，不依賴 numba / TA-Lib）
# 支援 Python 3.14+
# ==============================================================================

from __future__ import annotations
import pandas as pd
import numpy as np
from typing import Optional


# ── 基礎指標函數 ──────────────────────────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def _macd(series: pd.Series, fast=12, slow=26, signal=9) -> pd.DataFrame:
    ema_fast   = _ema(series, fast)
    ema_slow   = _ema(series, slow)
    macd_line  = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram  = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": histogram})

def _bollinger(series: pd.Series, period=20, std_dev=2) -> pd.DataFrame:
    mid  = series.rolling(period).mean()
    std  = series.rolling(period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    pct_b = (series - lower) / (upper - lower).replace(0, np.nan)
    return pd.DataFrame({"upper": upper, "mid": mid, "lower": lower, "pct_b": pct_b})

def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period=14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


# ── 主要計算函數 ──────────────────────────────────────────────────────────────

def compute_signals(
    close: pd.Series,
    high:  Optional[pd.Series] = None,
    low:   Optional[pd.Series] = None,
    volume: Optional[pd.Series] = None,
) -> dict:
    """
    計算單一股票的技術指標。
    輸入 close（必要），high / low / volume（選用，有則計算更多指標）。
    回傳 dict，可直接放入 D-Plan observations.values。
    """
    if len(close) < 26:
        return {"error": "歷史資料不足（需 ≥ 26 日）"}

    rsi14     = _rsi(close, 14)
    macd_df   = _macd(close)
    bb_df     = _bollinger(close)
    ema20     = _ema(close, 20)
    ema60     = _ema(close, 60)

    mom5      = close.pct_change(5).iloc[-1]
    mom20     = close.pct_change(20).iloc[-1]

    result = {
        "rsi_14":       round(float(rsi14.iloc[-1]),        2),
        "macd_hist":    round(float(macd_df["hist"].iloc[-1]), 4),
        "macd_bull":    int(macd_df["hist"].iloc[-1] > 0),
        "bb_pct_b":     round(float(bb_df["pct_b"].iloc[-1]), 3),
        "ema20":        round(float(ema20.iloc[-1]),         2),
        "ema60":        round(float(ema60.iloc[-1]),         2),
        "trend_up":     int(ema20.iloc[-1] > ema60.iloc[-1]),
        "mom_5pct":     round(float(mom5) * 100,            2),
        "mom_20pct":    round(float(mom20) * 100,           2),
    }

    if high is not None and low is not None:
        atr14 = _atr(high, low, close, 14)
        result["atr_14"] = round(float(atr14.iloc[-1]), 2)
        result["atr_pct"] = round(float(atr14.iloc[-1]) / float(close.iloc[-1]) * 100, 2)

    if volume is not None:
        obv = (volume * close.diff().apply(np.sign)).cumsum()
        result["obv_trend_up"] = int(obv.iloc[-1] > obv.iloc[-5])

    return result


def compute_momentum_score(signals: dict) -> float:
    """
    把技術指標轉換成 -1.0 到 1.0 的綜合動能分數。
    策略師可以調整各因子的權重。
    """
    score = 0.0
    w = {               # 各因子權重（總和 = 1.0）
        "mom_5":   0.25,
        "mom_20":  0.20,
        "rsi":     0.15,
        "trend":   0.20,
        "macd":    0.10,
        "bb":      0.10,
    }

    # 動能
    score += w["mom_5"]  * min(max(signals.get("mom_5pct",  0) / 5,  -1), 1)
    score += w["mom_20"] * min(max(signals.get("mom_20pct", 0) / 10, -1), 1)

    # RSI：30–70 視為中性；超買/超賣各加減分
    rsi = signals.get("rsi_14", 50)
    if   rsi < 30: score += w["rsi"]
    elif rsi > 70: score -= w["rsi"]

    # 趨勢
    score += w["trend"] * (1 if signals.get("trend_up") else -1)

    # MACD
    score += w["macd"]  * (1 if signals.get("macd_bull") else -1)

    # 布林帶位置：> 0.8 超買，< 0.2 超賣
    bb = signals.get("bb_pct_b", 0.5)
    if   bb > 0.8: score -= w["bb"]
    elif bb < 0.2: score += w["bb"]

    return round(max(-1.0, min(1.0, score)), 3)


def build_observation_values(ticker: str, signals: dict) -> dict:
    """
    把指標轉成帶 ticker 前綴的 D-Plan observations.values 格式。
    例：{"rsi_2330": 68.5, "mom5_2330": 3.2}
    """
    return {
        f"rsi_{ticker}":       signals.get("rsi_14"),
        f"mom5pct_{ticker}":   signals.get("mom_5pct"),
        f"mom20pct_{ticker}":  signals.get("mom_20pct"),
        f"trend_up_{ticker}":  signals.get("trend_up"),
        f"macd_bull_{ticker}": signals.get("macd_bull"),
        f"score_{ticker}":     compute_momentum_score(signals),
    }
