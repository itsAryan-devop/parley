"""Virtual-clock event loop — deterministic replay, at full speed.

The guide's harness is a "Virtual Clock Streaming Harness: deterministic event
replay, asynchronous mock tool responses, and complete event/action trace
logging". Latency is 15% of the score and is measured from the trace, so the
timeline has to be *exact*, not approximately right — and a 120 s wall-clock cap
per scenario means we cannot afford to actually sleep through a 900 ms tool call.

Approach: rather than detect loop quiescence by spinning `asyncio.sleep(0)` a
guessed number of times (fragile, and silently wrong when a coroutine chain is
deeper than the guess), we subclass the event loop and override its clock.

`asyncio`'s `_run_once` already computes how long to wait for the next scheduled
callback and then blocks. We intercept the moment before it blocks: if nothing is
runnable but something is scheduled, we *jump* virtual time to that callback's
deadline, so the selector timeout collapses to zero and the callback fires
immediately. Time only ever advances when the system is genuinely idle.

Two consequences worth stating, because they are what makes the rest of the
codebase simple:

1. Plain `asyncio.sleep`, `asyncio.wait_for`, `asyncio.timeout` and
   `loop.call_later` are all virtualised for free. Agent code needs no special
   clock-aware API, so the same agent runs against the real kit unmodified.
2. Runs are bit-identical across machines. An interruption fired at t=310 ms
   lands between exactly the same two awaits every time, which is what makes
   adversarial-timing tests reproducible instead of flaky.

`_ready` and `_scheduled` are private, but they have been stable since CPython
3.4 and the guide pins us to 3.10-3.12. `VirtualTimeLoop.probe()` asserts their
presence at construction so a future break is a loud error, not silent real-time
execution.
"""

from __future__ import annotations

import asyncio
import selectors
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")

MS = 1e-3
"""asyncio works in seconds; the protocol and traces work in milliseconds."""


class VirtualClockError(RuntimeError):
    pass


class Deadlock(VirtualClockError):
    """No task is runnable and no timer is scheduled: time cannot advance.

    In a replay harness this always means a real bug — a coroutine awaiting a
    queue nobody will ever feed — so it is raised rather than hung on.
    """


class _SelectorStub(selectors.BaseSelector):
    """A selector that does the bookkeeping but never reports readiness.

    The harness performs zero I/O: every wakeup comes from a timer. Registration
    still has to work, because `BaseSelectorEventLoop` unconditionally installs a
    self-pipe reader for `call_soon_threadsafe` — which we never use, so that fd
    never becomes ready and reporting nothing is the truthful answer.

    Stubbing `select()` to return immediately also keeps `select()` syscalls out
    of the hot loop and sidesteps the Windows quirk where an empty fd set raises
    WinError 10022.
    """

    def __init__(self) -> None:
        self._fds: dict[int, selectors.SelectorKey] = {}

    def register(self, fileobj: Any, events: int, data: Any = None) -> selectors.SelectorKey:
        fd = fileobj if isinstance(fileobj, int) else fileobj.fileno()
        key = selectors.SelectorKey(fileobj, fd, events, data)
        self._fds[fd] = key
        return key

    def unregister(self, fileobj: Any) -> selectors.SelectorKey:
        fd = fileobj if isinstance(fileobj, int) else fileobj.fileno()
        return self._fds.pop(fd)

    def modify(self, fileobj: Any, events: int, data: Any = None) -> selectors.SelectorKey:
        return self.register(fileobj, events, data)

    def select(self, timeout: float | None = None) -> list[Any]:
        # Nothing is ever ready: the only wakeups in this harness are timers.
        return []

    def get_map(self) -> dict[int, selectors.SelectorKey]:
        return self._fds

    def get_key(self, fileobj: Any) -> selectors.SelectorKey:
        fd = fileobj if isinstance(fileobj, int) else fileobj.fileno()
        return self._fds[fd]

    def close(self) -> None:
        self._fds.clear()


class VirtualTimeLoop(asyncio.SelectorEventLoop):
    """An event loop whose clock is a variable we control."""

    def __init__(self) -> None:
        super().__init__(selector=_SelectorStub())
        self._vtime: float = 0.0
        self._advances: int = 0

        # asyncio fires every timer within `_clock_resolution` of the current
        # instant in one batch, because on a real clock it cannot tell them
        # apart. On Windows that resolution is ~15.6 ms, which would silently
        # collapse events 5 ms apart into the same tick — and 5 ms apart is
        # precisely the "adversarial timing" the hidden set is built from.
        # Virtual time is exact, so the resolution is too. It must stay strictly
        # positive: `_run_once` breaks on `_when >= now + resolution`, so a zero
        # would starve timers scheduled for exactly now.
        self._clock_resolution = 1e-9  # 1 ns == 1e-6 ms

        self.probe()

    # -- the clock ---------------------------------------------------------

    def time(self) -> float:
        """Seconds, per the asyncio contract. Never wall-clock."""
        return self._vtime

    @property
    def now_ms(self) -> float:
        return self._vtime / MS

    @property
    def advances(self) -> int:
        """How many times time jumped. Useful for asserting a test really idled."""
        return self._advances

    # -- the one override that matters -------------------------------------

    def _run_once(self) -> None:
        if not self._ready and self._scheduled:
            # Everything is idle and a timer is due: jump to it.
            deadline = self._scheduled[0]._when
            if deadline > self._vtime:
                self._vtime = deadline
                self._advances += 1
        elif not self._ready and not self._scheduled and not self._stopping:
            raise Deadlock(
                f"virtual time is stuck at {self.now_ms:.1f} ms: "
                "nothing runnable and nothing scheduled"
            )
        super()._run_once()

    # -- guardrail ---------------------------------------------------------

    def probe(self) -> None:
        """Fail loudly if the loop internals we rely on ever move."""
        for attr in ("_ready", "_scheduled", "_stopping", "_clock_resolution"):
            if not hasattr(self, attr):
                raise VirtualClockError(
                    f"asyncio loop is missing {attr!r}; VirtualTimeLoop needs porting "
                    "for this Python version"
                )


class Clock:
    """Millisecond-facing view of the running virtual loop.

    Everything in the codebase that needs a timestamp goes through this, so
    there is exactly one definition of "now" and it is never `time.time()`.
    """

    __slots__ = ("_loop",)

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_event_loop()

    @property
    def now(self) -> float:
        """Milliseconds since session start."""
        return self._loop.time() / MS

    async def sleep(self, ms: float) -> None:
        await asyncio.sleep(ms * MS)

    def call_at(self, ms: float, fn: Callable[..., Any], *args: Any) -> asyncio.TimerHandle:
        return self._loop.call_at(ms * MS, fn, *args)

    def call_after(self, ms: float, fn: Callable[..., Any], *args: Any) -> asyncio.TimerHandle:
        return self._loop.call_later(ms * MS, fn, *args)

    def deadline(self, ms: float) -> Any:
        """`async with clock.deadline(50): ...` — a virtual-time timeout."""
        return asyncio.timeout(self._loop.time() + ms * MS)


def run_virtual(main: Awaitable[T]) -> T:
    """Run `main` to completion on a fresh virtual-time loop.

    Mirrors `asyncio.run`: new loop, cancel stragglers, close cleanly.
    """
    loop = VirtualTimeLoop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(main)
    finally:
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()
