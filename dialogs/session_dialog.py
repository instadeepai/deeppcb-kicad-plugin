import wx
import os
import json


class SessionDialog(wx.Dialog):
    def __init__(self, parent, project_name, project_directory):
        super().__init__(
            parent,
            title="Board Detected",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )

        self.project_name = project_name
        self.project_directory = project_directory
        self.session_data = None
        self.choice = None

        self.load_session_data()
        self.create_ui()
        self.EnableLayoutAdaptation(True)

    def load_session_data(self):
        data_file = os.path.join(self.project_directory, "data")

        if os.path.exists(data_file):
            try:
                with open(data_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    try:
                        self.session_data = json.loads(content)
                    except json.JSONDecodeError:
                        self.session_data = {"board_id": content}
            except Exception:
                self.session_data = None

    def create_ui(self):
        panel = wx.Panel(self)
        panel_sizer = wx.BoxSizer(wx.VERTICAL)

        message = wx.StaticText(
            panel,
            label="It looks like you already started a run with this board. "
            "Do you want to restore it or start a new one?\n\n"
            "If you start a new run, the old one won't be deleted, you can always view it on DeepPCB.",
        )

        font = message.GetFont()
        font.SetPointSize(font.GetPointSize() + 1)
        message.SetFont(font)
        message.Wrap(500)

        panel_sizer.Add(message, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 20)
        panel_sizer.Add((0, 10))

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.cancel_btn = wx.Button(panel, label="Close")
        self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
        button_sizer.Add(self.cancel_btn, 0, wx.ALL, 5)
        button_sizer.AddStretchSpacer()

        self.new_btn = wx.Button(panel, label="Start New Run")
        self.new_btn.Bind(wx.EVT_BUTTON, self.on_new)
        button_sizer.Add(self.new_btn, 0, wx.ALL, 5)

        self.restore_btn = wx.Button(panel, label="Restore Old Run")
        self.restore_btn.Bind(wx.EVT_BUTTON, self.on_restore)
        button_sizer.Add(self.restore_btn, 0, wx.ALL, 5)

        panel_sizer.Add(button_sizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

        panel.SetSizer(panel_sizer)
        panel.Layout()
        best_size = panel.GetBestSize()

        dialog_sizer = wx.BoxSizer(wx.VERTICAL)
        dialog_sizer.Add(panel, 1, wx.EXPAND | wx.ALL, 0)

        self.SetSizer(dialog_sizer)
        self.SetMinSize(best_size)
        self.Fit()
        self.Centre()

    def on_restore(self, event):
        self.choice = "restore"
        self.EndModal(wx.ID_OK)

    def on_new(self, event):
        self.choice = "new"
        self.EndModal(wx.ID_OK)

    def on_cancel(self, event):
        self.choice = None
        self.EndModal(wx.ID_CANCEL)

    def get_board_id(self):
        if self.session_data:
            return self.session_data.get("board_id")
        return None

    def get_choice(self):
        return self.choice


def check_and_show_session_dialog(parent, project_name, project_directory):
    data_file = os.path.join(project_directory, "data")

    if not os.path.exists(data_file):
        return ("new", None)

    dialog = SessionDialog(parent, project_name, project_directory)
    result = dialog.ShowModal()

    if result == wx.ID_CANCEL:
        dialog.Destroy()
        return (None, None)

    choice = dialog.get_choice()
    session_data = {"board_id": dialog.get_board_id()}

    dialog.Destroy()
    return (choice, session_data)


def save_session_data(project_directory, board_id):
    data_file = os.path.join(project_directory, "data")
    session_data = {"board_id": board_id}

    try:
        with open(data_file, "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2)
    except Exception as e:
        print(f"Error saving session data: {e}")
