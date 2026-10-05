"""Authenticated transport to a runner that has no controller database or secrets."""

import asyncio
import base64
import io
import json
import os
from pathlib import Path
import urllib.request
import zipfile

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXTRACT_BYTES = 256 * 1024 * 1024


def unpack_archive(data, destination):
    """Validate every entry before extraction; reject links and expansion bombs."""
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if (
            len(entries) > 10000
            or sum(entry.file_size for entry in entries) > MAX_EXTRACT_BYTES
        ):
            raise ValueError("Archive exceeds runner limits")
        names = set()
        for entry in entries:
            name = entry.filename.replace("\\", "/")
            target = (root / name).resolve()
            if (
                not target.is_relative_to(root)
                or name in names
                or (entry.external_attr >> 16 & 0o170000) == 0o120000
            ):
                raise ValueError("Unsafe archive entry")
            names.add(name)
        archive.extractall(root)


def pack_directory(directory):
    stream = io.BytesIO()
    root = Path(directory).resolve()
    total = 0
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("Runner artifacts cannot contain symlinks")
            if path.is_file():
                total += path.stat().st_size
                if total > MAX_EXTRACT_BYTES:
                    raise ValueError("Runner artifacts exceed expanded limit")
                archive.write(path, path.relative_to(root).as_posix())
    data = stream.getvalue()
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Runner archive exceeds 64 MiB")
    return data


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("Runner redirects are not allowed")


class RemoteRobotProcess:
    def __init__(self, url, token, job_id, output):
        self.url, self.token, self.job_id, self.output = (
            url.rstrip("/"),
            token,
            job_id,
            Path(output),
        )
        self.returncode = None
        self.wait_lock = asyncio.Lock()

    def request(self, method, path, payload=None, binary=False):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            self.url + path,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.build_opener(NoRedirect()).open(
            request, timeout=30
        ) as response:
            body = response.read(MAX_ARCHIVE_BYTES + 1)
        if len(body) > MAX_ARCHIVE_BYTES:
            raise ValueError("Runner response exceeds transfer limit")
        return body if binary else json.loads(body or "{}")

    @classmethod
    async def start(cls, project_id, cmd, suites_dir, output_dir, environment, timeout):
        endpoints = json.loads(os.getenv("BRACE_RUNNER_ENDPOINTS", "{}"))
        config = endpoints.get(str(project_id))
        if not config:
            raise RuntimeError("No isolated runner configured for this project")
        if not config["url"].startswith(("https://", "http://")):
            raise ValueError("Runner URL must use HTTP or HTTPS")
        client = cls(config["url"], config["token"], "", output_dir)
        args = list(cmd[3:])
        for index, value in enumerate(args):
            if value.startswith(str(suites_dir)):
                args[index] = "__SOURCES__" + value[len(str(suites_dir)) :].replace(
                    "\\", "/"
                )
            elif value == str(output_dir):
                args[index] = "__RESULTS__"
            elif "profile_variables.py" in value:
                args[index] = "__PROFILE__"
            elif "brace_capture.py" in value:
                args[index] = "__LISTENER__:__RESULTS__"
        archive = await asyncio.to_thread(pack_directory, suites_dir)
        # Only explicitly selected test variables/custom environment travel.
        from environments import RUNNER_ENV_KEYS

        environment = {
            key: value
            for key, value in environment.items()
            if key not in RUNNER_ENV_KEYS
        }
        result = await asyncio.to_thread(
            client.request,
            "POST",
            "/jobs",
            {
                "project_id": project_id,
                "arguments": args,
                "environment": environment,
                "sources": base64.b64encode(archive).decode(),
                "timeout": timeout,
            },
        )
        client.job_id = result["id"]
        return client

    async def wait(self):
        async with self.wait_lock:
            return await self._wait()

    async def _wait(self):
        while self.returncode is None:
            status = await asyncio.to_thread(
                self.request, "GET", f"/jobs/{self.job_id}"
            )
            if status["status"] in {"passed", "failed", "cancelled"}:
                archive = await asyncio.to_thread(
                    self.request, "GET", f"/jobs/{self.job_id}/results", None, True
                )
                await asyncio.to_thread(unpack_archive, archive, self.output)
                await asyncio.to_thread(self.request, "DELETE", f"/jobs/{self.job_id}")
                self.returncode = status["exit_code"]
                break
            await asyncio.sleep(0.5)
        return self.returncode

    async def cancel(self):
        if self.returncode is None:
            await asyncio.to_thread(self.request, "POST", f"/jobs/{self.job_id}/cancel")
            # wait() transfers final logs before deleting the job.
            await asyncio.wait_for(self.wait(), 30)
