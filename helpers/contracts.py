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

from typing import Optional, List
from dataclasses import dataclass, field


@dataclass
class CreateBoardRequest:
    board_name: str
    job_type: str
    kicad_board_file_path: str
    kicad_project_file_path: Optional[str] = None
    kicad_schematics_file_paths: List[str] = field(default_factory=list)
    routing_type: str = "CurrentUnprotectedWiring"
    batch: int = 1
    board_input_type: str = "Kicad"
    request_id: Optional[str] = None


@dataclass
class Revision:
    revision_number: int

    @staticmethod
    def from_dict(data: dict) -> "Revision":
        return Revision(revision_number=data.get("revisionNumber", 0))


@dataclass
class Workflow:
    job_type: str
    started_on: Optional[str]
    workflow_timeout: Optional[int]
    revisions: List[Revision] = field(default_factory=list)

    @staticmethod
    def from_dict(data: dict) -> "Workflow":
        return Workflow(
            job_type=data.get("jobType", ""),
            started_on=data.get("startedOn"),
            workflow_timeout=data.get("workflowTimeOut"),
            revisions=[Revision.from_dict(r) for r in data.get("revisions", [])],
        )


@dataclass
class DeepPCBBoard:
    board_status: str
    board_pid: str
    requires_credits: bool
    credits_cost_per_minute: float
    workflow: Optional[Workflow]
    workflows: List[Workflow]
    raw: dict

    @staticmethod
    def from_dict(data: dict) -> "DeepPCBBoard":
        workflow_data = data.get("workflow")
        return DeepPCBBoard(
            board_status=data.get("boardStatus", "Unknown"),
            board_pid=data.get("boardPId", ""),
            requires_credits=data.get("requiresCredits", False),
            credits_cost_per_minute=float(data.get("creditsCostPerMinute", 0)),
            workflow=Workflow.from_dict(workflow_data) if workflow_data else None,
            workflows=[Workflow.from_dict(w) for w in data.get("workflows", [])],
            raw=data,
        )

    def get_latest_revision_number(self) -> Optional[int]:
        if self.workflows and self.workflows[-1].revisions:
            return self.workflows[-1].revisions[-1].revision_number
        return None

    def get_all_revision_numbers(self) -> List[int]:
        numbers = []
        for workflow in self.workflows:
            for revision in workflow.revisions:
                numbers.append(revision.revision_number)
        return numbers


@dataclass
class BoardStatusResponse:
    status: int
    success: bool
    board: Optional[DeepPCBBoard] = None
    error: Optional[str] = None
    raw_response: str = ""


@dataclass
class BoardDownloadResponse:
    status: int
    success: bool
    content: str = ""
    error: Optional[str] = None


@dataclass
class BoardCreateResponse:
    status: int
    success: bool
    board_id: str = ""
    error: Optional[str] = None


@dataclass
class BoardSubmitResponse:
    status: int
    success: bool
    error: Optional[str] = None


@dataclass
class BoardStopResponse:
    status: int
    success: bool
    message: str = ""
    error: Optional[str] = None


@dataclass
class BoardResumeResponse:
    status: int
    success: bool
    message: str = ""
    error: Optional[str] = None


@dataclass
class CreditBalanceResponse:
    status: int
    success: bool
    balance: Optional[float] = None
    error: Optional[str] = None
