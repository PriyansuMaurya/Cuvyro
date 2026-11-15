"""Build the README demo from the running overlay.

The app is rendered offscreen at twice the final size (QT_SCALE_FACTOR=2) and
every surface is grabbed from the widget tree, then downscaled into a 1200x675
deck. That keeps the card and its type crisp in the GIF without recording a
desktop, and it means nothing outside the app can appear in the frame.

Each surface is driven through its real methods (speech results, Manual
playback, the menu builder, the editor, the note window, the settings and help
dialogs, the tracking grid), so the demo shows the app rather than a mock-up.

    QT_SCALE_FACTOR=2 .venv/Scripts/python.exe tests/record_demo.py
    ffmpeg -y -framerate 12 -i .sessions/demo/frames/%04d.png \
        -vf "split[a][b];[a]palettegen=max_colors=256:stats_mode=diff[p];\
[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" \
        -loop 0 assets/demo.gif

The frames land in ``.sessions/demo/frames``, which is gitignored; only the
encoded GIF is tracked.
"""

import io
import math
import os
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from PyQt5.QtCore import QBuffer, QIODevice
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

# Grabs arrive at twice the deck's resolution (QT_SCALE_FACTOR=2), so every
# surface is sampled from double-size pixels and then reduced into the deck.
DECK = 0.80

W, H = 1200, 675
FPS = 12
FRAMES = os.path.join(ROOT, ".sessions", "demo", "frames")
os.makedirs(FRAMES, exist_ok=True)
for name in os.listdir(FRAMES):
    if name.endswith(".png"):
        os.remove(os.path.join(FRAMES, name))

FONT = "C:/Windows/Fonts/segoeui.ttf"
FONT_B = "C:/Windows/Fonts/segoeuib.ttf"
FONT_SB = "C:/Windows/Fonts/seguisb.ttf"

DAT = "#f3f3f5"
DIM = "#9d9da6"
ACC = "#8FB4FF"
LIGHT_TXT = "#141416"
LIGHT_DIM = "#5f5f68"
LIGHT_ACC = "#0B47C4"

F_TITLE = ImageFont.truetype(FONT_SB, 33)
F_SUB = ImageFont.truetype(FONT, 18)
F_SMALL = ImageFont.truetype(FONT, 14)
F_SMALL_B = ImageFont.truetype(FONT_SB, 14)
F_HERO = ImageFont.truetype(FONT_B, 80)
F_HERO_SUB = ImageFont.truetype(FONT, 25)
F_CHIP = ImageFont.truetype(FONT_SB, 16)
F_SUMMARY = ImageFont.truetype(FONT_B, 37)
F_LIST = ImageFont.truetype(FONT, 18)


def background(dark):
    top = (9, 9, 12) if dark else (247, 247, 249)
    bottom = (20, 20, 27) if dark else (232, 232, 238)
    strip = Image.new("RGB", (1, H))
    for y in range(H):
        t = y / (H - 1)
        strip.putpixel((0, y), tuple(
            int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    img = strip.resize((W, H)).convert("RGBA")
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    colour = (72, 112, 255, 26) if dark else (90, 120, 220, 12)
    draw.ellipse((120, 70, W - 120, 560), fill=colour)
    glow = glow.filter(ImageFilter.GaussianBlur(130))
    return Image.alpha_composite(img, glow)


BG = {True: background(True), False: background(False)}

count = 0


def write(img, hold=1):
    """Write one composed frame, repeated for ``hold`` frames."""
    global count
    rgb = img.convert("RGB")
    for _ in range(max(1, hold)):
        rgb.save(os.path.join(FRAMES, f"{count:04d}.png"))
        count += 1


def grab(widget):
    pixmap = widget.grab()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    pixmap.save(buffer, "PNG")
    raw = Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA")
    return raw


def shrink(img, factor=DECK):
    return img.resize((max(1, round(img.width * factor)),
                       max(1, round(img.height * factor))), Image.LANCZOS)


def fit_box(img, max_w, max_h):
    factor = min(max_w / img.width, max_h / img.height, DECK)
    return shrink(img, factor)


def caption(img, title, sub, dark):
    draw = ImageDraw.Draw(img)
    txt = DAT if dark else LIGHT_TXT
    dim = DIM if dark else LIGHT_DIM
    accent = ACC if dark else LIGHT_ACC
    draw.rounded_rectangle((W // 2 - 18, 556, W // 2 + 18, 561),
                           radius=3, fill=accent)
    tw = draw.textbbox((0, 0), title, font=F_TITLE)[2]
    draw.text(((W - tw) // 2, 574), title, font=F_TITLE, fill=txt)
    if sub:
        sw = draw.textbbox((0, 0), sub, font=F_SUB)[2]
        draw.text(((W - sw) // 2, 618), sub, font=F_SUB, fill=dim)


def scene(dark, layers=(), title="", sub="", header=True, caption_on=True):
    img = BG[dark].copy()
    for layer, pos, alpha in layers:
        if alpha < 1.0:
            layer = layer.copy()
            layer.putalpha(layer.getchannel("A").point(
                lambda value, a=alpha: int(value * a)))
        img.alpha_composite(layer, pos)
    draw = ImageDraw.Draw(img)
    txt = DAT if dark else LIGHT_TXT
    dim = DIM if dark else LIGHT_DIM
    if header:
        draw.text((56, 32), "CUVYRO", font=F_SMALL_B, fill=txt)
        right = "local  ·  offline  ·  Windows"
        rw = draw.textbbox((0, 0), right, font=F_SMALL)[2]
        draw.text((W - 56 - rw, 32), right, font=F_SMALL, fill=dim)
    if caption_on:
        caption(img, title, sub, dark)
    return img


def side_text(img, x, y, title, sub, dark):
    """A left-aligned copy block, for scenes too tall for the foot caption."""
    draw = ImageDraw.Draw(img)
    txt = DAT if dark else LIGHT_TXT
    dim = DIM if dark else LIGHT_DIM
    accent = ACC if dark else LIGHT_ACC
    draw.rounded_rectangle((x, y, x + 38, y + 5), radius=3, fill=accent)
    draw.text((x, y + 24), title, font=F_TITLE, fill=txt)
    draw.multiline_text((x, y + 76), sub, font=F_SUB, fill=dim, spacing=9)


def panel_scene(dark, menu_img, alpha, title, sub):
    """The panel is as tall as the deck, so its copy sits beside it."""
    img = scene(dark, [(menu_img, (168, 62), alpha)], caption_on=False)
    side_text(img, 636, 232, title, sub, dark)
    return img


def title_card():
    img = BG[True].copy()
    draw = ImageDraw.Draw(img)
    words = "WINDOWS  ·  LOCAL  ·  OFFLINE"
    ww = draw.textbbox((0, 0), words, font=F_SMALL_B)[2]
    draw.text(((W - ww) // 2, 186), words, font=F_SMALL_B, fill=ACC)
    title = "Cuvyro"
    tw = draw.textbbox((0, 0), title, font=F_HERO)[2]
    draw.text(((W - tw) // 2, 214), title, font=F_HERO, fill=DAT)
    sub = "A teleprompter that follows your voice"
    sw = draw.textbbox((0, 0), sub, font=F_HERO_SUB)[2]
    draw.text(((W - sw) // 2, 326), sub, font=F_HERO_SUB, fill=DIM)
    chips = ["Voice follow", "Manual pace", "Sticky notes", "Dark / light"]
    widths = [draw.textbbox((0, 0), text, font=F_CHIP)[2] + 38
              for text in chips]
    total = sum(widths) + 14 * (len(chips) - 1)
    x = (W - total) // 2
    for text, width in zip(chips, widths):
        draw.rounded_rectangle((x, 414, x + width, 456), radius=21,
                               outline="#34343c", width=1)
        draw.text((x + 19, 425), text, font=F_CHIP, fill=DAT)
        x += width + 14
    foot = "Nothing leaves this machine."
    fw = draw.textbbox((0, 0), foot, font=F_SUB)[2]
    draw.text(((W - fw) // 2, 512), foot, font=F_SUB, fill=DIM)
    write(img, hold=19)


def summary_card():
    img = BG[True].copy()
    draw = ImageDraw.Draw(img)
    title = "No account. No network. No recording."
    tw = draw.textbbox((0, 0), title, font=F_SUMMARY)[2]
    draw.text(((W - tw) // 2, 128), title, font=F_SUMMARY, fill=DAT)
    left = [
        "Voice follow, shaded ahead of you",
        "Manual pace from 1 to 400 wpm",
        "Four checkpoints, two chords each",
        "Editor, .txt drop, six typefaces",
    ]
    right = [
        "Dark and light on every surface",
        "Optional head and eye tracking",
        "Global keys you opt into",
        "Windows excluded from capture",
    ]
    for lines, x in ((left, 214), (right, 636)):
        for row, line in enumerate(lines):
            y = 232 + row * 48
            draw.ellipse((x - 15, y + 9, x - 6, y + 18), fill=ACC)
            draw.text((x, y), line, font=F_LIST, fill=DAT)
    foot = "Windows 10 / 11  ·  MIT  ·  everything stays on the machine"
    fw = draw.textbbox((0, 0), foot, font=F_SMALL)[2]
    draw.text(((W - fw) // 2, 500), foot, font=F_SMALL, fill=DIM)
    write(img, hold=28)


def grid_card():
    """A schematic of the tracking grid; the app's own constants are 5 x 2."""
    img = BG[True].copy()
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = 320, 118, 880, 470
    draw.rounded_rectangle((x0, y0, x1, y1), radius=16,
                           outline="#35353e", width=1)
    mid = y0 + (y1 - y0) // 2
    draw.rectangle((x0 + 1, mid, x1 - 1, y1 - 1), fill="#111116")
    draw.text((x0 + 18, y0 + 14), "upper half", font=F_SMALL, fill=DIM)
    lower = "lower half · never a destination"
    lw = draw.textbbox((0, 0), lower, font=F_SMALL)[2]
    draw.text(((x0 + x1 - lw) // 2, mid + 22), lower, font=F_SMALL, fill=DIM)
    draw.text((56, 32), "CUVYRO", font=F_SMALL_B, fill=DAT)
    right = "local  ·  offline  ·  Windows"
    rw = draw.textbbox((0, 0), right, font=F_SMALL)[2]
    draw.text((W - 56 - rw, 32), right, font=F_SMALL, fill=DIM)
    for col in range(1, 5):
        x = x0 + (x1 - x0) * col / 5
        draw.line((x, y0, x, mid), fill="#2b2b34", width=1)
    draw.line((x0, mid, x1, mid), fill="#2b2b34", width=1)
    # A card walking between regions and settling in the neutral one.
    points = [(0.5, 0.25), (0.1, 0.25), (0.9, 0.25), (0.9, 0.72),
              (0.1, 0.72), (0.5, 0.25)]
    for step in range(34):
        phase = step / 8.0
        i = min(len(points) - 2, int(phase))
        local = phase - i
        u = points[i][0] + (points[i + 1][0] - points[i][0]) * local
        v = points[i][1] + (points[i + 1][1] - points[i][1]) * local
        canvas = img.copy()
        card_w, card_h = 164, 54
        cx = x0 + (x1 - x0) * u - card_w / 2
        cy = y0 + (mid - y0) * v - card_h / 2
        # The reader clamps its card to the screen; the drawing does the same
        # so the card never hangs over the grid's own border.
        cx = min(max(cx, x0 + 10), x1 - card_w - 10)
        cy = min(max(cy, y0 + 10), mid - card_h - 10)
        d = ImageDraw.Draw(canvas)
        d.rounded_rectangle((cx, cy, cx + card_w, cy + card_h), radius=9,
                            fill="#18181e", outline="#3d3d48", width=1)
        d.rounded_rectangle((cx + 14, cy + 15, cx + card_w - 14, cy + 21),
                            radius=3, fill="#dcdce2")
        d.rounded_rectangle((cx + 14, cy + 30, cx + card_w - 44, cy + 36),
                            radius=3, fill="#5a5a66")
        caption(canvas, "Head tracking: ten regions, upper half only",
                "5 x 2 grid  ·  eyes refine inside a cell, never across a line",
                True)
        write(canvas)


def reveal(assets, title, sub, dark, seconds=0.45):
    """Fade one surface in over the background, then hold it."""
    layer, pos = assets
    frames = max(1, int(seconds * FPS))
    for frame in range(frames):
        alpha = min(1.0, (frame + 1) / max(1, frames - 1))
        write(scene(dark, [(layer, pos, alpha)], title, sub))
    return count


def main():
    app = QApplication(sys.argv)
    from cuvyro.storage.store import Store
    from cuvyro.ui import overlay as ui
    from cuvyro.ui.overlay import HelpDialog
    from cuvyro.ui.settings import SettingsDialog

    win = ui.CuvyroOverlay()
    win._store = Store(tempfile.mkdtemp(prefix="cuvyro-demo-"))
    win._voice_mode = False
    win.show()
    QTest.qWait(700)
    win._load_script_from_file("tests/fixtures/demo-script.txt")
    # One step below the default type and a few pixels taller: at the default
    # size the fold lands in the middle of the next line, and a half-cut row
    # of words reads as a rendering fault in a still frame.
    win._font_size = max(6, win._font_size - 1)
    win._apply_typography()
    win.resize(win.width(), win.height() + int(10 * win._scale))
    win._wake_chrome()
    QTest.qWait(500)

    title_card()

    # ── Voice follow ────────────────────────────────────────────────
    # The flag is set without starting the microphone: the speech path is
    # driven directly so the capture is deterministic.
    win._voice_mode = True
    win._reset_position()
    win._wake_chrome()
    QTest.qWait(600)

    def speech_frame(frame):
        win._on_volume_level(0.30 + 0.20 * math.sin(frame * 0.7))
        if frame == 2:
            win._on_speech_text("Good evening and thanks for joining", True)
        elif 4 <= frame <= 18 and frame % 2 == 0:
            words = ["tonight", "i", "want", "to", "show", "you",
                     "three", "small"]
            win._on_speech_text(" ".join(words[:max(1, (frame - 2) // 2)]), False)
        elif frame == 20:
            win._on_speech_text(
                "Tonight I want to show you three small things not thirty", True)
        elif 23 <= frame <= 36 and frame % 2 == 1:
            words = ["first", "how", "the", "board", "reads", "a", "city"]
            win._on_speech_text(" ".join(words[:max(1, (frame - 21) // 2)]), False)
        elif frame == 38:
            win._on_speech_text("First how the board reads a city block", True)

    for frame in range(48):
        QTest.qWait(1000 // FPS)
        win._wake_chrome()
        speech_frame(frame)
        write(scene(True, [(shrink(grab(win)), (328, 140), 1.0)],
                    "Voice mode follows your reading",
                    "Spoken words dim; the line you are on stays bright and "
                    "centred"))

    # ── Manual mode ────────────────────────────────────────────────
    win._toggle_mode(False)
    win._set_speed(150, "manual")
    win._reset_position()
    QTest.qWait(700)
    win._toggle_manual_playback()
    for frame in range(38):
        if frame == 10:
            win._adjust_speed_dir(1)
        if frame == 22:
            win._adjust_speed_dir(1)
        QTest.qWait(1000 // FPS)
        win._wake_chrome()
        write(scene(True, [(shrink(grab(win)), (328, 140), 1.0)],
                    "Manual mode: a pace you set",
                    "Space starts or pauses  ·  arrows step the speed  ·  "
                    "1–400 wpm"))
    win._toggle_manual_playback()
    QTest.qWait(300)

    # ── The panel ──────────────────────────────────────────────────
    win._reset_position()
    QTest.qWait(600)
    win._wake_chrome()
    reader = shrink(grab(win))
    menu = win._build_menu()
    # A QMenu's own adjustSize() lands a row short of its size hint, which
    # leaves the footer half drawn; the hint is the height it really needs.
    menu.adjustSize()
    menu.resize(menu.width(), menu.sizeHint().height())
    QTest.qWait(200)
    menu_img = shrink(grab(menu), 0.72)
    for frame in range(30):
        alpha = min(1.0, (frame + 1) / 3)
        write(panel_scene(True, menu_img, alpha,
                          "Right-click for the whole panel",
                          "Mode, speed, type, theme, tracking,\n"
                          "global keys, script, quit"))
    menu.close()

    # ── Editor ─────────────────────────────────────────────────────
    win._toggle_edit_mode()
    QTest.qWait(1500)
    for frame in range(30):
        QTest.qWait(1000 // FPS)
        win._wake_chrome()
        editor = fit_box(grab(win), 1090, 400)
        write(scene(True, [(editor, ((W - editor.width) // 2, 112), 1.0)],
                    "Edit right on the card",
                    "Double-click the script, or drop a .txt file on the window"))
    win._toggle_edit_mode()
    QTest.qWait(1300)

    # ── Sticky note ────────────────────────────────────────────────
    win._reset_position()
    QTest.qWait(600)
    win._wake_chrome()
    note = win._open_note()
    note._edit.setPlainText(
        "Board block reads\nHarbour plan changed\nFriday: sign-off")
    # Without the caret in the note, the close glyph keeps the focus ring and
    # the still frame reads as a selected button.
    note._edit.setFocus()
    QTest.qWait(400)
    reader = shrink(grab(win))
    note_img = shrink(grab(note))
    note_pos = (772, 158)
    for frame in range(28):
        slide = min(1.0, (frame + 1) / 4)
        x = int((772 - 36) + 36 * slide)
        write(scene(True, [(reader, (110, 150), 1.0),
                           (note_img, (x, note_pos[1]), slide)],
                    "Sticky notes that stay put",
                    "Ctrl+N  ·  placed clear of the card and of each other"))
    win._close_all_notes()
    QTest.qWait(300)

    # ── Narrow and resting ─────────────────────────────────────────
    win._reset_position()
    win._toggle_compact_mode()
    QTest.qWait(1100)
    narrow = shrink(grab(win))
    for frame in range(22):
        QTest.qWait(1000 // FPS)
        write(scene(True, [(narrow, ((W - narrow.width) // 2, 150), 1.0)],
                    "Narrow reader: about two words per line",
                    "For glancing at the next line without the card growing"))
    win._minimize()
    QTest.qWait(1100)
    pill = shrink(grab(win), 1.04)
    for frame in range(22):
        QTest.qWait(1000 // FPS)
        write(scene(True, [(pill, ((W - pill.width) // 2, 196), 1.0)],
                    "Minimize leaves a resting pill",
                    "The script stays out of the way until you click it back"))
    win._restore_from_peek()
    QTest.qWait(1000)
    win._toggle_compact_mode()
    QTest.qWait(1100)

    # ── Setup and Help ─────────────────────────────────────────────
    win._reset_position()
    QTest.qWait(500)
    reader = shrink(grab(win))
    settings = SettingsDialog(current_device=win._device_index,
                              current_noise_gate=win._noise_gate,
                              current_input_gain=win._input_gain,
                              model_path=win.MODEL_PATH, parent=None,
                              dark=True)
    settings._stop_vu_stream()
    settings._vu_bar.setValue(46)
    # The dialog is frameless and sizes itself to the card it was opened from.
    # Opened on its own it keeps the height its layout asks for.
    settings.setFixedSize(420, settings.sizeHint().height())
    QTest.qWait(200)
    settings_img = fit_box(grab(settings), 620, 400)
    settings_pos = ((W - settings_img.width) // 2, 108)
    reveal((settings_img, settings_pos),
           "Setup: the microphone, not the script",
           "Input device, live level, noise gate and gain  ·  stored locally",
           True, seconds=0.5)
    for frame in range(20):
        QTest.qWait(1000 // FPS)
        write(scene(True, [(settings_img, settings_pos, 1.0)],
                    "Setup: the microphone, not the script",
                    "Input device, live level, noise gate and gain  ·  stored "
                    "locally"))
    settings.close()

    win._wake_chrome()
    help_dialog = HelpDialog(win, dark=True)
    QTest.qWait(200)
    help_img = fit_box(grab(help_dialog), 500, 420)
    help_pos = ((W - help_img.width) // 2, 92)
    reveal((help_img, help_pos), "Help is the same guide as the first run",
           "Keys, modes and the microphone, one panel from the menu", True,
           seconds=0.5)
    for frame in range(18):
        QTest.qWait(1000 // FPS)
        write(scene(True, [(help_img, help_pos, 1.0)],
                    "Help is the same guide as the first run",
                    "Keys, modes and the microphone, one panel from the menu"))
    help_dialog.close()

    # ── Light theme, every surface ─────────────────────────────────
    win._dark_theme = False
    win._apply_read_mode_style()
    win.update()
    win._apply_control_theme()
    win._apply_highlight(win._display_word())
    win._wake_chrome()
    win._text_edit.setFocus()
    QTest.qWait(500)

    for frame in range(24):
        QTest.qWait(1000 // FPS)
        win._wake_chrome()
        write(scene(False, [(shrink(grab(win)), (328, 140), 1.0)],
                    "The same surfaces in light",
                    "The card, its waveform and the reading line hold in "
                    "both themes"))

    light_menu = win._build_menu()
    light_menu.adjustSize()
    light_menu.resize(light_menu.width(), light_menu.sizeHint().height())
    QTest.qWait(200)
    light_menu_img = shrink(grab(light_menu), 0.72)
    for frame in range(24):
        alpha = min(1.0, (frame + 1) / 3)
        write(panel_scene(False, light_menu_img, alpha,
                          "The panel follows the theme",
                          "Rows, dividers and picks,\n"
                          "down to the light surface"))
    light_menu.close()

    win._toggle_edit_mode()
    QTest.qWait(1500)
    for frame in range(22):
        QTest.qWait(1000 // FPS)
        win._wake_chrome()
        editor = fit_box(grab(win), 1090, 400)
        write(scene(False, [(editor, ((W - editor.width) // 2, 112), 1.0)],
                    "The same surfaces in light",
                    "The editor keeps the document on its own quiet surface"))
    win._toggle_edit_mode()
    QTest.qWait(1300)

    win._reset_position()
    QTest.qWait(500)
    light_note = win._open_note()
    light_note._edit.setPlainText(
        "Board block reads\nHarbour plan changed\nFriday: sign-off")
    light_note._edit.setFocus()
    QTest.qWait(400)
    light_reader = shrink(grab(win))
    light_note_img = shrink(grab(light_note))
    for frame in range(22):
        QTest.qWait(1000 // FPS)
        write(scene(False, [(light_reader, (110, 150), 1.0),
                            (light_note_img, (772, 158), 1.0)],
                    "The same surfaces in light",
                    "Notes follow the theme, hairlines included"))
    win._close_all_notes()

    # ── Tracking and summary ───────────────────────────────────────
    grid_card()
    summary_card()

    win.close()
    app.quit()
    print(f"{count} frames written to {FRAMES} at {W}x{H}, {FPS} fps")


if __name__ == "__main__":
    main()
