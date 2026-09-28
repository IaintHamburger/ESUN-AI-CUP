## 整體架構一覽

```
每日 05:30
    │
    ▼
run_daily.py
    │
    ▼
agent/graph.py ── LangGraph 主幹（7個節點）
    │
    ├─ Node 1: fetch_data       → TWSE + TAIFEX + Yahoo + 新聞 + 融資融券 + 月營收 + 估值
    ├─ Node 2: analyze_tech     → pandas 技術指標（RSI/MACD/動能/ATR）
    ├─ Node 3: analyze_news     → trafilatura 全文新聞 + 加權情緒分析
    ├─ Node 4: optimize_port    → PyPortfolioOpt（Mean-Variance + Black-Litterman）
    ├─ Node 5: generate_dplan   → Gemini AI 生成 D-Plan 敘事（含融資/估值/新聞）
    ├─ Node 6: compute_orders   → 官方公式計算委託股數
    └─ Node 7: validate         → 雙層驗證（有錯誤 → 回到 Node 5 重試，最多 2 次）
    │
    ▼
dplans/D-Plan_TEAM_XXX_YYYY-MM-DD.json
    │
    ▼
submitter.py → 提交到比賽系統
    │
    ▼
（收盤後）settle_daily.py → 更新帳務 NAV
```

---

## 完整資料來源 & 插件清單

### 免費 / 官方（零設定）

| 模組 | 資料來源 | 資料內容 | D-Plan authority |
|---|---|---|---|
| `fetcher.py` | TWSE OpenAPI | 收盤價量、三大法人買賣超 | `twse` |
| `fetcher.py` | Yahoo Finance（yfinance）| SOX / NDX / SPX / TSM ADR / VIX | `vendor` |
| `fetcher.py` | TAIFEX | 台指期夜盤 | `taifex` |
| `twse_extra.py` | TWSE OpenAPI | **融資融券餘額** | `twse` |
| `twse_extra.py` | TWSE OpenAPI | **PE / 殖利率 / PB 估值** | `twse` |
| `twse_extra.py` | TWSE OpenAPI | **外資持股比例** | `twse` |
| `twse_extra.py` | MOPS 公開資訊觀測站 | **月營收** | `mops` |
| `twse_extra.py` | TWSE OpenAPI | 大盤成交量能 | `twse` |
| `news_scraper.py` | 鉅亨網 RSS（feedparser）| 新聞標題 | `media` |
| `news_scraper.py` | trafilatura | **新聞全文擷取** | `media` |
| `history.py` | Yahoo Finance（yfinance）| 90日歷史收盤（優化用）| `vendor` |

### 需申請（免費帳號）

| 模組 | 服務 | 資料內容 | 設定方式 |
|---|---|---|---|
| `fugle_client.py` | **Fugle MarketData API** | 盤前試撮、即時報價、Tick 明細、委買委賣、大單偵測 | `export FUGLE_API_KEY=...` |

**Fugle API 申請步驟：**
1. 前往 https://developer.fugle.tw/ 免費註冊
2. 申請 MarketData API Key（免費方案：每分鐘 60 次）
3. `export FUGLE_API_KEY='your-key'`
4. 執行 `python -c "from agent.fugle_client import test_connection; test_connection()"`

### 需付費（暫不整合）

| 服務 | 提供內容 | 月費 |
|---|---|---|
| CMoney API | 更完整基本面資料庫 | NT$200–500 |
| 永豐 Shioaji | 實單下單 API（非比賽用）| 需開戶 |
| Bloomberg / Refinitiv | 機構級資料 | 昂貴 |

---

## 各模組詳細說明

### `agent/graph.py` — LangGraph 主幹

**LangGraph 是什麼？**
LangGraph 是一個有向圖形框架，讓你把複雜的 AI 流程拆成獨立「節點」（Node），每個節點讀取 State、做一件事、把結果寫回 State，再傳給下一個節點。

**State（狀態）的作用：**
整個 Agent 共享一個 `AgentState` 字典，貫穿所有節點。每個節點只需要知道自己要讀哪些欄位、寫哪些欄位。

```
AgentState = {
    # 輸入（你提供）
    trade_date, nav, cash, holdings, strategy_rules, etf_prospectus, bl_views

    # Node 1 填充
    prices, institutional, us_market, night_futures, headlines, sources

    # Node 2 填充
    technical_signals, momentum_scores

    # Node 3 填充
    sentiment

    # Node 4 填充
    target_weights, weight_changes

    # Node 5 填充（AI 生成）
    observations, market_view, inferences, decisions, no_trade_decisions

    # Node 6 填充
    orders

    # Node 7 填充
    errors, attempt
}
```

**自動重試機制：**
Node 7（驗證）發現「孤兒決策」等致命錯誤時，會自動回到 Node 5 重試（最多 2 次），確保提交的 D-Plan 不被主辦方拒收。

---

### `agent/technicals.py` — 技術指標

**計算哪些指標？**

| 指標 | 用途 | 公式 |
|---|---|---|
| RSI(14) | 超買/超賣偵測 | 14日平均漲跌比 |
| 動能 5/20 日 | 短中期趨勢方向 | `(今日 - N日前) / N日前 * 100%` |
| EMA 20/60 | 趨勢判斷 | 指數移動平均，短 > 長 = 上升趨勢 |
| MACD Histogram | 動能轉折點 | EMA(12) - EMA(26) - EMA(signal) |
| 布林帶 %B | 價格相對位置 | `(收盤 - 下軌) / (上軌 - 下軌)` |
| ATR(14) | 波動率 | 真實區間 14 日均值 |

**動能分數計算：**
把 6 個指標加權合成 `-1（極弱）` 到 `+1（極強）` 的分數：

```
score = 0.25 * 動能5日 + 0.20 * 動能20日
      + 0.15 * RSI評分 + 0.20 * 趨勢
      + 0.10 * MACD    + 0.10 * 布林帶
```

這個分數直接傳入 PyPortfolioOpt，影響目標權重的高低。

---

### `agent/optimizer.py` — 投資組合最佳化

**兩層優化機制：**

```
1. 純量化（無觀點）：
   歷史報酬均值 + 共變異數矩陣 → Max Sharpe → 目標權重

2. 加入策略師觀點（Black-Litterman）：
   策略師 views = {"2330": 0.12}  ← 「我認為台積電年化報酬 12%」
       ↓
   Black-Litterman 模型（融合主觀觀點 + 市場均衡）
       ↓
   調整後的預期報酬 → Max Sharpe → 目標權重
```

**Black-Litterman 的好處：**
不需要精確預測每一檔股票，只需要對少數有信心的股票提供觀點（例如台積電漲/跌），模型自動處理其他股票的配置。

**無交易帶（`NO_TRADE_BAND = 0.015`）：**
如果目標權重和現有權重差距 < 1.5%，不執行調倉。
效果：每週只對真正有顯著變化的股票才動手，避免頻繁換手吃掉費用。

---

### `agent/sentiment.py` — 新聞情緒分析

**運作流程：**
```
feedparser → 鉅亨網 RSS + Yahoo 財經 RSS
    ↓
每條新聞標題關鍵字掃描
    ↓
正面關鍵字（上修、訂單滿載、降息...）→ +score
負面關鍵字（下修、砍單、升息...）     → -score
    ↓
所有標題平均 → 整體市場情緒分數 (-1 ~ +1)
```

**情緒分數如何影響 D-Plan？**
1. 直接放入 `observations.values`（可查核的事實數字）
2. 傳給 Gemini，讓 AI 在 market_view 和 inferences 中引用

---

### `agent/history.py` — 歷史價格資料

**為什麼需要歷史資料？**
- PyPortfolioOpt 需要 60–90 天的歷史報酬率才能計算共變異數矩陣
- 技術指標（EMA60、RSI14）也需要足夠歷史資料

**快取機制：**
首次執行會從 yfinance 抓 90 天歷史，存到 `data/snapshots/`。
之後每天重複使用快取（自動判斷是否超過 1 天），避免重複下載。

---

### Gemini AI 在 Node 5 做什麼？

**輸入（傳給 AI 的 context）：**
- 市場數據摘要（SOX / ADR / VIX / 夜盤）
- 三大法人前10大外資買超
- 每檔持股的技術指標分數
- 新聞情緒分數與代表性標題
- PyPortfolioOpt 計算出的目標調倉清單
- ETF 公開說明書（你們的投資主題）
- 策略師規則

**AI 的任務（只做敘事，不做計算）：**
1. 寫 `observations`：把市場數字轉成 D-Plan 格式的事實陳述
2. 寫 `market_view.logic`：解釋為何是 risk_on / neutral / risk_off
3. 寫 `inferences`：解釋每個調倉決策背後的邏輯
4. 填 `decisions`：列出每個持股的 BUY/TRIM/SELL_ALL/HOLD

**AI 不做的事（由程式計算）：**
- 委託股數（Node 6 用官方公式計算）
- 目標權重（PyPortfolioOpt 決定）
- 驗證（Node 7 的 validator）

---

## 如何使用

### 1. 設定 API Key
```bash
export GOOGLE_API_KEY="你的 Gemini API Key"
# 從 https://aistudio.google.com/apikey 取得
```

### 2. 設定 team_id
```python
# agent/config.py
TEAM_ID = "TEAM_XXX"   # ← 改成你們的真實代號
```

### 3. 填入策略（朋友負責）
```
prompts/etf_prospectus.txt  ← ETF 投資主題（10/21 截止）
prompts/strategy_rules.txt  ← 調倉規則
```

### 4. 策略師的主觀觀點（選用）
```python
# run_daily.py 中修改
bl_views = {
    "2330": 0.15,   # 預期台積電年化 15%
    "2891": 0.10,   # 預期中信金年化 10%
}
```

### 5. 每天早上執行
```bash
# 啟動虛擬環境
source .venv/bin/activate

# 執行 Agent
python run_daily.py
```

### 6. 收盤後結算
```bash
python settle_daily.py
```

---

## 資料流圖（D-Plan 引用鏈如何自動建立）

```
Node 1 建立 sources:
  S1 (twse) → S2 (twse) → S3 (taifex) → S4 (vendor) → S5 (media)
                                │
                                ▼
Node 5 (AI) 建立 observations（引用 S1–S5）:
  O1 (tw_market ← S1)
  O2 (us_overnight ← S4)
  O3 (futures_night ← S3)
  O4 (technicals ← S1)    ← 技術指標數值直接嵌入
  O5 (news_sentiment ← S5) ← 情緒分數直接嵌入
                                │
                                ▼
Node 5 (AI) 建立 inferences（引用 O1–O5）:
  I1 (2330 減碼理由 ← O2, O4)
  I2 (2891 建倉理由 ← O1, O5)
                                │
                                ▼
Node 5 (AI) 建立 decisions（引用 I1–I2）:
  D1: TRIM 2330 → 0.23 (← I1)
  D2: BUY  2891 → 0.045 (← I2)
                                │
                                ▼
Node 6 (程式) 計算 orders（引用 D1–D2）:
  {ticker: 2330, side: SELL, shares: 2000, decision_ref: D1}
  {ticker: 2891, side: BUY,  shares: 108000, decision_ref: D2}
```

引用鏈完全自動化，不需要手動維護 ID 對應關係。

---

## 費用優化說明

| 機制 | 效果 |
|---|---|
| 無交易帶 1.5% | 差距小的股票不動，省手續費 |
| PyPortfolioOpt | 數學最佳化，避免過度分散 |
| 最多重試 2 次 | 保證 D-Plan 品質但不浪費 API 費用 |
| 每日快取歷史資料 | 避免重複抓取 90 天資料 |

---

## 常見問題

**Q: `GOOGLE_API_KEY` 從哪裡取得？**
A: 參考參考資料中的「玉山挑戰賽_Google Token Credit 領取方式.pdf」，使用主辦方提供的 Credit。

**Q: PyPortfolioOpt 計算失敗怎麼辦？**
A: 自動 fallback 到等權重分配，不會中斷流程。

**Q: D-Plan 的 schema_version 該填 4.0 還是 4.1？**
A: 目前參考資料範例寫 4.1，但 schema.json 定義是 4.0。請向主辦方確認後，在 `graph.py` 中修改 `"schema_version"` 的值。

**Q: 沒有 yfinance 歷史資料怎麼辦（第一天）？**
A: 第一次執行時程式會自動從 yfinance 抓 90 天歷史，約需 1–2 分鐘。

**Q: 如何加入新的技術指標？**
A: 在 `agent/technicals.py` 的 `compute_signals()` 函數中新增，並在 `compute_momentum_score()` 調整權重。
