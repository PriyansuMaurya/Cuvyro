"""Reports what is stored in the reader's data folder and whether it is safe.

An update, a crash or a hand-edited file can leave a mix of primary files,
recovery copies and migration snapshots behind. This check reads that folder
without changing it and reports:

  * every state and speaking-profile file, with its schema and size,
  * which file the reader would load and whether a migration is pending,
  * whether a previous good copy exists for each store,
  * how many timestamped session copies and migration snapshots are kept,
  * the script, notes, learned profile and last save time that are
    actually recoverable.

It exits non-zero only when no readable copy of a store exists. A missing
folder or a fresh install is a pass, not a problem.

Run against the real data folder:

    .venv/Scripts/python.exe tests/check_storage.py

Inspect a copy or a test fixture instead:

    .venv/Scripts/python.exe tests/check_storage.py --dir <folder>
"""

import argparse
import fnmatch
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

from cuvyro.storage.store import (LEGACY_SCHEMA, MIGRATIONS, SCHEMA,
                                 default_directory, legacy_directory,
                                 migrate_payload, normalize_state, read_json,
                                 version_of)
from cuvyro.audio.predict import (PROFILE_MIGRATIONS, SCHEMA as PROFILE_SCHEMA,
                                 SpeakerProfile)

STATE_STEM = "state.json"
PROFILE_STEM = "speaking-profile.json"
failures = []


def say(level, text):
    print(f"  {level:<5} {text}")


def fail(text):
    failures.append(text)
    say("FAIL", text)


def human_size(size):
    if size < 1024:
        return f"{size} B"
    if size < 1024 ** 2:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 ** 2:.1f} MB"


def role_of(name, stem):
    if name == stem:
        return "primary"
    if name == stem + ".bak":
        return "backup"
    if fnmatch.fnmatch(name, f"{stem}.v*.bak"):
        return "snapshot"
    if fnmatch.fnmatch(name, f"{stem}.[0-9]*.bak"):
        return "session"
    return "other"


def schema_status(version, target):
    if version is None:
        return f"legacy (upgrades to {target})"
    if version < target:
        return f"older (upgrades to {target})"
    if version > target:
        return f"newer (snapshotted before rewrite)"
    return "current"


def stored_files(directory):
    """The files that belong to either store, in a stable order."""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return sorted(name for name in names
                  if name.startswith(STATE_STEM)
                  or name.startswith(PROFILE_STEM))


def readable(path, target, migrations):
    """The migrated payload at ``path`` and its source schema, or None."""
    data = read_json(path)
    if data is None:
        return None
    migrated = migrate_payload(data, target, migrations)
    if migrated is None:
        return None
    version = version_of(data)
    return migrated, (LEGACY_SCHEMA if version is None else version)


def print_inventory(directory):
    files = stored_files(directory)
    if not files:
        print("  (no state or speaking-profile files)")
        return

    width = max([34] + [len(name) for name in files])
    print(f"  {'file':<{width}} {'role':<9} {'schema':<7} {'size':>8}  status")
    for name in files:
        path = os.path.join(directory, name)
        stem = PROFILE_STEM if name.startswith(PROFILE_STEM) else STATE_STEM
        target = PROFILE_SCHEMA if stem == PROFILE_STEM else SCHEMA
        role = role_of(name, stem)
        try:
            size = human_size(os.path.getsize(path))
        except OSError:
            size = "?"
        data = read_json(path)
        if data is None:
            version = "-"
            status = "unreadable"
        else:
            declared = version_of(data)
            version = "-" if declared is None else str(declared)
            status = schema_status(declared, target)
            if role == "other":
                status = "not used automatically"
        print(f"  {name:<{width}} {role:<9} {version:<7} {size:>8}  {status}")


def report_state(directory):
    print("\nState")
    primary_path = os.path.join(directory, STATE_STEM)
    backup_path = primary_path + ".bak"
    primary = readable(primary_path, SCHEMA, MIGRATIONS)
    backup = readable(backup_path, SCHEMA, MIGRATIONS)

    chosen = None
    if primary is not None:
        chosen = primary
        source = primary[1]
        if source == SCHEMA:
            say("PASS", f"state.json loads at schema {SCHEMA}")
        elif source < SCHEMA:
            say("INFO", f"state.json is schema {source}; the next launch "
                       f"migrates it to {SCHEMA} and keeps the original")
        else:
            say("WARN", f"state.json is schema {source}, newer than this "
                       f"build; it will be snapshotted before the first save")
    elif backup is not None:
        chosen = backup
        say("WARN", "state.json is missing or unreadable; the reader will "
                   "recover state.json.bak")
    elif os.path.exists(primary_path) or os.path.exists(backup_path):
        fail("no readable state file or backup exists")
    else:
        say("PASS", "no state saved yet (fresh install)")

    if chosen is not None:
        state = normalize_state(chosen[0])
        print(f"        script: {len(state['script'])} characters")
        print(f"        notes: {len(state['notes'])}")
        print(f"        settings: {len(state['settings'])} keys")
        if state.get("saved_at"):
            print(f"        last saved: {state['saved_at']}")

    if os.path.exists(backup_path):
        if backup is None:
            say("WARN", "state.json.bak is unreadable")
        elif primary is not None:
            say("PASS", "a previous good state is available (state.json.bak)")

    snapshots = sorted(glob.glob(primary_path + ".v*.bak"))
    if snapshots:
        say("INFO", f"{len(snapshots)} state snapshot(s) kept: "
                   + ", ".join(os.path.basename(path) for path in snapshots))
    sessions = sorted(glob.glob(primary_path + ".[0-9]*.bak"))
    if sessions:
        say("INFO", f"{len(sessions)} timestamped state session copy(s) kept")


def report_profile(directory):
    print("\nSpeaking profile")
    primary_path = os.path.join(directory, PROFILE_STEM)
    backup_path = primary_path + ".bak"
    primary = readable(primary_path, PROFILE_SCHEMA, PROFILE_MIGRATIONS)
    backup = readable(backup_path, PROFILE_SCHEMA, PROFILE_MIGRATIONS)

    chosen = None
    if primary is not None:
        chosen = primary
        source = primary[1]
        if source == PROFILE_SCHEMA:
            say("PASS", f"speaking-profile.json loads at schema "
                       f"{PROFILE_SCHEMA}")
        elif source < PROFILE_SCHEMA:
            say("INFO", f"speaking-profile.json is schema {source}; the next "
                       f"load migrates it to {PROFILE_SCHEMA}")
        else:
            say("WARN", f"speaking-profile.json is schema {source}, newer "
                       f"than this build; it will be snapshotted before the "
                       f"first save")
    elif backup is not None:
        chosen = backup
        say("WARN", "speaking-profile.json is missing or unreadable; the "
                   "reader will recover speaking-profile.json.bak")
    elif os.path.exists(primary_path) or os.path.exists(backup_path):
        fail("no readable speaking profile or backup exists")
    else:
        say("PASS", "no learned profile yet (the default pace is used)")

    if chosen is not None:
        profile = SpeakerProfile(**chosen[0])
        print(f"        learned from {profile.words} words across "
              f"{profile.sessions} sessions")
        print(f"        rate: {profile.wps:.2f} words/second, "
              f"pause {profile.pause_seconds:.2f} s")

    if os.path.exists(backup_path):
        if backup is None:
            say("WARN", "speaking-profile.json.bak is unreadable")
        elif primary is not None:
            say("PASS", "a previous learned profile is available "
                       "(speaking-profile.json.bak)")

    snapshots = sorted(glob.glob(primary_path + ".v*.bak"))
    if snapshots:
        say("INFO", f"{len(snapshots)} profile snapshot(s) kept: "
                   + ", ".join(os.path.basename(path) for path in snapshots))
    sessions = sorted(glob.glob(primary_path + ".[0-9]*.bak"))
    if sessions:
        say("INFO", f"{len(sessions)} timestamped profile session copy(s) kept")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Report the reader's stored data without changing it.")
    parser.add_argument("--dir", dest="directory", default=None,
                        help="data folder to inspect "
                             "(default: %APPDATA%\\Cuvyro)")
    args = parser.parse_args(argv)
    # Never let the report trigger the rename migration: it promises not to
    # create or change anything, and the next launch does the copy itself.
    directory = os.path.abspath(os.path.expanduser(
        args.directory or default_directory(migrate=False)))

    print(f"Storage health - {directory}")
    if not args.directory and not os.path.isdir(directory):
        legacy = legacy_directory()
        if os.path.isdir(legacy):
            say("INFO", f"the pre-rename folder {legacy} is pending "
                        "migration; the next launch copies it here")
    if not os.path.isdir(directory):
        print("\n  The folder does not exist yet; a first launch will create it.")
        print("\nNo stored data to check. Nothing was changed.")
        return 0

    print()
    print_inventory(directory)
    report_state(directory)
    report_profile(directory)

    files_on_disk = [path for path in glob.glob(os.path.join(directory, "*"))
                     if os.path.isfile(path)]
    print(f"\n{len(files_on_disk)} file(s) in the folder; "
          f"read-only, nothing was changed.")
    if failures:
        print(f"FAILED: {len(failures)} problem(s) -> {failures}")
        return 1
    print("Storage health check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
