"""
Board Status Panel

This panel monitors the board status without blocking the main KiCad view.
It integrates directly into KiCad's interface as a dockable panel.
"""

import wx
import wx.adv
import os
import threading
import time

from ..config import BOARDS_URL, APP_VERSION
from ..custom_widgets import RoundedPanel
from ..utils import (
    download_and_save_board,
    load_and_render_board,
    calculate_remaining_time,
)
from ..helpers import DeepPCBClient, DeepPCBBoard
from ..dialogs.api_key_dialog import ApiKeyDialog, load_api_key_from_config
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
    ):
        self.board_id = board_id
        self.swagger_url = swagger_url
        self.project_name = project_name
        self.project_directory = project_directory
        self.api_key = load_api_key_from_config()
        self.client = DeepPCBClient(self.swagger_url, self.api_key)
        self.deeppcb_board: DeepPCBBoard = None
        self.auto_refresh_active = False
        self.refresh_thread = None
        self._is_closing = False
        self._on_panel_closed_callback = on_panel_closed
        self._on_new_board_requested = on_new_board_requested

        super().__init__(parent)

        self._update_caption(f"DeepPCB Status (v{APP_VERSION})")

        self.auto_refresh_active = self.auto_refresh_toggle.GetValue()

        self.load_board_status()

        if self.auto_refresh_active:
            self.start_auto_refresh()

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

        auto_refresh_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.auto_refresh_toggle = wx.CheckBox(advanced_win, label="")
        self.auto_refresh_toggle.SetValue(True)
        self.auto_refresh_toggle.Bind(wx.EVT_CHECKBOX, self.on_auto_refresh_toggle)
        auto_refresh_label = wx.StaticText(
            advanced_win, label="Auto-render new solutions"
        )

        auto_refresh_sizer.Add(
            self.auto_refresh_toggle, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5
        )
        auto_refresh_sizer.Add(
            auto_refresh_label, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5
        )

        advanced_sizer.Add(auto_refresh_sizer, 0, wx.ALL, 5)

        advanced_win.SetSizer(advanced_sizer)
        main_sizer.Add(self.advanced_pane, 0, wx.ALL | wx.EXPAND, 5)

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

        self.board_link = wx.adv.HyperlinkCtrl(
            self, wx.ID_ANY, "Open in DeepPCB", BOARDS_URL
        )
        button_sizer.Add(self.board_link, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        main_sizer.Add(button_sizer, 0, wx.ALL | wx.EXPAND, 5)

        self.SetSizer(main_sizer)
        self.Layout()

    def load_board_status(self, board_status_response=None):
        """Load and display the current board status."""
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
                self.status_text.SetLabel(self.deeppcb_board.board_status)

                board_status = self.deeppcb_board.board_status
                job_type = (
                    self.deeppcb_board.workflow.job_type
                    if self.deeppcb_board.workflow
                    else "Unknown"
                )
                self._update_caption(f"DeepPCB - {job_type} ({board_status})")

                if board_status in ["Stopped", "Failed", "Done"]:
                    self.auto_refresh_toggle.SetValue(False)
                    self.auto_refresh_active = False

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
                    self.revisions_list.SetValue(revisions_numbers_list[-1])
                    self.current_solution_value.SetLabel(revisions_numbers_list[-1])
                else:
                    self.latest_solution = None
                    self.current_solution_value.SetLabel("--")

                if self.deeppcb_board.board_pid:
                    self.board_link.SetURL(
                        f"{BOARDS_URL}/{self.deeppcb_board.board_pid}"
                    )

                self.update_stop_resume_button()
            else:
                error_msg = response.error or response.raw_response
                wx.MessageBox(
                    f"Error loading board status: {response.status}\n\nResponse:\n{error_msg}",
                    "Board Status Error",
                    wx.OK | wx.ICON_ERROR,
                )
        except Exception as e:
            print(f"Error loading board status: {str(e)}")

    def on_advanced_pane_changed(self, event):
        """Handle advanced settings pane expand/collapse."""
        self.update_panel_size()

    def update_stop_resume_button(self):
        """Update the stop/resume button based on board status."""
        if not self.deeppcb_board:
            self.stop_resume_btn.Enable(False)
            return

        board_status = self.deeppcb_board.board_status

        if board_status in ["Running", "ReceivingRevisions"]:
            self.stop_resume_btn.SetLabel("Stop")
            self.stop_resume_btn.Enable(True)
            if not self.info_label.IsShown():
                self.info_label.Show()
                self.update_panel_size()
            if not self.loading_animation.IsShown():
                self.loading_animation.Show()
                self.loading_animation.Play()
        elif board_status in ["Stopped", "Done"]:
            self.stop_resume_btn.SetLabel("New Job")
            self.stop_resume_btn.Enable(True)
            if self.info_label.IsShown():
                self.info_label.Hide()
                self.update_panel_size()

            if self.loading_animation.IsShown():
                self.loading_animation.Stop()
                self.loading_animation.Hide()
        else:
            self.stop_resume_btn.Enable(False)
            if self.info_label.IsShown():
                self.info_label.Hide()
                self.update_panel_size()

            if self.loading_animation.IsShown():
                self.loading_animation.Stop()
                self.loading_animation.Hide()

    def on_stop_resume(self, event):
        """Handle stop/resume button click."""
        if not self.deeppcb_board:
            return

        board_status = self.deeppcb_board.board_status

        if board_status in ["Running", "ReceivingRevisions"]:
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

        elif board_status in ["Stopped", "Done"]:
            if self._on_new_board_requested:
                self._on_new_board_requested()

    def on_auto_refresh_toggle(self, event):
        """Handle auto-refresh toggle."""
        self.auto_refresh_active = self.auto_refresh_toggle.GetValue()

        if self.auto_refresh_active:
            if self.deeppcb_board:
                board_status = self.deeppcb_board.board_status
                if board_status in ["Starting", "Running", "ReceivingRevisions"]:
                    self.start_auto_refresh()

    def on_solution_selected(self, event):
        """Handle solution selection from dropdown."""
        selected = self.revisions_list.GetValue()
        if self.latest_solution and selected != self.latest_solution:
            if self.auto_refresh_toggle.GetValue():
                self.auto_refresh_toggle.SetValue(False)
                self.auto_refresh_active = False
                self.stop_auto_refresh()

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

    def on_refresh(self, event):
        self.load_board_status()

    def on_download(self, event):
        """Download and render selected solution."""
        revision_number = self.revisions_list.GetValue()
        if not revision_number or revision_number == "----":
            wx.MessageBox(
                "Please select a solution to render.",
                "No Selection",
                wx.OK | wx.ICON_WARNING,
            )
            return

        solutions_dir = os.path.join(self.project_directory, "DeepPCB_Solutions")
        solution_filename = os.path.join(
            solutions_dir,
            f"{self.project_name}_solution_{int(revision_number):04d}.kicad_pcb",
        )

        result = download_and_save_board(
            self.client, self.board_id, int(revision_number), solution_filename
        )

        if result["success"]:
            try:
                load_and_render_board(solution_filename)
            except Exception as e:
                wx.MessageBox(
                    f"Failed to render board: {str(e)}",
                    "Render Error",
                    wx.OK | wx.ICON_ERROR,
                )
        else:
            wx.MessageBox(
                f"Failed to download board: {result['error']}",
                "Download Error",
                wx.OK | wx.ICON_ERROR,
            )

    def download_and_render_board(self, download_and_save_board_result=None):
        """Download and render the latest solution."""
        if self._is_closing:
            return

        revision_number = self.revisions_list.GetValue()
        if not revision_number or revision_number == "----":
            return

        solutions_dir = os.path.join(self.project_directory, "DeepPCB_Solutions")
        solution_filename = os.path.join(
            solutions_dir,
            f"{self.project_name}_solution_{int(revision_number):04d}.kicad_pcb",
        )

        if download_and_save_board_result is not None:
            result = download_and_save_board_result
        else:
            result = download_and_save_board(
                self.client, self.board_id, int(revision_number), solution_filename
            )

        if result["success"]:
            try:
                load_and_render_board(solution_filename)
            except Exception as e:
                print(f"Failed to render board: {str(e)}")

    def start_auto_refresh(self):
        """Start the auto-refresh worker thread."""
        if self.refresh_thread and self.refresh_thread.is_alive():
            return
        self.refresh_thread = threading.Thread(
            target=self.auto_refresh_worker, daemon=True
        )
        self.refresh_thread.start()

    def auto_refresh_worker(self):
        """Worker thread that refreshes board status every 5 seconds."""
        while self.auto_refresh_active and not self._is_closing:
            time.sleep(5)
            if not self.auto_refresh_active or self._is_closing:
                break
            try:
                status_response = self.client.check_board_status(self.board_id)
                if status_response.success and status_response.board:
                    self.deeppcb_board = status_response.board
                    latest_revision_number = (
                        self.deeppcb_board.get_latest_revision_number()
                    )
                    if latest_revision_number:
                        solutions_dir = os.path.join(
                            self.project_directory, "DeepPCB_Solutions"
                        )
                        solution_filename = os.path.join(
                            solutions_dir,
                            f"{self.project_name}_solution_{int(latest_revision_number):04d}.kicad_pcb",
                        )
                        download_result = download_and_save_board(
                            self.client,
                            self.board_id,
                            latest_revision_number,
                            solution_filename,
                        )
                        if self.auto_refresh_active and not self._is_closing:
                            wx.CallAfter(self.load_board_status, status_response)
                            if download_result["success"]:
                                wx.CallAfter(
                                    self.download_and_render_board, download_result
                                )
            except Exception as e:
                print(f"Auto-refresh error: {e}")

    def on_panel_close(self):
        """Cleanup when panel is closed - stops all processing."""
        self._is_closing = True

        self.auto_refresh_active = False
        try:
            self.auto_refresh_toggle.SetValue(False)
        except Exception:
            pass

        # Wait briefly for the thread to stop
        if self.refresh_thread and self.refresh_thread.is_alive():
            self.refresh_thread.join(timeout=0.5)

        # Notify the plugin that we're closed
        if self._on_panel_closed_callback:
            try:
                self._on_panel_closed_callback()
            except Exception:
                pass
