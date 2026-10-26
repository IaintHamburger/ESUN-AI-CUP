"""Automatic market data; immutable request snapshots; no embedded API keys."""
from __future__ import annotations
import gzip
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from .core import TZ, require

def load_env(path):
    """Small dotenv reader; existing process environment always wins."""
    if not path.exists(): return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",k.strip()):
            os.environ.setdefault(k.strip(),v.strip().strip("\"'"))

def atomic_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    tmp.replace(path)

class Client:
    def __init__(self, cache):
        self.cache=Path(cache);self.cache.mkdir(parents=True,exist_ok=True)

    def get(self,url,headers=None):
        # Cache names and logs never contain tokens. No stale-cache fallback.
        key=hashlib.sha256(url.encode()).hexdigest()
        for attempt in range(3):
            try:
                req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 ESUN-simple-agent",**(headers or {})})
                with urllib.request.urlopen(req,timeout=25) as response:
                    raw=response.read()
                data=json.loads(raw)
                (self.cache/(key+".json")).write_bytes(raw)
                return data
            except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError,json.JSONDecodeError) as e:
                if attempt==2 or (isinstance(e,urllib.error.HTTPError) and e.code in (400,401,403,404,422)):
                    # Sanitise URL (FinMind can include a token in its query).
                    raise ValueError(f"Data request failed: {urllib.parse.urlsplit(url).hostname} ({type(e).__name__})") from None
                time.sleep(attempt+1)

    def finmind(self,dataset,code,start,end):
        q={"dataset":dataset,"start_date":start,"end_date":end}
        if code:q["data_id"]=code
        token=os.environ.get("FINMIND_API_TOKEN","")
        headers={"Authorization":"Bearer "+token} if token else {}
        data=self.get("https://api.finmindtrade.com/api/v4/data?"+urllib.parse.urlencode(q),headers)
        require(data.get("status")==200 and isinstance(data.get("data"),list),f"FinMind {dataset}: unavailable or membership required")
        return data["data"]

def yahoo_rows(data, cutoff):
    results=data.get("chart",{}).get("result")
    require(bool(results),"Yahoo chart result missing")
    r=results[0];q=r["indicators"]["quote"][0]
    adj=r["indicators"].get("adjclose",[{}])[0].get("adjclose")
    require(adj is not None,"Adjusted close missing; cannot silently use unadjusted signals")
    rows=[]
    for i,t in enumerate(r.get("timestamp",[])):
        d=datetime.fromtimestamp(t,TZ).date().isoformat()
        if d>cutoff:continue
        values={k:q[k][i] for k in ("open","high","low","close","volume")}
        if any(v is None for v in values.values()) or adj[i] is None:continue
        rows.append({"date":d,**values,"adjusted_close":adj[i]})
    return sorted(rows,key=lambda x:x["date"])

def source(name,url,as_of,hour="13:30:00"):
    return {"authority":"vendor","url":url,"name":name,"content_as_of":as_of+"T"+hour+"+08:00"}

def trading_dates(client,start,end):
    rows=client.finmind("TaiwanStockTradingDate",None,start,end)
    dates=sorted({r["date"] for r in rows if start<=r["date"]<=end})
    require(bool(dates),"Trading calendar missing")
    return dates

def choose_trade_date(client,now):
    # Use the data provider's published trading calendar, NOT weekday guesses.
    today=now.date()
    dates=trading_dates(client,(today-timedelta(days=10)).isoformat(),(today+timedelta(days=10)).isoformat())
    candidates=[d for d in dates if d>=today.isoformat()]
    require(bool(candidates),"No next trading date")
    return candidates[0]

def get_etfs(client,codes,as_of):
    output,errors={},[]
    fugle=os.environ.get("FUGLE_API_KEY","")
    start=(date.fromisoformat(as_of)-timedelta(days=7)).isoformat()
    for etf in codes:
        try:
            components=[];effective=""
            if fugle:
                url=f"https://api.fugle.tw/marketdata/v1.0/stock/ownership/etf-holdings/{etf}?from={start}&to={as_of}"
                data=client.get(url,{"X-API-KEY":fugle}).get("data",[])
                valid=[r for r in data if start<=r.get("date","")<=as_of]
                require(bool(valid),f"No Fugle holdings {etf}")
                row=max(valid,key=lambda r:r["date"]);effective=row["date"]
                components=[(str(c["symbol"]),float(c["weight"])/100) for c in row["components"] if float(c.get("weight",0))>0]
            else:
                rows=client.finmind("TaiwanStockActiveETFHolding",etf,start,as_of)
                valid=[r for r in rows if start<=r.get("date","")<=as_of]
                require(bool(valid),f"No FinMind holdings {etf}")
                effective=max(r["date"] for r in valid)
                components=[(str(r["component_stock_id"]),float(r["weight"])/100) for r in valid if r["date"]==effective and r.get("component_stock_id") and float(r.get("weight",0))>0 and str(r.get("asset_type","")).lower() not in ("cash","現金")]
            # All equity identifiers (including foreign holdings) remain in union.
            weights={c:w for c,w in components}
            require(len(weights)>=10 and .1<sum(weights.values())<=1.02,f"Incomplete/mis-scaled ETF {etf}")
            require(effective==as_of,f"ETF {etf} stale: {effective}, expected {as_of}")
            output[etf]={"date":effective,"weights":weights}
        except (ValueError,KeyError,TypeError):
            errors.append(etf)
    return output,errors

def fetch_live(root,trade_date=None):
    now=datetime.now(TZ); client=Client(root/"runtime"/"snapshots"/now.date().isoformat())
    trade_date=trade_date or choose_trade_date(client,now)
    day=date.fromisoformat(trade_date)
    calendar=trading_dates(client,(day-timedelta(days=14)).isoformat(),trade_date)
    require(trade_date in calendar,"Trade date is not in published market calendar")
    before=[d for d in calendar if d<trade_date]
    require(bool(before),"Missing previous trading date")
    as_of=before[-1]
    # Official closes are the authoritative derive-orders.v2 input. Refuse to
    # treat an outdated exchange snapshot as the previous market session.
    official_closes={}
    official_sources=[]
    endpoints=[("twse","https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL","Code","ClosingPrice"),
               ("tpex","https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes","SecuritiesCompanyCode","Close")]
    for authority,url,codekey,pricekey in endpoints:
        rows=client.get(url)
        for r in rows:
            raw=str(r.get("Date",""))
            d=f"{int(raw[:3])+1911}-{raw[3:5]}-{raw[5:7]}" if len(raw)==7 and raw.isdigit() else raw
            if d!=as_of:continue
            try:
                value=float(str(r[pricekey]).replace(",",""))
                if value>0:official_closes[str(r[codekey])]=value
            except (ValueError,KeyError):pass
        require(any(str(r.get("Date","")) in (as_of,str(int(as_of[:4])-1911)+as_of[5:7]+as_of[8:10]) for r in rows),f"Official {authority} snapshot stale; expected {as_of}. No stale-price submission allowed")
        official_sources.append({**source(authority+"官方前日收盤價",url,as_of),"authority":authority})
    universe=json.loads((root/"simple_agent"/"universe.json").read_text(encoding="utf-8"))
    etfcodes=json.loads((root/"simple_agent"/"etfs.json").read_text())
    # 400 calendar days provide >=250 observations; no paid price API required.
    start=(day-timedelta(days=440)).isoformat()
    period1=int(datetime.combine(date.fromisoformat(start),datetime.min.time(),TZ).timestamp())
    period2=int(datetime.combine(day,datetime.min.time(),TZ).timestamp())
    price_sources={}
    def prices(code):
        symbol="^TWII" if code=="INDEX" else code+(".TWO" if universe[code]["exchange"]=="tpex" else ".TW")
        url="https://query1.finance.yahoo.com/v8/finance/chart/"+urllib.parse.quote(symbol)+f"?period1={period1}&period2={period2}&interval=1d&events=div%2Csplits"
        price_sources[code]=url
        return yahoo_rows(client.get(url),as_of)
    all_prices={};errors=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(prices,c):c for c in ["INDEX",*universe]}
        for future in as_completed(futures):
            c=futures[future]
            try:
                rows=future.result()
                if c!="INDEX":
                    require(c in official_closes,"Official closing price missing")
                    require(rows and rows[-1]["date"]==as_of and abs(rows[-1]["close"]-official_closes[c])<=max(.0001,official_closes[c]*1e-7),"Vendor and official close disagree")
                    rows[-1]["close"]=official_closes[c]
                all_prices[c]=rows
            except Exception as e:errors.append(c)
    require("INDEX" in all_prices and all_prices["INDEX"][-1]["date"]==as_of,"Market index is missing or stale")
    # Original institutional factor is optional, explicitly missing when unavailable.
    flows={};flow_missing=[]
    for c in universe:
        try:flows[c]=client.finmind("TaiwanStockInstitutionalInvestorsBuySell",c,(day-timedelta(days=12)).isoformat(),as_of)
        except ValueError:flow_missing.append(c)
    # Free official issued ordinary shares * official previous close. No par-value
    # assumption or stale hand-written list. Core is explicitly listed financials.
    caps={};cap_dates={};cap_publication_date=None
    rows=client.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L")
    for r in rows:
        c=str(r.get("公司代號",""));share=r.get("已發行普通股數或TDR原股發行股數")
        if c in universe and universe[c]["industry"]=="金融" and c in all_prices and share:
            rawdate=str(r.get("出表日期",""))
            require(len(rawdate)==7 and rawdate.isdigit(),"Missing company-table publication date")
            published=f"{int(rawdate[:3])+1911}-{rawdate[3:5]}-{rawdate[5:7]}"
            require(as_of<=published<=now.date().isoformat(),"Stale/future company shares table")
            caps[c]=float(str(share).replace(",",""))*official_closes[c];cap_dates[c]=as_of
            cap_publication_date=published
    etfs,etf_missing=get_etfs(client,etfcodes,as_of)
    sources=[source("Yahoo調整後日價與加權指數","https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII",as_of),
             source("FinMind法人買賣資料","https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell",as_of,"18:00:00"),
             source("金融普通股市值：TWSE最新發行股數乘官方前日收盤","https://openapi.twse.com.tw/v1/opendata/t187ap03_L",cap_publication_date or as_of,"00:00:00")]
    sources[2]["authority"]="twse"
    sources.extend(official_sources)
    if etfs:
        sources.append(source("主動ETF每日持股","https://developer.fugle.tw/docs/data/http-api/ownership/etf-holdings/" if os.environ.get("FUGLE_API_KEY") else "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockActiveETFHolding",as_of,"18:00:00"))
    sources[0]["url"]=price_sources["INDEX"]
    bundle={"trade_date":trade_date,"as_of":as_of,"official_closes":official_closes,"price_sources":price_sources,"prices":{c:v for c,v in all_prices.items() if c!="INDEX"},"index":all_prices["INDEX"],"flows":flows,"market_caps":caps,"market_cap_dates":cap_dates,"etfs":etfs,"sources":sources,"data_errors":{"price_missing":errors,"flow_missing":flow_missing,"etf_missing":etf_missing},"demo":False}
    atomic_json(root/"runtime"/"market.json",bundle)
    return bundle

def load_demo(root):
    with gzip.open(root/"simple_agent"/"fixtures"/"market_2026-10-22.json.gz","rt",encoding="utf-8") as f:
        return json.load(f)
