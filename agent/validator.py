# ==============================================================================
# validator.py — D-Plan 驗證
# 提交前必跑。兩層驗證：① Schema 結構、② 語意引用鏈
# ==============================================================================

from __future__ import annotations
import json
from pathlib import Path

import jsonschema

from agent.config import SCHEMA_PATH


# ── ① Schema 結構驗證 ────────────────────────────────────────────────────────

def validate_schema(dplan: dict) -> list[str]:
    """
    對照 D-Plan.schema.json 做結構驗證。
    回傳錯誤訊息列表，空列表代表通過。
    """
    schema = json.loads(Path(SCHEMA_PATH).read_text(encoding="utf-8"))
    errors: list[str] = []
    validator = jsonschema.Draft202012Validator(schema)
    for err in validator.iter_errors(dplan):
        path = " → ".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"[Schema] {path}: {err.message}")
    return errors


# ── ② 語意引用鏈驗證（防止 TEAM_043 的錯誤）───────────────────────────────

def validate_reference_chain(dplan: dict) -> list[str]:
    """
    驗證每一層的引用 ID 確實存在於上一層。
    這是 schema 驗證抓不到的語意錯誤（例如引用不存在的 I9）。
    """
    errors: list[str] = []

    source_ids   = {s["source_id"] for s in dplan.get("sources", [])}
    obs_ids      = {o["obs_id"] for o in dplan.get("observations", [])}
    inf_ids      = {i["inf_id"] for i in dplan.get("inferences", [])}
    decision_ids = {d["decision_id"] for d in dplan.get("decisions", [])}

    # market_view.basis_refs → observations
    for ref in dplan.get("market_view", {}).get("basis_refs", []):
        if ref not in obs_ids:
            errors.append(f"[C1] market_view.basis_refs: '{ref}' 不存在於 observations")

    # observations.source_ref → sources
    for o in dplan.get("observations", []):
        for ref in o.get("source_ref", []):
            if ref not in source_ids:
                errors.append(f"[C1] {o['obs_id']}.source_ref: '{ref}' 不存在於 sources")

    # inferences.premise_refs → observations
    for i in dplan.get("inferences", []):
        for ref in i.get("premise_refs", []):
            if ref not in obs_ids:
                errors.append(f"[C1] {i['inf_id']}.premise_refs: '{ref}' 不存在於 observations")

    # decisions.inference_refs → inferences（孤兒決策 = 直接拒收）
    for d in dplan.get("decisions", []):
        for ref in d.get("inference_refs", []):
            if ref not in inf_ids:
                errors.append(
                    f"[C1][致命] {d['decision_id']}.inference_refs: '{ref}' 不存在於 inferences "
                    f"→ 這是孤兒決策，主辦方會直接拒收整份 D-Plan！"
                )

    # decisions.funding_for → 同日 BUY/ADD decisions
    buy_add_ids = {
        d["decision_id"]
        for d in dplan.get("decisions", [])
        if d.get("action") in ("BUY", "ADD")
    }
    for d in dplan.get("decisions", []):
        for ref in d.get("funding_for", []):
            if ref not in buy_add_ids:
                errors.append(
                    f"[C14] {d['decision_id']}.funding_for: '{ref}' 不存在或不是 BUY/ADD 決策"
                )

    # no_trade_decisions.reason_refs → inferences
    for ntd in dplan.get("no_trade_decisions", []):
        for ref in ntd.get("reason_refs", []):
            if ref not in inf_ids:
                errors.append(
                    f"[C1] no_trade_decisions({ntd['ticker']}).reason_refs: '{ref}' 不存在於 inferences"
                )

    # orders.decision_ref → decisions
    for o in dplan.get("orders", []):
        if o.get("decision_ref") not in decision_ids:
            errors.append(
                f"[C1] orders({o['ticker']}).decision_ref: '{o.get('decision_ref')}' 不存在於 decisions"
            )

    return errors


# ── ③ 業務規則驗證 ───────────────────────────────────────────────────────────

def validate_business_rules(dplan: dict) -> list[str]:
    """
    驗證競賽業務規則（不在 schema 裡的額外限制）。
    """
    errors: list[str] = []

    # 每檔昨日持股必須出現在 decisions 或 no_trade_decisions（擇一，不重複）
    decision_tickers    = {d["ticker"] for d in dplan.get("decisions", [])}
    no_trade_tickers    = {d["ticker"] for d in dplan.get("no_trade_decisions", [])}
    overlap = decision_tickers & no_trade_tickers
    if overlap:
        errors.append(f"[業務] 以下 ticker 同時出現在 decisions 和 no_trade_decisions: {overlap}")

    # orders 中每個 ticker 只能出現一次
    order_tickers = [o["ticker"] for o in dplan.get("orders", [])]
    dup = {t for t in order_tickers if order_tickers.count(t) > 1}
    if dup:
        errors.append(f"[業務] orders 中 ticker 重複出現: {dup}")

    # market_view 的 regime/stance 組合合理性（警告）
    mv = dplan.get("market_view", {})
    if mv.get("regime") == "risk_off" and mv.get("posture", {}).get("net_exposure_intent") == "increase":
        errors.append("[警告][C9] 宣告 risk_off 但 net_exposure_intent 為 increase（嘴上空手上多）")
    if mv.get("regime") == "risk_on" and mv.get("posture", {}).get("net_exposure_intent") == "reduce":
        errors.append("[警告][C9] 宣告 risk_on 但 net_exposure_intent 為 reduce（嘴上多手上空）")

    return errors


# ── 主驗證入口 ────────────────────────────────────────────────────────────────

def run_all_checks(dplan_path: str) -> bool:
    """
    對指定的 D-Plan JSON 檔跑全部驗證。
    回傳 True（全部通過）或 False（有錯誤）。
    """
    print(f"\n🔍 驗證中：{dplan_path}")
    dplan = json.loads(Path(dplan_path).read_text(encoding="utf-8"))

    all_errors: list[str] = []
    all_errors += validate_schema(dplan)
    all_errors += validate_reference_chain(dplan)
    all_errors += validate_business_rules(dplan)

    fatal   = [e for e in all_errors if "致命" in e or "[C1]" in e or "[Schema]" in e]
    warning = [e for e in all_errors if "警告" in e]

    if fatal:
        print(f"\n❌ 致命錯誤 {len(fatal)} 項（主辦方會拒收）：")
        for e in fatal:
            print(f"   {e}")
    if warning:
        print(f"\n⚠️  警告 {len(warning)} 項（可能被懲罰）：")
        for e in warning:
            print(f"   {e}")
    if not all_errors:
        print("✅ 全部驗證通過！可以提交。")
        return True

    return len(fatal) == 0  # 只有警告時仍可提交
