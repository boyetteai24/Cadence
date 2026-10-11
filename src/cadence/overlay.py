"""On-screen mic button and status pill, shown just above the taskbar.

Idle: a slim light-blue bar that turns into a mic button when you hover it (click to start
listening, click again to stop). Right-click and drag to move it anywhere; drop it near the
middle of any screen edge (bottom, top, left, right) and it snaps there. Where it is, and
which edge it's snapped to, is remembered in overlay.json next to the config file.
Active: "Listening" with a live mic-level meter, then "Transcribing"; also start-up status
and errors. The window never takes keyboard focus (WS_EX_NOACTIVATE), so clicking it
doesn't move your cursor and the paste still lands in the app you were typing in.

Drawing is done by `overlay_render` (Pillow, anti-aliased, see-through). On Windows each
frame goes to a per-pixel-alpha layered window (UpdateLayeredWindow); elsewhere it's shown
on a Tk canvas. Tk runs on its own thread; the pill polls Dictation state.
"""

from __future__ import annotations

import collections
import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from cadence.app import IDLE, LOADING, PROCESSING, RECORDING
from cadence.feedback import Review
from cadence.overlay_render import PRESETS, Layout, render, render_marker

log = logging.getLogger(__name__)

HOVER_LINGER = 0.4  # keep the button a moment after the cursor leaves (no flicker)
KEY_COLOR = "#000000"  # canvas background where there's no layered window (not Windows)
FG = "#f4f4f5"
RED = "#f87171"
AMBER = "#fbbf24"
BLUE = "#7dd3fc"
READY_SECONDS = 2.5
NOTICE_SECONDS = 2.0
ERROR_SECONDS = 4.0


def _state_file():
    from cadence.config import config_dir

    return config_dir() / "overlay.json"


def load_position() -> tuple[int, int, str | None] | None:
    """Saved (x, y, snapped edge or None)."""
    try:
        data = json.loads(_state_file().read_text("utf-8"))
        anchor = data.get("anchor")
        return int(data["x"]), int(data["y"]), anchor if anchor in PRESETS else None
    except Exception:
        return None


def save_position(x: int, y: int, anchor: str | None = None) -> None:
    try:
        path = _state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"x": x, "y": y, "anchor": anchor}), "utf-8")
    except OSError:
        log.debug("Could not save overlay position", exc_info=True)


def work_area_at(root, x: int, y: int) -> tuple[int, int, int, int]:
    """Work area (excluding the taskbar) of the monitor nearest to the point."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MONITORINFO))
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        monitor = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)  # DEFAULTTONEAREST
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.rcWork
            return r.left, r.top, r.right, r.bottom
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    return 0, 0, sw, sh - 48  # assume a taskbar if we can't ask Windows


def _on_screen(root, x: int, y: int) -> bool:
    """Is the saved spot still visible (e.g. a second monitor may have been unplugged)?"""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        return bool(user32.MonitorFromPoint(wintypes.POINT(x, y), 0))  # 0 = DEFAULTTONULL
    return 0 <= x < root.winfo_screenwidth() and 0 <= y < root.winfo_screenheight()


def _pretty(combo: str) -> str:
    return combo.replace("+", " + ").title() if combo else ""


def _bring_to_front(win) -> None:
    """Windows only lets the app the user last interacted with take the foreground. A
    shortcut or a click on a no-activate window doesn't count, so tap Alt first (the usual
    workaround) and then raise the feedback window so it can be typed in straight away."""
    import ctypes

    user32 = ctypes.windll.user32
    try:
        win.update_idletasks()
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        user32.keybd_event(0x12, 0, 0, 0)  # Alt down
        user32.keybd_event(0x12, 0, 2, 0)  # Alt up
        user32.SetForegroundWindow(hwnd)
    except Exception:
        log.debug("Could not bring the feedback window to the front", exc_info=True)


def _make_layered(win, click_through: bool = False):
    """Turn a Tk window into a layered window that never takes keyboard focus (and,
    optionally, lets clicks through). Its pixels then come only from `_blit`. Returns its
    native handle. Windows only."""
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
    GWL_EXSTYLE = -20
    WS_EX_TOPMOST, WS_EX_TRANSPARENT = 0x8, 0x20
    WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_LAYERED = 0x80, 0x08000000, 0x80000
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    if click_through:
        style |= WS_EX_TRANSPARENT
    # Clear and re-set WS_EX_LAYERED: Tk's "-alpha" used SetLayeredWindowAttributes, which
    # would make UpdateLayeredWindow fail.
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style & ~WS_EX_LAYERED)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
    return hwnd


def _blit(hwnd, image) -> None:
    """Show a Pillow RGBA image as the layered window's content, with per-pixel alpha."""
    import ctypes
    from ctypes import wintypes

    from cadence.overlay_render import premultiplied_bgra

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                    ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = (wintypes.HWND,)
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateDIBSection.argtypes = (wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
                                       wintypes.DWORD)
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)
    user32.UpdateLayeredWindow.argtypes = (
        wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.POINTER(wintypes.SIZE),
        wintypes.HDC, ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
        ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD)

    w, h = image.size
    data = premultiplied_bgra(image)
    header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(screen)
    bits = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(screen, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
    try:
        ctypes.memmove(bits, data, len(data))
        old = gdi32.SelectObject(memory, bitmap)
        blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, use per-pixel alpha
        user32.UpdateLayeredWindow(hwnd, screen, None, ctypes.byref(wintypes.SIZE(w, h)),
                                   memory, ctypes.byref(wintypes.POINT(0, 0)), 0,
                                   ctypes.byref(blend), 2)  # ULW_ALPHA
        gdi32.SelectObject(memory, old)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, screen)


def _set_window_pos(hwnd, x: int, y: int, topmost: bool = False) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT)
    SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x4, 0x10
    flags = SWP_NOSIZE | SWP_NOACTIVATE | (0 if topmost else SWP_NOZORDER)
    user32.SetWindowPos(hwnd, -1 if topmost else None, x, y, 0, 0, flags)  # -1: HWND_TOPMOST


def display_scale(root) -> float:
    """Display scaling (1.0 = 100 %, 1.5 = 150 %). Real only when the process is DPI-aware
    (see `win32.enable_dpi_awareness`); otherwise Windows reports 96 DPI and stretches us."""
    try:
        return max(1.0, min(4.0, root.winfo_fpixels("1i") / 96.0))
    except Exception:
        return 1.0


@dataclass
class View:
    kind: str  # "hidden" | "bar" | "button" | "listening" | "working" | "message" | "feedback_prompt"
    text: str = ""
    color: str = FG


def compute_view(state: str, now: float, ready_at: float | None,
                 error: tuple[float, str] | None, hotkey: str,
                 show_button: bool = True, pasted_at: float | None = None,
                 prompt_seconds: float = 0.0, notice: tuple[float, str] | None = None,
                 feedback_hotkey: str = "", hovered: bool = False) -> View:
    """What the pill should display right now (pure, so it's unit-testable)."""
    if error and now - error[0] < ERROR_SECONDS:
        return View("message", error[1], AMBER)
    if notice and now - notice[0] < NOTICE_SECONDS:
        return View("message", notice[1], FG)
    if state == RECORDING:
        return View("listening", "Listening", RED)
    if state == PROCESSING:
        return View("working", "Transcribing", BLUE)
    if state == LOADING:
        return View("message", "Cadence is loading…", FG)
    if state == IDLE and ready_at is not None and now - ready_at < READY_SECONDS:
        return View("message", f"Cadence ready · {hotkey} or click", FG)
    if state == IDLE and pasted_at is not None and now - pasted_at < prompt_seconds:
        hint = f" · {feedback_hotkey}" if feedback_hotkey else ""
        return View("feedback_prompt", f"Pasted · Feedback{hint}", FG)
    if state == IDLE and show_button:
        return View("button" if hovered else "bar")
    return View("hidden")


def frame_key(view: View, anchor: str | None, levels, now: float) -> tuple:
    """Everything a frame depends on: the overlay is redrawn only when this changes."""
    key: tuple = (view.kind, view.text, view.color, anchor)
    if view.kind == "listening":
        key += (int(now * 3) % 2, tuple(round(v, 3) for v in levels))
    elif view.kind == "working":
        key += (int(now * 3) % 3,)
    return key


class Overlay:
    def __init__(self, dictation, hotkey: str, show_button: bool = True,
                 feedback_hotkey: str = "", prompt_seconds: float = 0.0,
                 save_feedback: Callable[[Review], None] | None = None) -> None:
        self.dictation = dictation
        self.show_button = show_button
        self.feedback_hotkey = _pretty(feedback_hotkey)
        self.prompt_seconds = prompt_seconds if save_feedback else 0.0
        self.save_feedback = save_feedback
        self.pasted_at: float | None = None
        self.notice: tuple[float, str] | None = None
        self._feedback_requested = False
        self._feedback_window = None
        self.view = View("hidden")
        self.hotkey = _pretty(hotkey)
        self.ready_at: float | None = None
        self.error: tuple[float, str] | None = None
        self.levels: collections.deque[float] = collections.deque([0.0] * 18, maxlen=18)
        self.layout = Layout()
        self.anchor: str | None = "bottom"  # snapped edge, or None if placed freely
        self._hover_until = 0.0
        self._frame_key: tuple | None = None
        self._was_down = {"left": False, "right": False}
        self._markers: dict = {}  # snap-spot outlines shown while dragging
        dictation.listeners.append(self._on_state)
        dictation.error_listeners.append(self._on_error)
        dictation.result_listeners.append(self._on_result)
        self._thread = threading.Thread(target=self._run, name="cadence-overlay", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _on_state(self, state: str) -> None:
        if state == IDLE and self.ready_at is None:
            self.ready_at = time.monotonic()

    def _on_result(self, result) -> None:
        self.pasted_at = time.monotonic()

    def request_feedback(self) -> None:
        """Open the feedback window for the last dictation. Safe from any thread."""
        if self.save_feedback:
            self._feedback_requested = True

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

    def _new_window(self, tk, master=None):
        """A borderless, topmost window (+ canvas) sized for the overlay, not yet visible."""
        win = tk.Tk() if master is None else tk.Toplevel(master)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)  # no flash before it's set up
        win.configure(bg=KEY_COLOR)
        if master is None:
            self.layout = Layout(display_scale(win))
        w, h = self.layout.width, self.layout.height
        win.geometry(f"{w}x{h}+0+0")
        canvas = tk.Canvas(win, width=w, height=h, bg=KEY_COLOR, highlightthickness=0, bd=0)
        canvas.pack()
        win.update_idletasks()
        return win, canvas

    def _build(self, tk) -> None:
        self.root, self.canvas = self._new_window(tk)
        self.canvas.bind("<ButtonPress-1>", self._on_left_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<ButtonPress-3>", self._on_right_press)
        self.canvas.bind("<ButtonRelease-3>", self._on_release)
        self._press: tuple[int, int, int, int] | None = None
        self._press_button = "left"
        self._dragging = False
        self._photo = None
        self.hwnd = None
        if sys.platform == "win32":
            # Purely informational without the button: let clicks pass through.
            self.hwnd = _make_layered(self.root, click_through=not self.show_button)
        self.visible = True
        self._hide()
        x, y = self._placement(self.root)
        self._move(x, y)
        if not self.hwnd:
            self.root.attributes("-alpha", 0.94)
        if self.show_button:
            self._build_markers(tk)
        log.debug("Overlay at %d,%d, scale %.2f", x, y, self.layout.scale)
        self._tick()

    def _placement(self, root) -> tuple[int, int]:
        lay = self.layout
        saved = load_position()
        if saved:
            x, y, anchor = saved
            if anchor:  # re-centre on that edge (the screen size may have changed)
                self.anchor = anchor
                cx, cy = lay.button_center((x, y), anchor)
                return lay.preset_positions(work_area_at(root, cx, cy))[anchor]
            if _on_screen(root, *lay.button_center((x, y), None)):
                self.anchor = None
                return x, y
        self.anchor = "bottom"
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        return lay.preset_positions(work_area_at(root, sw // 2, sh // 2))["bottom"]

    def _build_markers(self, tk) -> None:
        """Outlines of the snap spots, shown while you drag the button. Created now, while
        the overlay itself is being set up, so showing them mid-drag never steals focus."""
        for name in PRESETS:
            win, canvas = self._new_window(tk, self.root)
            hwnd = _make_layered(win, click_through=True) if self.hwnd else None
            self._markers[name] = {"win": win, "canvas": canvas, "hwnd": hwnd,
                                   "active": None, "shown": True, "photo": None}
            self._marker_visible(name, False)
            if not hwnd:
                win.attributes("-alpha", 0.9)

    def _marker_visible(self, name: str, visible: bool) -> None:
        m = self._markers[name]
        if m["shown"] == visible:
            return
        m["shown"] = visible
        if m["hwnd"]:
            import ctypes

            ctypes.windll.user32.ShowWindow(m["hwnd"], 4 if visible else 0)
        elif visible:
            m["win"].deiconify()
        else:
            m["win"].withdraw()

    def _update_markers(self, center: tuple[int, int] | None) -> None:
        """Show the snap spots on the monitor under the button, the one it would snap to
        highlighted. `None` hides them."""
        if not self._markers:
            return
        if center is None:
            for name in PRESETS:
                self._marker_visible(name, False)
            return
        lay = self.layout
        work = work_area_at(self.root, *center)
        target = lay.nearest_preset(center, work)
        newly_shown = not any(m["shown"] for m in self._markers.values())
        for name, (x, y) in lay.preset_positions(work).items():
            m = self._markers[name]
            active = name == target
            if m["active"] != active:
                m["active"] = active
                image = render_marker(lay, name, active)
                if m["hwnd"]:
                    _blit(m["hwnd"], image)
                else:
                    m["photo"] = self._to_canvas(m["canvas"], image)
            if m["hwnd"]:
                _set_window_pos(m["hwnd"], x, y)
            else:
                m["win"].geometry(f"+{x}+{y}")
            self._marker_visible(name, True)
        if newly_shown and self.hwnd:  # keep the button above the outlines
            _set_window_pos(self.hwnd, *self._window_pos(), topmost=True)

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

    def _tick(self) -> None:
        try:
            recorder = self.dictation.recorder
            self.levels.append(getattr(recorder, "level", 0.0))
            now = time.monotonic()
            cursor, window = self._cursor(), self._window_pos()
            if self.visible and self.layout.is_hovering(cursor, window, self.anchor):
                self._hover_until = now + HOVER_LINGER
            hovered = now < self._hover_until or self._press is not None
            if self._feedback_requested:
                self._feedback_requested = False
                self._open_feedback()
            self.view = compute_view(
                self.dictation.state, now, self.ready_at, self.error, self.hotkey,
                self.show_button, self.pasted_at, self.prompt_seconds, self.notice,
                self.feedback_hotkey, hovered,
            )
            self._draw(self.view, now)
            self._poll_buttons(cursor, window)
        except Exception:
            log.debug("Overlay tick failed", exc_info=True)
        self.root.after(30, self._tick)

    # Left click = start/stop dictation. Right-click and drag = move the button.
    # While a button is down we poll the real cursor and mouse-button state instead of
    # relying on Tk motion/release events, which a no-activate window may not receive.
    def _poll_buttons(self, cursor: tuple[int, int], window: tuple[int, int]) -> None:
        """Windows: also notice presses by polling, in case the layered window's clicks
        don't reach Tk. A press counts only if it starts on what's drawn."""
        if not self.hwnd or not self.visible:
            return
        for button in ("left", "right"):
            down = self._mouse_down(button)
            if down and not self._was_down[button] and self._press is None:
                if self.view.kind in ("bar", "button"):
                    on_it = self.layout.on_button(cursor, window, self.anchor)
                else:  # a status pill fills the window
                    on_it = (0 <= cursor[0] - window[0] < self.layout.width
                             and 0 <= cursor[1] - window[1] < self.layout.height)
                if on_it:
                    self._start_press(button, drag=button == "right")
            self._was_down[button] = down

    def _on_left_press(self, event) -> None:
        self._start_press("left", drag=False)

    def _on_right_press(self, event) -> None:
        self._start_press("right", drag=True)

    def _start_press(self, button: str, drag: bool) -> None:
        if self._press is not None:
            return
        self._press = (*self._cursor(), *self._window_pos())
        self._press_button = button
        self._was_down[button] = True
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
            self._update_markers(self.layout.button_center(self._window_pos(), self.anchor))
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
            self._update_markers(None)
            self._drop()
            return
        if self.view.kind == "feedback_prompt":
            self._open_feedback()
        elif self.view.kind in ("bar", "button", "listening"):
            # Off the Tk thread: opening the microphone can take a moment.
            threading.Thread(target=self.dictation.click, daemon=True).start()

    def _drop(self) -> None:
        """End of a drag: snap to the nearest edge spot if close, else stay where dropped."""
        lay = self.layout
        x, y = self._window_pos()
        center = lay.button_center((x, y), self.anchor)
        work = work_area_at(self.root, *center)
        target = lay.nearest_preset(center, work)
        if target:
            x, y = lay.preset_positions(work)[target]
        else:
            # Free placement draws the button in the middle of the window; shift the
            # window so the button stays exactly where it was dropped.
            x += lay.content_x(self.anchor) - lay.content_x(None)
        self.anchor = target
        self._frame_key = None  # the button moves within the window
        self._move(x, y)
        save_position(x, y, target)
        log.debug("Mic button moved to %d,%d (%s)", x, y, target or "free")

    def _open_feedback(self) -> None:
        """Tk thread only."""
        result = self.dictation.last_result
        if not result or not (result.raw or result.text):
            self.notice = (time.monotonic(), "Nothing to give feedback on yet")
            return
        if self._feedback_window and self._feedback_window.exists():
            self._feedback_window.close()
        self.pasted_at = None  # the prompt has done its job
        import tkinter as tk

        from cadence.feedback_window import FeedbackWindow

        x, y = self._window_pos()
        self._feedback_window = FeedbackWindow(
            tk, self.root, heard=result.raw, typed=result.text, cleanup=result.cleanup,
            on_save=self._saved, near=self.layout.button_center((x, y), self.anchor),
            scale=self.layout.scale,
        )
        if self.hwnd:
            _bring_to_front(self._feedback_window.win)

    def _saved(self, review: Review) -> None:
        self.save_feedback(review)
        self.notice = (time.monotonic(), "Feedback saved · thanks!")

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
            _set_window_pos(self.hwnd, x, y)
        else:
            self.root.geometry(f"+{x}+{y}")

    def _to_canvas(self, canvas, image):
        """Non-Windows fallback: show the image on a Tk canvas (no real transparency)."""
        from PIL import Image, ImageTk

        backdrop = Image.new("RGBA", image.size, (0, 0, 0, 255))
        photo = ImageTk.PhotoImage(Image.alpha_composite(backdrop, image))
        canvas.delete("all")
        canvas.create_image(0, 0, image=photo, anchor="nw")
        return photo  # keep a reference, or Tk shows nothing

    def _draw(self, view: View, now: float) -> None:
        if view.kind == "hidden":
            self._hide()
            return
        key = frame_key(view, self.anchor, self.levels, now)
        if key != self._frame_key:
            self._frame_key = key
            image = render(view.kind, view.text, view.color, self.layout, self.anchor,
                           self.levels, now)
            if self.hwnd:
                _blit(self.hwnd, image)
            else:
                self._photo = self._to_canvas(self.canvas, image)
        self._show()
