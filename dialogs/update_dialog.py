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

import time
import webbrowser

import wx

from ..config import (
    APP_VERSION,
    PLUGIN_LATEST_RELEASE_API_URL,
    PLUGIN_RELEASES_URL,
)
from ..helpers import update_check
from ..helpers.http_helpers import get_no_retry_session
from .api_key_dialog import get_config_path


class UpdateDialog(wx.Dialog):
    def __init__(self, parent, release):
        super().__init__(
            parent, title="Update available", style=wx.DEFAULT_DIALOG_STYLE
        )
        self.release = release
        self.create_ui()
        self.EnableLayoutAdaptation(True)

    def create_ui(self):
        panel = wx.Panel(self)
        panel_sizer = wx.BoxSizer(wx.VERTICAL)

        message = wx.StaticText(
            panel,
            label=f"DeepPCB plugin for KiCad v{self.release.version} is available.",
        )
        panel_sizer.Add(message, 0, wx.ALL, 20)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)

        skip_btn = wx.Button(panel, label="Skip this version")
        skip_btn.Bind(wx.EVT_BUTTON, self.on_skip)
        button_sizer.Add(skip_btn, 0, wx.ALL, 5)

        button_sizer.AddStretchSpacer()

        later_btn = wx.Button(panel, wx.ID_CANCEL, label="Later")
        later_btn.Bind(wx.EVT_BUTTON, self.on_later)
        button_sizer.Add(later_btn, 0, wx.ALL, 5)

        download_btn = wx.Button(panel, wx.ID_OK, label="Download")
        download_btn.SetDefault()
        download_btn.Bind(wx.EVT_BUTTON, self.on_download)
        button_sizer.Add(download_btn, 0, wx.ALL, 5)

        panel_sizer.Add(button_sizer, 0, wx.ALL | wx.EXPAND, 10)

        panel.SetSizer(panel_sizer)
        panel.Layout()
        best_size = panel.GetBestSize()

        dialog_sizer = wx.BoxSizer(wx.VERTICAL)
        dialog_sizer.Add(panel, 1, wx.EXPAND | wx.ALL, 0)

        self.SetSizer(dialog_sizer)
        self.SetMinSize(best_size)
        self.Fit()
        self.Centre()

    def on_download(self, event):
        webbrowser.open(self.release.url or PLUGIN_RELEASES_URL)
        self.EndModal(wx.ID_OK)

    def on_later(self, event):
        self.EndModal(wx.ID_CANCEL)

    def on_skip(self, event):
        update_check.skip_version(get_config_path(), self.release.version)
        self.EndModal(wx.ID_CANCEL)


def show_update_dialog_if_available():
    try:
        release = update_check.check_for_update(
            get_config_path(),
            APP_VERSION,
            get_no_retry_session(),
            time.time(),
            PLUGIN_LATEST_RELEASE_API_URL,
        )
    except Exception:
        return
    if not release:
        return
    dialog = UpdateDialog(None, release)
    dialog.ShowModal()
    dialog.Destroy()
