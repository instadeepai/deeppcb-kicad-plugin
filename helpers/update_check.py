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

import configparser
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

CHECK_INTERVAL_SECONDS = 24 * 60 * 60
REQUEST_TIMEOUT_SECONDS = 3

URL_OVERRIDE_ENV = "DEEPPCB_UPDATE_URL"

_SECTION = "Updates"

_VERSION_RE = re.compile(r"^\s*v?(\d+)\.(\d+)(?:\.(\d+))?\s*$")


@dataclass(frozen=True)
class Release:
    version: str
    url: str


@dataclass
class UpdateState:
    last_checked: float = 0.0
    latest_version: str = ""
    latest_url: str = ""
    skipped_version: str = ""


def parse_version(text: str) -> Optional[Tuple[int, int, int]]:
    match = _VERSION_RE.match(text or "")
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def is_newer(candidate: str, current: str) -> bool:
    candidate_v = parse_version(candidate)
    current_v = parse_version(current)
    if candidate_v is None or current_v is None:
        return False
    return candidate_v > current_v


def fetch_latest_release(
    session, url: str, timeout: int = REQUEST_TIMEOUT_SECONDS
) -> Optional[Release]:
    try:
        response = session.get(
            url,
            headers={"Accept": "application/vnd.github+json"},
            timeout=timeout,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
            return None
        version = str(data.get("tag_name") or "").strip()
        if parse_version(version) is None:
            return None
        return Release(
            version=version.lstrip("v"),
            url=str(data.get("html_url") or ""),
        )
    except Exception:
        return None


def call_with_deadline(seconds: float, fn: Callable, *args):
    # requests' timeout is per read, so a trickling connection can outlast it.
    result = []
    worker = threading.Thread(target=lambda: result.append(fn(*args)), daemon=True)
    worker.start()
    worker.join(seconds)
    return result[0] if result else None


def refresh_state(
    state: UpdateState,
    now: float,
    fetch: Callable[[], Optional[Release]],
    force: bool = False,
) -> UpdateState:
    if not force and now - state.last_checked < CHECK_INTERVAL_SECONDS:
        return state
    release = fetch()
    if release is None:
        return UpdateState(
            last_checked=now,
            latest_version=state.latest_version,
            latest_url=state.latest_url,
            skipped_version=state.skipped_version,
        )
    return UpdateState(
        last_checked=now,
        latest_version=release.version,
        latest_url=release.url,
        skipped_version=state.skipped_version,
    )


def available_update(state: UpdateState, current_version: str) -> Optional[Release]:
    if not is_newer(state.latest_version, current_version):
        return None
    if state.skipped_version == state.latest_version:
        return None
    return Release(version=state.latest_version, url=state.latest_url)


def load_state(config_path: Path) -> UpdateState:
    config = configparser.ConfigParser()
    try:
        config.read(config_path)
        if not config.has_section(_SECTION):
            return UpdateState()
        section = config[_SECTION]
        return UpdateState(
            last_checked=section.getfloat("last_checked", fallback=0.0),
            latest_version=section.get("latest_version", fallback=""),
            latest_url=section.get("latest_url", fallback=""),
            skipped_version=section.get("skipped_version", fallback=""),
        )
    except (configparser.Error, ValueError, OSError):
        return UpdateState()


def save_state(config_path: Path, state: UpdateState) -> None:
    config = configparser.ConfigParser()
    try:
        config.read(config_path)
        if not config.has_section(_SECTION):
            config.add_section(_SECTION)
        config.set(_SECTION, "last_checked", str(state.last_checked))
        config.set(_SECTION, "latest_version", state.latest_version)
        config.set(_SECTION, "latest_url", state.latest_url)
        config.set(_SECTION, "skipped_version", state.skipped_version)
        with open(config_path, "w") as configfile:
            config.write(configfile)
    except (configparser.Error, OSError):
        pass


def check_for_update(
    config_path: Path, current_version: str, session, now: float, url: str
) -> Optional[Release]:
    override = os.environ.get(URL_OVERRIDE_ENV)
    url = override or url
    state = load_state(config_path)
    new_state = refresh_state(
        state,
        now,
        fetch=lambda: call_with_deadline(
            REQUEST_TIMEOUT_SECONDS, fetch_latest_release, session, url
        ),
        force=bool(override),
    )
    if new_state != state:
        save_state(config_path, new_state)
    return available_update(new_state, current_version)


def skip_version(config_path: Path, version: str) -> None:
    state = load_state(config_path)
    state.skipped_version = version
    save_state(config_path, state)
