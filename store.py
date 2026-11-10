"""Local persistence for the Notch teleprompter.

Stores the active script and any written sticky notes under the app-data
folder. Writes go to a temp file and are then moved into place, so a crash
mid-write cannot leave a truncated state file; the previous good copy is
kept as ``state.json.bak`` and used if the primary file fails to parse.
"""

import json
import os
import tempfile

SCHEMA = 1
EMPTY_STATE = {"schema": SCHEMA, "script": "", "notes": [], "settings": {}}


def default_directory() -> str:
    # Deliberately not APPDATA/Notch: an installed Notch build keeps its own
    # trial, help and note files there, and this app must not touch them.
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "NotchFree")


class Store:
    def __init__(self, directory: str | None = None):
        self.directory = directory or default_directory()
        self.path = os.path.join(self.directory, "state.json")
        self.backup_path = self.path + ".bak"

    def load(self) -> dict:
        for candidate in (self.path, self.backup_path):
            try:
                with open(candidate, encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("schema") == SCHEMA:
                settings = data.get("settings")
                return {
                    "schema": SCHEMA,
                    "script": str(data.get("script") or ""),
                    "notes": [n for n in data.get("notes") or [] if isinstance(n, dict)],
                    # Optional so files written before settings existed still load.
                    "settings": dict(settings) if isinstance(settings, dict) else {},
                }
        return dict(EMPTY_STATE)

    def save(self, script: str, notes: list[dict], settings: dict | None = None) -> None:
        os.makedirs(self.directory, exist_ok=True)
        payload = {
            "schema": SCHEMA,
            "script": script,
            "notes": notes,
            "settings": dict(settings or {}),
        }
        handle, temp_path = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(payload, out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            if os.path.exists(self.path):
                try:
                    os.replace(self.path, self.backup_path)
                except OSError:
                    pass
            os.replace(temp_path, self.path)
        except OSError:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
