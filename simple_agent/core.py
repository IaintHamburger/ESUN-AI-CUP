"""Pure calculations. Prices in TWD; weights are NAV fractions; no shorting.

The strategy is a modification of the team's supplied momentum score, not a
trained predictive model. Keep all behaviour-changing constants in this file.
"""
from __future__ import annotations
import hashlib
import json
import math
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path

TZ = timezone(timedelta(hours=8))
FEE, TAX, LOT = .001425, .003, 1000
PARAMS = {"ema_periods": [5, 10, 20, 60, 120], "atr_period": 20,
          "slope_deadband_atr": .02, "candle_atr": 1.0, "volume_ratio": 1.5,
          "cross_group_cap": 4, "flow_ratio": 3, "rsi_deadband": 5,
          "volatility_floor": .005, "volatility_power": .5,
          "stop_atr_multiple": 3, "minimum_stop_pct": .06,
          "ranking_exit_percentile": .70, "ranking_exit_days": 2,
          "rebalance_band": .003, "max_replacements": 2,
          "active_share_min": .20}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def ema(values, period):
    require(bool(values), "EMA needs prices")
    a, result = 2 / (period + 1), [float(values[0])]
    for v in values[1:]:
        result.append(a * float(v) + (1 - a) * result[-1])
    return result

def clip(x, low=-1.0, high=1.0):
    return max(low, min(high, x))

def sign(x):
    return (x > 0) - (x < 0)

def indicators(rows):
    require(len(rows) >= 125, "Need >=125 observations (prefer >=250 for EMA warm-up)")
    for r in rows:
        require(all(math.isfinite(float(r[k])) for k in ("open", "high", "low", "close", "volume", "adjusted_close")), "Non-finite price")
        require(r["close"] > 0 and r["adjusted_close"] > 0 and 0 < r["low"] <= min(r["open"], r["close"]) <= max(r["open"], r["close"]) <= r["high"] and r["volume"] >= 0, "Invalid OHLCV")
    # Historical adjusted OHLC removes split/dividend discontinuities for signals.
    # Raw prices remain the ONLY prices used in orders and account valuation.
    c = [r["adjusted_close"] for r in rows]
    factors = [r["adjusted_close"] / r["close"] for r in rows]
    h = [r["high"] * f for r, f in zip(rows, factors)]
    l = [r["low"] * f for r, f in zip(rows, factors)]
    tr = [h[0] - l[0]] + [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])) for i in range(1,len(c))]
    atr = max(ema(tr, PARAMS["atr_period"])[-1], c[-1] * PARAMS["volatility_floor"])
    avgs = {p: ema(c, p) for p in PARAMS["ema_periods"]}
    changes = [c[i]-c[i-1] for i in range(1,len(c))]
    gains = ema([max(d, 0) for d in changes], 19)[-1]  # Wilder alpha=1/10
    losses = ema([max(-d, 0) for d in changes], 19)[-1]
    rsi = 50 if gains + losses == 0 else 100 * gains / (gains + losses)
    slopes = {p: (avgs[p][-1]-avgs[p][-2]) / atr for p in (20,60,120)}
    trend = sum(0 if abs(x) < PARAMS["slope_deadband_atr"] else clip(x / .15) for x in slopes.values())
    vol5 = sum(r["volume"] for r in rows[-6:-1]) / 5  # excludes today
    vr = rows[-1]["volume"] / vol5 if vol5 > 0 else 0
    candle = (rows[-1]["close"] - rows[-1]["open"]) * factors[-1] / atr
    candle_score = 3 * sign(candle) if abs(candle) >= PARAMS["candle_atr"] and vr >= PARAMS["volume_ratio"] else 0
    crosses = sum(2 if c[-2] <= a[-2] and c[-1] > a[-1] else -2 if c[-2] >= a[-2] and c[-1] < a[-1] else 0 for a in avgs.values())
    cross_score = clip(crosses, -PARAMS["cross_group_cap"], PARAMS["cross_group_cap"])
    rsi_score = 2 * clip((rsi-50) / 20) if abs(rsi-50) > PARAMS["rsi_deadband"] else 0
    return {"close": rows[-1]["close"], "return_1d": c[-1]/c[-2]-1,
            "atr_pct": atr/c[-1], "rsi10": rsi, "volume_ratio": vr,
            "trend_score": trend, "candle_score": candle_score,
            "cross_score": cross_score, "rsi_score": rsi_score,
            "score": trend+candle_score+cross_score+rsi_score,
            "adjusted_close": c[-1], "ema20": avgs[20][-1], "ema60": avgs[60][-1]}

def flow_score(rows, investor, as_of):
    r = sorted([x for x in rows if x["name"] == investor and x["date"] <= as_of],key=lambda x:x["date"])[-3:]
    if len(r) < 3 or r[-1]["date"] != as_of or len({x["date"] for x in r})!=3:
        return None
    b, s = sum(float(x["buy"]) for x in r), sum(float(x["sell"]) for x in r)
    up = sum(float(x["buy"]) > float(x["sell"]) for x in r)
    down = sum(float(x["buy"]) < float(x["sell"]) for x in r)
    ratio=PARAMS["flow_ratio"]
    return 3 if up >= 2 and b > ratio*s and b > 0 else -3 if down >= 2 and s > ratio*b and s > 0 else 0

def score_universe(bundle, universe):
    scored, excluded = {}, {}
    for code, meta in universe.items():
        rows = bundle["prices"].get(code, [])
        try:
            require(bool(rows) and rows[-1]["date"] == bundle["as_of"], "Quote missing/stale")
            s = indicators(rows)
            s.update(meta)
            s["missing_factors"] = []
            for name, key in [("Foreign_Investor", "foreign_score"), ("Investment_Trust", "trust_score")]:
                f = flow_score(bundle.get("flows", {}).get(code, []), name, bundle["as_of"])
                if f is None:
                    s["missing_factors"].append(key)
                s[key] = f or 0
                s["score"] += f or 0
            scored[code] = s
        except ValueError as e:
            excluded[code] = str(e)
    market = indicators(bundle["index"])
    for s in scored.values():
        peers = [x for x in scored.values() if x["industry"] == s["industry"]]
        breadth = sum(x["return_1d"] > market["return_1d"] for x in peers)/len(peers)
        s["industry_breadth"] = breadth
        s["breadth_score"] = 2 if breadth > .5 else -2 if breadth < .5 else 0
        s["score"] += s["breadth_score"]
    return scored, excluded, market

def regime(index_rows):
    c = [r["adjusted_close"] for r in index_rows]
    a, b = ema(c,20), ema(c,60)
    if all(c[i] > a[i] for i in (-1,-2,-3)):
        return "risk_on"
    if all(c[i] < b[i] for i in (-1,-2,-3)):
        return "risk_off"
    return "neutral"

def target_shares(weight, nav, price):
    require(price > 0 and nav > 0 and 0 <= weight <= .25, "Invalid order inputs")
    value = Decimal(str(weight))*Decimal(str(nav))/Decimal(str(price))/LOT
    return int(value.to_integral_value(rounding=ROUND_FLOOR))*LOT

def exact_weight(shares, nav, price):
    # A midpoint within the same floor bucket avoids float/decimal fenceposts.
    return float((Decimal(shares)+Decimal("0.001"))*Decimal(str(price))/Decimal(str(nav))) if shares else 0.0

def reference_return(rows, record, current_adjusted):
    # Yahoo rebases older adjusted prices after new corporate actions. Look up
    # the entry signal DATE in today's adjusted history, not yesterday's scalar.
    if record.get("reference_date"):
        entry=[r for r in rows if r["date"]==record["reference_date"]]
        require(len(entry)==1,"Entry signal date missing from adjusted history")
        reference=entry[0]["adjusted_close"]
    else:
        reference=record.get("reference_adjusted",current_adjusted)
    require(reference>0,"Invalid signal reference")
    return current_adjusted/reference-1

def project(state, orders, prices, buy_multiplier=1, sell_multiplier=1):
    cash, holdings = float(state["cash"]), dict(state["holdings"])
    for o in orders:
        q, code = o["shares"], o["ticker"]
        require(code in prices and prices[code] > 0, f"Missing valuation {code}")
        require(isinstance(q,int) and q > 0 and q%LOT == 0, "Order must be positive whole lots")
        if o["side"] == "BUY":
            cash -= q*prices[code]*buy_multiplier*(1+FEE)
            holdings[code] = holdings.get(code,0)+q
        else:
            require(o["side"] == "SELL", "Unknown order side")
            require(holdings.get(code,0) >= q, "Short sale forbidden")
            cash += q*prices[code]*sell_multiplier*(1-FEE-TAX)
            holdings[code] -= q
            if holdings[code] == 0:
                del holdings[code]
    require(all(x in prices for x in holdings), "Missing holding valuation")
    nav = cash+sum(q*prices[c] for c,q in holdings.items())+float(state.get("dividend_receivable",0))
    require(nav > 0, "NAV <=0")
    return {"cash": cash, "holdings": holdings, "nav": nav,
            "cash_pct": cash/nav, "weights": {c:q*prices[c]/nav for c,q in holdings.items()}}

def active_share(weights, benchmark, normalized=False):
    a = dict(sorted(weights.items(), key=lambda x:(-x[1],x[0]))[:10])
    b = dict(sorted(benchmark.items(), key=lambda x:(-x[1],x[0]))[:10])
    require(a and b and sum(a.values()) > 0 and sum(b.values()) > 0, "Empty Active Share input")
    if normalized:
        sa,sb = sum(a.values()),sum(b.values())
        a,b = {k:v/sa for k,v in a.items()}, {k:v/sb for k,v in b.items()}
    return .5*sum(abs(a.get(c,0)-b.get(c,0)) for c in a.keys()|b.keys())

def construct(bundle, universe, state, memory=None):
    memory = json.loads(json.dumps(memory or {}))
    scored, excluded, market = score_universe(bundle, universe)
    for code in state["holdings"]:
        require(code in scored, f"Held {code} lacks current valid indicators; cannot silently drop")
    require("2330" in scored, "TSMC missing")
    # Financial core remains the largest five by actual dated market values.
    financial_all = [c for c,m in universe.items() if m["industry"] == "金融" and m["exchange"] == "twse"]
    require(all(c in scored and bundle.get("market_caps",{}).get(c,0)>0 for c in financial_all),"Financial ranking requires ALL eligible listed financial market caps")
    financial = list(financial_all)
    financial.sort(key=lambda c:(-bundle["market_caps"][c],c))
    require(len(financial)>=5, "Need five dated financial market caps")
    financial = financial[:5]
    candidates = [c for c,s in scored.items() if universe[c]["momentum"] and c != "2330" and c not in financial]
    candidates.sort(key=lambda c:(-scored[c]["score"],c))
    require(len(candidates)>=20, "Need >=20 eligible momentum candidates")
    ranks = {c:i+1 for i,c in enumerate(candidates)}
    old = [c for c in state["holdings"] if c in candidates]
    pool = list(old)
    replaced, reasons = 0, {}
    for c in old:
        rec = memory.setdefault(c,{})
        # Repeated execution on the same date never manufactures two daily votes.
        if rec.get("rank_date") != bundle["as_of"]:
            rec["bad_rank_days"] = rec.get("bad_rank_days",0)+1 if ranks[c] > len(candidates)*PARAMS["ranking_exit_percentile"] else 0
            rec["rank_date"] = bundle["as_of"]
        pnl = reference_return(bundle["prices"][c],rec,scored[c]["adjusted_close"])
        stop = max(PARAMS["minimum_stop_pct"], PARAMS["stop_atr_multiple"]*rec.get("entry_atr_pct",scored[c]["atr_pct"]))
        exit_ = pnl <= -stop or (rec["bad_rank_days"] >= PARAMS["ranking_exit_days"] and pnl >= .15)
        if exit_ and replaced < PARAMS["max_replacements"]:
            pool.remove(c); replaced+=1
            reasons[c] = f"Exit: adjusted return since entry signal={pnl:.4f}, stop={stop:.4f}, weak_rank_days={rec['bad_rank_days']}"
    # Fill slots without buying a just-exited stock back the same day.
    pool += [c for c in candidates if c not in pool and c not in reasons][:max(0,20-len(pool))]
    pool = sorted(pool,key=lambda c:(-scored[c]["score"],c))[:20]
    require(len(pool)==20,"Unable to fill momentum sleeve")
    reg = regime(bundle["index"])
    cash,fin,mom = {"risk_on":(.10,.10,.60),"neutral":(.15,.20,.45),"risk_off":(.20,.30,.30)}[reg]
    target = {"2330":.20, **{c:fin/5 for c in financial}}
    risk = {c:(2 if i<10 else 1)/(max(PARAMS["volatility_floor"],scored[c]["atr_pct"])**PARAMS["volatility_power"]) for i,c in enumerate(pool)}
    # Water-fill capped allocations. Never redistribute past the 8% design cap.
    pending, budget = set(pool), mom
    for _ in range(21):
        if not pending: break
        total = sum(risk[c] for c in pending)
        capped = [c for c in pending if budget*risk[c]/total > .08]
        if not capped:
            target.update({c:budget*risk[c]/total for c in pending}); break
        for c in capped: target[c]=.08; budget-=.08; pending.remove(c)
    prices = {c:s["close"] for c,s in scored.items()}
    final = {c:target_shares(w,state["nav"],prices[c]) for c,w in target.items()}
    final = {c:q for c,q in final.items() if q>0}
    # Preserve held lot quantities inside a small band to limit fees/turnover.
    for c,q in state["holdings"].items():
        if c in target and abs(q*prices[c]/state["nav"]-target[c]) < PARAMS["rebalance_band"]:
            final[c]=q
    orders, decisions = [], []
    for c in sorted(set(final)|set(state["holdings"])):
        oldq,newq = state["holdings"].get(c,0),final.get(c,0)
        if oldq==newq: continue
        did=f"D{len(decisions)+1}"
        action="BUY" if oldq==0 else "SELL_ALL" if newq==0 else "ADD" if newq>oldq else "TRIM"
        decisions.append({"decision_id":did,"ticker":c,"action":action,
                          "target_weight":exact_weight(newq,state["nav"],prices[c])})
        orders.append({"ticker":c,"side":"BUY" if newq>oldq else "SELL","shares":abs(newq-oldq),"decision_ref":did})
    projected=project(state,orders,prices)
    require(20<=len(projected["holdings"])<=30,"Projected holding count outside20-30")
    require(len(orders)<=30,"Schema permits at most30 orders; stopping instead of dropping exits")
    require(0<=projected["cash_pct"]<.25,"Projected cash outside[0,25%)")
    require(project(state,orders,prices,1.10,.90)["cash"]>=0,"Negative cash under +/-10% execution stress")
    for c,w in projected["weights"].items():
        require(w<=(.25 if c=="2330" else .10),f"Projected weight cap {c}")
    for c in pool:
        if not state["holdings"].get(c):
            memory[c]={"reference_date":bundle["as_of"],"reference_adjusted":scored[c]["adjusted_close"],"entry_atr_pct":scored[c]["atr_pct"],"bad_rank_days":0,"rank_date":bundle["as_of"]}
    return {"scores":scored,"excluded":excluded,"market":market,"regime":reg,
            "target_weights":target,"cash_budget":cash,"prices":prices,
            "orders":orders,"decisions":decisions,"projected":projected,
            "memory":memory,"exit_reasons":reasons}

def schema_path(root):
    return root / "參考資料" / "D-Plan.schema.json"

def build_dplan(result, bundle, state, team, schema, started, version, ai=None):
    asof = bundle["as_of"]+"T13:30:00+08:00"
    sources=[]
    # Max50 sources: group identical API families rather than one per ticker.
    for source in bundle["sources"]:
        sources.append({"source_id":f"S{len(sources)+1}", **source})
    observations=[{"obs_id":"O1","source_ref":["S1"],"topic":"market_prices",
                   "statement":"加權指數調整後收盤價與二十、六十日指數移動平均值。",
                   "values":{k:result["market"][k] for k in ("adjusted_close","ema20","ema60","return_1d")}}]
    inferences=[]
    codes=sorted(set(result["projected"]["holdings"])|set(state["holdings"]))
    infmap={}
    for c in codes:
        s=result["scores"][c]
        symbol=c+(".TWO" if s["exchange"]=="tpex" else ".TW")
        sid=f"S{len(sources)+1}"
        sources.append({"source_id":sid,"authority":"vendor","url":bundle.get("price_sources",{}).get(c,"https://query1.finance.yahoo.com/v8/finance/chart/"+symbol),"name":("DEMO frozen history " if bundle.get("demo") else "Adjusted daily OHLCV ")+symbol,"content_as_of":asof})
        # Individual stock history + flow + capitalisation + its own exchange.
        refs=[sid]+[x["source_id"] for x in sources[:3] if x["source_id"]!="S1"]
        official=[x["source_id"] for x in sources if x["authority"]==s["exchange"] and "官方前日收盤價" in x.get("name","")]
        refs+=official[:1]
        oid=f"O{len(observations)+1}"
        observations.append({"obs_id":oid,"source_ref":refs[:5],"topic":"stock_factors",
          "statement":f"{c} {s['name']}：收盤價、ATR占價格比例、RSI10、量比與當日策略因子分數；缺少法人因子數量另列。",
          "values":{k:float(s[k]) for k in ("close","atr_pct","rsi10","volume_ratio","trend_score","candle_score","cross_score","rsi_score","breadth_score","foreign_score","trust_score","score")} | {"missing_factor_count":len(s["missing_factors"])} | ({"market_cap_twd":float(bundle["market_caps"][c])} if c in bundle.get("market_caps",{}) else {})})
        iid=f"I{len(inferences)+1}"; infmap[c]=iid
        role="台積電核心20%" if c=="2330" else "金融市值核心，分配依市場狀態" if s["industry"]=="金融" else "動能組合，依排名與ATR平方根反比配置"
        logic=f"依公開策略：{role}；分數{s['score']:.3f}、ATR比例{s['atr_pct']:.4f}。僅持有現股；分數為負僅降低排名，不建立空單。"
        if c in result["exit_reasons"]: logic+=result["exit_reasons"][c]
        inferences.append({"inf_id":iid,"premise_refs":["O1",oid],"logic":logic,
          "counter_evidence":"趨勢與法人資訊可能落後，波動調整無法避免跳空損失。"})
    changed={d["ticker"] for d in result["decisions"]}
    decisions=[{**d,"inference_refs":[infmap[d["ticker"]]],"risk_check":"整張、資金、持股數、權重上限均以同一帳戶狀態重算。"} for d in result["decisions"]]
    net=sum((1 if o["side"]=="BUY" else -1)*o["shares"]*result["prices"][o["ticker"]] for o in result["orders"])
    cp=result["projected"]["cash_pct"]
    return {"schema_version":schema["properties"]["schema_version"]["const"],"doc_type":"D-Plan","team_id":team,"trade_date":bundle["trade_date"],
       "sources":sources,"observations":observations,
       "market_view":{"basis_refs":["O1"],"logic":f"依加權指數相對EMA20與EMA60連續三個交易日的位置，判為{result['regime']}；股票與現金預算依策略表調整。委託後現金比{cp:.4%}，以含交易成本的實際整張數量計算。"+("AI覆核："+ai["reason"][:250] if ai else ""),
          "regime":result["regime"],"stance":{"risk_on":"aggressive","neutral":"neutral","risk_off":"defensive"}[result["regime"]],
          "posture":{"net_exposure_intent":"hold" if abs(net)<=.02*state["nav"] else "increase" if net>0 else "reduce","target_cash_pct_range":[round(max(0,cp-.025),6),round(min(.249,cp+.025),6)]},
          "counter_evidence":"三日確認可能在急跌後才反應，且金融與台積電核心仍承擔市場風險。"},
       "inferences":inferences,"decisions":decisions,
       "no_trade_decisions":[{"ticker":c,"reason_refs":[infmap[c]],"reason":"仍在保留名單且部位差異低於調整門檻；避免不必要手續費。"} for c in sorted(state["holdings"]) if c not in changed],
       "orders":result["orders"],"agent_metadata":{"model_provider":"google" if ai else "other","model_version":ai["model"] if ai else "deterministic-demo-no-LLM","run_started_at":started,"run_completed_at":datetime.now(TZ).isoformat(timespec="seconds"),"code_version":version}}

def validate(plan, schema, state, prices, universe):
    from jsonschema import Draft202012Validator, FormatChecker
    errors=list(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(plan))
    require(not errors,"Schema: "+"; ".join(e.message for e in errors[:5]))
    layers=[("sources","source_id","S"),("observations","obs_id","O"),("inferences","inf_id","I"),("decisions","decision_id","D")]
    ids={}
    for layer,key,prefix in layers:
        sequence=[x[key] for x in plan[layer]]
        require(sequence==[f"{prefix}{i+1}" for i in range(len(sequence))],f"Non-contiguous/duplicate {layer} IDs")
        ids[prefix]=set(sequence)
    for x in plan["observations"]: require(set(x["source_ref"])<=ids["S"],"Broken source ref")
    completed=datetime.fromisoformat(plan["agent_metadata"]["run_completed_at"])
    require(datetime.fromisoformat(plan["agent_metadata"]["run_started_at"])<=completed,"Reversed run timestamps")
    cutoff=datetime.fromisoformat(plan["trade_date"]+"T08:55:00+08:00")
    for x in plan["sources"]:
        require(datetime.fromisoformat(x["content_as_of"])<cutoff,"Future source content beyond cutoff")
        if "fetched_at" in x:require(datetime.fromisoformat(x["fetched_at"])<=completed,"Future fetch timestamp")
    require(set(plan["market_view"]["basis_refs"])<=ids["O"],"Broken market ref")
    for x in plan["inferences"]: require(set(x["premise_refs"])<=ids["O"],"Broken premise ref")
    for x in plan["decisions"]: require(set(x["inference_refs"])<=ids["I"],"Broken decision ref")
    for x in plan["no_trade_decisions"]: require(set(x["reason_refs"])<=ids["I"],"Broken hold ref")
    changed=[x["ticker"] for x in plan["decisions"]]
    held=[x["ticker"] for x in plan["no_trade_decisions"]]
    require(len(set(changed))==len(changed) and len(set(held))==len(held) and not(set(changed)&set(held)),"Duplicate ticker decision")
    require(set(state["holdings"])<=set(changed)|set(held),"Unexplained previous holding")
    require(set(held)<=set(state["holdings"]),"No-trade ticker was not held")
    expected=[]
    for d in plan["decisions"]:
        c=d["ticker"]; require(c in universe,"Outside official150")
        require(d["target_weight"]<=(.25 if c=="2330" else .10),"Target cap")
        old=state["holdings"].get(c,0)
        new=target_shares(d["target_weight"],state["nav"],prices[c])
        require(new!=old,"Decision produces zero order")
        action="BUY" if not old else "SELL_ALL" if not new else "ADD" if new>old else "TRIM"
        require(d["action"]==action,"Action contradicts actual order")
        expected.append({"ticker":c,"side":"BUY" if new>old else "SELL","shares":abs(new-old),"decision_ref":d["decision_id"]})
    actual=[{k:o[k] for k in ("ticker","side","shares","decision_ref")} for o in plan["orders"]]
    require(actual==expected,"Orders differ from derive-orders.v2")
    p=project(state,actual,prices)
    require(20<=len(p["holdings"])<=30 and set(p["holdings"])<=set(universe),"Holding count or universe")
    require(0<=p["cash_pct"]<.25,"Cash rule")
    require(all(w<=(.25 if c=="2330" else .10) for c,w in p["weights"].items()),"Post-cost weight cap")
    intent=plan["market_view"]["posture"]["net_exposure_intent"]
    net=sum((1 if o["side"]=="BUY" else -1)*o["shares"]*prices[o["ticker"]] for o in actual)
    require((intent=="hold" and abs(net)<=.02*state["nav"]) or (intent=="increase" and net>0) or (intent=="reduce" and net<0),"Exposure intent mismatch")
    lo,hi=plan["market_view"]["posture"]["target_cash_pct_range"]
    require(lo<=p["cash_pct"]<=hi,"Cash outside declared range")
    require(project(state,actual,prices,1.10,.90)["cash"]>=0,"Execution stress cash")
    return p

def version_hash(root):
    h=hashlib.sha256()
    for p in sorted((root/"simple_agent").glob("*.py")):
        h.update(p.name.encode()); h.update(p.read_bytes())
    for p in [root/"run_simple.py",schema_path(root),root/"simple_agent"/"universe.json",root/"simple_agent"/"etfs.json"]:
        h.update(p.name.encode());h.update(p.read_bytes())
    return "sha256:"+h.hexdigest()[:40]
