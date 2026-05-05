import os
import wx
import pcbnew
import time
from datetime import datetime
from .helpers import DeepPCBClient


def download_and_save_board(
    client: DeepPCBClient, board_id: str, revision_number: int, output_filename: str
):
    """
    Download a board revision and save it to a file.

    Parameters:
        client: DeepPCBClient instance
        board_id: The unique identifier of the board
        revision_number: The revision number to download
        output_filename: Path to save the downloaded board
    """
    response = client.download_board(
        board_id=board_id, type="KicadFile", revision_number=revision_number
    )

    if response.success:
        try:
            output_dir = os.path.dirname(output_filename)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)

            with open(output_filename, "w", encoding="utf-8") as f:
                f.write(response.content)

            print(f"Board downloaded successfully and saved as: {output_filename}")
            return {
                "success": True,
                "status": response.status,
                "file_path": os.path.abspath(output_filename),
                "message": "Board downloaded and saved successfully",
            }
        except Exception as e:
            wx.MessageBox("Error saving file:", f"{e}", wx.OK | wx.ICON_ERROR)
            return {
                "success": False,
                "status": response.status,
                "error": f"Failed to save file: {e}",
            }
    else:
        wx.MessageBox(
            f"Download failed with status code: {response.status}",
            f"Response: {response.error}",
        )
        return {
            "success": False,
            "status": response.status,
            "error": f"Download failed: {response.error}",
        }


def create_track_from_dict(board, track_dict):
    """
    Create a pcbnew track object from a track dictionary.
    """
    try:
        track_type = track_dict.get("type")

        if track_type == "segment":
            track = pcbnew.PCB_TRACK(board)
            if "start" in track_dict:
                start = track_dict["start"]
                track.SetStart(
                    pcbnew.VECTOR2I(
                        pcbnew.FromMM(float(start["x"])),
                        pcbnew.FromMM(float(start["y"])),
                    )
                )
            if "end" in track_dict:
                end = track_dict["end"]
                track.SetEnd(
                    pcbnew.VECTOR2I(
                        pcbnew.FromMM(float(end["x"])), pcbnew.FromMM(float(end["y"]))
                    )
                )
            if "width" in track_dict:
                track.SetWidth(pcbnew.FromMM(float(track_dict["width"])))
            if "layer" in track_dict:
                layer_id = board.GetLayerID(track_dict["layer"])
                if layer_id != pcbnew.UNDEFINED_LAYER:
                    track.SetLayer(layer_id)
            if "net" in track_dict:
                net_code = track_dict["net"]
                net = board.FindNet(net_code)
                if not net:
                    for net_info in board.GetNetsByName():
                        if net_info.GetNetCode() == net_code:
                            net = net_info
                            break
                if net:
                    track.SetNet(net)
            return track

        elif track_type == "arc":
            track = pcbnew.PCB_ARC(board)
            if "start" in track_dict:
                start = track_dict["start"]
                track.SetStart(
                    pcbnew.VECTOR2I(
                        pcbnew.FromMM(float(start["x"])),
                        pcbnew.FromMM(float(start["y"])),
                    )
                )
            if "mid" in track_dict:
                mid = track_dict["mid"]
                track.SetMid(
                    pcbnew.VECTOR2I(
                        pcbnew.FromMM(float(mid["x"])), pcbnew.FromMM(float(mid["y"]))
                    )
                )
            if "end" in track_dict:
                end = track_dict["end"]
                track.SetEnd(
                    pcbnew.VECTOR2I(
                        pcbnew.FromMM(float(end["x"])), pcbnew.FromMM(float(end["y"]))
                    )
                )
            if "width" in track_dict:
                track.SetWidth(pcbnew.FromMM(float(track_dict["width"])))
            if "layer" in track_dict:
                layer_id = board.GetLayerID(track_dict["layer"])
                if layer_id != pcbnew.UNDEFINED_LAYER:
                    track.SetLayer(layer_id)
            if "net" in track_dict:
                net_code = track_dict["net"]
                net = board.FindNet(net_code)
                if not net:
                    for net_info in board.GetNetsByName():
                        if net_info.GetNetCode() == net_code:
                            net = net_info
                            break
                if net:
                    track.SetNet(net)
            return track

        elif track_type == "via":
            via = pcbnew.PCB_VIA(board)
            if "at" in track_dict:
                at = track_dict["at"]
                via.SetPosition(
                    pcbnew.VECTOR2I(
                        pcbnew.FromMM(float(at["x"])), pcbnew.FromMM(float(at["y"]))
                    )
                )
            if "size" in track_dict:
                via.SetWidth(pcbnew.FromMM(float(track_dict["size"])))
            if "drill" in track_dict:
                via.SetDrill(pcbnew.FromMM(float(track_dict["drill"])))
            if "layers" in track_dict:
                layers = track_dict["layers"]
                if len(layers) >= 2:
                    top_layer = board.GetLayerID(layers[0])
                    bottom_layer = board.GetLayerID(layers[-1])
                    if (
                        top_layer != pcbnew.UNDEFINED_LAYER
                        and bottom_layer != pcbnew.UNDEFINED_LAYER
                    ):
                        via.SetLayerPair(top_layer, bottom_layer)
            if "net" in track_dict:
                net_code = track_dict["net"]
                net = board.FindNet(net_code)
                if not net:
                    for net_info in board.GetNetsByName():
                        if net_info.GetNetCode() == net_code:
                            net = net_info
                            break
                if net:
                    via.SetNet(net)
            return via

    except Exception as e:
        print(f"Error creating track: {e}")
        return None

    return None


def update_footprint_positions(board, footprint_dicts):
    """
    Update positions and rotations of existing footprints on the board.
    Matches footprints by their UUID (tstamp) and updates their position and rotation.

    Returns:
        tuple: (updated_count, not_found_list)
    """
    # Build a map of existing footprints by UUID (tstamp)
    existing_footprints = {}
    for footprint in board.GetFootprints():
        uuid = str(footprint.m_Uuid.AsString())
        existing_footprints[uuid] = footprint

    print(f"Found {len(existing_footprints)} footprints on current board")

    updated_count = 0
    not_found = []

    for fp_dict in footprint_dicts:
        tstamp = fp_dict.get("tstamp")
        reference = fp_dict.get("reference", "Unknown")

        if not tstamp:
            print(
                f"Warning: Footprint {reference} without UUID/tstamp found in solution file, skipping"
            )
            continue

        tstamp_clean = str(tstamp).strip('"').strip("'")

        if tstamp_clean in existing_footprints:
            footprint = existing_footprints[tstamp_clean]

            if "at" in fp_dict:
                at = fp_dict["at"]
                position = pcbnew.VECTOR2I(
                    pcbnew.FromMM(float(at["x"])), pcbnew.FromMM(float(at["y"]))
                )
                footprint.SetPosition(position)

            if "rotation" in fp_dict:
                rotation_degrees = float(fp_dict["rotation"])
                footprint.SetOrientation(
                    pcbnew.EDA_ANGLE(rotation_degrees, pcbnew.DEGREES_T)
                )

            if "layer" in fp_dict:
                layer_id = board.GetLayerID(fp_dict["layer"])
                if layer_id != pcbnew.UNDEFINED_LAYER:
                    footprint.SetLayer(layer_id)

            updated_count += 1
        else:
            not_found.append(f"{reference} (UUID: {tstamp_clean[:8]}...)")

    return updated_count, not_found


def load_and_render_board(solution_filename):
    from .parser import (
        parse_sexp_string,
        extract_tracks_from_sexp,
        extract_footprints_from_sexp,
    )

    try:
        if not os.path.isfile(solution_filename):
            raise FileNotFoundError(f"File does not exist: {solution_filename}")

        board = pcbnew.GetBoard()
        if not board or not hasattr(board, "GetTracks"):
            raise RuntimeError(
                "No active board found. Please open your .kicad_pcb file in the PCB Editor."
            )

        with open(solution_filename, "r", encoding="utf-8") as f:
            file_content = f.read()

        parsed_data = parse_sexp_string(file_content)
        track_dicts = extract_tracks_from_sexp(parsed_data)
        footprint_dicts = extract_footprints_from_sexp(parsed_data)

        existing_tracks = list(board.GetTracks())
        for track in existing_tracks:
            board.Remove(track)

        # Create and add tracks from the solution file
        added_count = 0
        for track_dict in track_dicts:
            track = create_track_from_dict(board, track_dict)
            print(
                f"Track created: {track.GetStart()}, {track.GetEnd()}, {track.GetWidth()}, {track.GetLayer()}, {track.GetNet()}"
            )
            if track:
                board.Add(track)
                added_count += 1

        print(f"Added {added_count} tracks from solution file")

        # Update footprint positions and rotations
        updated_count, not_found = update_footprint_positions(board, footprint_dicts)
        print(f"Updated {updated_count} footprint positions from solution file")
        if not_found:
            print(
                f'Warning: {len(not_found)} footprints not found on current board: {", ".join(not_found)}'
            )

        # Refresh the display
        pcbnew.Refresh()
    except Exception as e:
        wx.MessageBox(
            f"{e}", "Error while loading and rendering board:", wx.OK | wx.ICON_ERROR
        )


def poll_board_status(
    client: DeepPCBClient,
    board_id: str,
    expected_status: str,
    timeout: int = 10,
    poll_interval: int = 1,
):
    """
    Polls the board status repeatedly until it matches the expected status or timeout.

    Parameters:
        client: DeepPCBClient instance
        board_id: The unique identifier of the board to check
        expected_status: The expected board status to wait for (e.g., "Created", "Running", "Completed")
        timeout: Maximum time to wait in seconds (default: 10)
        poll_interval: Time between polls in seconds (default: 1)

    Returns:
        Dict containing:
            - success (bool): True if expected status was reached
            - status (int): HTTP status code of last response
            - board_status (str): The board status from the response
            - message (str): Status message
            - response (dict): Full board response if successful
    """
    start_time = time.time()
    last_response = None

    while time.time() - start_time < timeout:
        response = client.check_board_status(board_id)
        last_response = response

        if response.success and response.board:
            current_status = response.board.board_status

            if current_status == expected_status:
                return {
                    "success": True,
                    "status": response.status,
                    "board_status": current_status,
                    "message": f"Board status reached: {expected_status}",
                    "board": response.board,
                }

            time.sleep(poll_interval)
        else:
            time.sleep(poll_interval)

    if last_response and last_response.success and last_response.board:
        current_status = last_response.board.board_status
        return {
            "success": False,
            "status": last_response.status,
            "board_status": current_status,
            "message": f"Timeout: Expected '{expected_status}', got '{current_status}'",
            "timeout": True,
        }

    return {
        "success": False,
        "status": last_response.status if last_response else None,
        "board_status": None,
        "message": "Failed to get board status - timeout reached without successful response",
        "timeout": True,
    }


def calculate_remaining_time(started_on_str, timeout_minutes):
    """
    Calculate the remaining time for a workflow based on start time and timeout.

    Parameters:
        started_on_str (str): ISO 8601 timestamp string (e.g., "2025-12-01T11:47:21.209851+00:00")
        timeout_minutes (int/float): Timeout duration in minutes

    Returns:
        Dict containing:
            - success (bool): True if calculation was successful
            - remaining_minutes (int): Remaining minutes
            - remaining_seconds (int): Remaining seconds (within the current minute)
            - total_remaining_seconds (int): Total remaining time in seconds
            - message (str): Formatted time string (e.g., "45m 30s remaining")
    """
    try:
        started_on = datetime.fromisoformat(started_on_str.replace("Z", "+00:00"))
        current_time = datetime.now(started_on.tzinfo)
        elapsed_time_minutes = (current_time - started_on).total_seconds() / 60
        remaining_seconds = max(0, int((timeout_minutes - elapsed_time_minutes) * 60))
        remaining_minutes = remaining_seconds // 60
        remaining_secs = remaining_seconds % 60

        return {
            "success": True,
            "remaining_minutes": remaining_minutes,
            "remaining_seconds": remaining_secs,
            "total_remaining_seconds": remaining_seconds,
            "message": f"{remaining_minutes}m {remaining_secs}s remaining",
        }
    except (ValueError, TypeError, AttributeError) as e:
        return {
            "success": False,
            "remaining_minutes": 0,
            "remaining_seconds": 0,
            "total_remaining_seconds": 0,
            "message": "Time calculation unavailable",
            "error": str(e),
        }
