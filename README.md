# 玉山 AI 競技場 — 主動型 ETF 量化 Agent

> **2026 玉山 AI 競技場「主動型 ETF 基金管理」賽道**
> 以 LangGraph 驅動的多節點量化 AI Agent，整合技術分析、新聞情緒、組合最佳化，自動產出符合賽制規範的 D-Plan 並提交。

---

## 目錄

- [比賽說明](#比賽說明)
- [團隊分工](#團隊分工)
- [快速開始](#快速開始)
- [專案結構](#專案結構)
- [Agent 架構](#agent-架構)
- [資料來源](#資料來源)
- [每日工作流程](#每日工作流程)
- [設定說明](#設定說明)
- [協作指南](#協作指南)
- [常見問題](#常見問題)

---

## 比賽說明

| 項目 | 內容 |
|---|---|
| 主辦 | 玉山銀行 |
| 賽制 | 主動型 ETF 基金管理 |
| 指標 | **累積淨利最大化** |
| 初始資金 | 10 億 TWD |
| 持股限制 | 20–30 檔；2330 最高 25%；其他個股最高 10% |
| 現金限制 | 0–25% |
| 提交格式 | D-Plan JSON（schema v4.1，7 層結構） |
| 官方網站 | https://esun-ai-challenge.tw/events/fundaiagent |

---

## 團隊分工

| 角色 | 負責內容 |
|---|---|
| **程式端（你）** | Agent 架構、資料抓取、程式整合、與 AI 協作 |
| **策略端（朋友）** | 調倉邏輯、ETF 投資主題、Black-Litterman 觀點輸入 |

> **策略端需填寫的檔案：**
> - `prompts/etf_prospectus.txt` — ETF 公開說明書（**10/21 截止**）
> - `prompts/strategy_rules.txt` — 每日調倉規則
> - `run_daily.py` 中的 `bl_views` — 個股主觀觀點（每天更新）

---

## 快速開始

### 1. 克隆專案 & 建立虛擬環境

```bash
git clone <your-repo-url>
cd "Investment AI CUP"

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 2. 安裝依賴

```bash
pip install -r requirements.txt
```

> 如果沒有 `requirements.txt`，手動安裝：
> ```bash
> pip install langgraph langchain-google-genai PyPortfolioOpt \
>             yfinance feedparser trafilatura requests scipy \
>             python-dotenv
> ```

### 3. 設定 API Key

```bash
# 複製範本
cp .env.example .env

# 用編輯器打開 .env，填入真實的 Key
# GOOGLE_API_KEY=your-gemini-api-key   ← 必填
# FUGLE_API_KEY=your-fugle-api-key     ← 選填（盤前報價用）
# ESUN_API_TOKEN=your-token            ← 開賽後填入
```

- **Gemini API Key**：從 [Google AI Studio](https://aistudio.google.com/apikey) 取得，或使用主辦方提供的 Token Credit（參閱 `參考資料/玉山挑戰賽_Google Token Credit領取方式_v1.pdf`）
- **Fugle API Key**：前往 [developer.fugle.tw](https://developer.fugle.tw/) 免費申請

### 4. 設定隊伍代號

```python
# agent/config.py
TEAM_ID = "TEAM_XXX"   # ← 改成主辦方發給你們的真實代號
```

### 5. 驗證安裝

```bash
python -c "from agent.graph import build_graph; build_graph(); print('✅ 安裝成功')"
```

---

## 專案結構

```
Investment AI CUP/
│
├── agent/                      # 核心模組
│   ├── config.py               # ⚙️  全域設定 + .env 載入（唯一讀取 API Key 之處）
│   ├── graph.py                # 🧠 LangGraph 主幹（7 節點 Agent）
│   ├── fetcher.py              # 📥 TWSE / TAIFEX / Yahoo Finance 資料抓取
│   ├── twse_extra.py           # 📊 融資融券 / 月營收 / 估值 / 外資持股
│   ├── news_scraper.py         # 📰 全文新聞抓取 + 加權情緒分析
│   ├── fugle_client.py         # ⚡ 盤前試撮 / 即時報價 / 大單偵測
│   ├── history.py              # 📈 90 日歷史資料（yfinance + 快取）
│   ├── technicals.py           # 📐 技術指標（RSI / MACD / EMA / ATR）
│   ├── optimizer.py            # 💼 PyPortfolioOpt + Black-Litterman 最佳化
│   ├── sentiment.py            # 💬 RSS 基礎情緒分析（news_scraper 備援）
│   ├── ledger.py               # 🏦 帳務系統（NAV / 持股 / 官方委託公式）
│   ├── validator.py            # ✅ D-Plan 雙層驗證（Schema + 引用鏈）
│   ├── submitter.py            # 🚀 提交到比賽系統
│   └── ai_bridge.py            # 🔌 Prompt 組裝 / AI 回應解析（備援）
│
├── prompts/
│   ├── etf_prospectus.txt      # 📋 ETF 公開說明書（策略師填寫，10/21 截止）
│   ├── strategy_rules.txt      # 📋 調倉策略規則（策略師填寫）
│   └── daily_prompt.md         # 📝 每日 AI Prompt 模板
│
├── data/
│   ├── holdings.json           # 💰 當前持股狀態（程式自動維護）
│   └── snapshots/              # 📦 每日快取（自動生成，不需 commit）
│
├── dplans/                     # 📄 每日 D-Plan 輸出（自動生成）
│
├── 參考資料/                    # 📚 主辦方提供的官方文件
│   ├── D-Plan_撰寫指南.md
│   ├── D-Plan.schema.json
│   └── *.pdf / *.docx
│
├── run_daily.py                # 🏃 每日執行入口（早上跑這個）
├── settle_daily.py             # 🏁 日終結算（收盤後跑這個）
├── AGENT_ARCHITECTURE.md       # 📖 Agent 詳細架構說明
├── .env                        # 🔑 API Key（不 commit）
├── .env.example                # 🔑 API Key 範本（可 commit）
└── .gitignore
```

---

## Agent 架構

Agent 採用 **LangGraph 有向圖**，資料在 7 個節點之間流動：

```
                         AgentState（共享狀態）
                               │
    ┌──────────────────────────▼──────────────────────────┐
    │                                                     │
    │  Node 1: fetch_data                                 │
    │  ├─ TWSE 收盤價、三大法人                             │
    │  ├─ TAIFEX 台指期夜盤                                │
    │  ├─ Yahoo Finance（SOX / ADR / VIX）                │
    │  ├─ 融資融券餘額、PE 估值、月營收（twse_extra）        │
    │  └─ 新聞全文抓取 + 情緒分析（news_scraper）           │
    │               │                                     │
    │  Node 2: analyze_tech                               │
    │  └─ RSI / MACD / 動能 / EMA / ATR（純 pandas）      │
    │               │                                     │
    │  Node 3: analyze_news                               │
    │  └─ 格式化情緒分數供後續節點使用                       │
    │               │                                     │
    │  Node 4: optimize_port                              │
    │  └─ PyPortfolioOpt Max Sharpe + Black-Litterman     │
    │     （策略師觀點 bl_views 在此注入）                  │
    │               │                                     │
    │  Node 5: generate_dplan    ← Gemini 2.0 Flash       │
    │  └─ 生成 observations / market_view /               │
    │     inferences / decisions / no_trade_decisions     │
    │               │                                     │
    │  Node 6: compute_orders                             │
    │  └─ 官方公式：floor(w × NAV ÷ price ÷ 1000) × 1000  │
    │               │                                     │
    │  Node 7: validate                                   │
    │  ├─ Schema 驗證                                     │
    │  └─ 引用鏈驗證（孤兒決策 → 自動重試 Node 5）          │
    │                                                     │
    └─────────────────────────────────────────────────────┘
                               │
                    D-Plan JSON 輸出 & 提交
```

**自動重試機制**：Node 7 發現引用鏈斷裂（孤兒決策）等致命錯誤時，自動回到 Node 5 重生成（最多 2 次），確保不被主辦方拒收。

詳細說明請參閱：[AGENT_ARCHITECTURE.md](./AGENT_ARCHITECTURE.md)

---

## 資料來源

### 免費 / 零設定

| 模組 | 資料 | 更新頻率 |
|---|---|---|
| `fetcher.py` | TWSE 收盤價、三大法人買賣超 | 每日盤後 |
| `fetcher.py` | TAIFEX 台指期夜盤收盤 | 每日凌晨 |
| `fetcher.py` | Yahoo Finance（SOX / ADR / VIX） | 每日（美股收盤後）|
| `twse_extra.py` | 融資融券餘額 | 每日盤後 |
| `twse_extra.py` | PE / 殖利率 / PB 估值 | 每日盤後 |
| `twse_extra.py` | 月營收（MOPS） | 每月 10 日前 |
| `twse_extra.py` | 外資持股比例 | 每日盤後 |
| `news_scraper.py` | 鉅亨網 RSS + 全文抓取 | 即時 |
| `history.py` | 90 日歷史收盤（yfinance） | 每日（快取）|

### 需申請（免費）

| 模組 | 服務 | 功能 |
|---|---|---|
| `fugle_client.py` | [Fugle MarketData](https://developer.fugle.tw/) | 盤前試撮、Tick 明細、大單偵測 |

---

## 每日工作流程

```
[每個交易日]

05:30  python run_daily.py
         ↓
       Node 1–4：自動抓資料、算指標、最佳化
         ↓
       Node 5：Gemini AI 生成 D-Plan 敘事
         ↓
       Node 6–7：計算委託、驗證
         ↓
       確認提交（輸入 y）
         ↓
       D-Plan 送出 ✅

[收盤後]

15:30  python settle_daily.py
         ↓
       輸入各股成交均價
         ↓
       更新 data/holdings.json（NAV + 持股）
```

### 策略師每日需做的事

在 `run_daily.py` 中更新 `bl_views`（Black-Litterman 主觀觀點）：

```python
bl_views = {
    "2330": 0.15,   # 預期台積電未來一個月年化報酬 15%
    "2891": 0.08,   # 預期中信金年化報酬 8%
    # 不填 = 純量化最佳化（也可以）
}
```

---

## 設定說明

### `.env` — API Key 管理

| 變數 | 說明 | 必填 |
|---|---|---|
| `GOOGLE_API_KEY` | Gemini AI（Node 5 使用）| ✅ 必填 |
| `FUGLE_API_KEY` | 盤前試撮 / 即時報價 | 選填 |
| `ESUN_API_TOKEN` | 比賽提交 Token | ✅ 開賽後必填 |

> 所有 Key 統一在 `.env` 管理，程式從 `agent/config.py` 讀取，**不需要手動 `export`**。

### `agent/config.py` — 競賽參數

```python
TEAM_ID          = "TEAM_XXX"   # ← 改成你們的真實代號
NO_TRADE_BAND    = 0.015        # 無交易帶（差距 < 1.5% 不調倉）
TSMC_MAX_WEIGHT  = 0.25         # 2330 上限（競賽規則）
OTHER_MAX_WEIGHT = 0.10         # 其他個股上限
```

---

## 協作指南

### 克隆後的首次設定

```bash
# 1. 建立虛擬環境
python3 -m venv .venv && source .venv/bin/activate

# 2. 安裝依賴
pip install langgraph langchain-google-genai PyPortfolioOpt \
            yfinance feedparser trafilatura requests scipy python-dotenv

# 3. 設定 API Key
cp .env.example .env
# 在 .env 填入 GOOGLE_API_KEY

# 4. 設定隊伍代號（agent/config.py）
# TEAM_ID = "TEAM_XXX"  ← 改成真實代號

# 5. 驗證
python -c "from agent.graph import build_graph; build_graph(); print('✅ 就緒')"
```

### 程式端（不需要改的部分）

- `agent/` 下所有 Python 模組
- `run_daily.py` 的主要邏輯（只需要改 `bl_views`）
- `settle_daily.py`

### 策略端（需要填寫的部分）

- `prompts/etf_prospectus.txt` — ETF 投資主題說明（**10/21 截止**）
- `prompts/strategy_rules.txt` — 調倉規則（如：SOX 跌 > 1.5% → risk_off）
- `run_daily.py` 的 `bl_views` — 每日主觀觀點

### Git 分支建議

```
main          ← 穩定版（每天提交成功的 D-Plan 後合併）
dev/策略更新   ← 策略師更新 prompts/strategy_rules.txt
dev/功能開發   ← 程式端新功能
```

### 不要 commit 的檔案（已加入 `.gitignore`）

- `.env` — 含 API Key，絕對不能上傳
- `data/snapshots/` — 自動重抓，不需追蹤
- `dplans/` — 可選擇性 commit（建議留存）

---

## 常見問題

**Q: 第一次執行需要多久？**
A: Node 1 會從 yfinance 下載 90 天歷史資料，約需 2–3 分鐘。之後每天使用快取，約 30 秒完成。

**Q: `GOOGLE_API_KEY` 從哪裡取得？**
A: 使用主辦方提供的 Google Token Credit（見 `參考資料/玉山挑戰賽_Google Token Credit領取方式_v1.pdf`），或前往 [aistudio.google.com/apikey](https://aistudio.google.com/apikey) 申請。

**Q: PyPortfolioOpt 最佳化失敗怎麼辦？**
A: 自動 fallback 到等權重分配，不中斷流程。

**Q: D-Plan 的 `schema_version` 該填 `4.0` 還是 `4.1`？**
A: 開賽前請向主辦方確認，然後在 `agent/graph.py` 修改 `"schema_version"` 的值。

**Q: 驗證失敗（引用鏈斷裂）？**
A: Agent 會自動重試（最多 2 次）。若持續失敗，可查看 `dplans/*_draft.json` 診斷問題。

**Q: `pandas-ta` 安裝失敗？**
A: 專案使用純 pandas 實作的 `agent/technicals.py`，不依賴 `pandas-ta`，Python 3.14 完全相容。

**Q: 如何新增技術指標？**
A: 在 `agent/technicals.py` 的 `compute_signals()` 中新增，並在 `compute_momentum_score()` 調整權重。

**Q: 如何調整最佳化的積極程度？**
A: 在 `run_daily.py` 呼叫 `run_agent()` 時傳入 `risk_aversion` 參數（`< 1` 偏積極，`> 1` 偏保守）。

---

## 技術棧

| 套件 | 用途 |
|---|---|
| `langgraph` | 多節點 Agent 框架 |
| `langchain-google-genai` | Gemini 2.0 Flash 整合 |
| `PyPortfolioOpt` | Mean-Variance / Black-Litterman 最佳化 |
| `yfinance` | 歷史股價資料 |
| `feedparser` | RSS 新聞抓取 |
| `trafilatura` | 新聞全文擷取 |
| `scipy` | 數值運算 |
| `requests` | TWSE / Fugle API 呼叫 |
| `python-dotenv` | `.env` 管理 |

---

## 授權

本專案為競賽用途，請勿對外公開比賽策略內容。
