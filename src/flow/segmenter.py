"""Find natural pauses in ongoing speech, so finished phrases can be transcribed (and,
hands-free, typed) while the user keeps talking.

A cheap energy detector is enough here: it only picks cut points. Whisper's own VAD still
runs on every segment.
"""

from __future__ import annotations

import numpy as np

FRAME_SECONDS = 0.03
MIN_SPEECH_SECONDS = 0.4  # a segment must contain at least this much speech
CUT_PADDING_SECONDS = 0.15  # keep a little silence after the last word


def frame_rms(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    n = int(sample_rate * FRAME_SECONDS)
    frames = len(audio) // n
    if frames == 0:
        return np.zeros(0, dtype=np.float32)
    x = audio[: frames * n].reshape(frames, n)
    return np.sqrt(np.mean(x * x, axis=1))


def speech_threshold(rms: np.ndarray, floor: float = 0.008) -> float:
    """Adaptive: well above the quietest frames (room noise), never below `floor`."""
    if len(rms) == 0:
        return floor
    return max(floor, 3.0 * float(np.percentile(rms, 10)))


def find_pause_cut(
    audio: np.ndarray,
    sample_rate: int,
    pause_seconds: float = 0.6,
    min_chunk_seconds: float = 2.0,
) -> int | None:
    """Return a sample index to cut `audio` at, or None to keep waiting.

    Cuts only when the chunk is long enough, contains real speech, and ends in a pause
    of at least `pause_seconds`. The cut lands just after the last spoken frame.
    """
    if len(audio) < min_chunk_seconds * sample_rate:
        return None
    rms = frame_rms(audio, sample_rate)
    threshold = speech_threshold(rms)
    speaking = rms > threshold
    if not speaking.any():
        return None
    last_speech = int(np.flatnonzero(speaking)[-1])
    silent_tail = len(rms) - 1 - last_speech
    if silent_tail * FRAME_SECONDS < pause_seconds:
        return None
    if speaking.sum() * FRAME_SECONDS < MIN_SPEECH_SECONDS:
        return None
    frame_len = int(sample_rate * FRAME_SECONDS)
    cut = (last_speech + 1) * frame_len + int(CUT_PADDING_SECONDS * sample_rate)
    return min(cut, len(audio))
