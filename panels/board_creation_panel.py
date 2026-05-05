"""
Board Creation Panel

This component integrates directly into KiCad's interface as a dockable panel.
"""

import wx
from wx import adv
import os
import pcbnew

from ..config import APP_VERSION, DEFAULT_TIMEOUT
from ..utils import poll_board_status
from ..helpers import DeepPCBClient, CreateBoardRequest
from ..dialogs.api_key_dialog import ApiKeyDialog, load_api_key_from_config
from ..dialogs.not_enough_credits_dialog import NotEnoughCreditsDialog
from .dockable_panel import KiCadDockablePanel, get_icon_path, is_dark_theme


class BoardCreationPanel(KiCadDockablePanel):
    PANEL_NAME = "deeppcb_creation"
    PANEL_CAPTION = "DeepPCB - New Board"
    DEFAULT_SIZE = (400, 350)
    MIN_SIZE = (400, 350)

    def __init__(
        self,
        parent,
        project_name,
        project_directory,
        api_url,
        on_board_created=None,
        on_cancelled=None,
    ):
        self.api_url = api_url
        self.project_name = project_name
        self.project_directory = project_directory
        self.api_key = load_api_key_from_config()
        self.job_type = "Routing"
        self.board_id = ""
        self.timeout = DEFAULT_TIMEOUT
        self.schematics_paths = []
        self.client = DeepPCBClient(self.api_url, self.api_key)
        self._is_closing = False

        self._on_board_created = on_board_created
        self._on_cancelled = on_cancelled

        super().__init__(parent)

        self._update_caption(f"DeepPCB - New Board (v{APP_VERSION})")

    def _update_caption(self, caption):
        if self._aui_mgr:
            pane = self._aui_mgr.GetPane(self.PANEL_NAME)
            if pane.IsOk():
                pane.Caption(caption)
                self._aui_mgr.Update()

    def create_ui(self):
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.AddSpacer(10)

        # Timeout slider
        timeout_sizer = wx.BoxSizer(wx.HORIZONTAL)
        timeout_label = wx.StaticText(self, label="Allocated Time:")
        self.timeout_slider = wx.Slider(
            self,
            wx.ID_ANY,
            DEFAULT_TIMEOUT,
            5,
            120,
            wx.DefaultPosition,
            (140, 50),
            wx.SL_HORIZONTAL,
        )
        self.timeout_value_label = wx.StaticText(self, label=f"{DEFAULT_TIMEOUT} min")
        self.timeout_slider.Bind(wx.EVT_SLIDER, self.on_timeout_change)

        timeout_sizer.Add(timeout_label, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 5)
        timeout_sizer.Add(self.timeout_slider, 1, wx.LEFT | wx.RIGHT | wx.TOP, 5)
        timeout_sizer.Add(
            self.timeout_value_label, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 5
        )
        main_sizer.Add(timeout_sizer, 0, wx.ALL | wx.EXPAND, 5)

        unlock_fee_label = wx.StaticText(
            self,
            label="Includes a 5 mins unlock fee. Non-refundable if you stop early.",
        )
        if is_dark_theme():
            unlock_fee_label.SetForegroundColour(wx.Colour(180, 180, 180))
        else:
            unlock_fee_label.SetForegroundColour(wx.Colour(120, 120, 120))
        font = unlock_fee_label.GetFont()
        font.SetPointSize(font.GetPointSize() - 1)
        unlock_fee_label.SetFont(font)
        main_sizer.Add(unlock_fee_label, 0, wx.LEFT | wx.RIGHT, 10)

        # Schematics section
        sch_label_sizer = wx.BoxSizer(wx.HORIZONTAL)
        sch_label = wx.StaticText(self, label="Schematics Files:")
        sch_label_sizer.Add(sch_label, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 5)
        sch_label_sizer.AddStretchSpacer(1)

        self.add_sch_btn = wx.Button(self, label="Add Files...")
        self.add_sch_btn.Bind(wx.EVT_BUTTON, self.on_add_schematics)
        sch_label_sizer.Add(self.add_sch_btn, 0, wx.RIGHT, 5)

        self.remove_sch_btn = wx.Button(self, label="Remove")
        self.remove_sch_btn.Bind(wx.EVT_BUTTON, self.on_remove_schematics)
        self.remove_sch_btn.Enable(False)
        sch_label_sizer.Add(self.remove_sch_btn, 0, wx.RIGHT, 5)

        main_sizer.Add(sch_label_sizer, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 5)

        self.sch_listbox = wx.ListBox(self, style=wx.LB_EXTENDED)
        self.sch_listbox.Bind(wx.EVT_LISTBOX, self.on_sch_selection_change)
        main_sizer.Add(self.sch_listbox, 1, wx.ALL | wx.EXPAND, 10)

        # Button bar
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")

        icon_size = (20, 20)
        key_icon_path = get_icon_path(assets_dir, "key_icon")

        if os.path.exists(key_icon_path):
            key_image = wx.Image(key_icon_path, wx.BITMAP_TYPE_PNG)
            key_image = key_image.Scale(
                icon_size[0], icon_size[1], wx.IMAGE_QUALITY_HIGH
            )
            key_bitmap = wx.Bitmap(key_image)
            self.settings_btn = wx.BitmapButton(self, bitmap=key_bitmap)
        else:
            self.settings_btn = wx.Button(self, label="⚙")
        self.settings_btn.SetToolTip("Configure API Key")
        self.settings_btn.Bind(wx.EVT_BUTTON, self.on_settings)
        button_sizer.Add(self.settings_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        deeppcb_link = adv.HyperlinkCtrl(
            self, wx.ID_ANY, "DeepPCB.ai", "https://deeppcb.ai"
        )
        button_sizer.Add(deeppcb_link, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        button_sizer.AddStretchSpacer(1)

        self.cancel_btn = wx.Button(self, label="Cancel")
        self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
        button_sizer.Add(self.cancel_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        self.start_btn = wx.Button(self, label="Start Routing")
        self.start_btn.Bind(wx.EVT_BUTTON, self.on_confirm)
        self.start_btn.Enable(False)
        button_sizer.Add(self.start_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        main_sizer.Add(button_sizer, 0, wx.ALL | wx.EXPAND, 5)

        self.SetSizer(main_sizer)
        self.Layout()

    def on_timeout_change(self, event):
        value = self.timeout_slider.GetValue()
        self.timeout_value_label.SetLabel(f"{value} min")
        self.timeout = value

    def on_confirm(self, event):
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
                f"Warning: Could not save board before upload: {str(e)}\n\nProceeding with existing file.",
                "Save Warning",
                wx.OK | wx.ICON_WARNING,
            )

        progress = wx.ProgressDialog(
            "Uploading Board",
            "Uploading board files to DeepPCB...",
            maximum=100,
            parent=self._frame,
            style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE | wx.PD_SMOOTH,
        )
        progress.Pulse("Preparing files for upload...")
        try:
            auth_check = self.client.get_credit_balance()
            if not auth_check.success and auth_check.status in (401, 403):
                progress.Destroy()
                wx.MessageBox(
                    f"Failed to create board: {auth_check.error}\n\n status code: {auth_check.status}\n\nPlease verify your API key, or generate a new one.",
                    "Error",
                    wx.OK | wx.ICON_ERROR,
                )
                return

            request = CreateBoardRequest(
                board_name=self.project_name,
                job_type=self.job_type,
                kicad_board_file_path=kicad_pcb_path,
                kicad_project_file_path=kicad_pro_path,
                kicad_schematics_file_paths=self.schematics_paths,
            )
            create_response = self.client.create_board(request)

            if create_response.success:
                self.board_id = create_response.board_id

                progress.Update(50, "Waiting for board to be created...")
                polling_result = poll_board_status(
                    self.client, self.board_id, "Pending", 90
                )
                progress.Update(60, "Checking eligibility...")
                status_response = self.client.check_board_status(self.board_id)

                if status_response.success and status_response.board:
                    board = status_response.board
                    if board.requires_credits:
                        print("Board requires credits")
                        board_cost = board.credits_cost_per_minute * self.timeout
                        balance_response = self.client.get_credit_balance()
                        if balance_response.success:
                            if balance_response.balance < board_cost:
                                progress.Destroy()
                                credits_dialog = NotEnoughCreditsDialog(self._frame)
                                credits_dialog.ShowModal()
                                credits_dialog.Destroy()
                                return
                        else:
                            wx.MessageBox(
                                f"Failed to get user balance: {balance_response.error}, status code: {balance_response.status}",
                                "Error",
                                wx.OK | wx.ICON_ERROR,
                            )
                            progress.Destroy()
                            return

                if polling_result["success"]:
                    progress.Update(75, f"Starting your {self.job_type} job...")
                    submit_response = self.client.submit_board(
                        self.board_id, self.timeout, self.job_type
                    )
                    if submit_response.success:
                        progress.Update(
                            85,
                            f"{self.job_type} Job submitted successfully, waiting for run to start...",
                        )
                        polling_result = poll_board_status(
                            self.client, self.board_id, "Running", 300
                        )
                        if polling_result["success"]:
                            progress.Update(92, "Waiting for the first revision...")
                            polling_result = poll_board_status(
                                self.client, self.board_id, "ReceivingRevisions", 600
                            )
                            if polling_result["success"]:
                                progress.Update(100, "First solutions received!")
                                progress.Destroy()

                                if self._on_board_created:
                                    self._on_board_created(self.board_id)

                                self.close_panel()
                                return
                            else:
                                wx.MessageBox(
                                    f"Timed out waiting for first solutions: {polling_result['message']}\nPlease visit the DeepPCB app for more details.",
                                    "Warning",
                                    wx.OK | wx.ICON_WARNING,
                                )
                                progress.Destroy()
                                if self._on_board_created:
                                    self._on_board_created(self.board_id)
                                self.close_panel()
                                return
                        else:
                            wx.MessageBox(
                                f"Failed to start job: {polling_result['message']}, status code: {polling_result['status']}",
                                "Error",
                                wx.OK | wx.ICON_ERROR,
                            )
                    else:
                        wx.MessageBox(
                            f"Failed to submit job: {submit_response.error}, status code: {submit_response.status}",
                            "Error",
                            wx.OK | wx.ICON_ERROR,
                        )
                else:
                    wx.MessageBox(
                        f"Your board failed to be created {polling_result['message']}, status code: {polling_result['status']}",
                        "Error",
                        wx.OK | wx.ICON_ERROR,
                    )
                progress.Destroy()
                return
            else:
                if create_response.status == 401:
                    wx.MessageBox(
                        f"Failed to create board: {create_response.error}\n\n status code: {create_response.status}\n\nPlease verify your API key, or generate a new one.",
                        "Error",
                        wx.OK | wx.ICON_ERROR,
                    )
                else:
                    wx.MessageBox(
                        f"Failed to create board: {create_response.error}, status code: {create_response.status}",
                        "Error",
                        wx.OK | wx.ICON_ERROR,
                    )
                progress.Destroy()
                return
        except Exception as e:
            progress.Destroy()
            wx.MessageBox(
                f"Error during upload: {str(e)}", "Error", wx.OK | wx.ICON_ERROR
            )

    def on_add_schematics(self, event):
        dlg = wx.FileDialog(
            self._frame,
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
            self.start_btn.Enable(self.sch_listbox.GetCount() > 0)
        dlg.Destroy()

    def on_remove_schematics(self, event):
        selections = list(self.sch_listbox.GetSelections())
        for idx in reversed(selections):
            self.schematics_paths.pop(idx)
            self.sch_listbox.Delete(idx)
        self.remove_sch_btn.Enable(self.sch_listbox.GetCount() > 0)
        self.start_btn.Enable(self.sch_listbox.GetCount() > 0)

    def on_sch_selection_change(self, event):
        self.remove_sch_btn.Enable(len(self.sch_listbox.GetSelections()) > 0)

    def on_settings(self, event):
        api_key_dialog = ApiKeyDialog(
            self._frame, self.project_name, self.project_directory, self.api_url
        )
        result = api_key_dialog.ShowModal()
        api_key_dialog.Destroy()

        if result == wx.ID_OK:
            self.api_key = load_api_key_from_config()
            self.client = DeepPCBClient(self.api_url, self.api_key)

    def on_cancel(self, event):
        if self._on_cancelled:
            self._on_cancelled()
        self.close_panel()

    def on_panel_close(self):
        """Cleanup when panel is closed - stops all processing."""
        self._is_closing = True

        # Notify the plugin that we're closed
        if self._on_cancelled:
            try:
                self._on_cancelled()
            except Exception:
                pass
