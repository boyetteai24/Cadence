"""The feedback window: shows what was heard and what was typed for the last dictation, and
lets the user write what it should have said and/or a note. Runs on the overlay's Tk thread.

Unlike the mic button, this window takes keyboard focus so the user can type.
Ctrl+Enter saves, Esc closes.
"""

from __future__ import annotations

import logging
from typing import Callable

from cadence.feedback import Review

log = logging.getLogger(__name__)

BG = "#18181b"
PANEL = "#27272a"
FG = "#f4f4f5"
MUTED = "#a1a1aa"
ACCENT = "#60a5fa"
WIDTH, HEIGHT = 560, 540


class FeedbackWindow:
    def __init__(self, tk, master, heard: str, typed: str, cleanup: str,
                 on_save: Callable[[Review], None], near: tuple[int, int] | None = None) -> None:
        self.tk = tk
        self.heard, self.typed, self.cleanup = heard, typed, cleanup
        self.on_save = on_save
        win = self.win = tk.Toplevel(master)
        win.title("Cadence feedback")
        win.configure(bg=BG)
        win.attributes("-topmost", True)
        win.resizable(True, True)
        win.minsize(420, 420)
        x, y = self._position(win, near)
        win.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

        pad = {"padx": 14}
        self._label("What you said (as heard)").pack(anchor="w", pady=(12, 2), **pad)
        self._box(heard, height=4, readonly=True).pack(fill="x", **pad)
        self._label(f"What was typed  ·  cleanup: {cleanup or 'unknown'}").pack(
            anchor="w", pady=(10, 2), **pad)
        self._box(typed, height=4, readonly=True).pack(fill="x", **pad)
        self._label("What it should have said  (optional: edit the text below)").pack(
            anchor="w", pady=(10, 2), **pad)
        self.corrected = self._box(typed.strip(), height=4)
        self.corrected.pack(fill="both", expand=True, **pad)
        self._label("Notes  (optional: what went wrong)").pack(anchor="w", pady=(10, 2), **pad)
        self.notes = self._box("", height=3)
        self.notes.pack(fill="both", expand=True, **pad)

        buttons = tk.Frame(win, bg=BG)
        buttons.pack(fill="x", pady=12, **pad)
        tk.Label(buttons, text="Ctrl+Enter to save · Esc to close", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        tk.Button(buttons, text="Close", command=self.close, bg=PANEL, fg=FG, relief="flat",
                  activebackground="#3f3f46", activeforeground=FG, padx=14).pack(side="right")
        tk.Button(buttons, text="Save", command=self.save, bg=ACCENT, fg="#0b1220",
                  relief="flat", activebackground="#93c5fd", padx=18).pack(side="right", padx=8)

        win.bind("<Escape>", lambda e: self.close())
        win.bind("<Control-Return>", lambda e: self.save())
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.lift()
        win.focus_force()
        self.notes.focus_set()

    def _label(self, text: str):
        return self.tk.Label(self.win, text=text, bg=BG, fg=MUTED, font=("Segoe UI", 10))

    def _box(self, text: str, height: int, readonly: bool = False):
        box = self.tk.Text(self.win, height=height, wrap="word", bg=PANEL, fg=FG,
                           insertbackground=FG, relief="flat", font=("Segoe UI", 11),
                           padx=8, pady=6)
        box.insert("1.0", text)
        if readonly:
            box.configure(state="disabled", fg=MUTED)
        return box

    @staticmethod
    def _position(win, near: tuple[int, int] | None) -> tuple[int, int]:
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        if near:
            x, y = near[0] - WIDTH // 2, near[1] - HEIGHT - 12
        else:
            x, y = (sw - WIDTH) // 2, (sh - HEIGHT) // 2
        return max(0, min(x, sw - WIDTH)), max(0, min(y, sh - HEIGHT))

    def save(self) -> None:
        review = Review(
            heard=self.heard,
            typed=self.typed,
            corrected=self.corrected.get("1.0", "end").strip(),
            notes=self.notes.get("1.0", "end").strip(),
            cleanup=self.cleanup,
        )
        # Saved even with nothing changed: that records "this one looked right".
        try:
            self.on_save(review)
        except Exception:
            log.exception("Saving feedback failed")
        self.close()

    def close(self) -> None:
        try:
            self.win.destroy()
        except Exception:
            pass

    def exists(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False
