"""faster-whisper (CTranslate2) backend, GPU-first with automatic CPU fallback."""

from __future__ import annotations

import logging
import os
import sys
import time

import numpy as np

from flow.stt import Transcript

log = logging.getLogger(__name__)

# Phrases Whisper is known to hallucinate on silence or noise. ("Thank you." is also a
# classic, but people genuinely dictate it; the energy gate and VAD cover that case.)
_HALLUCINATIONS = {
    "thanks for watching!",
    "thanks for watching.",
    "thank you for watching.",
    "you",
    ".",
}


def add_nvidia_dll_dirs() -> None:
    """On Windows, make pip-installed CUDA libraries (nvidia-cublas-cu12,
    nvidia-cudnn-cu12) loadable by CTranslate2 without a system CUDA install."""
    if sys.platform != "win32":
        return
    try:
        import nvidia  # namespace package provided by the nvidia-* wheels
    except ImportError:
        return
    for root in list(nvidia.__path__):
        if not os.path.isdir(root):
            continue
        for lib in os.listdir(root):
            bin_dir = os.path.join(root, lib, "bin")
            if os.path.isdir(bin_dir):
                os.add_dll_directory(bin_dir)
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


def cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


class FasterWhisperTranscriber:
    def __init__(
        self,
        model: str = "large-v3-turbo",
        device: str = "auto",
        compute_type: str = "auto",
        language: str | None = "en",
        beam_size: int = 1,
        vad_filter: bool = True,
        vocabulary: list[str] | None = None,
    ) -> None:
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.initial_prompt = ("Vocabulary: " + ", ".join(vocabulary) + ".") if vocabulary else None
        self._model = None
        self.active_device = None

    def _create(self, device: str):
        from faster_whisper import WhisperModel

        compute = self.compute_type
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        log.info("Loading Whisper %r on %s (%s)", self.model_name, device, compute)
        model = WhisperModel(self.model_name, device=device, compute_type=compute)
        # Warm up: surfaces missing CUDA DLLs now (they only fail at first inference)
        # and makes the first real dictation fast.
        segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), beam_size=1)
        list(segments)
        return model

    def load(self) -> None:
        if self._model is not None:
            return
        add_nvidia_dll_dirs()
        t0 = time.perf_counter()
        device = self.device
        if device == "auto":
            device = "cuda" if cuda_available() else "cpu"
        try:
            self._model = self._create(device)
        except Exception as e:
            if device != "cuda" or self.device == "cuda":
                raise
            log.warning("CUDA initialization failed (%s); falling back to CPU", e)
            device = "cpu"
            self._model = self._create(device)
        self.active_device = device
        log.info("Whisper ready on %s in %.1fs", device, time.perf_counter() - t0)

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000, context: str = "") -> Transcript:
        if self._model is None:
            self.load()
        if sample_rate != 16000:
            raise ValueError("faster-whisper expects 16 kHz audio")
        segments, info = self._model.transcribe(
            audio.astype(np.float32, copy=False),
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=self.vad_filter,
            vad_parameters={"min_silence_duration_ms": 500},
            # Earlier phrases of the same dictation keep wording and style consistent.
            initial_prompt=" ".join(p for p in (self.initial_prompt, context[-300:]) if p) or None,
            # Each dictation is independent; conditioning only causes repetition loops.
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        if text.lower() in _HALLUCINATIONS:
            text = ""
        return Transcript(text, info.language, len(audio) / sample_rate)
