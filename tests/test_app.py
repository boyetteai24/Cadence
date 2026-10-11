import time

import numpy as np

from flow.app import IDLE, Dictation
from flow.cleanup import CleanupChain
from flow.cleanup.rules import RuleCleaner
from flow.config import Config
from flow.stt import Transcript


class FakeRecorder:
    def __init__(self, audio):
        self.audio = audio
        self.started = 0

    def warm_up(self):
        pass

    def start(self):
        self.started += 1

    def stop(self):
        return self.audio

    def shutdown(self):
        pass


class FakeTranscriber:
    def __init__(self, text):
        self.text = text

    def load(self):
        pass

    def transcribe(self, audio, sample_rate=16000):
        return Transcript(self.text, "en", len(audio) / sample_rate)


class FakeInjector:
    def __init__(self):
        self.inserted = []

    def insert(self, text):
        self.inserted.append(text)


def make(text="um so this is a test", audio=None):
    cfg = Config()
    cfg.ui.sounds = False
    if audio is None:
        audio = (np.sin(np.linspace(0, 1000, 32000)) * 0.3).astype(np.float32)
    injector = FakeInjector()
    d = Dictation(cfg, FakeRecorder(audio), FakeTranscriber(text), CleanupChain(RuleCleaner()), injector)
    return d, injector


def wait_idle(d, timeout=2.0):
    end = time.monotonic() + timeout
    while d.state != IDLE and time.monotonic() < end:
        time.sleep(0.01)


def test_end_to_end_hold_flow():
    d, injector = make()
    d.load()
    d.start_recording()
    d.stop_recording()
    wait_idle(d)
    assert injector.inserted == ["So this is a test "]
    assert d.last_result.raw == "um so this is a test"
    assert "stt" in d.last_result.timings


def test_cancel_inserts_nothing():
    d, injector = make()
    d.load()
    d.start_recording()
    d.stop_recording(cancel=True)
    wait_idle(d)
    assert injector.inserted == []
    assert d.state == IDLE


def test_silence_and_short_taps_are_skipped():
    d, injector = make(audio=np.zeros(32000, dtype=np.float32))
    assert d.process(d.recorder.audio).skipped == "silence"
    d, injector = make(audio=np.ones(1600, dtype=np.float32) * 0.5)
    assert d.process(d.recorder.audio).skipped == "too short"
    assert injector.inserted == []


def test_empty_transcript_is_skipped():
    d, injector = make(text="  ")
    assert d.process(d.recorder.audio).skipped == "no speech"
    assert injector.inserted == []


def test_toggle_mode():
    d, injector = make(text="hello world")
    d.load()
    d.toggle()
    d.toggle()
    wait_idle(d)
    assert injector.inserted == ["Hello world "]


def hotkey_app(mode):
    d, injector = make(text="hello world")
    d.cfg.hotkey.mode = mode
    d.load()
    return d, injector


def test_tap_or_hold_hold_releases_to_stop():
    d, injector = hotkey_app("tap_or_hold")
    d.hotkey_down(now=10.0)
    d.hotkey_up(now=12.0)  # held 2 s
    wait_idle(d)
    assert injector.inserted == ["Hello world "]


def test_tap_or_hold_tap_starts_hands_free_and_tap_stops():
    d, injector = hotkey_app("tap_or_hold")
    d.hotkey_down(now=10.0)
    d.hotkey_up(now=10.1)  # quick tap: keep listening
    assert d.state == "recording"
    d.hotkey_down(now=15.0)  # tap again: stop
    d.hotkey_up(now=15.1)
    wait_idle(d)
    assert injector.inserted == ["Hello world "]


def test_hold_mode_never_latches():
    d, injector = hotkey_app("hold")
    d.hotkey_down(now=10.0)
    d.hotkey_up(now=10.1)
    assert d.state != "recording"


def test_click_starts_and_stops_and_hotkey_can_stop_it():
    d, injector = hotkey_app("tap_or_hold")
    d.click()
    assert d.state == "recording"
    d.click()
    wait_idle(d)
    assert injector.inserted == ["Hello world "]
    d.click()
    d.hotkey_down(now=20.0)  # shortcut also stops a click-started recording
    d.hotkey_up(now=20.1)
    wait_idle(d)
    assert len(injector.inserted) == 2


def test_escape_cancels_hands_free_recording():
    d, injector = hotkey_app("tap_or_hold")
    d.click()
    d.cancel()
    wait_idle(d)
    assert injector.inserted == []
    d.hotkey_down(now=30.0)  # fresh press starts a new recording, not a stale stop
    assert d.state == "recording"
