# ==============================================================================
# ai_bridge.py — AI 協作橋接器
# 負責組裝給 AI 的 prompt，以及解析 AI 輸出的 D-Plan JSON
# ==============================================================================

from __future__ import annotations
import json
import re
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

from agent.config import PROMPT_TEMPLATE, STRATEGY_RULES, ETF_PROSPECTUS, TEAM_ID

TZ_TAIPEI = timezone(timedelta(hours=8))


def build_daily_prompt(
    trade_date: str,
    prices: dict[str, float],
    institutional: dict[str, dict],
    us_market: dict[str, float],
    night_futures: dict | None,
    holdings: dict[str, int],
    nav: float,
    cash: float,
) -> str:
    """
    組裝每日分析 prompt，輸出給 AI 分析。
    AI 讀完後輸出完整 D-Plan JSON。
    """
    strategy_rules  = _read_file(STRATEGY_RULES, "[尚未填入策略規則，請於 prompts/strategy_rules.txt 定義]")
    etf_prospectus  = _read_file(ETF_PROSPECTUS, "[尚未填入 ETF 公開說明書，請於 prompts/etf_prospectus.txt 定義]")
    template        = _read_file(PROMPT_TEMPLATE, _default_prompt_template())

    # 組裝持倉摘要
    holding_lines = "\n".join(
        f"  {ticker}: {shares:,} 股，前日收盤 {prices.get(ticker, '?')} 元"
        for ticker, shares in sorted(holdings.items(), key=lambda x: -x[1])
    ) or "  （目前無持股）"

    # 三大法人前 10 大外資買超
    top_foreign = sorted(
        [(t, v.get("foreign_net", 0)) for t, v in institutional.items()],
        key=lambda x: -x[1]
    )[:10]
    inst_summary = "\n".join(
        f"  {t}: 外資 {v:+,.0f} 千元"
        for t, v in top_foreign
    )

    # 美股摘要
    us_summary = "\n".join(
        f"  {k}: {v:+.2f}%" if "close" not in k else f"  {k}: {v}"
        for k, v in us_market.items()
    )

    # 台指期夜盤
    futures_summary = json.dumps(night_futures, ensure_ascii=False) if night_futures else "（無資料，請手動補充）"

    return template.format(
        team_id=TEAM_ID,
        trade_date=trade_date,
        nav=f"{nav:,.0f}",
        cash=f"{cash:,.0f}",
        cash_pct=f"{cash / nav:.1%}",
        holding_count=len(holdings),
        holding_lines=holding_lines,
        us_summary=us_summary,
        inst_summary=inst_summary,
        futures_summary=futures_summary,
        strategy_rules=strategy_rules,
        etf_prospectus=etf_prospectus,
    )


def parse_ai_response(response_text: str) -> dict:
    """
    從 AI 回應中提取 D-Plan JSON。
    AI 可能在 JSON 前後附加說明文字，這裡只取 JSON 部分。
    """
    # 嘗試抓 ```json ... ``` 區塊
    match = re.search(r"```json\s*(.*?)\s*```", response_text, re.DOTALL)
    if match:
        return json.loads(match.group(1))

    # 嘗試抓 ``` ... ``` 區塊
    match = re.search(r"```\s*(.*?)\s*```", response_text, re.DOTALL)
    if match:
        return json.loads(match.group(1))

    # 嘗試直接解析
    return json.loads(response_text.strip())


def save_prompt(prompt: str, trade_date: str) -> str:
    """把今日 prompt 存成檔案，方便手動貼給 AI"""
    path = f"prompts/prompt_{trade_date}.txt"
    Path(path).write_text(prompt, encoding="utf-8")
    return path


def _read_file(path: str, default: str = "") -> str:
    p = Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else default


def _default_prompt_template() -> str:
    """
    預設 prompt 模板。
    如果 prompts/daily_prompt.md 不存在，用這個。
    """
    return """你是一個台股主動型 ETF 的 AI 投資管理員。請根據以下資料，輸出今日完整的 D-Plan JSON。

## 基本資訊
- 隊伍代號：{team_id}
- 交易日：{trade_date}
- 當前 NAV：{nav} TWD
- 現金：{cash} TWD（{cash_pct}）
- 持股數：{holding_count} 檔

## ETF 公開說明書（投資主題與理念）
{etf_prospectus}

## 策略規則（策略師定義）
{strategy_rules}

---

## 今日市場資料

### 美股收盤（含 ADR）
{us_summary}

### 台指期夜盤
{futures_summary}

### 三大法人：外資買超前 10
{inst_summary}

---

## 當前持股
{holding_lines}

---

## 你的任務

請根據上述資料，輸出一份完整符合 D-Plan schema v4.1 的 JSON。

規則提醒：
1. sources → observations → market_view + inferences → decisions → orders，引用鏈不能斷
2. 每一檔當前持股都要出現在 decisions 或 no_trade_decisions（擇一，不重複）
3. orders 的股數必須用官方公式：floor(target_weight × NAV ÷ prev_close ÷ 1000) × 1000
4. 持股 20–30 檔；2330 最高 25%；其他個股最高 10%；現金 0–24%
5. market_view.posture 的宣告必須和 orders 的淨買賣方向一致

請直接輸出 JSON，用 ```json ... ``` 包住：

```json
{{
  "schema_version": "4.1",
  "doc_type": "D-Plan",
  "team_id": "{team_id}",
  "trade_date": "{trade_date}",
  ...
}}
```
"""
