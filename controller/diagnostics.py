"""BRACE diagnostics — extracted application responsibility."""

from datetime import datetime
from pathlib import Path
from typing import Optional
from db import get_db
import locator_repair


def _dom_capture_enabled(project_id: int) -> bool:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT dom_capture_enabled FROM projects WHERE id=?", (project_id,)
        ).fetchone()
    except Exception:
        return False
    finally:
        conn.close()
    return bool(row and row["dom_capture_enabled"])


def _read_captures(item_dir: Path) -> list:
    """The listener's sidecar metadata for this item, oldest first."""
    d = item_dir / "dom"
    if not d.is_dir():
        return []
    import json as _json

    out = []
    for meta in sorted(d.glob("*.json")):
        try:
            out.append(_json.loads(meta.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def _locator_signature(locator: str, suite_path: Optional[str]) -> str:
    """Collapse the same broken locator across every test that hits it.

    Interim definition: the locator plus the suite it appears in. When the
    failure-signature layer lands this should key off that instead, so one
    renamed element is one row even across suites.
    """
    import hashlib

    raw = f"{(locator or '').strip()}||{(suite_path or '').strip()}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _record_capture(
    project_id: int,
    run_id: str,
    item_id: int,
    tc: dict,
    item_dir: Path,
    fail_detail: Optional[str] = None,
) -> tuple:
    """Record the capture against the item and upsert its repair proposal.

    Blocking — call through to_thread. Returns (relative html path, locator,
    signature) for the caller's own UPDATE, so the item is written once rather
    than twice.
    """
    import runtime as mod_runtime
    import json as _json

    caps = _read_captures(item_dir)
    if not caps:
        return (None, None, None)
    first = caps[0]
    rel = f"{item_dir.name}/dom/{first.get('html_file')}"
    locator = (
        first.get("locator")
        or locator_repair.locator_from_message(first.get("message"))
        or locator_repair.locator_from_message(fail_detail)
    )
    if not locator:
        return (rel, None, None)
    suite_path = tc.get("suite_path")
    sig = _locator_signature(locator, suite_path)
    shortlist = None
    try:
        html = (item_dir / "dom" / str(first.get("html_file"))).read_text(
            encoding="utf-8", errors="replace"
        )
        shortlist = locator_repair.candidates(html, locator)
    except Exception as exc:
        mod_runtime.log.debug("Locator shortlist failed for %s: %s", run_id, exc)
    cand = _json.dumps(shortlist["candidates"]) if shortlist else None
    prop = (
        _json.dumps(shortlist["proposed"])
        if shortlist and shortlist["proposed"]
        else None
    )
    now = datetime.now().isoformat()
    mod_runtime._metrics["dom_captures"] += 1
    if prop:
        mod_runtime._metrics["dom_proposals"] += 1
    conn = get_db()
    try:
        conn.execute(
            "\n            INSERT INTO locator_proposals\n                (project_id, signature, failed_locator, suite_path, tc_code,\n                 candidates, proposed, first_seen, last_seen, last_run_id, last_item_id)\n            VALUES (?,?,?,?,?,?,?,?,?,?,?)\n            ON CONFLICT(project_id, signature) DO UPDATE SET\n                occurrences  = occurrences + 1,\n                last_seen    = excluded.last_seen,\n                last_run_id  = excluded.last_run_id,\n                last_item_id = excluded.last_item_id,\n                candidates   = CASE WHEN status='new' THEN excluded.candidates ELSE candidates END,\n                proposed     = CASE WHEN status='new' THEN excluded.proposed   ELSE proposed   END\n        ",
            (
                project_id,
                sig,
                locator,
                suite_path,
                tc.get("tc_code"),
                cand,
                prop,
                now,
                now,
                run_id,
                item_id,
            ),
        )
        conn.commit()
    except Exception as exc:
        mod_runtime.log.debug(
            "Could not record locator proposal for %s: %s", run_id, exc
        )
    finally:
        conn.close()
    return (rel, locator, sig)
