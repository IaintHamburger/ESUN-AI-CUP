# ==============================================================================
# ledger.py — 帳務系統
# 追蹤每日 NAV、持股、現金，並計算官方委託公式
# ==============================================================================

from __future__ import annotations
import json
import math
from datetime import date
from pathlib import Path
from typing import Optional

from agent.config import (
    HOLDINGS_PATH, FEE_RATE, TAX_RATE,
    TSMC_TICKER, TSMC_MAX_WEIGHT, OTHER_MAX_WEIGHT,
    CASH_MAX, CASH_MIN
)


class PortfolioLedger:
    """管理持股狀態與每日結算"""

    def __init__(self, path: str = HOLDINGS_PATH):
        self.path = Path(path)
        self._state = json.loads(self.path.read_text(encoding="utf-8"))

    # ── 基本屬性 ─────────────────────────────────────────────────────────────

    @property
    def nav(self) -> float:
        return float(self._state["nav"])

    @property
    def cash(self) -> float:
        return float(self._state["cash"])

    @property
    def holdings(self) -> dict[str, int]:
        """返回 {ticker: shares}"""
        return self._state["holdings"]

    @property
    def last_trade_date(self) -> str:
        return self._state.get("trade_date", "")

    # ── 權重計算 ─────────────────────────────────────────────────────────────

    def current_weights(self, prices: dict[str, float]) -> dict[str, float]:
        """計算當前各股權重（以 NAV 為基準）"""
        nav = self.nav
        return {
            ticker: (shares * prices.get(ticker, 0)) / nav
            for ticker, shares in self.holdings.items()
            if prices.get(ticker, 0) > 0
        }

    def cash_pct(self) -> float:
        return self.cash / self.nav

    # ── 官方委託公式 ──────────────────────────────────────────────────────────

    def calc_order_shares(
        self,
        ticker: str,
        target_weight: float,
        prev_close: float,
    ) -> int:
        """
        官方公式：
          target_shares = floor(target_weight × NAV ÷ prev_close ÷ 1000) × 1000
          order_shares  = target_shares − current_holdings
          正值 = BUY，負值 = SELL
        """
        if prev_close <= 0:
            raise ValueError(f"{ticker} 前日收盤價無效：{prev_close}")
        target_shares = math.floor(
            target_weight * self.nav / prev_close / 1000
        ) * 1000
        current_shares = self.holdings.get(ticker, 0)
        return target_shares - current_shares

    # ── 驗證輔助 ──────────────────────────────────────────────────────────────

    def validate_target_weights(
        self, target: dict[str, float]
    ) -> list[str]:
        """
        檢查目標權重是否違反競賽規則，回傳錯誤訊息列表。
        空列表代表通過。
        """
        errors: list[str] = []
        total_stock_weight = sum(target.values())
        cash_pct = 1.0 - total_stock_weight

        if cash_pct < CASH_MIN:
            errors.append(f"現金比 {cash_pct:.2%} < 下限 {CASH_MIN:.0%}")
        if cash_pct > CASH_MAX:
            errors.append(f"現金比 {cash_pct:.2%} > 上限 {CASH_MAX:.0%}")

        for ticker, w in target.items():
            limit = TSMC_MAX_WEIGHT if ticker == TSMC_TICKER else OTHER_MAX_WEIGHT
            if w > limit:
                errors.append(f"{ticker} 目標權重 {w:.2%} 超過上限 {limit:.0%}")

        n = len(target)
        if n < 20:
            errors.append(f"持股 {n} 檔 < 最低 20 檔")
        if n > 30:
            errors.append(f"持股 {n} 檔 > 上限 30 檔")

        return errors

    # ── 日終結算 ──────────────────────────────────────────────────────────────

    def settle_day(
        self,
        orders: list[dict],
        avg_prices: dict[str, float],
        closing_prices: dict[str, float],
        trade_date: str,
    ) -> None:
        """
        每日收盤後呼叫，依成交均價更新持股與現金，並重新計算 NAV。

        orders 格式:
          [{"ticker": "2330", "side": "BUY", "shares": 2000}, ...]

        avg_prices: 主辦方公布的當日成交均價（若無成交則不含該 ticker）
        closing_prices: 當日所有持股收盤價（用來重算 NAV）
        """
        cash = self.cash
        hld = dict(self.holdings)  # 複製一份修改

        for order in orders:
            ticker = order["ticker"]
            shares = int(order["shares"])
            side   = order["side"]
            avg    = avg_prices.get(ticker)

            if avg is None:
                print(f"⚠️  {ticker} 當日無成交均價，委託未成交，略過")
                continue

            amount = shares * avg

            if side == "BUY":
                cost = amount * (1 + FEE_RATE)
                cash -= cost
                hld[ticker] = hld.get(ticker, 0) + shares
            elif side == "SELL":
                proceeds = amount * (1 - FEE_RATE - TAX_RATE)
                cash += proceeds
                hld[ticker] = hld.get(ticker, 0) - shares
                if hld[ticker] == 0:
                    del hld[ticker]
                elif hld[ticker] < 0:
                    raise ValueError(f"❌ {ticker} 賣出後持股為負！請確認委託股數。")

        # 重算 NAV
        equity = sum(
            hld.get(t, 0) * closing_prices.get(t, 0)
            for t in hld
        )
        nav = cash + equity

        self._state.update({
            "trade_date": trade_date,
            "nav": round(nav, 2),
            "cash": round(cash, 2),
            "holdings": hld,
        })
        self._save()
        print(f"✅ {trade_date} 結算完成 | NAV: {nav:,.0f} | 現金: {cash:,.0f} | 持股: {len(hld)} 檔")

    def _save(self) -> None:
        self.path.write_text(
            json.dumps(self._state, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    # ── 報表 ─────────────────────────────────────────────────────────────────

    def summary(self, prices: Optional[dict[str, float]] = None) -> str:
        lines = [
            f"📊 持股快照（{self.last_trade_date}）",
            f"   NAV:  {self.nav:>18,.0f} TWD",
            f"   現金: {self.cash:>18,.0f} TWD ({self.cash_pct():.2%})",
            f"   持股: {len(self.holdings)} 檔",
        ]
        if prices:
            weights = self.current_weights(prices)
            for t, w in sorted(weights.items(), key=lambda x: -x[1]):
                lines.append(f"   {t}  {self.holdings[t]:>10,} 股  {w:.2%}")
        return "\n".join(lines)
