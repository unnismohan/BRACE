"""
BRACE v2 — git-native test cases.

The repository is the source of truth for what tests exist; BRACE keeps the
execution history. Sync reads the .robot files already copied into the project's
suites directory (by the existing git-sync, or by upload) and reconciles the
test_cases table against them.

Three rules make this safe to run repeatedly:

  1. Nothing is ever deleted. A test that disappears from the repo is marked
     sync_status='missing' and kept, because its run history is real history.
     Removing it is a deliberate act in the UI.
  2. Hand-created test cases are never touched. They have no source_path, and
     every statement here is scoped to rows that have one.
  3. Identity is (source_path, source_test), or an explicit `braceid:CODE` tag.
     The tag is what survives a test being renamed — without it, a rename reads
     as one test removed and a different one added, which is also the honest
     interpretation of what a file diff shows.
"""
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# A test can pin its BRACE code from the repo:  [Tags]    braceid:VDRC_API_01
_BRACEID_RE = re.compile(r"^braceid[:=](?P<code>[A-Za-z0-9_.-]{1,60})$", re.IGNORECASE)

# Only files under a "Testcases" folder are treated as test cases — the same
# convention /api/projects/{id}/suites already uses to separate executable
# suites from resource and library files.
_TESTCASE_DIR = "testcases"


def _norm_rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _is_testcase_file(rel: str) -> bool:
    parts = rel.lower().split("/")
    return any(p == _TESTCASE_DIR for p in parts[:-1])


def parse_repo(root: Path, testcase_dirs_only: bool = True) -> tuple:
    """Read every .robot file under `root`. Returns (tests, errors).

    Each test is a dict: source_path, source_test, name, tags, doc, braceid.

    Files are parsed one at a time on purpose. Robot's builder aborts the whole
    tree on a parse error, and one broken file should not make a sync report
    that every test in the project has vanished.
    """
    from robot.api import TestSuiteBuilder

    tests, errors = [], []
    if not root.is_dir():
        return tests, [f"Suites directory does not exist: {root}"]

    for path in sorted(root.rglob("*.robot")):
        rel = _norm_rel(path, root)
        if testcase_dirs_only and not _is_testcase_file(rel):
            continue
        try:
            suite = TestSuiteBuilder(process_curdir=False).build(str(path))
        except Exception as exc:                      # noqa: BLE001 — reported, not raised
            errors.append(f"{rel}: {type(exc).__name__}: {str(exc)[:200]}")
            continue
        for test in _iter_tests(suite):
            raw_tags = [str(t) for t in (test.tags or [])]
            braceid = None
            plain   = []
            for t in raw_tags:
                m = _BRACEID_RE.match(t.strip())
                if m:
                    braceid = m.group("code")
                else:
                    plain.append(t)
            tests.append({
                "source_path": rel,
                "source_test": test.name,
                "name":        test.name,
                "tags":        plain,
                "doc":         (test.doc or "").strip() or None,
                "braceid":     braceid,
            })
    return tests, errors


def _iter_tests(suite):
    """Robot nests suites when a file contains sections; walk the whole tree."""
    for t in suite.tests:
        yield t
    for child in suite.suites:
        yield from _iter_tests(child)


def _extra_args_for(source_test: str) -> str:
    """Robot invocation args that pin execution to this one test.

    BRACE executes a whole .robot file per test case. A file holding five tests
    would otherwise produce five BRACE cases that each run all five — reporting
    five times the work and attributing every failure to all of them. Quoting
    matters here: test names contain spaces, which is why the executor splits
    extra_args with shlex rather than str.split.
    """
    escaped = source_test.replace('"', '\\"')
    return f'--test "{escaped}"'


def sync_project(conn, project_id: int, suites_dir: Path,
                 next_code_fn, norm_tags_fn, dry_run: bool = False) -> dict:
    """Reconcile test_cases against the .robot files on disk.

    `next_code_fn(conn)` and `norm_tags_fn(str)` are injected rather than
    imported so this module stays free of a circular import with main.
    """
    tests, errors = parse_repo(suites_dir)
    out = {"added": [], "updated": [], "missing": [], "unchanged": 0,
           "errors": errors, "duplicates": [], "parsed": len(tests),
           "dry_run": dry_run}

    # Duplicate braceid in the repo is a repo bug: two tests claiming one BRACE
    # code would fight over the same row on every sync. Report and skip both.
    seen_ids: dict = {}
    for t in tests:
        if t["braceid"]:
            seen_ids.setdefault(t["braceid"], []).append(
                f'{t["source_path"]}::{t["source_test"]}')
    dupes = {k: v for k, v in seen_ids.items() if len(v) > 1}
    if dupes:
        out["duplicates"] = [{"braceid": k, "tests": v} for k, v in dupes.items()]
        tests = [t for t in tests if t["braceid"] not in dupes]

    existing = conn.execute(
        "SELECT id, tc_code, name, description, suite_path, extra_args, tags,"
        "       source_path, source_test, sync_status"
        "  FROM test_cases WHERE project_id=?", (project_id,)).fetchall()

    by_source = {(r["source_path"], r["source_test"]): dict(r)
                 for r in existing if r["source_path"]}
    by_code   = {r["tc_code"]: dict(r) for r in existing if r["tc_code"]}
    matched_ids = set()

    for t in tests:
        row = None
        if t["braceid"] and t["braceid"] in by_code:
            row = by_code[t["braceid"]]
        if row is None:
            row = by_source.get((t["source_path"], t["source_test"]))
        # A hand-created case that happens to share the pinned code is adopted
        # into the sync rather than duplicated — the tester already named it.
        tags_norm  = norm_tags_fn(",".join(t["tags"])) if t["tags"] else norm_tags_fn("")
        extra_args = _extra_args_for(t["source_test"])

        if row is None:
            code = t["braceid"]
            # tc_code is UNIQUE across the whole database, not per project. Two
            # projects pointed at the same repository would otherwise collide on
            # the first pinned braceid and abort the sync — so a code already
            # taken elsewhere falls back to a generated one, and says so.
            if code:
                owner = conn.execute(
                    "SELECT project_id FROM test_cases WHERE tc_code=?", (code,)).fetchone()
                if owner and owner["project_id"] != project_id:
                    out["errors"].append(
                        f'{t["source_path"]}::{t["source_test"]}: braceid "{code}" is '
                        f'already used by project {owner["project_id"]}; '
                        f'a generated code was assigned instead')
                    code = None
            if not code:
                code = next_code_fn(conn) if not dry_run else "(new)"
            out["added"].append({"tc_code": code, "name": t["name"],
                                 "source_path": t["source_path"]})
            if not dry_run:
                try:
                    conn.execute(
                        "INSERT INTO test_cases (tc_code, project_id, name, description,"
                        " suite_path, extra_args, tags, source_path, source_test, sync_status)"
                        " VALUES (?,?,?,?,?,?,?,?,?, 'synced')",
                        (code, project_id, t["name"], t["doc"], t["source_path"],
                         extra_args, tags_norm, t["source_path"], t["source_test"]))
                    conn.commit()
                except Exception as exc:              # noqa: BLE001 — one row must not kill the sync
                    conn.rollback()
                    out["added"].pop()
                    out["errors"].append(
                        f'{t["source_path"]}::{t["source_test"]}: {type(exc).__name__}: '
                        f'{str(exc)[:120]}')
            continue

        matched_ids.add(row["id"])
        changes = {}
        if (row["name"] or "") != t["name"]:
            changes["name"] = t["name"]
        if (row["description"] or None) != t["doc"]:
            changes["description"] = t["doc"]
        if (row["suite_path"] or "") != t["source_path"]:
            changes["suite_path"] = t["source_path"]
        if (row["extra_args"] or "") != extra_args:
            changes["extra_args"] = extra_args
        if (row["tags"] or "") != tags_norm:
            changes["tags"] = tags_norm
        # An adopted manual case gains its source columns on first match.
        if (row["source_path"] or "") != t["source_path"]:
            changes["source_path"] = t["source_path"]
        if (row["source_test"] or "") != t["source_test"]:
            changes["source_test"] = t["source_test"]
        if row["sync_status"] != "synced":
            changes["sync_status"] = "synced"

        if not changes:
            out["unchanged"] += 1
            continue
        out["updated"].append({"tc_code": row["tc_code"], "name": t["name"],
                               "fields": sorted(changes)})
        if not dry_run:
            sets   = ", ".join(f"{k}=?" for k in changes)
            params = list(changes.values()) + [row["id"]]
            conn.execute(f"UPDATE test_cases SET {sets} WHERE id=?", params)
            conn.commit()

    # Anything that was synced before and is not in the repo now.
    for r in existing:
        if not r["source_path"] or r["id"] in matched_ids:
            continue
        out["missing"].append({"tc_code": r["tc_code"], "name": r["name"],
                               "source_path": r["source_path"]})
        if not dry_run and r["sync_status"] != "missing":
            conn.execute("UPDATE test_cases SET sync_status='missing' WHERE id=?",
                         (r["id"],))
            conn.commit()

    log.info("Git sync for project %s: +%d ~%d missing=%d unchanged=%d errors=%d",
             project_id, len(out["added"]), len(out["updated"]),
             len(out["missing"]), out["unchanged"], len(errors))
    return out


def summary(result: dict) -> dict:
    """Counts only — what gets stored on the project row and shown in a toast."""
    return {"added": len(result.get("added", [])),
            "updated": len(result.get("updated", [])),
            "missing": len(result.get("missing", [])),
            "unchanged": result.get("unchanged", 0),
            "errors": len(result.get("errors", [])),
            "duplicates": len(result.get("duplicates", [])),
            "parsed": result.get("parsed", 0)}
