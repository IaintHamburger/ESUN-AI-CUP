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

def supplement_with_official_daily_quotes(stock_dfs: Dict[str, pd.DataFrame], latest_trade_date: datetime.date) -> Dict[str, pd.DataFrame]:
    """
    若 Yahoo Finance 尚未更新當日收盤價（或為 NaN），直接從 TWSE / TPEx 官方盤後 API 補齊最新一日的 OHLCV，並重算技術指標
    """
    date_str = latest_trade_date.strftime("%Y-%m-%d")
    twse_date = latest_trade_date.strftime("%Y%m%d")
    roc_year = latest_trade_date.year - 1911
    tpex_date = f"{roc_year}/{latest_trade_date.month:02d}/{latest_trade_date.day:02d}"

    log(f"檢查並從 TWSE / TPEx 官方盤後行情補齊 {date_str} 最新 OHLCV...")
    
    def needs_update(df: pd.DataFrame) -> bool:
        if date_str not in df.index:
            return True
        val = df.loc[date_str, "Close"]
        return pd.isna(val)

    def upsert_row(df: pd.DataFrame, o: float, h: float, l: float, c: float, vol: float) -> pd.DataFrame:
        base_cols = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
        df_clean = df[base_cols].copy()
        if date_str in df_clean.index:
            df_clean = df_clean.drop(index=date_str)
        new_row = pd.DataFrame([{
            "Open": o, "High": h, "Low": l,
            "Close": c, "Adj Close": c, "Volume": vol
        }], index=[date_str])
        new_row.index.name = "Date"
        df_clean = pd.concat([df_clean, new_row]).sort_index()
        return calculate_technical_indicators(df_clean)

    # 1. 補齊 TAIEX 大盤
    if "TAIEX" in stock_dfs and needs_update(stock_dfs["TAIEX"]):
        try:
            url_taiex = f"https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?date={twse_date[:6]}01&response=json"
            r = requests.get(url_taiex, timeout=10)
            if r.ok and r.json().get("stat") == "OK":
                for row in r.json().get("data", []):
                    if row[0].strip() == tpex_date:
                        def c_f(v): return float(str(v).replace(",", "").strip())
                        stock_dfs["TAIEX"] = upsert_row(
                            stock_dfs["TAIEX"], c_f(row[1]), c_f(row[2]), c_f(row[3]), c_f(row[4]), 0.0
                        )
                        break
        except Exception as e:
            log(f"補齊 TAIEX 失敗: {e}")

    # 2. 補齊 TWSE 個股
    try:
        url_mi = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={twse_date}&type=ALLBUT0999&response=json"
        r = requests.get(url_mi, timeout=15)
        if r.ok and r.json().get("stat") == "OK":
            for t in r.json().get("tables", []):
                if "每日收盤行情" in t.get("title", ""):
                    for row in t.get("data", []):
                        code = row[0].strip()
                        if code in stock_dfs and needs_update(stock_dfs[code]):
                            try:
                                def c_f(v): return float(str(v).replace(",", "").strip())
                                vol = c_f(row[2])
                                o, h, l, c = c_f(row[5]), c_f(row[6]), c_f(row[7]), c_f(row[8])
                                stock_dfs[code] = upsert_row(stock_dfs[code], o, h, l, c, vol)
                            except Exception:
                                pass
    except Exception as e:
        log(f"補齊 TWSE 個股失敗: {e}")

    # 3. 補齊 TPEx 個股
    try:
        url_tpex = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&d={tpex_date}&response=json"
        r = requests.get(url_tpex, timeout=15)
        if r.ok and r.json().get("tables"):
            for row in r.json()["tables"][0].get("data", []):
                code = row[0].strip()
                if code in stock_dfs and needs_update(stock_dfs[code]):
                    try:
                        def c_f(v): return float(str(v).replace(",", "").strip())
                        c, o, h, l = c_f(row[2]), c_f(row[4]), c_f(row[5]), c_f(row[6])
                        vol = c_f(row[8])
                        stock_dfs[code] = upsert_row(stock_dfs[code], o, h, l, c, vol)
                    except Exception:
                        pass
    except Exception as e:
        log(f"補齊 TPEx 個股失敗: {e}")

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
    
    for offset in range(0, 15):
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

def fetch_us_and_adr_market() -> Dict[str, Any]:
    """抓取美股與台股 ADR 最新收盤行情（含週五美東收盤）"""
    log("開始抓取美股指數與台股 ADR 行情 (SOX / NDX / SPX / DJI / TSM / UMC / VIX)...")
    symbols = {
        "^SOX": "SOX",
        "^NDX": "NDX",
        "^GSPC": "SPX",
        "^DJI": "DJI",
        "TSM": "TSM_ADR",
        "UMC": "UMC_ADR",
        "^VIX": "VIX",
    }
    result: Dict[str, Any] = {}
    for sym, name in symbols.items():
        try:
            hist = yf.Ticker(sym).history(period="10d")
            if not hist.empty and len(hist) >= 2:
                hist = hist.dropna(subset=["Close"])
                last_date = pd.to_datetime(hist.index[-1]).strftime("%Y-%m-%d")
                prev_close = float(hist["Close"].iloc[-2])
                last_close = float(hist["Close"].iloc[-1])
                chg_pct = round((last_close - prev_close) / prev_close * 100.0, 2)
                # 計算週漲跌幅 (若有 5 個交易日前資料)
                week_chg_pct = None
                if len(hist) >= 6:
                    w_prev = float(hist["Close"].iloc[-6])
                    week_chg_pct = round((last_close - w_prev) / w_prev * 100.0, 2)
                result[name] = {
                    "symbol": sym,
                    "last_date": last_date,
                    "close": round(last_close, 2),
                    "prev_close": round(prev_close, 2),
                    "chg_pct": chg_pct,
                    "week_chg_pct": week_chg_pct,
                }
        except Exception as e:
            log(f"抓取美股 {sym} 失敗: {e}")
    return result

def fetch_official_vwap_for_dates(dates: List[str], universe: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    抓取指定交易日 TWSE / TPEx 官方『成交金額 ÷ 成交股數』精確成交均價與收盤價，供帳務結算核對
    """
    log(f"抓取官方精確成交均價 (VWAP) 與收盤價: {dates}")
    out: Dict[str, Dict[str, Any]] = {}
    for d_str in dates:
        d = datetime.date.fromisoformat(d_str)
        twse_date = d.strftime("%Y%m%d")
        roc_year = d.year - 1911
        tpex_date = f"{roc_year}/{d.month:02d}/{d.day:02d}"
        day_map: Dict[str, Any] = {}

        # TWSE MI_INDEX
        try:
            url_mi = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={twse_date}&type=ALLBUT0999&response=json"
            r = requests.get(url_mi, timeout=15)
            if r.ok and r.json().get("stat") == "OK":
                for t in r.json().get("tables", []):
                    if "每日收盤行情" in t.get("title", ""):
                        for row in t.get("data", []):
                            code = row[0].strip()
                            if code in universe:
                                try:
                                    def c_f(v): return float(str(v).replace(",", "").strip())
                                    vol = c_f(row[2])
                                    val = c_f(row[4])
                                    close_p = c_f(row[8])
                                    vwap = (val / vol) if vol > 0 else close_p
                                    day_map[code] = {
                                        "close": close_p,
                                        "vwap": vwap,
                                        "volume": vol,
                                        "value": val,
                                    }
                                except Exception:
                                    pass
        except Exception as e:
            log(f"TWSE VWAP {d_str} 失敗: {e}")

        # TPEx dailyQuotes (支援指定歷史日期 YYYY/MM/DD)
        try:
            url_tpex = f"https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={d.year}/{d.month:02d}/{d.day:02d}&id=&response=json"
            r = requests.get(url_tpex, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if r.ok and r.json().get("tables"):
                for row in r.json()["tables"][0].get("data", []):
                    code = row[0].strip()
                    if code in universe:
                        try:
                            def c_f(v): return float(str(v).replace(",", "").strip())
                            close_p = c_f(row[2])
                            vol = c_f(row[8])
                            val = c_f(row[9])
                            vwap = (val / vol) if vol > 0 else close_p
                            day_map[code] = {
                                "close": close_p,
                                "vwap": vwap,
                                "volume": vol,
                                "value": val,
                            }
                        except Exception:
                            pass
        except Exception as e:
            log(f"TPEx VWAP {d_str} 失敗: {e}")

        out[d_str] = day_map
        time.sleep(0.3)
    return out

def fetch_weekend_and_recent_news(universe: Dict[str, Any], start_date: str = "2026-10-30") -> Dict[str, Any]:
    """
    抓取週五至週日（及最新盤前）之台股、美股、總經新聞與重大訊息，並對應 150 檔個股
    """
    log(f"開始抓取 {start_date} 以來之台股、美股與總經新聞...")
    articles: List[Dict[str, Any]] = []
    seen_urls = set()
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))

    # 1. Cnyes JSON API (包含精確時間戳與摘要，涵蓋 tw_stock, us_stock, macro, wd_macro)
    cnyes_cats = ["tw_stock", "us_stock", "wd_macro", "tech"]
    for cat in cnyes_cats:
        for page in [1, 2]:
            url = f"https://api.cnyes.com/media/api/v1/newslist/category/{cat}?limit=30&page={page}"
            try:
                r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                if r.ok:
                    items = r.json().get("items", {}).get("data", [])
                    for it in items:
                        nid = it.get("newsId")
                        title = (it.get("title") or "").strip()
                        summary_txt = (it.get("summary") or "").strip()
                        pub_ts = it.get("publishAt")
                        if not nid or not title or not pub_ts:
                            continue
                        pub_dt = datetime.datetime.fromtimestamp(int(pub_ts), tz=tz_tw)
                        pub_str = pub_dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")
                        pub_date = pub_dt.strftime("%Y-%m-%d")
                        if pub_date < start_date:
                            continue
                        link = f"https://news.cnyes.com/news/id/{nid}"
                        if link in seen_urls:
                            continue
                        seen_urls.add(link)
                        articles.append({
                            "title": title,
                            "summary": summary_txt,
                            "url": link,
                            "source": f"cnyes_{cat}",
                            "published_at": pub_str,
                            "date": pub_date,
                        })
            except Exception as e:
                log(f"Cnyes {cat} p{page} 抓取失敗: {e}")
            time.sleep(0.15)

    # 2. RSS 補充 (MoneyDJ / TWSE 公告 / Google News 台股財經)
    try:
        import feedparser
        rss_feeds = {
            "moneydj_tw": "https://www.moneydj.com/KMDJ/RssProxy.aspx?feedName=tw",
            "twse_news": "https://www.twse.com.tw/rss/zh/news.rss",
            "google_tw_market": "https://news.google.com/rss/search?q=%E5%8F%B0%E8%82%A1+OR+%E5%8F%B0%E7%A9%8D%E9%9B%BB+OR+%E8%B2%BB%E5%8D%8A+when:3d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
        }
        for src_name, rss_url in rss_feeds.items():
            try:
                r = requests.get(rss_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                if r.ok:
                    feed = feedparser.parse(r.content)
                    for entry in feed.entries[:25]:
                        title = entry.get("title", "").strip()
                        link = entry.get("link", "").strip()
                        if not title or not link or link in seen_urls:
                            continue
                        pub_str = None
                        pub_date = datetime.date.today().strftime("%Y-%m-%d")
                        if entry.get("published_parsed"):
                            dt_utc = datetime.datetime(*entry.published_parsed[:6], tzinfo=datetime.timezone.utc)
                            dt_tw = dt_utc.astimezone(tz_tw)
                            pub_str = dt_tw.strftime("%Y-%m-%dT%H:%M:%S+08:00")
                            pub_date = dt_tw.strftime("%Y-%m-%d")
                        if pub_date < start_date:
                            continue
                        seen_urls.add(link)
                        articles.append({
                            "title": title,
                            "summary": (entry.get("summary") or "")[:200].strip(),
                            "url": link,
                            "source": src_name,
                            "published_at": pub_str or f"{pub_date}T12:00:00+08:00",
                            "date": pub_date,
                        })
            except Exception as e:
                log(f"RSS {src_name} 抓取失敗: {e}")
    except ImportError:
        pass

    # 依時間由新到舊排序
    articles.sort(key=lambda x: x.get("published_at", ""), reverse=True)

    # 對應 150 檔標的提及狀況
    ticker_mentions: Dict[str, List[Dict[str, str]]] = {}
    for art in articles:
        txt = f"{art['title']} {art.get('summary', '')}"
        matched_codes = []
        for code, info in universe.items():
            name = info.get("name", "")
            if code in txt or (name and len(name) >= 2 and name in txt):
                matched_codes.append(code)
                ticker_mentions.setdefault(code, []).append({
                    "title": art["title"],
                    "published_at": art["published_at"],
                    "url": art["url"],
                    "source": art["source"],
                })
        art["matched_tickers"] = matched_codes

    log(f"成功抓取 {len(articles)} 則週末與近期新聞，涵蓋 {len(ticker_mentions)} 檔個股相關報導")
    return {
        "fetched_at": datetime.datetime.now(tz_tw).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "start_date": start_date,
        "total_articles": len(articles),
        "ticker_mentions_count": len(ticker_mentions),
        "ticker_mentions": ticker_mentions,
        "articles": articles,
    }

def main():
    log("=== 啟動台股 150 檔、大盤、美股與週末新聞爬蟲作業 ===")
    universe = load_universe()
    
    # 1. 抓取股價歷史與技術指標
    stock_dfs = fetch_yfinance_history(universe, period="1y")
    if not stock_dfs:
        raise RuntimeError("無法連線取得股價歷史資料（請確認網路權限），中止覆寫本地資料。")
    
    # 2. 抓取法人買賣超
    flows = fetch_institutional_flows(days=6)
    
    # 若有最新交易日但 Yahoo Finance 尚未更新，從 TWSE / TPEx 官方盤後 API 補齊
    latest_d_str = None
    if flows:
        sample_code = next(iter(flows))
        if flows[sample_code]:
            latest_d_str = flows[sample_code][0]["date"]
            latest_d = datetime.date.fromisoformat(latest_d_str)
            stock_dfs = supplement_with_official_daily_quotes(stock_dfs, latest_d)
    
    # 3. 抓股市值
    market_caps = fetch_market_caps(universe)
    
    # 4. 抓取美股與 ADR 最新收盤行情
    us_market = fetch_us_and_adr_market()
    
    # 5. 抓取交易日官方 VWAP（供帳務結算精確核對）
    vwap_dates = ["2026-10-27", "2026-10-30", "2026-11-02", "2026-11-03", "2026-11-04"]
    if latest_d_str and latest_d_str not in vwap_dates:
        vwap_dates.append(latest_d_str)
    official_vwap = fetch_official_vwap_for_dates(sorted(vwap_dates), universe)
    
    # 6. 抓取近期與當日財經新聞（涵蓋近 2 日至最新盤前）
    recent_start = (datetime.date.today() - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
    weekend_news = fetch_weekend_and_recent_news(universe, start_date=recent_start)
    
    # 7. 儲存各股資料至 CSV / JSON
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

    # 儲存美股與 ADR 行情
    us_path = DATA_DIR / "us_market_latest.json"
    with open(us_path, "w", encoding="utf-8") as f:
        json.dump(us_market, f, ensure_ascii=False, indent=2)

    # 儲存官方 VWAP 結算參考表
    vwap_path = DATA_DIR / "official_vwap.json"
    with open(vwap_path, "w", encoding="utf-8") as f:
        json.dump(official_vwap, f, ensure_ascii=False, indent=2)

    # 儲存週末與近期新聞
    news_path = DATA_DIR / "weekend_news.json"
    with open(news_path, "w", encoding="utf-8") as f:
        json.dump(weekend_news, f, ensure_ascii=False, indent=2)

    log(f"歷史數據、美股行情與週末新聞爬取完成！所有檔案已存放至: {DATA_DIR}")

if __name__ == "__main__":
    main()
