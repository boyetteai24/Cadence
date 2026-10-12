"""On-screen mic button and status pill, shown just above the taskbar.

Idle: a thin bar that turns into a mic button when you hover it (click to start listening,
click again to stop). Right-click and drag to move it anywhere; drop it near the middle of
any screen edge (bottom, top, left, right) and it snaps there. Where it is, and which edge
it's snapped to, is remembered in overlay.json next to the config file.
Active: "Live" with a live mic-level meter, then "Transcribing"; also start-up status
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
from typing import Callable

from cadence.app import IDLE, LOADING, PROCESSING, RECORDING
from cadence.feedback import Review

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 300, 44
EDGE_MARGIN = 10  # gap between a snapped button and the screen edge / taskbar
PRESETS = ("bottom", "top", "left", "right")  # snap spots, centered on each edge
SNAP_DISTANCE = 140  # drop the button this close to a spot and it snaps there
BAR_W, BAR_H = 46, 8  # idle bar
PILL_W, PILL_H = 76, 32  # mic button shown on hover
HOVER_LINGER = 0.4  # keep the button a moment after the cursor leaves (no flicker)
KEY_COLOR = "#ff00fe"  # made fully transparent, gives the pill rounded corners
# Mint theme.
BG = "#0f172a"  # dark navy: status pill
FG = "#f8fafc"
MINT = "#5eead4"  # idle bar, pill edge, mic meter
TEAL = "#14b8a6"  # mic button
MINT_PALE = "#ccfbf1"  # button edge
RED = "#f87171"
AMBER = "#fbbf24"
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


def content_x(anchor: str | None) -> int:
    """Where the button sits inside the window. Against the left or right edge it hugs that
    side, so the button is right at the edge and the wider status pill extends inwards."""
    if anchor == "left":
        return PILL_W // 2 + 4
    if anchor == "right":
        return WIDTH - PILL_W // 2 - 4
    return WIDTH // 2


def preset_positions(work: tuple[int, int, int, int]) -> dict[str, tuple[int, int]]:
    """Window position for each snap spot, given a monitor's work area (left, top, right,
    bottom; the taskbar excluded). Every spot is centered along its edge."""
    left, top, right, bottom = work
    mid_x = (left + right) // 2 - WIDTH // 2
    mid_y = (top + bottom) // 2 - HEIGHT // 2
    return {
        "bottom": (mid_x, bottom - HEIGHT - EDGE_MARGIN),
        "top": (mid_x, top + EDGE_MARGIN),
        "left": (left + EDGE_MARGIN, mid_y),
        "right": (right - WIDTH - EDGE_MARGIN, mid_y),
    }


def button_center(pos: tuple[int, int], anchor: str | None) -> tuple[int, int]:
    return pos[0] + content_x(anchor), pos[1] + HEIGHT // 2


def nearest_preset(center: tuple[int, int], work: tuple[int, int, int, int],
                   max_distance: float = SNAP_DISTANCE) -> str | None:
    """The snap spot whose button would be closest to `center`, if it's close enough."""
    best, best_d = None, max_distance
    for name, pos in preset_positions(work).items():
        bx, by = button_center(pos, name)
        d = ((bx - center[0]) ** 2 + (by - center[1]) ** 2) ** 0.5
        if d <= best_d:
            best, best_d = name, d
    return best


def is_hovering(cursor: tuple[int, int], window: tuple[int, int], anchor: str | None) -> bool:
    """Is the cursor over the bar / button (with a little slack, so it's easy to hit)?"""
    cx, cy = button_center(window, anchor)
    return abs(cursor[0] - cx) <= PILL_W // 2 + 6 and abs(cursor[1] - cy) <= HEIGHT // 2 + 4


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
    cx, cy = x + WIDTH // 2, y + HEIGHT // 2
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        return bool(user32.MonitorFromPoint(wintypes.POINT(cx, cy), 0))  # 0 = DEFAULTTONULL
    return 0 <= cx < root.winfo_screenwidth() and 0 <= cy < root.winfo_screenheight()


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


def _capsule(canvas, cx: int, cy: int, width: int, height: int, color: str) -> None:
    """A rounded bar (stadium shape) centered on cx, cy."""
    r = height / 2
    canvas.create_line(cx - width / 2 + r, cy, cx + width / 2 - r, cy, fill=color,
                       width=height, capstyle="round")


def _make_unfocusable(win, click_through: bool = False):
    """Make a Tk window one that never takes keyboard focus (and, optionally, lets clicks
    through to the window below). Returns its native handle. Windows only."""
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
    GWL_EXSTYLE = -20
    WS_EX_TOPMOST, WS_EX_TRANSPARENT = 0x8, 0x20
    WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_LAYERED = 0x80, 0x08000000, 0x80000
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
    if click_through:
        style |= WS_EX_TRANSPARENT
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    return hwnd


def _set_window_pos(hwnd, x: int, y: int) -> None:
    import ctypes

    SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x4, 0x10
    ctypes.windll.user32.SetWindowPos(
        hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
    )


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
        return View("listening", "Live", RED)
    if state == PROCESSING:
        return View("working", "Transcribing", MINT)
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
        self.anchor: str | None = "bottom"  # snapped edge, or None if placed freely
        self._hover_until = 0.0
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

    def _build(self, tk) -> None:
        root = self.root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.0)
        root.configure(bg=KEY_COLOR)
        if sys.platform == "win32":
            root.attributes("-transparentcolor", KEY_COLOR)
        x, y = self._placement(root)
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
            # Purely informational without the button: let clicks pass through.
            self.hwnd = _make_unfocusable(root, click_through=not self.show_button)
        if self.show_button:
            self._build_markers(tk)
        self.visible = True
        self._hide()
        root.attributes("-alpha", 0.94)
        self._tick()

    def _placement(self, root) -> tuple[int, int]:
        saved = load_position()
        if saved:
            x, y, anchor = saved
            if anchor:  # re-centre on that edge (the screen size may have changed)
                self.anchor = anchor
                cx, cy = button_center((x, y), anchor)
                return preset_positions(work_area_at(root, cx, cy))[anchor]
            if _on_screen(root, x, y):
                self.anchor = None
                return x, y
        self.anchor = "bottom"
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        return preset_positions(work_area_at(root, sw // 2, sh // 2))["bottom"]

    def _build_markers(self, tk) -> None:
        """Outlines of the snap spots, shown while you drag the button. Created now, while
        the overlay itself is being set up, so showing them mid-drag never steals focus."""
        for name in PRESETS:
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.attributes("-alpha", 0.0)
            win.configure(bg=KEY_COLOR)
            if sys.platform == "win32":
                win.attributes("-transparentcolor", KEY_COLOR)
            win.geometry(f"{WIDTH}x{HEIGHT}+0+0")
            canvas = tk.Canvas(win, width=WIDTH, height=HEIGHT, bg=KEY_COLOR,
                               highlightthickness=0, bd=0)
            canvas.pack()
            win.update_idletasks()
            hwnd = _make_unfocusable(win, click_through=True) if self.hwnd else None
            self._markers[name] = {"win": win, "canvas": canvas, "hwnd": hwnd,
                                   "active": None, "shown": True}
            self._marker_visible(name, False)
            win.attributes("-alpha", 0.8)

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
        work = work_area_at(self.root, *center)
        target = nearest_preset(center, work)
        newly_shown = not any(m["shown"] for m in self._markers.values())
        for name, (x, y) in preset_positions(work).items():
            m = self._markers[name]
            active = name == target
            if m["active"] != active:
                m["active"] = active
                c, cx, cy = m["canvas"], content_x(name), HEIGHT // 2
                c.delete("all")
                _capsule(c, cx, cy, PILL_W, PILL_H, MINT_PALE if active else TEAL)
                _capsule(c, cx, cy, PILL_W - 4, PILL_H - 4, TEAL if active else KEY_COLOR)
            if m["hwnd"]:
                _set_window_pos(m["hwnd"], x, y)
            else:
                m["win"].geometry(f"+{x}+{y}")
            self._marker_visible(name, True)
        if newly_shown and self.hwnd:  # keep the button above the outlines
            import ctypes

            SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
            ctypes.windll.user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0,  # HWND_TOPMOST
                                              SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)

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
        """Dark pill with a thin mint edge."""
        c = self.canvas
        for inset, color in ((0, MINT), (1, BG)):
            c.create_oval(inset, inset, HEIGHT - inset, HEIGHT - inset, fill=color, outline=color)
            c.create_oval(WIDTH - HEIGHT + inset, inset, WIDTH - inset, HEIGHT - inset,
                          fill=color, outline=color)
            c.create_rectangle(HEIGHT // 2, inset, WIDTH - HEIGHT // 2, HEIGHT - inset,
                               fill=color, outline=color)

    def _tick(self) -> None:
        try:
            recorder = self.dictation.recorder
            self.levels.append(getattr(recorder, "level", 0.0))
            now = time.monotonic()
            if self.visible and is_hovering(self._cursor(), self._window_pos(), self.anchor):
                self._hover_until = now + HOVER_LINGER
            hovered = now < self._hover_until or self._press is not None
            if self._feedback_requested:
                self._feedback_requested = False
                self._open_feedback()
            self.view = compute_view(
                self.dictation.state, time.monotonic(), self.ready_at, self.error, self.hotkey,
                self.show_button, self.pasted_at, self.prompt_seconds, self.notice,
                self.feedback_hotkey, hovered,
            )
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
            self._update_markers(button_center(self._window_pos(), self.anchor))
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
        x, y = self._window_pos()
        center = button_center((x, y), self.anchor)
        work = work_area_at(self.root, *center)
        target = nearest_preset(center, work)
        if target:
            x, y = preset_positions(work)[target]
        else:
            # Free placement draws the button in the middle of the window; shift the
            # window so the button stays exactly where it was dropped.
            x += content_x(self.anchor) - content_x(None)
        self.anchor = target
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
            on_save=self._saved, near=button_center((x, y), self.anchor),
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

    def _bar(self) -> None:
        """Idle: a slim bar, out of the way until you hover it."""
        cx, cy = content_x(self.anchor), HEIGHT // 2
        _capsule(self.canvas, cx, cy, BAR_W + 2, BAR_H + 2, TEAL)
        _capsule(self.canvas, cx, cy, BAR_W, BAR_H, MINT)

    def _mic_button(self) -> None:
        """Hovered: a rounded button with a microphone."""
        c, cx, cy = self.canvas, content_x(self.anchor), HEIGHT // 2
        _capsule(c, cx, cy, PILL_W + 2, PILL_H + 2, MINT_PALE)
        _capsule(c, cx, cy, PILL_W, PILL_H, TEAL)
        c.create_line(cx, cy - 8, cx, cy - 1, fill=FG, width=7, capstyle="round")  # head
        c.create_arc(cx - 8, cy - 12, cx + 8, cy + 4, start=200, extent=140,
                     style="arc", outline=FG, width=2)  # holder
        c.create_line(cx, cy + 4, cx, cy + 8, fill=FG, width=2)  # stand
        c.create_line(cx - 4, cy + 8, cx + 4, cy + 8, fill=FG, width=2, capstyle="round")

    def _draw(self, view: View) -> None:
        if view.kind == "hidden":
            self._hide()
            return
        c = self.canvas
        c.delete("all")
        font = ("Segoe UI", 11)
        if view.kind in ("bar", "button"):
            self._bar() if view.kind == "bar" else self._mic_button()
            self._show()
            return
        self._pill()
        if view.kind == "feedback_prompt":
            c.create_text(WIDTH // 2, 22, text=view.text, fill=MINT, font=font)
            self._show()
            return
        if view.kind == "listening":
            pulse = 5 + 2 * (int(time.monotonic() * 3) % 2)
            c.create_oval(20 - pulse, 22 - pulse, 20 + pulse, 22 + pulse, fill=RED, outline=RED)
            c.create_text(36, 22, text=view.text, fill=FG, font=font, anchor="w")
            # Live mic meter: you can see that Cadence hears you.
            x0 = 82
            for i, level in enumerate(self.levels):
                h = max(2, min(26, int((level ** 0.5) * 80)))
                x = x0 + i * 6
                c.create_rectangle(x, 22 - h // 2, x + 3, 22 + h // 2, fill=MINT, outline=MINT)
        elif view.kind == "working":
            dots = "." * (1 + int(time.monotonic() * 3) % 3)
            c.create_oval(14, 16, 26, 28, fill=view.color, outline=view.color)
            c.create_text(36, 22, text=view.text + dots, fill=FG, font=font, anchor="w")
        else:
            c.create_text(WIDTH // 2, 22, text=view.text, fill=view.color, font=font)
        self._show()
