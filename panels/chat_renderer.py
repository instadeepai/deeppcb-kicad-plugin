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
Chat renderer.

Two backends behind one interface:

  - WebViewRenderer  : wx.html2.WebView + a minimal HTML/JS chat. Uses
                       window.deeppcb.postMessage(...) (or the platform
                       equivalent) to send approvals/suggestions back to
                       Python.
  - FallbackRenderer : wx.richtext.RichTextCtrl + native buttons for tool
                       approval / suggestion approval, used when WebView is
                       unavailable or fails to initialise.
"""

import json
import os
import uuid
from typing import Any, Callable, Dict, List, Optional

import wx
import wx.richtext


def _try_import_webview():
    try:
        import wx.html2 as html2  # noqa: F401

        return html2
    except Exception:
        return None


def _destroy_webview_safely(wv):
    try:
        wv.Destroy()
    except Exception:
        # Already-dead WebView: nothing left to destroy.
        pass


class ChatRendererBase(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)
        self.on_send: Optional[Callable[[str], None]] = None
        self.on_apply_suggestions: Optional[
            Callable[[str, List[Dict[str, Any]], List[str]], None]
        ] = None

    def add_user_message(self, text: str) -> None:
        raise NotImplementedError

    def start_assistant_message(self) -> str:
        raise NotImplementedError

    def append_assistant_delta(self, message_id: str, text: str) -> None:
        raise NotImplementedError

    def finalize_assistant_message(
        self,
        message_id: str,
        text: str,
        tool_calls: Optional[List[Dict[str, Any]]],
        requires_approval: bool,
        suggestions: Optional[List[Dict[str, Any]]],
    ) -> None:
        raise NotImplementedError

    def set_status(self, text: Optional[str]) -> None:
        raise NotImplementedError

    def set_error(self, text: Optional[str]) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError

    def shutdown(self) -> None:
        """Stop background work / release platform resources before destroy."""
        pass


def build_renderer(parent) -> ChatRendererBase:
    html2 = _try_import_webview()
    if html2 is not None:
        try:
            return WebViewRenderer(parent, html2)
        except Exception:
            # Fall through to the plain-text fallback renderer below.
            pass
    return FallbackRenderer(parent)


_chat_html_cache: Optional[str] = None


def _load_chat_html() -> str:
    """Read the chat page from assets/chat.html, cached after the first read."""
    global _chat_html_cache
    if _chat_html_cache is None:
        path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "assets", "chat.html"
        )
        with open(path, encoding="utf-8") as f:
            _chat_html_cache = f.read()
    return _chat_html_cache


class WebViewRenderer(ChatRendererBase):
    def __init__(self, parent, html2_module):
        super().__init__(parent)
        self._html2 = html2_module
        sizer = wx.BoxSizer(wx.VERTICAL)

        self._webview = html2_module.WebView.New(self)
        if self._webview is None:
            raise RuntimeError("WebView.New returned None")

        sizer.Add(self._webview, 1, wx.EXPAND)

        input_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self._input = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self._input.Bind(wx.EVT_TEXT_ENTER, self._on_enter)
        self._send_btn = wx.Button(self, label="Send")
        self._send_btn.Bind(wx.EVT_BUTTON, self._on_send_click)
        input_sizer.Add(self._input, 1, wx.ALL | wx.EXPAND, 4)
        input_sizer.Add(self._send_btn, 0, wx.ALL, 4)
        sizer.Add(input_sizer, 0, wx.EXPAND)

        self.SetSizer(sizer)

        self._ready = False
        self._pending_ops: List[str] = []

        self._webview.Bind(html2_module.EVT_WEBVIEW_LOADED, self._on_loaded)
        self._webview.Bind(html2_module.EVT_WEBVIEW_NAVIGATING, self._on_navigating)
        try:
            self._webview.Bind(
                html2_module.EVT_WEBVIEW_SCRIPT_MESSAGE_RECEIVED,
                self._on_script_message,
            )
            self._webview.AddScriptMessageHandler("deeppcb")
        except Exception:
            pass

        self._webview.SetPage(_load_chat_html(), "about:blank")

    def _on_loaded(self, event):
        if self._webview is None:
            return
        self._ready = True
        for op in self._pending_ops:
            self._exec(op)
        self._pending_ops = []

    def _on_navigating(self, event):
        if self._webview is None:
            return
        url = event.GetURL() or ""
        if url.startswith("deeppcb://msg/"):
            event.Veto()
            try:
                from urllib.parse import unquote

                payload = unquote(url[len("deeppcb://msg/") :])
                self._handle_script_payload(payload)
            except Exception:
                # A malformed deeppcb:// payload is ignored.
                pass

    def _on_script_message(self, event):
        if self._webview is None:
            return
        try:
            self._handle_script_payload(event.GetString())
        except Exception:
            # A malformed script message must not kill the renderer.
            pass

    def _handle_script_payload(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if msg.get("kind") == "applySuggestions":
            if self.on_apply_suggestions:
                self.on_apply_suggestions(
                    msg.get("messageId") or "",
                    msg.get("decisions") or [],
                    msg.get("approvedTitles") or [],
                )

    def _on_enter(self, _evt):
        self._submit()

    def _on_send_click(self, _evt):
        self._submit()

    def _submit(self):
        text = (self._input.GetValue() or "").strip()
        if not text:
            return
        self._input.SetValue("")
        if self.on_send:
            self.on_send(text)

    def _exec(self, js: str) -> None:
        if not self._ready:
            self._pending_ops.append(js)
            return
        wv = self._webview
        if wv is None:
            return
        try:
            if hasattr(wv, "RunScriptAsync"):
                wv.RunScriptAsync(js)
            else:
                wv.RunScript(js)
        except Exception:
            # A closing WebView rejects scripts; the next send retries.
            pass

    def _send_op(self, op: Dict[str, Any]) -> None:
        encoded = json.dumps(op).replace("\\", "\\\\").replace("'", "\\'")
        self._exec(f"window.deeppcbApply('{encoded}');")

    def add_user_message(self, text: str) -> None:
        self._send_op(
            {
                "op": "add",
                "message": {
                    "id": str(uuid.uuid4()),
                    "role": "user",
                    "content": text,
                },
            }
        )

    def start_assistant_message(self) -> str:
        message_id = str(uuid.uuid4())
        self._send_op(
            {
                "op": "add",
                "message": {
                    "id": message_id,
                    "role": "assistant",
                    "content": "",
                },
            }
        )
        return message_id

    def append_assistant_delta(self, message_id: str, text: str) -> None:
        self._send_op({"op": "delta", "id": message_id, "delta": text})

    def finalize_assistant_message(
        self,
        message_id: str,
        text: str,
        tool_calls: Optional[List[Dict[str, Any]]],
        requires_approval: bool,
        suggestions: Optional[List[Dict[str, Any]]],
    ) -> None:
        self._send_op(
            {
                "op": "finalize",
                "id": message_id,
                "content": text,
                "toolCalls": tool_calls or [],
                "requiresApproval": requires_approval,
                "suggestions": suggestions or [],
            }
        )

    def set_status(self, text: Optional[str]) -> None:
        self._send_op({"op": "status", "text": text or ""})

    def set_error(self, text: Optional[str]) -> None:
        self._send_op({"op": "error", "text": text or ""})

    def clear(self) -> None:
        self._send_op({"op": "clear"})

    def shutdown(self) -> None:
        self._ready = False
        self._pending_ops = []
        wv = self._webview
        self._webview = None
        if wv is None:
            return
        for evt_name in (
            "EVT_WEBVIEW_LOADED",
            "EVT_WEBVIEW_NAVIGATING",
            "EVT_WEBVIEW_NAVIGATED",
            "EVT_WEBVIEW_ERROR",
            "EVT_WEBVIEW_SCRIPT_MESSAGE_RECEIVED",
            "EVT_WEBVIEW_SCRIPT_RESULT",
        ):
            evt = getattr(self._html2, evt_name, None)
            if evt is None:
                continue
            try:
                wv.Unbind(evt)
            except Exception:
                pass
        try:
            wv.RemoveScriptMessageHandler("deeppcb")
        except Exception:
            pass
        try:
            wv.Stop()
        except Exception:
            pass
        try:
            wv.SetPage("<html><body></body></html>", "about:blank")
        except Exception:
            pass
        try:
            wv.Hide()
        except Exception:
            pass
        try:
            sizer = self.GetSizer()
            if sizer is not None:
                sizer.Detach(wv)
        except Exception:
            pass
        try:
            hidden_parent = wx.GetApp().GetTopWindow() if wx.GetApp() else None
            if hidden_parent is not None:
                wv.Reparent(hidden_parent)
        except Exception:
            pass
        wx.CallAfter(_destroy_webview_safely, wv)


class FallbackRenderer(ChatRendererBase):
    def __init__(self, parent):
        super().__init__(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self._text = wx.richtext.RichTextCtrl(
            self, style=wx.richtext.RE_READONLY | wx.richtext.RE_MULTILINE
        )
        sizer.Add(self._text, 1, wx.EXPAND | wx.ALL, 2)

        self._action_panel = wx.Panel(self)
        self._action_sizer = wx.BoxSizer(wx.VERTICAL)
        self._action_panel.SetSizer(self._action_sizer)
        sizer.Add(self._action_panel, 0, wx.EXPAND | wx.ALL, 2)

        input_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self._input = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self._input.Bind(wx.EVT_TEXT_ENTER, self._on_enter)
        self._send_btn = wx.Button(self, label="Send")
        self._send_btn.Bind(wx.EVT_BUTTON, self._on_send_click)
        input_sizer.Add(self._input, 1, wx.ALL | wx.EXPAND, 4)
        input_sizer.Add(self._send_btn, 0, wx.ALL, 4)
        sizer.Add(input_sizer, 0, wx.EXPAND)

        self.SetSizer(sizer)
        self._messages: Dict[str, Dict[str, Any]] = {}
        self._status: Optional[str] = None
        self._error: Optional[str] = None

    def _redraw(self) -> None:
        self._text.SetValue("")
        for entry in self._messages.values():
            role = "You" if entry["role"] == "user" else "Cooper"
            self._text.BeginBold()
            self._text.WriteText(f"{role}:\n")
            self._text.EndBold()
            self._text.WriteText((entry.get("content") or "") + "\n\n")
        if self._status:
            self._text.WriteText(f"[{self._status}]\n")
        if self._error:
            self._text.BeginTextColour(wx.Colour(200, 60, 60))
            self._text.WriteText(f"Error: {self._error}\n")
            self._text.EndTextColour()
        self._text.ShowPosition(self._text.GetLastPosition())

    def _on_enter(self, _evt):
        self._submit()

    def _on_send_click(self, _evt):
        self._submit()

    def _submit(self):
        text = (self._input.GetValue() or "").strip()
        if not text:
            return
        self._input.SetValue("")
        if self.on_send:
            self.on_send(text)

    def _clear_action_panel(self) -> None:
        for child in list(self._action_panel.GetChildren()):
            child.Destroy()
        self._action_panel.Layout()
        self.Layout()

    def add_user_message(self, text: str) -> None:
        mid = str(uuid.uuid4())
        self._messages[mid] = {"role": "user", "content": text}
        self._redraw()

    def start_assistant_message(self) -> str:
        mid = str(uuid.uuid4())
        self._messages[mid] = {"role": "assistant", "content": ""}
        self._redraw()
        return mid

    def append_assistant_delta(self, message_id: str, text: str) -> None:
        entry = self._messages.get(message_id)
        if not entry:
            return
        entry["content"] = (entry.get("content") or "") + text
        self._redraw()

    def finalize_assistant_message(
        self,
        message_id: str,
        text: str,
        tool_calls: Optional[List[Dict[str, Any]]],
        requires_approval: bool,
        suggestions: Optional[List[Dict[str, Any]]],
    ) -> None:
        entry = self._messages.get(message_id)
        if entry is None:
            entry = {"role": "assistant"}
            self._messages[message_id] = entry
        entry["content"] = text
        entry["tool_calls"] = tool_calls or []
        self._redraw()

        self._clear_action_panel()
        if suggestions:
            self._render_suggestions(message_id, suggestions)
        self._action_panel.Layout()
        self.Layout()

    def _render_suggestions(
        self, message_id: str, suggestions: List[Dict[str, Any]]
    ) -> None:
        header = wx.StaticText(self._action_panel, label="Suggested actions:")
        self._action_sizer.Add(header, 0, wx.ALL, 4)
        checks = []
        for s in suggestions:
            label = s.get("title") or s.get("tool") or "(unnamed)"
            cb = wx.CheckBox(self._action_panel, label=label)
            cb.SetValue(True)
            self._action_sizer.Add(cb, 0, wx.LEFT | wx.RIGHT, 8)
            checks.append((cb, s))
        apply_btn = wx.Button(self._action_panel, label="Apply selected")

        def _apply(_evt):
            decisions = []
            approved_titles = []
            for cb, s in checks:
                args = s.get("args")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                approved = bool(cb.GetValue())
                decisions.append(
                    {"tool": s.get("tool"), "args": args or {}, "approved": approved}
                )
                if approved:
                    approved_titles.append(s.get("title") or s.get("tool"))
            self._clear_action_panel()
            if self.on_apply_suggestions:
                self.on_apply_suggestions(message_id, decisions, approved_titles)

        apply_btn.Bind(wx.EVT_BUTTON, _apply)
        self._action_sizer.Add(apply_btn, 0, wx.ALL, 4)

    def set_status(self, text: Optional[str]) -> None:
        self._status = text or None
        self._redraw()

    def set_error(self, text: Optional[str]) -> None:
        self._error = text or None
        self._redraw()

    def clear(self) -> None:
        self._messages = {}
        self._status = None
        self._error = None
        self._clear_action_panel()
        self._redraw()

    def shutdown(self) -> None:
        try:
            self._clear_action_panel()
        except Exception:
            pass
