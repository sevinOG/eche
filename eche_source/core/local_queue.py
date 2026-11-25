# core/local_queue.py
# One queue for Eche's local Ollama calls (the GPU is shared).
#
# A slim, in-process take on Waerhouse's GpuScheduler, for Eche only:
#   * one asyncio worker runs local model jobs one at a time, oldest first;
#   * before a job, /api/ps says what is loaded. Models THIS queue ran are
#     evicted least-recently-used until the new one fits the VRAM budget
#     (unknown size = does not fit), each unload is keep_alive 0 and is checked
#     gone in /api/ps. Models other programs loaded (Waerhouse, Eche's own
#     child-safety checker, which stays outside this queue) are counted, never
#     unloaded;
#   * a later job for a model that is already loaded may go first, at most
#     MAX_BYPASS times / BATCH_WAIT_S for the job it passes, so nothing starves;
#   * every wait is bounded: QUEUE_TIMEOUT_S in line, the job's run limit once
#     started, UNLOAD_TIMEOUT_S per unload. Nobody waits forever;
#   * a "gpu_queue" GUI event and short [gpu] log lines say what it is doing.
#
# Remote API calls never come here: callers only queue a call whose URL is this
# PC. If the queue itself breaks (a bug, another event loop, an unload that
# never finishes) the job runs directly, as it did before this queue; a full
# queue or a job past its run limit fails clearly instead (QueueBusy /
# QueueTimeout). An exception from the job itself is passed through unchanged.
#
# Whisper runs on the CPU and is not queued here (core/watch_listen keeps it to
# one transcription at a time).
#
# Later, not now: sharing the card with Waerhouse properly needs a cross-process
# lock both apps take around a local model call (e.g. a lock file or named
# mutex beside both data folders, holding pid + model + since), plus agreeing
# not to unload each other's models while that lock is held. Today each app
# only knows its own jobs.
#
# Settings (env): ECHE_LOCAL_QUEUE=off, ECHE_GPU_VRAM_GB (24), ECHE_GPU_RESERVE_GB (4).

from __future__ import annotations

import asyncio
import collections
import itertools
import os
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from core.debuglog import dprint

GB = 1 << 30
DEFAULT_VRAM_GB = 24.0
DEFAULT_RESERVE_GB = 4.0
KV_OVERHEAD = 1.12            # loaded = weights plus a context cache
QUEUE_TIMEOUT_S = 240.0
RUN_TIMEOUT_S = 300.0
UNLOAD_TIMEOUT_S = 30.0
UNLOAD_POLL_S = 0.5
PS_TIMEOUT_S = 2.0
# Hard caps on each bookkeeping call, enforced with asyncio.wait_for on top of aiohttp's own
# timeout (live 2026-11-25 16:04 ET: the loop froze inside /api/ps). A slow or failed call
# skips the budget work and the job runs anyway.
PS_CALL_TIMEOUT_S = 3.0
UNLOAD_CALL_TIMEOUT_S = 12.0
PREPARE_TIMEOUT_S = 75.0
MAX_BYPASS = 3
BATCH_WAIT_S = 30.0
MAX_QUEUED = 32
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class QueueBusy(RuntimeError):
    """Waited too long in line. The call fails clearly."""


class QueueTimeout(RuntimeError):
    """The job ran past its limit. The call fails clearly."""


class _QueueBroken(RuntimeError):
    """The queue machinery failed. The caller runs the job directly."""


# --- sizes and the budget (pure) ---------------------------------------------------------------

def norm(name: str) -> str:
    text = str(name or "").strip().lower()
    return text[: -len(":latest")] if text.endswith(":latest") else text


def guess_bytes(name: str) -> int | None:
    """~Q4 weights from a size in the name ('…27B…' -> ~16.7 GB). None when the name has none."""
    hit = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)\s*b(?![a-z])", str(name or "").lower())
    if not hit:
        return None
    params = float(hit.group(1))
    if params <= 0 or params > 400:
        return None
    return int(params * 0.62 * GB)


def footprint(weights: int | None) -> int | None:
    if not weights or weights <= 0:
        return None
    return int(weights * KV_OVERHEAD)


def _env_gb(key: str, default: float) -> float:
    try:
        value = float(os.environ.get(key) or default)
    except ValueError:
        value = default
    return max(0.0, value)


@dataclass
class Budget:
    total: int
    reserve: int

    @classmethod
    def from_env(cls) -> "Budget":
        total = max(1.0, _env_gb("ECHE_GPU_VRAM_GB", DEFAULT_VRAM_GB))
        reserve = min(_env_gb("ECHE_GPU_RESERVE_GB", DEFAULT_RESERVE_GB), total - 1.0)
        return cls(int(total * GB), int(reserve * GB))

    @property
    def usable(self) -> int:
        return max(0, self.total - self.reserve)

    def plan(self, fixed: list[int | None], owned: list[tuple[str, int | None]], new: int | None) -> list[str]:
        """Owned models to unload (least recently used first) so `new` fits beside `fixed` (other programs'
        models, never unloaded) and the owned ones kept. `owned` is most recent first. Unknown sizes never fit:
        an unknown new model or an unknown fixed model means every owned model goes."""
        if new is None or any(size is None for size in fixed):
            return [key for key, _size in reversed(owned)]
        used = int(new) + sum(int(size) for size in fixed)
        keep_room = self.usable - used
        drop: list[str] = []
        for key, size in owned:  # most recent first get the room first
            if size is not None and size <= keep_room:
                keep_room -= size
            else:
                drop.append(key)
        return list(reversed(drop))  # least recently used first


# --- the queue --------------------------------------------------------------------------------

@dataclass
class _Job:
    seq: int
    model: str
    base: str
    fn: Callable[[], Awaitable]
    label: str
    run_timeout: float
    enqueued: float
    bypassed: int = 0
    started: asyncio.Event = field(default_factory=asyncio.Event)
    future: asyncio.Future | None = None
    cancelled: bool = False


def is_local(url: str) -> bool:
    try:
        return (urlsplit(str(url or "")).hostname or "").lower() in _LOOPBACK
    except ValueError:
        return False


def ip_literal(host: str) -> str:
    """'localhost' -> '127.0.0.1'. aiohttp resolves names through loop.getaddrinfo, which needs
    a thread from the default executor; an IP literal skips resolution entirely."""
    host = str(host or "").strip().lower()
    return "127.0.0.1" if host in ("", "localhost") else host


def base_of(url: str) -> str:
    parts = urlsplit(str(url or ""))
    host = ip_literal(parts.hostname or "")
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme or 'http'}://{host}:{parts.port or 11434}"


class LocalQueue:
    def __init__(self):
        self._queue: list[_Job] = []
        self._current: _Job | None = None
        self._seq = itertools.count(1)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._worker: asyncio.Task | None = None
        self.owned: "collections.OrderedDict[str, float]" = collections.OrderedDict()  # model -> last use
        self.sizes: dict[str, int] = {}       # model -> bytes /api/ps showed while it was loaded
        self.resident: set[str] = set()       # last /api/ps view
        self.budget = Budget.from_env()
        self.queue_timeout = QUEUE_TIMEOUT_S
        self.unload_timeout = UNLOAD_TIMEOUT_S
        self.unload_poll = UNLOAD_POLL_S
        self.max_bypass = MAX_BYPASS
        self.batch_wait = BATCH_WAIT_S
        self.listeners: list[Callable[[dict], None]] = []
        # I/O seams (tests replace these): base -> [(name, size_vram|None)] or None; base,name -> None;
        # base,name -> weight bytes or None.
        self.ps = _http_ps
        self.unload = _http_unload
        self.tag_size = _http_tag_size

    # -- public --------------------------------------------------------------------------------

    async def run(self, model: str, fn: Callable[[], Awaitable], *, base: str, label: str = "chat",
                  run_timeout: float = RUN_TIMEOUT_S):
        """Run `fn()` (a coroutine factory) when the GPU is free for `model`.

        QueueBusy / QueueTimeout fail clearly; a broken queue runs `fn()` directly; fn's own errors pass through."""
        try:
            job = self._enqueue(model, fn, base, label, run_timeout)
        except _QueueBroken as exc:
            dprint(f"[gpu] queue unavailable ({exc}); running {label} directly")
            return await fn()
        try:
            await asyncio.wait_for(job.started.wait(), self.queue_timeout)
        except asyncio.TimeoutError:
            job.cancelled = True
            self._drop(job)
            self._log(f"{label} for {_short(model)} waited {self.queue_timeout:.0f}s in line; gave up")
            raise QueueBusy("local model queue is busy") from None
        except asyncio.CancelledError:
            job.cancelled = True
            self._drop(job)
            raise
        # Started: the worker enforces the run limit; this is only a backstop so nobody hangs.
        backstop = job.run_timeout + self.unload_timeout * 4 + 10
        try:
            outcome = await asyncio.wait_for(asyncio.shield(job.future), backstop)
        except asyncio.TimeoutError:
            raise QueueTimeout(f"{label} ran past its limit") from None
        kind, value = outcome
        if kind == "ok":
            return value
        if kind == "job":
            raise value
        if kind == "timeout":
            raise QueueTimeout(f"{label} ran past {job.run_timeout:.0f}s") from None
        dprint(f"[gpu] queue failed before {label} ({value}); running it directly")
        return await fn()

    def status(self) -> dict:
        cur = self._current
        return {
            "running": {"model": _short(cur.model), "label": cur.label} if cur else None,
            "waiting": len([j for j in self._queue if not j.cancelled]),
            "loaded": sorted(_short(m) for m in self.resident),
        }

    # -- internals -----------------------------------------------------------------------------

    def _enqueue(self, model, fn, base, label, run_timeout) -> _Job:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise _QueueBroken("no event loop") from exc
        if self._loop is None or self._loop.is_closed():
            self._loop, self._wake, self._worker = loop, asyncio.Event(), None
            self._queue, self._current = [], None
        elif self._loop is not loop:
            raise _QueueBroken("another event loop")
        if len(self._queue) >= MAX_QUEUED:
            raise QueueBusy("local model queue is full")
        job = _Job(next(self._seq), norm(model), base, fn, label, float(run_timeout), time.monotonic())
        job.future = loop.create_future()
        self._queue.append(job)
        if self._worker is None or self._worker.done():
            self._worker = loop.create_task(self._work(), name="eche-local-queue")
        self._wake.set()
        if self._current is not None:
            self._log(f"{label} for {_short(job.model)} waiting behind {self._current.label}")
        return job

    def _drop(self, job: _Job) -> None:
        try:
            self._queue.remove(job)
        except ValueError:
            pass
        self._emit()

    def _take(self) -> _Job | None:
        jobs = [j for j in self._queue if not j.cancelled]
        self._queue = jobs
        if not jobs:
            return None
        head = jobs[0]
        chosen = head
        if (head.model not in self.resident and head.bypassed < self.max_bypass
                and time.monotonic() - head.enqueued < self.batch_wait):
            for job in jobs[1:]:
                if job.model in self.resident:
                    head.bypassed += 1
                    chosen = job
                    break
        self._queue.remove(chosen)
        return chosen

    async def _work(self) -> None:
        while True:
            job = self._take()
            if job is None:
                self._wake.clear()
                self._current = None
                self._emit()
                try:
                    await asyncio.wait_for(self._wake.wait(), 60)
                except asyncio.TimeoutError:
                    if not self._queue:
                        self._worker = None
                        return
                continue
            self._current = job
            job.started.set()
            self._emit()
            try:
                await asyncio.wait_for(self._prepare(job), PREPARE_TIMEOUT_S)
            except asyncio.TimeoutError:
                self._log(f"getting the GPU ready for {job.label} took over {PREPARE_TIMEOUT_S:.0f}s; running it directly")
                _settle(job, ("broken", "prepare timeout"))
                continue
            except Exception as exc:
                # The job still runs: directly, by its caller, outside the queue's bookkeeping.
                _settle(job, ("broken", type(exc).__name__))
                continue
            try:
                value = await asyncio.wait_for(job.fn(), job.run_timeout)
                _settle(job, ("ok", value))
            except asyncio.TimeoutError:
                self._log(f"{job.label} on {_short(job.model)} passed {job.run_timeout:.0f}s; stopped waiting")
                _settle(job, ("timeout", None))
            except asyncio.CancelledError:
                _settle(job, ("timeout", None))
                raise
            except Exception as exc:
                _settle(job, ("job", exc))
            self.owned[job.model] = time.monotonic()
            self.owned.move_to_end(job.model)
            self.resident.add(job.model)

    async def _prepare(self, job: _Job) -> None:
        """Make room for job.model. /api/ps unreadable or slow: run anyway (Ollama decides, as before)."""
        listed = await self._ps(job.base)
        if listed is None:
            return
        self.resident = {norm(n) for n, _s in listed}
        for name, size in listed:
            if size:
                self.sizes[norm(name)] = int(size)
        for name in list(self.owned):
            if name not in self.resident:
                self.owned.pop(name, None)  # Ollama let it go on its own (keep_alive ran out)
        if job.model in self.resident:
            return
        new = await self._size(job.base, job.model)
        owned = [(m, self.sizes.get(m)) for m in reversed(self.owned) if m in self.resident]
        fixed = [self.sizes.get(norm(n)) for n, _s in listed if norm(n) not in self.owned]
        for name in self.budget.plan(fixed, owned, new):
            await self._unload(job.base, name)

    async def _size(self, base: str, model: str) -> int | None:
        if model in self.sizes:
            return self.sizes[model]
        try:
            weights = await asyncio.wait_for(self.tag_size(base, model), PS_CALL_TIMEOUT_S)
        except Exception:  # includes asyncio.TimeoutError
            weights = None
        return footprint(weights) or footprint(guess_bytes(model))

    async def _unload(self, base: str, name: str) -> None:
        """keep_alive 0, then /api/ps until it is gone. Not gone in time: the queue is broken for this job."""
        self._log(f"unloading {_short(name)} to make room")
        try:
            await asyncio.wait_for(self.unload(base, name), UNLOAD_CALL_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise _QueueBroken("unload call timed out") from None
        deadline = time.monotonic() + self.unload_timeout
        while True:
            listed = await self._ps(base)
            if listed is not None and name not in {norm(n) for n, _s in listed}:
                self.owned.pop(name, None)
                self.resident.discard(name)
                return
            if time.monotonic() >= deadline:
                self._log(f"{_short(name)} did not leave the GPU within {self.unload_timeout:.0f}s")
                raise _QueueBroken("unload not verified")
            await asyncio.sleep(self.unload_poll)

    async def _ps(self, base: str):
        """/api/ps with a hard cap. None (unknown) when it is slow or fails."""
        try:
            return await asyncio.wait_for(self.ps(base), PS_CALL_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._log(f"/api/ps took over {PS_CALL_TIMEOUT_S:.0f}s; skipping the VRAM check")
            return None
        except Exception:
            return None

    def _emit(self) -> None:
        status = self.status()
        for fn in list(self.listeners):
            try:
                fn(status)
            except Exception:
                pass
        try:
            from core.gui_bridge import emit

            emit("gpu_queue", status)
        except Exception:
            pass

    def _log(self, line: str) -> None:
        dprint(f"[gpu] {line}")
        try:
            from core.gui_bridge import log

            log(line, channel="gpu")
        except Exception:
            pass


def _settle(job: _Job, outcome: tuple) -> None:
    if job.future is not None and not job.future.done():
        job.future.set_result(outcome)


def _short(model: str) -> str:
    return norm(model).rsplit("/", 1)[-1][:60]


# --- Ollama HTTP (local only) -------------------------------------------------------------------

def _local_base(base: str) -> str:
    """Always an IP literal: no DNS, so no executor thread, for this PC's Ollama."""
    return base_of(base)


async def _http_ps(base: str):
    import aiohttp

    base = _local_base(base)

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=PS_TIMEOUT_S)) as session:
            async with session.get(base + "/api/ps") as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
    except Exception:
        return None
    return [(str(m.get("name") or m.get("model") or ""), m.get("size_vram") or m.get("size"))
            for m in (data or {}).get("models") or [] if isinstance(m, dict)]


async def _http_unload(base: str, name: str) -> None:
    import aiohttp

    base = _local_base(base)

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(base + "/api/chat", json={"model": name, "messages": [], "keep_alive": 0}):
                pass
    except Exception:
        pass


async def _http_tag_size(base: str, model: str) -> int | None:
    import aiohttp

    base = _local_base(base)

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=PS_TIMEOUT_S)) as session:
        async with session.get(base + "/api/tags") as resp:
            data = await resp.json(content_type=None)
    for item in (data or {}).get("models") or []:
        if norm(item.get("name") or item.get("model") or "") == model:
            return int(item.get("size") or 0) or None
    return None


# --- the one queue ------------------------------------------------------------------------------

_QUEUE: LocalQueue | None = None


def get_queue() -> LocalQueue:
    global _QUEUE
    if _QUEUE is None:
        _QUEUE = LocalQueue()
    return _QUEUE


def reset_queue() -> None:
    global _QUEUE
    _QUEUE = None


def enabled() -> bool:
    return (os.environ.get("ECHE_LOCAL_QUEUE") or "").strip().lower() not in ("0", "off", "false", "no")


async def run_local(url: str, model: str, fn: Callable[[], Awaitable], *, label: str = "chat",
                    run_timeout: float = RUN_TIMEOUT_S):
    """Queue `fn` when `url` is this PC's Ollama; anything else runs directly (remote APIs never queue)."""
    if not enabled() or not is_local(url):
        return await fn()
    return await get_queue().run(model, fn, base=base_of(url), label=label, run_timeout=run_timeout)
