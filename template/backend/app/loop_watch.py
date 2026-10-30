"""A held event loop, reported in every deployment.

A tick on the event loop every `TICK_SECONDS` hands how late it ran to the recorder it was given.
When the loop's thread used more than `REPORTED_CPU_SECONDS` of CPU between two ticks,
`log.loop_held` names that CPU time, how late the tick ran, and where the loop was: the innermost
frame of this project's own code, or the innermost frame when none is. A thread beside the loop
looks every `LOOK_SECONDS` once a tick is overdue by `TICK_SECONDS`. A hold it did not see, and
a hold in code that keeps the interpreter lock, names no frame.

A step that waits uses no CPU and is not reported, as in `tests/event_loop.py`. A process that its
platform throttles between requests runs the tick late while nothing holds the loop: the delay
records that, and the CPU time does not.
"""

import asyncio
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import FrameType, TracebackType
from typing import Final, Self, final

from app import log

TICK_SECONDS: Final = 0.1
"""How often the tick runs, and how often the thread beside the loop looks at it."""

REPORTED_CPU_SECONDS: Final = 0.25
"""The CPU time the loop's thread may use between two ticks before `log.loop_held` names it."""

LOOK_SECONDS: Final = TICK_SECONDS / 4
"""How often the thread beside the loop looks whether a tick is overdue."""

OWN_CODE: Final = f"{Path(__file__).resolve().parents[1]}{os.sep}"
"""The folder of this project's own code. A frame of a file in it, outside an installed package,
is where a held loop is named."""


@final
class LoopWatch:
    """Watches the running event loop from `__enter__` to `__exit__`. Enter it on that loop.

    `record` receives how late each tick ran, in seconds, on the loop; `None` records nothing.
    """

    def __init__(self, record: Callable[[float], None] | None) -> None:
        self._record = record
        self._loop = asyncio.get_running_loop()
        self._thread = threading.get_ident()
        self._due = 0.0
        self._cpu = 0.0
        self._seen: tuple[float, str, int] | None = None
        self._tick: asyncio.TimerHandle | None = None
        self._stopped = threading.Event()
        self._beside = threading.Thread(target=self._look, name="loop-watch", daemon=True)

    def __enter__(self) -> Self:
        self._next(time.monotonic(), time.thread_time())
        self._beside.start()
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self._stopped.set()
        if self._tick is not None:
            self._tick.cancel()
        self._beside.join()

    def _next(self, now: float, cpu: float) -> None:
        self._due, self._cpu = now + TICK_SECONDS, cpu
        self._tick = self._loop.call_later(TICK_SECONDS, self._ticked)

    def _ticked(self) -> None:
        now, cpu = time.monotonic(), time.thread_time()
        late = max(0.0, now - self._due)
        seen = self._seen
        if self._record is not None:
            self._record(late)
        held = cpu - self._cpu
        if held > REPORTED_CPU_SECONDS:
            function, line = seen[1:] if seen is not None and seen[0] == self._due else (None, None)
            log.loop_held(held, late, function, line)
        self._next(now, cpu)

    def _look(self) -> None:
        while not self._stopped.wait(LOOK_SECONDS):
            due = self._due
            seen = self._seen
            if time.monotonic() - due > TICK_SECONDS and (seen is None or seen[0] != due):
                frame = _own(sys._current_frames()[self._thread])
                self._seen = (due, _qualified(frame), frame.f_lineno)


def _is_own(frame: FrameType) -> bool:
    file = frame.f_code.co_filename
    return file.startswith(OWN_CODE) and "site-packages" not in file


def _own(innermost: FrameType) -> FrameType:
    frame: FrameType | None = innermost
    while frame is not None:
        if _is_own(frame):
            return frame
        frame = frame.f_back
    return innermost


def _qualified(frame: FrameType) -> str:
    module = frame.f_globals.get("__name__")
    named = frame.f_code.co_qualname
    return named if module is None else f"{module}.{named}"
