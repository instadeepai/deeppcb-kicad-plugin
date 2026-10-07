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

import pcbnew
import os
import wx

from .config import API_URL
from .dialogs import (
    check_and_show_session_dialog,
    save_session_data,
    ApiKeyDialog,
    load_api_key_from_config,
    BoardCreationDialog,
)
from .panels import (
    BoardStatusPanel,
    ChatPanel,
)
from .panels.chat_panel import INITIAL_ANALYSIS_PROMPT as INITIAL_CHAT_PROMPT


class RouteBoard(pcbnew.ActionPlugin):
    """DeepPCB Plugin for KiCad - Provides AI-powered PCB routing and placement."""

    _active_status_panel = None
    _active_chat_panel = None

    def defaults(self):
        self.name = "DeepPCB"
        self.description = "Route your board automatically using DeepPCB"
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(os.path.dirname(__file__), "icon.png")

    def Run(self):
        if self._try_show_existing_panel():
            return

        board = pcbnew.GetBoard()
        full_path = board.GetFileName()
        basename = os.path.basename(full_path)
        project_name = os.path.splitext(basename)[0]
        project_directory = os.path.dirname(full_path)
        api_url = API_URL

        if not load_api_key_from_config():
            api_key_dialog = ApiKeyDialog(
                None, project_name, project_directory, api_url
            )
            result = api_key_dialog.ShowModal()
            api_key_dialog.Destroy()

            if result == wx.ID_CANCEL:
                return

            if not load_api_key_from_config():
                wx.MessageBox(
                    "API key is required to use DeepPCB", "Error", wx.OK | wx.ICON_ERROR
                )
                return

        choice, session_data = check_and_show_session_dialog(
            None, project_name, project_directory
        )

        if choice is None:
            return

        if choice == "restore" and session_data and session_data.get("board_id"):
            self._open_panels(
                session_data["board_id"],
                api_url,
                project_name,
                project_directory,
                initial_user_message=None,
            )
        elif choice == "new":
            dialog = BoardCreationDialog(None, project_name, project_directory, api_url)
            result = dialog.ShowModal()
            board_id = dialog.get_board_id()
            dialog.Destroy()
            if result != wx.ID_OK or not board_id:
                return
            save_session_data(project_directory, board_id)
            self._open_panels(
                board_id,
                api_url,
                project_name,
                project_directory,
                initial_user_message=INITIAL_CHAT_PROMPT,
            )

    @staticmethod
    def _live_panel(attr):
        """Return the panel held in `attr` if it is still usable, else None."""
        panel = getattr(RouteBoard, attr)
        if not panel:
            return None
        try:
            if panel.is_valid() and not panel._is_closing:
                return panel
        except (RuntimeError, AttributeError):
            pass
        setattr(RouteBoard, attr, None)
        return None

    def _try_show_existing_panel(self):
        """Refocus what is open, and reopen whichever panel is missing.

        The panels are independent: treating 'one is alive' as 'nothing to do'
        left a closed panel unreachable for the rest of the session.
        """
        status = self._live_panel("_active_status_panel")
        chat = self._live_panel("_active_chat_panel")
        if not status and not chat:
            return False

        if status:
            status.show_panel()
        if chat:
            chat.show_panel()

        source = status or chat
        if not status:
            self._show_status_panel(
                source.board_id,
                API_URL,
                source.project_name,
                source.project_directory,
            )
        elif not chat:
            self._show_chat_panel(
                source.board_id,
                API_URL,
                source.project_name,
                source.project_directory,
                initial_user_message=None,
            )
        self._wire_chat_to_status()
        return True

    def _open_panels(
        self,
        board_id,
        api_url,
        project_name,
        project_directory,
        initial_user_message,
    ):
        self._show_status_panel(board_id, api_url, project_name, project_directory)
        self._show_chat_panel(
            board_id,
            api_url,
            project_name,
            project_directory,
            initial_user_message=initial_user_message,
        )
        self._wire_chat_to_status()

    def _show_status_panel(self, board_id, api_url, project_name, project_directory):
        if RouteBoard._active_status_panel:
            try:
                RouteBoard._active_status_panel.close_panel()
            except Exception:
                pass
            RouteBoard._active_status_panel = None

        def on_status_panel_closed():
            RouteBoard._active_status_panel = None

        def on_new_board_requested():
            dialog = BoardCreationDialog(None, project_name, project_directory, api_url)
            result = dialog.ShowModal()
            new_board_id = dialog.get_board_id()
            dialog.Destroy()
            if result != wx.ID_OK or not new_board_id:
                return
            save_session_data(project_directory, new_board_id)
            self._open_panels(
                new_board_id,
                api_url,
                project_name,
                project_directory,
                initial_user_message=INITIAL_CHAT_PROMPT,
            )

        def on_api_key_changed():
            chat = RouteBoard._active_chat_panel
            if chat:
                try:
                    chat.reload_credentials()
                except Exception:
                    # Best-effort: Cooper keeps its old key until reopened.
                    pass

        def on_open_chat_requested():
            self._show_chat_panel(
                board_id,
                api_url,
                project_name,
                project_directory,
                initial_user_message=None,
            )
            self._wire_chat_to_status()

        try:
            RouteBoard._active_status_panel = BoardStatusPanel(
                None,
                board_id,
                api_url,
                project_name,
                project_directory,
                on_panel_closed=on_status_panel_closed,
                on_new_board_requested=on_new_board_requested,
                on_open_chat_requested=on_open_chat_requested,
                on_api_key_changed=on_api_key_changed,
            )
        except Exception as e:
            wx.MessageBox(
                f"Failed to create status panel: {str(e)}",
                "Panel Error",
                wx.OK | wx.ICON_ERROR,
            )

    def _show_chat_panel(
        self,
        board_id,
        api_url,
        project_name,
        project_directory,
        initial_user_message=None,
    ):
        existing = RouteBoard._active_chat_panel
        if existing:
            try:
                if existing.is_valid() and not existing._is_closing:
                    if getattr(existing, "board_id", None) == board_id:
                        existing.show_panel()
                        return
                    # Different board: a reused panel would keep the previous
                    # board's conversation and skip this one's seed analysis.
                    existing.close_panel()
            except Exception:
                pass
            RouteBoard._active_chat_panel = None

        def on_chat_closed():
            RouteBoard._active_chat_panel = None

        try:
            RouteBoard._active_chat_panel = ChatPanel(
                None,
                board_id,
                api_url,
                project_name,
                project_directory,
                on_panel_closed=on_chat_closed,
                initial_user_message=initial_user_message,
            )
        except Exception as e:
            wx.MessageBox(
                f"Failed to open chat panel: {str(e)}",
                "Chat Error",
                wx.OK | wx.ICON_ERROR,
            )

    def _wire_chat_to_status(self):
        chat = RouteBoard._active_chat_panel
        status = RouteBoard._active_status_panel
        if not chat or not status:
            return
        try:
            chat.set_on_assistant_turn_complete(status.trigger_chat_turn_refresh)
        except Exception:
            # Best-effort wiring: both panels still work unlinked.
            pass
