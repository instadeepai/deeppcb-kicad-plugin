"""
KiCad PCB S-expression parser module.

This module provides functions to parse KiCad PCB files (S-expressions)
and extract track and footprint information.
"""


def parse_sexp_string(sexp_str):
    """
    Parse an S-expression string into a nested list structure.
    Returns a list representation of the S-expression.
    """

    # Simple recursive S-expression parser fallback
    def parse_at_index(idx):
        result = []
        current_token = []
        in_string = False
        escape_next = False

        while idx < len(sexp_str):
            char = sexp_str[idx]

            if escape_next:
                current_token.append(char)
                escape_next = False
                idx += 1
                continue

            if char == "\\" and in_string:
                escape_next = True
                current_token.append(char)
                idx += 1
                continue

            if char == '"':
                in_string = not in_string
                current_token.append(char)
            elif char == "(" and not in_string:
                if current_token:
                    token_str = "".join(current_token).strip()
                    if token_str:
                        result.append(token_str)
                    current_token = []
                idx, sublist = parse_at_index(idx + 1)
                result.append(sublist)
            elif char == ")" and not in_string:
                if current_token:
                    token_str = "".join(current_token).strip()
                    if token_str:
                        result.append(token_str)
                return idx + 1, result
            elif char in [" ", "\n", "\t", "\r"] and not in_string:
                if current_token:
                    token_str = "".join(current_token).strip()
                    if token_str:
                        result.append(token_str)
                    current_token = []
            else:
                current_token.append(char)
            idx += 1

        if current_token:
            token_str = "".join(current_token).strip()
            if token_str:
                result.append(token_str)

        return idx, result

    _, parsed = parse_at_index(0)
    return parsed[0] if parsed else []


def extract_tracks_from_sexp(sexp_data):
    """
    Extract track information from parsed S-expression data.
    Returns a list of track dictionaries with their properties.
    """
    tracks = []

    def find_tracks(obj):
        if not isinstance(obj, list) or len(obj) == 0:
            return

        first = obj[0]
        track_type = None

        if isinstance(first, str):
            track_type = first
        elif isinstance(first, list) and len(first) > 0:
            track_type = first[0] if isinstance(first[0], str) else None

        if track_type in ["segment", "via"]:
            track_info = {"type": track_type}

            for item in obj[1:]:
                if isinstance(item, list) and len(item) >= 2:
                    key = item[0]

                    if key in ["start", "end", "mid", "center", "at"]:
                        if len(item) >= 3:
                            try:
                                x = float(item[1])
                                y = float(item[2])
                                track_info[key] = {"x": x, "y": y}
                            except (ValueError, TypeError):
                                pass

                    elif key == "width":
                        if len(item) >= 2:
                            try:
                                track_info["width"] = float(item[1])
                            except (ValueError, TypeError):
                                pass

                    elif key == "layer":
                        if len(item) >= 2:
                            layer_str = str(item[1])
                            track_info["layer"] = layer_str.strip('"').strip("'")

                    elif key == "net":
                        if len(item) >= 2:
                            try:
                                track_info["net"] = int(item[1])
                            except (ValueError, TypeError):
                                pass

                    elif key == "size":
                        if len(item) >= 2:
                            try:
                                track_info["size"] = float(item[1])
                            except (ValueError, TypeError):
                                pass

                    elif key == "drill":
                        if len(item) >= 2:
                            try:
                                track_info["drill"] = float(item[1])
                            except (ValueError, TypeError):
                                pass

                    elif key == "layers":
                        if len(item) >= 2:
                            layers = []
                            for layer_item in item[1:]:
                                layer_str = str(layer_item).strip('"').strip("'")
                                layers.append(layer_str)
                            track_info["layers"] = layers

                    elif key == "tstamp":
                        if len(item) >= 2:
                            track_info["tstamp"] = str(item[1])

            tracks.append(track_info)
        else:
            # Recursively search nested structures
            for item in obj:
                if isinstance(item, list):
                    find_tracks(item)

    find_tracks(sexp_data)
    return tracks


def extract_footprints_from_sexp(sexp_data):
    """
    Extract footprint information from parsed S-expression data.
    Returns a list of footprint dictionaries with their properties.
    """
    footprints = []

    def find_footprints(obj):
        if not isinstance(obj, list) or len(obj) == 0:
            return

        first = obj[0]
        element_type = None

        if isinstance(first, str):
            element_type = first
        elif isinstance(first, list) and len(first) > 0:
            element_type = first[0] if isinstance(first[0], str) else None

        if element_type in ["footprint", "module"]:  # 'module' for older KiCad versions
            footprint_info = {"type": "footprint"}

            # Get footprint library and name (second element)
            if len(obj) >= 2 and isinstance(obj[1], str):
                footprint_info["library_id"] = obj[1].strip('"')

            # Extract properties
            for item in obj[1:] if len(obj) > 1 else []:
                if isinstance(item, list) and len(item) >= 2:
                    key = item[0]

                    if key == "at":
                        if len(item) >= 3:
                            try:
                                x = float(item[1])
                                y = float(item[2])
                                footprint_info["at"] = {"x": x, "y": y}
                                if len(item) >= 4:
                                    footprint_info["rotation"] = float(item[3])
                                else:
                                    footprint_info["rotation"] = 0.0
                            except (ValueError, TypeError):
                                pass

                    elif key == "layer":
                        if len(item) >= 2:
                            layer_str = str(item[1])
                            footprint_info["layer"] = layer_str.strip('"').strip("'")

                    elif key == "property" or key == "fp_text":
                        if len(item) >= 3:
                            prop_type = str(item[1]).strip('"').strip("'")
                            prop_value = str(item[2]).strip('"').strip("'")

                            if prop_type.lower() == "reference":
                                footprint_info["reference"] = prop_value
                            elif prop_type.lower() == "value":
                                footprint_info["value"] = prop_value
                            elif prop_type.lower() == "footprint":
                                footprint_info["footprint"] = prop_value

                    elif key == "tstamp" or key == "uuid":
                        if len(item) >= 2:
                            footprint_info["tstamp"] = str(item[1])

            footprints.append(footprint_info)
        else:
            for item in obj:
                if isinstance(item, list):
                    find_footprints(item)

    find_footprints(sexp_data)
    return footprints
