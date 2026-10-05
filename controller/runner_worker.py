"""Dedicated project worker. Start separately with uvicorn runner_worker:app."""

import asyncio
import base64
import hmac
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field
from execution import terminate_tree
from environments import runner_environment, validate_values
from runner_transport import unpack_archive, pack_directory


@asynccontextmanager
async def lifespan(app):
    yield
    for job in list(jobs.values()):
        if proc := job.get("proc"):
            await terminate_tree(proc)
        if not job["task"].done():
            job["task"].cancel()
    await asyncio.gather(
        *(job["task"] for job in jobs.values()), return_exceptions=True
    )


app = FastAPI(title="BRACE isolated project runner", lifespan=lifespan)


@app.middleware("http")
async def bound_request(request, call_next):
    if request.url.path != "/health":
        try:
            authorize(request)
        except HTTPException as exc:
            return Response(exc.detail, status_code=exc.status_code)
    length = request.headers.get("content-length")
    if length is None and request.method == "POST":
        return Response("Content-Length required", status_code=411)
    if length and (not length.isdigit() or int(length) > 90 * 1024 * 1024):
        return Response("Request exceeds runner limit", status_code=413)
    return await call_next(request)


jobs = {}
slots = None
ROOT = Path(os.getenv("BRACE_RUNNER_WORK_DIR", tempfile.gettempdir())) / "brace-worker"


def authorize(request: Request):
    token = os.getenv("BRACE_RUNNER_TOKEN", "")
    if len(token) < 32 or not hmac.compare_digest(
        request.headers.get("authorization", "").encode(), ("Bearer " + token).encode()
    ):
        raise HTTPException(401, "Invalid runner credential")


class JobInput(BaseModel):
    project_id: int
    sources: str = Field(max_length=90 * 1024 * 1024)
    arguments: list[str]
    environment: dict[str, str] = Field(default_factory=dict)
    timeout: int = Field(ge=1, le=86400)


async def execute(job, req):
    global slots
    if slots is None:
        slots = asyncio.Semaphore(max(1, int(os.getenv("BRACE_RUNNER_CAPACITY", "3"))))
    async with slots:
        if job["status"] == "cancelled":
            return
        job["status"] = "running"
        source, results = job["dir"] / "sources", job["dir"] / "results"
        args = [
            arg.replace("__SOURCES__", str(source))
            .replace("__RESULTS__", str(results))
            .replace("__PROFILE__", str(Path(__file__).parent / "profile_variables.py"))
            .replace(
                "__LISTENER__",
                str(Path(__file__).parent / "rf_listener" / "brace_capture.py"),
            )
            for arg in req.arguments
        ]
        environment = runner_environment(
            {
                "environment": {
                    key: value
                    for key, value in req.environment.items()
                    if key != "BRACE_PROFILE_VARIABLES"
                }
            }
        )
        environment["BRACE_PROFILE_VARIABLES"] = req.environment.get(
            "BRACE_PROFILE_VARIABLES", "{}"
        )
        try:
            with (results / "console.log").open("w", encoding="utf-8") as console:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "robot",
                    *args,
                    cwd=source,
                    env=environment,
                    stdout=console,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=os.name != "nt",
                )
                job["proc"] = proc
                try:
                    code = await asyncio.wait_for(proc.wait(), req.timeout)
                except asyncio.TimeoutError:
                    await terminate_tree(proc)
                    code = -1
            job["exit_code"] = code
            if job["status"] != "cancelled":
                job["status"] = "passed" if code == 0 else "failed"
        except Exception as exc:
            (results / "console.log").write_text(
                f"Worker execution failed: {exc}", encoding="utf-8"
            )
            job.update(status="failed", exit_code=-1)
        finally:
            job.pop("proc", None)
            job["finished"] = time.monotonic()


@app.post("/jobs", dependencies=[Depends(authorize)])
async def start(req: JobInput):
    expected = int(os.getenv("BRACE_RUNNER_PROJECT_ID", "0"))
    if expected <= 0 or req.project_id != expected:
        raise HTTPException(403, "Worker is dedicated to another project")
    for key, job in list(jobs.items()):
        if job.get("finished") and time.monotonic() - job["finished"] > 3600:
            await asyncio.to_thread(shutil.rmtree, job["dir"])
            jobs.pop(key, None)
    if len(jobs) >= 20:
        raise HTTPException(429, "Worker job limit reached")
    validate_values(
        {
            key: value
            for key, value in req.environment.items()
            if key != "BRACE_PROFILE_VARIABLES"
        },
        environment=True,
    )
    key = uuid.uuid4().hex
    directory = ROOT / key
    (directory / "results").mkdir(parents=True)
    try:
        data = base64.b64decode(req.sources, validate=True)
        await asyncio.to_thread(unpack_archive, data, directory / "sources")
    except Exception as exc:
        await asyncio.to_thread(shutil.rmtree, directory)
        raise HTTPException(400, f"Invalid source archive: {exc}")
    job = {"id": key, "dir": directory, "status": "queued", "exit_code": -1}
    jobs[key] = job
    job["task"] = asyncio.create_task(execute(job, req))
    return {"id": key}


def get_job(key):
    if key not in jobs:
        raise HTTPException(404, "Worker job not found")
    return jobs[key]


@app.get("/jobs/{key}", dependencies=[Depends(authorize)])
def status(key: str):
    job = get_job(key)
    return {name: job[name] for name in ("id", "status", "exit_code")}


@app.post("/jobs/{key}/cancel", dependencies=[Depends(authorize)])
async def cancel(key: str):
    job = get_job(key)
    job["status"] = "cancelled"
    if proc := job.get("proc"):
        await terminate_tree(proc)
    else:
        job["task"].cancel()
        await asyncio.gather(job["task"], return_exceptions=True)
        job["finished"] = time.monotonic()
    await asyncio.gather(job["task"], return_exceptions=True)
    return {"ok": True}


@app.get("/jobs/{key}/results", dependencies=[Depends(authorize)])
async def results(key: str):
    job = get_job(key)
    if not job["task"].done():
        raise HTTPException(409, "Job still active")
    return Response(
        await asyncio.to_thread(pack_directory, job["dir"] / "results"),
        media_type="application/zip",
    )


@app.delete("/jobs/{key}", dependencies=[Depends(authorize)])
async def remove(key: str):
    job = get_job(key)
    if not job["task"].done():
        raise HTTPException(409, "Job still active")
    await asyncio.to_thread(shutil.rmtree, job["dir"])
    jobs.pop(key, None)
    return {"ok": True}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "project_id": int(os.getenv("BRACE_RUNNER_PROJECT_ID", "0")),
    }
