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


class RoundedPanel(wx.Panel):
    def __init__(
        self,
        parent,
        bg_color=wx.Colour(230, 230, 230),
        border_color=wx.Colour(200, 200, 200),
        border_width=1,
        radius=8,
    ):
        super().__init__(parent, style=wx.TRANSPARENT_WINDOW)
        self.bg_color = bg_color
        self.border_color = border_color
        self.border_width = border_width
        self.radius = radius

        self.Bind(wx.EVT_PAINT, self.on_paint)
        self.Bind(wx.EVT_SIZE, self.on_size)

    def on_size(self, event):
        self.Refresh()
        event.Skip()

    def on_paint(self, event):
        dc = wx.PaintDC(self)
        gc = wx.GraphicsContext.Create(dc)

        if gc:
            w, h = self.GetSize()
            path = gc.CreatePath()
            path.AddRoundedRectangle(0, 0, w, h, self.radius)
            gc.SetBrush(wx.Brush(self.bg_color))
            gc.SetPen(wx.Pen(self.border_color, self.border_width))
            gc.DrawPath(path)
