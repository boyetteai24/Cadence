"""On-screen mic button and status pill, shown just above the taskbar.

Idle: a small mic button (click to start listening, click again to stop). Right-click and
drag to move it; the position is remembered in overlay.json next to the config file.
Active: "Listening" with a live mic-level meter, then "Transcribing"; also start-up status
and errors. The window never takes keyboard focus (WS_EX_NOACTIVATE), so clicking it
doesn't move your cursor and the paste still lands in the app you were typing in.
Tk runs on its own thread; the pill polls Dictation state.
"""

from __future__ import annotations

import collections
import json
import logging
import sys
import threading
import time
from dataclasses import dataclass

from flow.app import IDLE, LOADING, PROCESSING, RECORDING

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 300, 44
BOTTOM_MARGIN = 24
KEY_COLOR = "#ff00fe"  # made fully transparent, gives the pill rounded corners
BG = "#18181b"
FG = "#f4f4f5"
RED = "#ef4444"
AMBER = "#f59e0b"
BLUE = "#60a5fa"
READY_SECONDS = 2.5
ERROR_SECONDS = 4.0


def _state_file():
    from flow.config import config_dir

    return config_dir() / "overlay.json"


def load_position() -> tuple[int, int] | None:
    try:
        data = json.loads(_state_file().read_text("utf-8"))
        return int(data["x"]), int(data["y"])
    except Exception:
        return None


def save_position(x: int, y: int) -> None:
    try:
        path = _state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"x": x, "y": y}), "utf-8")
    except OSError:
        log.debug("Could not save overlay position", exc_info=True)


def _on_screen(root, x: int, y: int) -> bool:
    """Is the saved spot still visible (e.g. a second monitor may have been unplugged)?"""
    cx, cy = x + WIDTH // 2, y + HEIGHT // 2
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        return bool(user32.MonitorFromPoint(wintypes.POINT(cx, cy), 0))  # 0 = DEFAULTTONULL
    return 0 <= cx < root.winfo_screenwidth() and 0 <= cy < root.winfo_screenheight()


@dataclass
class View:
    kind: str  # "hidden" | "button" | "listening" | "working" | "message"
    text: str = ""
    color: str = FG


def compute_view(state: str, now: float, ready_at: float | None,
                 error: tuple[float, str] | None, hotkey: str,
                 show_button: bool = True) -> View:
    """What the pill should display right now (pure, so it's unit-testable)."""
    if error and now - error[0] < ERROR_SECONDS:
        return View("message", error[1], AMBER)
    if state == RECORDING:
        return View("listening", "Listening", RED)
    if state == PROCESSING:
        return View("working", "Transcribing", BLUE)
    if state == LOADING:
        return View("message", "Flow is loading…", FG)
    if state == IDLE and ready_at is not None and now - ready_at < READY_SECONDS:
        return View("message", f"Flow ready · {hotkey} or click", FG)
    if state == IDLE and show_button:
        return View("button")
    return View("hidden")


class Overlay:
    def __init__(self, dictation, hotkey: str, show_button: bool = True) -> None:
        self.dictation = dictation
        self.show_button = show_button
        self.view = View("hidden")
        self.hotkey = hotkey.replace("+", " + ").title()
        self.ready_at: float | None = None
        self.error: tuple[float, str] | None = None
        self.levels: collections.deque[float] = collections.deque([0.0] * 18, maxlen=18)
        dictation.listeners.append(self._on_state)
        dictation.error_listeners.append(self._on_error)
        self._thread = threading.Thread(target=self._run, name="flow-overlay", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _on_state(self, state: str) -> None:
        if state == IDLE and self.ready_at is None:
            self.ready_at = time.monotonic()

    def _on_error(self, message: str) -> None:
        short = message if len(message) <= 40 else message[:39] + "…"
        self.error = (time.monotonic(), short)

    # --- Tk (overlay thread only) -------------------------------------------------
    def _run(self) -> None:
        try:
            import tkinter as tk
        except ImportError:
            log.warning("tkinter not available; no on-screen indicator")
            return
        try:
            self._build(tk)
            self.root.mainloop()
        except Exception:
            log.exception("Overlay failed; continuing without it")

    def _build(self, tk) -> None:
        root = self.root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.0)
        root.configure(bg=KEY_COLOR)
        if sys.platform == "win32":
            root.attributes("-transparentcolor", KEY_COLOR)
        x, y = self._position(root)
        root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
        self.canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, bg=KEY_COLOR,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self._on_left_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<ButtonPress-3>", self._on_right_press)
        self.canvas.bind("<ButtonRelease-3>", self._on_release)
        self._press: tuple[int, int, int, int] | None = None
        self._press_button = "left"
        self._dragging = False
        root.update_idletasks()
        self.hwnd = None
        if sys.platform == "win32":
            self._make_unfocusable()
        self.visible = True
        self._hide()
        root.attributes("-alpha", 0.94)
        self._tick()

    def _position(self, root) -> tuple[int, int]:
        saved = load_position()
        if saved and _on_screen(root, *saved):
            return saved
        return self._default_position(root)

    def _default_position(self, root) -> tuple[int, int]:
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        bottom = sh - 48  # assume a taskbar if we can't ask Windows
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                bottom = rect.bottom  # SPI_GETWORKAREA: excludes the taskbar
        return (sw - WIDTH) // 2, bottom - HEIGHT - BOTTOM_MARGIN

    def _make_unfocusable(self) -> None:
        import ctypes

        user32 = ctypes.windll.user32
        self.hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        GWL_EXSTYLE = -20
        WS_EX_TOPMOST, WS_EX_TRANSPARENT = 0x8, 0x20
        WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_LAYERED = 0x80, 0x08000000, 0x80000
        style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        style |= WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
        if not self.show_button:
            style |= WS_EX_TRANSPARENT  # purely informational: let clicks pass through
        user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style)

    def _show(self) -> None:
        if self.visible:
            return
        self.visible = True
        if self.hwnd:
            import ctypes

            ctypes.windll.user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
        else:
            self.root.deiconify()

    def _hide(self) -> None:
        if not self.visible:
            return
        self.visible = False
        if self.hwnd:
            import ctypes

            ctypes.windll.user32.ShowWindow(self.hwnd, 0)  # SW_HIDE
        else:
            self.root.withdraw()

    def _pill(self) -> None:
        c, r = self.canvas, HEIGHT // 2
        c.create_oval(0, 0, HEIGHT, HEIGHT, fill=BG, outline=BG)
        c.create_oval(WIDTH - HEIGHT, 0, WIDTH, HEIGHT, fill=BG, outline=BG)
        c.create_rectangle(r, 0, WIDTH - r, HEIGHT, fill=BG, outline=BG)

    def _tick(self) -> None:
        try:
            recorder = self.dictation.recorder
            self.levels.append(getattr(recorder, "level", 0.0))
            self.view = compute_view(self.dictation.state, time.monotonic(), self.ready_at,
                                     self.error, self.hotkey, self.show_button)
            self._draw(self.view)
        except Exception:
            log.debug("Overlay tick failed", exc_info=True)
        self.root.after(40, self._tick)

    # Left click = start/stop dictation. Right-click and drag = move the button.
    # While a button is down we poll the real cursor and mouse-button state instead of
    # relying on Tk motion/release events, which a no-activate window may not receive.
    def _on_left_press(self, event) -> None:
        self._start_press("left", drag=False)

    def _on_right_press(self, event) -> None:
        self._start_press("right", drag=True)

    def _start_press(self, button: str, drag: bool) -> None:
        if self._press is not None:
            return
        self._press = (*self._cursor(), *self._window_pos())
        self._press_button = button
        self._dragging = drag
        log.debug("Mic button %s-pressed", button)
        self._poll_press()

    def _on_release(self, event) -> None:
        if not self.hwnd:  # on Windows the poll loop detects the release itself
            self._finish_press()

    def _poll_press(self) -> None:
        if self._press is None:
            return
        if self._dragging:
            px, py, wx, wy = self._press
            cx, cy = self._cursor()
            self._move(wx + cx - px, wy + cy - py)
        if self._mouse_down(self._press_button):
            self.root.after(15, self._poll_press)
        else:
            self._finish_press()

    def _finish_press(self) -> None:
        if self._press is None:
            return
        self._press = None
        if self._dragging:
            self._dragging = False
            x, y = self._window_pos()
            save_position(x, y)
            log.debug("Mic button moved to %d,%d", x, y)
            return
        if self.view.kind in ("button", "listening"):
            # Off the Tk thread: opening the microphone can take a moment.
            threading.Thread(target=self.dictation.click, daemon=True).start()

    def _cursor(self) -> tuple[int, int]:
        if self.hwnd:
            import ctypes
            from ctypes import wintypes

            pt = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            return pt.x, pt.y
        return self.root.winfo_pointerxy()

    def _window_pos(self) -> tuple[int, int]:
        if self.hwnd:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
            return rect.left, rect.top
        return self.root.winfo_x(), self.root.winfo_y()

    def _mouse_down(self, button: str) -> bool:
        if self.hwnd:
            import ctypes

            user32 = ctypes.windll.user32
            # GetAsyncKeyState reads physical buttons; honor "swap mouse buttons".
            physical_left = (button == "left") != bool(user32.GetSystemMetrics(23))
            vk = 0x01 if physical_left else 0x02  # VK_LBUTTON / VK_RBUTTON
            return bool(user32.GetAsyncKeyState(vk) & 0x8000)
        return self._press is not None  # Tk's release event ends the press

    def _move(self, x: int, y: int) -> None:
        if self.hwnd:
            import ctypes

            SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x4, 0x10
            ctypes.windll.user32.SetWindowPos(
                self.hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
            )
        else:
            self.root.geometry(f"+{x}+{y}")

    def _mic_button(self) -> None:
        c, cx, cy, r = self.canvas, WIDTH // 2, HEIGHT // 2, HEIGHT // 2 - 2
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=BG, outline="#3f3f46", width=2)
        # Microphone glyph.
        c.create_oval(cx - 5, cy - 12, cx + 5, cy - 2, fill=FG, outline=FG)
        c.create_rectangle(cx - 5, cy - 7, cx + 5, cy - 1, fill=FG, outline=FG)
        c.create_oval(cx - 5, cy - 6, cx + 5, cy + 4, fill=FG, outline=FG)
        c.create_arc(cx - 9, cy - 10, cx + 9, cy + 8, start=200, extent=140,
                     style="arc", outline=FG, width=2)
        c.create_line(cx, cy + 8, cx, cy + 12, fill=FG, width=2)

    def _draw(self, view: View) -> None:
        if view.kind == "hidden":
            self._hide()
            return
        c = self.canvas
        c.delete("all")
        font = ("Segoe UI", 11)
        if view.kind == "button":
            self._mic_button()
            self._show()
            return
        self._pill()
        if view.kind == "listening":
            pulse = 5 + 2 * (int(time.monotonic() * 3) % 2)
            c.create_oval(20 - pulse, 22 - pulse, 20 + pulse, 22 + pulse, fill=RED, outline=RED)
            c.create_text(36, 22, text=view.text, fill=FG, font=font, anchor="w")
            # Live mic meter: you can see that Flow hears you.
            x0 = 118
            for i, level in enumerate(self.levels):
                h = max(2, min(26, int((level ** 0.5) * 80)))
                x = x0 + i * 6
                c.create_rectangle(x, 22 - h // 2, x + 3, 22 + h // 2, fill=FG, outline=FG)
        elif view.kind == "working":
            dots = "." * (1 + int(time.monotonic() * 3) % 3)
            c.create_oval(14, 16, 26, 28, fill=view.color, outline=view.color)
            c.create_text(36, 22, text=view.text + dots, fill=FG, font=font, anchor="w")
        else:
            c.create_text(WIDTH // 2, 22, text=view.text, fill=view.color, font=font)
        self._show()
