from pathlib import Path

import pytest

from flow.config import config_from_dict, load_config
from flow.hotkey import HotkeyMatcher


def test_hold_combo_either_side():
    m = HotkeyMatcher("ctrl+win")
    assert m.press("ctrl_r") is None
    assert m.press("win_l") == "activate"
    assert m.press("win_l") is None  # key auto-repeat
    assert m.release("ctrl_r") == "deactivate"
    assert m.release("win_l") is None


def test_specific_side():
    m = HotkeyMatcher("ctrl_r")
    assert m.press("ctrl_l") is None
    assert m.press("ctrl_r") == "activate"
    assert m.release("ctrl_r") == "deactivate"


def test_aliases_and_chars():
    m = HotkeyMatcher("Control+Alt+Space")
    m.press("ctrl_l")
    m.press("alt_r")
    assert m.press("space") == "activate"
    m2 = HotkeyMatcher("ctrl+shift+d")
    m2.press("ctrl_l")
    m2.press("shift_r")
    assert m2.press("d") == "activate"
    assert m2.uses_win is False


def test_config_defaults_and_overrides(tmp_path: Path):
    assert load_config(tmp_path / "missing.toml").stt.model == "large-v3-turbo"
    p = tmp_path / "c.toml"
    p.write_text('[hotkey]\nmode = "toggle"\n[cleanup]\nllm = "claude"\n'
                 '[dictionary.replacements]\n"foo bar" = "FooBar"\n')
    cfg = load_config(p)
    assert cfg.hotkey.mode == "toggle"
    assert cfg.cleanup.llm == "claude"
    assert cfg.dictionary.replacements == {"foo bar": "FooBar"}


def test_default_config_file_is_valid():
    import tomllib
    from importlib import resources

    data = tomllib.loads(resources.files("flow").joinpath("default_config.toml").read_text())
    config_from_dict(data)


def test_invalid_values_rejected():
    with pytest.raises(ValueError):
        config_from_dict({"cleanup": {"llm": "gpt"}})
    with pytest.raises(ValueError):
        config_from_dict({"hotkey": {"mode": "press"}})
