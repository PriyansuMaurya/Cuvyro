"""Checks that a version update keeps the reader's saved work.

Every update runs new code against files an older build wrote. Those files
hold the script, the sticky notes, the reader's preferences and the speaking
profile the predictive shading learned, so losing one of them to a schema
check is a data loss, not a cosmetic bug.

This check writes each older format, loads it through the current build, and
then launches the real overlay against it. It also covers the two recovery
copies: the previous state and the untouched different-schema snapshot.

Run headless:

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_upgrades.py
"""

import glob
import json
import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import patch

from PyQt5.QtCore import QRect, QSettings
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

failures = []


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name}"
          f"{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def note(note_id, text, x=10, y=10):
    return {"id": note_id, "text": text, "x": x, "y": y}


def state_payload(schema=None):
    payload = {
        "script": "Ladies and gentlemen, the update begins here.\nSecond line.",
        "notes": [note("first", "Board block reads"),
                  note("second", "Ask about the timer", 120, 90)],
        "settings": {
            "font_family": "Consolas",
            "font_size": 18,
            "dark_theme": False,
            "voice_mode": False,
            "manual_wpm": 170,
            "voice_wpm": 75,
            "device_index": 3,
            "noise_gate": 0.07,
            "input_gain": 3.25,
        },
    }
    if schema is not None:
        payload["schema"] = schema
    return payload


def profile_payload(schema=None):
    payload = {
        "sessions": 6,
        "words": 1200,
        "seconds": 500.0,
        "wps": 2.9,
        "pause_seconds": 1.1,
        "pause_count": 80,
        "timing_weights": [0.3, 0.1, 0.2, 0.6, 0.8],
    }
    if schema is not None:
        payload["schema"] = schema
    return payload


def main():
    QSettings("Cuvyro", "Cuvyro").setValue("guide_seen", True)
    app = QApplication(sys.argv)
    from cuvyro.storage.store import ROLLING_KEEP, SCHEMA, Store
    from cuvyro.audio.predict import SCHEMA as PROFILE_SCHEMA
    from cuvyro.audio.predict import ProfileStore

    # ── A same-version load is a plain round trip ────────────────────
    tmp_current = tempfile.mkdtemp(prefix="cuvyro-upgrade-current-")
    write(os.path.join(tmp_current, "state.json"), state_payload(SCHEMA))
    current = Store(tmp_current).load()
    original = state_payload(SCHEMA)
    check("a current-version state loads without losing a field",
          current["script"] == original["script"]
          and current["notes"] == original["notes"]
          and current["settings"] == original["settings"]
          and current["schema"] == SCHEMA
          and current["saved_at"] is None,
          str(current.get("settings")))
    check("a current-version state needs no migration snapshot",
          not os.path.exists(Store(tmp_current).snapshot_path(SCHEMA)))

    # ── The worked v1 -> v2 step is a template for the next change ─────
    # v2 adds saved_at. The test writes a v1 file, upgrades it, checks that
    # nothing else moved, and checks the new field is only filled by a save.
    tmp_v1 = tempfile.mkdtemp(prefix="cuvyro-upgrade-v1-")
    v1_path = os.path.join(tmp_v1, "state.json")
    v1_payload = state_payload(1)
    write(v1_path, v1_payload)
    v1 = Store(tmp_v1).load()
    check("a v1 state upgrades to schema 2",
          v1["schema"] == 2, str(v1["schema"]))
    check("the v1 script, notes and preferences survive the step",
          v1["script"] == v1_payload["script"]
          and v1["notes"] == v1_payload["notes"]
          and v1["settings"] == v1_payload["settings"])
    check("an old file gets no invented save time",
          v1["saved_at"] is None, repr(v1.get("saved_at")))
    check("the original v1 file is kept as a v1 snapshot",
          read(Store(tmp_v1).snapshot_path(1)) == v1_payload)
    check("loading v1 does not rewrite the v1 file",
          read(v1_path) == v1_payload)

    Store(tmp_v1).save(v1["script"], v1["notes"], v1["settings"])
    v2_saved = read(v1_path)
    check("the first save after v1 writes schema 2",
          v2_saved.get("schema") == 2, str(v2_saved.get("schema")))
    check("the first save records when the state was written",
          isinstance(v2_saved.get("saved_at"), str)
          and datetime.fromisoformat(v2_saved["saved_at"]),
          repr(v2_saved.get("saved_at")))
    reloaded_v2 = Store(tmp_v1).load()
    check("the save time survives a reload",
          reloaded_v2["saved_at"] == v2_saved["saved_at"]
          and reloaded_v2["script"] == v1_payload["script"])

    # ── A pre-schema state is upgraded, not rejected ──────────────────
    tmp_legacy = tempfile.mkdtemp(prefix="cuvyro-upgrade-legacy-")
    legacy_path = os.path.join(tmp_legacy, "state.json")
    write(legacy_path, state_payload())
    legacy = Store(tmp_legacy).load()
    expected = state_payload()
    check("a pre-schema script survives the upgrade",
          legacy["script"] == expected["script"], repr(legacy["script"]))
    check("pre-schema notes survive the upgrade",
          legacy["notes"] == expected["notes"], str(legacy["notes"]))
    check("pre-schema preferences survive the upgrade",
          legacy["settings"] == expected["settings"], str(legacy["settings"]))
    check("the upgraded state declares the current schema",
          legacy["schema"] == SCHEMA, str(legacy["schema"]))
    check("the old format is kept beside the state file",
          read(Store(tmp_legacy).snapshot_path(0)) == expected)
    check("loading alone does not rewrite the old file",
          read(legacy_path) == expected)

    # The reader saves on the first change; that save must not be the moment
    # the old file disappears. The snapshot covers that, and the new primary
    # has to carry the same data forward.
    store_legacy = Store(tmp_legacy)
    store_legacy.save(legacy["script"], legacy["notes"], legacy["settings"])
    rewritten = read(legacy_path)
    check("the first save after an upgrade writes the current schema",
          rewritten.get("schema") == SCHEMA, str(rewritten.get("schema")))
    check("the first save keeps every migrated value",
          rewritten["script"] == expected["script"]
          and rewritten["notes"] == expected["notes"]
          and rewritten["settings"] == expected["settings"])
    check("the pre-upgrade snapshot is not overwritten by the save",
          read(store_legacy.snapshot_path(0)) == expected)
    stable = store_legacy.load()
    check("loading the upgraded file is stable",
          stable["schema"] == SCHEMA
          and stable["script"] == expected["script"]
          and stable["notes"] == expected["notes"]
          and stable["settings"] == expected["settings"]
          and stable["saved_at"] == rewritten["saved_at"])

    # ── The real overlay starts from the old file ──────────────────────
    # Schema migration is only useful if the reader actually comes back with
    # its script, notes and preferences, so this is the update a person sees.
    from cuvyro.ui import overlay as ui_overlay
    from cuvyro.audio.predict import PredictiveHighlighter

    screen = QApplication.primaryScreen().availableGeometry()
    tmp_ui = tempfile.mkdtemp(prefix="cuvyro-upgrade-ui-")
    ui_legacy = state_payload()
    ui_legacy["notes"] = [
        note("ui-first", "Board block reads", screen.x() + 120,
             screen.y() + 80),
        note("ui-second", "Ask about the timer", screen.x() + 260,
             screen.y() + 80),
    ]
    ui_legacy["settings"].update({
        "window_x": screen.x() + 40,
        "window_y": screen.y() + 30,
        "window_w": ui_overlay.CuvyroOverlay.DEFAULT_W,
        "window_h": ui_overlay.CuvyroOverlay.DEFAULT_H,
    })
    ui_path = os.path.join(tmp_ui, "state.json")
    write(ui_path, ui_legacy)

    win = ui_overlay.CuvyroOverlay()
    win._store = Store(tmp_ui)
    win._predictor = PredictiveHighlighter(ProfileStore(tmp_ui))
    win._voice_mode = False
    win._start_listening = lambda: None
    win.show()
    QTest.qWait(300)

    check("an upgraded launch restores the script",
          win._script_text == ui_legacy["script"]
          and win._text_edit.toPlainText() == ui_legacy["script"],
          f"{len(win._script_text)} chars")
    check("an upgraded launch restores the notes",
          [window.text() for window in win._notes]
          == ["Board block reads", "Ask about the timer"],
          str([window.text() for window in win._notes]))
    check("an upgraded launch restores font and theme preferences",
          win._font_family == "Consolas" and win._font_size == 18
          and win._dark_theme is False,
          f"{win._font_family} {win._font_size} dark={win._dark_theme}")
    check("an upgraded launch restores the reading speeds",
          win._manual_wpm == 170 and win._voice_wpm == 75,
          f"manual {win._manual_wpm}, voice {win._voice_wpm}")
    check("an upgraded launch restores the microphone setup",
          win._device_index == 3
          and abs(win._noise_gate - 0.07) < 1e-9
          and abs(win._input_gain - 3.25) < 1e-9,
          f"device {win._device_index}, gate {win._noise_gate}, "
          f"gain {win._input_gain}")
    check("an upgraded launch restores the reader's position",
          win.pos().x() == screen.x() + 40
          and win.pos().y() == screen.y() + 30,
          f"{win.pos().x()},{win.pos().y()}")

    win._save_state()
    saved_ui = Store(tmp_ui).load()
    check("saving the upgraded session keeps its script and notes",
          saved_ui["script"] == ui_legacy["script"]
          and [n["text"] for n in saved_ui["notes"]]
          == ["Board block reads", "Ask about the timer"])
    check("saving the upgraded session keeps its preferences",
          saved_ui["settings"].get("font_family") == "Consolas"
          and saved_ui["settings"].get("manual_wpm") == 170
          and saved_ui["settings"].get("device_index") == 3)
    check("the first upgraded session leaves a pre-upgrade snapshot",
          os.path.exists(Store(tmp_ui).snapshot_path(0)))
    with patch.object(QApplication, "quit", lambda *_: None):
        win.close()

    # ── A file from a newer build is preserved, not silently dropped ──
    tmp_newer = tempfile.mkdtemp(prefix="cuvyro-upgrade-newer-")
    newer_payload = state_payload(99)
    newer_payload["future_field"] = {"kept": True}
    write(os.path.join(tmp_newer, "state.json"), newer_payload)
    newer = Store(tmp_newer).load()
    check("a newer state still yields the fields this build understands",
          newer["script"] == newer_payload["script"]
          and newer["notes"] == newer_payload["notes"]
          and newer["settings"] == newer_payload["settings"])
    Store(tmp_newer).save(newer["script"], newer["notes"], newer["settings"])
    newer_snapshot = read(Store(tmp_newer).snapshot_path(99))
    check("a newer state is snapshotted before this build overwrites it",
          newer_snapshot["future_field"] == {"kept": True}
          and newer_snapshot["script"] == newer_payload["script"])
    check("the re-saved state keeps the reader's data",
          read(os.path.join(tmp_newer, "state.json"))["script"]
          == newer_payload["script"])

    # ── The rename from Notch keeps the reader's folder ───────────────
    # The build before the rename wrote %APPDATA%\NotchFree. That folder is
    # copied once, so an update does not look like a first run with no script.
    tmp_appdata = tempfile.mkdtemp(prefix="cuvyro-upgrade-appdata-")
    legacy_dir = os.path.join(tmp_appdata, "NotchFree")
    legacy_state = state_payload(SCHEMA)
    write(os.path.join(legacy_dir, "state.json"), legacy_state)
    write(os.path.join(legacy_dir, "speaking-profile.json"), profile_payload(PROFILE_SCHEMA))
    with open(os.path.join(legacy_dir, "notch-4242.log"), "w", encoding="utf-8") as handle:
        handle.write("old session log")
    with patch.dict(os.environ, {"APPDATA": tmp_appdata}):
        renamed = Store().load()
        renamed_profile = ProfileStore(os.path.join(tmp_appdata, "Cuvyro")).load()
    new_dir = os.path.join(tmp_appdata, "Cuvyro")
    check("the pre-rename data folder is copied to Cuvyro",
          os.path.isfile(os.path.join(new_dir, "state.json")), new_dir)
    check("the copied state keeps the script and notes",
          renamed["script"] == legacy_state["script"]
          and renamed["notes"] == legacy_state["notes"])
    check("the copied state keeps the learned speaking profile",
          renamed_profile.words == profile_payload(PROFILE_SCHEMA)["words"])
    check("the old session logs are not carried across",
          not os.path.exists(os.path.join(new_dir, "notch-4242.log")))
    check("the pre-rename folder is left in place",
          os.path.isfile(os.path.join(legacy_dir, "state.json")))

    # ── Rolling copies keep more than the last session ────────────────
    tmp_roll = tempfile.mkdtemp(prefix="cuvyro-upgrade-rolling-")
    Store(tmp_roll).save("session one", [note("old", "old note")],
                         {"font_size": 12})
    second_session = Store(tmp_roll)
    second_session.save("session two", [], {"font_size": 13})
    copies = sorted(glob.glob(os.path.join(tmp_roll, "state.json.[0-9]*.bak")))
    check("the first save of a new session keeps the previous session",
          len(copies) == 1 and read(copies[0])["script"] == "session one"
          and read(copies[0])["notes"], str([os.path.basename(p) for p in copies]))
    second_session.save("session two edited", [], {"font_size": 14})
    copies = sorted(glob.glob(os.path.join(tmp_roll, "state.json.[0-9]*.bak")))
    check("further saves in one session do not add another copy",
          len(copies) == 1, str(len(copies)))
    Store(tmp_roll).save("session three", [], {"font_size": 15})
    copies = sorted(glob.glob(os.path.join(tmp_roll, "state.json.[0-9]*.bak")))
    check("each launch adds one timestamped copy",
          len(copies) == 2
          and {read(path)["script"] for path in copies}
          == {"session one", "session two edited"},
          str([os.path.basename(path) for path in copies]))
    check("the session copies sort in the order they were written",
          copies == sorted(copies) and copies[-1].endswith(".bak"))

    for index in range(ROLLING_KEEP + 10):
        Store(tmp_roll).save(f"session {index}", [], {})
    copies = sorted(glob.glob(os.path.join(tmp_roll, "state.json.[0-9]*.bak")))
    check("rolling copies are capped instead of growing without bound",
          len(copies) == ROLLING_KEEP, str(len(copies)))
    check("the cap drops the oldest session, not the newest",
          read(copies[-1])["script"] == f"session {ROLLING_KEEP + 8}",
          read(copies[-1])["script"])

    # The learned profile rolls the same way: the first save of a launch
    # keeps the profile the previous launch ended with.
    tmp_profile_roll = tempfile.mkdtemp(prefix="cuvyro-upgrade-rolling-profile-")
    first_profile = ProfileStore(tmp_profile_roll).load()
    first_profile.words = 100
    first_profile.wps = 2.5
    ProfileStore(tmp_profile_roll).save(first_profile)
    second_profile = ProfileStore(tmp_profile_roll).load()
    second_profile.words = 200
    second_profile.wps = 2.9
    ProfileStore(tmp_profile_roll).save(second_profile)
    profile_copies = sorted(glob.glob(os.path.join(
        tmp_profile_roll, "speaking-profile.json.[0-9]*.bak")))
    check("the first profile save of a launch keeps the earlier profile",
          len(profile_copies) == 1
          and read(profile_copies[0])["words"] == 100,
          str([os.path.basename(p) for p in profile_copies]))

    # ── The learned speaking profile is data too ──────────────────────
    tmp_profile = tempfile.mkdtemp(prefix="cuvyro-upgrade-profile-")
    write(os.path.join(tmp_profile, "speaking-profile.json"),
          profile_payload(PROFILE_SCHEMA))
    learned = ProfileStore(tmp_profile).load()
    expected_profile = profile_payload(PROFILE_SCHEMA)
    check("a saved speaking profile restores every learned figure",
          learned.wps == expected_profile["wps"]
          and learned.words == expected_profile["words"]
          and learned.sessions == expected_profile["sessions"]
          and learned.pause_seconds == expected_profile["pause_seconds"]
          and learned.timing_weights == expected_profile["timing_weights"],
          f"wps={learned.wps}, words={learned.words}")

    trained = ProfileStore(tmp_profile)
    learned.wps = 3.1
    learned.words = 1300
    learned.timing_weights = [0.3, 0.1, 0.2, 0.6, 0.9]
    trained.save(learned)
    check("saving a trained profile keeps a previous copy",
          read(os.path.join(tmp_profile, "speaking-profile.json.bak"))["wps"]
          == expected_profile["wps"])
    with open(os.path.join(tmp_profile, "speaking-profile.json"), "w",
              encoding="utf-8") as handle:
        handle.write("{broken")
    recovered_profile = ProfileStore(tmp_profile).load()
    check("a corrupt profile recovers the previous learned values",
          recovered_profile.wps == expected_profile["wps"]
          and recovered_profile.words == expected_profile["words"],
          f"wps={recovered_profile.wps}, words={recovered_profile.words}")

    tmp_profile_old = tempfile.mkdtemp(prefix="cuvyro-upgrade-profile-old-")
    write(os.path.join(tmp_profile_old, "speaking-profile.json"),
          profile_payload())
    old_profile = ProfileStore(tmp_profile_old).load()
    check("a pre-schema profile is upgraded instead of reset",
          old_profile.wps == expected_profile["wps"]
          and old_profile.words == expected_profile["words"]
          and old_profile.timing_weights == expected_profile["timing_weights"])
    check("the old profile format is kept beside the new file",
          read(ProfileStore(tmp_profile_old).snapshot_path(0))
          == profile_payload())
    ProfileStore(tmp_profile_old).save(old_profile)
    check("the upgraded profile writes the current schema",
          read(os.path.join(tmp_profile_old, "speaking-profile.json"))
          .get("schema") == PROFILE_SCHEMA)

    tmp_profile_new = tempfile.mkdtemp(prefix="cuvyro-upgrade-profile-new-")
    future_profile = profile_payload(99)
    future_profile["future_weighting"] = "keep me"
    write(os.path.join(tmp_profile_new, "speaking-profile.json"),
          future_profile)
    newer_profile = ProfileStore(tmp_profile_new).load()
    check("a newer profile still yields the learned figures",
          newer_profile.wps == expected_profile["wps"]
          and newer_profile.words == expected_profile["words"])
    ProfileStore(tmp_profile_new).save(newer_profile)
    check("a newer profile is snapshotted before it is replaced",
          read(ProfileStore(tmp_profile_new).snapshot_path(99))
          .get("future_weighting") == "keep me")

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All upgrade checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
