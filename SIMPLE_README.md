# 簡單動能 Agent｜先跑這個

這是原專案的可獨立執行擴充，不需要 LangGraph、訓練模型或投資組合最佳化套件。保留隊友的台積電／金融核心／20檔動能結構；策略由固定規則產生，正式模式另經單次輕量 Gemini 覆核，AI可否決，不能任意改寫股數。

**可立即執行：示範、計分、配置、整張委託、D-Plan與本地驗證。正式自動提交仍需要真實憑證、完整ETF資料，以及隊友依官方文件確認一次提交介面與schema版本。尚未對比賽伺服器實測，不表示已可正式送件。**

## 1. 馬上跑

Windows：安裝 Python 3.10以上後，雙擊 `START_DEMO.cmd`。這會建立環境、安裝唯一直接依賴jsonschema並執行示範。

macOS／Linux：

```bash
python3 -m venv .venv-simple
.venv-simple/bin/python -m pip install -r requirements-simple.txt
.venv-simple/bin/python run_simple.py demo
```

示範使用2026-10-22的凍結歷史資料，金融股市值沿用4/23發行股數乘10/22價格，明確只作測試。會輸出26檔、26筆整張委託；ETF持股沒有偽造，所以 `ready_for_submission=false`。**示範不是10/27可繳交的最新計劃，不會連線送出。**

查看 `output/demo/validation.json`、`scores.json`、`D-Plan_DEMO_ONLY_2026-10-27.json`。不需手填價格，也不用每天上傳行情。

## 2. 一次性接正式環境

1. Windows執行 `SETUP_LIVE.cmd`，或執行 `python setup_simple.py`。輸入正式TEAM_ID、Gemini Key、比賽Token，以及有ETF資料權限的FinMind Sponsor token或Fugle開發者／進階方案Key。已存在環境變數會沿用，輸入的密鑰不顯示。
2. **隊友維護者**複製 `submit_adapter.example.json` 成 `submit_adapter.json`，依官方文件填入真實URL、認證／本文／回執欄位。不要照網路猜網址。預設只支援Bearer認證與JSON或包一層JSON，若官方是multipart／Cookie／簽名，須修改 `simple_agent/service.py` 的接頭後再啟用。
3. 確認官方最新版schema。專案附schema要求4.0，指南／原程式卻寫4.1；新版直接讀schema且不忽略錯誤。將官方schema放回 `參考資料/D-Plan.schema.json`，把接頭內 `schema_confirmed_version` 設成正式版本，並且確認後才設 `verified_against_official_docs=true`。
4. 確認首日官方帳戶確實為空倉10億，才在 `.env` 設 `ESUN_INITIAL_PORTFOLIO_CONFIRMED=1`。成功提交後自動耗用這次初始化，往後每天必須同步官方前日持股／現金／NAV；不會重設本金，也不會要你手填成交均價。

密鑰、runtime狀態與提交接頭均已排除在git之外。Token不能替別人產生，也不能從你附的示意畫面推測。

## 3. 每日自動跑

先執行：

```bash
python run_simple.py doctor
python run_simple.py plan
```

`doctor`僅檢查本機設定是否存在，不保證憑證有效或會員有權限。`plan`會真正抓資料、同步帳戶、產生AI覆核與策略說明文字，但不提交。

正式自動流程：

```bash
python run_simple.py auto
```

在Windows雙擊 `INSTALL_SCHEDULE.cmd`，安裝每天台北07:30的本機排程；電腦需開機、有網路且Windows時區為台北。啟動器僅對該次PowerShell程序使用ExecutionPolicy Bypass，不更改系統全域政策。未在本次聊天替你的電腦安裝排程。macOS／Linux可用cron，系統時區設台北：

```cron
30 7 * * * cd /你的專案路徑 && .venv-simple/bin/python run_simple.py auto >> runtime/schedule.log 2>&1
```

每天會依交易日曆跳過休市日，抓最新官方收盤／調整後歷史日價／法人／ETF持股，決定部位、覆核、驗證、提交、保存正式回執。下次執行從官方同步前日帳本，因此不用手動執行舊的 `settle_daily.py`。這是每日盤前策略，不使用盤中「即時價」下當日收盤才能知道的訊號。

成功記錄在 `runtime/receipts/YYYY-MM-DD.json`；僅有JSON輸出或HTTP200都不算已提交。逾時而不知道對方是否收到時，會鎖住自動重送；需查官方收件紀錄再處理。每日25次上限僅計本程式；其他工具的同隊提交次數不在本機掌握。

## 4. 策略怎麼改

| 原本 | 本版的明確計算 |
|---|---|
| 20／60／120日均線上彎各+1、下彎各-1 | EMA，alpha=2/(N+1)；每日EMA差除ATR20，再夾限每條-1至1；小於0.02ATR不計 |
| 當日漲跌3%且爆量，±3 | 當日收開差至少1ATR，成交量至少前5日均量1.5倍，±3；五日均量不含當日 |
| 多條均線穿越各±2，可累加到10 | 保留5／10／20／60／120日穿越，但整組最高+4、最低-4 |
| RSI10過50就±2 | Wilder RSI10；45至55不計，依距50的程度平滑到±2 |
| 同產業超過半數跑贏大盤，±2 | 保留，產業範圍是固定150檔中有完整資料的同業；比較調整後日報酬 |
| 外資／投信三天中兩天同向且買賣比3倍，±3 | 保留；無資料或資料未到前日則因子缺漏、貢獻為0，明記缺漏數，不編造買賣超 |
| 前10名3%、後10名1.5% | 保留2:1排名偏重，再乘ATR百分比平方根倒數；整組重新正規化到當日動能預算，單檔設計上限8% |
| 連兩天掉排名才處理 | 弱排名門檻改為候選後30%（原後50%），連兩日且訊號參考價以來漲15%才汰換；另加跌破max(6%,3×進場ATR%)獨立退出；每天最多2檔 |

ATR20為真實波幅的EMA20，最低按價格0.5%計。所有信號使用調整後OHLC，委託及估值使用官方原始收盤價。進場停損參考價是進場訊號日的調整後收盤，**不是當日實際成交均價**。只在每日盤前檢查，無盤中即時停損保證。

| 指數狀態 | 台積電 | 金融核心 | 動能20檔 | 現金預算 |
|---|---:|---:|---:|---:|
| 連續3日高於EMA20 | 20% | 10% | 60% | 10% |
| 中性／尚未確認 | 20% | 20% | 45% | 15% |
| 連續3日低於EMA60 | 20% | 30% | 30% | 20% |

金融核心為名單內**上市金融股**按普通股市值選前5，各平均分配；不把上櫃期貨商6023混作銀行核心。正式模式以TWSE最新發行普通股數乘官方前日收盤估算市值。因整張、保留門檻與交易費，實際比例會偏離預算，所以以重算結果決定能否送出。

只有0.3個百分點以上的權重偏差才做小幅再平衡；汰換順位則依退出規則。參數在 `simple_agent/core.py: PARAMS`，平常不用每天調整。本版常數是保守工程起點，沒有宣稱經過尋優或證明超額報酬。波動正規化可以降低高價股／高波動股的數值偏差，不能抵銷整體市場下跌。

## 5. 比賽核對與尚待確認

已依公開競賽辦法及repo附文件核對兩輪：選股與配置時、產生D-Plan後。完整逐項表見 `docs/rule_audit.md`。

硬檢查包含150檔白名單、20至30檔、台積電25%／其他10%、現金0至低於25%、千股整張、禁止放空、雙邊手續費0.1425%與賣出稅0.3%、官方derive-orders.v2股數、完整引用與昨日持股交代、30筆委託上限、台北時區19:30至08:55提交、憑正式回執才算成功。

Active Share以固定30檔ETF的前10大，分別算「原NAV權重」及「前10大重新正規化」兩種口徑，兩者都至少20%才放行。**這是保守檢查；正式口徑仍需主辦方確認。** 沒有完整最新ETF資料就停止，不把缺漏當通過；低於20%也停止，不隨意扭曲持股湊分。

交易當日均價與當日收盤事前未知，現金及權重不能事前百分之百保證。程式檢查前日收盤下的部位與費用，並額外測買價+10%／賣價-10%現金不為負；這不是對所有市場情境的保證。股票分割、除息應收款與帳戶狀態以官方前日結算為準；同步後有零股或帳目對不上會停用，不私自假設處理方式。

**日期衝突：官方競賽文件寫10/26開始；TWSE明定2026-10-26補假休市，下一交易日10/27。是否10/26仍要交首份報告／策略說明，須以活動最新公告確認，不能自行跳過文件義務。** 本程式auto僅在交易日下單；首日文件提交尚未自動接通。

## 6. 接回隊友專案／GitHub PR

建議先用 `run_simple.py` 獨立入口，避免同日又跑舊 `run_daily.py` 造成重複提交。所有原模組仍在；不要把這份當成已修完舊流程的所有問題。想只替換原Node4，位置是 `agent/graph.py` 的 `portfolio_optimization_node`；介面與注意事項見 `docs/integration.md`。不能只塞權重就繞過新版帳本／schema／Active Share檢查。

```bash
git switch -c feat/simple-ema-atr-agent
git apply --check simple-agent.patch
git apply simple-agent.patch
python -m pip install -r requirements-simple.txt
python -m unittest discover -s tests -v
python run_simple.py demo
git add .
git commit -m "Add simple volatility-adjusted momentum agent"
git push -u origin feat/simple-ema-atr-agent
```

GitHub依 `docs/PR_DESCRIPTION.md` 開PR。本交付已準備分支與patch，**未登入GitHub推送、未建立遠端PR**。官方規則禁止不同隊伍私下共用Agent；只在你們同隊的授權專案合併，避免公開策略與Key。

## 7. 故障處理

| 訊息／情況 | 怎麼處理 |
|---|---|
| Official snapshot stale | 等交易所更新；不可把as_of改舊來硬過驗證 |
| Missing ETF holdings／Active Share not verified | 確認資料方案有30檔且日期一致；不支援的資料源需由維護者加接頭 |
| schema version not confirmed | 由維護者拿正式最新版schema，比對4.0／4.1差異後確認 |
| Previous submission outcome unknown | 去官方查收件狀態；未查清楚前不要刪除pending檔硬重送 |
| running.lock exists | 先確認沒有另一個程式執行，再由維護者清掉崩潰留下的鎖 |
| Corporate action produced odd lots | 依主辦方正式零股／配股處理辦法調整接頭，不猜測帳本 |
| 排程未執行／錯過截止 | 看Windows工作排程器與runtime/schedule.log；本交付尚未在你的電腦安裝測試 |

詳細來源、版本衝突、可驗證範圍與限制列在PDF及規則核對表；本地測試通過不等於官方已收件。
