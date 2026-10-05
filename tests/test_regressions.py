"""Run with python -m unittest discover -s tests -v."""
import asyncio
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

_temp = tempfile.TemporaryDirectory()
os.environ.update(CONFIG_DIR=str(Path(_temp.name) / "config"),
                  SUITES_DIR=str(Path(_temp.name) / "suites"),
                  RESULTS_DIR=str(Path(_temp.name) / "results"),
                  BSS_ENV="local", JWT_SECRET="test-secret-" * 4,
                  BRACE_ENCRYPT_KEY=Fernet.generate_key().decode())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))
import db
import main
import execution
import git_sync
from provenance import snapshot_sources
from fastapi.testclient import TestClient
from security import LoginLimiter


class SecurityTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.database() as conn:
            conn.execute("DELETE FROM project_members")
            conn.execute("DELETE FROM users")
            conn.execute("INSERT INTO users(username,password_hash,system_role) VALUES (?,?,?)",
                         ("admin", db.pwd_context.hash("old-password"), "admin"))
        self.client = TestClient(main.app)
        self.token = main._make_token("admin", "admin")
        self.headers = {"Authorization": "Bearer " + self.token}

    def test_deleted_admin_token_denied(self):
        with db.database() as conn:
            conn.execute("DELETE FROM users")
        self.assertEqual(self.client.get("/api/users", headers=self.headers).status_code, 401)

    def test_demotion_revokes_existing_token(self):
        with db.database() as conn:
            uid = conn.execute("SELECT id FROM users").fetchone()[0]
        response = self.client.put(f"/api/users/{uid}", json={"system_role": "user"}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/users", headers=self.headers).status_code, 401)

    def test_password_change_rotates_token(self):
        response = self.client.put("/api/auth/change-password", headers=self.headers,
                                  json={"old_password": "old-password", "new_password": "new-password"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/users", headers=self.headers).status_code, 401)
        self.assertEqual(self.client.get("/api/users", headers={"Authorization": "Bearer " + response.json()["access_token"]}).status_code, 200)

    def test_forced_password_change_blocks_api_and_reports(self):
        with db.database() as conn:
            conn.execute("UPDATE users SET must_change_password=1")
        self.assertEqual(self.client.get("/api/projects", headers=self.headers).status_code, 403)
        self.assertEqual(self.client.get("/results/1/run/x.html", params={"token": self.token}).status_code, 403)
        self.assertEqual(self.client.get("/api/runs/run/events", params={"token": self.token}).status_code, 403)
        response = self.client.put("/api/auth/change-password", headers=self.headers,
                                  json={"old_password": "old-password", "new_password": "new-password"})
        self.assertEqual(response.status_code, 200)

    def test_logout_revokes_token(self):
        self.assertEqual(self.client.post("/api/auth/logout", headers=self.headers).status_code, 204)
        self.assertEqual(self.client.get("/api/users", headers=self.headers).status_code, 401)

    def test_production_requires_encryption(self):
        with patch.object(main, "BSS_ENV", "production"), patch.object(main, "encryption_available", return_value=False):
            with self.assertRaises(RuntimeError):
                main._preflight_security_check()

    def test_transaction_rolls_back(self):
        with self.assertRaises(RuntimeError):
            with db.database() as conn:
                conn.execute("UPDATE users SET full_name='changed'")
                raise RuntimeError("abort")
        with db.database() as conn:
            self.assertIsNone(conn.execute("SELECT full_name FROM users").fetchone()[0])

    def test_password_policy(self):
        response = self.client.put("/api/auth/change-password", headers=self.headers,
                                  json={"old_password": "old-password", "new_password": "short"})
        self.assertEqual(response.status_code, 400)

    def test_login_throttle_expires(self):
        clock = [0]
        limiter = LoginLimiter(attempts=2, window=60, clock=lambda: clock[0])
        limiter.check("peer")
        limiter.check("peer")
        with self.assertRaises(main.HTTPException) as caught:
            limiter.check("peer")
        self.assertEqual(caught.exception.status_code, 429)
        clock[0] = 61
        limiter.check("peer")

    def test_pagination_rejects_unbounded_requests(self):
        self.assertEqual(self.client.get("/api/projects/1/runs?limit=99999", headers=self.headers).status_code, 422)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_executes_frozen_sources_and_merges_reports(self):
        db.init_db()
        with db.database() as conn:
            pid = conn.execute("INSERT INTO projects(name) VALUES ('Frozen run regression')").lastrowid
            tid = conn.execute("INSERT INTO test_cases(project_id,name,tc_code,suite_path) VALUES (?,?,?,?)",
                               (pid, "Pass", "FROZEN_TEST", "pass.robot")).lastrowid
            tc = dict(conn.execute("SELECT * FROM test_cases WHERE id=?", (tid,)).fetchone())
        suites = main._project_suites(pid)
        suites.mkdir(parents=True)
        (suites / "pass.robot").write_text("*** Test Cases ***\nPass\n    No Operation\n")
        main._run_slots = main._test_slots = None
        main._slots()
        started = main._start_run(pid, [tc], "Frozen regression", "admin", None)
        run_id = started["run_id"]
        task = next(task for task in asyncio.all_tasks() if task.get_coro().__name__ == "_execute_run")
        await asyncio.wait_for(task, 20)
        with db.database() as conn:
            self.assertEqual(conn.execute("SELECT status FROM test_runs WHERE run_id=?", (run_id,)).fetchone()[0], "passed")
        run_dir = main._project_results(pid) / run_id
        self.assertTrue((run_dir / "sources" / "pass.robot").is_file())
        self.assertTrue((run_dir / "source-manifest.json").is_file())
        self.assertTrue((run_dir / "combined" / "report.html").is_file())
        self.assertNotIn(run_id, main._active_runs)

    async def test_cancelled_item_is_not_overwritten_by_worker(self):
        db.init_db()
        with db.database() as conn:
            pid = conn.execute("INSERT INTO projects(name) VALUES ('Cancellation regression')").lastrowid
            tid = conn.execute("INSERT INTO test_cases(project_id,name,tc_code,suite_path) VALUES (?,?,?,?)",
                               (pid, "Wait", "CANCEL_TEST", "wait.robot")).lastrowid
            tc = dict(conn.execute("SELECT * FROM test_cases WHERE id=?", (tid,)).fetchone())
        suites = main._project_suites(pid)
        suites.mkdir(parents=True)
        (suites / "wait.robot").write_text("*** Test Cases ***\nWait\n    Sleep    60s\n")
        main._run_slots = main._test_slots = None
        main._slots()
        started = main._start_run(pid, [tc], "Cancel regression", "admin", None)
        run_id = started["run_id"]
        task = next(task for task in asyncio.all_tasks() if task.get_coro().__name__ == "_execute_run")
        for _ in range(100):
            if main._active_procs:
                break
            await asyncio.sleep(0.02)
        self.assertTrue(main._active_procs)
        await main.cancel_run(run_id, {"username": "admin", "role": "admin"})
        await asyncio.wait_for(task, 10)
        with db.database() as conn:
            self.assertEqual(conn.execute("SELECT status FROM test_run_items WHERE run_id=?", (run_id,)).fetchone()[0], "cancelled")
            self.assertEqual(conn.execute("SELECT status FROM test_runs WHERE run_id=?", (run_id,)).fetchone()[0], "cancelled")
        self.assertFalse(main._active_procs)

    async def test_real_robot_process_can_be_terminated(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sleep.robot"
            path.write_text("*** Test Cases ***\nWait\n    Sleep    60s\n")
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "robot", "--outputdir", folder, str(path),
                start_new_session=os.name != "nt",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.sleep(0.3)
            await execution.terminate_tree(proc)
            self.assertIsNotNone(proc.returncode)

    async def test_bounded_workers_preserve_order_and_failures(self):
        active = peak = 0
        async def work(value):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.001)
                if value == 5:
                    raise ValueError("failed")
                return value * 2
            finally:
                active -= 1
        result = await execution.map_bounded(list(range(1000)), work, 3)
        self.assertEqual(peak, 3)
        self.assertEqual(result[999], 1998)
        self.assertIsInstance(result[5], ValueError)


class ParsingTests(unittest.TestCase):
    def test_frozen_source_survives_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "scripts"
            source.mkdir()
            script = source / "example.robot"
            script.write_text("original")
            destination = snapshot_sources(source, root / "run" / "sources")
            script.write_text("changed")
            self.assertEqual((destination / "example.robot").read_text(), "original")
            self.assertTrue((destination.parent / "source-manifest.json").is_file())

    def test_schedule_policy_rejects_unknown_value(self):
        with self.assertRaises(ValueError):
            main.ScheduleCreate(group_id=1, cron_expr="0 2 * * *", overlap_policy="unknown")

    def test_schedule_skip_checks_live_runs_on_owning_loop(self):
        db.init_db()
        with db.database() as conn:
            pid = conn.execute("INSERT INTO projects(name) VALUES ('Schedule regression')").lastrowid
            gid = conn.execute("INSERT INTO test_groups(project_id,name) VALUES (?, 'Suite')", (pid,)).lastrowid
            tid = conn.execute("INSERT INTO test_cases(project_id,name) VALUES (?, 'Scheduled')", (pid,)).lastrowid
            conn.execute("INSERT INTO group_test_cases(group_id,test_case_id) VALUES (?,?)", (gid, tid))
        class Loop:
            def is_closed(self): return False
            def call_soon_threadsafe(self, callback): callback()
        with patch.object(main, "_main_loop", Loop()), patch.object(main, "_active_runs", {"active": {"group_id": gid, "status": "running"}}), patch.object(main, "_start_run") as start:
            main._trigger_group_run(gid, "skip")
            start.assert_not_called()
            main._trigger_group_run(gid, "queue")
            start.assert_called_once()

    def test_content_change_with_same_size_and_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "test.robot"
            path.write_text("*** Test Cases ***\nFirst\n    No Operation\n")
            stamp = path.stat()
            self.assertEqual(git_sync.parse_repo(root, False)[0][0]["name"], "First")
            path.write_text("*** Test Cases ***\nOther\n    No Operation\n")
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            self.assertEqual(git_sync.parse_repo(root, False)[0][0]["name"], "Other")


if __name__ == "__main__":
    unittest.main()
