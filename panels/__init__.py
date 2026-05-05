"""
DeepPCB Panels Module

This module contains dockable panels that integrate with KiCad's AUI interface.
"""

from .dockable_panel import (
    KiCadDockablePanel,
    find_kicad_frame,
    get_kicad_aui_manager,
)
from .board_status_panel import BoardStatusPanel
from .board_creation_panel import BoardCreationPanel

__all__ = [
    "KiCadDockablePanel",
    "find_kicad_frame",
    "get_kicad_aui_manager",
    "BoardStatusPanel",
    "BoardCreationPanel",
]
