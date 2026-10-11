"""Short, non-blocking audio cues (Windows only; silent elsewhere)."""

from __future__ import annotations

import sys
import threading


def _beep(*tones: tuple[int, int]) -> None:
    if sys.platform != "win32":
        return

    def play() -> None:
        import winsound

        for freq, ms in tones:
            winsound.Beep(freq, ms)

    threading.Thread(target=play, daemon=True).start()


def start() -> None:
    _beep((880, 80))


def stop() -> None:
    _beep((660, 80))


def error() -> None:
    _beep((300, 120), (220, 160))
