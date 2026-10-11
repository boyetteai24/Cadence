import time

import numpy as np

from cadence import streaming
from cadence.app import IDLE, Dictation
from cadence.cleanup import CleanupChain
from cadence.cleanup.rules import RuleCleaner
from cadence.config import Config
from cadence.stt import Transcript, Word

SR = 16000


def W(text, start, end):
    return Word(text, start, end)


# --- pure agreement logic -------------------------------------------------------------

def test_commit_needs_two_agreeing_passes_and_lag():
    cur = [W(f" w{i}", i, i + 0.5) for i in range(8)]
    assert streaming.words_to_commit(None, cur, lag_words=5, at_pause=False) == 0
    assert streaming.words_to_commit(cur, cur, lag_words=5, at_pause=False) == 3
    changed = cur[:2] + [W(" other", 2, 2.5)] + cur[3:]
    assert streaming.words_to_commit(changed, cur, lag_words=5, at_pause=False) == 2


def test_pause_commits_everything():
    cur = [W(" a", 0, 1), W(" b.", 1, 2)]
    assert streaming.words_to_commit(None, cur, lag_words=5, at_pause=True) == 2


def test_agreement_ignores_case_and_punctuation():
    assert streaming.agreed_prefix([W(" Hello,", 0, 1)], [W(" hello", 0, 1)]) == 1


def test_cut_lands_between_words():
    words = [W(" a", 0.0, 0.4), W(" b", 0.6, 1.0)]
    assert streaming.cut_seconds(words, 1, 2.0) == 0.5
    assert streaming.cut_seconds(words, 2, 1.05) == 1.05  # never past the audio


# --- end to end with a fake microphone and a fake word-level transcriber -----------------

SCRIPT = ["hello", "there", "um", "this", "is", "a", "test.", "then", "we", "keep",
          "talking", "for", "a", "while."]
WORD_S, GAP_S = 0.35, 0.12


def script_audio(pause_after=6):
    """Each word is a constant-amplitude block encoding its index; gaps are silence."""
    parts = []
    for i in range(len(SCRIPT)):
        parts.append(np.full(int(WORD_S * SR), 0.05 + 0.01 * i, dtype=np.float32))
        gap = 1.0 if i == pause_after else GAP_S
        parts.append(np.zeros(int(gap * SR), dtype=np.float32))
    parts.append(np.zeros(int(1.0 * SR), dtype=np.float32))
    return np.concatenate(parts)


class WordTranscriber:
    """Decodes the words from the amplitude blocks. A block cut off at the end of the audio
    is reported as an unfinished word, like a real model mid-word."""

    def __init__(self):
        self.calls = 0

    def load(self):
        pass

    def transcribe(self, audio, sample_rate=SR, context="", word_timestamps=False):
        self.calls += 1
        frame = SR // 100
        n = len(audio) // frame
        levels = np.abs(audio[: n * frame]).reshape(n, frame).max(axis=1)
        words, i = [], 0
        while i < n:
            if levels[i] < 0.01:
                i += 1
                continue
            j = i
            while j < n and levels[j] >= 0.01:
                j += 1
            idx = int(round((float(np.median(levels[i:j])) - 0.05) / 0.01))
            text = SCRIPT[idx] if idx < len(SCRIPT) else "?"
            if j == n and (j - i) * 0.01 < WORD_S - 0.02:
                text += "-"  # unfinished
            words.append(Word(" " + text, i * 0.01, j * 0.01))
            i = j
        return Transcript("".join(w.text for w in words).strip(), "en",
                          len(audio) / sample_rate, words if word_timestamps else [])


class LiveRecorder:
    SPEEDUP = 4

    def __init__(self, audio):
        self.audio = audio

    def warm_up(self):
        pass

    def start(self):
        self.t0 = time.monotonic()

    def read(self, start):
        available = int((time.monotonic() - self.t0) * SR * self.SPEEDUP)
        return self.audio[start:min(available, len(self.audio))]

    def stop(self):
        return self.audio

    def shutdown(self):
        pass


class Injector:
    def __init__(self):
        self.inserted = []

    def insert(self, text):
        self.inserted.append(text)


def words_app(mode="tap_or_hold"):
    cfg = Config()
    cfg.ui.sounds = False
    cfg.hotkey.mode = mode
    cfg.stt.live_mode = "words"
    cfg.stt.pass_interval = 0.05
    injector = Injector()
    transcriber = WordTranscriber()
    d = Dictation(cfg, LiveRecorder(script_audio()), transcriber,
                  CleanupChain(RuleCleaner()), injector)
    d.load()
    return d, injector, transcriber


def wait_for(predicate, timeout=8.0):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        time.sleep(0.01)
    return predicate()


EXPECTED = "Hello there this is a test. Then we keep talking for a while. "


def test_hands_free_types_words_while_talking_without_loss_or_duplicates():
    d, injector, _ = words_app()
    d.click()
    # Words appear while still recording, before the speaker has finished.
    assert wait_for(lambda: len(injector.inserted) >= 2)
    assert d.state == "recording"
    assert wait_for(lambda: "".join(injector.inserted).endswith("while. "))
    d.click()
    assert wait_for(lambda: d.state == IDLE)
    assert "".join(injector.inserted) == EXPECTED


def test_holding_types_nothing_until_release():
    d, injector, transcriber = words_app(mode="hold")
    d.hotkey_down(now=0.0)
    assert wait_for(lambda: transcriber.calls >= 4)
    assert injector.inserted == []
    d.hotkey_up(now=60.0)
    assert wait_for(lambda: d.state == IDLE)
    assert injector.inserted == [EXPECTED]


def test_stopping_mid_sentence_loses_nothing():
    d, injector, transcriber = words_app(mode="hold")
    d.hotkey_down(now=0.0)
    assert wait_for(lambda: transcriber.calls >= 2)
    d.hotkey_up(now=60.0)  # the rest is covered by the final job
    assert wait_for(lambda: d.state == IDLE)
    assert "".join(injector.inserted) == EXPECTED


def test_llm_cleanup_falls_back_to_pause_mode():
    d, _, _ = words_app()
    d.cfg.cleanup.llm = "claude"
    assert d.live_mode() == "pauses"
