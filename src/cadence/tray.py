"""System tray icon: shows state and offers quick settings."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading

import pystray
from PIL import Image, ImageDraw

from cadence.app import IDLE, LOADING, PROCESSING, RECORDING, Dictation
from cadence.cleanup import build_cleaner
from cadence.config import Config, default_config_path, settings_edit_url

log = logging.getLogger(__name__)

_COLORS = {
    LOADING: (128, 128, 128),
    IDLE: (60, 130, 246),
    RECORDING: (220, 38, 38),
    PROCESSING: (234, 179, 8),
}
_LABELS = {
    LOADING: "Loading speech model…",
    IDLE: "Ready",
    RECORDING: "Listening…",
    PROCESSING: "Transcribing…",
}


def _icon_image(color: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=color + (255,))
    # Simple microphone glyph.
    d.rounded_rectangle((25, 14, 39, 38), radius=7, fill=(255, 255, 255, 255))
    d.arc((19, 24, 45, 46), start=0, end=180, fill=(255, 255, 255, 255), width=3)
    d.line((32, 46, 32, 52), fill=(255, 255, 255, 255), width=3)
    return img


def _open_path(path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])


class Tray:
    def __init__(self, dictation: Dictation, cfg: Config) -> None:
        self.dictation = dictation
        self.cfg = cfg
        self._images = {state: _icon_image(c) for state, c in _COLORS.items()}
        self.icon = pystray.Icon("Cadence", self._images[LOADING], "Cadence", menu=self._menu())
        self.stopped = threading.Event()
        dictation.listeners.append(self._on_state)
        dictation.error_listeners.append(self._on_error)

    def _menu(self) -> pystray.Menu:
        def llm_item(label: str, value: str) -> pystray.MenuItem:
            return pystray.MenuItem(
                label,
                lambda: self._set_llm(value),
                checked=lambda _: self.cfg.cleanup.llm == value,
                radio=True,
            )

        return pystray.Menu(
            pystray.MenuItem(lambda _: f"Cadence: {_LABELS[self.dictation.state]}", None, enabled=False),
            pystray.MenuItem(f"Hotkey: {self.cfg.hotkey.combo} ({self.cfg.hotkey.mode})", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Copy last dictation", self._copy_last),
            pystray.MenuItem(
                "AI cleanup",
                pystray.Menu(
                    llm_item("Rules only (instant)", "none"),
                    llm_item("Local LLM", "local"),
                    llm_item("Claude Haiku", "claude"),
                ),
            ),
            pystray.MenuItem("Edit settings on GitHub", lambda: self._open_config()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self.stop()),
        )

    def _on_state(self, state: str) -> None:
        self.icon.icon = self._images.get(state, self._images[IDLE])
        self.icon.title = f"Cadence: {_LABELS.get(state, state)}"

    def _on_error(self, message: str) -> None:
        try:
            self.icon.notify(message, "Cadence")
        except Exception:
            pass

    def _copy_last(self) -> None:
        import pyperclip

        r = self.dictation.last_result
        if r and (r.text or r.raw):
            pyperclip.copy(r.text or r.raw)

    def _set_llm(self, value: str) -> None:
        # Session-only switch; edit the config file to make it permanent.
        self.cfg.cleanup.llm = value
        self.dictation.cleaner = build_cleaner(self.cfg)
        log.info("Cleanup LLM set to %s", value)

    def _open_config(self) -> None:
        # Settings are edited on GitHub (then `git pull`); fall back to the local file.
        url = settings_edit_url()
        if url:
            import webbrowser

            webbrowser.open(url)
            return
        path = default_config_path()
        if not path.exists():
            from cadence.cli import write_default_config

            write_default_config(path)
        _open_path(path)

    def start(self) -> None:
        self.icon.run_detached()

    def stop(self) -> None:
        self.stopped.set()
        try:
            self.icon.stop()
        except Exception:
            pass
