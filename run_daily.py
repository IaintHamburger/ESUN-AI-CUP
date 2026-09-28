#!/usr/bin/env python3
# ==============================================================================
# run_daily.py — 每日執行入口（升級版：使用完整 LangGraph Agent）
# ==============================================================================

import json
import sys
from datetime import date
from pathlib import Path

from agent.config import TEAM_ID, HOLDINGS_PATH, DPLAN_DIR, GOOGLE_API_KEY
from agent.graph import run_agent
from agent import submitter, validator
from agent.ledger import PortfolioLedger


def main():
    trade_date = date.today().strftime("%Y-%m-%d")
    print(f"\n{'='*60}")
    print(f"  玉山 AI CUP — 量化 AI Agent")
    print(f"  隊伍：{TEAM_ID}  |  交易日：{trade_date}")
    print(f"{'='*60}")

    # ── 確認 API Key ───────────────────────────────────────────────────────────
    if not GOOGLE_API_KEY or GOOGLE_API_KEY == "your-gemini-api-key-here":
        print("\n❌ 請在 .env 填入 Gemini API Key：")
        print("   GOOGLE_API_KEY=your-key-here")
        print("   （從 https://aistudio.google.com/apikey 取得）")
        sys.exit(1)


    # ── 帳務確認 ──────────────────────────────────────────────────────────────
    book = PortfolioLedger(HOLDINGS_PATH)
    print(f"\n📂 帳務狀態：NAV={book.nav:,.0f} | 持股={len(book.holdings)}檔 | 現金={book.cash_pct():.1%}")

    # ── 策略師的主觀觀點（Black-Litterman，可選）─────────────────────────────
    # 格式：{ticker: 預期年化報酬率}，留空 {} 表示純量化最佳化
    bl_views = {}
    # 範例：
    # bl_views = {
    #     "2330": 0.15,   # 預期台積電年化 15%
    #     "2891": 0.10,   # 預期中信金年化 10%
    # }

    # ── 執行 Agent ────────────────────────────────────────────────────────────
    print(f"\n🚀 啟動 LangGraph Agent...")
    try:
        dplan = run_agent(trade_date=trade_date, bl_views=bl_views)
    except Exception as e:
        print(f"\n❌ Agent 執行失敗：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # ── 最終確認 ──────────────────────────────────────────────────────────────
    output_path = Path(DPLAN_DIR) / f"D-Plan_{TEAM_ID}_{trade_date}.json"
    print(f"\n📄 D-Plan 路徑：{output_path}")
    print(f"   委託筆數：{len(dplan.get('orders', []))}")
    print(f"   持股決策：{len(dplan.get('decisions', []))} 異動 + {len(dplan.get('no_trade_decisions', []))} 續抱")

    # ── 提交 ──────────────────────────────────────────────────────────────────
    confirm = input("\n確認提交？(y/N) ").strip().lower()
    if confirm == "y":
        success = submitter.submit(str(output_path))
        if success:
            print("✅ 提交成功！")
        else:
            print("❌ 提交失敗，請手動上傳")
    else:
        print("⏭️  已跳過提交，D-Plan 儲存於本機")

    print(f"\n提醒：今日收盤後請執行：python settle_daily.py")


if __name__ == "__main__":
    main()
