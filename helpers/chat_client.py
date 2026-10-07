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

"""
DeepPCB Chat API client.

Uses the DeepPCB API key (x-deeppcb-api-key header). The chat endpoints are
board-scoped:
    /api/v1/boards/{boardId}/conversations
    /api/v1/boards/{boardId}/conversations/{id}/messages
    /api/v1/boards/{boardId}/conversations/{id}/chat/completions

Streaming uses the AG-UI protocol. A frontend-tool call pauses the run; the
client executes the tool locally and RESUMES the same turn by re-POSTing to
chat/completions with an empty `messages` list and the tool result in `resume`
(there is no separate tool-decision endpoint under AG-UI).
"""

import json
from typing import Any, Dict, List, Optional

import requests

from .http_helpers import get_session, get_no_retry_session, DEFAULT_TIMEOUT

# On a streamed run the read timeout is silence tolerance, not total duration.
# It must stay above ChatPanel's stall thresholds, or requests kills the stream
# before the watchdog can explain why.
STREAM_CONNECT_TIMEOUT = 10
STREAM_READ_TIMEOUT = 330


class ChatAuthMissingError(Exception):
    pass


def _auth_headers(api_key: str, content_type: Optional[str] = None) -> Dict[str, str]:
    if not api_key:
        raise ChatAuthMissingError("API key is required for the chat API")
    headers = {
        "accept": "*/*",
        "x-deeppcb-api-key": api_key,
        "x-client-type": "KicadPlugin",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


class ChatClient:
    def __init__(self, base_url: str, api_key: str):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def api_key(self) -> str:
        return self._api_key

    def list_conversations(
        self, board_id: str, page_number: int = 1, page_size: int = 20
    ) -> Dict[str, Any]:
        url = f"{self._base_url}/boards/{board_id}/conversations"
        params = {"pageNumber": page_number, "pageSize": page_size}
        return _request("GET", url, headers=_auth_headers(self._api_key), params=params)

    def create_conversation(
        self, board_id: str, title: Optional[str] = None
    ) -> Dict[str, Any]:
        url = f"{self._base_url}/boards/{board_id}/conversations"
        payload = {"title": title, "agentSessionId": None}
        return _request(
            "POST",
            url,
            headers=_auth_headers(self._api_key, content_type="application/json"),
            json_data=payload,
            # Never retried: a replay creates a duplicate conversation.
            session=get_no_retry_session(),
        )

    def get_messages(
        self,
        board_id: str,
        conversation_id: str,
        page_number: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        url = (
            f"{self._base_url}/boards/{board_id}"
            f"/conversations/{conversation_id}/messages"
        )
        params = {"pageNumber": page_number, "pageSize": page_size}
        return _request("GET", url, headers=_auth_headers(self._api_key), params=params)

    def open_stream(
        self,
        board_id: str,
        conversation_id: str,
        messages: List[Dict[str, str]],
        resume: Any = None,
    ) -> requests.Response:
        """Open a streaming AG-UI run.

        A normal turn sends the user `messages`. A resume of a paused turn sends
        `messages=[]` and the executed frontend-tool result in `resume` — the
        server forwards it to the agent as command.resume (no new user message).
        """
        url = (
            f"{self._base_url}/boards/{board_id}"
            f"/conversations/{conversation_id}/chat/completions"
        )
        payload: Dict[str, Any] = {"messages": messages, "stream": True}
        if resume is not None:
            payload["resume"] = resume
        session = get_no_retry_session()
        return session.post(
            url,
            headers=_auth_headers(self._api_key, content_type="application/json"),
            json=payload,
            stream=True,
            timeout=(STREAM_CONNECT_TIMEOUT, STREAM_READ_TIMEOUT),
        )


def _request(
    method: str,
    url: str,
    headers: Dict[str, str],
    params: Optional[Dict] = None,
    json_data: Optional[Dict] = None,
    timeout: int = DEFAULT_TIMEOUT,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    session = session or get_session()
    try:
        response = session.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=timeout,
        )
        text = response.text
        ok = 200 <= response.status_code < 300
        parsed = None
        if ok and text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
        return {
            "status": response.status_code,
            "success": ok,
            "response": text,
            "data": parsed,
        }
    except requests.exceptions.RequestException as e:
        return {
            "status": 0,
            "success": False,
            "response": str(e),
            "data": None,
        }
