"""Text insertion into the focused application."""

from __future__ import annotations

import logging
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

    def __init__(self, paste_keys: str = "ctrl+v", restore: bool = True, delay_ms: int = 120):
        self.paste_keys = paste_keys
        self.restore = restore
        self.delay_s = delay_ms / 1000

    def insert(self, text: str) -> None:
        import pyperclip
        from pynput.keyboard import Controller

        previous = None
        if self.restore:
            try:
                previous = pyperclip.paste()
            except Exception:
                previous = None
        pyperclip.copy(text)
        kb = Controller()
        keys = _parse_keys(self.paste_keys)
        for k in keys:
            kb.press(k)
        for k in reversed(keys):
            kb.release(k)
        # The target app reads the clipboard asynchronously; restoring too early would
        # paste the old contents.
        time.sleep(self.delay_s)
        # Only text is restored; if the clipboard held non-text data (pyperclip returns ""),
        # leave the transcript there rather than wiping the clipboard.
        if previous:
            pyperclip.copy(previous)


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
