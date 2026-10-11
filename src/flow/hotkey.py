"""Global hotkey detection.

`HotkeyMatcher` is pure logic over normalized key names (testable anywhere);
`GlobalHotkeyListener` feeds it from a pynput system-wide keyboard hook.
"""

from __future__ import annotations

import logging
import sys
from typing import Callable

log = logging.getLogger(__name__)

# Generic modifier tokens accept either side.
_SIDES = {
    "ctrl": {"ctrl_l", "ctrl_r"},
    "alt": {"alt_l", "alt_r"},
    "shift": {"shift_l", "shift_r"},
    "win": {"win_l", "win_r"},
}
_ALIASES = {
    "control": "ctrl",
    "cmd": "win",
    "super": "win",
    "meta": "win",
    "option": "alt",
    "escape": "esc",
    "capslock": "caps_lock",
    "altgr": "alt_r",
}
MODIFIER_NAMES = set().union(*_SIDES.values())


def parse_combo(combo: str) -> list[set[str]]:
    """'ctrl+win' -> [{'ctrl_l','ctrl_r'}, {'win_l','win_r'}]."""
    tokens = [t.strip().lower() for t in combo.split("+") if t.strip()]
    if not tokens:
        raise ValueError("Empty hotkey combo")
    out = []
    for t in tokens:
        t = _ALIASES.get(t, t)
        out.append(set(_SIDES.get(t, {t})))
    return out


class HotkeyMatcher:
    """Tracks pressed keys and reports when the combo becomes fully pressed
    ("activate") or stops being fully pressed ("deactivate")."""

    def __init__(self, combo: str) -> None:
        self.required = parse_combo(combo)
        self.pressed: set[str] = set()
        self.active = False

    def _satisfied(self) -> bool:
        return all(self.pressed & options for options in self.required)

    def press(self, name: str) -> str | None:
        self.pressed.add(name)
        if not self.active and self._satisfied():
            self.active = True
            return "activate"
        return None

    def release(self, name: str) -> str | None:
        self.pressed.discard(name)
        if self.active and not self._satisfied():
            self.active = False
            return "deactivate"
        return None

    def reset(self) -> None:
        self.pressed.clear()
        self.active = False

    @property
    def uses_win(self) -> bool:
        return any(o & _SIDES["win"] for o in self.required)


def pynput_key_name(key) -> str:
    """Normalize a pynput key to our naming scheme."""
    from pynput.keyboard import Key

    if isinstance(key, Key):
        name = key.name
        return {
            "cmd": "win_l",
            "cmd_l": "win_l",
            "cmd_r": "win_r",
            "ctrl": "ctrl_l",
            "alt": "alt_l",
            "alt_gr": "alt_r",
            "shift": "shift_l",
        }.get(name, name)
    vk = getattr(key, "vk", None)
    # Use the virtual-key code for letters/digits: with Ctrl held, .char is a control char.
    if vk is not None and (65 <= vk <= 90 or 48 <= vk <= 57):
        return chr(vk).lower()
    char = getattr(key, "char", None)
    if char:
        return char.lower()
    return f"vk{vk}"


def suppress_start_menu() -> None:
    """Tapping an unassigned virtual key while Win is held stops Windows from opening
    the Start menu when Win is released (same trick AutoHotkey uses)."""
    if sys.platform != "win32":
        return
    import ctypes

    vk_unassigned = 0xE8
    ctypes.windll.user32.keybd_event(vk_unassigned, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk_unassigned, 0, 2, 0)  # KEYEVENTF_KEYUP


class GlobalHotkeyListener:
    def __init__(
        self,
        combo: str,
        on_activate: Callable[[], None],
        on_deactivate: Callable[[], None],
        on_escape: Callable[[], None] | None = None,
    ) -> None:
        self.matcher = HotkeyMatcher(combo)
        self.on_activate = on_activate
        self.on_deactivate = on_deactivate
        self.on_escape = on_escape
        self._listener = None

    def _on_press(self, key) -> None:
        try:
            name = pynput_key_name(key)
            if name == "esc" and self.on_escape:
                self.on_escape()
            if self.matcher.press(name) == "activate":
                if self.matcher.uses_win:
                    suppress_start_menu()
                self.on_activate()
        except Exception:
            log.exception("Hotkey press handler failed")

    def _on_release(self, key) -> None:
        try:
            if self.matcher.release(pynput_key_name(key)) == "deactivate":
                self.on_deactivate()
        except Exception:
            log.exception("Hotkey release handler failed")

    def modifiers_held(self) -> bool:
        return bool(self.matcher.pressed & MODIFIER_NAMES)

    def start(self) -> None:
        from pynput import keyboard

        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
