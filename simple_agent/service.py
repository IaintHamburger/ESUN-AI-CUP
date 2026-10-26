"""Account sync, bounded AI review, and a deliberately explicit submit adapter.

The organizer's private API contract was not supplied. URL/body/receipt mapping
must be verified once by the team. An HTTP200 by itself never means accepted.
"""
from __future__ import annotations
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from .core import TZ, require, active_share
from .data import atomic_json

def https_url(url):
    p=urllib.parse.urlsplit(url)
    require(p.scheme=="https" and p.hostname and not p.username and not p.password,"Adapter URL must be HTTPS without embedded credentials")
    return url

def call_json(url,payload=None,headers=None):
    https_url(url)
    req=urllib.request.Request(url,data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None,
        headers={"Content-Type":"application/json",**(headers or {})},method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(req,timeout=45) as r:return json.load(r)
    except Exception as e:
        # Never echo raw API exceptions/URLs/auth headers to logs.
        raise ValueError(f"API request failed ({type(e).__name__}); outcome may be unknown, check receipt before retry") from None

def check_state(state,bundle,universe,initial=False):
    require(isinstance(state,dict) and isinstance(state.get("holdings"),dict),"Invalid state shape")
    require(all(isinstance(q,int) and q>=0 for q in state["holdings"].values()),"Invalid holding quantities")
    require(set(state["holdings"])<=set(universe),"Account contains non-universe stocks")
    require(state["nav"]>0 and 0<=state["cash"]<=state["nav"],"Invalid account NAV/cash")
    require(all(q%1000==0 for q in state["holdings"].values()),"Corporate action produced odd lots; organizer lot treatment required")
    if initial:
        require(not state["holdings"] and state["nav"]==1_000_000_000 and state["cash"]==1_000_000_000,"Initial state must be official10億 empty portfolio")
    else:
        require(state.get("trade_date")==bundle["as_of"],"Organizer state not from previous trading day")
    prices={c:r[-1]["close"] for c,r in bundle["prices"].items() if r and r[-1]["date"]==bundle["as_of"]}
    require(set(state["holdings"])<=set(prices),"Missing current holding quotes")
    reconstructed=state["cash"]+sum(q*prices[c] for c,q in state["holdings"].items())+float(state.get("dividend_receivable",0))
    require(abs(reconstructed-state["nav"])<=max(10,state["nav"]*.0001),"Organizer NAV disagrees with current holdings/quotes; do not silently trade")
    return state

def auth_headers():
    token=os.environ.get("ESUN_API_TOKEN","")
    require(bool(token),"Missing ESUN_API_TOKEN")
    return {"Authorization":"Bearer "+token}

def sync_state(root,bundle,universe,adapter):
    if os.environ.get("ESUN_INITIAL_PORTFOLIO_CONFIRMED")=="1" and not (root/"runtime"/"initial_used.json").exists():
        # First successful submission consumes this flag, even across restarts.
        state={"trade_date":bundle["as_of"],"nav":1_000_000_000,"cash":1_000_000_000,"holdings":{}}
        return check_state(state,bundle,universe,initial=True)
    url=adapter.get("holdings_url","")
    require(bool(url),"Official holdings URL/adapter not configured; no manual settlement guessing")
    body=call_json(url,headers=auth_headers())
    key=adapter.get("holdings_root","")
    state=body[key] if key else body
    return check_state(state,bundle,universe)

def review(result,bundle):
    key=os.environ.get("GOOGLE_API_KEY","")
    require(bool(key),"Missing GOOGLE_API_KEY (live autonomous AI review required)")
    model=os.environ.get("GEMINI_MODEL","gemini-3.5-flash-lite")
    require(all(c.isalnum() or c in "-._" for c in model),"Invalid model name")
    prompt="""你是玉山AI CUP基金Agent的風險覆核員。只能根據提供的數值決定accept或reject，不可編造行情、改股數或假稱讀過外部文件。策略是台積電20%、金融核心、20檔動能，依指數EMA分配，負分不放空。審查風險與敘述是否一致。輸出JSON: {\"accept\":true/false,\"reason\":\"80字內繁體中文依據\",\"prospectus\":\"繁體中文：ETF名稱、投資主題、投資理念、風險，每項一小段；不得保證收益。\"}。接受只代表同意規則推導；無法承諾獲利。數值："""
    facts={"trade_date":bundle["trade_date"],"as_of":bundle["as_of"],"regime":result["regime"],"weights":result["projected"]["weights"],"cash_pct":result["projected"]["cash_pct"],"orders":result["orders"]}
    body={"contents":[{"parts":[{"text":prompt+json.dumps(facts,ensure_ascii=False)}]}],"generationConfig":{"temperature":0,"responseMimeType":"application/json","maxOutputTokens":4096}}
    data=call_json(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",body,{"x-goog-api-key":key})
    try:
        text="".join(p.get("text","") for p in data["candidates"][0]["content"]["parts"] if not p.get("thought"))
        output=json.loads(text)
    except (KeyError,IndexError,json.JSONDecodeError):raise ValueError("Gemini returned incomplete/invalid review") from None
    require(type(output.get("accept")) is bool and isinstance(output.get("reason"),str) and len(output["reason"])>=5,"Invalid AI review contract")
    require(isinstance(output.get("prospectus"),str) and len(output["prospectus"])>=40,"Missing AI prospectus text")
    require(output["accept"],"AI rejected plan: "+output["reason"][:200])
    return {**output,"model":data.get("modelVersion",model),"requested_model":model,"raw_response":data}

def active_checks(result,bundle,etfcodes):
    missing=sorted(set(etfcodes)-set(bundle.get("etfs",{})))
    rows=[]
    for c,b in bundle.get("etfs",{}).items():
        require(b["date"]==bundle["as_of"],"Stale Active Share benchmark")
        a=active_share(result["projected"]["weights"],b["weights"])
        n=active_share(result["projected"]["weights"],b["weights"],True)
        rows.append({"etf":c,"date":b["date"],"raw_nav_top10":a,"normalized_top10":n,"pass":a>=.20 and n>=.20})
    return {"pass":not missing and len(rows)==30 and all(r["pass"] for r in rows),"missing":missing,"details":rows,
            "note":"Both interpretations checked conservatively; official top10 weighting convention still requires confirmation."}

def window_check(trade_date,now=None):
    now=now or datetime.now(TZ)
    d=date.fromisoformat(trade_date)
    lo=datetime.combine(d-timedelta(days=1),datetime.min.time(),TZ).replace(hour=19,minute=30)
    hi=datetime.combine(d,datetime.min.time(),TZ).replace(hour=8,minute=55)
    require(lo<=now<hi,"Outside official submission window19:30 to08:55 Taipei")

def adapter_config(root):
    path=root/"submit_adapter.json"
    require(path.exists(),"Missing submit_adapter.json; copy example and verify organizer API contract once")
    a=json.loads(path.read_text(encoding="utf-8"))
    require(a.get("verified_against_official_docs") is True,"Submission adapter not verified against official documentation")
    return a

def send(root,plan,path,adapter,checks,memory):
    require(checks["pass"],"Active Share incomplete or below20%; cannot submit")
    window_check(plan["trade_date"])
    require("2026-10-26"<=plan["trade_date"]<="2026-11-27","Outside initial contest period")
    receipt_path=root/"runtime"/"receipts"/(plan["trade_date"]+".json")
    attempt_path=root/"runtime"/"attempts"/(plan["trade_date"]+".json")
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if receipt_path.exists():
        prev=json.loads(receipt_path.read_text())
        require(prev.get("sha256")==digest,"Already accepted a different plan today; auto-replacement disabled")
        return {"status":"already_accepted",**prev}
    # An ambiguous timeout blocks automatic retries, avoiding accidental duplicate writes.
    prior=json.loads(attempt_path.read_text()) if attempt_path.exists() else {"count":0}
    require(not prior.get("pending"),"Previous submission outcome unknown; check organizer before retry")
    require(prior["count"]<25,"Daily submission limit reached")
    url=adapter.get("submit_url","");https_url(url)
    require(adapter.get("body_mode") in ("json","wrapped_json"),"Configure supported official request body; multipart requires team adapter")
    body=plan if adapter["body_mode"]=="json" else {adapter["body_field"]:plan}
    atomic_json(attempt_path,{"count":prior["count"]+1,"pending":True,"sha256":digest})
    response=call_json(url,body,auth_headers())
    key=adapter.get("accepted_field","")
    require(key in response and response[key]==adapter.get("accepted_value"),"Organizer did not explicitly confirm acceptance; outcome pending")
    idkey=adapter.get("receipt_id_field","")
    require(bool(response.get(idkey)),"Missing organizer receipt ID; outcome pending")
    # Echo identity/date checks are required in the verified adapter.
    require(response.get(adapter["team_field"])==plan["team_id"] and response.get(adapter["trade_date_field"])==plan["trade_date"],"Receipt belongs to another team/date")
    receipt={"sha256":digest,"receipt_id":str(response[idkey]),"team_id":plan["team_id"],"trade_date":plan["trade_date"],"accepted_at":datetime.now(TZ).isoformat(timespec="seconds"),"raw_response":response}
    atomic_json(receipt_path,receipt)
    atomic_json(root/"runtime"/"initial_used.json",{"trade_date":plan["trade_date"]})
    atomic_json(root/"runtime"/"strategy_memory.json",memory)
    atomic_json(attempt_path,{"count":prior["count"]+1,"pending":False,"accepted":True})
    return {"status":"accepted","receipt_id":receipt["receipt_id"]}
