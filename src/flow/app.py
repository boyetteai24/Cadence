"""Dictation controller: hotkey -> record -> transcribe -> clean -> insert.

`Dictation` holds no OS-specific code; all I/O components are injected, so the whole
pipeline can be tested with fakes.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from flow import sounds
from flow.config import Config

log = logging.getLogger(__name__)

IDLE, RECORDING, PROCESSING, LOADING = "idle", "recording", "processing", "loading"


@dataclass
class Result:
    raw: str = ""
    text: str = ""
    audio_seconds: float = 0.0
    timings: dict[str, float] = field(default_factory=dict)
    skipped: str | None = None  # reason, if nothing was inserted


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
        self._jobs: queue.Queue[np.ndarray | None] = queue.Queue()
        self._pending = 0
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._work, name="flow-worker", daemon=True)

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
            self._set_state(RECORDING)
        if self.cfg.ui.sounds:
            sounds.start()

    def stop_recording(self, cancel: bool = False) -> None:
        with self._lock:
            if self.state != RECORDING:
                return
            audio = self.recorder.stop()
            if cancel:
                log.info("Recording cancelled")
                self._set_state(PROCESSING if self._pending else IDLE)
                return
            self._pending += 1
            self._set_state(PROCESSING)
        if self.cfg.ui.sounds:
            sounds.stop()
        self._jobs.put(audio)

    def toggle(self) -> None:
        if self.state == RECORDING:
            self.stop_recording()
        else:
            self.start_recording()

    # --- processing -------------------------------------------------------------
    def _work(self) -> None:
        while True:
            audio = self._jobs.get()
            if audio is None:
                return
            try:
                self.process(audio)
            except Exception as e:
                log.exception("Dictation failed")
                self._error(f"Dictation failed: {e}")
            finally:
                with self._lock:
                    self._pending -= 1
                    if self.state != RECORDING:
                        self._set_state(PROCESSING if self._pending else IDLE)

    def process(self, audio: np.ndarray) -> Result:
        """Synchronous pipeline for one recording. Utterances are processed in order."""
        sr = self.cfg.audio.sample_rate
        result = Result(audio_seconds=len(audio) / sr)
        self.last_result = result
        if result.audio_seconds < self.cfg.audio.min_seconds:
            result.skipped = "too short"
            return result
        if float(np.max(np.abs(audio))) < self.cfg.audio.min_peak:
            result.skipped = "silence"
            return result

        t0 = time.perf_counter()
        transcript = self.transcriber.transcribe(audio, sr)
        result.timings["stt"] = time.perf_counter() - t0
        result.raw = transcript.text
        if not transcript.text.strip():
            result.skipped = "no speech"
            return result

        t0 = time.perf_counter()
        text = self.cleaner.clean(transcript.text)
        result.timings["cleanup"] = time.perf_counter() - t0
        result.timings.update(
            {f"cleanup.{k}": v for k, v in getattr(self.cleaner, "last_timings", {}).items()}
        )
        if not text:
            result.skipped = "empty after cleanup"
            return result
        if self.cfg.output.trailing_space and not text.endswith(("\n", " ")):
            text += " "
        result.text = text

        if self.before_insert:
            self.before_insert()
        t0 = time.perf_counter()
        self.injector.insert(text)
        result.timings["insert"] = time.perf_counter() - t0
        log.info(
            "Dictated %.1fs audio | %s",
            result.audio_seconds,
            " ".join(f"{k}={v * 1000:.0f}ms" for k, v in result.timings.items()),
        )
        log.debug("raw=%r clean=%r", result.raw, text)
        return result


def run(cfg: Config) -> None:
    """Wire real components together and run until quit."""
    from flow.audio import Recorder
    from flow.cleanup import build_cleaner
    from flow.hotkey import GlobalHotkeyListener
    from flow.output import build_injector
    from flow.stt import build_transcriber

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

    if cfg.hotkey.mode == "hold":
        on_activate, on_deactivate = dictation.start_recording, dictation.stop_recording
    else:
        on_activate, on_deactivate = dictation.toggle, (lambda: None)
    listener = GlobalHotkeyListener(
        cfg.hotkey.combo,
        on_activate,
        on_deactivate,
        on_escape=lambda: dictation.stop_recording(cancel=True),
    )

    tray = None
    if cfg.ui.tray:
        try:
            from flow.tray import Tray

            tray = Tray(dictation, cfg)
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
        log.info("Ready. Hold %s and speak (%s mode). Esc cancels.", cfg.hotkey.combo, cfg.hotkey.mode)

    if tray is not None:
        # Tray must own the main thread; load the model in the background meanwhile.
        threading.Thread(target=load, daemon=True).start()
        tray.run()
    else:
        load()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    listener.stop()
    dictation.shutdown()
