from cadence.app import IDLE, LOADING, PROCESSING, RECORDING
from cadence.overlay import ERROR_SECONDS, READY_SECONDS, View, compute_view, frame_key
from cadence.overlay_render import Layout, pill_extent, premultiplied_bgra, render, render_marker

L = Layout()
WIDTH, HEIGHT = L.width, L.height
button_center, content_x, is_hovering = L.button_center, L.content_x, L.is_hovering
nearest_preset, preset_positions = L.nearest_preset, L.preset_positions


def view(state, now=100.0, ready_at=None, error=None):
    return compute_view(state, now, ready_at, error, "Ctrl + Win")


def test_states_map_to_views():
    assert view(LOADING).text == "Cadence is loading…"
    assert view(RECORDING).kind == "listening" and view(RECORDING).text == "Live"
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


def test_scaled_layout_grows_with_display_scaling():
    big = Layout(1.5)
    assert big.width == round(WIDTH * 1.5) and big.height == round(HEIGHT * 1.5)
    assert big.button[0] > L.button[0]
    spots = big.preset_positions(WORK)
    assert big.button_center(spots["bottom"], "bottom")[0] == 960
    assert spots["bottom"][1] + big.height <= 1032


def test_on_button_is_the_drawn_button_only():
    window = (100, 200)
    cx, cy = button_center(window, None)
    assert L.on_button((cx, cy), window, None)
    assert not L.on_button((cx + L.button[0], cy), window, None)
    assert is_hovering((cx + L.button[0] // 2 + 4, cy), window, None)  # hover is a bit wider


def test_render_is_smooth_and_see_through():
    for scale in (1.0, 1.5):
        lay = Layout(scale)
        for kind, text in [("bar", ""), ("button", ""), ("listening", "Listening"),
                           ("working", "Transcribing"), ("message", "Cadence ready"),
                           ("feedback_prompt", "Pasted · Feedback")]:
            img = render(kind, text, "#f4f4f5", lay, "bottom", [0.1] * 18, now=1.0)
            assert img.size == (lay.width, lay.height) and img.mode == "RGBA"
            hist = img.getchannel("A").histogram()
            alphas = {a for a, count in enumerate(hist) if count}
            assert 0 in alphas  # transparent around it
            assert any(0 < a < 255 for a in alphas)  # anti-aliased / see-through edges
        assert render_marker(lay, "left", True).size == (lay.width, lay.height)


def test_button_is_drawn_where_the_layout_says():
    img = render("button", "", "", L, "left")
    alpha = img.getchannel("A")
    cy = L.height // 2
    assert alpha.getpixel((L.content_x("left"), cy)) > 150
    assert alpha.getpixel((L.width - 10, cy)) == 0


def test_premultiplied_bgra():
    from PIL import Image

    img = Image.new("RGBA", (1, 1), (200, 100, 50, 128))
    assert premultiplied_bgra(img) == bytes([25, 50, 100, 128])


def test_frames_are_redrawn_only_when_something_changes():
    bar = View("bar")
    assert frame_key(bar, "bottom", [0.1], 1.0) == frame_key(bar, "bottom", [0.5], 9.0)
    assert frame_key(bar, "bottom", [], 1.0) != frame_key(bar, "left", [], 1.0)
    listening = View("listening", "Listening")
    assert frame_key(listening, None, [0.1], 1.0) != frame_key(listening, None, [0.2], 1.0)


def test_thin_pill_and_button():
    assert L.height <= 34 and L.button[1] <= 22 and L.bar[1] <= 6
    img = render("listening", "Live", "", L, "bottom", [0.1] * 18, now=1.0)
    column = [img.getpixel((L.width // 2, y))[3] for y in range(L.height)]
    opaque = [y for y, a in enumerate(column) if a > 150]
    assert opaque and opaque[-1] - opaque[0] <= 28  # the pill itself is thin


def test_pill_is_sized_to_what_it_shows():
    live = pill_extent(L, "listening", "Live", "bottom")
    ready = pill_extent(L, "message", "Cadence ready · Ctrl + Win or click", "bottom")
    assert live[1] - live[0] < ready[1] - ready[0] <= L.width
    assert live[0] + live[1] in (L.width - 1, L.width, L.width + 1)  # centered
    left, right = pill_extent(L, "listening", "Live", "left"), pill_extent(L, "listening", "Live", "right")
    assert left[0] < 5 and right[1] > L.width - 5
    img = render("listening", "Live", "", L, "left", [0.1] * 18, now=1.0)
    assert img.getpixel((L.width - 5, L.height // 2))[3] == 0  # nothing past the pill
