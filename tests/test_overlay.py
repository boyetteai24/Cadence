from cadence.app import IDLE, LOADING, PROCESSING, RECORDING
from cadence.overlay import (
    ERROR_SECONDS,
    HEIGHT,
    READY_SECONDS,
    WIDTH,
    button_center,
    compute_view,
    content_x,
    is_hovering,
    nearest_preset,
    preset_positions,
)


def view(state, now=100.0, ready_at=None, error=None):
    return compute_view(state, now, ready_at, error, "Ctrl + Win")


def test_states_map_to_views():
    assert view(LOADING).text == "Cadence is loading…"
    assert view(RECORDING).kind == "listening"
    assert view(PROCESSING).kind == "working"


def test_ready_message_shows_briefly_after_load():
    assert "Ctrl + Win or click" in view(IDLE, now=101.0, ready_at=100.0).text
    assert view(IDLE, now=100.0 + READY_SECONDS + 0.1, ready_at=100.0).kind == "bar"


def test_errors_take_priority_then_expire():
    err = (100.0, "Could not open microphone")
    assert view(RECORDING, now=101.0, error=err).text == "Could not open microphone"
    assert view(IDLE, now=100.0 + ERROR_SECONDS + 0.1, error=err).kind == "bar"


def test_idle_shows_a_bar_that_becomes_the_mic_button_on_hover():
    assert view(IDLE).kind == "bar"
    assert compute_view(IDLE, 100.0, None, None, "Ctrl + Win", hovered=True).kind == "button"
    assert compute_view(RECORDING, 100.0, None, None, "Ctrl + Win", hovered=True).kind == "listening"
    assert compute_view(IDLE, 100.0, None, None, "Ctrl + Win", show_button=False).kind == "hidden"


def test_button_position_is_remembered(tmp_path, monkeypatch):
    from cadence import overlay

    monkeypatch.setattr(overlay, "_state_file", lambda: tmp_path / "overlay.json")
    assert overlay.load_position() is None
    overlay.save_position(120, -40)  # negative: a monitor left of the primary one
    assert overlay.load_position() == (120, -40, None)
    overlay.save_position(10, 500, "left")
    assert overlay.load_position() == (10, 500, "left")
    (tmp_path / "overlay.json").write_text('{"x": 1, "y": 2}')  # saved by an older version
    assert overlay.load_position() == (1, 2, None)
    (tmp_path / "overlay.json").write_text("garbage")
    assert overlay.load_position() is None


def test_feedback_prompt_after_paste_then_back_to_button():
    kw = dict(pasted_at=100.0, prompt_seconds=4.0, feedback_hotkey="Ctrl + Alt + F")
    v = compute_view(IDLE, 101.0, None, None, "Ctrl + Win", **kw)
    assert v.kind == "feedback_prompt" and v.text == "Pasted · Feedback · Ctrl + Alt + F"
    assert compute_view(IDLE, 105.0, None, None, "Ctrl + Win", **kw).kind == "bar"
    assert compute_view(RECORDING, 101.0, None, None, "Ctrl + Win", **kw).kind == "listening"
    off = dict(kw, prompt_seconds=0.0)
    assert compute_view(IDLE, 101.0, None, None, "Ctrl + Win", **off).kind == "bar"


def test_notice_shows_briefly():
    v = compute_view(IDLE, 101.0, None, None, "Ctrl + Win", notice=(100.0, "Feedback saved · thanks!"))
    assert v.text == "Feedback saved · thanks!"
    assert compute_view(IDLE, 103.0, None, None, "Ctrl + Win",
                        notice=(100.0, "Feedback saved")).kind == "bar"


WORK = (0, 0, 1920, 1032)  # a 1080p screen minus a 48 px taskbar


def test_snap_spots_are_centered_on_each_edge():
    spots = preset_positions(WORK)
    assert button_center(spots["bottom"], "bottom")[0] == 960
    assert button_center(spots["top"], "top")[0] == 960
    assert spots["bottom"][1] + HEIGHT <= 1032  # above the taskbar
    assert spots["top"][1] >= 0
    assert button_center(spots["left"], "left")[1] == 516
    assert button_center(spots["right"], "right")[1] == 516
    assert 0 <= spots["left"][0] and button_center(spots["left"], "left")[0] < 60
    assert spots["right"][0] + WIDTH <= 1920 and button_center(spots["right"], "right")[0] > 1860


def test_snap_spots_follow_the_monitor():
    second = (1920, 0, 3840, 1080)  # a monitor to the right, no taskbar
    assert button_center(preset_positions(second)["bottom"], "bottom")[0] == 2880


def test_dropping_near_a_spot_snaps_there_otherwise_stays_free():
    assert nearest_preset((950, 1000), WORK) == "bottom"
    assert nearest_preset((900, 40), WORK) == "top"
    assert nearest_preset((80, 560), WORK) == "left"
    assert nearest_preset((1850, 480), WORK) == "right"
    assert nearest_preset((700, 500), WORK) is None  # middle of the screen: free placement
    assert nearest_preset((300, 300), WORK) is None


def test_button_hugs_the_edge_it_is_snapped_to():
    assert content_x("left") < content_x(None) == content_x("bottom") < content_x("right")


def test_hover_only_near_the_bar():
    window = (100, 200)
    cx, cy = button_center(window, None)
    assert is_hovering((cx, cy), window, None)
    assert is_hovering((cx + 30, cy + 10), window, None)
    assert not is_hovering((cx + 120, cy), window, None)  # same window, but away from the bar
    assert not is_hovering((cx, cy + 60), window, None)
    lx, ly = button_center(window, "left")
    assert is_hovering((lx, ly), window, "left") and not is_hovering((cx, cy), window, "left")
