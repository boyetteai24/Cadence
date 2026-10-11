"""Microphone capture. Recording length is unbounded (chunks accumulate in memory:
16 kHz float32 is ~3.8 MB per minute)."""

from __future__ import annotations

import logging
import threading

import numpy as np

log = logging.getLogger(__name__)


class Recorder:
    def __init__(self, sample_rate: int = 16000, device=None, keep_stream_open: bool = False):
        self.sample_rate = sample_rate
        self.device = device
        self.keep_stream_open = keep_stream_open
        self._stream = None
        self._chunks: list[np.ndarray] = []
        self._recording = False
        self._lock = threading.Lock()
        self.level = 0.0  # RMS of the latest audio block, for the on-screen meter

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("Audio status: %s", status)
        if self._recording:
            block = indata[:, 0].copy()
            self.level = float(np.sqrt(np.mean(block * block)))
            with self._lock:
                self._chunks.append(block)

    def _open(self) -> None:
        if self._stream is not None:
            return
        import sounddevice as sd

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=self.device,
            blocksize=int(self.sample_rate * 0.03),
            callback=self._callback,
        )
        self._stream.start()

    def _close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def warm_up(self) -> None:
        if self.keep_stream_open:
            self._open()

    def start(self) -> None:
        with self._lock:
            self._chunks = []
        self._recording = True
        self._open()

    def read(self, start: int) -> np.ndarray:
        """Audio captured so far from sample `start` on, without stopping the recording."""
        with self._lock:
            chunks = self._chunks
            total = sum(len(c) for c in chunks)
            if start >= total:
                return np.zeros(0, dtype=np.float32)
            # Walk back from the newest chunk until we cover `start`; only copy that tail.
            tail, pos = [], total
            for c in reversed(chunks):
                tail.append(c)
                pos -= len(c)
                if pos <= start:
                    break
        audio = np.concatenate(tail[::-1])
        return audio[start - pos:]

    def stop(self) -> np.ndarray:
        self._recording = False
        self.level = 0.0
        if not self.keep_stream_open:
            self._close()
        with self._lock:
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)

    def shutdown(self) -> None:
        self._recording = False
        self._close()


def list_input_devices() -> list[str]:
    import sounddevice as sd

    default_in = sd.default.device[0]
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            mark = "*" if i == default_in else " "
            out.append(f"{mark} [{i}] {d['name']} ({int(d['default_samplerate'])} Hz)")
    return out
