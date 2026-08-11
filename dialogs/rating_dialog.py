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

import math

import wx

from ..helpers import DeepPCBClient

MIN_RATING = 1
MAX_RATING = 5
FEEDBACK_MAX_LENGTH = 1000


class StarRating(wx.Panel):
    """A row of clickable, owner-drawn stars for selecting a 1..max_stars rating.

    The stars are painted directly on a single panel (rather than using
    per-star StaticText glyphs) so that the hover highlight reliably reverts
    when the pointer leaves, and so a selected star renders as a solid shape on
    every platform. Hover only previews; the value changes only on click.
    """

    STAR_SIZE = 24
    SPACING = 6
    MARGIN = 3

    def __init__(self, parent, max_stars=MAX_RATING, on_change=None):
        super().__init__(parent)
        self.max_stars = max_stars
        self.rating = 0
        self._hover = 0
        self._on_change = on_change

        self._filled_color = wx.Colour(245, 184, 0)
        self._hover_color = wx.Colour(255, 205, 70)
        self._empty_color = wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT)

        self.SetBackgroundColour(parent.GetBackgroundColour())
        self.SetCursor(wx.Cursor(wx.CURSOR_HAND))

        width = (
            self.max_stars * self.STAR_SIZE
            + (self.max_stars - 1) * self.SPACING
            + 2 * self.MARGIN
        )
        height = self.STAR_SIZE + 2 * self.MARGIN
        self.SetMinSize((width, height))

        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_ERASE_BACKGROUND, lambda e: None)
        self.Bind(wx.EVT_MOTION, self._on_motion)
        self.Bind(wx.EVT_LEAVE_WINDOW, self._on_leave)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_click)
        self.Bind(wx.EVT_SIZE, lambda e: self.Refresh())

    def _index_at(self, x):
        cell = self.STAR_SIZE + self.SPACING
        idx = int((x - self.MARGIN) // cell)
        return max(0, min(self.max_stars - 1, idx))

    def _on_motion(self, event):
        hover = self._index_at(event.GetX()) + 1
        if hover != self._hover:
            self._hover = hover
            self.Refresh()
        event.Skip()

    def _on_leave(self, event):
        if self._hover:
            self._hover = 0
            self.Refresh()
        event.Skip()

    def _on_click(self, event):
        self.rating = self._index_at(event.GetX()) + 1
        self.Refresh()
        if self._on_change:
            self._on_change(self.rating)
        event.Skip()

    def _star_polygon(self, cx, cy, outer_r, inner_r):
        points = []
        angle = -math.pi / 2  # start at the top point
        step = math.pi / 5
        for i in range(10):
            r = outer_r if i % 2 == 0 else inner_r
            points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
            angle += step
        return points

    def _on_paint(self, event):
        dc = wx.BufferedPaintDC(self)
        background = self.GetBackgroundColour()
        dc.SetBackground(wx.Brush(background))
        dc.Clear()

        gc = wx.GraphicsContext.Create(dc)
        if gc is None:
            return

        if self._hover:
            active = self._hover
            fill_color = self._hover_color
        else:
            active = self.rating
            fill_color = self._filled_color

        outer_r = self.STAR_SIZE / 2 - 1
        inner_r = outer_r * 0.4
        cy = self.MARGIN + self.STAR_SIZE / 2

        for i in range(self.max_stars):
            cx = self.MARGIN + i * (self.STAR_SIZE + self.SPACING) + self.STAR_SIZE / 2
            points = self._star_polygon(cx, cy, outer_r, inner_r)

            path = gc.CreatePath()
            path.MoveToPoint(*points[0])
            for point in points[1:]:
                path.AddLineToPoint(*point)
            path.CloseSubpath()

            if i < active:
                gc.SetBrush(wx.Brush(fill_color))
                gc.SetPen(wx.Pen(fill_color, 1))
            else:
                gc.SetBrush(wx.Brush(background))
                gc.SetPen(wx.Pen(self._empty_color, 1))
            gc.DrawPath(path)

    def get_rating(self):
        return self.rating


class BoardRatingDialog(wx.Dialog):
    """Modal that collects 1-5 star ratings (speed, DRC, solution) plus
    required feedback, then submits them to the board rating endpoint."""

    DIMENSIONS = [
        ("speed", "Speed"),
        ("drc", "DRC"),
        ("solution", "Solution quality"),
    ]

    def __init__(self, parent, client: DeepPCBClient, board_id):
        super().__init__(
            parent,
            title="Rate this board",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )

        self.client = client
        self.board_id = board_id
        self.star_widgets = {}

        self.create_ui()
        self.EnableLayoutAdaptation(True)

    def create_ui(self):
        panel = wx.Panel(self)
        panel_sizer = wx.BoxSizer(wx.VERTICAL)

        grey = wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT)

        message = wx.StaticText(
            panel,
            label=(
                "How satisfied are you with the results? Did you encounter any "
                "issues? Your feedback helps us make DeepPCB better for everyone!"
            ),
        )
        message.Wrap(420)
        panel_sizer.Add(message, 0, wx.LEFT | wx.RIGHT | wx.TOP, 20)
        panel_sizer.AddSpacer(12)

        grid = wx.FlexGridSizer(rows=len(self.DIMENSIONS), cols=2, vgap=10, hgap=15)
        for key, label in self.DIMENSIONS:
            label_ctrl = wx.StaticText(panel, label=label)
            label_font = label_ctrl.GetFont()
            label_font.SetWeight(wx.FONTWEIGHT_BOLD)
            label_ctrl.SetFont(label_font)
            grid.Add(label_ctrl, 0, wx.ALIGN_CENTER_VERTICAL)

            stars = StarRating(
                panel, on_change=lambda v, k=key: self._on_rating_change(k, v)
            )
            self.star_widgets[key] = stars
            grid.Add(stars, 0, wx.ALIGN_CENTER_VERTICAL)
        panel_sizer.Add(grid, 0, wx.LEFT | wx.RIGHT, 20)

        panel_sizer.AddSpacer(15)

        feedback_label = wx.StaticText(panel, label="Feedback (required)")
        feedback_font = feedback_label.GetFont()
        feedback_font.SetWeight(wx.FONTWEIGHT_BOLD)
        feedback_label.SetFont(feedback_font)
        panel_sizer.Add(feedback_label, 0, wx.LEFT | wx.RIGHT, 20)

        self.feedback_text = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(-1, 90))
        self.feedback_text.SetMaxLength(FEEDBACK_MAX_LENGTH)
        self.feedback_text.SetHint("Tell us what worked well and what could be better…")
        self.feedback_text.Bind(wx.EVT_TEXT, self.on_feedback_changed)
        panel_sizer.Add(
            self.feedback_text, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 20
        )

        self.char_counter = wx.StaticText(panel, label=f"0 / {FEEDBACK_MAX_LENGTH}")
        self.char_counter.SetForegroundColour(grey)
        counter_font = self.char_counter.GetFont()
        counter_font.SetPointSize(counter_font.GetPointSize() - 1)
        self.char_counter.SetFont(counter_font)
        panel_sizer.Add(
            self.char_counter, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.ALIGN_RIGHT, 20
        )

        panel_sizer.AddSpacer(10)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.AddStretchSpacer(1)

        self.cancel_btn = wx.Button(panel, id=wx.ID_CANCEL, label="Cancel")
        button_sizer.Add(self.cancel_btn, 0, wx.ALL, 5)

        self.submit_btn = wx.Button(panel, label="Submit")
        self.submit_btn.Bind(wx.EVT_BUTTON, self.on_submit)
        self.submit_btn.Enable(False)
        button_sizer.Add(self.submit_btn, 0, wx.ALL, 5)

        panel_sizer.Add(button_sizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 15)

        panel.SetSizer(panel_sizer)
        panel.Layout()
        best_size = panel.GetBestSize()

        dialog_sizer = wx.BoxSizer(wx.VERTICAL)
        dialog_sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(dialog_sizer)

        min_width = max(460, best_size.GetWidth())
        self.SetMinSize((min_width, best_size.GetHeight()))
        self.Fit()
        self.Centre()

    def _on_rating_change(self, key, value):
        self._update_submit_state()

    def on_feedback_changed(self, event):
        length = len(self.feedback_text.GetValue())
        self.char_counter.SetLabel(f"{length} / {FEEDBACK_MAX_LENGTH}")
        if length > FEEDBACK_MAX_LENGTH:
            self.char_counter.SetForegroundColour(wx.Colour(200, 0, 0))
        else:
            self.char_counter.SetForegroundColour(
                wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT)
            )
        self._update_submit_state()
        event.Skip()

    def _all_rated(self):
        return all(
            self.star_widgets[key].get_rating() >= MIN_RATING
            for key, _label in self.DIMENSIONS
        )

    def _feedback_valid(self):
        length = len(self.feedback_text.GetValue().strip())
        return 0 < length <= FEEDBACK_MAX_LENGTH

    def _update_submit_state(self):
        self.submit_btn.Enable(self._all_rated() and self._feedback_valid())

    def on_submit(self, event):
        if not self._all_rated():
            wx.MessageBox(
                "Please rate all three aspects before submitting.",
                "Incomplete rating",
                wx.OK | wx.ICON_WARNING,
            )
            return

        feedback = self.feedback_text.GetValue().strip()
        if not feedback:
            wx.MessageBox(
                "Please enter feedback before submitting.",
                "Feedback required",
                wx.OK | wx.ICON_WARNING,
            )
            return
        if len(feedback) > FEEDBACK_MAX_LENGTH:
            wx.MessageBox(
                f"Feedback must not exceed {FEEDBACK_MAX_LENGTH} characters.",
                "Feedback too long",
                wx.OK | wx.ICON_WARNING,
            )
            return

        speed = self.star_widgets["speed"].get_rating()
        drc = self.star_widgets["drc"].get_rating()
        solution = self.star_widgets["solution"].get_rating()

        self.submit_btn.Enable(False)
        self.submit_btn.SetLabel("Submitting…")
        wx.BeginBusyCursor()
        try:
            response = self.client.rate_board(
                self.board_id, speed, drc, solution, feedback
            )
        finally:
            wx.EndBusyCursor()

        if response.success:
            wx.MessageBox(
                "Thanks for your feedback!",
                "Rating submitted",
                wx.OK | wx.ICON_INFORMATION,
            )
            self.EndModal(wx.ID_OK)
        else:
            wx.MessageBox(
                f"Failed to submit rating:\n\n{response.error}",
                "Rating Error",
                wx.OK | wx.ICON_ERROR,
            )
            self.submit_btn.SetLabel("Submit")
            self.submit_btn.Enable(True)
