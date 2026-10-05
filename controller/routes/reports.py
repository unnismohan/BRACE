"""BRACE routes.reports — extracted application responsibility."""

from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, Query
from db import get_db, rows_to_list
import authentication as mod_authentication
from fastapi import APIRouter

router = APIRouter()


@router.get("/api/projects/{project_id}/coverage")
def coverage_report(
    project_id: int, stale_days: int = 14, user=Depends(mod_authentication._proj_viewer)
):
    """Automation coverage — an inventory view, not an execution view.

    The Reports dashboard answers "how did the runs in this window go?". This
    answers a different question: "of everything we have, how much is actually
    being tested?" — which is what tells you whether a 100% pass rate is
    meaningful or just the same three cases passing over and over.

    Deliberately NOT date-filtered: coverage is about the current state of the
    inventory. `stale_days` only decides how old a last run must be to count as
    stale.
    """
    import runtime as mod_runtime

    conn = get_db()
    tcs = rows_to_list(
        conn.execute(
            "SELECT id, tc_code, name, suite_path, tags, last_run_status, last_run_at FROM test_cases WHERE project_id=? ORDER BY tc_code",
            (project_id,),
        ).fetchall()
    )
    memb: dict = {}
    for r in conn.execute(
        "\n            SELECT gtc.test_case_id AS tid, tg.name AS gname\n            FROM group_test_cases gtc JOIN test_groups tg ON tg.id = gtc.group_id\n            WHERE tg.project_id=?",
        (project_id,),
    ).fetchall():
        memb.setdefault(r["tid"], []).append(r["gname"])
    conn.close()
    suites_dir = mod_runtime._project_suites(project_id)
    scripts = set()
    if suites_dir.exists():
        for p in suites_dir.rglob("*.robot"):
            rel = str(p.relative_to(suites_dir)).replace("\\", "/")
            if any((part == "testcases" for part in rel.lower().split("/")[:-1])):
                scripts.add(rel)
    linked = {
        (t["suite_path"] or "").strip() for t in tcs if (t["suite_path"] or "").strip()
    }
    orphan_scripts = sorted(scripts - linked)
    missing_scripts = sorted((p for p in linked if p not in scripts))
    cutoff = (datetime.now() - timedelta(days=max(1, stale_days))).isoformat(
        timespec="seconds"
    )

    def bucket(t: dict) -> str:
        if not t["last_run_status"]:
            return "never"
        if t["last_run_status"] == "passed":
            return "passed"
        return "failed"

    total = len(tcs)
    counts = {"passed": 0, "failed": 0, "never": 0}
    stale, never_list, failing_list, stale_list = (0, [], [], [])
    for t in tcs:
        b = bucket(t)
        counts[b] += 1
        brief = {
            "tc_code": t["tc_code"],
            "name": t["name"],
            "suite_path": t["suite_path"],
            "last_run_at": t["last_run_at"],
            "suites": memb.get(t["id"], []),
        }
        if b == "never":
            never_list.append(brief)
        else:
            if b == "failed":
                failing_list.append(brief)
            if (t["last_run_at"] or "") < cutoff:
                stale += 1
                stale_list.append(brief)
    executed = total - counts["never"]
    pct = lambda n, d: round(n / d * 100, 1) if d else 0.0
    by_suite: dict = {}
    for t in tcs:
        for g in memb.get(t["id"]) or ["(no suite)"]:
            s = by_suite.setdefault(
                g, {"suite": g, "total": 0, "passed": 0, "failed": 0, "never": 0}
            )
            s["total"] += 1
            s[bucket(t)] += 1
    for s in by_suite.values():
        s["executed"] = s["total"] - s["never"]
        s["coverage_pct"] = pct(s["executed"], s["total"])
        s["pass_pct"] = pct(s["passed"], s["executed"])
    return {
        "stale_days": stale_days,
        "scripts": {
            "on_disk": len(scripts),
            "onboarded": len(scripts & linked),
            "orphan": len(orphan_scripts),
            "onboarded_pct": pct(len(scripts & linked), len(scripts)),
            "orphan_list": orphan_scripts[:100],
            "missing_list": missing_scripts[:100],
        },
        "test_cases": {
            "total": total,
            "executed": executed,
            "never": counts["never"],
            "passed": counts["passed"],
            "failed": counts["failed"],
            "stale": stale,
            "coverage_pct": pct(executed, total),
            "passed_pct": pct(counts["passed"], total),
            "failed_pct": pct(counts["failed"], total),
            "never_pct": pct(counts["never"], total),
            "health_pct": pct(counts["passed"], executed),
        },
        "by_suite": sorted(
            by_suite.values(), key=lambda s: (s["coverage_pct"], -s["total"])
        ),
        "never_run": never_list[:200],
        "failing": failing_list[:200],
        "stale_list": stale_list[:200],
    }


@router.get("/api/projects/{project_id}/report-stats")
def report_stats(
    project_id: int,
    limit: int = Query(30, ge=1, le=200),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user=Depends(mod_authentication._proj_viewer),
):
    """Aggregated analytics for the Reports dashboard.

    date_from/date_to are inclusive calendar dates (YYYY-MM-DD). Default: today only.
    """
    if not date_from and (not date_to):
        today = datetime.now().strftime("%Y-%m-%d")
        date_from = date_to = today
    date_from = date_from or "0001-01-01"
    date_to = date_to or "9999-12-31"
    range_start = date_from[:10]
    range_end = date_to[:10]
    conn = get_db()
    runs = rows_to_list(
        conn.execute(
            "\n        SELECT tr.run_id, tr.run_name, tr.status, tr.total, tr.passed, tr.failed,\n               tr.started_at, tr.finished_at, tr.triggered_by, tg.name AS group_name\n        FROM test_runs tr LEFT JOIN test_groups tg ON tr.group_id = tg.id\n        WHERE tr.project_id=? AND substr(tr.started_at,1,10) BETWEEN ? AND ?\n        ORDER BY tr.started_at DESC, tr.id DESC LIMIT ?\n    ",
            (project_id, range_start, range_end, limit),
        ).fetchall()
    )
    tc_rows = rows_to_list(
        conn.execute(
            "\n        SELECT tri.tc_code, tri.tc_name,\n               COUNT(*)                                            AS runs,\n               SUM(CASE WHEN tri.status='passed' THEN 1 ELSE 0 END) AS passed,\n               SUM(CASE WHEN tri.status='failed' THEN 1 ELSE 0 END) AS failed,\n               SUM(tri.passed_after_retry) AS passed_after_retry,\n               MAX(tri.finished_at)                                AS last_run\n        FROM test_run_items tri\n        JOIN test_runs tr ON tri.run_id = tr.run_id\n        WHERE tr.project_id=? AND substr(tr.started_at,1,10) BETWEEN ? AND ?\n        GROUP BY tri.tc_code, tri.tc_name\n        ORDER BY failed DESC, runs DESC\n    ",
            (project_id, range_start, range_end),
        ).fetchall()
    )
    agg = conn.execute(
        "\n        SELECT COUNT(*) AS n_runs,\n               COALESCE(SUM(total),0)  AS tot,\n               COALESCE(SUM(passed),0) AS pass,\n               COALESCE(SUM(failed),0) AS fail\n        FROM test_runs WHERE project_id=? AND substr(started_at,1,10) BETWEEN ? AND ?\n    ",
        (project_id, range_start, range_end),
    ).fetchone()
    conn.close()

    def _dur(a: Optional[str], b: Optional[str]) -> Optional[float]:
        if not a or not b:
            return None
        try:
            return (
                datetime.fromisoformat(b) - datetime.fromisoformat(a)
            ).total_seconds()
        except ValueError:
            return None

    durations = []
    for r in runs:
        d = _dur(r.get("started_at"), r.get("finished_at"))
        r["duration_sec"] = d
        if d is not None:
            durations.append(d)
    flaky = [
        t
        for t in tc_rows
        if (t["passed"] > 0 and t["failed"] > 0) or t["passed_after_retry"] > 0
    ]
    for t in tc_rows:
        t["pass_rate"] = round(t["passed"] / t["runs"] * 100) if t["runs"] else 0
    total_tc = agg["tot"] or 0
    return {
        "date_from": date_from,
        "date_to": date_to,
        "summary": {
            "total_runs": agg["n_runs"] or 0,
            "total_tests": total_tc,
            "total_passed": agg["pass"] or 0,
            "total_failed": agg["fail"] or 0,
            "pass_rate": (
                round((agg["pass"] or 0) / total_tc * 100, 1) if total_tc else 0.0
            ),
            "avg_duration": (
                round(sum(durations) / len(durations), 1) if durations else None
            ),
            "flaky_count": len(flaky),
        },
        "trend": list(reversed(runs)),
        "top_failing": [t for t in tc_rows if t["failed"] > 0][:10],
        "flaky": flaky[:10],
        "tc_stats": tc_rows,
    }


@router.get("/api/projects/{project_id}/tester-activity")
def tester_activity(
    project_id: int,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user=Depends(mod_authentication._current_user),
):
    """Per-tester execution activity for one project. project_admin only.

    Measures what the system actually records: runs triggered, tests executed,
    outcomes, machine execution time and active days. It does NOT measure hours
    worked — nothing in BRACE observes a person's working time.
    """
    import authentication as mod_authentication

    mod_authentication._require_proj_admin(project_id, user)
    if not date_from and (not date_to):
        date_to = datetime.now().strftime("%Y-%m-%d")
        date_from = (datetime.now() - timedelta(days=29)).strftime("%Y-%m-%d")
    date_from = date_from or "0001-01-01"
    date_to = date_to or "9999-12-31"
    lo, hi = (f"{date_from} 00:00:00", f"{date_to} 23:59:59")
    conn = get_db()
    runs = rows_to_list(
        conn.execute(
            "SELECT run_id, triggered_by, status, total, passed, failed, started_at, finished_at\n           FROM test_runs\n           WHERE project_id=? AND substr(replace(started_at,'T',' '),1,19) BETWEEN ? AND ?\n           ORDER BY started_at, id",
            (project_id, lo, hi),
        ).fetchall()
    )
    cov = rows_to_list(
        conn.execute(
            "SELECT tr.triggered_by AS who, COUNT(DISTINCT tri.tc_code) AS n\n           FROM test_run_items tri JOIN test_runs tr ON tri.run_id = tr.run_id\n           WHERE tr.project_id=? AND substr(replace(tr.started_at,'T',' '),1,19) BETWEEN ? AND ?\n           GROUP BY tr.triggered_by",
            (project_id, lo, hi),
        ).fetchall()
    )
    conn.close()
    coverage = {c["who"]: c["n"] for c in cov}

    def _secs(a, b):
        if not a or not b:
            return None
        try:
            return max(
                0.0,
                (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds(),
            )
        except ValueError:
            return None

    people: dict = {}
    daily: dict = {}
    for r in runs:
        who = r["triggered_by"] or "(unknown)"
        day = (r["started_at"] or "")[:10]
        p = people.setdefault(
            who,
            {
                "user": who,
                "runs": 0,
                "tests": 0,
                "passed": 0,
                "failed": 0,
                "exec_seconds": 0.0,
                "days": set(),
                "first_seen": None,
                "last_seen": None,
                "cancelled": 0,
            },
        )
        p["runs"] += 1
        p["tests"] += r["total"] or 0
        p["passed"] += r["passed"] or 0
        p["failed"] += r["failed"] or 0
        if r["status"] == "cancelled":
            p["cancelled"] += 1
        d = _secs(r["started_at"], r["finished_at"])
        if d is not None:
            p["exec_seconds"] += d
        if day:
            p["days"].add(day)
            p["first_seen"] = min(p["first_seen"] or day, day)
            p["last_seen"] = max(p["last_seen"] or day, day)
            slot = daily.setdefault(day, {})
            slot[who] = slot.get(who, 0) + 1
    testers = []
    for p in people.values():
        active = len(p["days"]) or 1
        tests = p["tests"]
        testers.append(
            {
                "user": p["user"],
                "runs": p["runs"],
                "tests": tests,
                "passed": p["passed"],
                "failed": p["failed"],
                "cancelled": p["cancelled"],
                "pass_rate": round(p["passed"] / tests * 100, 1) if tests else 0.0,
                "exec_seconds": round(p["exec_seconds"], 1),
                "active_days": len(p["days"]),
                "runs_per_day": round(p["runs"] / active, 1),
                "tests_per_day": round(tests / active, 1),
                "unique_tcs": coverage.get(p["user"], 0),
                "first_seen": p["first_seen"],
                "last_seen": p["last_seen"],
            }
        )
    testers.sort(key=lambda t: t["tests"], reverse=True)
    return {
        "date_from": date_from,
        "date_to": date_to,
        "testers": testers,
        "daily": [{"date": d, "by_user": daily[d]} for d in sorted(daily)],
        "totals": {
            "testers": len(testers),
            "runs": sum((t["runs"] for t in testers)),
            "tests": sum((t["tests"] for t in testers)),
            "exec_seconds": round(sum((t["exec_seconds"] for t in testers)), 1),
        },
    }
