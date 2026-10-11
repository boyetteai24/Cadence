"""Dictation controller: hotkey -> record -> transcribe -> clean -> insert.

`Dictation` holds no OS-specific code; all I/O components are injected, so the whole
pipeline can be tested with fakes.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from cadence import sounds
from cadence.config import Config

log = logging.getLogger(__name__)

IDLE, RECORDING, PROCESSING, LOADING = "idle", "recording", "processing", "loading"
# A shortcut press shorter than this is a "tap" (starts hands-free listening).
TAP_SECONDS = 0.35


@dataclass
class Result:
    raw: str = ""
    text: str = ""
    audio_seconds: float = 0.0
    timings: dict[str, float] = field(default_factory=dict)
    skipped: str | None = None  # reason, if nothing was inserted
    cleanup: str = ""  # which cleanup produced `text`: the LLM's name, or "rules"


@dataclass
class Utterance:
    """One dictation (press to release, or click to click). With background transcription
    it is processed as several segments; `result` accumulates across them."""

    cancelled: bool = False
    segments: int = 0
    pending: list[str] = field(default_factory=list)  # raw text of the segments so far
    context: str = ""  # recent raw transcript, given to Whisper for continuity
    stopped_at: float | None = None  # when the user finished speaking
    result: Result = field(default_factory=Result)


@dataclass
class Job:
    audio: np.ndarray
    utterance: Utterance
    final: bool


class Dictation:
    def __init__(
        self,
        cfg: Config,
        recorder,
        transcriber,
        cleaner,
        injector,
        before_insert: Callable[[], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.recorder = recorder
        self.transcriber = transcriber
        self.cleaner = cleaner
        self.injector = injector
        self.before_insert = before_insert
        self.state = LOADING
        self.last_result: Result | None = None
        self.listeners: list[Callable[[str], None]] = []
        self.error_listeners: list[Callable[[str], None]] = []
        self.result_listeners: list[Callable[[Result], None]] = []  # after each paste
        self._jobs: queue.Queue[Job | None] = queue.Queue()
        self._pending = 0
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._work, name="cadence-worker", daemon=True)
        self._pressed_at = 0.0
        self._latched = False  # recording continues after the shortcut is released
        self._ignore_next_up = False
        self._utterance = Utterance()
        self._cut = 0  # samples of the current recording already handed to the worker

    # --- state ---------------------------------------------------------------
    def _set_state(self, state: str) -> None:
        self.state = state
        for fn in self.listeners:
            try:
                fn(state)
            except Exception:
                log.exception("State listener failed")

    def _error(self, message: str) -> None:
        log.error(message)
        if self.cfg.ui.sounds:
            sounds.error()
        for fn in self.error_listeners:
            try:
                fn(message)
            except Exception:
                log.exception("Error listener failed")

    # --- lifecycle ------------------------------------------------------------
    def load(self) -> None:
        self._set_state(LOADING)
        self.transcriber.load()
        self.recorder.warm_up()
        self._worker.start()
        self._set_state(IDLE)

    def shutdown(self) -> None:
        self._jobs.put(None)
        self.recorder.shutdown()

    # --- hotkey actions -----------------------------------------------------------
    def start_recording(self) -> None:
        with self._lock:
            if self.state in (RECORDING, LOADING):
                return
            try:
                self.recorder.start()
            except Exception as e:
                self._error(f"Could not open microphone: {e}")
                return
            self._utterance = Utterance()
            self._cut = 0
            self._set_state(RECORDING)
        if self.cfg.stt.background and hasattr(self.recorder, "read"):
            threading.Thread(
                target=self._split_on_pauses, args=(self._utterance,), daemon=True
            ).start()
        if self.cfg.ui.sounds:
            sounds.start()

    def stop_recording(self, cancel: bool = False) -> None:
        with self._lock:
            if self.state != RECORDING:
                return
            audio = self.recorder.stop()
            utterance = self._utterance
            if cancel:
                log.info("Recording cancelled")
                utterance.cancelled = True  # drops queued segments and pending text
                self._set_state(PROCESSING if self._pending else IDLE)
                return
            utterance.stopped_at = time.perf_counter()
            self._pending += 1
            self._set_state(PROCESSING)
            self._jobs.put(Job(audio[self._cut:], utterance, final=True))
        if self.cfg.ui.sounds:
            sounds.stop()

    def _split_on_pauses(self, utterance: Utterance) -> None:
        """While recording, hand each finished phrase to the worker at the next pause, so
        it's transcribed while the user keeps talking. Nothing is typed until they stop."""
        from cadence.segmenter import find_pause_cut

        sr = self.cfg.audio.sample_rate
        while True:
            time.sleep(0.15)
            with self._lock:
                if self.state != RECORDING or self._utterance is not utterance:
                    return
                audio = self.recorder.read(self._cut)
                cut = find_pause_cut(
                    audio, sr, self.cfg.stt.pause_seconds, self.cfg.stt.min_chunk_seconds
                )
                if cut is None:
                    continue
                self._cut += cut
                self._pending += 1
                self._jobs.put(Job(audio[:cut], utterance, final=False))

    def _set_latched(self, latched: bool) -> None:
        self._latched = latched

    def toggle(self) -> None:
        if self.state == RECORDING:
            self.stop_recording()
        else:
            self.start_recording()

    # --- input sources ------------------------------------------------------------
    # hold:        record while the shortcut is held.
    # toggle:      press to start, press again to stop.
    # tap_or_hold: hold to talk, or tap quickly to start hands-free (tap again to stop).
    def hotkey_down(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if self.state == RECORDING:
            if self._latched:  # hands-free recording: this press stops it
                self._latched = False
                self._ignore_next_up = True
                self.stop_recording()
            return
        self._pressed_at = now
        self._latched = False
        self.start_recording()

    def hotkey_up(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if self._ignore_next_up:
            self._ignore_next_up = False
            return
        if self.state != RECORDING or self._latched:
            return
        mode = self.cfg.hotkey.mode
        tapped = now - self._pressed_at < TAP_SECONDS
        if mode == "toggle" or (mode == "tap_or_hold" and tapped):
            self._set_latched(True)  # keep listening until the next press or click
            return
        self.stop_recording()

    def click(self) -> None:
        """On-screen button: click to start listening, click again to stop."""
        if self.state == RECORDING:
            self._latched = False
            self.stop_recording()
        else:
            self.start_recording()
            self._set_latched(self.state == RECORDING)

    def cancel(self) -> None:
        self._latched = False
        self.stop_recording(cancel=True)

    # --- processing -------------------------------------------------------------
    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            try:
                self._process_job(job)
            except Exception as e:
                log.exception("Dictation failed")
                self._error(f"Dictation failed: {e}")
            finally:
                with self._lock:
                    self._pending -= 1
                    if self.state != RECORDING:
                        self._set_state(PROCESSING if self._pending else IDLE)

    def process(self, audio: np.ndarray) -> Result:
        """Process one complete recording synchronously (single segment)."""
        utterance = Utterance()
        self._process_job(Job(audio, utterance, final=True))
        return utterance.result

    def _process_job(self, job: Job) -> None:
        """Transcribe one segment; at the final one, clean and type the whole dictation.

        Segments of one utterance arrive in order. Cleaning all their text in one go lets the
        rules and the LLM see across the pauses where the audio was split (a repeat or a
        "no wait" spanning two segments).
        """
        u = job.utterance
        if u.cancelled:
            return
        u.segments += 1
        self.last_result = u.result
        raw = self._transcribe(job.audio, u, alone=job.final and u.segments == 1)
        if raw:
            u.pending.append(raw)
        if not job.final or not u.pending:
            return
        text = self._clean(" ".join(u.pending), u)
        u.pending = []
        if not text or u.cancelled:
            return
        if self.before_insert:
            self.before_insert()
        t0 = time.perf_counter()
        self.injector.insert(text)
        u.result.timings["insert"] = time.perf_counter() - t0
        u.result.text = text
        u.result.skipped = None
        for fn in self.result_listeners:
            try:
                fn(u.result)
            except Exception:
                log.exception("Result listener failed")
        if u.stopped_at is not None:
            # The latency the user feels: from finishing speaking to text appearing.
            u.result.timings["after_stop"] = time.perf_counter() - u.stopped_at
            log.info(
                "Dictated %.1fs audio in %d segment(s) | text appeared %.0fms after you stopped",
                u.result.audio_seconds, u.segments, u.result.timings["after_stop"] * 1000,
            )

    def _transcribe(self, audio: np.ndarray, u: Utterance, alone: bool) -> str:
        """Speech to raw text for one segment ("" if there's nothing to transcribe)."""
        sr = self.cfg.audio.sample_rate
        result = u.result
        seconds = len(audio) / sr
        result.audio_seconds += seconds
        # The short-tap guard only applies to a whole recording, not a trailing segment.
        if alone and seconds < self.cfg.audio.min_seconds:
            result.skipped = "too short"
            return ""
        if len(audio) == 0 or float(np.max(np.abs(audio))) < self.cfg.audio.min_peak:
            result.skipped = result.skipped or "silence"
            return ""
        t0 = time.perf_counter()
        raw = self.transcriber.transcribe(audio, sr, context=u.context).text.strip()
        elapsed = time.perf_counter() - t0
        result.timings["stt"] = result.timings.get("stt", 0.0) + elapsed
        log.info("Segment %d: %.1fs audio | stt=%.0fms", u.segments, seconds, elapsed * 1000)
        if not raw:
            result.skipped = result.skipped or "no speech"
            return ""
        result.raw = f"{result.raw} {raw}".strip()
        u.context = result.raw[-400:]
        return raw

    def _clean(self, raw: str, u: Utterance) -> str:
        """Rules (+ LLM) cleanup of the whole dictation, with a trailing space."""
        result = u.result
        t0 = time.perf_counter()
        text = self.cleaner.clean(raw)
        timings = {"cleanup": time.perf_counter() - t0}
        timings.update(
            {f"cleanup.{k}": v for k, v in getattr(self.cleaner, "last_timings", {}).items()}
        )
        result.timings.update(timings)
        llm = getattr(self.cleaner, "llm", None)
        used_llm = llm is not None and getattr(self.cleaner, "last_llm_status", "") == "used"
        result.cleanup = llm.name if used_llm else "rules"
        log.info("Cleanup | %s", " ".join(f"{k}={v * 1000:.0f}ms" for k, v in timings.items()))
        log.debug("raw=%r clean=%r", raw, text)
        if not text:
            result.skipped = result.skipped or "empty after cleanup"
            return ""
        if self.cfg.output.trailing_space and not text.endswith(("\n", " ")):
            text += " "
        return text


def run(cfg: Config) -> None:
    """Wire real components together and run until quit."""
    from cadence import __version__

    log.info("Cadence %s starting", __version__)
    if sys.platform == "win32":
        from cadence.win32 import enable_dpi_awareness

        enable_dpi_awareness()  # crisp overlay on scaled displays; before any window exists
    from cadence.audio import Recorder
    from cadence.cleanup import build_cleaner
    from cadence.hotkey import GlobalHotkeyListener
    from cadence.output import build_injector
    from cadence.stt import build_transcriber

    listener: GlobalHotkeyListener | None = None

    def wait_for_modifiers_released() -> None:
        # Pasting while the user still holds Ctrl/Win would send the wrong shortcut.
        deadline = time.monotonic() + 1.5
        while listener and listener.modifiers_held() and time.monotonic() < deadline:
            time.sleep(0.01)

    dictation = Dictation(
        cfg,
        Recorder(cfg.audio.sample_rate, cfg.audio.device, cfg.audio.keep_stream_open),
        build_transcriber(cfg),
        build_cleaner(cfg),
        build_injector(cfg),
        before_insert=wait_for_modifiers_released,
    )

    listener = GlobalHotkeyListener(
        cfg.hotkey.combo,
        dictation.hotkey_down,
        dictation.hotkey_up,
        on_escape=dictation.cancel,
    )

    from cadence.feedback import FeedbackStore

    feedback_store = FeedbackStore()
    overlay = None
    if cfg.ui.overlay:
        from cadence.overlay import Overlay

        overlay = Overlay(
            dictation, cfg.hotkey.combo, show_button=cfg.ui.button,
            feedback_hotkey=cfg.feedback.hotkey, prompt_seconds=cfg.feedback.prompt_seconds,
            save_feedback=feedback_store.add,
        )
        overlay.start()
    open_feedback = overlay.request_feedback if overlay else None

    # A second shortcut that opens the feedback window for the last dictation.
    feedback_listener = None
    if open_feedback and cfg.feedback.hotkey:
        feedback_listener = GlobalHotkeyListener(cfg.feedback.hotkey, open_feedback, lambda: None)

    tray = None
    if cfg.ui.tray:
        try:
            from cadence.tray import Tray

            tray = Tray(dictation, cfg, open_feedback=open_feedback)
        except ImportError:
            log.warning("pystray/Pillow not installed; running without a tray icon")

    def load() -> None:
        try:
            dictation.load()
        except Exception as e:
            log.exception("Failed to load")
            dictation._error(f"Failed to load speech model: {e}")
            return
        listener.start()
        if feedback_listener:
            feedback_listener.start()
        log.info("Ready. Hotkey %s (%s mode), or click the mic button. Esc cancels.", cfg.hotkey.combo, cfg.hotkey.mode)

    if tray is not None:
        # The tray runs its own message loop on a background thread, so the main thread
        # stays in Python and Ctrl+C in the console still works.
        tray.start()
        threading.Thread(target=load, daemon=True).start()
    else:
        load()
    try:
        while tray is None or not tray.stopped.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        log.info("Stopping (Ctrl+C)")
    if tray is not None:
        tray.stop()
    listener.stop()
    if feedback_listener:
        feedback_listener.stop()
    dictation.shutdown()
