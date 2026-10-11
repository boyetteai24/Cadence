"""Speech-to-text backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from cadence.config import Config


@dataclass
class Word:
    text: str  # as produced by the model, usually with a leading space and punctuation
    start: float  # seconds from the start of the transcribed audio
    end: float


@dataclass
class Transcript:
    text: str
    language: str | None = None
    audio_seconds: float = 0.0
    words: list[Word] = field(default_factory=list)  # only with word_timestamps=True


class Transcriber(Protocol):
    def load(self) -> None:
        """Load the model (slow). Called once at start-up."""
        ...

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        context: str = "",
        word_timestamps: bool = False,
    ) -> Transcript:
        """audio: mono float32 in [-1, 1]. context: preceding text of the same dictation.
        word_timestamps: also fill Transcript.words (used for live word-by-word typing)."""
        ...


def build_transcriber(cfg: Config) -> Transcriber:
    if cfg.stt.backend == "faster-whisper":
        from cadence.stt.whisper import FasterWhisperTranscriber

        return FasterWhisperTranscriber(
            model=cfg.stt.model,
            device=cfg.stt.device,
            compute_type=cfg.stt.compute_type,
            language=cfg.stt.language,
            beam_size=cfg.stt.beam_size,
            vad_filter=cfg.stt.vad_filter,
            vocabulary=cfg.dictionary.words,
        )
    raise ValueError(f"Unknown stt.backend: {cfg.stt.backend!r}")
