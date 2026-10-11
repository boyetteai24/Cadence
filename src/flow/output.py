"""Text insertion into the focused application."""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Protocol

log = logging.getLogger(__name__)


class Injector(Protocol):
    def insert(self, text: str) -> None: ...


def _parse_keys(spec: str):
    from pynput.keyboard import Key

    keys = []
    for token in spec.lower().split("+"):
        token = token.strip()
        keys.append(getattr(Key, token) if hasattr(Key, token) else token)
    return keys


class PasteInjector:
    """Clipboard + paste shortcut: fast and reliable for any length of text, and newlines
    don't trigger "send" in chat apps. The previous clipboard text is restored afterwards."""

    def __init__(self, paste_keys: str = "ctrl+v", restore: bool = True, delay_ms: int = 400):
        self.paste_keys = paste_keys
        self.restore = restore
        self.delay_s = delay_ms / 1000

    def _press_paste(self) -> None:
        if sys.platform == "win32":
            from flow import win32

            if not win32.wait_for_modifiers_released():
                log.warning("Modifier keys still held; pasting anyway")
            process, title = win32.foreground_window()
            log.info("Pasting into %s", process or "<unknown window>")
            log.debug("Target window title: %r", title)
            win32.send_chord([win32.vk_for(k) for k in self.paste_keys.split("+")])
            return
        from pynput.keyboard import Controller

        kb = Controller()
        keys = _parse_keys(self.paste_keys)
        for k in keys:
            kb.press(k)
        for k in reversed(keys):
            kb.release(k)

    def insert(self, text: str) -> None:
        import pyperclip

        previous = None
        if self.restore:
            try:
                previous = pyperclip.paste()
            except Exception:
                previous = None
        pyperclip.copy(text)
        self._press_paste()
        # Only text is restored; if the clipboard held non-text data (pyperclip returns ""),
        # the transcript is left there rather than wiping the clipboard.
        if previous and previous != text:
            threading.Thread(
                target=self._restore, args=(previous, text), daemon=True
            ).start()

    def _restore(self, previous: str, ours: str) -> None:
        import pyperclip

        # Apps read the clipboard asynchronously after Ctrl+V; restoring too early would
        # paste the old contents. Restore in the background so it adds no latency.
        time.sleep(self.delay_s)
        try:
            if pyperclip.paste() == ours:  # don't clobber something the user copied since
                pyperclip.copy(previous)
        except Exception:
            log.debug("Clipboard restore failed", exc_info=True)


class TypeInjector:
    """Synthetic Unicode keystrokes. Leaves the clipboard alone but is slower for long text,
    and newlines are typed as Enter."""

    def insert(self, text: str) -> None:
        from pynput.keyboard import Controller

        Controller().type(text)


def build_injector(cfg) -> Injector:
    o = cfg.output
    if o.method == "type":
        return TypeInjector()
    return PasteInjector(o.paste_keys, o.restore_clipboard, o.paste_delay_ms)
