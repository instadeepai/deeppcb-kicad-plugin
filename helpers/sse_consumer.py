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
AG-UI SSE consumer for the DeepPCB chat stream.

Runs on a worker thread. The API relays the agent's **AG-UI** event stream: each
frame is `data: <json>\\n\\n` with NO `event:` line — the event kind is the
`type` field inside the JSON. This consumer parses those frames, assembles the
streamed pieces (text deltas, tool-call START/ARGS/END), and dispatches typed
callbacks through a thread-safe sink (wx.CallAfter or any callable(func, *args)).

Mirrors the web client so plugin and browser behave identically against the
same endpoint.

AG-UI event types:
  - RUN_STARTED            → (start of run; no callback)
  - TEXT_MESSAGE_CONTENT   → on_delta(content)          # {messageId, delta}
  - TOOL_CALL_START        → begin a tool call          # {toolCallId, toolCallName}
  - TOOL_CALL_ARGS         → append tool-call args       # {toolCallId, delta}
  - TOOL_CALL_END          → on_tool_call(tool_calls)    # emits the assembled list
  - CUSTOM                 → by `name`:                   # {name, value}
      status      → on_status(type, toolName, description)
      budget      → on_budget(value)
      metadata    → on_metadata(chatId, conversationId)
      on_interrupt→ marks the run as paused on a frontend-tool call
      usage/trace_id/heartbeat → ignored
  - RUN_FINISHED           → on_run_finished(tool_calls, interrupted)   # terminal
  - RUN_ERROR              → on_error(code, message, None)              # terminal

When the run pauses on a frontend-tool interrupt (CUSTOM:on_interrupt), the
trailing RUN_FINISHED is a pause, not the turn's end: on_run_finished fires with
interrupted=True and the caller executes the tool + resumes the turn.
"""

import json
import threading
from typing import Any, Callable, Dict, List, Optional

import requests


# AG-UI event type discriminators (value of the JSON `type` field).
_RUN_STARTED = "RUN_STARTED"
_RUN_FINISHED = "RUN_FINISHED"
_RUN_ERROR = "RUN_ERROR"
_TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
_TOOL_CALL_START = "TOOL_CALL_START"
_TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
_TOOL_CALL_END = "TOOL_CALL_END"
_CUSTOM = "CUSTOM"

# CUSTOM event `name` values the relay carries.
_CUSTOM_STATUS = "status"
_CUSTOM_BUDGET = "budget"
_CUSTOM_METADATA = "metadata"
_CUSTOM_ON_INTERRUPT = "on_interrupt"


def normalize_tool_call(dto: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize an OpenAI-style tool-call DTO (used for history rendering)."""
    fn = dto.get("function") or {}
    raw_args = fn.get("arguments") or "{}"
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except (json.JSONDecodeError, TypeError):
        args = {}
    return {
        "id": dto.get("id"),
        "type": dto.get("type"),
        "name": fn.get("name"),
        "args": args,
    }


def _parse_args(raw_args: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw_args) if raw_args else {}
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


class StreamCallbacks:
    def __init__(
        self,
        on_metadata: Optional[Callable[[str, str], None]] = None,
        on_status: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None,
        on_tool_call: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
        on_delta: Optional[Callable[[str], None]] = None,
        on_budget: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_error: Optional[Callable[[str, str, Optional[int]], None]] = None,
        on_run_finished: Optional[Callable[[List[Dict[str, Any]], bool], None]] = None,
        on_stream_closed: Optional[Callable[[], None]] = None,
        on_activity: Optional[Callable[[], None]] = None,
    ):
        self.on_metadata = on_metadata
        self.on_status = on_status
        self.on_tool_call = on_tool_call
        self.on_delta = on_delta
        self.on_budget = on_budget
        self.on_error = on_error
        self.on_run_finished = on_run_finished
        self.on_stream_closed = on_stream_closed
        # Every well-formed frame, including ones not modelled here
        # (heartbeats, usage): liveness, not content.
        self.on_activity = on_activity


class SseConsumer:
    """Owns a worker thread that consumes one streaming AG-UI HTTP response."""

    def __init__(
        self,
        response: requests.Response,
        callbacks: StreamCallbacks,
        ui_sink: Callable[..., None],
    ):
        self._response = response
        self._callbacks = callbacks
        self._ui_sink = ui_sink
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._tool_order: List[str] = []
        self._tool_calls: Dict[str, Dict[str, str]] = {}
        self._interrupted = False
        self._content_seen = False
        self._last_message_id: Optional[str] = None
        self._error_emitted = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._response.close()
        except Exception:
            pass

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _emit(self, fn: Optional[Callable], *args) -> None:
        if fn is None:
            return
        try:
            self._ui_sink(fn, *args)
        except Exception:
            # A dead UI sink must not kill the stream thread.
            pass

    def _run(self) -> None:
        try:
            if not (200 <= self._response.status_code < 300):
                self._handle_error_response()
                return

            content_type = self._response.headers.get("content-type", "")
            if "charset=" not in content_type.lower():
                self._response.encoding = "utf-8"

            for raw_line in self._response.iter_lines(decode_unicode=True):
                if self._stop.is_set():
                    break
                if raw_line is None:
                    continue
                line = raw_line.strip()
                if not line or line.startswith(":"):
                    # Blank line = frame boundary; ":" = SSE comment/heartbeat.
                    continue
                if line.startswith("event:"):
                    # AG-UI carries no `event:` line; ignore if a proxy adds one.
                    continue
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    self._dispatch(payload)
        except Exception as e:
            if not self._error_emitted:
                self._error_emitted = True
                self._emit(self._callbacks.on_error, "STREAM_ERROR", str(e), None)
        finally:
            try:
                self._response.close()
            except Exception:
                pass
            self._emit(self._callbacks.on_stream_closed)

    def _handle_error_response(self) -> None:
        retry_after = self._response.headers.get("Retry-After")
        try:
            retry_seconds = int(retry_after) if retry_after else None
        except (TypeError, ValueError):
            retry_seconds = None

        try:
            body = self._response.json()
        except (ValueError, json.JSONDecodeError):
            body = {}

        code = body.get("errorCode") or f"HTTP_{self._response.status_code}"
        message = (
            body.get("errorMessage") or self._response.reason or "Chat request failed"
        )
        self._error_emitted = True
        self._emit(self._callbacks.on_error, code, message, retry_seconds)

    def _assembled_tool_calls(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": tid,
                "type": "function",
                "name": self._tool_calls[tid].get("name"),
                "args": _parse_args(self._tool_calls[tid].get("args", "")),
            }
            for tid in self._tool_order
        ]

    def _dispatch(self, raw: str) -> None:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return

        self._emit(self._callbacks.on_activity)

        event_type = parsed.get("type")

        if event_type == _TEXT_MESSAGE_CONTENT:
            delta = parsed.get("delta")
            if not delta:
                return
            # A new messageId is a new assistant turn (after a tool round); join
            # turns with a blank line so concatenated content reads correctly
            message_id = parsed.get("messageId")
            if (
                self._content_seen
                and message_id
                and message_id != self._last_message_id
            ):
                delta = "\n\n" + delta
            self._content_seen = True
            self._last_message_id = message_id
            self._emit(self._callbacks.on_delta, delta)

        elif event_type == _TOOL_CALL_START:
            tool_call_id = parsed.get("toolCallId")
            if tool_call_id and tool_call_id not in self._tool_calls:
                self._tool_order.append(tool_call_id)
                self._tool_calls[tool_call_id] = {
                    "name": parsed.get("toolCallName"),
                    "args": "",
                }

        elif event_type == _TOOL_CALL_ARGS:
            tool_call_id = parsed.get("toolCallId")
            delta = parsed.get("delta")
            if tool_call_id in self._tool_calls and delta:
                self._tool_calls[tool_call_id]["args"] += delta

        elif event_type == _TOOL_CALL_END:
            self._emit(self._callbacks.on_tool_call, self._assembled_tool_calls())

        elif event_type == _CUSTOM:
            name = parsed.get("name")
            value = parsed.get("value")
            if name == _CUSTOM_STATUS and isinstance(value, dict):
                self._emit(
                    self._callbacks.on_status,
                    value.get("type"),
                    value.get("toolName"),
                    value.get("description"),
                )
            elif name == _CUSTOM_BUDGET:
                self._emit(self._callbacks.on_budget, value)
            elif name == _CUSTOM_METADATA and isinstance(value, dict):
                self._emit(
                    self._callbacks.on_metadata,
                    value.get("chatId"),
                    value.get("conversationId"),
                )
            elif name == _CUSTOM_ON_INTERRUPT:
                # A frontend-tool interrupt: the following RUN_FINISHED is a
                # pause (awaiting the tool's result), not the turn's end.
                self._interrupted = True

        elif event_type == _RUN_ERROR:
            self._error_emitted = True
            self._emit(
                self._callbacks.on_error,
                parsed.get("code") or "UNKNOWN",
                parsed.get("message") or "Unknown error",
                None,
            )

        elif event_type == _RUN_FINISHED:
            self._emit(
                self._callbacks.on_run_finished,
                self._assembled_tool_calls(),
                self._interrupted,
            )

        # RUN_STARTED and unmodeled events (usage/trace_id/heartbeat) are ignored.
