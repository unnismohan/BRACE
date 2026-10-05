"""Bounded scheduling and process-tree cleanup for Robot workers."""

import asyncio
import os
import signal
from collections import defaultdict, deque
from contextlib import asynccontextmanager


class ProjectFairGate:
    """Admit runs by project share, then least-recently-served project."""

    def __init__(self, capacity):
        self.capacity = capacity
        self.pending = defaultdict(deque)
        self.active = defaultdict(int)
        self.served = defaultdict(int)
        self.tick = 0

    def _pump(self):
        for project in list(self.pending):
            self.pending[project] = deque(
                (key, future)
                for key, future in self.pending[project]
                if not future.done()
            )
            if not self.pending[project]:
                del self.pending[project]
        while self.pending and sum(self.active.values()) < self.capacity:
            project = min(
                self.pending, key=lambda value: (self.active[value], self.served[value])
            )
            _, future = self.pending[project].popleft()
            if not self.pending[project]:
                del self.pending[project]
            self.active[project] += 1
            self.tick += 1
            self.served[project] = self.tick
            future.set_result(True)

    def cancel(self, run_id):
        for entries in self.pending.values():
            for key, future in entries:
                if key == run_id and not future.done():
                    future.cancel()
        self._pump()

    @asynccontextmanager
    async def slot(self, project_id, run_id):
        future = asyncio.get_running_loop().create_future()
        self.pending[project_id].append((run_id, future))
        self._pump()
        acquired = False
        try:
            await future
            acquired = True
            yield
        finally:
            if acquired:
                self.active[project_id] -= 1
                if not self.active[project_id]:
                    del self.active[project_id]
            elif future.done() and not future.cancelled():
                # Cancellation raced with admission before the waiter resumed.
                self.active[project_id] -= 1
            future.cancel()
            self._pump()


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
    if hasattr(proc, "cancel"):
        await proc.cancel()
        return
    if os.name == "nt":
        if proc.returncode is None:
            cleanup = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(proc.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
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
