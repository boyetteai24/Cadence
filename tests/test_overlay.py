from flow.app import IDLE, LOADING, PROCESSING, RECORDING
from flow.overlay import ERROR_SECONDS, READY_SECONDS, compute_view


def view(state, now=100.0, ready_at=None, error=None):
    return compute_view(state, now, ready_at, error, "Ctrl + Win")


def test_states_map_to_views():
    assert view(LOADING).text == "Flow is loading…"
    assert view(RECORDING).kind == "listening"
    assert view(PROCESSING).kind == "working"


def test_ready_message_shows_briefly_after_load():
    assert "Ctrl + Win or click" in view(IDLE, now=101.0, ready_at=100.0).text
    assert view(IDLE, now=100.0 + READY_SECONDS + 0.1, ready_at=100.0).kind == "button"


def test_errors_take_priority_then_expire():
    err = (100.0, "Could not open microphone")
    assert view(RECORDING, now=101.0, error=err).text == "Could not open microphone"
    assert view(IDLE, now=100.0 + ERROR_SECONDS + 0.1, error=err).kind == "button"


def test_idle_shows_mic_button_unless_disabled():
    assert view(IDLE).kind == "button"
    assert compute_view(IDLE, 100.0, None, None, "Ctrl + Win", show_button=False).kind == "hidden"
