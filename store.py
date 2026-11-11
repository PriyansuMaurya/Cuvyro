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
# Coordinates beyond this are not a position any desktop has ever had, and
# Qt's QRect raises OverflowError rather than clamping them, so they are
# treated as corrupt instead of being handed on.
COORD_LIMIT = 10_000_000
EMPTY_STATE = {"schema": SCHEMA, "script": "", "notes": [], "settings": {}}


def empty_state() -> dict:
    """A fresh state dict. The module-level one must never be handed out.

    ``dict(EMPTY_STATE)`` is shallow, so every caller that fell back to the
    defaults shared one notes list and one settings dict. Anything that later
    mutated a loaded state also mutated the defaults, and the next failed load
    inherited it.
    """
    fresh = dict(EMPTY_STATE)
    fresh["notes"] = []
    fresh["settings"] = {}
    return fresh


def sane_coord(value) -> int | None:
    """An integer in QRect's range, or None when the value is unusable.

    JSON has no integer type and Python treats ``bool`` as an ``int``, so
    ``true``, ``12.9``, ``"12"``, ``NaN`` and ``1e30`` all reach here as
    plausible-looking numbers. Only the ones a real display could produce are
    returned.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value == value and abs(value) != float("inf"):
        number = int(value)
    else:
        return None
    if not -COORD_LIMIT <= number <= COORD_LIMIT:
        return None
    return number


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
                script = data.get("script")
                notes = data.get("notes")
                return {
                    "schema": SCHEMA,
                    # A container here is not a script; coercing it would put
                    # "{'a': 1}" in the reader as if the user had typed it.
                    "script": script if isinstance(script, str) else "",
                    "notes": ([n for n in notes if isinstance(n, dict)]
                              if isinstance(notes, list) else []),
                    # Optional so files written before settings existed still load.
                    "settings": dict(settings) if isinstance(settings, dict) else {},
                }
        return empty_state()

    def save(self, script: str, notes: list[dict], settings: dict | None = None) -> None:
        """Write the whole state atomically; the previous file becomes .bak.

        A reader that crashes between the two replaces leaves no primary file,
        which ``load`` already handles by using the backup. Nothing is ever
        written in place, so a partially written file cannot be observed.
        """
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
