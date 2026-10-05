"""HTTP worker smoke: authentication, project boundary and real Robot artifacts."""

import asyncio
import base64
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from test_regressions import runtime
from runner_transport import RemoteRobotProcess, pack_directory
import urllib.error


class RemoteWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_worker_transport_and_project_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            env = dict(
                os.environ,
                BRACE_RUNNER_PROJECT_ID="777",
                BRACE_RUNNER_TOKEN="worker-test-token-" * 3,
                BRACE_RUNNER_WORK_DIR=folder,
            )
            with open(Path(folder) / "worker.log", "w") as log:
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "runner_worker:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                    ],
                    cwd=Path(__file__).resolve().parents[1] / "controller",
                    env=env,
                    stdout=log,
                    stderr=log,
                )
                try:
                    import urllib.request

                    url = f"http://127.0.0.1:{port}"
                    for _ in range(100):
                        try:
                            await asyncio.to_thread(
                                urllib.request.urlopen, url + "/health", timeout=1
                            )
                            break
                        except Exception:
                            await asyncio.sleep(0.05)
                    else:
                        self.fail((Path(folder) / "worker.log").read_text())
                    client = RemoteRobotProcess(url, "wrong", "", folder)
                    with self.assertRaises(urllib.error.HTTPError) as denied:
                        await asyncio.to_thread(client.request, "GET", "/jobs/missing")
                    self.assertEqual(denied.exception.code, 401)
                    source = Path(folder) / "scripts"
                    source.mkdir()
                    (source / "pass.robot").write_text(
                        "*** Test Cases ***\nRemote\n    Should Be Equal    ${TARGET}    qa\n"
                    )
                    client = RemoteRobotProcess(
                        url, env["BRACE_RUNNER_TOKEN"], "", folder
                    )
                    with self.assertRaises(urllib.error.HTTPError) as boundary:
                        await asyncio.to_thread(
                            client.request,
                            "POST",
                            "/jobs",
                            {
                                "project_id": 778,
                                "sources": base64.b64encode(
                                    pack_directory(source)
                                ).decode(),
                                "arguments": [],
                                "timeout": 10,
                            },
                        )
                    self.assertEqual(boundary.exception.code, 403)
                    result = Path(folder) / "result"
                    result.mkdir()
                    with patch.dict(
                        os.environ,
                        {
                            "BRACE_RUNNER_ENDPOINTS": '{"777":{"url":"'
                            + url
                            + '","token":"'
                            + env["BRACE_RUNNER_TOKEN"]
                            + '"}}'
                        },
                    ):
                        remote = await RemoteRobotProcess.start(
                            777,
                            [
                                sys.executable,
                                "-m",
                                "robot",
                                "--outputdir",
                                str(result),
                                "--variablefile",
                                str(
                                    Path(__file__).resolve().parents[1]
                                    / "controller"
                                    / "profile_variables.py"
                                ),
                                str(source / "pass.robot"),
                            ],
                            source,
                            result,
                            {"BRACE_PROFILE_VARIABLES": '{"TARGET":"qa"}'},
                            10,
                        )
                        self.assertEqual(await asyncio.wait_for(remote.wait(), 20), 0)
                    self.assertTrue((result / "output.xml").is_file())
                    self.assertTrue((result / "log.html").is_file())
                    with self.assertRaises(urllib.error.HTTPError) as deleted:
                        await asyncio.to_thread(
                            remote.request, "GET", f"/jobs/{remote.job_id}"
                        )
                    self.assertEqual(deleted.exception.code, 404)
                finally:
                    proc.terminate()
                    await asyncio.to_thread(proc.wait, 10)
