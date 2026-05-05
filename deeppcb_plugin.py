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
)
from .panels import (
    BoardCreationPanel,
    BoardStatusPanel,
)


class RouteBoard(pcbnew.ActionPlugin):
    """DeepPCB Plugin for KiCad - Provides AI-powered PCB routing and placement."""

    # Class-level panel references to keep them alive
    _active_status_panel = None
    _active_creation_panel = None

    def defaults(self):
        self.name = "DeepPCB"
        self.description = "PoC of DeepPCB plugin"
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
            # Restore existing session - open status panel (docked in KiCad)
            board_id = session_data["board_id"]
            self._show_status_panel(
                board_id, api_url, project_name, project_directory, board
            )

        elif choice == "new":
            # Create new board - open creation panel (docked in KiCad)
            self._show_creation_panel(project_name, project_directory, api_url, board)

    def _try_show_existing_panel(self):
        """
        Check if there's an existing panel open and show/focus it.
        Returns True if an existing panel was shown, False otherwise.
        """

        if RouteBoard._active_status_panel:
            try:
                panel = RouteBoard._active_status_panel
                if panel.is_valid() and not panel._is_closing:
                    panel.show_panel()
                    return True
                else:
                    RouteBoard._active_status_panel = None
            except (RuntimeError, AttributeError, wx.PyDeadObjectError):
                RouteBoard._active_status_panel = None

        if RouteBoard._active_creation_panel:
            try:
                panel = RouteBoard._active_creation_panel
                if panel.is_valid() and not panel._is_closing:
                    panel.show_panel()
                    return True
                else:
                    RouteBoard._active_creation_panel = None
            except (RuntimeError, AttributeError, wx.PyDeadObjectError):
                RouteBoard._active_creation_panel = None

        return False

    def _show_status_panel(
        self, board_id, api_url, project_name, project_directory, board
    ):
        """Show the status panel (docked in KiCad's interface)."""
        if RouteBoard._active_creation_panel:
            try:
                RouteBoard._active_creation_panel.close_panel()
            except Exception:
                pass
            RouteBoard._active_creation_panel = None

        # Close any existing status panel
        if RouteBoard._active_status_panel:
            try:
                RouteBoard._active_status_panel.close_panel()
            except Exception:
                pass
            RouteBoard._active_status_panel = None

        def on_status_panel_closed():
            """Called when the status panel is closed by the user."""
            RouteBoard._active_status_panel = None

        def on_new_board_requested():
            """Called when user wants to create a new job."""
            self._show_creation_panel(project_name, project_directory, api_url, None)

        try:
            RouteBoard._active_status_panel = BoardStatusPanel(
                None,
                board_id,
                api_url,
                project_name,
                project_directory,
                on_panel_closed=on_status_panel_closed,
                on_new_board_requested=on_new_board_requested,
            )
        except Exception as e:
            wx.MessageBox(
                f"Failed to create status panel: {str(e)}\n\nThis may be due to KiCad AUI compatibility issues.",
                "Panel Error",
                wx.OK | wx.ICON_ERROR,
            )

    def _show_creation_panel(self, project_name, project_directory, api_url, board):
        """Show the creation panel (docked in KiCad's interface)."""
        if RouteBoard._active_status_panel:
            try:
                RouteBoard._active_status_panel.close_panel()
            except Exception:
                pass
            RouteBoard._active_status_panel = None

        if RouteBoard._active_creation_panel:
            try:
                RouteBoard._active_creation_panel.close_panel()
            except Exception:
                pass
            RouteBoard._active_creation_panel = None

        def on_board_created(board_id):
            save_session_data(project_directory, board_id)
            RouteBoard._active_creation_panel = None
            self._show_status_panel(
                board_id, api_url, project_name, project_directory, board
            )

        def on_cancelled():
            RouteBoard._active_creation_panel = None

        try:
            RouteBoard._active_creation_panel = BoardCreationPanel(
                None,
                project_name,
                project_directory,
                api_url,
                on_board_created=on_board_created,
                on_cancelled=on_cancelled,
            )
        except Exception as e:
            wx.MessageBox(
                f"Failed to create panel: {str(e)}\n\nThis may be due to KiCad AUI compatibility issues.",
                "Panel Error",
                wx.OK | wx.ICON_ERROR,
            )
