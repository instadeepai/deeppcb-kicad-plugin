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

"""Starting a routing job: check credits, submit, wait for the run.

No wx here, so this can move off the UI thread without touching the sequence.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from .client import DeepPCBClient
from .contracts import DeepPCBBoard

# Everything except STARTED and NOT_RUNNING_YET means nothing was submitted.
BALANCE_CHECK_FAILED = "balance_check_failed"
INSUFFICIENT_CREDITS = "insufficient_credits"
SUBMIT_FAILED = "submit_failed"
NOT_RUNNING_YET = "not_running_yet"
STARTED = "started"

RUN_START_TIMEOUT_SECONDS = 300


@dataclass
class RoutingJobResult:
    outcome: str
    message: str = ""
    status: Optional[int] = None
    balance: Optional[float] = None
    cost: Optional[float] = None

    @property
    def submitted(self) -> bool:
        """Whether the job reached the server. A run that has not turned Running
        yet is still submitted, and still billable."""
        return self.outcome in (STARTED, NOT_RUNNING_YET)


def start_routing_job(
    client: DeepPCBClient,
    board: DeepPCBBoard,
    board_id: str,
    timeout: int,
    job_type: str,
    on_progress: Optional[Callable[[int, str], None]] = None,
) -> RoutingJobResult:
    """Check the balance if the board needs credits, submit, then wait for the
    run to start. on_progress(percent, message) is for a progress indicator."""
    from ..utils import poll_board_status

    def progress(percent: int, message: str) -> None:
        if on_progress:
            on_progress(percent, message)

    if board.requires_credits:
        cost = board.credits_cost_per_minute * timeout
        balance_response = client.get_credit_balance()
        if not balance_response.success:
            return RoutingJobResult(
                outcome=BALANCE_CHECK_FAILED,
                message=balance_response.error or "",
                status=balance_response.status,
            )
        if balance_response.balance < cost:
            return RoutingJobResult(
                outcome=INSUFFICIENT_CREDITS,
                balance=balance_response.balance,
                cost=cost,
            )

    progress(25, f"Submitting your {job_type} job…")
    submit_response = client.submit_board(board_id, timeout, job_type)
    if not submit_response.success:
        return RoutingJobResult(
            outcome=SUBMIT_FAILED,
            message=submit_response.error or "",
            status=submit_response.status,
        )

    progress(60, "Waiting for run to start…")
    polling = poll_board_status(client, board_id, "Running", RUN_START_TIMEOUT_SECONDS)
    progress(100, "Run started")
    if not polling["success"]:
        return RoutingJobResult(
            outcome=NOT_RUNNING_YET,
            message=polling["message"],
            status=polling.get("status"),
        )
    return RoutingJobResult(outcome=STARTED)
