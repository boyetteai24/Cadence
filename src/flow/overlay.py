"""On-screen status pill shown near the bottom of the screen.

It shows "Listening" with a live mic-level meter, "Transcribing", start-up status and errors.
The window never takes keyboard focus and ignores the mouse, so the paste still lands
in the app you were typing in. Tk runs on its own thread; the pill polls Dictation state.
"""

from __future__ import annotations

import collections
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


@dataclass
class View:
    kind: str  # "hidden" | "listening" | "working" | "message"
    text: str = ""
    color: str = FG


def compute_view(state: str, now: float, ready_at: float | None,
                 error: tuple[float, str] | None, hotkey: str) -> View:
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
        return View("message", f"Flow ready · hold {hotkey}", FG)
    return View("hidden")


class Overlay:
    def __init__(self, dictation, hotkey: str) -> None:
        self.dictation = dictation
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
        root.update_idletasks()
        self.hwnd = None
        if sys.platform == "win32":
            self._make_unfocusable()
        self.visible = True
        self._hide()
        root.attributes("-alpha", 0.94)
        self._tick()

    def _position(self, root) -> tuple[int, int]:
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
        style |= WS_EX_TOPMOST | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
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
            view = compute_view(self.dictation.state, time.monotonic(), self.ready_at,
                                self.error, self.hotkey)
            self._draw(view)
        except Exception:
            log.debug("Overlay tick failed", exc_info=True)
        self.root.after(40, self._tick)

    def _draw(self, view: View) -> None:
        if view.kind == "hidden":
            self._hide()
            return
        c = self.canvas
        c.delete("all")
        self._pill()
        font = ("Segoe UI", 11)
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
