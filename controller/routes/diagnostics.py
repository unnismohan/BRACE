"""BRACE routes.diagnostics — extracted application responsibility."""

from datetime import datetime
from typing import Optional
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from db import get_db
import locator_repair
import authentication as mod_authentication
from fastapi import APIRouter

router = APIRouter()


def _prop_json(row) -> dict:
    import json as _json

    d = dict(row)
    for k in ("candidates", "proposed"):
        try:
            d[k] = _json.loads(d[k]) if d[k] else None
        except Exception:
            d[k] = None
    return d


@router.get("/api/projects/{project_id}/locator-proposals")
def list_locator_proposals(
    project_id: int,
    status: Optional[str] = None,
    user=Depends(mod_authentication._proj_viewer),
):
    """Broken locators for this project, most-recently-seen first.

    One row per distinct locator, not per failed test — twelve cases broken by
    one renamed button are one entry with occurrences=12.
    """
    sql = "SELECT * FROM locator_proposals WHERE project_id=?"
    params = [project_id]
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY last_seen DESC, id DESC LIMIT 200"
    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_prop_json(r) for r in rows]


@router.post("/api/projects/{project_id}/locator-proposals/{prop_id}/recompute")
def recompute_locator_proposal(
    project_id: int, prop_id: int, user=Depends(mod_authentication._proj_tester)
):
    """Re-run the shortlist against the stored capture.

    Worth having because the scoring changes as BRACE improves, and because a
    proposal computed before somebody fixed the page is stale.
    """
    import runtime as mod_runtime
    import json as _json

    conn = get_db()
    row = conn.execute(
        "SELECT * FROM locator_proposals WHERE id=? AND project_id=?",
        (prop_id, project_id),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Proposal not found")
    item = conn.execute(
        "SELECT run_id, rf_run_id, dom_capture FROM test_run_items WHERE id=?",
        (row["last_item_id"],),
    ).fetchone()
    if not item or not item["dom_capture"]:
        conn.close()
        raise HTTPException(
            409, "The captured page for this locator is no longer on disk"
        )
    path = (
        mod_runtime._project_results(project_id) / item["run_id"] / item["dom_capture"]
    )
    if not path.is_file():
        conn.close()
        raise HTTPException(409, "The captured page has been purged")
    shortlist = locator_repair.candidates(
        path.read_text(encoding="utf-8", errors="replace"), row["failed_locator"]
    )
    conn.execute(
        "UPDATE locator_proposals SET candidates=?, proposed=? WHERE id=?",
        (
            _json.dumps(shortlist["candidates"]),
            _json.dumps(shortlist["proposed"]) if shortlist["proposed"] else None,
            prop_id,
        ),
    )
    conn.commit()
    out = conn.execute(
        "SELECT * FROM locator_proposals WHERE id=?", (prop_id,)
    ).fetchone()
    conn.close()
    mod_runtime.audit(
        user, "locator.recompute", project_id=project_id, target=row["failed_locator"]
    )
    return _prop_json(out)


class LocatorDecision(BaseModel):
    status: str


@router.post("/api/projects/{project_id}/locator-proposals/{prop_id}/decide")
def decide_locator_proposal(
    project_id: int,
    prop_id: int,
    req: LocatorDecision,
    user=Depends(mod_authentication._proj_tester),
):
    """Reject a proposal, or reopen one.

    'applied' is deliberately not settable here. Applying a change to a suite
    belongs to the verify-and-branch flow, not to a status field — a test that
    was marked fixed without anything being edited is worse than a broken one.
    """
    import runtime as mod_runtime

    if req.status not in ("rejected", "new"):
        raise HTTPException(400, "status must be 'rejected' or 'new'")
    conn = get_db()
    row = conn.execute(
        "SELECT failed_locator FROM locator_proposals WHERE id=? AND project_id=?",
        (prop_id, project_id),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Proposal not found")
    conn.execute(
        "UPDATE locator_proposals SET status=?, decided_by=?, decided_at=? WHERE id=?",
        (
            req.status,
            user["username"],
            datetime.now().isoformat() if req.status != "new" else None,
            prop_id,
        ),
    )
    conn.commit()
    conn.close()
    mod_runtime.audit(
        user,
        "locator.decide",
        project_id=project_id,
        target=row["failed_locator"],
        status=req.status,
    )
    return {"ok": True}
