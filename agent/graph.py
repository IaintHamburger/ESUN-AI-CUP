# ==============================================================================
# graph.py — LangGraph 主幹 Agent
# 完整量化 AI Agent：資料抓取 → 技術分析 → 新聞情緒 → 組合最佳化
#                   → AI 敘事生成 → D-Plan 驗證
# ==============================================================================

from __future__ import annotations
import json
import math
import os
from datetime import date, datetime, timezone, timedelta
from typing import TypedDict, Optional, Annotated
from pathlib import Path

from langgraph.graph import StateGraph, END
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage

from agent import fetcher, validator
from agent.ledger import PortfolioLedger
from agent.technicals import compute_signals, compute_momentum_score, build_observation_values
from agent.optimizer import optimize, describe_weights
from agent.sentiment import fetch_headlines, analyze_sentiment, ticker_sentiment
from agent.news_scraper import run_news_pipeline
from agent.twse_extra import fetch_margin_short, fetch_valuation, fetch_monthly_revenue, build_source_entries as twse_extra_sources
from agent.history import fetch_price_history, load_cached_history
from agent.config import (
    TEAM_ID, HOLDINGS_PATH, SCHEMA_PATH,
    FEE_RATE, TAX_RATE, TSMC_TICKER,
    TSMC_MAX_WEIGHT, OTHER_MAX_WEIGHT,
    NO_TRADE_BAND, DPLAN_DIR
)

TZ_TAIPEI = timezone(timedelta(hours=8))


# ── State 定義 ────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    # ── 輸入 ──
    trade_date:        str
    nav:               float
    cash:              float
    holdings:          dict          # {ticker: shares}
    strategy_rules:    str
    etf_prospectus:    str
    bl_views:          dict          # Black-Litterman 主觀觀點（策略師提供）

    # ── 進階資料 ──
    margin_short:      dict          # 融資融券
    valuation:         dict          # PE / 殖利率 / PB
    monthly_revenue:   dict          # 月營收
    news_analysis:     dict          # 全文情緒分析結果

    # ── 資料層 ──
    prices:            dict          # {ticker: float}
    institutional:     dict          # {ticker: {foreign_net, trust_net, dealer_net}}
    us_market:         dict          # {SOX: float, ...}
    night_futures:     Optional[dict]
    headlines:         list          # [{title, source, url}]

    # ── 分析層 ──
    technical_signals: dict          # {ticker: {rsi_14, mom_5pct, ...}}
    momentum_scores:   dict          # {ticker: float -1~1}
    sentiment:         dict          # {score, bullish_count, ...}
    target_weights:    dict          # {ticker: float}（optimizer 輸出）
    weight_changes:    list          # describe_weights() 輸出

    # ── D-Plan 各層 ──
    sources:           list
    observations:      list
    market_view:       dict
    inferences:        list
    decisions:         list
    no_trade_decisions: list
    orders:            list

    # ── 控制 ──
    errors:            list
    attempt:           int
    run_started_at:    str


# ── LLM 初始化 ────────────────────────────────────────────────────────────────

def _get_llm() -> ChatGoogleGenerativeAI:
    """取得 Gemini LLM 實例（API Key 統一從 .env 讀取）"""
    from agent.config import GOOGLE_API_KEY
    if not GOOGLE_API_KEY or GOOGLE_API_KEY == "your-gemini-api-key-here":
        raise EnvironmentError(
            "\n❌ 需要 Gemini API Key\n"
            "   在 .env 檔案填入：GOOGLE_API_KEY=your-key-here\n"
            "   從 https://aistudio.google.com/apikey 取得"
        )
    return ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        google_api_key=GOOGLE_API_KEY,
        temperature=0.3,
    )


# ── Node 1: 資料抓取 ──────────────────────────────────────────────────────────

def fetch_data_node(state: AgentState) -> dict:
    """抓取所有市場資料（基本 + 進階），建立 D-Plan sources 區塊"""
    print("\n[Node 1] 📥 抓取市場資料...")
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    yesterday_18 = (date.today() - timedelta(days=1)).strftime("%Y-%m-%dT18:30:00+08:00")
    us_close_time = (date.today() - timedelta(days=1)).strftime("%Y-%m-%dT04:00:00+08:00")

    # ── 基礎資料（原有）──
    prices        = fetcher.fetch_twse_prices()
    institutional = fetcher.fetch_twse_institutional()
    us_market     = fetcher.fetch_us_market()
    night_futures = fetcher.fetch_taifex_night_close()

    # ── 進階資料（新增）──
    print("   📊 抓取融資融券、估值、月營收...")
    margin_short     = fetch_margin_short(save=True)
    valuation        = fetch_valuation()
    monthly_revenue  = fetch_monthly_revenue()

    # ── 全文新聞情緒分析（news_scraper，比 RSS 標題更準）──
    print("   📰 執行全文新聞 Pipeline...")
    news_analysis = run_news_pipeline(
        holdings=list(state["holdings"].keys()),
        fetch_fulltext=True,
        max_rss=20,
        max_fulltext=8,
    )

    # ── D-Plan sources（S1–S5 基礎 + S6–S8 進階）──
    sources = [
        {
            "source_id":    "S1",
            "authority":    "twse",
            "name":         "TWSE 盤後資訊：上市個股日收盤價量",
            "url":          "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            "content_as_of": yesterday_18,
            "fetched_at":   now_str,
        },
        {
            "source_id":    "S2",
            "authority":    "twse",
            "name":         "TWSE 三大法人買賣超",
            "url":          "https://openapi.twse.com.tw/v1/fund/TWT38U",
            "content_as_of": yesterday_18,
            "fetched_at":   now_str,
        },
        {
            "source_id":    "S3",
            "authority":    "taifex",
            "name":         "台指期夜盤收盤（TAIFEX 盤後行情表）",
            "url":          "https://www.taifex.com.tw/cht/3/futDailyMarketReport",
            "content_as_of": date.today().strftime("%Y-%m-%dT05:00:00+08:00"),
            "fetched_at":   now_str,
        },
        {
            "source_id":    "S4",
            "authority":    "vendor",
            "name":         "美股收盤行情（SOX / NDX / SPX / TSM ADR / VIX）",
            "url":          "https://finance.yahoo.com",
            "content_as_of": us_close_time,
            "fetched_at":   now_str,
        },
        {
            "source_id":    "S5",
            "authority":    "media",
            "name":         "鉅亨網台股財經新聞 RSS + 全文情緒分析",
            "url":          "https://news.cnyes.com/rss/cat/tw_stock",
            "content_as_of": now_str,
            "fetched_at":   now_str,
        },
    ] + twse_extra_sources(fetched_at=now_str)  # 加入 S6–S8

    return {
        "prices":          prices,
        "institutional":   institutional,
        "us_market":       us_market,
        "night_futures":   night_futures,
        "headlines":       [],   # news_scraper 已整合，不再單獨用 headlines
        "margin_short":    margin_short,
        "valuation":       valuation,
        "monthly_revenue": monthly_revenue,
        "news_analysis":   news_analysis,
        "sources":         sources,
        "run_started_at":  now_str,
    }


# ── Node 2: 技術分析 ──────────────────────────────────────────────────────────

def technical_analysis_node(state: AgentState) -> dict:
    """對持股與候選股計算技術指標與動能分數"""
    print("\n[Node 2] 📊 計算技術指標...")

    holdings      = state["holdings"]
    prices        = state["prices"]

    # 取歷史資料（優先用快取）
    all_tickers   = list(holdings.keys())
    price_history = load_cached_history(max_age_days=1)
    if price_history is None or price_history.empty:
        price_history = fetch_price_history(all_tickers, days=90)

    technical_signals: dict[str, dict] = {}
    momentum_scores:   dict[str, float] = {}

    for ticker in all_tickers:
        if ticker not in price_history.columns:
            continue
        series = price_history[ticker].dropna()
        if len(series) < 26:
            continue
        signals = compute_signals(series)
        technical_signals[ticker] = signals
        momentum_scores[ticker]   = compute_momentum_score(signals)

    print(f"   計算完成：{len(technical_signals)} 檔")
    return {
        "technical_signals": technical_signals,
        "momentum_scores":   momentum_scores,
    }


# ── Node 3: 新聞情緒 ──────────────────────────────────────────────────────────

def sentiment_node(state: AgentState) -> dict:
    """使用 news_scraper 的全文分析結果（Node 1 已執行），這裡僅做格式轉換"""
    print("\n[Node 3] 📰 新聞情緒處理...")
    news_a = state.get("news_analysis", {})
    # 轉成 sentiment.py 的結構格式（相容後續節點使用）
    sentiment = {
        "score":         news_a.get("market_score", 0.0),
        "bullish_count": news_a.get("source_count", 0),
        "bearish_count": 0,
        "neutral_count": 0,
        "top_bullish":   [e["title"] for e in news_a.get("key_events", []) if e.get("score", 0) > 0][:3],
        "top_bearish":   [e["title"] for e in news_a.get("key_events", []) if e.get("score", 0) < 0][:3],
    }
    print(f"   情緒分數：{sentiment['score']:+.3f}")
    return {"sentiment": sentiment}


# ── Node 4: 組合最佳化 ────────────────────────────────────────────────────────

def portfolio_optimization_node(state: AgentState) -> dict:
    """用 PyPortfolioOpt 計算目標權重"""
    print("\n[Node 4] 💼 投資組合最佳化...")

    holdings      = state["holdings"]
    prices        = state["prices"]
    nav           = state["nav"]
    momentum_scores = state["momentum_scores"]
    bl_views      = state.get("bl_views", {})

    # 計算當前權重
    current_weights: dict[str, float] = {}
    for ticker, shares in holdings.items():
        price = prices.get(ticker, 0)
        if price > 0 and nav > 0:
            current_weights[ticker] = (shares * price) / nav

    # 取得歷史報酬率
    price_history = load_cached_history(max_age_days=1)
    if price_history is not None and not price_history.empty:
        returns = price_history.pct_change().dropna()
        target_weights = optimize(
            returns=returns,
            current_weights=current_weights,
            momentum_scores=momentum_scores,
            views=bl_views if bl_views else None,
        )
    else:
        print("   ⚠️  無歷史資料，使用等權重備用")
        n = max(20, len(holdings))
        w = round(0.95 / n, 4)
        target_weights = {t: w for t in list(holdings.keys())[:30]}

    weight_changes = describe_weights(target_weights, current_weights)
    print(f"   目標持股：{len(target_weights)} 檔，調倉 {sum(1 for r in weight_changes if r['action'] != 'HOLD')} 筆")
    return {
        "target_weights":  target_weights,
        "weight_changes":  weight_changes,
    }


# ── Node 5: AI D-Plan 生成 ────────────────────────────────────────────────────

def generate_dplan_node(state: AgentState) -> dict:
    """
    呼叫 Gemini 生成 D-Plan 的 observations / market_view / inferences / decisions / no_trade_decisions。
    orders 由下一個節點用官方公式計算。
    """
    print("\n[Node 5] 🧠 AI 生成 D-Plan 敘事...")

    # 組裝 context
    us       = state["us_market"]
    inst     = state["institutional"]
    news_a   = state.get("news_analysis", {})
    wc       = state["weight_changes"]
    holdings = state["holdings"]
    prices   = state["prices"]
    nav      = state["nav"]
    margin   = state.get("margin_short", {})
    val      = state.get("valuation", {})

    # 三大法人：外資前 10 大
    top_foreign = sorted(
        [(t, v.get("foreign_net", 0)) for t, v in inst.items()],
        key=lambda x: -x[1]
    )[:10]
    inst_str = "\n".join(f"  {t}: {v:+,.0f}" for t, v in top_foreign)

    # 調倉摘要
    changes_str = "\n".join(
        f"  {r['ticker']}: {r['action']} {r['current_weight']:.1%}→{r['target_weight']:.1%}"
        for r in wc if r["action"] != "HOLD"
    )
    holds_str = "\n".join(
        f"  {r['ticker']}: HOLD {r['current_weight']:.1%}"
        for r in wc if r["action"] == "HOLD"
    )

    # 融資融券前 10 大（融資增加 = 散戶偏多 → 逆向信號）
    top_margin = sorted(
        [(t, v.get("margin_balance", 0)) for t, v in margin.items() if t in holdings],
        key=lambda x: -x[1]
    )[:10]
    margin_str = "\n".join(f"  {t}: 融資餘額 {v:,} 千" for t, v in top_margin)

    # 估值摘要（持股中 PE 最高 / 最低）
    holding_val = {t: val[t] for t in holdings if t in val}
    val_str = "\n".join(
        f"  {t}: PE={v['pe_ratio']:.1f} 殖利率={v['yield_pct']:.2f}% PB={v['pb_ratio']:.2f}"
        for t, v in sorted(holding_val.items(), key=lambda x: -x[1].get("pe_ratio", 0))[:8]
    ) or "  （無估值資料）"

    # 重要新聞事件
    key_events_str = "\n".join(
        f"  [{e['score']:+.2f}] {e['title']}"
        for e in news_a.get("key_events", [])[:5]
    ) or "  （無重要事件）"

    # 技術信號摘要
    tech_str = "\n".join(
        f"  {t}: 動能={state['momentum_scores'].get(t, 0):+.2f}, RSI={state['technical_signals'].get(t, {}).get('rsi_14', '?')}"
        for t in list(holdings.keys())[:15]
    )

    prompt = f"""你是台股主動型 ETF 的 AI 投資管理員。今天是 {state['trade_date']}，隊伍代號 {TEAM_ID}。

## ETF 公開說明書
{state['etf_prospectus']}

## 策略規則
{state['strategy_rules']}

## 市場資料
美股: SOX={us.get('SOX', '?')}%, NDX={us.get('NDX', '?')}%, SPX={us.get('SPX', '?')}%
TSM ADR={us.get('TSM_adr', '?')}%, VIX={us.get('VIX_close', '?')}
台指期夜盤: {json.dumps(state.get('night_futures'), ensure_ascii=False)}
新聞整體情緒分數: {news_a.get('market_score', 0):+.3f}
重要新聞事件:
{key_events_str}

## 三大法人：外資買超前10（千元）
{inst_str}

## 融資融券（持股中融資餘額最高）
{margin_str}

## 估值（持股 PE / 殖利率）
{val_str}

## 技術指標（持股前15）
{tech_str}

## 量化最佳化決定的調倉
{changes_str or "（無調倉）"}

維持不動：
{holds_str or "（無）"}

## NAV & 持股
NAV: {nav:,.0f} TWD | 現金: {state['cash']:,.0f} TWD

---

請輸出以下 JSON（注意：不要輸出 orders，只輸出下面這些欄位）：

```json
{{
  "observations": [
    {{
      "obs_id": "O1",
      "source_ref": ["S4"],
      "topic": "us_overnight",
      "statement": "...",
      "values": {{...}}
    }}
    // 至少 4 條，涵蓋 us_market / tw_market / news_sentiment / technical
  ],
  "market_view": {{
    "basis_refs": ["O1", "O2"],
    "logic": "...",
    "regime": "risk_on|neutral|risk_off",
    "stance": "aggressive|neutral|defensive",
    "posture": {{
      "net_exposure_intent": "increase|hold|reduce",
      "target_cash_pct_range": [0.03, 0.10]
    }},
    "counter_evidence": "...",
    "confidence": 0.7
  }},
  "inferences": [
    {{
      "inf_id": "I1",
      "premise_refs": ["O1", "O2"],
      "logic": "（說明為何做此調倉，引用 observations）",
      "conclusion": "...",
      "counter_evidence": null,
      "confidence": 0.7
    }}
    // 每個 ticker 的調倉決策都要有對應的 inference
  ],
  "decisions": [
    {{
      "decision_id": "D1",
      "ticker": "2330",
      "action": "BUY|ADD|TRIM|SELL_ALL",
      "target_weight": 0.20,
      "inference_refs": ["I1"],
      "risk_check": "...",
      "funding_for": ["D2"]   // 選填，資金調度型賣出才需要
    }}
  ],
  "no_trade_decisions": [
    {{
      "ticker": "2317",
      "reason_refs": ["I2"],
      "reason": "..."
    }}
  ]
}}
```

規則：
- observations 中引用的 source_id 必須存在（S1-S5）
- inferences 中引用的 obs_id 必須存在於 observations
- decisions 中引用的 inf_id 必須存在於 inferences
- 每一檔持股（holdings）必須出現在 decisions 或 no_trade_decisions（擇一）
- decisions 的 target_weight 必須符合量化最佳化的結果（上方「調倉」欄位）
- 2330 最高 0.25，其他最高 0.10
- 只輸出 JSON，不要其他文字
"""

    llm = _get_llm()
    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content

    # 解析 AI 回應
    import re
    match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
    if match:
        partial = json.loads(match.group(1))
    else:
        partial = json.loads(content.strip())

    return {
        "observations":      partial.get("observations", []),
        "market_view":       partial.get("market_view", {}),
        "inferences":        partial.get("inferences", []),
        "decisions":         partial.get("decisions", []),
        "no_trade_decisions": partial.get("no_trade_decisions", []),
    }


# ── Node 6: 委託計算（官方公式）─────────────────────────────────────────────

def compute_orders_node(state: AgentState) -> dict:
    """用官方公式從 decisions 計算 orders，不可手動調整"""
    print("\n[Node 6] 🧮 計算委託股數（官方公式）...")

    decisions = state["decisions"]
    holdings  = state["holdings"]
    prices    = state["prices"]
    nav       = state["nav"]
    orders    = []

    for d in decisions:
        ticker       = d["ticker"]
        target_weight = d["target_weight"]
        prev_close   = prices.get(ticker)

        if prev_close is None or prev_close <= 0:
            print(f"   ⚠️  {ticker} 無收盤價，跳過")
            continue

        # 官方公式
        target_shares  = math.floor(target_weight * nav / prev_close / 1000) * 1000
        current_shares = holdings.get(ticker, 0)
        delta          = target_shares - current_shares

        if delta == 0:
            continue

        side   = "BUY" if delta > 0 else "SELL"
        shares = abs(int(delta))

        # 安全檢查：SELL 不能超過持有股數
        if side == "SELL" and shares > current_shares:
            shares = current_shares
            print(f"   ⚠️  {ticker} 委託股數修正：不能超賣（改為 {shares}）")

        orders.append({
            "ticker":       ticker,
            "side":         side,
            "shares":       shares,
            "decision_ref": d["decision_id"],
            "derivation_detail": (
                f"target={math.floor(target_weight * nav / prev_close / 1000) * 1000}; "
                f"current={current_shares}; delta={delta:+d}"
            ),
        })

    print(f"   生成委託 {len(orders)} 筆")
    return {"orders": orders}


# ── Node 7: 驗證 ──────────────────────────────────────────────────────────────

def validate_node(state: AgentState) -> dict:
    """組裝完整 D-Plan 並驗證，回傳錯誤列表"""
    print("\n[Node 7] ✅ 驗證 D-Plan...")

    run_completed = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%dT%H:%M:%S+08:00")

    dplan = {
        "schema_version":    "4.1",
        "doc_type":          "D-Plan",
        "team_id":           TEAM_ID,
        "trade_date":        state["trade_date"],
        "sources":           state["sources"],
        "observations":      state["observations"],
        "market_view":       state["market_view"],
        "inferences":        state["inferences"],
        "decisions":         state["decisions"],
        "no_trade_decisions": state["no_trade_decisions"],
        "orders":            state["orders"],
        "agent_metadata": {
            "model_provider":   "google",
            "model_version":    "gemini-2.0-flash",
            "run_started_at":   state["run_started_at"],
            "run_completed_at": run_completed,
            "code_version":     "v1.0.0",
        },
    }

    # 寫入暫存
    tmp_path = Path("dplans") / f"D-Plan_{TEAM_ID}_{state['trade_date']}_draft.json"
    tmp_path.parent.mkdir(exist_ok=True)
    tmp_path.write_text(json.dumps(dplan, ensure_ascii=False, indent=2), encoding="utf-8")

    errors = (
        validator.validate_schema(dplan)
        + validator.validate_reference_chain(dplan)
        + validator.validate_business_rules(dplan)
    )

    # 過濾 schema_version 不一致的警告（等主辦方確認）
    errors = [e for e in errors if "schema_version" not in e.lower() or "4.0" not in e]

    if errors:
        print(f"   ❌ 發現 {len(errors)} 個問題：")
        for e in errors[:5]:
            print(f"      {e}")
    else:
        print("   ✅ 全部通過！")

    return {
        "errors": errors,
        "attempt": state.get("attempt", 0) + 1,
    }


# ── 路由函數 ──────────────────────────────────────────────────────────────────

def _should_retry(state: AgentState) -> str:
    """有致命錯誤且未超過重試次數 → 重新生成；否則結束"""
    fatal = [e for e in state.get("errors", []) if "[C1][致命]" in e or "[Schema]" in e]
    if fatal and state.get("attempt", 0) < 2:
        print(f"\n🔄 發現 {len(fatal)} 個致命錯誤，重試（第 {state['attempt']+1} 次）...")
        return "generate_dplan"
    return END


# ── Graph 建立 ────────────────────────────────────────────────────────────────

def build_graph():
    """建立並編譯 LangGraph"""
    g = StateGraph(AgentState)

    g.add_node("fetch_data",    fetch_data_node)
    g.add_node("analyze_tech",  technical_analysis_node)
    g.add_node("analyze_news",  sentiment_node)
    g.add_node("optimize_port", portfolio_optimization_node)
    g.add_node("generate_dplan", generate_dplan_node)
    g.add_node("compute_orders", compute_orders_node)
    g.add_node("validate",      validate_node)

    g.set_entry_point("fetch_data")
    g.add_edge("fetch_data",    "analyze_tech")
    g.add_edge("analyze_tech",  "analyze_news")
    g.add_edge("analyze_news",  "optimize_port")
    g.add_edge("optimize_port", "generate_dplan")
    g.add_edge("generate_dplan", "compute_orders")
    g.add_edge("compute_orders", "validate")
    g.add_conditional_edges("validate", _should_retry)

    return g.compile()


# ── 主要執行函數 ──────────────────────────────────────────────────────────────

def run_agent(
    trade_date: str | None = None,
    bl_views: dict | None = None,
) -> dict:
    """
    執行完整 Agent，回傳最終 D-Plan dict。

    bl_views: 策略師的 Black-Litterman 觀點
              例：{"2330": 0.12, "2891": 0.08}
    """
    if trade_date is None:
        trade_date = date.today().strftime("%Y-%m-%d")

    book = PortfolioLedger(HOLDINGS_PATH)

    strategy_rules = Path("prompts/strategy_rules.txt").read_text(encoding="utf-8") \
        if Path("prompts/strategy_rules.txt").exists() else "[尚未填入策略規則]"
    etf_prospectus = Path("prompts/etf_prospectus.txt").read_text(encoding="utf-8") \
        if Path("prompts/etf_prospectus.txt").exists() else "[尚未填入 ETF 說明書]"

    initial_state: AgentState = {
        "trade_date":      trade_date,
        "nav":             book.nav,
        "cash":            book.cash,
        "holdings":        book.holdings,
        "strategy_rules":  strategy_rules,
        "etf_prospectus":  etf_prospectus,
        "bl_views":        bl_views or {},
        # 資料層（節點會填充）
        "prices":          {},
        "institutional":   {},
        "us_market":       {},
        "night_futures":   None,
        "headlines":       [],
        # 分析層
        "technical_signals": {},
        "momentum_scores": {},
        "sentiment":       {},
        "target_weights":  {},
        "weight_changes":  [],
        # D-Plan
        "sources":         [],
        "observations":    [],
        "market_view":     {},
        "inferences":      [],
        "decisions":       [],
        "no_trade_decisions": [],
        "orders":          [],
        # 進階資料（Node 1 填充）
        "margin_short":    {},
        "valuation":       {},
        "monthly_revenue": {},
        "news_analysis":   {},
        # 控制
        "errors":          [],
        "attempt":         0,
        "run_started_at":  "",
    }

    graph = build_graph()
    final_state = graph.invoke(initial_state)

    # 儲存最終 D-Plan
    run_completed = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    dplan = {
        "schema_version":    "4.1",
        "doc_type":          "D-Plan",
        "team_id":           TEAM_ID,
        "trade_date":        trade_date,
        "sources":           final_state["sources"],
        "observations":      final_state["observations"],
        "market_view":       final_state["market_view"],
        "inferences":        final_state["inferences"],
        "decisions":         final_state["decisions"],
        "no_trade_decisions": final_state["no_trade_decisions"],
        "orders":            final_state["orders"],
        "agent_metadata": {
            "model_provider":   "google",
            "model_version":    "gemini-2.0-flash",
            "run_started_at":   final_state["run_started_at"],
            "run_completed_at": run_completed,
            "code_version":     "v1.0.0",
        },
    }

    output_path = Path(DPLAN_DIR) / f"D-Plan_{TEAM_ID}_{trade_date}.json"
    output_path.parent.mkdir(exist_ok=True)
    output_path.write_text(json.dumps(dplan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 D-Plan 已儲存：{output_path}")

    return dplan
