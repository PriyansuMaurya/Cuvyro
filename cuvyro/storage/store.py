"""Local persistence for the Cuvyro teleprompter.

Stores the active script, any written sticky notes and the reader's
preferences under the app-data folder. Writes go to a temp file and are then
moved into place, so a crash mid-write cannot leave a truncated state file;
the previous good copy is kept as ``state.json.bak`` and used if the primary
file fails to parse.

A file written by an older build is upgraded in memory by ``MIGRATIONS`` and
the original is copied to ``state.json.v<schema>.bak`` before the first
rewrite, so an update cannot discard data that a future version might still
understand. A file from a newer build is read for the fields this build knows
and is snapshotted the same way before this build overwrites it.

The first save of each launch also keeps a timestamped copy of the state that
was on disk, and prunes the set to ``ROLLING_KEEP``. That copy is what makes
an earlier session recoverable after the next one has started; unlike
``.bak``, a rolling copy is never loaded automatically.
"""

import glob
import json
import math
import os
import shutil
import tempfile
from datetime import datetime, timezone

SCHEMA = 2
# How many timestamped session copies to keep beside each store. Ten covers
# several days of short sessions without turning the folder into an archive.
ROLLING_KEEP = 10
# Files written before the schema field existed are treated as version 0 and
# upgraded by the first migration.
LEGACY_SCHEMA = 0
# Coordinates beyond this are not a position any desktop has ever had, and
# Qt's QRect raises OverflowError rather than clamping them, so they are
# treated as corrupt instead of being handed on.
COORD_LIMIT = 10_000_000
EMPTY_STATE = {"schema": SCHEMA, "script": "", "notes": [], "settings": {},
               "saved_at": None}


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


def sane_number(value, low: float, high: float) -> float | None:
    """A finite number inside ``[low, high]``, or None when unusable.

    Used for preferences that are genuinely fractional. ``sane_coord``
    truncates to an int, which would turn a saved noise gate of 0.01 into 0.
    JSON can still hold ``true``, strings, NaN and infinity, so none of those
    may reach the audio stack.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if not low <= number <= high:
        return None
    return number


# The folder the app wrote before it was called Cuvyro. Its data is carried
# forward once, so an update across the rename is not a reset.
LEGACY_DIRECTORY = "NotchFree"


def data_base() -> str:
    """The roaming folder the reader's data lives under."""
    return os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")


def legacy_directory() -> str:
    """The folder the build before the rename wrote."""
    return os.path.join(data_base(), LEGACY_DIRECTORY)


def default_directory(migrate: bool = True) -> str:
    """The reader's data folder, with an older install carried forward.

    The app stored its state under ``%APPDATA%\\NotchFree`` until it was
    renamed. On the first launch after the rename that folder is copied to
    ``Cuvyro`` before the request is answered, so the reader keeps the script,
    the notes and the learned speaking profile. If the copy fails the legacy
    folder is used instead of starting empty: losing the reader's work is a
    worse failure than writing to the old path once more.

    ``migrate=False`` answers with the destination only. The read-only storage
    report uses it so inspecting the folder cannot create one.
    """
    directory = os.path.join(data_base(), "Cuvyro")
    legacy = legacy_directory()
    if migrate and not os.path.exists(directory) and os.path.isdir(legacy):
        return migrate_legacy_directory(legacy, directory)
    return directory


def migrate_legacy_directory(legacy: str, directory: str) -> str:
    """Copy the pre-rename data folder to ``directory`` and return where to read.

    Only files are copied; the session logs beside them belonged to the old
    install and are pruned as stale on the next start anyway. The legacy
    folder stays untouched, so an older build still finds its state and a
    failure halfway through cannot leave the old copy half-moved.
    """
    created = not os.path.exists(directory)
    try:
        os.makedirs(directory, exist_ok=True)
        for name in sorted(os.listdir(legacy)):
            source = os.path.join(legacy, name)
            if not os.path.isfile(source) or name.lower().endswith(".log"):
                continue
            shutil.copy2(source, os.path.join(directory, name))
        return directory
    except OSError:
        if created:
            shutil.rmtree(directory, ignore_errors=True)
        return legacy


def read_json(path: str) -> dict | None:
    """The object stored at ``path``, or None when it is missing or invalid."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def version_of(data: object) -> int | None:
    """The schema version an on-disk payload declares.

    None means the payload declares none at all, which is what the first
    development builds wrote; callers treat that as ``LEGACY_SCHEMA``. A
    boolean is rejected because ``True`` is an ``int`` in Python.
    """
    if not isinstance(data, dict):
        return None
    version = data.get("schema")
    if isinstance(version, bool) or not isinstance(version, int):
        return None
    return version if version >= 0 else None


def migrate_payload(data: object, target: int, migrations: dict) -> dict | None:
    """Upgrade ``data`` to ``target``, or None when no upgrade path exists.

    ``migrations`` maps a schema version to the function that turns a payload
    at that version into the next one. A payload with no schema is treated as
    version 0 so a file from before versioning still loads. A payload from a
    newer build is returned unchanged: this build can still read the fields it
    recognises, and refusing it would lose far more than the unknown ones.
    """
    if not isinstance(data, dict):
        return None
    version = version_of(data)
    if version is None:
        version = LEGACY_SCHEMA
    if version > target:
        return data
    while version < target:
        step = migrations.get(version)
        if step is None:
            return None
        data = step(data)
        if not isinstance(data, dict):
            return None
        version += 1
    return data


def rolling_backups(path: str) -> list[str]:
    """The timestamped session copies for ``path``, oldest first."""
    return sorted(glob.glob(path + ".[0-9]*.bak"))


def rolling_backup(path: str, directory: str, keep: int = ROLLING_KEEP,
                  stamp: str | None = None) -> str | None:
    """Copy ``path`` to a timestamped sibling and prune old copies.

    The copy is taken once per launch, at the first save, so it holds the
    state the previous session left behind. A save that changes the file
    again in the same session does not add another copy; retaining several
    sessions is the point, not retaining every keystroke. Failure to make a
    backup never blocks the save it precedes.
    """
    if read_json(path) is None:
        return None
    if stamp is None:
        stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
    destination = f"{path}.{stamp}.bak"
    if os.path.exists(destination):
        counter = 1
        while os.path.exists(f"{path}.{stamp}-{counter}.bak"):
            counter += 1
        destination = f"{path}.{stamp}-{counter}.bak"
    if not copy_atomic(path, destination, directory):
        return None
    if keep > 0:
        for old in rolling_backups(path)[:-keep]:
            try:
                os.unlink(old)
            except OSError:
                pass
    return destination


def copy_atomic(source: str, destination: str, directory: str) -> bool:
    """Copy ``source`` to ``destination`` through a temp file."""
    try:
        handle, temp_path = tempfile.mkstemp(dir=directory, suffix=".bak.tmp")
        os.close(handle)
        try:
            shutil.copyfile(source, temp_path)
            os.replace(temp_path, destination)
        except OSError:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            return False
        return True
    except OSError:
        return False


def write_json_atomic(directory: str, path: str, payload: dict,
                      suffix: str = ".tmp") -> None:
    """Write ``payload`` to ``path`` so no partial file is ever observable.

    The caller decides whether the previous file should be copied aside first;
    this function only makes the new file complete, fsynced and atomically
    named over the old one.
    """
    os.makedirs(directory, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(dir=directory, suffix=suffix)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(payload, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp_path, path)
    except OSError:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _v0_to_v1(data: dict) -> dict:
    """Give a pre-schema payload the schema it was written in.

    Older files already used the same script/notes/settings keys; only the
    declared version is missing. The values themselves are normalised by
    ``load`` after every migration, so a malformed field is still rejected.
    """
    return {**data, "schema": 1}


def _v1_to_v2(data: dict) -> dict:
    """Record when the state was last written.

    v1 files have no save time and there is no honest way to infer one, so
    the field starts as None and the next save fills it in. This is the
    shape of a schema change that adds a field: copy the payload so no
    unknown key is lost, give the new key a safe default, and stamp the new
    schema number last so a failed step cannot look upgraded.
    """
    upgraded = dict(data)
    upgraded.setdefault("saved_at", None)
    upgraded["schema"] = 2
    return upgraded


# When the payload shape changes, add one step per old version and raise
# SCHEMA by the same amount. A missing step makes ``load`` fall back to the
# backup rather than guess at the format. Each step targets exactly the next
# version, so 0 -> 1 -> 2 is the same code path a much older file takes.
MIGRATIONS = {LEGACY_SCHEMA: _v0_to_v1, 1: _v1_to_v2}


def normalize_state(data: dict) -> dict:
    """The state shape the reader expects, with unusable values dropped."""
    settings = data.get("settings")
    script = data.get("script")
    notes = data.get("notes")
    saved_at = data.get("saved_at")
    return {
        "schema": SCHEMA,
        # A container here is not a script; coercing it would put
        # "{'a': 1}" in the reader as if the user had typed it.
        "script": script if isinstance(script, str) else "",
        "notes": ([n for n in notes if isinstance(n, dict)]
                  if isinstance(notes, list) else []),
        # Optional so files written before settings existed still load.
        "settings": dict(settings) if isinstance(settings, dict) else {},
        # Only the v2 field; older files migrate to None and the next save
        # writes a real timestamp.
        "saved_at": saved_at if isinstance(saved_at, str) and saved_at else None,
    }


class Store:
    def __init__(self, directory: str | None = None):
        self.directory = directory or default_directory()
        self.path = os.path.join(self.directory, "state.json")
        self.backup_path = self.path + ".bak"
        self._rolled_this_session = False

    def snapshot_path(self, version: int) -> str:
        """Where the untouched copy of a different schema is kept."""
        return f"{self.path}.v{version}.bak"

    def _snapshot(self, source: str, version: int, overwrite: bool = False) -> None:
        """Keep the original file before this build rewrites it.

        The snapshot is named for the schema that wrote it, so a later
        downgrade or support request can tell which format it holds. During a
        load it is written once; before a save the current file replaces it,
        because that is the copy about to be lost.
        """
        destination = self.snapshot_path(version)
        if not overwrite and os.path.exists(destination):
            return
        copy_atomic(source, destination, self.directory)

    def load(self) -> dict:
        for candidate in (self.path, self.backup_path):
            data = read_json(candidate)
            if data is None:
                continue
            source = version_of(data)
            if source is None:
                source = LEGACY_SCHEMA
            upgraded = migrate_payload(data, SCHEMA, MIGRATIONS)
            if upgraded is None:
                continue
            if source != SCHEMA:
                # Do this before returning: the reader's next save would
                # otherwise replace the only copy of the old format.
                self._snapshot(candidate, source)
            return normalize_state(upgraded)
        return empty_state()

    def _preserve_newer(self) -> None:
        """Snapshot a file this build is about to overwrite with less."""
        data = read_json(self.path)
        version = version_of(data)
        if version is not None and version > SCHEMA:
            self._snapshot(self.path, version, overwrite=True)

    def _backup_current(self) -> None:
        """Copy the last good primary aside, leaving the primary in place.

        Moving the primary to .bak and then moving the temp file in left a
        window in which no primary file existed at all: a crash there made the
        next launch fall back to a state one save older. Copying keeps the
        primary readable until the final atomic replace, and a primary that no
        longer parses is never promoted over the last good backup.
        """
        if not os.path.exists(self.path):
            return
        data = read_json(self.path)
        if data is None:
            return
        copy_atomic(self.path, self.backup_path, self.directory)

    def _mirror_backup(self, source: str) -> None:
        """Make .bak a copy of the state that was just put in place.

        The ordinary path promotes the previous state to .bak, which is right
        for a crash but wrong for a deliberate deletion: the previous state
        still held the deleted note, and a primary that later fails to parse
        would let the reader's own deletion be undone by recovery. After a
        deletion both files carry the new state instead.
        """
        copy_atomic(source, self.backup_path, self.directory)

    def save(self, script: str, notes: list[dict], settings: dict | None = None,
             purge_backup: bool = False) -> None:
        """Write the whole state atomically; the previous good state is .bak.

        Nothing is ever written in place: the new file is complete and fsynced
        before it replaces the primary, so a partially written file cannot be
        observed. ``_backup_current`` copies the old primary without removing
        it, so even a crash between the two replaces leaves a readable file.

        ``purge_backup`` is for states that removed something on purpose. The
        previous copy is not promoted; .bak is written from this state after
        the primary is in place, so recovery can never restore what the reader
        deleted. A crash between the two replaces leaves at worst a stale
        backup, never a missing primary.
        """
        payload = {
            "schema": SCHEMA,
            "script": script,
            "notes": notes,
            "settings": dict(settings or {}),
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._preserve_newer()
        if not self._rolled_this_session:
            # The primary still holds the previous session's state here,
            # which is the copy worth keeping. This also runs for a purge:
            # the deliberate deletion is protected in .bak, not by erasing
            # the history the reader asked to keep.
            rolling_backup(self.path, self.directory)
            self._rolled_this_session = True
        if purge_backup:
            write_json_atomic(self.directory, self.path, payload)
            self._mirror_backup(self.path)
        else:
            self._backup_current()
            write_json_atomic(self.directory, self.path, payload)
