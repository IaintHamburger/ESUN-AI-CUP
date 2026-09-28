# ==============================================================================
# submitter.py — D-Plan 提交
# 提交到比賽系統的 HTTP 客戶端（API 端點待主辦方公告後填入）
# ==============================================================================

from __future__ import annotations
import json
from pathlib import Path

import requests

from agent.config import TEAM_ID, ESUN_API_TOKEN

# ⚠️ 待主辦方公告後填入
SUBMIT_URL = "https://esun-ai-challenge.tw/api/dplan/submit"   # 範例，請更換

# API Token 從 .env 讀取（由 config.py 載入）
_TOKEN = ESUN_API_TOKEN


def submit(dplan_path: str, dry_run: bool = False) -> bool:
    """
    提交 D-Plan JSON 到比賽系統。
    dry_run=True 時只做模擬，不真的發送（用於測試）。
    回傳 True 表示提交成功。
    """
    path = Path(dplan_path)
    if not path.exists():
        print(f"❌ 找不到 D-Plan 檔案：{dplan_path}")
        return False

    dplan = json.loads(path.read_text(encoding="utf-8"))

    # 基本前置確認
    assert dplan.get("team_id") == TEAM_ID, \
        f"team_id 不符：檔案裡是 {dplan.get('team_id')}，config 是 {TEAM_ID}"

    if dry_run:
        print(f"[DRY RUN] 模擬提交 {dplan_path}（不真正發送）")
        return True

    if not _TOKEN or _TOKEN == "":
        print("❌ 尚未設定 ESUN_API_TOKEN，請在 .env 填入：ESUN_API_TOKEN=your-token")
        return False

    try:
        resp = requests.post(
            SUBMIT_URL,
            json=dplan,
            headers={
                "Authorization": f"Bearer {_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        print(f"✅ 提交成功！狀態碼：{resp.status_code}")
        print(f"   回應：{resp.text[:300]}")
        return True

    except requests.HTTPError as e:
        print(f"❌ 提交失敗（HTTP {e.response.status_code}）：{e.response.text[:300]}")
        return False
    except Exception as e:
        print(f"❌ 提交時發生錯誤：{e}")
        return False


def check_holdings_api() -> dict:
    """
    查詢比賽系統的庫存明細（提交前確認持股用）。
    API: GET /api/portfolio/holdings/
    """
    if not _TOKEN:
        print("❌ 尚未設定 API_TOKEN")
        return {}

    try:
        resp = requests.get(
            "https://esun-ai-challenge.tw/api/portfolio/holdings/",
            headers={"Authorization": f"Bearer {_TOKEN}"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"❌ 查詢持股 API 失敗：{e}")
        return {}
