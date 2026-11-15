"""Spring motion for the overlay.

Apple's two designer-facing parameters, implemented as a damped harmonic
oscillator: ``damping`` controls overshoot (1.0 = critically damped, no
bounce) and ``response`` is roughly how long the value takes to reach the
target, in seconds.

The point of a spring rather than a fixed-duration curve is that it starts
from the value **on screen** and carries the velocity it already has.
Re-targeting mid-flight continues the motion instead of restarting it, which
is what makes a transition grabbable and reversible rather than a script
that must play out before the user is allowed to change their mind.
"""

import math
import os
import time

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal

TWO_PI = 2.0 * math.pi

# Windows: "Show animations in Windows" (Settings > Accessibility > Visual
# effects > Animation effects). Not part of the Qt API, so read it directly.
SPI_GETCLIENTAREAANIMATION = 0x1042


def _env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() not in ("1", "true", "yes", "on")


def system_allows_motion() -> bool:
    """False when the user asked Windows to reduce animation."""
    override = _env_bool("CUVYRO_REDUCED_MOTION")
    if override is not None:
        return override
    try:
        import ctypes

        enabled = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0
        )
        return bool(enabled.value) if ok else True
    except Exception:
        return True


def system_allows_transparency() -> bool:
    """False when Windows "Transparency effects" is switched off.

    Windows keeps this in the personalisation key rather than exposing an
    SPI for it.
    """
    override = _env_bool("CUVYRO_REDUCED_TRANSPARENCY")
    if override is not None:
        return override
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "EnableTransparency")
        return bool(value)
    except Exception:
        return True


class Spring:
    """A single scalar spring."""

    __slots__ = ("value", "velocity", "damping", "response",
                 "_target", "rest_value", "rest_velocity")

    def __init__(self, value: float = 0.0, damping: float = 1.0,
                 response: float = 0.35, rest_value: float = 0.4,
                 rest_velocity: float = 12.0):
        self.value = float(value)
        self.velocity = 0.0
        self.damping = damping
        self.response = response
        self.rest_value = rest_value
        self.rest_velocity = rest_velocity
        self._target = float(value)

    @property
    def target(self) -> float:
        return self._target

    def set_target(self, target: float, carry_velocity: bool = True) -> None:
        """Retarget without disturbing the current value.

        Velocity is kept by default: that handoff is what keeps a reversal
        from feeling like a brick wall. Pass ``carry_velocity=False`` only
        when the new motion is not continuous with what came before.
        """
        self._target = float(target)
        if not carry_velocity:
            self.velocity = 0.0

    def jump_to(self, value: float) -> None:
        """Snap with no motion (reduced motion, or a layout reset)."""
        self.value = float(value)
        self._target = float(value)
        self.velocity = 0.0

    def step(self, dt: float) -> float:
        if dt <= 0.0:
            return self.value
        w0 = TWO_PI / max(self.response, 0.02)
        stiffness = w0 * w0
        drag = 2.0 * self.damping * w0
        # Sub-step so one long frame (a stall, a menu opening) cannot push
        # the integrator past its own target.
        substeps = max(1, min(8, int(dt / (1.0 / 240.0))))
        h = dt / substeps
        for _ in range(substeps):
            accel = -stiffness * (self.value - self._target) - drag * self.velocity
            self.velocity += accel * h
            self.value += self.velocity * h
        return self.value

    @property
    def settled(self) -> bool:
        return (abs(self.value - self._target) <= self.rest_value
                and abs(self.velocity) <= self.rest_velocity)

    def land(self) -> None:
        """Finish exactly on target so repeated settles cannot drift."""
        self.value = self._target
        self.velocity = 0.0


class SpringGroup(QObject):
    """A set of springs sharing one clock.

    Decomposed per axis on purpose: a single spring on a 2D distance drifts
    when X and Y settle at different rates.
    """

    tick = pyqtSignal()
    settled = pyqtSignal()

    def __init__(self, parent: QObject | None = None, interval_ms: int = 16,
                 reduced_motion: bool | None = None):
        super().__init__(parent)
        self._springs: dict[str, Spring] = {}
        self._reduced = (not system_allows_motion()
                         if reduced_motion is None else reduced_motion)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._on_timeout)
        self._last = 0.0

    # ── setup ────────────────────────────────────────────────────────
    def add(self, name: str, value: float, *, damping: float = 1.0,
            response: float = 0.35, rest_value: float = 0.4,
            rest_velocity: float = 12.0) -> Spring:
        spring = Spring(value, damping, response, rest_value, rest_velocity)
        self._springs[name] = spring
        return spring

    def __getitem__(self, name: str) -> Spring:
        return self._springs[name]

    def __contains__(self, name: str) -> bool:
        return name in self._springs

    @property
    def reduced_motion(self) -> bool:
        return self._reduced

    @reduced_motion.setter
    def reduced_motion(self, value: bool) -> None:
        self._reduced = bool(value)

    # ── driving ──────────────────────────────────────────────────────
    def set_target(self, name: str, target: float,
                   carry_velocity: bool = True) -> None:
        spring = self._springs[name]
        if self._reduced:
            spring.jump_to(target)
            # Deferred so a settle handler can rebuild geometry without
            # re-entering the call that triggered it.
            QTimer.singleShot(0, self._emit_reduced)
            return
        spring.set_target(target, carry_velocity)
        self.start()

    def _emit_reduced(self) -> None:
        self.tick.emit()
        self.settled.emit()

    def set_targets(self, **targets: float) -> None:
        for name, target in targets.items():
            self.set_target(name, target)

    def jump(self, **values: float) -> None:
        for name, value in values.items():
            self._springs[name].jump_to(value)

    def values(self) -> dict[str, float]:
        return {name: s.value for name, s in self._springs.items()}

    def start(self) -> None:
        if self._reduced:
            return
        if not self._timer.isActive():
            self._last = 0.0
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def _on_timeout(self) -> None:
        now = time.monotonic()
        dt = 0.016 if not self._last else min(0.064, max(0.001, now - self._last))
        self._last = now

        all_settled = True
        for spring in self._springs.values():
            if spring.settled:
                spring.land()
                continue
            spring.step(dt)
            if spring.settled:
                spring.land()
            else:
                all_settled = False
        self.tick.emit()
        if all_settled:
            self._timer.stop()
            self.settled.emit()
