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
Dockable Panel Base Class for KiCad Plugins

This module provides a base class for creating dockable panels that integrate
with KiCad's interface.
"""

import os
import wx
import wx.aui


def is_dark_theme():
    """Detect if the current wx theme is dark based on background luminance."""
    bg = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)
    luminance = 0.299 * bg.Red() + 0.587 * bg.Green() + 0.114 * bg.Blue()
    return luminance < 128


def get_icon_path(assets_dir, base_name):
    """Return the white icon variant for dark themes, grey for light themes."""
    if is_dark_theme():
        dark_path = os.path.join(assets_dir, f"{base_name}-white.png")
        if os.path.exists(dark_path):
            return dark_path
    return os.path.join(assets_dir, f"{base_name}.png")


def find_kicad_frame():
    """
    Find the KiCad PCB Editor main frame.

    Uses wx.FindWindowByName("PcbFrame"), which is the canonical approach
    used by KiCad's own Python shell and all major plugins (KiKit, KiVar,
    ReplicateLayout, etc.). The name "PcbFrame" is defined in KiCad's C++
    source as PCB_EDIT_FRAME_NAME in eda_draw_frame.h — it is stable across
    OS, locale, KiCad version, and launch method.

    Returns:
        The KiCad PCB Editor frame, or None if not found
    """
    frame = wx.FindWindowByName("PcbFrame")
    if frame:
        try:
            print(
                f"[DeepPCB] find_kicad_frame: found PcbFrame, "
                f"title='{frame.GetTitle().encode('ascii', errors='replace').decode('ascii')}'"
            )
        except Exception:
            print("[DeepPCB] find_kicad_frame: found PcbFrame")
        return frame

    print("[DeepPCB] find_kicad_frame: 'PcbFrame' not found")
    return None


def get_kicad_aui_manager():
    """
    Get KiCad's AuiManager to add dockable panels.
    Returns the AuiManager and parent frame, or (None, None) if not found.
    """
    frame = find_kicad_frame()
    if not frame:
        return None, None

    aui_mgr = wx.aui.AuiManager.GetManager(frame)

    return aui_mgr, frame


class KiCadDockablePanel(wx.Panel):
    # Override these in subclasses
    PANEL_NAME = "deeppcb_panel"
    PANEL_CAPTION = "DeepPCB"
    DOCK_DIRECTION = wx.aui.AUI_DOCK_RIGHT
    DEFAULT_SIZE = (500, 350)
    MIN_SIZE = (300, 200)

    _instances = {}
    _saved_pane_info = None

    def __init__(self, parent_frame=None):
        """
        Create a dockable panel integrated with KiCad's AUI.

        Args:
            parent_frame: The KiCad main frame (auto-detected if None)
        """
        self._aui_mgr = None
        self._frame = parent_frame or find_kicad_frame()

        if not self._frame:
            raise RuntimeError(
                "Could not find the PCB Editor. Please open your .kicad_pcb file in the PCB Editor before using DeepPCB."
            )

        self._aui_mgr, _ = get_kicad_aui_manager()

        if not self._aui_mgr:
            raise RuntimeError("Could not access KiCad's AuiManager")

        super().__init__(self._frame, style=wx.TAB_TRAVERSAL)

        self.SetMinSize(wx.Size(*self.MIN_SIZE))
        self.SetSize(wx.Size(*self.DEFAULT_SIZE))

        self.create_ui()

        self._add_to_kicad_aui()

        KiCadDockablePanel._instances[self.PANEL_NAME] = self

    def _add_to_kicad_aui(self):
        """Add this panel to KiCad's AuiManager."""
        self.Layout()
        sizer = self.GetSizer()
        if sizer:
            best_size = sizer.GetMinSize()
        else:
            best_size = self.GetBestSize()
        pane_info = wx.aui.AuiPaneInfo()
        pane_info.Name(self.PANEL_NAME)
        pane_info.Caption(self.PANEL_CAPTION)
        pane_info.BestSize(best_size)
        pane_info.MinSize(best_size)
        pane_info.CloseButton(True)
        pane_info.MaximizeButton(True)
        pane_info.PinButton(True)
        pane_info.Floatable(True)
        pane_info.Dockable(True)
        pane_info.Show(True)

        # Apply saved layout from previous panel if available
        saved = KiCadDockablePanel._saved_pane_info
        if saved:
            if saved.IsFloating():
                pane_info.Float()
                pane_info.FloatingPosition(saved.floating_pos)
                pane_info.FloatingSize(best_size)
            else:
                # Apply dock direction
                if saved.dock_direction == wx.aui.AUI_DOCK_TOP:
                    pane_info.Top()
                elif saved.dock_direction == wx.aui.AUI_DOCK_BOTTOM:
                    pane_info.Bottom()
                elif saved.dock_direction == wx.aui.AUI_DOCK_LEFT:
                    pane_info.Left()
                elif saved.dock_direction == wx.aui.AUI_DOCK_RIGHT:
                    pane_info.Right()
                elif saved.dock_direction == wx.aui.AUI_DOCK_CENTER:
                    pane_info.Center()

                pane_info.Layer(saved.dock_layer)
                pane_info.Position(saved.dock_pos)
                pane_info.Row(saved.dock_row)
        else:
            # Default position: dock on the right
            pane_info.Right()
            pane_info.Layer(1)
            pane_info.Position(0)

        # Use content-based size for floating
        pane_info.FloatingSize(best_size)

        # Check if pane already exists
        existing = self._aui_mgr.GetPane(self.PANEL_NAME)
        if existing.IsOk():
            # Remove existing pane first
            self._aui_mgr.DetachPane(existing.window)
            if existing.window:
                existing.window.Destroy()

        self._aui_mgr.AddPane(self, pane_info)
        self._aui_mgr.Update()

        self._frame.Bind(wx.aui.EVT_AUI_PANE_CLOSE, self._on_aui_pane_close)

    def create_ui(self):
        """Override this method to create your panel's UI."""
        pass

    def _on_aui_pane_close(self, event):
        pane = event.GetPane()
        # Check if this event is for our pane
        if pane.name == self.PANEL_NAME:
            # Save the layout before closing
            self._save_pane_layout()

            # Call our cleanup
            self.on_panel_close()

            # Remove from instances
            if self.PANEL_NAME in KiCadDockablePanel._instances:
                del KiCadDockablePanel._instances[self.PANEL_NAME]

            event.Skip()

            # Schedule destruction after event processing
            wx.CallAfter(self._destroy_after_close)
        else:
            # Not our pane, let it propagate
            event.Skip()

    def _destroy_after_close(self):
        """Destroy the panel after the close event has been processed."""
        try:
            if self._aui_mgr:
                self._aui_mgr.DetachPane(self)
                self._aui_mgr.Update()
            self.Destroy()
        except Exception:
            pass

    def show_panel(self):
        """Show the panel and bring it to focus."""
        if self._aui_mgr:
            pane = self._aui_mgr.GetPane(self.PANEL_NAME)
            if pane.IsOk():
                pane.Show(True)
                # If it's floating, raise the floating frame
                if pane.IsFloating() and pane.frame:
                    pane.frame.Raise()
                self._aui_mgr.Update()
                # Also set focus to the panel
                self.SetFocus()

    def is_valid(self):
        """Check if the panel is still valid (not destroyed)."""
        try:
            return self.GetHandle() != 0
        except Exception:
            return False

    def _save_pane_layout(self):
        """Save the current pane POSITION (not size) so it can be restored by the next panel."""
        if self._aui_mgr:
            pane = self._aui_mgr.GetPane(self.PANEL_NAME)
            if pane.IsOk():
                # Create a copy of the pane info to save
                saved = wx.aui.AuiPaneInfo()
                saved.dock_direction = pane.dock_direction
                saved.dock_layer = pane.dock_layer
                saved.dock_row = pane.dock_row
                saved.dock_pos = pane.dock_pos
                saved.floating_pos = pane.floating_pos

                if pane.IsFloating():
                    saved.Float()
                    saved.FloatingPosition(pane.floating_pos)
                else:
                    if pane.dock_direction == wx.aui.AUI_DOCK_TOP:
                        saved.Top()
                    elif pane.dock_direction == wx.aui.AUI_DOCK_BOTTOM:
                        saved.Bottom()
                    elif pane.dock_direction == wx.aui.AUI_DOCK_LEFT:
                        saved.Left()
                    elif pane.dock_direction == wx.aui.AUI_DOCK_RIGHT:
                        saved.Right()
                    elif pane.dock_direction == wx.aui.AUI_DOCK_CENTER:
                        saved.Center()

                saved.Layer(pane.dock_layer)
                saved.Position(pane.dock_pos)
                saved.Row(pane.dock_row)
                saved.CloseButton(True)
                saved.MaximizeButton(True)
                saved.PinButton(True)
                saved.Floatable(True)
                saved.Dockable(True)

                KiCadDockablePanel._saved_pane_info = saved

    @classmethod
    def clear_saved_layout(cls):
        cls._saved_pane_info = None

    def close_panel(self, save_layout=True):
        # Unbind the close event to prevent double-handling
        try:
            self._frame.Unbind(
                wx.aui.EVT_AUI_PANE_CLOSE, handler=self._on_aui_pane_close
            )
        except Exception:
            pass

        if save_layout:
            self._save_pane_layout()

        self.on_panel_close()

        if self._aui_mgr:
            pane = self._aui_mgr.GetPane(self.PANEL_NAME)
            if pane.IsOk():
                self._aui_mgr.DetachPane(self)
                self._aui_mgr.Update()

        if self.PANEL_NAME in KiCadDockablePanel._instances:
            del KiCadDockablePanel._instances[self.PANEL_NAME]

        self.Destroy()

    def on_panel_close(self):
        """Override to add cleanup logic when panel is closed."""
        pass

    def update_panel_size(self):
        """Update the panel size to fit content, especially useful for floating panels."""
        self.Layout()
        sizer = self.GetSizer()
        if sizer:
            best_size = sizer.GetMinSize()
        else:
            best_size = self.GetBestSize()
        if self._aui_mgr:
            pane = self._aui_mgr.GetPane(self.PANEL_NAME)
            if pane.IsOk():
                pane.BestSize(best_size)
                pane.MinSize(best_size)
                if pane.IsFloating() and pane.frame:
                    # Account for frame decorations (title bar, borders)
                    current_frame_size = pane.frame.GetSize()
                    current_client_size = pane.frame.GetClientSize()
                    decoration_width = (
                        current_frame_size.width - current_client_size.width
                    )
                    decoration_height = (
                        current_frame_size.height - current_client_size.height
                    )
                    frame_size = wx.Size(
                        best_size.width + decoration_width,
                        best_size.height + decoration_height,
                    )
                    pane.FloatingSize(frame_size)
                    pane.frame.SetClientSize(best_size)
                self._aui_mgr.Update()

    @classmethod
    def get_instance(cls, name):
        """Get an existing panel instance by name."""
        return cls._instances.get(name)

    @classmethod
    def close_all(cls):
        """Close all dockable panel instances."""
        for name in list(cls._instances.keys()):
            instance = cls._instances.get(name)
            if instance:
                try:
                    instance.close_panel()
                except Exception:
                    pass
