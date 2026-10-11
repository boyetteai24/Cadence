import numpy as np

from cadence.segmenter import find_pause_cut

SR = 16000


def tone(seconds, amp=0.3):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(seconds, noise=0.001):
    rng = np.random.default_rng(0)
    return (rng.standard_normal(int(seconds * SR)) * noise).astype(np.float32)


def test_cuts_after_speech_followed_by_pause():
    audio = np.concatenate([tone(2.5), silence(0.8)])
    cut = find_pause_cut(audio, SR, pause_seconds=0.6, min_chunk_seconds=2.0)
    assert cut is not None
    assert 2.5 * SR <= cut <= 2.8 * SR  # just after the last word


def test_no_cut_while_still_talking_or_pause_too_short():
    assert find_pause_cut(tone(4.0), SR) is None
    assert find_pause_cut(np.concatenate([tone(2.5), silence(0.3)]), SR) is None


def test_no_cut_for_short_chunks_or_pure_silence():
    assert find_pause_cut(np.concatenate([tone(0.8), silence(0.8)]), SR) is None
    assert find_pause_cut(silence(4.0), SR) is None
