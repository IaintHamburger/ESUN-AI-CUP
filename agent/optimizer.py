# ==============================================================================
# optimizer.py — 投資組合最佳化
# 使用 PyPortfolioOpt：Mean-Variance 最佳化 + Black-Litterman 主觀觀點注入
# ==============================================================================

from __future__ import annotations
import warnings
import numpy as np
import pandas as pd

from agent.config import (
    TSMC_TICKER, TSMC_MAX_WEIGHT, OTHER_MAX_WEIGHT,
    CASH_MIN, CASH_MAX, MIN_HOLDINGS, MAX_HOLDINGS, NO_TRADE_BAND
)

warnings.filterwarnings("ignore")


# ── 協方差矩陣 ────────────────────────────────────────────────────────────────

def _compute_cov_shrinkage(returns: pd.DataFrame) -> pd.DataFrame:
    """Ledoit-Wolf 收縮估計（比樣本共變異數更穩定）"""
    try:
        from pypfopt import risk_models
        return risk_models.CovarianceShrinkage(returns, returns_data=True).ledoit_wolf()
    except Exception:
        # fallback：樣本共變異數
        return returns.cov() * 252


def _compute_expected_returns(returns: pd.DataFrame, views: dict | None) -> pd.Series:
    """計算預期報酬（有主觀觀點時用 Black-Litterman）"""
    try:
        from pypfopt import expected_returns, risk_models
        from pypfopt.black_litterman import BlackLittermanModel

        mu = expected_returns.mean_historical_return(returns, returns_data=True)
        S  = _compute_cov_shrinkage(returns)

        if views:
            # 只用在可交易清單內的觀點
            valid_views = {k: v for k, v in views.items() if k in returns.columns}
            if valid_views:
                bl = BlackLittermanModel(S, pi=mu, absolute_views=valid_views)
                return bl.bl_returns()

        return mu
    except Exception as e:
        print(f"⚠️  PyPortfolioOpt 失敗，改用簡單動能：{e}")
        return returns.mean() * 252  # fallback：年化平均報酬


# ── 主要最佳化函數 ────────────────────────────────────────────────────────────

def optimize(
    returns: pd.DataFrame,
    current_weights: dict[str, float],
    momentum_scores: dict[str, float],
    views: dict[str, float] | None = None,
    target_n: int = 25,
    risk_aversion: float = 1.0,
) -> dict[str, float]:
    """
    計算目標持股權重。

    Args:
        returns:         歷史日報酬率 DataFrame（欄=ticker，越多天越好）
        current_weights: 當前持股權重 {ticker: weight}
        momentum_scores: 動能分數 {ticker: -1~1}（由 technicals.py 計算）
        views:           策略師主觀觀點 {ticker: 年化預期報酬}
                         例：{"2330": 0.12} = 預期 2330 年化 12%
        target_n:        目標持股檔數（20–30）
        risk_aversion:   風險厭惡係數（1=平衡；>1=偏保守；<1=偏積極）

    Returns:
        {ticker: target_weight}，加總 ≤ 1（差值為現金）
    """
    tickers = list(returns.columns)

    # 1. 計算預期報酬與共變異數
    mu = _compute_expected_returns(returns, views)
    S  = _compute_cov_shrinkage(returns)

    # 2. 把動能分數疊加進預期報酬（動能 +0.1 → 預期報酬加 10%）
    for ticker in tickers:
        score = momentum_scores.get(ticker, 0)
        if ticker in mu.index:
            mu[ticker] += score * 0.1

    # 3. 選出目標持股池：當前持股 + 動能最高的候選股
    current = set(current_weights.keys())
    candidates = sorted(momentum_scores, key=lambda t: -momentum_scores[t])
    candidate_pool = list(current | set(candidates[:target_n]))
    # 只保留有歷史資料的
    candidate_pool = [t for t in candidate_pool if t in tickers][:MAX_HOLDINGS]

    if not candidate_pool:
        return current_weights  # fallback

    mu_sub = mu[candidate_pool]
    S_sub  = S.loc[candidate_pool, candidate_pool]

    # 4. 最佳化
    try:
        from pypfopt import EfficientFrontier

        ef = EfficientFrontier(mu_sub, S_sub, weight_bounds=(0, None))

        # 加競賽個股上限
        for i, ticker in enumerate(candidate_pool):
            limit = TSMC_MAX_WEIGHT if ticker == TSMC_TICKER else OTHER_MAX_WEIGHT
            ef.add_constraint(lambda w, idx=i, lim=limit: w[idx] <= lim)

        # 總持股上限（留現金）
        cash_floor = max(CASH_MIN + 0.02, 0.03)
        ef.add_constraint(lambda w: w.sum() <= 1.0 - cash_floor)

        if risk_aversion != 1.0:
            ef.max_quadratic_utility(risk_aversion=risk_aversion)
        else:
            ef.max_sharpe()

        raw = ef.clean_weights()

    except Exception as e:
        print(f"⚠️  EfficientFrontier 失敗，改用等權重：{e}")
        raw = _equal_weight_fallback(candidate_pool, current_weights, momentum_scores, target_n)

    # 5. 套用無交易帶過濾
    result: dict[str, float] = {}
    for ticker, w in raw.items():
        if w <= 0:
            continue
        curr_w = current_weights.get(ticker, 0)
        if abs(w - curr_w) < NO_TRADE_BAND and ticker in current_weights:
            result[ticker] = curr_w   # 差距不夠大，維持原權重
        else:
            result[ticker] = round(w, 4)

    return result


def _equal_weight_fallback(
    pool: list[str],
    current: dict[str, float],
    scores: dict[str, float],
    n: int,
) -> dict[str, float]:
    """PyPortfolioOpt 失敗時的備用方案：等權重選動能最強 n 檔"""
    sorted_pool = sorted(pool, key=lambda t: -scores.get(t, 0))[:n]
    w = round((1 - 0.05) / len(sorted_pool), 4)  # 預留 5% 現金
    return {t: w for t in sorted_pool}


# ── 報告函數 ──────────────────────────────────────────────────────────────────

def describe_weights(
    target: dict[str, float],
    current: dict[str, float],
) -> list[dict]:
    """
    產生每個持股的調倉描述，方便 AI 寫 D-Plan inferences。
    """
    all_tickers = set(target) | set(current)
    rows = []
    for ticker in all_tickers:
        t_w = target.get(ticker, 0)
        c_w = current.get(ticker, 0)
        diff = t_w - c_w
        action = (
            "BUY"      if c_w == 0 and t_w > 0 else
            "SELL_ALL" if t_w == 0 and c_w > 0 else
            "ADD"      if diff > NO_TRADE_BAND  else
            "TRIM"     if diff < -NO_TRADE_BAND else
            "HOLD"
        )
        rows.append({
            "ticker":        ticker,
            "action":        action,
            "current_weight": round(c_w, 4),
            "target_weight":  round(t_w, 4),
            "weight_change":  round(diff, 4),
        })
    return sorted(rows, key=lambda x: -abs(x["weight_change"]))
