# Copyright 2026 InstaDeep Ltd. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Stall detection for a streaming chat run.

Owns the timing state and the thread; reports transitions through callbacks so
the caller keeps all the wx.
"""

import threading
import time
import traceback
from typing import Callable

STALL_WARNING_SECONDS = 20
STALL_ERROR_SECONDS = 45
WATCHDOG_INTERVAL_SECONDS = 2.0

# Must stay below ChatClient.STREAM_READ_TIMEOUT so this watchdog, not the
# socket, is what ends a stuck stream.
MAX_STREAM_DURATION_SECONDS = 300


def _never_cancelled() -> bool:
    return False


class StreamWatchdog:
    """Watches one run at a time for silence and for overrunning its limit.

    start() supersedes the previous run, so the watchdog of a run that has been
    replaced (a resume, or a new turn) can no longer report anything.
    """

    def __init__(
        self,
        on_slow: Callable[[], None],
        on_stall: Callable[[float, bool], None],
        ui_sink: Callable[..., None],
        is_cancelled: Callable[[], bool] = _never_cancelled,
        warning_seconds: float = STALL_WARNING_SECONDS,
        error_seconds: float = STALL_ERROR_SECONDS,
        max_duration_seconds: float = MAX_STREAM_DURATION_SECONDS,
        interval_seconds: float = WATCHDOG_INTERVAL_SECONDS,
    ):
        self._on_slow = on_slow
        self._on_stall = on_stall
        self._ui_sink = ui_sink
        self._is_cancelled = is_cancelled
        self._warning_seconds = warning_seconds
        self._error_seconds = error_seconds
        self._max_duration_seconds = max_duration_seconds
        self._interval_seconds = interval_seconds

        self._generation = 0
        self._stop_event = threading.Event()
        self._thread = None
        self._kill_timer = None
        self._last_event_time = 0.0
        self._started_at = 0.0
        self._warned = False
        # The loop and the kill timer both notice an overrun, from different
        # threads, so the report has to be claimed exactly once.
        self._report_lock = threading.Lock()
        self._reported = False

    def start(self) -> None:
        self._stop_current()

        self._generation += 1
        generation = self._generation
        now = time.time()
        self._last_event_time = now
        self._started_at = now
        self._warned = False
        with self._report_lock:
            self._reported = False

        stop_event = threading.Event()
        self._stop_event = stop_event
        self._thread = threading.Thread(
            target=self._loop, args=(generation, stop_event), daemon=True
        )
        self._thread.start()

        kill_timer = threading.Timer(
            self._max_duration_seconds, self._on_kill_timer, args=(generation,)
        )
        kill_timer.daemon = True
        self._kill_timer = kill_timer
        kill_timer.start()

    def stop(self) -> None:
        self._stop_current()

    def mark_event(self) -> None:
        """Record a sign of life, resetting the silence clock."""
        self._last_event_time = time.time()
        self._warned = False

    def _stop_current(self) -> None:
        self._stop_event.set()
        self._thread = None
        if self._kill_timer is not None:
            try:
                self._kill_timer.cancel()
            except Exception:
                pass
            self._kill_timer = None

    def _superseded(self, generation: int) -> bool:
        return generation != self._generation or self._is_cancelled()

    def _report_stall(self, generation: int, elapsed: float, forced: bool) -> bool:
        """Report a stall, at most once per run. True if this call reported it."""
        with self._report_lock:
            if self._reported or self._superseded(generation):
                return False
            self._reported = True
        self._ui_sink(self._on_stall, elapsed, forced)
        return True

    def _on_kill_timer(self, generation: int) -> None:
        self._report_stall(generation, self._max_duration_seconds, True)

    def _loop(self, generation: int, stop_event: threading.Event) -> None:
        try:
            while not stop_event.is_set():
                if stop_event.wait(self._interval_seconds):
                    return
                if self._superseded(generation):
                    return

                now = time.time()
                elapsed = now - self._last_event_time
                age = now - self._started_at

                if age > self._max_duration_seconds:
                    self._report_stall(generation, elapsed, True)
                    return
                if elapsed > self._error_seconds:
                    self._report_stall(generation, elapsed, False)
                    return
                if elapsed > self._warning_seconds and not self._warned:
                    self._warned = True
                    self._ui_sink(self._on_slow)
        except BaseException:
            # Dying silently would leave a stalled stream looking healthy.
            print("[DeepPCB Chat] watchdog crashed:\n" + traceback.format_exc())
