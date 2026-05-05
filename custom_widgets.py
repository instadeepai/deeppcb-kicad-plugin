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
