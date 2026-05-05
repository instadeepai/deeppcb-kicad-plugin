import json

from . import http_helpers
from .contracts import (
    CreateBoardRequest,
    DeepPCBBoard,
    BoardStatusResponse,
    BoardDownloadResponse,
    BoardCreateResponse,
    BoardSubmitResponse,
    BoardStopResponse,
    BoardResumeResponse,
    CreditBalanceResponse,
)


class DeepPCBClient:
    def __init__(self, base_url: str, api_key: str):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def api_key(self) -> str:
        return self._api_key

    def download_board(
        self, board_id: str, type: str, revision_number: int
    ) -> BoardDownloadResponse:
        result = http_helpers.download_board(
            board_id=board_id,
            type=type,
            swagger_url=self._base_url,
            revision_number=revision_number,
            api_key=self._api_key,
        )
        return BoardDownloadResponse(
            status=result["status"],
            success=result.get("success", False),
            content=result["response"] if result.get("success") else "",
            error=result.get("error")
            or (result["response"] if not result.get("success") else None),
        )

    def check_board_status(self, board_id: str) -> BoardStatusResponse:
        result = http_helpers.check_board_status(
            board_id=board_id, swagger_url=self._base_url, api_key=self._api_key
        )

        board = None
        error = None

        if result.get("success"):
            try:
                data = json.loads(result["response"])
                board = DeepPCBBoard.from_dict(data)
            except (json.JSONDecodeError, KeyError) as e:
                error = f"Failed to parse board data: {str(e)}"
        else:
            error = result.get("error") or result["response"]

        return BoardStatusResponse(
            status=result["status"],
            success=result.get("success", False) and board is not None,
            board=board,
            error=error,
            raw_response=result["response"],
        )

    def submit_board(
        self, board_id: str, timeout: int, job_type: str
    ) -> BoardSubmitResponse:
        result = http_helpers.submit_board(
            board_id=board_id,
            timeout=timeout,
            swagger_url=self._base_url,
            api_key=self._api_key,
            job_type=job_type,
        )
        return BoardSubmitResponse(
            status=result["status"],
            success=result.get("success", False),
            error=result.get("error")
            or (result["response"] if not result.get("success") else None),
        )

    def create_board(self, request: CreateBoardRequest) -> BoardCreateResponse:
        result = http_helpers.create_board(
            swagger_url=self._base_url,
            api_key=self._api_key,
            board_name=request.board_name,
            kicad_board_file_path=request.kicad_board_file_path,
            kicad_project_file_path=request.kicad_project_file_path,
            kicad_schematics_file_paths=request.kicad_schematics_file_paths,
            routing_type=request.routing_type,
            batch=request.batch,
            board_input_type=request.board_input_type,
            request_id=request.request_id,
        )

        board_id = ""
        if result.get("success"):
            board_id = result["response"].strip().strip('"')

        return BoardCreateResponse(
            status=result["status"],
            success=result.get("success", False) and bool(board_id),
            board_id=board_id,
            error=result.get("error")
            or (result["response"] if not result.get("success") else None),
        )

    def stop_board(self, board_id: str) -> BoardStopResponse:
        result = http_helpers.stop_board(
            board_id=board_id, swagger_url=self._base_url, api_key=self._api_key
        )
        return BoardStopResponse(
            status=result["status"],
            success=result.get("success", False),
            message=result["response"] if result.get("success") else "",
            error=result.get("error")
            or (result["response"] if not result.get("success") else None),
        )

    def resume_board(
        self, board_id: str, timeout: int, job_type: str
    ) -> BoardResumeResponse:
        result = http_helpers.resume_board(
            board_id=board_id,
            timeout=timeout,
            swagger_url=self._base_url,
            api_key=self._api_key,
            job_type=job_type,
        )
        return BoardResumeResponse(
            status=result["status"],
            success=result.get("success", False),
            message=result["response"] if result.get("success") else "",
            error=result.get("error")
            or (result["response"] if not result.get("success") else None),
        )

    def get_credit_balance(self) -> CreditBalanceResponse:
        result = http_helpers.get_credit_balance(
            swagger_url=self._base_url, api_key=self._api_key
        )
        return CreditBalanceResponse(
            status=result["status"],
            success=result.get("success", False) and result["balance"] is not None,
            balance=result["balance"],
            error=result.get("error")
            or (result["response"] if not result.get("success") else None),
        )
