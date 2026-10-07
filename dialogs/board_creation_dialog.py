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
Modal pre-creation dialog.

Collects board name + schematics, runs the upload (POST /boards), and polls
status to Pending. On success the caller gets the new board_id back via
get_board_id(); on failure or cancel, the modal returns wx.ID_CANCEL.
"""

import os
import wx
import pcbnew

from ..custom_widgets import get_icon_path
from ..helpers import DeepPCBClient, CreateBoardRequest
from ..utils import poll_board_status
from .api_key_dialog import ApiKeyDialog, load_api_key_from_config


def _key_icon(parent):
    """Key icon button, in the variant that suits the user's theme."""
    assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
    path = get_icon_path(assets_dir, "key_icon")
    if not os.path.exists(path):
        return None
    img = wx.Image(path, wx.BITMAP_TYPE_PNG).Scale(20, 20, wx.IMAGE_QUALITY_HIGH)
    return wx.BitmapButton(parent, bitmap=wx.Bitmap(img))


class BoardCreationDialog(wx.Dialog):
    def __init__(self, parent, project_name, project_directory, api_url):
        super().__init__(
            parent,
            title="Create DeepPCB Board",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.project_name = project_name
        self.project_directory = project_directory
        self.api_url = api_url
        self.api_key = load_api_key_from_config()
        self.client = DeepPCBClient(self.api_url, self.api_key)
        self.schematics_paths = []
        self.board_id = ""

        self._build_ui()
        self.EnableLayoutAdaptation(True)

    def _build_ui(self):
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)
        s.AddSpacer(10)

        name_sizer = wx.BoxSizer(wx.HORIZONTAL)
        name_sizer.Add(
            wx.StaticText(panel, label="Board name:"),
            0,
            wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            5,
        )
        self.name_text = wx.TextCtrl(panel, value=self.project_name, size=(260, -1))
        name_sizer.Add(self.name_text, 1, wx.ALL, 5)
        s.Add(name_sizer, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)

        sch_header = wx.BoxSizer(wx.HORIZONTAL)
        sch_header.Add(
            wx.StaticText(panel, label="Schematics files:"),
            0,
            wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            5,
        )
        sch_header.AddStretchSpacer(1)
        self.add_btn = wx.Button(panel, label="Add Files…")
        self.add_btn.Bind(wx.EVT_BUTTON, self._on_add)
        sch_header.Add(self.add_btn, 0, wx.ALL, 5)
        self.remove_btn = wx.Button(panel, label="Remove")
        self.remove_btn.Bind(wx.EVT_BUTTON, self._on_remove)
        self.remove_btn.Enable(False)
        sch_header.Add(self.remove_btn, 0, wx.ALL, 5)
        s.Add(sch_header, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)

        self.sch_listbox = wx.ListBox(panel, style=wx.LB_EXTENDED)
        self.sch_listbox.Bind(wx.EVT_LISTBOX, self._on_select_changed)
        s.Add(self.sch_listbox, 1, wx.ALL | wx.EXPAND, 10)

        hint = wx.StaticText(
            panel,
            label=(
                "Files are uploaded immediately so the AI agent can start "
                "analysing your board. You start routing afterwards from the "
                "DeepPCB Status panel."
            ),
        )
        hint.SetForegroundColour(wx.Colour(120, 120, 120))
        hint.Wrap(440)
        font = hint.GetFont()
        font.SetPointSize(font.GetPointSize() - 1)
        font.SetStyle(wx.FONTSTYLE_ITALIC)
        hint.SetFont(font)
        s.Add(hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.settings_btn = _key_icon(panel) or wx.Button(panel, label="⚙")
        self.settings_btn.SetToolTip("Configure API Key")
        self.settings_btn.Bind(wx.EVT_BUTTON, self._on_settings)
        button_sizer.Add(self.settings_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        button_sizer.AddStretchSpacer(1)
        self.cancel_btn = wx.Button(panel, label="Cancel", id=wx.ID_CANCEL)
        button_sizer.Add(self.cancel_btn, 0, wx.ALL, 5)
        self.create_btn = wx.Button(panel, label="Create Board")
        self.create_btn.Bind(wx.EVT_BUTTON, self._on_create)
        button_sizer.Add(self.create_btn, 0, wx.ALL, 5)
        s.Add(button_sizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 5)

        panel.SetSizer(s)
        panel.Layout()
        best = panel.GetBestSize()
        dialog_sizer = wx.BoxSizer(wx.VERTICAL)
        dialog_sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(dialog_sizer)
        self.SetMinSize((max(520, best.GetWidth()), max(380, best.GetHeight())))
        self.Fit()
        self.Centre()

    def _on_add(self, _evt):
        dlg = wx.FileDialog(
            self,
            "Select KiCad Schematics Files",
            defaultDir=self.project_directory,
            wildcard="KiCad Schematics (*.kicad_sch)|*.kicad_sch",
            style=wx.FD_OPEN | wx.FD_MULTIPLE | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            for path in dlg.GetPaths():
                if path not in self.schematics_paths:
                    self.schematics_paths.append(path)
                    self.sch_listbox.Append(os.path.basename(path))
        dlg.Destroy()

    def _on_remove(self, _evt):
        for idx in reversed(list(self.sch_listbox.GetSelections())):
            self.schematics_paths.pop(idx)
            self.sch_listbox.Delete(idx)
        self.remove_btn.Enable(len(self.sch_listbox.GetSelections()) > 0)

    def _on_select_changed(self, _evt):
        self.remove_btn.Enable(len(self.sch_listbox.GetSelections()) > 0)

    def _on_settings(self, _evt):
        dialog = ApiKeyDialog(
            self, self.project_name, self.project_directory, self.api_url
        )
        result = dialog.ShowModal()
        dialog.Destroy()
        if result == wx.ID_OK:
            self.api_key = load_api_key_from_config()
            self.client = DeepPCBClient(self.api_url, self.api_key)

    def _on_create(self, _evt):
        board_name = (self.name_text.GetValue() or "").strip() or self.project_name

        kicad_pcb_path = os.path.join(
            self.project_directory, f"{self.project_name}.kicad_pcb"
        )
        kicad_pro_path = os.path.join(
            self.project_directory, f"{self.project_name}.kicad_pro"
        )

        try:
            board = pcbnew.GetBoard()
            if board:
                pcbnew.SaveBoard(kicad_pcb_path, board)
        except Exception as e:
            wx.MessageBox(
                f"Warning: Could not save board before upload: {e}\n\n"
                "Proceeding with existing file.",
                "Save Warning",
                wx.OK | wx.ICON_WARNING,
            )

        # A second submit would create a duplicate board.
        self.create_btn.Enable(False)
        progress = wx.ProgressDialog(
            "Uploading Board",
            "Uploading board files to DeepPCB…",
            maximum=100,
            parent=self,
            style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE | wx.PD_SMOOTH,
        )
        progress.Pulse("Verifying API key…")
        try:
            auth = self.client.get_credit_balance()
            # A TLS or connectivity failure is not an auth problem, but the
            # upload cannot succeed either - report it here rather than let
            # the same failure resurface as a confusing upload error.
            if not auth.success and auth.status in (401, 403, 408, 495, 503):
                progress.Destroy()
                if auth.status in (401, 403):
                    message = (
                        f"Auth failed: {auth.error} (status {auth.status}).\n\n"
                        "Please verify your API key."
                    )
                else:
                    message = f"Could not reach DeepPCB: {auth.error}"
                wx.MessageBox(message, "Error", wx.OK | wx.ICON_ERROR)
                self.create_btn.Enable(True)
                return

            progress.Update(20, "Uploading board files…")
            request = CreateBoardRequest(
                board_name=board_name,
                job_type="Routing",
                kicad_board_file_path=kicad_pcb_path,
                kicad_project_file_path=kicad_pro_path,
                kicad_schematics_file_paths=self.schematics_paths,
            )
            create_response = self.client.create_board(request)
            if not create_response.success:
                progress.Destroy()
                wx.MessageBox(
                    f"Failed to create board: {create_response.error} "
                    f"(status {create_response.status}).",
                    "Error",
                    wx.OK | wx.ICON_ERROR,
                )
                self.create_btn.Enable(True)
                return

            self.board_id = create_response.board_id

            progress.Update(60, "Waiting for board to be ready…")
            polling = poll_board_status(self.client, self.board_id, "Pending", 90)
            progress.Destroy()
            if not polling["success"]:
                # The board exists; only the status poll fell short. Hand it
                # back anyway rather than orphaning it server-side.
                wx.MessageBox(
                    f"The board was created but is not ready yet: "
                    f"{polling['message']} (status {polling['status']}).\n\n"
                    "Opening it anyway - the status panel will keep checking.",
                    "Warning",
                    wx.OK | wx.ICON_WARNING,
                )

            self.EndModal(wx.ID_OK)
        except Exception as e:
            try:
                progress.Destroy()
            except Exception:
                pass
            wx.MessageBox(
                f"Unexpected error during upload: {e}",
                "Error",
                wx.OK | wx.ICON_ERROR,
            )
            self.create_btn.Enable(True)

    def get_board_id(self) -> str:
        return self.board_id
