# ==============================================================================
# crawl_market_data.py
# 依據「動能策略.pdf」、「數據.pdf」、「個股分類.xlsx」規格之歷史數據爬蟲與指標計算引擎
# 抓取台股 150 檔與大盤歷史數據，計算技術指標與法人買賣超，並存至 data/market_history/
# ==============================================================================

import os
import sys
import json
import time
import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

import requests
import pandas as pd
import numpy as np
import yfinance as yf

# 路徑設定
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "market_history"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UNIVERSE_FILE = BASE_DIR / "simple_agent" / "universe.json"

def log(msg: str):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}")

def load_universe() -> Dict[str, Any]:
    """載入 150 檔個股名單與分類"""
    if not UNIVERSE_FILE.exists():
        raise FileNotFoundError(f"Universe file not found: {UNIVERSE_FILE}")
    with open(UNIVERSE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def get_yfinance_ticker(code: str, exchange: str) -> str:
    """轉換為 Yahoo Finance 代碼"""
    if exchange.lower() == "twse":
        return f"{code}.TW"
    else:
        return f"{code}.TWO"

def calculate_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    計算「數據.pdf」與「動能策略.pdf」所需的各項技術指標：
    - 5日、10日、20日、60日、120日 均線 (SMA)
    - 20日、60日、120日 均線斜率 (上彎 / 下彎)
    - 每日漲跌幅、日均價 (High+Low+Close)/3 或 Open/Close/High/Low 平均
    - 5日均量
    - RSI 5 與 RSI 10 (Wilder RSI: RS = N日平均漲幅 / N日平均跌幅)
    - 均線突破 (金叉向上站上均線 / 跌破均線)
    """
    df = df.copy()
    if df.empty or len(df) < 5:
        return df

    # 日均價 (典型的平均價格 Typical Price)
    df["TypicalPrice"] = (df["High"] + df["Low"] + df["Close"]) / 3.0
    # 每日漲跌幅 (%)
    df["PctChange"] = df["Close"].pct_change() * 100.0

    # 均線
    for ma in [5, 10, 20, 60, 120]:
        df[f"MA_{ma}"] = df["Close"].rolling(window=ma).mean()
        # 均線上彎/下彎 (與前一日相比之斜率差)
        if ma in [20, 60, 120]:
            df[f"MA_{ma}_Slope"] = df[f"MA_{ma}"] - df[f"MA_{ma}"].shift(1)

    # 5日均量 (依策略：不含當日或滾動均量)
    df["Vol_MA5"] = df["Volume"].rolling(window=5).mean()
    # 前5日均量 (shift 1，計算當日爆量用)
    df["Prior_Vol_MA5"] = df["Volume"].shift(1).rolling(window=5).mean()

    # Wilder's RSI (5 與 10)
    for n in [5, 10]:
        delta = df["Close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1.0/n, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0/n, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df[f"RSI_{n}"] = 100.0 - (100.0 / (1.0 + rs))
        df[f"RSI_{n}"] = df[f"RSI_{n}"].fillna(50.0)

    # 站上與跌破 均線訊號
    # 站上：昨日收盤 <= 昨日MA 且 今日收盤 > 今日MA
    # 跌破：昨日收盤 >= 昨日MA 且 今日收盤 < 今日MA
    for ma in [5, 10, 20, 60, 120]:
        ma_col = f"MA_{ma}"
        df[f"Cross_Over_MA_{ma}"] = (df["Close"].shift(1) <= df[ma_col].shift(1)) & (df["Close"] > df[ma_col])
        df[f"Cross_Under_MA_{ma}"] = (df["Close"].shift(1) >= df[ma_col].shift(1)) & (df["Close"] < df[ma_col])

    return df

def fetch_yfinance_history(universe: Dict[str, Any], period: str = "1y") -> Dict[str, pd.DataFrame]:
    """批次抓取 150 檔個股與大盤歷史行情"""
    log(f"開始自 Yahoo Finance 抓取 150 檔個股歷史數據 (週期: {period})...")
    
    ticker_to_code = {}
    tickers = ["^TWII"] # 大盤加權指數
    ticker_to_code["^TWII"] = "TAIEX"
    
    for code, info in universe.items():
        sym = get_yfinance_ticker(code, info.get("exchange", "twse"))
        tickers.append(sym)
        ticker_to_code[sym] = code

    # 批次下載
    data = yf.download(tickers, period=period, group_by="ticker", auto_adjust=False, threads=True)
    
    stock_dfs: Dict[str, pd.DataFrame] = {}
    for sym in tickers:
        try:
            if sym in data.columns.levels[0]:
                df = data[sym].dropna(how="all").copy()
                if not df.empty:
                    df = df.reset_index()
                    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
                    df = df.set_index("Date").sort_index()
                    # 重新計算技術指標
                    df = calculate_technical_indicators(df)
                    code = ticker_to_code[sym]
                    stock_dfs[code] = df
        except Exception as e:
            log(f"處理 {sym} 時發生錯誤: {e}")

    log(f"成功下載並計算技術指標共 {len(stock_dfs)} 檔標的 (含大盤)")
    return stock_dfs

def fetch_market_caps(universe: Dict[str, Any]) -> Dict[str, float]:
    """獲取各標的市值 (供同分排序與金融股權重分配)"""
    log("抓取個股市值與發行股數資料...")
    market_caps: Dict[str, float] = {}
    
    for code, info in universe.items():
        sym = get_yfinance_ticker(code, info.get("exchange", "twse"))
        try:
            ticker = yf.Ticker(sym)
            mc = ticker.info.get("marketCap")
            if mc:
                market_caps[code] = float(mc)
            else:
                # 備用方案: 發行股數 * 收盤價
                shares = ticker.info.get("sharesOutstanding")
                price = ticker.info.get("previousClose") or ticker.info.get("currentPrice")
                if shares and price:
                    market_caps[code] = float(shares * price)
        except Exception:
            pass
        # 避免觸發 rate limit
        time.sleep(0.05)

    log(f"成功取得 {len(market_caps)} 檔個股市值")
    return market_caps

def fetch_institutional_flows(days: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    """
    抓取 TWSE 與 TPEx 過去幾日的法人買賣超 (外資、投信)
    """
    log(f"開始抓取最近 {days} 個交易日的三大法人買賣超...")
    institutional_history: Dict[str, List[Dict[str, Any]]] = {}
    
    # 尋找最近的交易日
    today = datetime.date.today()
    found_dates = []
    
    for offset in range(1, 15):
        d = today - datetime.timedelta(days=offset)
        d_str = d.strftime("%Y%m%d")
        url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={d_str}&selectType=ALL&response=json"
        try:
            r = requests.get(url, timeout=10)
            if r.ok and r.json().get("stat") == "OK":
                found_dates.append(d)
                if len(found_dates) >= days:
                    break
        except Exception:
            pass
        time.sleep(0.2)

    log(f"找到最近有效法人交易日: {[d.strftime('%Y-%m-%d') for d in found_dates]}")
    
    # 依日期解析 TWSE 與 TPEx
    for d in found_dates:
        date_str = d.strftime("%Y-%m-%d")
        # 1. TWSE
        twse_date = d.strftime("%Y%m%d")
        url_twse = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={twse_date}&selectType=ALL&response=json"
        try:
            r = requests.get(url_twse, timeout=10)
            if r.ok:
                data = r.json()
                for row in data.get("data", []):
                    code = row[0].strip()
                    # 買進、賣出、買賣超 (股數轉張數)
                    # 欄位: [0]代號, [1]名稱, [2]外資買進, [3]外資賣出, [4]外資買賣超, [8]投信買進, [9]投信賣出, [10]投信買賣超
                    def clean(val): return float(str(val).replace(",", "") or 0) / 1000.0
                    f_buy = clean(row[2])
                    f_sell = clean(row[3])
                    f_net = clean(row[4])
                    t_buy = clean(row[8])
                    t_sell = clean(row[9])
                    t_net = clean(row[10])
                    
                    if code not in institutional_history:
                        institutional_history[code] = []
                    institutional_history[code].append({
                        "date": date_str,
                        "foreign_buy_lots": f_buy,
                        "foreign_sell_lots": f_sell,
                        "foreign_net_lots": f_net,
                        "trust_buy_lots": t_buy,
                        "trust_sell_lots": t_sell,
                        "trust_net_lots": t_net
                    })
        except Exception as e:
            log(f"TWSE {date_str} 法人抓取失敗: {e}")

        # 2. TPEx
        roc_year = d.year - 1911
        tpex_date = f"{roc_year}/{d.month:02d}/{d.day:02d}"
        url_tpex = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&se=EW&t=D&d={tpex_date}&response=json"
        try:
            r = requests.get(url_tpex, timeout=10)
            if r.ok:
                data = r.json()
                if data.get("tables") and len(data["tables"]) > 0:
                    for row in data["tables"][0].get("data", []):
                        code = row[0].strip()
                        def clean(val):
                            try:
                                return float(str(val).replace(",", "") or 0) / 1000.0
                            except (ValueError, TypeError):
                                return 0.0
                        f_buy = clean(row[2])
                        f_sell = clean(row[3])
                        f_net = clean(row[4])
                        t_buy = clean(row[11])
                        t_sell = clean(row[12])
                        t_net = clean(row[13])
                        if code not in institutional_history:
                            institutional_history[code] = []
                        institutional_history[code].append({
                            "date": date_str,
                            "foreign_buy_lots": f_buy,
                            "foreign_sell_lots": f_sell,
                            "foreign_net_lots": f_net,
                            "trust_buy_lots": t_buy,
                            "trust_sell_lots": t_sell,
                            "trust_net_lots": t_net
                        })
        except Exception as e:
            log(f"TPEx {date_str} 法人抓取失敗: {e}")

        time.sleep(0.3)

    log(f"完成法人買賣超資料整合，涵蓋 {len(institutional_history)} 檔證券")
    return institutional_history

def main():
    log("=== 啟動台股 150 檔及大盤歷史資料爬蟲作業 ===")
    universe = load_universe()
    
    # 1. 抓取股價歷史與技術指標
    stock_dfs = fetch_yfinance_history(universe, period="1y")
    
    # 2. 抓取法人買賣超
    flows = fetch_institutional_flows(days=5)
    
    # 3. 抓股市值
    market_caps = fetch_market_caps(universe)
    
    # 4. 儲存各股資料至 CSV / JSON
    summary = {}
    for code, df in stock_dfs.items():
        csv_path = DATA_DIR / f"{code}.csv"
        df.to_csv(csv_path)
        
        last_row = df.iloc[-1].to_dict() if not df.empty else {}
        summary[code] = {
            "name": universe.get(code, {}).get("name", "加權指數" if code=="TAIEX" else ""),
            "industry": universe.get(code, {}).get("industry", "大盤" if code=="TAIEX" else ""),
            "exchange": universe.get(code, {}).get("exchange", "twse"),
            "momentum_eligible": universe.get(code, {}).get("momentum", False),
            "market_cap": market_caps.get(code, 0),
            "last_date": df.index[-1] if not df.empty else None,
            "last_close": last_row.get("Close"),
            "last_pct_change": last_row.get("PctChange"),
            "ma_20": last_row.get("MA_20"),
            "ma_60": last_row.get("MA_60"),
            "ma_120": last_row.get("MA_120"),
            "ma_20_slope": last_row.get("MA_20_Slope"),
            "rsi_10": last_row.get("RSI_10"),
            "institutional_recent_flows": flows.get(code, [])
        }

    # 儲存總表
    summary_path = DATA_DIR / "latest_market_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # 儲存法人買賣超專屬歷史
    flows_path = DATA_DIR / "institutional_flows.json"
    with open(flows_path, "w", encoding="utf-8") as f:
        json.dump(flows, f, ensure_ascii=False, indent=2)

    # 儲存市值字典
    caps_path = DATA_DIR / "market_caps.json"
    with open(caps_path, "w", encoding="utf-8") as f:
        json.dump(market_caps, f, ensure_ascii=False, indent=2)

    log(f"歷史數據爬取與指標計算完成！所有檔案已存放至: {DATA_DIR}")

if __name__ == "__main__":
    main()
