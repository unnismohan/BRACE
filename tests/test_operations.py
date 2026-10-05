import asyncio
import base64
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from test_regressions import db, runtime, authentication, execution_engine, main
from execution import ProjectFairGate
from profiles import runner_environment, seal_secrets
from runner_transport import unpack_archive, pack_directory
from fastapi.testclient import TestClient


class OperationsApiTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.database() as c:
            c.execute("DELETE FROM users")
            c.execute(
                "INSERT INTO users(username,password_hash,system_role) VALUES ('ops',?,'admin')",
                (db.pwd_context.hash("testing-password"),),
            )
            self.pid = c.execute(
                "INSERT INTO projects(name) VALUES ('Operations tests')"
            ).lastrowid
            c.executemany(
                "INSERT INTO test_cases(project_id,name,tc_code) VALUES (?,?,?)",
                [
                    (self.pid, f"Case {i:03}", f"OPS_{self.pid}_{i:03}")
                    for i in range(125)
                ],
            )
        self.client = TestClient(main.app)
        self.headers = {
            "Authorization": "Bearer " + authentication._make_token("ops", "admin")
        }

    def test_server_pagination_and_literal_search(self):
        path = f"/api/projects/{self.pid}/test-cases/page"
        first = self.client.get(path, headers=self.headers).json()
        second = self.client.get(
            path, params={"offset": 50}, headers=self.headers
        ).json()
        self.assertEqual(first["total"], 125)
        self.assertEqual(len(first["items"]), 50)
        self.assertFalse(
            {r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}
        )
        self.assertEqual(
            self.client.get(path, params={"q": "%"}, headers=self.headers).json()[
                "total"
            ],
            0,
        )
        self.assertEqual(
            self.client.get(
                path, params={"limit": 999}, headers=self.headers
            ).status_code,
            422,
        )

    def test_profile_scope_reserved_keys_and_secret_redaction(self):
        path = f"/api/projects/{self.pid}/profiles"
        response = self.client.post(
            path,
            headers=self.headers,
            json={
                "name": "QA",
                "variables": {"BASE_URL": "https://example.test"},
                "secret_variables": {"PASSWORD": "hidden-value"},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        profiles = self.client.get(path, headers=self.headers).json()
        self.assertEqual(profiles[0]["secret_names"], ["PASSWORD"])
        self.assertNotIn("hidden-value", json.dumps(profiles))
        with db.database() as c:
            stored = c.execute(
                "SELECT secret_variables FROM environment_profiles WHERE id=?",
                (response.json()["id"],),
            ).fetchone()[0]
        self.assertNotIn("hidden-value", stored)
        rejected = self.client.post(
            path,
            headers=self.headers,
            json={"name": "bad", "environment": {"PYTHONPATH": "/evil"}},
        )
        self.assertEqual(rejected.status_code, 400)
        other = self.client.get("/api/projects/999999/profiles", headers=self.headers)
        self.assertEqual(other.status_code, 404)

    def test_quarantine_requires_reason_and_overview_counts(self):
        with db.database() as c:
            tid = c.execute(
                "SELECT id FROM test_cases WHERE project_id=?", (self.pid,)
            ).fetchone()[0]
        url = f"/api/test-cases/{tid}/quarantine"
        self.assertEqual(
            self.client.put(
                url, headers=self.headers, json={"quarantined": True}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.put(
                url,
                headers=self.headers,
                json={"quarantined": True, "reason": "Intermittent dependency"},
            ).status_code,
            200,
        )
        response = self.client.get(
            f"/api/projects/{self.pid}/overview", headers=self.headers
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["cases"]["quarantined"], 1)

    def test_suites_and_members_are_paged_with_global_counts(self):
        with db.database() as c:
            gid = c.execute(
                "INSERT INTO test_groups(project_id,name) VALUES (?,?)",
                (self.pid, "Large suite"),
            ).lastrowid
            c.execute(
                "INSERT INTO group_test_cases(group_id,test_case_id) SELECT ?,id FROM test_cases WHERE project_id=?",
                (gid, self.pid),
            )
        response = self.client.get(
            f"/api/projects/{self.pid}/groups/page", headers=self.headers
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"][0]["tc_count"], 125)
        self.assertEqual(response.json()["items"][0]["test_cases"], [])
        members = self.client.get(
            f"/api/projects/{self.pid}/groups/{gid}/cases/page?offset=100",
            headers=self.headers,
        )
        self.assertEqual(members.status_code, 200, members.text)
        self.assertEqual(members.json()["total"], 125)
        self.assertEqual(len(members.json()["items"]), 25)
        other = self.client.get(
            f"/api/projects/{self.pid+1}/groups/{gid}/cases/page", headers=self.headers
        )
        self.assertEqual(other.status_code, 404)

    def test_history_and_users_pages_are_bounded(self):
        with db.database() as c:
            tid = c.execute(
                "SELECT id FROM test_cases WHERE project_id=?", (self.pid,)
            ).fetchone()[0]
            rid = f"history-{self.pid}"
            c.execute(
                "INSERT INTO test_runs(run_id,project_id,status,total) VALUES (?,?,'passed',75)",
                (rid, self.pid),
            )
            c.executemany(
                "INSERT INTO test_run_items(run_id,test_case_id,status) VALUES (?,?,'passed')",
                [(rid, tid)] * 75,
            )
        response = self.client.get(
            f"/api/test-cases/{tid}/history?offset=50&limit=50", headers=self.headers
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 75)
        self.assertEqual(len(response.json()["history"]), 25)
        self.assertEqual(response.json()["stats_scope"], "page")
        users = self.client.get("/api/users/page?limit=50", headers=self.headers)
        self.assertEqual(users.status_code, 200, users.text)
        self.assertNotIn("password_hash", users.text)

    def test_quarantine_is_excluded_unless_explicitly_requested(self):
        from unittest.mock import patch

        with db.database() as c:
            rows = c.execute(
                "SELECT id FROM test_cases WHERE project_id=? ORDER BY id LIMIT 2",
                (self.pid,),
            ).fetchall()
            ids = [row[0] for row in rows]
            c.execute(
                "UPDATE test_cases SET quarantined=1,quarantine_reason='Dependency' WHERE id=?",
                (ids[0],),
            )
        with patch(
            "execution_engine._start_run",
            return_value={"run_id": "test-run", "total": 1, "parallel": 1},
        ) as start:
            response = self.client.post(
                f"/api/projects/{self.pid}/runs",
                headers=self.headers,
                json={"tc_ids": ids},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([tc["id"] for tc in start.call_args.args[1]], [ids[1]])
            self.client.post(
                f"/api/projects/{self.pid}/runs",
                headers=self.headers,
                json={"tc_ids": ids, "include_quarantined": True},
            )
            self.assertEqual([tc["id"] for tc in start.call_args.args[1]], ids)

    def test_runner_environment_drops_controller_secrets(self):
        with patch.dict(
            os.environ,
            {
                "JWT_SECRET": "dont-send",
                "SMTP_PASSWORD": "dont-send",
                "BRACE_RUNNER_TOKEN": "dont-send",
            },
        ):
            env = runner_environment(
                {"environment": {"TARGET": "qa"}, "variables": {"BASE": "qa"}},
                seal_secrets({"PASSWORD": "selected-test-secret"}),
            )
        self.assertNotIn("JWT_SECRET", env)
        self.assertNotIn("SMTP_PASSWORD", env)
        self.assertNotIn("BRACE_RUNNER_TOKEN", env)
        self.assertEqual(
            json.loads(env["BRACE_PROFILE_VARIABLES"])["PASSWORD"],
            "selected-test-secret",
        )

    def test_archive_traversal_and_links_rejected(self):
        for name, mode in [("../escape", 0), ("link", 0o120777)]:
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as z:
                entry = zipfile.ZipInfo(name)
                entry.external_attr = mode << 16
                z.writestr(entry, "data")
            with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
                unpack_archive(stream.getvalue(), folder)


class FairAndRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_other_project_precedes_bulk_backlog(self):
        gate = ProjectFairGate(1)
        order = []
        release = asyncio.Event()

        async def run(project, key, hold=False):
            async with gate.slot(project, key):
                order.append(key)
                if hold:
                    await release.wait()

        first = asyncio.create_task(run(1, "first", True))
        await asyncio.sleep(0)
        tasks = [
            asyncio.create_task(run(1, "bulk1")),
            asyncio.create_task(run(1, "bulk2")),
            asyncio.create_task(run(2, "other")),
        ]
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, *tasks)
        self.assertEqual(order, ["first", "other", "bulk1", "bulk2"])
        self.assertEqual(sum(gate.active.values()), 0)

    async def test_cancelled_waiter_releases_capacity(self):
        gate = ProjectFairGate(1)
        async with gate.slot(1, "hold"):

            async def pending():
                async with gate.slot(2, "cancel"):
                    self.fail("cancelled waiter admitted")

            task = asyncio.create_task(pending())
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with gate.slot(3, "next"):
            self.assertEqual(sum(gate.active.values()), 1)

    async def test_preparation_failure_updates_run_counts(self):
        db.init_db()
        with db.database() as c:
            pid = c.execute(
                "INSERT INTO projects(name) VALUES ('Preparation failure')"
            ).lastrowid
            tid = c.execute(
                "INSERT INTO test_cases(project_id,name,tc_code) VALUES (?,?,?)",
                (pid, "Broken", "PREP_FAILURE"),
            ).lastrowid
            tc = dict(
                c.execute("SELECT * FROM test_cases WHERE id=?", (tid,)).fetchone()
            )
        with patch(
            "execution_engine.snapshot_sources",
            side_effect=OSError("source unavailable"),
        ):
            run = execution_engine._start_run(
                pid, [tc], "Preparation failure", "ops", None
            )
            task = next(
                t
                for t in asyncio.all_tasks()
                if t.get_coro().__name__ == "_execute_run"
            )
            await asyncio.wait_for(task, 10)
        with db.database() as c:
            row = c.execute(
                "SELECT status,passed,failed FROM test_runs WHERE run_id=?",
                (run["run_id"],),
            ).fetchone()
        self.assertEqual(tuple(row), ("failed", 0, 1))

    async def test_real_failure_retry_keeps_both_reports(self):
        db.init_db()
        with db.database() as c:
            pid = c.execute(
                "INSERT INTO projects(name) VALUES ('Retry tests')"
            ).lastrowid
            tid = c.execute(
                "INSERT INTO test_cases(project_id,name,tc_code,suite_path) VALUES (?,?,?,?)",
                (pid, "Flaky", "FLAKY", "flaky.robot"),
            ).lastrowid
            tc = dict(
                c.execute("SELECT * FROM test_cases WHERE id=?", (tid,)).fetchone()
            )
        source = runtime._project_suites(pid)
        source.mkdir(parents=True)
        (source / "flaky.robot").write_text(
            "*** Settings ***\nLibrary    OperatingSystem\n*** Test Cases ***\nFlaky\n    ${exists}=    Run Keyword And Return Status    File Should Exist    ${CURDIR}/marker\n    Create File    ${CURDIR}/marker    first attempt\n    Should Be True    ${exists}\n"
        )
        runtime._run_slots = runtime._test_slots = None
        run = execution_engine._start_run(
            pid, [tc], "Retry", "ops", None, retry_count=1
        )
        task = next(
            t for t in asyncio.all_tasks() if t.get_coro().__name__ == "_execute_run"
        )
        await asyncio.wait_for(task, 30)
        with db.database() as c:
            item = dict(
                c.execute(
                    "SELECT * FROM test_run_items WHERE run_id=?", (run["run_id"],)
                ).fetchone()
            )
            attempts = [
                r[0]
                for r in c.execute(
                    "SELECT status FROM test_attempts WHERE item_id=? ORDER BY attempt",
                    (item["id"],),
                )
            ]
        self.assertEqual(attempts, ["failed", "passed"])
        self.assertEqual(item["passed_after_retry"], 1)
        from routes.runs import get_run_item

        detail = get_run_item(
            run["run_id"], item["id"], {"username": "ops", "role": "admin"}
        )
        self.assertEqual(len(detail["attempts"]), 2)
        self.assertTrue(
            all(
                attempt["has_log"] and attempt["has_console"]
                for attempt in detail["attempts"]
            )
        )
        root = runtime._project_results(pid) / run["run_id"] / item["rf_run_id"]
        self.assertTrue((root / "attempt-1" / "log.html").exists())
        self.assertTrue((root / "attempt-2" / "log.html").exists())
