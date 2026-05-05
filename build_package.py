#!/usr/bin/env python3
"""
Cross-platform script to build the KiCad plugin package archive.

Creates a .zip file with the following structure:
- plugins/       : Contains all plugin source files from KiCad folder
- resources/     : Contains icon.png
- metadata.json  : Plugin metadata at the root (auto-generated)

Usage: python build_package.py [options]
"""

import os
import sys
import zipfile
import json
import argparse
import re
from pathlib import Path


# Metadata template - version is injected from config.py
METADATA_TEMPLATE = {
    "name": "DeepPCB",
    "description": "Route your board automatically using DeepPCB",
    "description_full": "Route your PCB directly from KiCad. The DeepPCB plugin connects to our cloud routing engine and sends results back into your project. No file exports, no workflow interruption. Pay only for what you use.",
    "identifier": "com.instadeep.deeppcb",
    "type": "plugin",
    "author": {"name": "DeepPCB Team", "contact": {}},
    "license": "Apache-2.0",
    "resources": {
        "homepage": "https://github.com/instadeep/deeppcb-kicad-plugin",
        "issues": "https://github.com/instadeep/deeppcb-kicad-plugin/issues",
        "repository": "https://github.com/instadeep/deeppcb-kicad-plugin",
    },
    "tags": ["pcbnew", "routing", "autorouter", "ai"],
    "versions": [
        {
            "version": "",  # Populated from config.py
            "status": "stable",
            "kicad_version": "6.0",
            "platforms": ["linux", "macos", "windows"],
        }
    ],
}


def get_script_dir() -> Path:
    """Get the directory where this script is located."""
    return Path(__file__).parent.resolve()


def get_version_from_config() -> str:
    """Extract APP_VERSION from config.py."""
    script_dir = get_script_dir()
    config_file = script_dir / "config.py"

    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            content = f.read()
            # Match APP_VERSION = "x.x.x" or APP_VERSION = 'x.x.x'
            match = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                return match.group(1)

    return "unknown"


def generate_metadata() -> dict:
    """Generate metadata dictionary with version from config.py."""
    metadata = METADATA_TEMPLATE.copy()
    metadata["versions"] = [v.copy() for v in METADATA_TEMPLATE["versions"]]

    version = get_version_from_config()
    metadata["versions"][0]["version"] = version

    return metadata


def should_exclude(path: Path) -> bool:
    """Check if a path should be excluded from the archive."""
    exclude_patterns = [
        "__pycache__",
        ".pyc",
        ".pyo",
        ".git",
        ".gitignore",
        ".github",
        "build_package.py",
        "README.md",
        "CHANGELOG.md",
        "LICENSE",
        ".pre-commit-config.yaml",
        ".ruff_cache",
        ".DS_Store",
        "Thumbs.db",
        ".zip",
    ]

    path_str = str(path)
    for pattern in exclude_patterns:
        if pattern in path_str:
            return True
    return False


def patch_config_urls(content: str, base_url: str, api_url: str) -> str:
    """Replace BASE_URL and API_URL values in config.py content."""
    content = re.sub(
        r'(BASE_URL\s*=\s*)["\'][^"\']+["\']',
        f'\\1"{base_url}"',
        content,
    )
    content = re.sub(
        r'(API_URL\s*=\s*)["\'][^"\']+["\']',
        f'\\1"{api_url}"',
        content,
    )
    return content


def create_archive(
    output_name: str = None,
    output_dir: Path = None,
    base_url: str = None,
    api_url: str = None,
) -> str:
    """
    Create the plugin archive.

    Args:
        output_name: Optional name for the output file (without extension)
        output_dir: Optional output directory (defaults to script directory)
        base_url: Optional custom BASE_URL to inject into config.py inside the ZIP
        api_url: Optional custom API_URL to inject into config.py inside the ZIP

    Returns:
        Path to the created archive
    """
    script_dir = get_script_dir()

    # Get version from config.py
    version = get_version_from_config()

    # Generate output filename using version
    if output_name is None:
        output_name = f"deeppcb-kicad-plugin-v{version}"

    # Use specified output dir or default to script directory
    if output_dir is None:
        output_dir = script_dir

    output_path = output_dir / f"{output_name}.zip"

    override_urls = base_url is not None and api_url is not None

    print(f"Creating archive: {output_path}")
    print(f"Version: {version}")
    if override_urls:
        print(f"URL override: BASE_URL={base_url}")
        print(f"              API_URL={api_url}")
    print("-" * 50)

    files_added = 0

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        # 1. Generate and add metadata.json at the root
        metadata = generate_metadata()
        metadata_json = json.dumps(metadata, indent=4)
        zipf.writestr("metadata.json", metadata_json)
        print(f"  Generated: metadata.json (version: {version})")
        files_added += 1

        # 2. Add icon.png to resources/
        icon_file = script_dir / "icon.png"
        if icon_file.exists():
            zipf.write(icon_file, "resources/icon.png")
            print("  Added: resources/icon.png")
            files_added += 1
        else:
            print("  WARNING: icon.png not found!")

        # 3. Add all KiCad folder content to plugins/
        print("\n  Adding plugins folder content:")
        for root, dirs, files in os.walk(script_dir):
            # Filter out excluded directories
            dirs[:] = [d for d in dirs if not should_exclude(Path(root) / d)]

            for file in files:
                file_path = Path(root) / file

                if should_exclude(file_path):
                    continue

                # Calculate relative path from script_dir
                rel_path = file_path.relative_to(script_dir)

                # Create the archive path under plugins/
                archive_path = Path("plugins") / rel_path

                # If URL override is active, patch config.py content
                if override_urls and file_path.name == "config.py":
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    content = patch_config_urls(content, base_url, api_url)
                    zipf.writestr(str(archive_path), content)
                    print(f"    Added: {archive_path} (URLs patched)")
                else:
                    zipf.write(file_path, archive_path)
                    print(f"    Added: {archive_path}")
                files_added += 1

    print("-" * 50)
    print("Archive created successfully!")
    print(f"  Location: {output_path}")
    print(f"  Total files: {files_added}")

    return str(output_path)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Build KiCad plugin package archive for DeepPCB"
    )
    parser.add_argument(
        "-o", "--output", help="Output filename (without .zip extension)"
    )
    parser.add_argument(
        "-d", "--output-dir", type=Path, help="Output directory (default: KiCad folder)"
    )
    parser.add_argument(
        "--base-url",
        help="Override BASE_URL in the packaged config.py (ZIP only, source unchanged)",
    )
    parser.add_argument(
        "--api-url",
        help="Override API_URL in the packaged config.py (ZIP only, source unchanged)",
    )
    parser.add_argument(
        "--version", action="store_true", help="Print version from config.py and exit"
    )
    parser.add_argument(
        "--metadata", action="store_true", help="Print generated metadata.json and exit"
    )

    args = parser.parse_args()

    if args.version:
        print(get_version_from_config())
        return 0

    if args.metadata:
        metadata = generate_metadata()
        print(json.dumps(metadata, indent=4))
        return 0

    if (args.base_url is None) != (args.api_url is None):
        print(
            "Error: --base-url and --api-url must be provided together.",
            file=sys.stderr,
        )
        return 1

    try:
        archive_path = create_archive(
            args.output, args.output_dir, args.base_url, args.api_url
        )
        print(f"\nDone! Archive saved to:\n  {archive_path}")
        return 0
    except Exception as e:
        print(f"Error creating archive: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
