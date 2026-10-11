from cadence.app import IDLE, LOADING, PROCESSING, RECORDING
from cadence.overlay import ERROR_SECONDS, READY_SECONDS, compute_view


def view(state, now=100.0, ready_at=None, error=None):
    return compute_view(state, now, ready_at, error, "Ctrl + Win")


def test_states_map_to_views():
    assert view(LOADING).text == "Cadence is loading…"
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


def test_button_position_is_remembered(tmp_path, monkeypatch):
    from cadence import overlay

    monkeypatch.setattr(overlay, "_state_file", lambda: tmp_path / "overlay.json")
    assert overlay.load_position() is None
    overlay.save_position(120, -40)  # negative: a monitor left of the primary one
    assert overlay.load_position() == (120, -40)
    (tmp_path / "overlay.json").write_text("garbage")
    assert overlay.load_position() is None


def test_feedback_prompt_after_paste_then_back_to_button():
    kw = dict(pasted_at=100.0, prompt_seconds=4.0, feedback_hotkey="Ctrl + Alt + F")
    v = compute_view(IDLE, 101.0, None, None, "Ctrl + Win", **kw)
    assert v.kind == "feedback_prompt" and v.text == "Pasted · Feedback · Ctrl + Alt + F"
    assert compute_view(IDLE, 105.0, None, None, "Ctrl + Win", **kw).kind == "button"
    assert compute_view(RECORDING, 101.0, None, None, "Ctrl + Win", **kw).kind == "listening"
    off = dict(kw, prompt_seconds=0.0)
    assert compute_view(IDLE, 101.0, None, None, "Ctrl + Win", **off).kind == "button"


def test_notice_shows_briefly():
    v = compute_view(IDLE, 101.0, None, None, "Ctrl + Win", notice=(100.0, "Feedback saved · thanks!"))
    assert v.text == "Feedback saved · thanks!"
    assert compute_view(IDLE, 103.0, None, None, "Ctrl + Win",
                        notice=(100.0, "Feedback saved")).kind == "button"
