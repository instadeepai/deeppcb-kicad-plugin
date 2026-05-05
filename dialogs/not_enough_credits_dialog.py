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

import wx
import webbrowser

from ..config import CREDITS_URL


class NotEnoughCreditsDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(
            parent, title="Not enough credits", style=wx.DEFAULT_DIALOG_STYLE
        )
        self.create_ui()
        self.EnableLayoutAdaptation(True)

    def create_ui(self):
        panel = wx.Panel(self)
        panel_sizer = wx.BoxSizer(wx.VERTICAL)

        message = wx.StaticText(
            panel,
            label="You don't have enough credits to start this run.\nPlease top-up your account and try again.",
        )
        panel_sizer.Add(message, 0, wx.ALL, 20)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.AddStretchSpacer()

        cancel_btn = wx.Button(panel, wx.ID_CANCEL, label="Cancel")
        cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
        button_sizer.Add(cancel_btn, 0, wx.ALL, 5)

        buy_credits_btn = wx.Button(panel, label="Buy Credits")
        buy_credits_btn.Bind(wx.EVT_BUTTON, self.on_buy_credits)
        button_sizer.Add(buy_credits_btn, 0, wx.ALL, 5)

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

    def on_buy_credits(self, event):
        webbrowser.open(CREDITS_URL)
        self.EndModal(wx.ID_CANCEL)

    def on_cancel(self, event):
        self.EndModal(wx.ID_CANCEL)
