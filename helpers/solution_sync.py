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

"""Keeping the panel in step with the board: when to poll, and when a new
solution may be loaded onto the open board without being asked.

Rendering itself stays with the caller — it touches pcbnew and must run on the
main thread — so this reports through callbacks and never renders anything.
"""

import os
import threading
import time
from typing import Callable, Optional

from .contracts import ACTIVE_STATES, Revision

ACTIVE_POLL_INTERVAL_SECONDS = 5
IDLE_POLL_INTERVAL_SECONDS = 60

# Also the window in which auto-render is authorised: a change requested through
# Cooper can land seconds after the request that caused it.
POST_TURN_POLL_WINDOW_SECONDS = 90


def _never_cancelled() -> bool:
    return False


class SolutionSync:
    """Polls board status and decides what to do with a new revision.

    on_status(response)              every successful poll
    on_render(revision, download)    a new revision that may be loaded now
    on_solution_available(revision)  a new revision the caller should offer
                                     instead of loading unasked
    """

    def __init__(
        self,
        client,
        board_id: str,
        project_name: str,
        project_directory: str,
        on_status: Callable[[object], None],
        on_render: Callable[[int, dict], None],
        on_solution_available: Callable[[int], None],
        ui_sink: Callable[..., None],
        is_cancelled: Callable[[], bool] = _never_cancelled,
    ):
        self._client = client
        self._board_id = board_id
        self._project_name = project_name
        self._project_directory = project_directory
        self._on_status = on_status
        self._on_render = on_render
        self._on_solution_available = on_solution_available
        self._ui_sink = ui_sink
        self._is_cancelled = is_cancelled

        self._thread: Optional[threading.Thread] = None
        self._board = None
        self._poll_until = 0.0
        # Rendered, or deliberately skipped: never auto-render it again.
        self._handled: Optional[Revision] = None
        # Guards the decide-and-claim step: the gate is evaluated on
        # whichever worker thread got here first, and tick_now() runs one
        # alongside the polling loop.
        self._claim = threading.Lock()
        self._baseline_set = False

    @property
    def board(self):
        return self._board

    @property
    def handled(self) -> Optional[Revision]:
        return self._handled

    @property
    def baseline_set(self) -> bool:
        """Whether the caller has declared what is already on the canvas.

        Distinct from a revision having been handled: a board can open with no
        revisions at all, and the first one to appear must still be treated as
        new rather than as the baseline.
        """
        return self._baseline_set

    def solution_path(self, revision_number: int) -> str:
        solutions_dir = os.path.join(self._project_directory, "DeepPCB_Solutions")
        return os.path.join(
            solutions_dir,
            f"{self._project_name}_solution_{int(revision_number):04d}.kicad_pcb",
        )

    def set_baseline(self, revision: Optional[Revision]) -> None:
        """Declare what is already on the canvas. Until this is called nothing
        may auto-render, so a revision that predates the panel is never loaded
        over the user's board."""
        self._baseline_set = True
        if revision is not None:
            self._handled = revision

    def mark_handled(self, revision: Revision) -> None:
        """Called after a render attempt, successful or not: a revision that
        fails to render must not be retried on every tick.

        Never moves backwards: this runs on the UI thread once a render has
        finished, by which point a tick may already have claimed a newer
        revision, and overwriting it would render that one twice.
        """
        if self._handled is None or revision.is_new_since(self._handled):
            self._handled = revision

    def bump_window(self) -> None:
        """Hold the fast interval, and auto-render authorisation, open for a
        change that lands after the request that caused it."""
        self._poll_until = time.time() + POST_TURN_POLL_WINDOW_SECONDS

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 0.5) -> None:
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def tick_now(self) -> None:
        """Poll once, off the calling thread, without waiting for the interval."""
        threading.Thread(target=self.tick, daemon=True).start()

    def download(self, revision_number: int) -> dict:
        from ..utils import download_and_save_board

        return download_and_save_board(
            self._client,
            self._board_id,
            int(revision_number),
            self.solution_path(revision_number),
        )

    def poll_interval_seconds(self) -> int:
        if time.time() < self._poll_until:
            return ACTIVE_POLL_INTERVAL_SECONDS
        if self._board and self._board.board_status in ACTIVE_STATES:
            return ACTIVE_POLL_INTERVAL_SECONDS
        return IDLE_POLL_INTERVAL_SECONDS

    def auto_render_allowed(self) -> bool:
        """Only where the user implicitly asked for it: just after a Cooper turn,
        or during a job they started. A background tick must never overwrite the
        open board - loading a solution is destructive and has no undo."""
        if not self._baseline_set:
            return False
        if time.time() < self._poll_until:
            return True
        return bool(self._board and self._board.board_status in ACTIVE_STATES)

    def tick(self) -> None:
        try:
            response = self._client.check_board_status(self._board_id)
            if not (response.success and response.board):
                # Nothing else reports it: the panel is never called.
                print(f"[DeepPCB] board status fetch failed: {response.status}")
                return
            self._board = response.board
            latest = self._board.get_latest_revision()

            with self._claim:
                is_new = latest is not None and latest.is_new_since(self._handled)
                should_render = is_new and self.auto_render_allowed()
                if should_render:
                    # Claim it here, on the thread that decided. Waiting for
                    # mark_handled() on the UI thread leaves a window in
                    # which another tick picks the same revision and renders
                    # it a second time.
                    self._handled = latest

            download_result = None
            if should_render:
                download_result = self.download(latest.revision_number)

            if self._is_cancelled():
                return

            self._ui_sink(self._on_status, response)
            if is_new and not should_render:
                self._ui_sink(self._on_solution_available, latest.revision_number)
            elif should_render and download_result and download_result.get("success"):
                self._ui_sink(self._on_render, latest, download_result)
        except Exception:
            # A failed tick is transient; the next one retries.
            pass

    def _loop(self) -> None:
        while not self._is_cancelled():
            deadline = time.time() + self.poll_interval_seconds()
            while not self._is_cancelled() and time.time() < deadline:
                time.sleep(1)
            if self._is_cancelled():
                break
            self.tick()
