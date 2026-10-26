# 與原專案的接點

基礎repo：https://github.com/IaintHamburger/ESUN-AI-CUP

基礎commit：613778b08bcc1b2817bc2ab88746b08d9da58460。

最省事的方式是讓排程改跑 `run_simple.py auto`，使用獨立的simple環境。`run_daily.py`／`settle_daily.py` 不與此入口同日執行。沒有改動舊LangGraph、optimizer或舊submitter，方便review與回退。

## 若團隊只想換計分與配重

`agent/graph.py` 的 `portfolio_optimization_node` 原本呼叫PyPortfolioOpt。可將同一帳戶狀態與市場bundle餵給：

```python
from simple_agent.core import construct

result = construct(bundle, official_universe, official_account_state, strategy_memory)
target_weights = result["target_weights"]
orders = result["orders"]
```

`construct`是純計算，不抓網路、不送單、不改帳本；`state`要含 `nav`、`cash`、`holdings:{ticker:int_shares}`，可帶不可用現金支付的 `dividend_receivable`。`bundle`的具體格式可看 `simple_agent/data.py` 與凍結fixtures；必須包含相同日期的價格、指數、金融普通股市值與來源。不得把舊graph輸出未驗證的權重直接當作官方委託。

`target_weights`是預算，`result['decisions']`及`orders`才是經保留門檻／整張化後的實際決策。請保留新入口的 `build_dplan → validate → active_checks → send`，或在原graph提供同等檢查。以官方derive-orders.v2重算而非另算一套。

## 官方接口需由維護者確認一次

範例設定故意沒有猜測URL。`simple_agent/service.py`有兩個接點：

- `sync_state`：GET正式持股接口，將回應解包成下方帳戶格式；每次盤前讀官方上一交易日結算，因此除權息／分割不依人工推算。
- `send`：POST D-Plan並核對accepted旗標、正式receipt_id、team_id與trade_date；不認HTTP200為成功。當請求結果未知時保留pending狀態，禁止無條件重試。

```json
{
  "trade_date": "2026-10-23",
  "nav": 1000000000,
  "cash": 1000000000,
  "holdings": {},
  "dividend_receivable": 0
}
```

上面只是介面格式，不表示主辦方的JSON長這樣。官方若用清單型持股、股數字串、不同認證、multipart上傳或另一種回執，必須先寫映射；沒有映射好不能只把verified設true。

## 合併時特別處理

1. repo附schema const=4.0；指南與舊graph硬寫4.1，且舊graph會過濾版本錯誤。新版不做這種過濾，但目前不能從公開入口證實比賽伺服器採哪版。下載官方版再更新schema與adapter，保留diff供review。
2. 舊submitter中的 `/api/dplan/submit` 有「範例，請更換」註記，不能當成已核實API。新版不依賴這個URL。
3. 舊 `settle_daily.py` 會問成交均價；新版必須透過官方持股同步，因此不需每天輸入價格。沒有官方同步接口時，不能宣稱端到端自動化已完成。
4. 團隊尚未提供最新Slack／私下公告全文。本次只讀使用者提供的Discord截圖與repo附文件，不代表已讀所有隊友對話。
5. 同日僅保留一個排程；其他電腦也可能送同隊計劃，本機25次計數不會自動統計所有電腦。
