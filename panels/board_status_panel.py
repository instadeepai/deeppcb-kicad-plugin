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
Board Status Panel

This panel monitors the board status without blocking the main KiCad view.
It integrates directly into KiCad's interface as a dockable panel.
"""

import traceback

import wx
import wx.adv
import os
from typing import Optional

from ..config import BOARDS_URL, APP_VERSION, DEFAULT_TIMEOUT
from ..custom_widgets import RoundedPanel
from ..utils import (
    load_and_render_board,
    calculate_remaining_time,
)
from ..helpers import DeepPCBClient, DeepPCBBoard
from ..helpers import routing_jobs
from ..helpers.contracts import (
    ACTIVE_STATES,
    PRE_RUN_STATES,
    TERMINAL_STATES,
    Revision,
)
from ..helpers.solution_sync import SolutionSync
from ..dialogs.api_key_dialog import ApiKeyDialog, load_api_key_from_config
from ..dialogs.not_enough_credits_dialog import NotEnoughCreditsDialog
from ..dialogs.rating_dialog import BoardRatingDialog
from .dockable_panel import KiCadDockablePanel, get_icon_path, is_dark_theme


class BoardStatusPanel(KiCadDockablePanel):
    """
    A dockable panel that displays board status and allows interaction
    Integrates with KiCad's AUI.
    """

    PANEL_NAME = "deeppcb_status"
    PANEL_CAPTION = "DeepPCB Status"
    DEFAULT_SIZE = (450, 340)
    MIN_SIZE = (450, 340)

    def __init__(
        self,
        parent,
        board_id,
        swagger_url,
        project_name,
        project_directory,
        on_panel_closed=None,
        on_new_board_requested=None,
        on_open_chat_requested=None,
        on_api_key_changed=None,
    ):
        self.board_id = board_id
        self.swagger_url = swagger_url
        self.project_name = project_name
        self.project_directory = project_directory
        self.api_key = load_api_key_from_config()
        self.client = DeepPCBClient(self.swagger_url, self.api_key)
        self.deeppcb_board: DeepPCBBoard = None
        self._is_closing = False
        self._rating_prompt_shown = False
        self._on_panel_closed_callback = on_panel_closed
        self._on_new_board_requested = on_new_board_requested
        self._on_open_chat_requested = on_open_chat_requested
        self._on_api_key_changed = on_api_key_changed
        self._pending_solution_revision: Optional[int] = None
        # None means follow the latest revision.
        self._user_selected_revision: Optional[str] = None
        self._last_seen_status: Optional[str] = None
        self._last_layout_key = None
        self._sync = SolutionSync(
            self.client,
            board_id,
            project_name,
            project_directory,
            on_status=self.load_board_status,
            on_render=self._render_revision,
            on_solution_available=self._offer_solution,
            ui_sink=wx.CallAfter,
            is_cancelled=lambda: self._is_closing,
        )
        self.timeout = DEFAULT_TIMEOUT
        self.job_type = "Routing"

        super().__init__(parent)

        self._update_caption(f"DeepPCB Status (v{APP_VERSION})")

        self.load_board_status()
        self._sync.start()

    def _update_caption(self, caption):
        """Update the panel caption in KiCad's AUI."""
        if self._aui_mgr:
            pane = self._aui_mgr.GetPane(self.PANEL_NAME)
            if pane.IsOk():
                pane.Caption(caption)
                self._aui_mgr.Update()

    def _get_adjusted_color(self, base_color, adjustment=15):
        """Get a lighter or darker version of a color based on its brightness."""
        r, g, b = base_color.Red(), base_color.Green(), base_color.Blue()

        if is_dark_theme():
            new_r = min(255, r + adjustment)
            new_g = min(255, g + adjustment)
            new_b = min(255, b + adjustment)
        else:
            new_r = max(0, r - adjustment)
            new_g = max(0, g - adjustment)
            new_b = max(0, b - adjustment)

        return wx.Colour(new_r, new_g, new_b)

    def create_ui(self):
        """Create the panel UI."""
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        self._build_status_card(main_sizer)
        self._build_routing_setup(main_sizer)
        self._build_notices(main_sizer)
        self._build_advanced_pane(main_sizer)
        self._build_toolbar(main_sizer)

        self.SetSizer(main_sizer)
        self.Layout()

    def _build_status_card(self, main_sizer) -> None:
        """Board status, remaining time, and the Stop / New Job button."""
        system_bg = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)
        panel_bg_color = self._get_adjusted_color(system_bg, 20)
        border_color = self._get_adjusted_color(system_bg, 50)

        status_panel = RoundedPanel(
            self,
            bg_color=panel_bg_color,
            border_color=border_color,
            border_width=1,
            radius=6,
        )
        status_panel_sizer = wx.BoxSizer(wx.VERTICAL)

        status_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.status_label = wx.StaticText(status_panel, label="Board Status:")
        self.status_label.SetBackgroundColour(panel_bg_color)

        self.status_text = wx.StaticText(status_panel, label="Loading...")
        self.status_text.SetBackgroundColour(panel_bg_color)
        font = self.status_text.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.status_text.SetFont(font)

        assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
        loading_gif_path = os.path.join(assets_dir, "logo_loading.gif")
        self.loading_animation = wx.adv.AnimationCtrl(status_panel, wx.ID_ANY)
        self.loading_animation.SetBackgroundColour(panel_bg_color)
        if os.path.exists(loading_gif_path):
            self.loading_animation.LoadFile(loading_gif_path)
        self.loading_animation.Hide()

        status_sizer.Add(self.status_label, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        status_sizer.Add(self.status_text, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        status_sizer.Add(
            self.loading_animation, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 2
        )

        status_details_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.remaining_time_label = wx.StaticText(status_panel, label="")
        self.remaining_time_label.SetBackgroundColour(panel_bg_color)
        status_details_sizer.Add(self.remaining_time_label, 0, wx.ALL, 5)

        self.stop_resume_btn = wx.Button(status_panel, label="Stop")
        self.stop_resume_btn.Bind(wx.EVT_BUTTON, self.on_stop_resume)
        self.stop_resume_btn.Enable(False)
        status_details_sizer.Add(self.stop_resume_btn, 0, wx.ALL, 5)

        status_panel_sizer.Add(status_sizer, 0, wx.ALL | wx.CENTER, 5)
        status_panel_sizer.Add(status_details_sizer, 0, wx.ALL | wx.CENTER, 5)
        status_panel.SetSizer(status_panel_sizer)

        main_sizer.Add(status_panel, 0, wx.ALL | wx.EXPAND, 10)

    def _build_routing_setup(self, main_sizer) -> None:
        """Allocated time and Start Routing. Only shown before a run starts."""
        self.routing_setup_panel = wx.Panel(self)
        setup_sizer = wx.BoxSizer(wx.VERTICAL)

        timeout_sizer = wx.BoxSizer(wx.HORIZONTAL)
        timeout_label = wx.StaticText(self.routing_setup_panel, label="Allocated Time:")
        self.timeout_slider = wx.Slider(
            self.routing_setup_panel,
            wx.ID_ANY,
            DEFAULT_TIMEOUT,
            5,
            120,
            wx.DefaultPosition,
            (140, 50),
            wx.SL_HORIZONTAL,
        )
        self.timeout_value_label = wx.StaticText(
            self.routing_setup_panel, label=f"{DEFAULT_TIMEOUT} min"
        )
        self.timeout_slider.Bind(wx.EVT_SLIDER, self.on_timeout_change)
        timeout_sizer.Add(timeout_label, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 5)
        timeout_sizer.Add(self.timeout_slider, 1, wx.LEFT | wx.RIGHT | wx.TOP, 5)
        timeout_sizer.Add(
            self.timeout_value_label, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 5
        )
        setup_sizer.Add(timeout_sizer, 0, wx.ALL | wx.EXPAND, 5)

        unlock_fee_label = wx.StaticText(
            self.routing_setup_panel,
            label="Includes a 5 mins unlock fee. Non-refundable if you stop early.",
        )
        if is_dark_theme():
            unlock_fee_label.SetForegroundColour(wx.Colour(180, 180, 180))
        else:
            unlock_fee_label.SetForegroundColour(wx.Colour(120, 120, 120))
        font = unlock_fee_label.GetFont()
        font.SetPointSize(font.GetPointSize() - 1)
        unlock_fee_label.SetFont(font)
        setup_sizer.Add(unlock_fee_label, 0, wx.LEFT | wx.RIGHT, 10)

        start_row = wx.BoxSizer(wx.HORIZONTAL)
        start_row.AddStretchSpacer(1)
        self.start_routing_btn = wx.Button(
            self.routing_setup_panel, label="Start Routing"
        )
        self.start_routing_btn.Bind(wx.EVT_BUTTON, self.on_start_routing)
        start_row.Add(self.start_routing_btn, 0, wx.ALL, 5)
        setup_sizer.Add(start_row, 0, wx.ALL | wx.EXPAND, 5)

        self.routing_setup_panel.SetSizer(setup_sizer)
        self.routing_setup_panel.Hide()
        main_sizer.Add(self.routing_setup_panel, 0, wx.ALL | wx.EXPAND, 5)

    def _build_notices(self, main_sizer) -> None:
        """The do-not-edit warning, the current solution, and the pending-solution offer."""
        self.info_label = wx.StaticText(
            self,
            label="Please avoid modifying the board while the job is running. Use Stop to make changes.",
        )
        self.info_label.SetForegroundColour(wx.Colour(100, 100, 100))
        font = self.info_label.GetFont()
        font.SetPointSize(font.GetPointSize() - 1)
        font.SetStyle(wx.FONTSTYLE_ITALIC)
        self.info_label.SetFont(font)
        self.info_label.Wrap(400)
        self.info_label.Hide()
        main_sizer.Add(self.info_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        solution_display_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.current_solution_label = wx.StaticText(self, label="Current Solution:")
        self.current_solution_value = wx.StaticText(self, label="--")
        font = self.current_solution_value.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.current_solution_value.SetFont(font)
        solution_display_sizer.Add(
            self.current_solution_label, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5
        )
        solution_display_sizer.Add(
            self.current_solution_value, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5
        )
        main_sizer.Add(solution_display_sizer, 0, wx.LEFT | wx.RIGHT, 5)

        self.pending_solution_label = wx.StaticText(self, label="")
        if is_dark_theme():
            self.pending_solution_label.SetForegroundColour(wx.Colour(180, 180, 180))
        else:
            self.pending_solution_label.SetForegroundColour(wx.Colour(120, 120, 120))
        pending_font = self.pending_solution_label.GetFont()
        pending_font.SetPointSize(pending_font.GetPointSize() - 1)
        pending_font.SetStyle(wx.FONTSTYLE_ITALIC)
        self.pending_solution_label.SetFont(pending_font)
        self.pending_solution_label.Wrap(400)
        self.pending_solution_label.Hide()
        main_sizer.Add(
            self.pending_solution_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10
        )

    def _build_advanced_pane(self, main_sizer) -> None:
        """Collapsible pane: the Solutions dropdown and Render Solution."""
        self.advanced_pane = wx.CollapsiblePane(self, label="Advanced Settings")
        self.advanced_pane.Bind(
            wx.EVT_COLLAPSIBLEPANE_CHANGED, self.on_advanced_pane_changed
        )
        advanced_win = self.advanced_pane.GetPane()
        advanced_sizer = wx.BoxSizer(wx.VERTICAL)

        revisions_list_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.revisions_label = wx.StaticText(advanced_win, label="Solutions: ")
        self.revisions_list = wx.ComboBox(
            advanced_win,
            wx.ID_ANY,
            "----",
            wx.DefaultPosition,
            (100, -1),
            ["No solutions available"],
            0,
        )
        self.revisions_list.Bind(wx.EVT_COMBOBOX, self.on_solution_selected)
        self.latest_solution = None
        revisions_list_sizer.Add(
            self.revisions_label, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5
        )
        revisions_list_sizer.Add(self.revisions_list, 0, wx.ALL, 5)
        revisions_list_sizer.AddStretchSpacer(1)

        self.render_btn = wx.Button(advanced_win, label="Render Solution")
        self.render_btn.Bind(wx.EVT_BUTTON, self.on_download)
        revisions_list_sizer.Add(self.render_btn, 0, wx.ALL, 5)

        advanced_sizer.Add(revisions_list_sizer, 0, wx.ALL | wx.EXPAND, 5)

        advanced_win.SetSizer(advanced_sizer)
        main_sizer.Add(self.advanced_pane, 0, wx.ALL | wx.EXPAND, 5)

    def _build_toolbar(self, main_sizer) -> None:
        """Settings, Refresh, Cooper, the board link, and Rate."""
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

        refresh_icon_path = get_icon_path(assets_dir, "refresh_icon")
        if os.path.exists(refresh_icon_path):
            refresh_image = wx.Image(refresh_icon_path, wx.BITMAP_TYPE_PNG)
            refresh_image = refresh_image.Scale(
                icon_size[0], icon_size[1], wx.IMAGE_QUALITY_HIGH
            )
            refresh_bitmap = wx.Bitmap(refresh_image)
            self.refresh_btn = wx.BitmapButton(self, bitmap=refresh_bitmap)
        else:
            self.refresh_btn = wx.Button(self, label="↻")
        self.refresh_btn.SetToolTip("Refresh")
        self.refresh_btn.Bind(wx.EVT_BUTTON, self.on_refresh)
        button_sizer.Add(self.refresh_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        self.chat_btn = wx.Button(self, label="Cooper")
        self.chat_btn.SetToolTip("Open Cooper for this board")
        self.chat_btn.Bind(wx.EVT_BUTTON, self.on_open_chat)
        button_sizer.Add(self.chat_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        self.board_link = wx.adv.HyperlinkCtrl(
            self, wx.ID_ANY, "Open in DeepPCB", BOARDS_URL
        )
        button_sizer.Add(self.board_link, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        button_sizer.AddStretchSpacer(1)

        self.rate_btn = wx.Button(self, label="★ Rate")
        self.rate_btn.SetToolTip("Rate this board")
        self.rate_btn.Bind(wx.EVT_BUTTON, self.on_rate)
        button_sizer.Add(self.rate_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        main_sizer.Add(button_sizer, 0, wx.ALL | wx.EXPAND, 5)

    def load_board_status(self, board_status_response=None, report_errors=False):
        """Load and display the current board status.

        report_errors is set only for user-initiated refreshes: a modal from a
        background tick would reappear every few seconds.
        """
        if self._is_closing:
            return

        try:
            if not self.status_text or not self.status_text.GetParent():
                return

            if board_status_response is not None:
                response = board_status_response
            else:
                response = self.client.check_board_status(self.board_id)

            if response.success and response.board:
                self.deeppcb_board = response.board

                revisions_numbers_list = [
                    str(r) for r in self.deeppcb_board.get_all_revision_numbers()
                ]
                if not self._sync.baseline_set:
                    self._sync.set_baseline(self.deeppcb_board.get_latest_revision())

                self.status_text.SetLabel(self.deeppcb_board.board_status)

                board_status = self.deeppcb_board.board_status
                job_type = (
                    self.deeppcb_board.workflow.job_type
                    if self.deeppcb_board.workflow
                    else "Unknown"
                )
                self._update_caption(f"DeepPCB - {job_type} ({board_status})")

                # Update remaining time
                if self.deeppcb_board.board_status == "ReceivingRevisions":
                    if (
                        self.deeppcb_board.workflow
                        and self.deeppcb_board.workflow.started_on
                    ):
                        time_result = calculate_remaining_time(
                            self.deeppcb_board.workflow.started_on,
                            self.deeppcb_board.workflow.workflow_timeout,
                        )

                        if time_result["success"]:
                            self.remaining_time_label.SetLabel(time_result["message"])
                            self.remaining_time_label.Show(True)
                        else:
                            self.remaining_time_label.Show(False)
                    else:
                        self.remaining_time_label.Show(False)
                else:
                    self.remaining_time_label.Show(False)

                self.remaining_time_label.GetParent().Layout()
                self.revisions_list.SetItems(revisions_numbers_list)
                if revisions_numbers_list:
                    self.latest_solution = revisions_numbers_list[-1]
                    if self._user_selected_revision in revisions_numbers_list:
                        self.revisions_list.SetValue(self._user_selected_revision)
                    else:
                        self._user_selected_revision = None
                        self.revisions_list.SetValue(revisions_numbers_list[-1])
                    self.current_solution_value.SetLabel(revisions_numbers_list[-1])
                else:
                    self.latest_solution = None
                    self._user_selected_revision = None
                    self.current_solution_value.SetLabel("--")

                if self.deeppcb_board.board_pid:
                    self.board_link.SetURL(
                        f"{BOARDS_URL}/{self.deeppcb_board.board_pid}"
                    )

                self.update_stop_resume_button()

                # Only on the transition, not every time a finished board
                # is opened.
                if (
                    board_status in ["Done", "Stopped"]
                    and self._last_seen_status is not None
                    and self._last_seen_status != board_status
                    and not self._rating_prompt_shown
                ):
                    self._rating_prompt_shown = True
                    wx.CallAfter(self.show_rating_dialog)

                self._last_seen_status = board_status
            else:
                error_msg = response.error or response.raw_response
                self.status_text.SetLabel("Unavailable")
                if report_errors:
                    wx.MessageBox(
                        f"Error loading board status: {response.status}\n\n"
                        f"Response:\n{error_msg}",
                        "Board Status Error",
                        wx.OK | wx.ICON_ERROR,
                    )
        except Exception:
            print("[DeepPCB] board status update failed:")
            print(traceback.format_exc())

    def on_advanced_pane_changed(self, event):
        """Handle advanced settings pane expand/collapse."""
        self.update_panel_size()

    def update_stop_resume_button(self):
        """Update routing controls based on board status."""
        if not self.deeppcb_board:
            self.stop_resume_btn.Enable(False)
            self._set_routing_setup_visible(False)
            return

        board_status = self.deeppcb_board.board_status

        if board_status in ACTIVE_STATES:
            self._set_routing_setup_visible(False)
            self.stop_resume_btn.SetLabel("Stop")
            self.stop_resume_btn.Enable(True)
            self.stop_resume_btn.Show()
            if not self.info_label.IsShown():
                self.info_label.Show()
            if not self.loading_animation.IsShown():
                self.loading_animation.Show()
                self.loading_animation.Play()
        elif board_status in TERMINAL_STATES:
            self._set_routing_setup_visible(False)
            self.stop_resume_btn.SetLabel("New Job")
            self.stop_resume_btn.Enable(True)
            self.stop_resume_btn.Show()
            if self.info_label.IsShown():
                self.info_label.Hide()
            if self.loading_animation.IsShown():
                self.loading_animation.Stop()
                self.loading_animation.Hide()
        elif board_status in PRE_RUN_STATES:
            self._set_routing_setup_visible(True)
            self.stop_resume_btn.Hide()
            if self.info_label.IsShown():
                self.info_label.Hide()
            if self.loading_animation.IsShown():
                self.loading_animation.Stop()
                self.loading_animation.Hide()
        else:
            self._set_routing_setup_visible(False)
            self.stop_resume_btn.Enable(False)
            self.stop_resume_btn.Show()
            if self.info_label.IsShown():
                self.info_label.Hide()
            if self.loading_animation.IsShown():
                self.loading_animation.Stop()
                self.loading_animation.Hide()

        # Resizing the pane on every tick would fight the user's own resizing.
        layout_key = (
            self.routing_setup_panel.IsShown(),
            self.stop_resume_btn.IsShown(),
            self.info_label.IsShown(),
            self.pending_solution_label.IsShown(),
        )
        if layout_key != self._last_layout_key:
            self._last_layout_key = layout_key
            self.update_panel_size()

    def _set_routing_setup_visible(self, visible: bool) -> None:
        if visible:
            self.routing_setup_panel.Show()
            self.start_routing_btn.Enable(True)
            self.timeout_slider.Enable(True)
        else:
            self.routing_setup_panel.Hide()

    def on_stop_resume(self, event):
        """Handle stop/resume button click."""
        if not self.deeppcb_board:
            return

        board_status = self.deeppcb_board.board_status

        # These must be the same tuples update_stop_resume_button labels the
        # button from, or the button reads "New Job" (or "Stop") and silently
        # does nothing.
        if board_status in ACTIVE_STATES:
            try:
                response = self.client.stop_board(self.board_id)
                if response.success:
                    self.load_board_status()
                else:
                    wx.MessageBox(
                        f"Failed to stop board: {response.error}",
                        "Stop Error",
                        wx.OK | wx.ICON_ERROR,
                    )
            except Exception as e:
                wx.MessageBox(
                    f"Error stopping board: {str(e)}", "Error", wx.OK | wx.ICON_ERROR
                )

        elif board_status in TERMINAL_STATES:
            if not self._on_new_board_requested:
                return
            # Deferred: this flow replaces the status panel, and destroying the
            # panel that owns this button from inside its own event handler
            # returns wx into a deleted object.
            wx.CallAfter(self._on_new_board_requested)

    def on_solution_selected(self, event):
        """Remember an explicit pick so the refresh tick stops resetting it.

        Re-selecting the latest revision means "follow the latest again".
        """
        selected = self.revisions_list.GetValue()
        if not selected or selected == "----":
            self._user_selected_revision = None
            return
        self._user_selected_revision = (
            None if selected == self.latest_solution else selected
        )

    def on_timeout_change(self, event):
        value = self.timeout_slider.GetValue()
        self.timeout_value_label.SetLabel(f"{value} min")
        self.timeout = value

    def on_start_routing(self, event):
        if not self.deeppcb_board:
            wx.MessageBox(
                "Board state unknown — wait for the next refresh and retry.",
                "Not ready",
                wx.OK | wx.ICON_WARNING,
            )
            return

        progress = wx.ProgressDialog(
            "Starting Routing",
            "Checking eligibility…",
            maximum=100,
            parent=self._frame,
            style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE | wx.PD_SMOOTH,
        )
        self.start_routing_btn.Enable(False)
        try:
            result = routing_jobs.start_routing_job(
                self.client,
                self.deeppcb_board,
                self.board_id,
                self.timeout,
                self.job_type,
                on_progress=progress.Update,
            )
        except Exception as e:
            self._destroy_quietly(progress)
            wx.MessageBox(
                f"Error starting routing: {e}", "Error", wx.OK | wx.ICON_ERROR
            )
            self.start_routing_btn.Enable(True)
            return

        self._destroy_quietly(progress)
        if result.submitted:
            self._sync.bump_window()
        self._report_routing_result(result)
        if result.submitted:
            self.load_board_status()
        else:
            self.start_routing_btn.Enable(True)

    def _report_routing_result(self, result) -> None:
        if result.outcome == routing_jobs.STARTED:
            return
        if result.outcome == routing_jobs.INSUFFICIENT_CREDITS:
            credits_dialog = NotEnoughCreditsDialog(self._frame)
            credits_dialog.ShowModal()
            credits_dialog.Destroy()
            return
        if result.outcome == routing_jobs.NOT_RUNNING_YET:
            wx.MessageBox(
                f"Submitted, but the run hasn't reached Running yet: "
                f"{result.message}. Status will update as it progresses.",
                "Warning",
                wx.OK | wx.ICON_WARNING,
            )
            return
        titles = {
            routing_jobs.BALANCE_CHECK_FAILED: "Failed to get user balance",
            routing_jobs.SUBMIT_FAILED: "Failed to submit job",
        }
        wx.MessageBox(
            f"{titles.get(result.outcome, 'Could not start routing')}: "
            f"{result.message} (status {result.status}).",
            "Error",
            wx.OK | wx.ICON_ERROR,
        )

    @staticmethod
    def _destroy_quietly(dialog) -> None:
        try:
            dialog.Destroy()
        except Exception:
            pass

    def on_settings(self, event):
        """Open API key settings dialog."""
        api_key_dialog = ApiKeyDialog(
            self._frame, self.project_name, self.project_directory, self.swagger_url
        )
        result = api_key_dialog.ShowModal()
        api_key_dialog.Destroy()

        if result == wx.ID_OK:
            self.api_key = load_api_key_from_config()
            self.client = DeepPCBClient(self.swagger_url, self.api_key)
            # Cooper holds its own client.
            if self._on_api_key_changed:
                try:
                    self._on_api_key_changed()
                except Exception:
                    # Best-effort: Cooper picks the new key up on next use.
                    pass

    def on_refresh(self, event):
        self.load_board_status(report_errors=True)

    def on_open_chat(self, event):
        if self._on_open_chat_requested:
            try:
                self._on_open_chat_requested()
            except Exception as e:
                wx.MessageBox(
                    f"Failed to open chat panel: {e}",
                    "Chat Error",
                    wx.OK | wx.ICON_ERROR,
                )

    def on_rate(self, event):
        """Open the rating modal from the Rate button."""
        self.show_rating_dialog()

    def show_rating_dialog(self):
        """Show the board rating modal. Used by the Rate button and by the
        automatic one-time prompt when a board finishes."""
        if self._is_closing or not self.board_id:
            return
        try:
            dialog = BoardRatingDialog(self._frame, self.client, self.board_id)
            dialog.ShowModal()
            dialog.Destroy()
        except Exception as e:
            print(f"Error showing rating dialog: {str(e)}")

    def on_download(self, event):
        """Render the solution currently selected in the dropdown."""
        revision_number = self.revisions_list.GetValue()
        if not revision_number or revision_number == "----":
            wx.MessageBox(
                "Please select a solution to render.",
                "No Selection",
                wx.OK | wx.ICON_WARNING,
            )
            return
        number = int(revision_number)
        # The dropdown is filled from this same board, so the lookup only
        # misses if the list went stale; the number alone still renders.
        revision = self.deeppcb_board.get_revision(number) or Revision(number)
        self._render_revision(revision, report_errors=True)

    def _render_revision(
        self, revision, download_result=None, report_errors=False
    ) -> bool:
        """Render exactly `revision` onto the open board.

        Passed in rather than read from the dropdown, so a background render
        cannot pick up a selection the user changed in the meantime.
        """
        if self._is_closing:
            return False
        number = revision.revision_number
        solution_filename = self._sync.solution_path(number)
        result = download_result or self._sync.download(number)

        if not result.get("success"):
            message = f"Failed to download solution {number}: {result.get('error')}"
            self._handle_render_failure(message, revision, report_errors)
            return False

        try:
            load_and_render_board(solution_filename)
        except Exception as e:
            message = f"Failed to render solution {number}: {e}"
            self._handle_render_failure(message, revision, report_errors)
            return False

        self._sync.mark_handled(revision)
        self._clear_pending_solution()
        return True

    def _handle_render_failure(
        self, message: str, revision, report_errors: bool
    ) -> None:
        """Report a failed render once, and never auto-retry that revision.

        load_and_render_board opens its own modal on failure, so retrying it
        every tick would be a dialog every few seconds.
        """
        if report_errors:
            wx.MessageBox(message, "Render Error", wx.OK | wx.ICON_ERROR)
            return
        self._sync.mark_handled(revision)
        self._show_pending_solution(revision.revision_number)

    def _show_pending_solution(self, revision_number: int) -> None:
        """Offer a solution that arrived but was deliberately not auto-rendered.

        Idempotent: the tick re-offers the same revision until the user loads
        it, and relaying out the pane every time would be churn.
        """
        if self._is_closing:
            return
        revision_number = int(revision_number)
        # Revision 0 is the board as uploaded, not a routing result.
        if revision_number <= 0:
            return
        if (
            self._pending_solution_revision == revision_number
            and self.pending_solution_label.IsShown()
        ):
            return
        self._pending_solution_revision = revision_number
        self.pending_solution_label.SetLabel(
            f"Solution {revision_number} is ready - use Render Solution under "
            "Advanced Settings to load it onto this board."
        )
        self.pending_solution_label.Wrap(400)
        self.pending_solution_label.Show()
        self.Layout()
        self.update_panel_size()

    def _offer_solution(self, revision_number: int) -> None:
        self._show_pending_solution(revision_number)

    def _clear_pending_solution(self) -> None:
        self._pending_solution_revision = None
        if self._is_closing:
            return
        if self.pending_solution_label.IsShown():
            self.pending_solution_label.Hide()
            self.Layout()
            self.update_panel_size()

    def trigger_chat_turn_refresh(self):
        """Called from ChatPanel after an assistant turn completes.

        The one-shot tick catches an already-committed change; the poll window
        covers one that lands a few seconds later.
        """
        if self._is_closing:
            return

        self._sync.bump_window()
        self._sync.tick_now()
        self._sync.start()

    def on_panel_close(self):
        """Cleanup when panel is closed - stops all processing."""
        self._is_closing = True
        self._sync.stop()

        if self._on_panel_closed_callback:
            try:
                self._on_panel_closed_callback()
            except Exception:
                pass
