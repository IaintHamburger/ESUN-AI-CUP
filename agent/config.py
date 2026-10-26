# ==============================================================================
# config.py — 全域設定 + API 金鑰載入
# ⚙️  所有模組都從這裡讀取設定，不需要自己呼叫 os.environ 或 dotenv
# ==============================================================================

import os
from pathlib import Path
from dotenv import load_dotenv

# 自動從專案根目錄的 .env 載入環境變數
# override=False：環境變數已存在時不覆蓋（方便 CI / 手動 export 優先）
_ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)


# ── API 金鑰（從 .env 讀取，請勿在這裡硬寫）─────────────────────────────────
GOOGLE_API_KEY  = os.getenv("GOOGLE_API_KEY",  "")  # Gemini AI
FUGLE_API_KEY   = os.getenv("FUGLE_API_KEY",   "")  # 台股即時資料
ESUN_API_TOKEN  = os.getenv("ESUN_API_TOKEN",  "")  # 比賽提交 Token
CMONEY_API_KEY  = os.getenv("CMONEY_API_KEY",  "")  # 選填

def require(key: str) -> str:
    """取得必要金鑰；未設定時印出清楚的錯誤提示"""
    val = os.getenv(key, "")
    if not val:
        raise EnvironmentError(
            f"\n❌ 缺少環境變數：{key}\n"
            f"   請在 .env 檔案中填入：{key}=your-key-here\n"
            f"   參考：.env.example"
        )
    return val


# ── 隊伍設定 ─────────────────────────────────────────────────────────────────
TEAM_ID      = "TEAM_11718"        # ⚠️ 改成主辦方發給你們的真實代號
INITIAL_NAV  = 1_000_000_000     # 初始本金 10 億（確認後再改）

# ── 路徑設定 ─────────────────────────────────────────────────────────────────
SCHEMA_PATH      = "參考資料/D-Plan.schema.json"
UNIVERSE_PATH    = "data/stock_universe.json"
HOLDINGS_PATH    = "data/holdings.json"
DPLAN_DIR        = "dplans/"
PROMPT_TEMPLATE  = "prompts/daily_prompt.md"
STRATEGY_RULES   = "prompts/strategy_rules.txt"
ETF_PROSPECTUS   = "prompts/etf_prospectus.txt"

# ── 競賽規則（請勿修改）──────────────────────────────────────────────────────
MAX_HOLDINGS     = 30
MIN_HOLDINGS     = 20
TSMC_TICKER      = "2330"
TSMC_MAX_WEIGHT  = 0.25
OTHER_MAX_WEIGHT = 0.10
CASH_MIN         = 0.00
CASH_MAX         = 0.24          # 留 1% 緩衝，官方上限是 25%
FEE_RATE         = 0.001425      # 買賣手續費
TAX_RATE         = 0.003         # 賣出證交稅

# ── 策略參數 ─────────────────────────────────────────────────────────────────
NO_TRADE_BAND    = 0.015         # 目標權重差距 < 1.5% → 不調倉（省費用）
