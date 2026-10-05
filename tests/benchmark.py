"""Disposable benchmark. Never reads or changes a deployment database."""

import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))


def measure(call, repeats=20):
    timings = []
    value = None
    for _ in range(repeats):
        started = time.perf_counter()
        value = call()
        timings.append((time.perf_counter() - started) * 1000)
    return {
        "median_ms": round(statistics.median(timings), 3),
        "p95_ms": round(sorted(timings)[int(0.95 * (len(timings) - 1))], 3),
        "response_bytes": len(json.dumps(value).encode()),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="docs/benchmark-results.json")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        os.environ.update(
            CONFIG_DIR=folder,
            SUITES_DIR=folder + "/suites",
            RESULTS_DIR=folder + "/results",
            BSS_ENV="local",
        )
        import db, git_sync
        from routes.pages import cases_page, runs_page, groups_page
        from routes.runs import get_run

        db.init_db()
        with db.database() as c:
            pid = c.execute("INSERT INTO projects(name) VALUES ('Benchmark')").lastrowid
            c.executemany(
                "INSERT INTO test_cases(project_id,name,tc_code) VALUES (?,?,?)",
                [(pid, f"Test {i}", f"BENCH_{i:05}") for i in range(10000)],
            )
            c.executemany(
                "INSERT INTO test_runs(run_id,project_id,run_name,started_at,status,total) VALUES (?,?,?,'2026-10-05T10:00:00','passed',1200)",
                [(f"benchmark-{i:05}", pid, f"Run {i}") for i in range(2000)],
            )
            c.executemany(
                "INSERT INTO test_run_items(run_id,tc_code,tc_name,status) VALUES ('benchmark-00000',?,?,'passed')",
                [(f"B{i}", f"Test {i}") for i in range(1200)],
            )
        with db.database() as c:
            c.executemany(
                "INSERT INTO test_groups(project_id,name) VALUES (?,?)",
                [(pid, f"Suite {i:04}") for i in range(1000)],
            )
            c.execute(
                "INSERT INTO group_test_cases(group_id,test_case_id) SELECT 1,id FROM test_cases WHERE project_id=?",
                (pid,),
            )
            c.executemany(
                "INSERT INTO group_test_cases(group_id,test_case_id) VALUES (?,?)",
                [
                    (gid, ((gid * 7 + i) % 10000) + 1)
                    for gid in range(2, 1001)
                    for i in range(10)
                ],
            )
        user = {"username": "benchmark", "role": "admin"}
        results = {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "rows": {"cases": 10000, "runs": 2000, "items_in_summary_run": 1200},
            "repeats": 20,
        }
        results["cases_page_50"] = measure(
            lambda: cases_page(pid, offset=0, limit=50, user=user)
        )
        results["groups_page_50"] = measure(
            lambda: groups_page(pid, offset=0, limit=50, user=user)
        )
        results["rows"]["groups"] = 1000
        results["rows"]["group_members"] = 19990
        results["cases_search"] = measure(
            lambda: cases_page(pid, offset=0, limit=50, q="Test 99", user=user)
        )
        results["runs_page_50"] = measure(
            lambda: runs_page(pid, offset=0, limit=50, user=user)
        )
        results["summary_only"] = measure(
            lambda: get_run("benchmark-00000", False, user)
        )
        results["summary_with_items"] = measure(
            lambda: get_run("benchmark-00000", True, user)
        )
        source = Path(folder) / "parse"
        source.mkdir()
        for i in range(100):
            (source / f"suite{i}.robot").write_text(
                f"*** Test Cases ***\nTest {i}\n    Log    hello\n"
            )
        started = time.perf_counter()
        git_sync.parse_repo(source, False)
        results["parser_cold_ms"] = round((time.perf_counter() - started) * 1000, 3)
        results["parser_warm"] = measure(lambda: git_sync.parse_repo(source, False), 10)
        results["scope"] = (
            "In-process SQLite/serialization and parsing; excludes HTTP, Chrome, remote transfer and concurrent production load."
        )
    target = ROOT / args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
