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
DeepPCB Chat dockable panel.

Single persistent conversation per board. Streams DeepPCB chat completions over
SSE, dispatches tool calls to the local pcbnew executor (or sends approve/
reject decisions back to the server for tools that need it), and supports the
suggest_actions flow used by the web app.
"""

import json
import threading
import traceback
from typing import Any, Dict, List, Optional

import wx

from ..config import APP_VERSION
from ..dialogs.api_key_dialog import load_api_key_from_config
from ..helpers import (
    ChatClient,
    ChatAuthMissingError,
    SseConsumer,
    StreamCallbacks,
    normalize_tool_call,
)
from ..helpers import tool_executor
from ..helpers.conversation_store import ConversationStore
from ..helpers.stream_watchdog import (
    MAX_STREAM_DURATION_SECONDS,
    StreamWatchdog,
)
from .dockable_panel import KiCadDockablePanel
from .chat_renderer import build_renderer


SUGGESTION_DECISIONS_HEADER = "The user APPROVED the following actions."


# Seed prompt sent to open a new conversation, matching the web app's
# INITIAL_ANALYSIS_PROMPT verbatim (so a conversation started in one client reads
# the same in the other).
INITIAL_ANALYSIS_PROMPT = (
    "Please analyze this PCB board and provide an overview of its configuration. "
    "After your analysis, use the suggest_actions tool to present configuration "
    "actions I should take to prepare the board for routing."
)

# Auto-analysis seed prompts are internal plumbing, not shown in the transcript
# (mirrors the web app's HIDDEN_PROMPTS). Includes the legacy short prompt so
# conversations created before this change also hide their seed on reload.
AUTO_ANALYSIS_PROMPTS = {
    INITIAL_ANALYSIS_PROMPT,
    "Analyze this board and give recommendations.",
}


def _format_suggestion_decisions(decisions: List[Dict[str, Any]]) -> str:
    approved = [d for d in decisions if d.get("approved")]
    rejected = [d for d in decisions if not d.get("approved")]
    parts: List[str] = []
    if approved:
        parts.append(
            SUGGESTION_DECISIONS_HEADER
            + " Determine the correct execution order based on dependencies "
            "(apply board setup configurations before starting routing or placement):"
        )
        for d in approved:
            parts.append(f"- {d.get('tool')}({json.dumps(d.get('args') or {})})")
    if rejected:
        parts.append("\nThe user REJECTED the following actions. Do NOT execute these:")
        for d in rejected:
            parts.append(f"- {d.get('tool')}({json.dumps(d.get('args') or {})})")
    parts.append(
        "\nDo NOT call suggest_actions — execute the approved tools directly. "
        "After executing, ask the user if they want to make any other configuration "
        "changes before starting routing."
    )
    return "\n".join(parts)


class ChatPanel(KiCadDockablePanel):
    PANEL_NAME = "deeppcb_chat"
    PANEL_CAPTION = "Cooper"
    DEFAULT_SIZE = (480, 640)
    MIN_SIZE = (380, 420)

    def __init__(
        self,
        parent,
        board_id: str,
        api_url: str,
        project_name: str,
        project_directory: str,
        on_panel_closed=None,
        on_assistant_turn_complete=None,
        initial_user_message: Optional[str] = None,
    ):
        self.board_id = board_id
        self.api_url = api_url
        self.project_name = project_name
        self.project_directory = project_directory
        self._on_panel_closed_callback = on_panel_closed
        self._on_assistant_turn_complete = on_assistant_turn_complete
        self._initial_user_message = initial_user_message
        self._initial_message_sent = False

        self._is_closing = False
        self._stream_opening = False
        self._api_key = load_api_key_from_config()
        self._client: Optional[ChatClient] = (
            ChatClient(api_url, self._api_key) if self._api_key else None
        )
        self._conversation_store = ConversationStore()
        self._conversation_id: Optional[str] = None
        self._active_consumer: Optional[SseConsumer] = None
        # Turn-level state: a turn spans multiple runs (streams) when a frontend
        # tool interrupts it — the assistant bubble and content persist across
        # the resume runs until a run completes without an interrupt.
        self._active_assistant_id: Optional[str] = None
        self._accumulated_content = ""
        self._turn_tool_calls: List[Dict[str, Any]] = []
        self._turn_finalized = False
        self._turn_saw_content = False
        self._turn_errored = False
        self._init_thread: Optional[threading.Thread] = None
        self._health_state = "ready"
        self._watchdog = StreamWatchdog(
            on_slow=self._handle_slow,
            on_stall=self._handle_stall,
            ui_sink=wx.CallAfter,
            is_cancelled=lambda: self._is_closing,
        )

        super().__init__(parent)
        self._update_caption(f"Cooper (v{APP_VERSION})")

        self._renderer.on_send = self._on_user_send
        self._renderer.on_apply_suggestions = self._on_apply_suggestions

        if not self._api_key:
            self._renderer.set_error("No API key configured. Open Settings to add one.")
        else:
            self._renderer.set_status("Connecting…")
            self._start_init()

    def create_ui(self) -> None:
        outer = wx.BoxSizer(wx.VERTICAL)

        header = wx.BoxSizer(wx.HORIZONTAL)
        title = wx.StaticText(self, label="Cooper")
        font = title.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        title.SetFont(font)
        header.Add(title, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 6)

        self._health_label = wx.StaticText(self, label="● Ready")
        header.Add(self._health_label, 1, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 6)

        outer.Add(header, 0, wx.EXPAND)

        self._renderer = build_renderer(self)
        outer.Add(self._renderer, 1, wx.EXPAND)

        self.SetSizer(outer)
        self.Layout()

        self._set_health("ready")

    def _update_caption(self, caption: str) -> None:
        if self._aui_mgr:
            pane = self._aui_mgr.GetPane(self.PANEL_NAME)
            if pane.IsOk():
                pane.Caption(caption)
                self._aui_mgr.Update()

    def _set_health(self, state: str) -> None:
        self._health_state = state
        if (
            self._is_closing
            or not hasattr(self, "_health_label")
            or self._health_label is None
        ):
            return
        labels = {
            "ready": "● Ready",
            "streaming": "● Streaming…",
            "slow": "● Slow response",
            "stalled": "● Stalled",
            "error": "● Error",
        }
        colors = {
            "ready": (40, 160, 40),
            "streaming": (244, 117, 26),
            "slow": (210, 160, 40),
            "stalled": (200, 60, 60),
            "error": (200, 60, 60),
        }
        try:
            self._health_label.SetLabel(labels.get(state, "● Unknown"))
            self._health_label.SetForegroundColour(
                wx.Colour(*colors.get(state, (120, 120, 120)))
            )
            self._health_label.Refresh()
        except Exception:
            pass

    def _mark_event(self) -> None:
        self._watchdog.mark_event()
        if self._health_state in ("slow", "stalled"):
            self._set_health("streaming")

    def _handle_slow(self) -> None:
        if self._is_closing or self._health_state != "streaming":
            return
        self._set_health("slow")

    def _handle_stall(self, elapsed: float, force_max_duration: bool) -> None:
        if self._is_closing:
            return
        self._set_health("stalled")
        if self._renderer:
            if force_max_duration:
                msg = (
                    f"Stream exceeded the {MAX_STREAM_DURATION_SECONDS // 60}-minute "
                    "limit — closing it. Send another message to continue."
                )
            else:
                msg = (
                    f"No response from server for {int(elapsed)}s. "
                    "Stream is stalled — send another message to retry, "
                    "or refresh your API key in Settings."
                )
            self._renderer.set_error(msg)
        if self._active_consumer:
            try:
                self._active_consumer.stop()
            except Exception:
                pass

    def _safe_set_error(self, text: Optional[str]) -> None:
        if self._is_closing or not self._renderer:
            return
        try:
            self._renderer.set_error(text)
        except Exception:
            pass

    def _safe_set_status(self, text: Optional[str]) -> None:
        if self._is_closing or not self._renderer:
            return
        try:
            self._renderer.set_status(text)
        except Exception:
            pass

    def _start_init(self) -> None:
        self._init_thread = threading.Thread(target=self._init_worker, daemon=True)
        self._init_thread.start()

    def _init_worker(self) -> None:
        if self._client is None:
            return
        try:
            cached = self._conversation_store.get(self.project_directory, self.board_id)
            if cached:
                wx.CallAfter(self._adopt_conversation, cached, False)
                return

            existing = self._client.list_conversations(self.board_id)
            if existing.get("success"):
                data = (existing.get("data") or {}).get("data") or []
                if data:
                    wx.CallAfter(self._adopt_conversation, data[0].get("id"), False)
                    return

            created = self._client.create_conversation(self.board_id)
            if not created.get("success") or not (created.get("data") or {}).get("id"):
                wx.CallAfter(
                    self._safe_set_error,
                    f"Could not create conversation: {created.get('response')}",
                )
                wx.CallAfter(self._safe_set_status, None)
                return
            wx.CallAfter(
                self._adopt_conversation, (created.get("data") or {}).get("id"), True
            )
        except ChatAuthMissingError as e:
            wx.CallAfter(self._safe_set_error, str(e))
            wx.CallAfter(self._safe_set_status, None)
        except Exception as e:
            wx.CallAfter(self._safe_set_error, f"Init failed: {e}")
            wx.CallAfter(self._safe_set_status, None)

    def _adopt_conversation(self, conversation_id: str, is_new: bool) -> None:
        if self._is_closing or not conversation_id:
            return
        self._conversation_id = conversation_id
        self._conversation_store.set(
            self.project_directory, self.board_id, conversation_id
        )
        self._renderer.set_status(None)
        if not is_new and self._client:
            self._load_history()
        if is_new and self._initial_user_message and not self._initial_message_sent:
            self._initial_message_sent = True
            wx.CallAfter(self._send_initial_message)

    def _send_initial_message(self) -> None:
        if self._is_closing or not self._renderer or not self._client:
            return
        if not self._conversation_id or not self._initial_user_message:
            return
        if not self._ready_to_send():
            return
        # Send the auto-analysis seed the way the web app does: no visible user
        # bubble — the user sees only Cooper's reply. The prompt is also filtered
        # from history on reload (see _render_history / AUTO_ANALYSIS_PROMPTS).
        self._start_turn([{"role": "user", "content": self._initial_user_message}])

    def set_on_assistant_turn_complete(self, callback) -> None:
        self._on_assistant_turn_complete = callback

    def _load_history(self) -> None:
        if not self._client or not self._conversation_id:
            return

        def _worker():
            try:
                result = self._client.get_messages(
                    self.board_id, self._conversation_id, page_number=1, page_size=50
                )
                if not result.get("success"):
                    return
                page = result.get("data") or {}
                messages = page.get("data") or []
                wx.CallAfter(self._render_history, messages)
            except Exception:
                # Best-effort: the panel opens without history rather than failing.
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _render_history(self, messages: List[Dict[str, Any]]) -> None:
        if self._is_closing:
            return
        self._renderer.clear()
        for dto in messages:
            role = (dto.get("role") or "").lower()
            content = dto.get("content") or ""
            tool_calls_raw = dto.get("toolCalls") or []
            tool_calls = [normalize_tool_call(tc) for tc in tool_calls_raw]
            suggest_tool = next(
                (tc for tc in tool_calls if tc.get("name") == "suggest_actions"), None
            )
            if role == "user":
                # Skip the auto-analysis seed prompt — internal plumbing, never
                # shown in the transcript (mirrors the web app).
                if content in AUTO_ANALYSIS_PROMPTS:
                    continue
                self._renderer.add_user_message(content)
            else:
                message_id = self._renderer.start_assistant_message()
                self._renderer.finalize_assistant_message(
                    message_id,
                    content,
                    None,
                    False,
                    (suggest_tool or {}).get("args", {}).get("suggestions"),
                )

    def _on_user_send(self, text: str) -> None:
        if not self._ready_to_send():
            return
        self._renderer.add_user_message(text)
        self._start_turn([{"role": "user", "content": text}])

    def _on_apply_suggestions(
        self,
        _message_id: str,
        decisions: List[Dict[str, Any]],
        approved_titles: List[str],
    ) -> None:
        if not self._ready_to_send():
            return
        summary = (
            "Applied: " + ", ".join(approved_titles)
            if approved_titles
            else "Rejected all suggestions"
        )
        self._renderer.add_user_message(summary)
        formatted = _format_suggestion_decisions(decisions)
        self._start_turn([{"role": "user", "content": formatted}])

    def _ready_to_send(self) -> bool:
        if self._is_closing:
            return False
        if not self._client:
            self._renderer.set_error(
                "Chat client not configured. Add an API key in Settings."
            )
            return False
        if not self._conversation_id:
            self._renderer.set_error("Chat is still initialising.")
            return False
        if self._stream_opening or (
            self._active_consumer and self._active_consumer.is_running()
        ):
            self._renderer.set_error("Already streaming — please wait.")
            return False
        return True

    def _start_turn(self, messages: List[Dict[str, str]]) -> None:
        """Begin a new turn: fresh assistant bubble + content, first run."""
        self._accumulated_content = ""
        self._turn_tool_calls = []
        self._turn_finalized = False
        self._turn_saw_content = False
        self._turn_errored = False
        self._active_assistant_id = self._renderer.start_assistant_message()
        self._renderer.set_error(None)
        self._run_stream(messages, resume=None)

    def _reset_turn_state(self) -> None:
        """Clear everything a turn owns except the error flag."""
        self._active_assistant_id = None
        self._accumulated_content = ""
        self._turn_tool_calls = []
        self._turn_finalized = False
        self._turn_saw_content = False

    def _run_stream(self, messages: List[Dict[str, str]], resume: Any) -> None:
        """Open one AG-UI run within the current turn (initial or a resume).

        The POST runs on a worker thread. It returns only once the server sends
        response headers - bounded by the read timeout, not the connect one -
        and on the UI thread that stops the event loop for the whole wait, so
        the user's own message cannot paint and every delta the consumer posts
        through wx.CallAfter queues behind it. Both then arrive at once.
        """
        self._stream_opening = True

        def _worker():
            try:
                response = self._client.open_stream(
                    self.board_id, self._conversation_id, messages, resume=resume
                )
            except Exception as e:
                wx.CallAfter(self._on_stream_open_failed, e)
                return
            wx.CallAfter(self._on_stream_opened, response)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_stream_opened(self, response) -> None:
        self._stream_opening = False
        if self._is_closing:
            # Nobody will consume it, so let go of the socket.
            try:
                response.close()
            except Exception:
                # Closing a response that never opened is not worth reporting.
                pass
            return
        self._begin_run(response)

    def _on_stream_open_failed(self, error: Exception) -> None:
        self._stream_opening = False
        if self._is_closing:
            return
        self._renderer.set_error(f"Connect failed: {error}")
        self._set_health("error")
        self._turn_errored = True
        # No consumer, so _on_stream_closed will never run for this turn.
        self._reset_turn_state()
        self._watchdog.stop()
        self._safe_set_status(None)

    def _begin_run(self, response) -> None:
        self._set_health("streaming")
        self._watchdog.start()
        self._renderer.set_status("Thinking…")

        consumer_holder = {}

        def _close_handler():
            self._on_stream_closed(consumer_holder.get("consumer"))

        callbacks = StreamCallbacks(
            on_metadata=self._on_metadata,
            on_status=self._on_status,
            on_tool_call=self._on_tool_call,
            on_delta=self._on_delta,
            on_budget=self._on_budget,
            on_error=self._on_stream_error,
            on_run_finished=self._on_run_finished,
            on_stream_closed=_close_handler,
            on_activity=self._on_activity,
        )
        consumer = SseConsumer(response, callbacks, ui_sink=wx.CallAfter)
        consumer_holder["consumer"] = consumer
        self._active_consumer = consumer
        consumer.start()

    def _on_activity(self):
        """Any frame means the stream is alive - reset the stall clock.

        A long tool execution sends only status and heartbeat frames; counting
        those as silence reported a stall on a healthy stream.
        """
        if self._is_closing:
            return
        self._mark_event()

    def _on_metadata(self, _chat_id, _conversation_id):
        pass

    def _on_status(self, status_type, _tool_name, description):
        if self._is_closing or not self._renderer:
            return
        if status_type == "thinking":
            self._renderer.set_status("Thinking…")
        elif status_type == "tool_start":
            self._renderer.set_status(description or "Executing tool…")
        elif status_type == "tool_end":
            self._renderer.set_status("Thinking…")

    def _on_tool_call(self, tool_calls):
        if self._is_closing:
            return
        self._mark_event()
        self._turn_tool_calls = tool_calls or []

    def _on_delta(self, delta: str) -> None:
        if self._is_closing or not self._renderer:
            return
        if not self._active_assistant_id:
            return
        self._mark_event()
        self._turn_saw_content = True
        self._accumulated_content += delta
        self._renderer.append_assistant_delta(self._active_assistant_id, delta)

    def _on_run_finished(
        self, tool_calls: List[Dict[str, Any]], interrupted: bool
    ) -> None:
        """Terminal event of one run. If the run paused on a frontend-tool
        interrupt, execute the tool locally and resume the turn; otherwise the
        turn is done — finalize the assistant message."""
        if self._is_closing or not self._renderer:
            return
        self._mark_event()
        if tool_calls:
            self._turn_tool_calls = tool_calls

        if interrupted:
            # The interrupting frontend tool is the last one emitted this run
            # (single tool per turn; multi-tool is out of scope, mirroring web).
            tool_call = tool_calls[-1] if tool_calls else None
            if tool_call:
                self._renderer.set_status(
                    f"Executing {tool_call.get('name') or 'tool'}…"
                )
                result = tool_executor.execute_for_resume(tool_call)
            else:
                result = {
                    "status": "error",
                    "message": "No tool call to execute",
                }
            # Resume the same turn with the tool result (empty messages).
            self._run_stream([], resume=result)
            return

        self._finalize_turn(tool_calls)

    def _finalize_turn(self, tool_calls: List[Dict[str, Any]]) -> None:
        if self._turn_finalized or self._is_closing or not self._renderer:
            return
        if not self._active_assistant_id:
            return
        self._turn_finalized = True
        self._turn_saw_content = True

        suggest_tool = next(
            (tc for tc in tool_calls if tc.get("name") == "suggest_actions"), None
        )
        executable_tool_calls = [
            tc for tc in tool_calls if tc.get("name") != "suggest_actions"
        ]
        suggestions = (
            (suggest_tool or {}).get("args", {}).get("suggestions")
            if suggest_tool
            else None
        )

        final_content = self._accumulated_content
        if suggest_tool and not final_content:
            final_content = (suggest_tool.get("args") or {}).get("message") or ""

        self._renderer.finalize_assistant_message(
            self._active_assistant_id,
            final_content,
            None,
            False,
            suggestions,
        )

        # Non-interrupt tool calls in a completed run are server-executed or
        # viewer-only; run them through the executor (a no-op for those) to
        # mirror the web client's onComplete behaviour.
        if executable_tool_calls:
            self._execute_local_tools(executable_tool_calls)

    def _execute_local_tools(self, tool_calls: List[Dict[str, Any]]) -> None:
        for tc in tool_calls:
            tool_executor.execute_tool_call(tc)

    def _on_budget(self, _budget):
        pass

    def reload_credentials(self) -> None:
        """Re-read the API key after the user changed it in Settings."""
        if self._is_closing:
            return
        api_key = load_api_key_from_config()
        if not api_key:
            self._safe_set_error("No API key configured. Open Settings to add one.")
            return
        if api_key == self._api_key and self._client is not None:
            return
        self._api_key = api_key
        self._client = ChatClient(self.api_url, api_key)
        self._safe_set_error(None)
        if self._conversation_id is None:
            self._safe_set_status("Connecting…")
            self._start_init()

    def _on_stream_error(self, code: str, message: str, retry_after: Optional[int]):
        if self._is_closing or not self._renderer:
            return
        self._turn_errored = True
        self._set_health("error")
        suffix = f" (retry in {retry_after}s)" if retry_after else ""
        self._renderer.set_error(f"{code}: {message}{suffix}")
        self._renderer.set_status(None)

    def _on_stream_closed(self, consumer):
        if self._is_closing:
            if consumer is self._active_consumer:
                self._active_consumer = None
                self._active_assistant_id = None
            return
        # A run that started a resume (or a new turn) is superseded: the newer
        # run is now the active consumer and owns the turn lifecycle.
        if consumer is not self._active_consumer:
            return
        if not self._renderer:
            return
        # Reaching here means the current run ended without starting a resume —
        # the turn is over (normal completion, or error).
        self._watchdog.stop()
        self._renderer.set_status(None)
        # Finalize if the stream ended without an explicit RUN_FINISHED (e.g. a
        # dropped connection) but we did receive content/tool calls: the server
        # normally sends RUN_FINISHED/RUN_ERROR, but tie completion to stream end
        # too, matching the web client. A truly silent close (nothing received)
        # is left unfinalized so the "no reply" diagnostic below still fires.
        if (
            not self._turn_errored
            and not self._turn_finalized
            and (self._turn_saw_content or self._turn_tool_calls)
        ):
            self._finalize_turn(self._turn_tool_calls)
        produced_content = self._turn_saw_content
        had_error = self._turn_errored
        if not produced_content and not had_error:
            self._renderer.set_error(
                "Server closed the stream without sending a reply. "
                "This usually means the conversation is locked or the API key is invalid — "
                "wait a moment and retry, or refresh the API key in Settings."
            )
        self._active_consumer = None
        self._active_assistant_id = None
        self._accumulated_content = ""
        self._turn_tool_calls = []
        self._turn_finalized = False
        self._turn_saw_content = False
        self._turn_errored = False
        if had_error:
            self._set_health("error")
        elif produced_content:
            self._set_health("ready")
        else:
            self._set_health("stalled")
        if produced_content and not had_error and self._on_assistant_turn_complete:
            try:
                self._on_assistant_turn_complete()
            except Exception:
                # Best-effort: a failed refresh must not break the turn, but a
                # silent one leaves auto-render dead with nothing to show why.
                print("[DeepPCB] post-turn board refresh failed:")
                print(traceback.format_exc())

    def _allow_pane_close(self, event):
        """Defer the close once to stop a live stream, then always allow it.

        A veto that depends on turn bookkeeping strands the panel open if any
        of it leaks, so nothing else may block the close.
        """
        consumer = self._active_consumer
        if consumer is not None and consumer.is_running():
            try:
                consumer.stop()
            except Exception:
                pass
            self._safe_set_error(
                "Stopped Cooper's reply. Close the panel again to dismiss it."
            )
            return False
        return True

    def on_panel_close(self) -> None:
        self._is_closing = True
        self._watchdog.stop()

        consumer = self._active_consumer
        self._active_consumer = None
        if consumer:
            try:
                consumer.stop()
            except Exception:
                pass
            thread = getattr(consumer, "_thread", None)
            if thread is not None:
                try:
                    thread.join(timeout=0.5)
                except Exception:
                    pass

        renderer = self._renderer
        self._renderer = None
        if renderer is not None:
            try:
                renderer.shutdown()
            except Exception:
                pass

        if self._on_panel_closed_callback:
            try:
                self._on_panel_closed_callback()
            except Exception:
                pass
