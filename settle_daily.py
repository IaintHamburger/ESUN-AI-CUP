#!/usr/bin/env python3
# ==============================================================================
# settle_daily.py — 日終結算
# 每天收到成交均價後跑這個，更新帳務
# ==============================================================================
"""
使用方式：
  python settle_daily.py

程式會要求你輸入：
1. 今天的成交均價（從比賽系統查詢）
2. 今天的收盤價（從 TWSE 抓取）

然後自動更新 data/holdings.json
"""

import json
from datetime import date

from agent.ledger import PortfolioLedger
from agent.fetcher import fetch_twse_prices
from agent.config import HOLDINGS_PATH, DPLAN_DIR, TEAM_ID


def main():
    trade_date = date.today().strftime("%Y-%m-%d")
    print(f"\n📊 日終結算 — {trade_date}")

    # 讀入今日已提交的 D-Plan（取出 orders）
    dplan_path = f"{DPLAN_DIR}D-Plan_{TEAM_ID}_{trade_date}.json"
    try:
        with open(dplan_path, encoding="utf-8") as f:
            dplan = json.load(f)
        orders = dplan.get("orders", [])
        print(f"✅ 讀取 D-Plan，委託筆數：{len(orders)}")
    except FileNotFoundError:
        print(f"❌ 找不到 {dplan_path}，請確認今日 D-Plan 已提交")
        return

    if not orders:
        print("今日無委託，帳務不需更新")
        return

    # 從比賽系統或交易所取得成交均價
    print("\n請輸入今日各委託的成交均價（若無成交直接按 Enter 跳過）：")
    avg_prices: dict[str, float] = {}
    for order in orders:
        ticker = order["ticker"]
        side   = order["side"]
        shares = order["shares"]
        val = input(f"  {ticker} {side} {shares:,}股 — 均價（TWD）: ").strip()
        if val:
            avg_prices[ticker] = float(val)

    if not avg_prices:
        print("⏭️  無成交均價，結算略過")
        return

    # 抓今日收盤價（用於重算 NAV）
    print("\n📥 抓取今日收盤價...")
    closing_prices = fetch_twse_prices(save_snapshot=False)
    print(f"✅ 取得 {len(closing_prices)} 檔收盤價")

    # 執行結算
    book = PortfolioLedger(HOLDINGS_PATH)
    book.settle_day(
        orders         = orders,
        avg_prices     = avg_prices,
        closing_prices = closing_prices,
        trade_date     = trade_date,
    )
    print(book.summary(closing_prices))


if __name__ == "__main__":
    main()
