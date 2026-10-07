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
Map DeepPCB chat tool calls to pcbnew operations.

The web tool catalog targets the in-browser 3D viewer. Several operations have
a KiCad analogue (component selection, net highlight, layer visibility); the
3D-viewer-specific ones (planes, screenshot, etc.) have no equivalent and are
logged as unsupported but reported as a non-error so the agent isn't blocked.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

import pcbnew


ToolResult = Tuple[bool, str]


def _get_board() -> Optional[pcbnew.BOARD]:
    try:
        return pcbnew.GetBoard()
    except Exception:
        return None


def _refresh_canvas() -> None:
    # Redraw the board (GAL) canvas. Must be the module-level pcbnew.Refresh()
    # — a wx frame.Refresh() repaints the window chrome, not the OpenGL/Cairo
    # board view, so model changes (selection, highlight, layer visibility)
    # would never render. Matches utils.py's board redraw.
    try:
        pcbnew.Refresh()
    except Exception:
        pass


def _find_footprint(board: pcbnew.BOARD, ref: str):
    try:
        fp = board.FindFootprintByReference(ref)
        if fp:
            return fp
    except Exception:
        pass
    for fp in board.GetFootprints():
        try:
            if fp.GetReference() == ref:
                return fp
        except Exception:
            continue
    return None


def _clear_item_marks(item) -> None:
    # Clear both the selection flag and the brightened flag — select_component
    # sets both (see _select_component) so clearing must undo both.
    for method in ("ClearSelected", "ClearBrightened"):
        try:
            getattr(item, method)()
        except Exception:
            pass


def _clear_selection(board: pcbnew.BOARD) -> None:
    for fp in board.GetFootprints():
        _clear_item_marks(fp)
    for track in board.GetTracks():
        _clear_item_marks(track)


def _select_component(args: Dict[str, Any]) -> ToolResult:
    ref = args.get("componentRef") or args.get("component_ref") or args.get("ref")
    if not ref:
        return False, "Missing componentRef"
    board = _get_board()
    if board is None:
        return False, "No active board"
    fp = _find_footprint(board, ref)
    if fp is None:
        return False, f"Component {ref} not found"
    _clear_selection(board)
    try:
        fp.SetSelected()
    except Exception as e:
        return False, f"SetSelected failed: {e}"
    # Also brighten it: on some KiCad versions the selection flag alone doesn't
    # render a visible halo (selection is owned by the selection tool), whereas
    # the brightened flag is drawn directly by the painter.
    try:
        fp.SetBrightened()
    except Exception:
        pass
    _refresh_canvas()
    return True, f"Selected {ref}"


def _clear_selection_tool(_args: Dict[str, Any]) -> ToolResult:
    board = _get_board()
    if board is None:
        return False, "No active board"
    _clear_selection(board)
    _refresh_canvas()
    return True, "Selection cleared"


def _find_net(board: pcbnew.BOARD, net_id):
    netinfo = board.GetNetInfo()
    try:
        if isinstance(net_id, int) or (isinstance(net_id, str) and net_id.isdigit()):
            net = netinfo.GetNetItem(int(net_id))
            if net and net.GetNetCode() != 0:
                return net
    except Exception:
        pass
    try:
        for code in range(netinfo.GetNetCount()):
            net = netinfo.GetNetItem(code)
            if net and net.GetNetname() == str(net_id):
                return net
    except Exception:
        pass
    return None


def _brighten_net(board: pcbnew.BOARD, netcode: int) -> int:
    """Brighten every track/via/pad on the net. The painter draws the brightened
    flag directly, so this renders in the GAL canvas — unlike the legacy
    board.SetHighLightNet path, which the modern view does not read from."""
    count = 0
    for track in board.GetTracks():
        try:
            if track.GetNetCode() == netcode:
                track.SetBrightened()
                count += 1
        except Exception:
            pass
    for fp in board.GetFootprints():
        try:
            for pad in fp.Pads():
                if pad.GetNetCode() == netcode:
                    pad.SetBrightened()
                    count += 1
        except Exception:
            pass
    return count


def _clear_all_brightened(board: pcbnew.BOARD) -> None:
    for track in board.GetTracks():
        try:
            track.ClearBrightened()
        except Exception:
            pass
    for fp in board.GetFootprints():
        try:
            fp.ClearBrightened()
            for pad in fp.Pads():
                pad.ClearBrightened()
        except Exception:
            pass


def _highlight_net(args: Dict[str, Any]) -> ToolResult:
    net_id = args.get("netId") or args.get("net_id") or args.get("net")
    if net_id is None:
        return False, "Missing netId"
    board = _get_board()
    if board is None:
        return False, "No active board"
    net = _find_net(board, net_id)
    if net is None:
        return False, f"Net {net_id} not found"
    netcode = net.GetNetCode()
    count = _brighten_net(board, netcode)
    # Legacy board highlight as a harmless secondary (honoured on some setups).
    try:
        board.SetHighLightNet(netcode, True)
        board.HighLightON()
    except Exception:
        pass
    _refresh_canvas()
    return True, f"Highlighted net {net_id} ({count} items)"


def _clear_highlight(_args: Dict[str, Any]) -> ToolResult:
    board = _get_board()
    if board is None:
        return False, "No active board"
    _clear_all_brightened(board)
    try:
        board.ResetNetHighLight()
    except Exception:
        try:
            board.SetHighLightNet(-1)
        except Exception:
            pass
    try:
        board.HighLightOFF()
    except Exception:
        pass
    _refresh_canvas()
    return True, "Highlights cleared"


def _show_only_net(args: Dict[str, Any]) -> ToolResult:
    ok, _ = _clear_highlight({})
    if not ok:
        return False, "Could not reset highlights"
    return _highlight_net(args)


def _coerce_bool(value: Any, current: bool) -> bool:
    if value is None or value == "toggle":
        return not current
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "show", "yes", "on", "1")
    return bool(value)


def _toggle_layer(args: Dict[str, Any]) -> ToolResult:
    layer_index = args.get("layerIndex")
    if layer_index is None:
        layer_index = args.get("layer_index")
    if layer_index is None:
        return False, "Missing layerIndex"
    try:
        layer_index = int(layer_index)
    except (TypeError, ValueError):
        return False, "layerIndex must be int"
    board = _get_board()
    if board is None:
        return False, "No active board"
    try:
        visible = board.GetVisibleLayers()
        current = visible.Contains(layer_index)
        new_value = _coerce_bool(args.get("visible"), current)
        if new_value == current:
            return (
                True,
                f"Layer {layer_index} already {'visible' if current else 'hidden'}",
            )
        if new_value:
            visible.AddLayer(layer_index)
        else:
            visible.RemoveLayer(layer_index)
        board.SetVisibleLayers(visible)
    except Exception as e:
        return False, f"Layer toggle failed: {e}"
    _refresh_canvas()
    return True, f"Layer {layer_index} set to {'visible' if new_value else 'hidden'}"


def _unsupported_factory(name: str) -> Callable[[Dict[str, Any]], ToolResult]:
    # Reported as a success so the agent is not blocked mid-turn; the message is
    # therefore the only thing stopping it claiming the action happened.
    def _impl(_args: Dict[str, Any]) -> ToolResult:
        return True, (
            f"'{name}' is not available in the KiCad plugin - it exists only in "
            "the DeepPCB web app. Tell the user this action is not supported here."
        )

    return _impl


_HANDLERS: Dict[str, Callable[[Dict[str, Any]], ToolResult]] = {
    "select_component": _select_component,
    "clear_selection": _clear_selection_tool,
    "highlight_net": _highlight_net,
    "clear_highlight": _clear_highlight,
    "show_only_net": _show_only_net,
    "toggle_layer": _toggle_layer,
    "toggle_visibility": _unsupported_factory("toggle_visibility"),
    "toggle_plane": _unsupported_factory("toggle_plane"),
    "take_screenshot": _unsupported_factory("take_screenshot"),
    "select_pin": _unsupported_factory("select_pin"),
    "suggest_actions": _unsupported_factory("suggest_actions"),
}


def execute_tool_call(tool_call: Dict[str, Any]) -> ToolResult:
    name = (tool_call.get("name") or "").strip()
    args = tool_call.get("args") or {}
    handler = _HANDLERS.get(name)
    if handler is None:
        return True, f"Tool '{name}' runs server-side"
    try:
        ok, msg = handler(args)
    except Exception as e:
        return False, f"{name} failed: {e}"
    return ok, msg


def execute_tool_calls(tool_calls: List[Dict[str, Any]]) -> List[ToolResult]:
    return [execute_tool_call(tc) for tc in tool_calls]


def execute_for_resume(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a frontend-tool call and return the AG-UI resume payload.

    Shape mirrors the web tool handler's ToolExecutionResult
    ({status: 'success'|'error', message}) — sent back to the agent via
    command.resume so it can reason about the outcome and continue the run.
    """
    ok, msg = execute_tool_call(tool_call)
    return {"status": "success" if ok else "error", "message": msg}


def has_local_handler(tool_name: str) -> bool:
    return tool_name in _HANDLERS
