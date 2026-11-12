<p align="center">
  <p align="center">
    <img width="150" height="150" src="/assets/Notch_Clear_1.png" alt="Notch Logo">
  </p>
  <h1 align="center"><b>Notch - Smart Teleprompter for macOS & Windows</b></h1>
  <p align="center">
    A local, offline teleprompter that follows your voice.
    <br />
    Built for creators, presenters, interviews, meetings, and live delivery.
    <br />
    <br />
    <b>Downloads for </b>
    <a href="https://socia.ae/products/notch">macOS & Windows</a>
    <br />
  </p>
</p>
<br/>

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Mac-lightgrey)](https://socia.ae/products/notch)


<p align="center">
  <img src="./assets/Notch.gif" alt="Notch demo GIF" width="900" />
</p>

How to Use Notch: https://www.youtube.com/watch?v=8LaDcjB6gCM


Notch is a desktop teleprompter that stays pinned near your camera and scrolls as you speak. It is designed to feel natural on calls, recordings, presentations, and live takes, with smooth motion, fast setup, and fully local processing.

No cloud. No API keys. No uploading your script or voice.

---

## Features

| Feature | Description |
| :--- | :--- |
| **Voice follow** | Scrolls with your speech so the script moves when you talk and settles when you pause. |
| **Invisible in capture workflows** | Built to stay out of screen recordings and video calls while you present. |
| **Smooth reading motion** | Uses tuned spring-based scrolling so movement feels natural and easy to track. |
| **Offline speech processing** | Runs locally without sending your script or voice to the cloud. |
| **Manual mode** | Lets you run the teleprompter at a fixed pace when you want full control. |
| **Always on top** | Stays near your webcam so your eyeline remains more natural on camera. |
| **Document import** | Load `.txt` scripts from the menu or drag them directly onto Notch. |

---

## Use cases

- **Presentations**: Keep your delivery smooth without looking down at notes.
- **Video recording**: Read scripts naturally while keeping your eyes close to the camera.
- **Meetings and demos**: Stay on message with key talking points always in view.
- **Interviews and podcasts**: Track questions, intros, and transitions without losing flow.
- **Live streaming**: Follow prepared segments with a cleaner, more confident delivery.

---

## How it works

1. Double-click Notch to open the editor, paste your script, or load a `.txt` document from the menu.
2. Minimize back into the pill and position Notch near your webcam.
3. Choose between Voice mode and Manual mode from the right-click menu.
4. Use the right-click menu any time to adjust font, theme, speed, microphone, and presentation settings.

### Modes

- **Voice mode**: Voice Follow stays active while you speak, helping the script track your delivery.
- **Manual mode**: Scroll at a controlled pace when you prefer a fixed rhythm instead of speech-driven movement. `Space` and the arrow keys work while Notch has the keyboard; switch **Global keys** on in the right-click menu (or Setup) and `Ctrl+Shift+Space` starts or pauses from any application, with `Ctrl+Alt+Left / Right` for the pace, without taking focus away from what you are presenting in.

---

## Privacy

Notch is built for local-first use. Audio processing happens on your machine, and the app is designed around offline teleprompting rather than cloud transcription workflows.

Speech recognition runs from a model on disk (sherpa-onnx with a Silero voice-activity detector, or a local Vosk model as the fallback). There is no network path for microphone audio, transcripts or your script: `tests/check_speech_engine.py` fails if loading or decoding tries to open a socket.

Global keys are **off by default** and opt-in. When switched on, Notch registers three 
chords with Windows and does nothing else with the keyboard: no key is logged, 
recorded or sent anywhere, and the chords are released when the mode changes to 
Voice, when the setting is switched off, and when the app exits.

Optional head and eye tracking is **off by default** and experimental. When switched on it reads the webcam at 640×480 and 15 fps, through DirectShow first (measured: MediaFoundation took 11.8 s to produce a first reading here, DirectShow about one), so a video call can usually keep the camera. Frames are inspected in memory with a MediaPipe face landmarker and then discarded: nothing is recorded, written or sent anywhere, and the model ships in `model/face_landmarker.task` so nothing is downloaded either. Turning it off releases the camera immediately, and turning it back on reuses the loaded model.

The landmarker returns 478 points per face, both irises included. The midpoint of the outer eye corners gives the head's position — it does not move when you look around — and the irises give a gaze offset inside each eye. Iris position is rejected when the eye is shut, so a blink contributes no gaze. A single reading that jumps a fifth of the frame is dropped as a detector re-lock; two in agreement are followed, so a genuinely fast turn still lands.

The card then travels with your head and a little toward where you are looking — at most 68 px sideways and 45 px vertically, within the top half of your screen — so the words stay in front of your eyes without following every fidget. Movement is deliberately not full compensation: at 1:1 a real reader's sway pinned the card against its stop for a fifth of a session, which is why `FOLLOW_GAIN` is 0.45. Looking around mid-line does not move it: the gaze has its own dead zone and a slow filter, because reading is a series of saccades and following those would make the words chase your eyes.

If the camera cannot see a face it says so in the log instead of sitting there looking switched on and inert, and the first enable says it is loading the model (a few seconds) rather than appearing dead. A dropped frame or a stalled device is retried and the capture reopened; only a camera that has been gone for twelve seconds is reported as failed, and nothing retries in a tight loop, so the camera light does not flicker while a transient problem passes.

---

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for more information.

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=SultanAlshehhi/Notch&type=Date)](https://www.star-history.com/#SultanAlshehhi/Notch&Date)
