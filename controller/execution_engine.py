"""BRACE execution_engine — extracted application responsibility."""

import asyncio
import json
from db import database
from profiles import snapshot_profile, runner_environment
from runner_transport import RemoteRobotProcess
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from db import get_db
from execution import map_bounded, terminate_tree
from provenance import snapshot_sources


def _start_run(
    project_id: int,
    tcs: list,
    run_name: str,
    username: str,
    extra_args: Optional[str],
    group_id: Optional[int] = None,
    rerun_of: Optional[str] = None,
    parallel: Optional[int] = None,
    profile_id: Optional[int] = None,
    retry_count: int = 0,
) -> dict:
    """Persist a run + its items and hand it to the executor.

    Shared by the normal trigger, re-run-failed and the scheduler so the three
    paths cannot drift apart.
    """
    import runtime as mod_runtime

    public_profile, profile_secrets = snapshot_profile(project_id, profile_id)
    conn = get_db()
    run_id = f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    conn.execute(
        "INSERT INTO test_runs (run_id, project_id, group_id, run_name, triggered_by, total, started_at, status, rerun_of, git_commit) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            project_id,
            group_id,
            run_name,
            username,
            len(tcs),
            datetime.now().isoformat(timespec="seconds"),
            "queued",
            rerun_of,
            (
                conn.execute(
                    "SELECT last_git_commit FROM projects WHERE id=?", (project_id,)
                ).fetchone()
                or {"last_git_commit": None}
            )["last_git_commit"],
        ),
    )
    conn.execute(
        "UPDATE test_runs SET profile_snapshot=?,profile_secrets=?,retry_limit=? WHERE run_id=?",
        (json.dumps(public_profile), profile_secrets, retry_count, run_id),
    )
    items = []
    for tc in tcs:
        conn.execute(
            "INSERT INTO test_run_items (run_id, test_case_id, tc_code, tc_name, status, source_path) VALUES (?,?,?,?,?,?)",
            (
                run_id,
                tc["id"],
                tc["tc_code"],
                tc["name"],
                "pending",
                tc.get("suite_path"),
            ),
        )
        item_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        items.append({"item_id": item_id, "tc": tc})
    conn.commit()
    conn.close()
    mod_runtime._active_runs[run_id] = {
        "status": "queued",
        "total": len(tcs),
        "passed": 0,
        "failed": 0,
        "project_id": project_id,
        "group_id": group_id,
    }
    mod_runtime._metrics["runs_started"] += 1
    mod_runtime.log.info(
        "Run queued",
        extra={
            "run_id": run_id,
            "project_id": project_id,
            "user": username,
            "total": len(tcs),
            "rerun_of": rerun_of,
        },
    )
    width = max(
        1,
        min(
            mod_runtime.MAX_CONCURRENT_TESTS,
            parallel or mod_runtime.RUN_PARALLEL_DEFAULT,
            len(tcs),
        ),
    )
    asyncio.create_task(_execute_run(run_id, project_id, items, extra_args, width))
    queued_ahead = max(
        0,
        sum((1 for r in mod_runtime._active_runs.values() if r["status"] == "queued"))
        - 1,
    )
    running_now = sum(
        (1 for r in mod_runtime._active_runs.values() if r["status"] == "running")
    )
    starts_now = queued_ahead == 0 and running_now < mod_runtime.MAX_CONCURRENT_RUNS
    return {
        "run_id": run_id,
        "run_name": run_name,
        "total": len(tcs),
        "status": "queued",
        "queued_ahead": queued_ahead,
        "rerun_of": rerun_of,
        "parallel": width,
        "starts_immediately": starts_now,
        "slots_busy": max(0, running_now),
        "slots_total": mod_runtime.MAX_CONCURRENT_RUNS,
    }


async def _execute_run(
    run_id: str,
    project_id: int,
    items: list,
    extra_args: Optional[str],
    parallel: Optional[int] = None,
):
    """Wait for a free execution slot, then run the suite."""
    import runtime as mod_runtime

    async with mod_runtime.fair_gate().slot(project_id, run_id):
        if run_id in mod_runtime._cancelled_runs:
            mod_runtime._cancelled_runs.discard(run_id)
            mod_runtime._active_runs.pop(run_id, None)
            mod_runtime.log.info(
                "Run %s was cancelled while queued — not starting.", run_id
            )
            return
        conn = get_db()
        conn.execute(
            "UPDATE test_runs SET status='running', started_at=? WHERE run_id=?",
            (datetime.now().isoformat(timespec="seconds"), run_id),
        )
        conn.commit()
        conn.close()
        if run_id in mod_runtime._active_runs:
            mod_runtime._active_runs[run_id]["status"] = "running"
        mod_runtime._publish(run_id, "summary", {"status": "running"})
        try:
            await _run_suite(run_id, project_id, items, extra_args, parallel)
        except Exception as exc:
            mod_runtime.log.exception(
                "Run preparation or execution failed", extra={"run_id": run_id}
            )
            if run_id not in mod_runtime._cancelled_runs:
                now = datetime.now().isoformat()
                await asyncio.to_thread(
                    mod_runtime._db_write,
                    [
                        (
                            "UPDATE test_runs SET status='failed', finished_at=? WHERE run_id=? AND status!='cancelled'",
                            (now, run_id),
                        ),
                        (
                            "UPDATE test_run_items SET status='failed', finished_at=?, fail_summary=? WHERE run_id=? AND status IN ('pending','running')",
                            (now, f"Run preparation failed: {exc}", run_id),
                        ),
                    ],
                )
                await asyncio.to_thread(
                    mod_runtime._db_write,
                    [
                        (
                            "UPDATE test_runs SET passed=(SELECT COUNT(*) FROM test_run_items WHERE run_id=? AND status='passed'),failed=(SELECT COUNT(*) FROM test_run_items WHERE run_id=? AND status='failed') WHERE run_id=?",
                            (run_id, run_id, run_id),
                        )
                    ],
                )
                if run_id in mod_runtime._active_runs:
                    mod_runtime._active_runs[run_id]["status"] = "failed"
                mod_runtime._publish(run_id, "done", {"status": "failed"})
        finally:
            mod_runtime._cancelled_runs.discard(run_id)
            mod_runtime._active_runs.pop(run_id, None)


async def _run_one_item(
    run_id: str,
    project_id: int,
    item: dict,
    extra_args: Optional[str],
    run_dir: Path,
    suites_dir: Path,
    tally: dict,
    dom_capture: bool = False,
    profile=None,
    profile_secrets="",
    retry_limit=0,
) -> Optional[str]:
    """Execute one test case. Returns its output.xml path, or None.

    Split out of _run_suite so several can be in flight at once. Every robot
    process — whichever run it belongs to — must hold a slot from the global
    _tslots() budget, because that budget counts browsers, and browsers are what
    exhausts the pod.
    """
    import diagnostics as mod_diagnostics
    import reporting as mod_reporting
    import runtime as mod_runtime

    tc = item["tc"]
    item_id = item["item_id"]
    rf_run_id = f"{run_id}-tc-{tc['tc_code'] or tc['id']}"
    item_dir = run_dir / rf_run_id
    async with mod_runtime._tslots():
        if (
            run_id in mod_runtime._cancelled_runs
            or run_id not in mod_runtime._active_runs
        ):
            return None
        item_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            mod_runtime._db_write,
            [
                (
                    "UPDATE test_run_items SET rf_run_id=?, status='running', started_at=? WHERE id=?",
                    (rf_run_id, datetime.now().isoformat(), item_id),
                )
            ],
        )
        mod_runtime._publish(
            run_id,
            "item",
            {
                "id": item_id,
                "tc_code": tc.get("tc_code"),
                "tc_name": tc.get("name"),
                "status": "running",
                "rf_run_id": rf_run_id,
            },
        )
        suite_path = tc.get("suite_path")
        target = suites_dir / suite_path if suite_path else suites_dir
        if not mod_runtime._contained(suites_dir, target):
            raise ValueError("Test source must stay inside the frozen project scripts")
        cmd = [
            sys.executable,
            "-m",
            "robot",
            "--outputdir",
            str(item_dir),
            "--output",
            "output.xml",
            "--log",
            "log.html",
            "--report",
            "report.html",
            "--pythonpath",
            str(suites_dir),
            "--variable",
            f"RUN_ID:{run_id}",
            "--variable",
            f"BSS_ENV:{mod_runtime.BSS_ENV}",
        ]
        if dom_capture and mod_runtime.DOM_LISTENER.is_file():
            cmd += ["--listener", f"{mod_runtime.DOM_LISTENER}:{item_dir}"]
        if tc.get("extra_args"):
            cmd.extend(mod_runtime._split_args(tc["extra_args"]))
        if extra_args:
            cmd.extend(mod_runtime._split_args(extra_args))
        cmd += ["--variablefile", str(Path(__file__).parent / "profile_variables.py")]
        cmd.append(str(target))
        log_file = item_dir / "console.log"
        env = runner_environment(profile, profile_secrets)
        timed_out = False
        t_start = time.monotonic()
        attempts = 0
        exit_code = -1
        for attempt in range(retry_limit + 1):
            if run_id in mod_runtime._cancelled_runs:
                break
            attempts += 1
            timed_out = False
            attempt_dir = item_dir / f"attempt-{attempts}"
            attempt_dir.mkdir()
            attempt_cmd = list(cmd)
            attempt_cmd[attempt_cmd.index("--outputdir") + 1] = str(attempt_dir)
            attempt_started = time.monotonic()
            with open(
                (
                    log_file
                    if os.getenv("BRACE_RUNNER_MODE", "local") == "remote"
                    else attempt_dir / "console.log"
                ),
                "a",
                encoding="utf-8",
            ) as f:
                f.write(f"\n[BRACE] Attempt {attempts}\n")
                f.flush()
                if os.getenv("BRACE_RUNNER_MODE", "local") == "remote":
                    proc = await RemoteRobotProcess.start(
                        project_id,
                        attempt_cmd,
                        suites_dir,
                        attempt_dir,
                        env,
                        mod_runtime.TEST_TIMEOUT_SEC,
                    )
                else:
                    proc = await asyncio.create_subprocess_exec(
                        *attempt_cmd,
                        stdout=f,
                        stderr=subprocess.STDOUT,
                        env=env,
                        start_new_session=os.name != "nt",
                    )
                mod_runtime._active_procs[rf_run_id] = proc
                try:
                    exit_code = await asyncio.wait_for(
                        proc.wait(), timeout=mod_runtime.TEST_TIMEOUT_SEC
                    )
                except asyncio.TimeoutError:
                    timed_out = True
                    mod_runtime.log.warning(
                        "Test %s exceeded %ds — terminating.",
                        rf_run_id,
                        mod_runtime.TEST_TIMEOUT_SEC,
                    )
                    await terminate_tree(proc)
                    exit_code = proc.returncode
                    f.write(
                        f"\n\n[BRACE] Timed out after {mod_runtime.TEST_TIMEOUT_SEC}s and was terminated.\n"
                    )
                finally:
                    mod_runtime._active_procs.pop(rf_run_id, None)
            import shutil

            for artifact in attempt_dir.iterdir():
                if artifact.is_file():
                    if artifact.name == "console.log":
                        with log_file.open("a", encoding="utf-8") as console:
                            console.write(
                                artifact.read_text(encoding="utf-8", errors="replace")
                            )
                    else:
                        await asyncio.to_thread(
                            shutil.copyfile, artifact, item_dir / artifact.name
                        )
                elif artifact.is_dir():
                    await asyncio.to_thread(
                        shutil.copytree,
                        artifact,
                        item_dir / artifact.name,
                        dirs_exist_ok=True,
                    )
            attempt_status = (
                "cancelled"
                if run_id in mod_runtime._cancelled_runs
                else ("passed" if exit_code == 0 and not timed_out else "failed")
            )
            with database() as conn:
                conn.execute(
                    "INSERT INTO test_attempts(item_id,attempt,status,duration_sec,artifact_dir) VALUES (?,?,?,?,?)",
                    (
                        item_id,
                        attempts,
                        attempt_status,
                        time.monotonic() - attempt_started,
                        attempt_dir.name,
                    ),
                )
            if attempt_status == "passed" or run_id in mod_runtime._cancelled_runs:
                break
        with database() as conn:
            conn.execute(
                "UPDATE test_run_items SET attempt_count=?,passed_after_retry=? WHERE id=?",
                (attempts, int(attempts > 1 and exit_code == 0), item_id),
            )
    if run_id in mod_runtime._cancelled_runs or run_id not in mod_runtime._active_runs:
        return None
    status = "passed" if exit_code == 0 and (not timed_out) else "failed"
    if status == "passed":
        tally["passed"] += 1
        mod_runtime._metrics["tests_passed"] += 1
    else:
        tally["failed"] += 1
        mod_runtime._metrics["tests_failed"] += 1
    if timed_out:
        mod_runtime._metrics["tests_timeout"] += 1
    elapsed = max(0.0, time.monotonic() - t_start)
    mod_runtime._metrics["test_seconds"] += elapsed
    mod_runtime._metrics["test_count"] += 1
    mod_runtime.log.info(
        "Test case finished",
        extra={
            "run_id": run_id,
            "project_id": project_id,
            "tc_code": tc.get("tc_code"),
            "status": status,
            "duration_sec": round(elapsed, 1),
            "timed_out": timed_out,
        },
    )
    out_xml = item_dir / "output.xml"
    fail_summary = fail_detail = fail_shot = None
    if status == "failed" and out_xml.exists():
        try:
            fail_summary, fail_detail, fail_shot = await asyncio.to_thread(
                mod_reporting._extract_failure, out_xml, item_dir
            )
        except Exception as exc:
            mod_runtime.log.debug(
                "Could not extract failure detail for %s: %s", rf_run_id, exc
            )
    if status == "failed" and not fail_summary:
        fail_summary = (
            f"Execution timed out after {mod_runtime.TEST_TIMEOUT_SEC}s"
            if timed_out
            else f"Robot exited with code {exit_code}; inspect the attempt console"
        )
    dom_rel = failed_locator = locator_sig = None
    if status == "failed" and dom_capture:
        try:
            dom_rel, failed_locator, locator_sig = await asyncio.to_thread(
                mod_diagnostics._record_capture,
                project_id,
                run_id,
                item_id,
                tc,
                item_dir,
                fail_detail,
            )
        except Exception as exc:
            mod_runtime.log.debug(
                "Could not record page capture for %s: %s", rf_run_id, exc
            )
    now = datetime.now().isoformat()
    await asyncio.to_thread(
        mod_runtime._db_write,
        [
            (
                "UPDATE test_run_items SET status=?, finished_at=?, fail_summary=?, fail_detail=?, fail_screenshot=?, dom_capture=?, failed_locator=?, locator_sig=? WHERE id=? AND status='running'",
                (
                    status,
                    now,
                    fail_summary,
                    fail_detail,
                    fail_shot,
                    dom_rel,
                    failed_locator,
                    locator_sig,
                    item_id,
                ),
            ),
            (
                "UPDATE test_cases SET last_run_status=?, last_run_at=? WHERE id=?",
                (status, now, tc["id"]),
            ),
        ],
    )
    if run_id in mod_runtime._active_runs:
        mod_runtime._active_runs[run_id]["passed"] = tally["passed"]
        mod_runtime._active_runs[run_id]["failed"] = tally["failed"]
    mod_runtime._publish(
        run_id,
        "item",
        {
            "id": item_id,
            "tc_code": tc.get("tc_code"),
            "tc_name": tc.get("name"),
            "status": status,
            "rf_run_id": rf_run_id,
            "fail_summary": fail_summary,
            "passed": tally["passed"],
            "failed": tally["failed"],
        },
    )
    return str(out_xml) if out_xml.exists() else None


async def _run_suite(
    run_id: str,
    project_id: int,
    items: list,
    extra_args: Optional[str],
    parallel: Optional[int] = None,
):
    """Execute the run's test cases, then merge their reports with rebot.

    Cases run `parallel`-wide within the run; the global _tslots() budget still
    bounds how many actually execute at once across all runs.
    """
    import diagnostics as mod_diagnostics
    import jobs as mod_jobs
    import runtime as mod_runtime

    run_dir = mod_runtime._project_results(project_id) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    suites_dir = await asyncio.to_thread(
        snapshot_sources, mod_runtime._project_suites(project_id), run_dir / "sources"
    )
    with database() as conn:
        run_config = conn.execute(
            "SELECT profile_snapshot,profile_secrets,retry_limit FROM test_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
    profile = json.loads(run_config["profile_snapshot"] or "{}")
    tally = {"passed": 0, "failed": 0}
    dom_capture = await asyncio.to_thread(
        mod_diagnostics._dom_capture_enabled, project_id
    )
    width = max(
        1,
        min(
            mod_runtime.MAX_CONCURRENT_TESTS,
            parallel or mod_runtime.RUN_PARALLEL_DEFAULT,
            len(items),
        ),
    )
    gate = asyncio.Semaphore(width)

    async def worker(item):
        import runtime as mod_runtime

        async with gate:
            if (
                run_id in mod_runtime._cancelled_runs
                or run_id not in mod_runtime._active_runs
            ):
                return None
            return await _run_one_item(
                run_id,
                project_id,
                item,
                extra_args,
                run_dir,
                suites_dir,
                tally,
                dom_capture,
                profile,
                run_config["profile_secrets"] or "",
                run_config["retry_limit"],
            )

    mod_runtime.log.info(
        "Run executing",
        extra={
            "run_id": run_id,
            "project_id": project_id,
            "total": len(items),
            "parallel": width,
        },
    )
    results = await map_bounded(items, worker, width)
    output_files = []
    for item, res in zip(items, results):
        if isinstance(res, Exception):
            mod_runtime.log.error(
                "Test case raised",
                extra={
                    "run_id": run_id,
                    "tc_code": item["tc"].get("tc_code"),
                    "error": str(res),
                },
            )
            tally["failed"] += 1
            conn = get_db()
            conn.execute(
                "UPDATE test_run_items SET status='failed', finished_at=?, fail_summary=? WHERE id=? AND status='running'",
                (datetime.now().isoformat(), f"Executor error: {res}", item["item_id"]),
            )
            conn.commit()
            conn.close()
        elif res:
            output_files.append(res)
    passed, failed = (tally["passed"], tally["failed"])
    if run_id in mod_runtime._cancelled_runs or run_id not in mod_runtime._active_runs:
        mod_runtime.log.info(
            "Run %s cancelled — %d/%d case(s) completed.",
            run_id,
            passed + failed,
            len(items),
        )
        return
    final_status = "passed" if failed == 0 else "failed"
    if output_files:
        try:
            rebot_dir = run_dir / "combined"
            rebot_dir.mkdir(exist_ok=True)
            await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-m",
                    "robot.rebot",
                    "--outputdir",
                    str(rebot_dir),
                    "--output",
                    "output.xml",
                    "--log",
                    "log.html",
                    "--report",
                    "report.html",
                    "--name",
                    run_id,
                    *output_files,
                ],
                capture_output=True,
            )
        except Exception as e:
            mod_runtime.log.warning("rebot merge failed: %s", e)
    conn = get_db()
    conn.execute(
        "UPDATE test_runs SET status=?, passed=?, failed=?, finished_at=? WHERE run_id=? AND status != 'cancelled'",
        (final_status, passed, failed, datetime.now().isoformat(), run_id),
    )
    conn.commit()
    conn.close()
    if run_id in mod_runtime._active_runs:
        mod_runtime._active_runs[run_id]["status"] = final_status
    mod_runtime._metrics[
        "runs_passed" if final_status == "passed" else "runs_failed"
    ] += 1
    mod_runtime.log.info(
        "Run finished",
        extra={
            "run_id": run_id,
            "project_id": project_id,
            "status": final_status,
            "passed": passed,
            "failed": failed,
        },
    )
    mod_runtime._publish(
        run_id, "done", {"status": final_status, "passed": passed, "failed": failed}
    )
    if run_id not in mod_runtime._cancelled_runs:
        asyncio.create_task(mod_jobs._notify_run_finished(run_id, final_status))
