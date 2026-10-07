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

"""Persist (project_directory, board_id) -> conversationId so chats survive a
plugin reload."""

import json
from typing import Any, Dict, Optional


class ConversationStore:
    def __init__(self):
        # Imported here, not at module scope: helpers is imported by dialogs, so
        # a module-level import back into dialogs would be a cycle.
        from ..dialogs.api_key_dialog import get_config_path

        self._path = get_config_path().parent / "chat_sessions.json"

    def _load(self) -> Dict[str, Any]:
        try:
            if self._path.exists():
                with open(self._path, "r", encoding="utf-8") as f:
                    return json.load(f) or {}
        except Exception:
            pass
        return {}

    def _save(self, data: Dict[str, Any]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            # Best-effort: an unsaved map only costs a new conversation next time.
            pass

    def get(self, project_directory: str, board_id: str) -> Optional[str]:
        data = self._load()
        return (data.get(project_directory) or {}).get(board_id)

    def set(self, project_directory: str, board_id: str, conversation_id: str) -> None:
        data = self._load()
        bucket = data.setdefault(project_directory, {})
        bucket[board_id] = conversation_id
        self._save(data)
