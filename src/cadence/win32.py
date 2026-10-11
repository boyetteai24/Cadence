"""Thin ctypes wrappers around the Win32 calls Cadence needs. Import only on Windows."""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

VK = {
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "win": 0x5B,
    "insert": 0x2D,
    "enter": 0x0D,
}
_MODIFIER_VKS = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # shift, ctrl, alt, left/right win


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetAsyncKeyState.restype = ctypes.c_short
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
)


def vk_for(name: str) -> int:
    name = name.strip().lower()
    if name in VK:
        return VK[name]
    if len(name) == 1 and name.isalnum():
        return ord(name.upper())
    raise ValueError(f"Unsupported key in paste_keys: {name!r}")


def send_chord(vks: list[int]) -> None:
    """Press keys in order, release in reverse, as one atomic SendInput batch."""
    events = [(vk, 0) for vk in vks] + [(vk, KEYEVENTF_KEYUP) for vk in reversed(vks)]
    arr = (INPUT * len(events))()
    for i, (vk, flags) in enumerate(events):
        arr[i].type = INPUT_KEYBOARD
        arr[i].u.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
    sent = user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise OSError(f"SendInput sent {sent}/{len(events)} events "
                      f"(error {ctypes.get_last_error()}); is the target app running as admin?")


def modifiers_down() -> bool:
    return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in _MODIFIER_VKS)


def wait_for_modifiers_released(timeout_s: float = 2.0) -> bool:
    """Physical key state, not hook bookkeeping, so a missed key-up event can't fool us."""
    deadline = time.monotonic() + timeout_s
    while modifiers_down():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def foreground_window() -> tuple[str, str]:
    """(process name, window title) of the window that will receive the paste."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", ""
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    title = buf.value
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if handle:
        size = wintypes.DWORD(1024)
        path = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            process = os.path.basename(path.value)
        kernel32.CloseHandle(handle)
    return process, title


# Shell surfaces that pop up when the Win key is released on its own. If one of them has
# focus at paste time, the user's real target window is right behind it.
_SHELL_POPUPS = {"searchhost.exe", "startmenuexperiencehost.exe", "searchapp.exe"}


def dismiss_start_menu_if_open(timeout_s: float = 0.6) -> bool:
    """Close Start/Search with Esc and wait for focus to return. True if one was open."""
    process, _ = foreground_window()
    if process.lower() not in _SHELL_POPUPS:
        return False
    send_chord([0x1B])  # Esc
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(0.02)
        if foreground_window()[0].lower() not in _SHELL_POPUPS:
            break
    return True


def enable_dpi_awareness() -> None:
    """Tell Windows Cadence draws at the real screen resolution. Without this, on a display
    scaled to 125 % / 150 % Windows draws the overlay small and stretches it, which makes
    it blurry and pixelated. Call before any window is created."""
    try:
        # DPI_AWARENESS_CONTEXT_SYSTEM_AWARE (Windows 10 1703+). System-aware rather than
        # per-monitor, because Tk doesn't re-layout when a window moves between monitors.
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-2)):
            return
    except AttributeError:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE (8.1+)
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()
