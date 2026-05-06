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
import wx.adv
import configparser
from pathlib import Path

from ..config import INTEGRATION_URL


def get_config_path():
    home = Path.home()
    kicad_config_dir = home / ".kicad" / "deeppcb"

    kicad_config_dir.mkdir(parents=True, exist_ok=True)
    return kicad_config_dir / "config.ini"


def load_api_key_from_config():
    """Load the API key from the config file if it exists.

    Returns:
        str: The API key if found, empty string otherwise.
    """
    config_path = get_config_path()

    if config_path.exists():
        config = configparser.ConfigParser()
        config.read(config_path)

        if config.has_section("API") and config.has_option("API", "key"):
            return config.get("API", "key")

    return ""


class ApiKeyDialog(wx.Dialog):
    def __init__(self, parent, project_name, project_directory, api_url):
        super().__init__(
            parent,
            title="DeepPCB API Key",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )

        self.api_url = api_url
        self.api_key = ""
        self.create_ui()
        self.load_api_key()
        self.EnableLayoutAdaptation(True)

    def create_ui(self):
        panel = wx.Panel(self)
        panel_sizer = wx.BoxSizer(wx.VERTICAL)
        panel_sizer.AddSpacer(20)

        api_key_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.api_key_label = wx.StaticText(panel, label="API Key:")
        self.api_key_text = wx.TextCtrl(panel, style=wx.TE_PASSWORD, size=(250, -1))

        api_key_sizer.Add(self.api_key_label, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        api_key_sizer.Add(self.api_key_text, 0, wx.ALL, 5)
        panel_sizer.Add(api_key_sizer, 0, wx.ALL | wx.CENTER, 5)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)

        api_key_link = wx.adv.HyperlinkCtrl(
            panel, wx.ID_ANY, "Get your API key here", INTEGRATION_URL
        )
        button_sizer.Add(api_key_link, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        button_sizer.AddStretchSpacer(1)

        self.cancel_btn = wx.Button(panel, label="Cancel", id=wx.ID_CANCEL)
        button_sizer.Add(self.cancel_btn, 0, wx.ALL, 5)

        self.save_btn = wx.Button(panel, label="Save")
        self.save_btn.Bind(wx.EVT_BUTTON, self.on_save)
        button_sizer.Add(self.save_btn, 0, wx.ALL, 5)

        panel_sizer.Add(button_sizer, 0, wx.ALL | wx.EXPAND, 10)

        panel.SetSizer(panel_sizer)
        panel.Layout()  # Force layout calculation
        best_size = panel.GetBestSize()  # Get the optimal size

        dialog_sizer = wx.BoxSizer(wx.VERTICAL)
        dialog_sizer.Add(panel, 1, wx.EXPAND | wx.ALL, 0)

        self.SetSizer(dialog_sizer)
        min_width = max(550, best_size.GetWidth())
        self.SetMinSize((min_width, best_size.GetHeight()))
        self.Fit()
        self.Centre()

    def load_api_key(self):
        api_key = load_api_key_from_config()
        if api_key:
            self.api_key_text.SetValue(api_key)
            self.api_key = api_key

    def save_api_key(self):
        config_path = get_config_path()
        config = configparser.ConfigParser()

        if config_path.exists():
            config.read(config_path)

        if not config.has_section("API"):
            config.add_section("API")

        config.set("API", "key", self.api_key)

        with open(config_path, "w") as configfile:
            config.write(configfile)

    def on_save(self, event):
        self.api_key = self.api_key_text.GetValue().strip()

        if not self.api_key:
            wx.MessageBox("Please enter an API key.", "Error", wx.OK | wx.ICON_ERROR)
            return

        try:
            self.save_api_key()
            wx.MessageBox(
                "API key saved successfully!", "Success", wx.OK | wx.ICON_INFORMATION
            )
            self.EndModal(wx.ID_OK)
        except Exception as e:
            wx.MessageBox(
                f"Error saving API key: {str(e)}", "Error", wx.OK | wx.ICON_ERROR
            )
