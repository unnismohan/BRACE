"""Bounded scheduling and process-tree cleanup for Robot workers."""
import asyncio
import os
import signal


async def map_bounded(items, worker, width):
    """Preserve submission order with at most width live worker tasks."""
    iterator = iter(enumerate(items))
    results = [None] * len(items)

    async def consume():
        for index, item in iterator:
            try:
                results[index] = await worker(item)
            except Exception as exc:
                results[index] = exc

    await asyncio.gather(*(consume() for _ in range(min(width, len(items)))))
    return results


async def terminate_tree(proc, grace=15):
    """Robot is started in its own POSIX session, including its browsers."""
    if os.name == "nt":
        if proc.returncode is None:
            cleanup = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(proc.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            code = await cleanup.wait()
            if code and proc.returncode is None:
                proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), grace)
        except asyncio.TimeoutError:
            pass
        # Descendants may outlive Robot even when Robot exited promptly.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await asyncio.wait_for(proc.wait(), grace)
