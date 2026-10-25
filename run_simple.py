"""Usage: python run_simple.py demo | doctor | plan | submit | auto."""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from simple_agent.core import TZ, require, construct, build_dplan, validate, schema_path, version_hash
from simple_agent.data import atomic_json, load_env, load_demo, fetch_live, choose_trade_date, Client
from simple_agent.service import adapter_config, active_checks, sync_state, review, send, window_check

ROOT=Path(__file__).resolve().parent

def main(argv=None):
    parser=argparse.ArgumentParser(description="ESUN simple autonomous momentum agent")
    parser.add_argument("command",choices=["demo","doctor","plan","submit","auto"])
    parser.add_argument("--trade-date",help="Normally discovered automatically from trading calendar")
    args=parser.parse_args(argv)
    load_env(ROOT/".env")
    if args.command=="doctor":
        from jsonschema import Draft202012Validator
        schema=json.loads(schema_path(ROOT).read_text(encoding="utf-8-sig"));Draft202012Validator.check_schema(schema)
        checks={"python_310_plus":sys.version_info>=(3,10),"schema_version":schema["properties"]["schema_version"]["const"],"team_id_set":bool(os.environ.get("TEAM_ID")),"google_key_set":bool(os.environ.get("GOOGLE_API_KEY")),"etf_data_key_set":bool(os.environ.get("FINMIND_API_TOKEN") or os.environ.get("FUGLE_API_KEY")),"official_adapter_present":(ROOT/"submit_adapter.json").exists()}
        print(json.dumps(checks,ensure_ascii=False,indent=2));return 0
    (ROOT/"runtime").mkdir(exist_ok=True)
    lock=ROOT/"runtime"/"running.lock"
    try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:raise ValueError("Another run is active; if a crash left running.lock, verify no process is active before removing it")
    try:
        os.write(fd,str(os.getpid()).encode());os.close(fd)
        started=datetime.now(TZ).isoformat(timespec="seconds")
        demo=args.command=="demo"
        # Return early after an accepted run: regenerated timestamps cannot cause resubmission.
        if not demo and args.command in ("submit","auto"):
            today=datetime.now(TZ).date().isoformat()
            r=ROOT/"runtime"/"receipts"/(args.trade_date or today)
            if r.with_suffix(".json").exists():
                print("Already accepted for date; no new submission.");return 0
        universe=json.loads((ROOT/"simple_agent"/"universe.json").read_text(encoding="utf-8"))
        etfcodes=json.loads((ROOT/"simple_agent"/"etfs.json").read_text())
        schema=json.loads(schema_path(ROOT).read_text(encoding="utf-8-sig"))
        if args.command=="auto":
            now=datetime.now(TZ)
            next_date=choose_trade_date(Client(ROOT/"runtime"/"calendar"),now)
            if next_date!=now.date().isoformat():
                print("Today is not a published trading day. No submission.");return 0
            window_check(next_date,now)
        bundle=load_demo(ROOT) if demo else fetch_live(ROOT,args.trade_date)
        if args.command=="auto":
            # Scheduler runs every calendar day; weekends/holidays are automatic no-ops.
            if bundle["trade_date"]!=datetime.now(TZ).date().isoformat():
                print("Today is not a published trading day. No submission.");return 0
        adapter=None
        if demo:
            state={"trade_date":bundle["as_of"],"nav":1_000_000_000,"cash":1_000_000_000,"holdings":{}}
            team="DEMO_ONLY"
        else:
            adapter=adapter_config(ROOT)
            require(adapter.get("schema_confirmed_version")==schema["properties"]["schema_version"]["const"],"Organizer schema version has not been confirmed (repo schema4.0 vs guide4.1)")
            team=os.environ.get("TEAM_ID","")
            require(re.fullmatch(r"[A-Za-z0-9_-]{1,32}",team) and "XXX" not in team,"Set actual official TEAM_ID once")
            state=sync_state(ROOT,bundle,universe,adapter)
        mempath=ROOT/"runtime"/"strategy_memory.json"
        memory=json.loads(mempath.read_text()) if not demo and mempath.exists() else {}
        result=construct(bundle,universe,state,memory)
        ai=None if demo else review(result,bundle)
        out=ROOT/"output"/("demo" if demo else bundle["trade_date"])
        out.mkdir(parents=True,exist_ok=True)
        if ai:
            atomic_json(out/"ai_review.json",ai)
            (out/"ETF_strategy_AI_draft.txt").write_text(ai["prospectus"],encoding="utf-8")
        plan=build_dplan(result,bundle,state,team,schema,started,version_hash(ROOT),ai)
        validate(plan,schema,state,result["prices"],universe)
        checks=active_checks(result,bundle,etfcodes)
        report={"mode":"DEMO_FROZEN_DATA_NO_SUBMISSION" if demo else "LIVE", "trade_date":bundle["trade_date"],"as_of":bundle["as_of"],"code_version":version_hash(ROOT),"schema_local_pass":True,"holdings":len(result["projected"]["holdings"]),"orders":len(plan["orders"]),"estimated_cash_pct":result["projected"]["cash_pct"],"active_share":checks,"data_errors":bundle.get("data_errors",{}),"ready_for_submission":not demo and checks["pass"],"notes":["Official verifier C1-C14 not supplied; local validation does not guarantee server acceptance.","Closing-price projections differ from actual daily-average execution and close valuations."]}
        path=out/f"D-Plan_{team}_{bundle['trade_date']}.json";atomic_json(path,plan)
        atomic_json(out/"validation.json",report)
        atomic_json(out/"state_before.json",state)
        atomic_json(out/"scores.json",result["scores"])
        print(json.dumps({k:v for k,v in report.items() if k not in ("active_share","data_errors","notes")},ensure_ascii=False,indent=2))
        print("D-Plan:",path)
        if args.command in ("submit","auto"):
            require(not demo,"Demo cannot submit")
            print(json.dumps(send(ROOT,plan,path,adapter,checks,result["memory"]),ensure_ascii=False))
        elif not checks["pass"]:
            print("Active Share not verified. This plan is NOT cleared for submission.")
        return 0
    finally:
        lock.unlink(missing_ok=True)

if __name__=="__main__":
    try:sys.exit(main())
    except Exception as e:
        print("STOP:",str(e),file=sys.stderr);sys.exit(2)
