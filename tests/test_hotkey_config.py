from pathlib import Path

import pytest

from cadence.config import config_from_dict, load_config
from cadence.hotkey import HotkeyMatcher


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

    data = tomllib.loads(resources.files("cadence").joinpath("default_config.toml").read_text())
    config_from_dict(data)


def test_invalid_values_rejected():
    with pytest.raises(ValueError):
        config_from_dict({"cleanup": {"llm": "gpt"}})
    with pytest.raises(ValueError):
        config_from_dict({"hotkey": {"mode": "press"}})


def test_settings_file_lives_in_the_project_folder():
    from cadence.config import default_config_path, project_root

    root = project_root()
    assert root is not None and (root / "README.md").exists()
    assert default_config_path() == root / "settings.toml"


def test_every_option_is_in_the_settings_template():
    import dataclasses
    import tomllib
    from importlib import resources

    from cadence.config import Config

    data = tomllib.loads(resources.files("cadence").joinpath("default_config.toml").read_text())
    internal = {("audio", "sample_rate"), ("audio", "min_peak"), ("stt", "backend"),
                ("audio", "device")}  # device is shown commented out
    cfg = Config()
    for section in dataclasses.fields(cfg):
        for f in dataclasses.fields(getattr(cfg, section.name)):
            if (section.name, f.name) not in internal:
                assert f.name in data.get(section.name, {}), f"{section.name}.{f.name} missing"


def test_one_run_overrides():
    from cadence.config import Config, apply_overrides

    cfg = Config()
    changes = apply_overrides(cfg, ["cleanup.llm=claude", "stt.beam_size=3",
                                    "stt.pause_seconds=1", "ui.sounds=false",
                                    'hotkey.combo="ctrl+alt+space"', "hotkey.mode=hold"])
    assert cfg.cleanup.llm == "claude" and cfg.stt.beam_size == 3
    assert cfg.stt.pause_seconds == 1.0 and cfg.ui.sounds is False
    assert cfg.hotkey.combo == "ctrl+alt+space" and cfg.hotkey.mode == "hold"
    assert changes[0] == "cleanup.llm = 'claude'"


@pytest.mark.parametrize("bad", ["cleanup.llm=gpt", "nope.x=1", "cleanup.nope=1", "llm=claude",
                                 "stt.beam_size=many", "ui.sounds=yes", "cleanup.llm"])
def test_bad_overrides_are_rejected(bad):
    from cadence.config import Config, apply_overrides

    with pytest.raises(ValueError):
        apply_overrides(Config(), [bad])


def test_cli_override_does_not_touch_settings_file(tmp_path, capsys):
    from cadence.cli import main

    settings = tmp_path / "settings.toml"
    assert main(["--config", str(settings), "--llm", "none", "--set", "cleanup.remove_fillers=false",
                 "clean", "um hello"]) == 0
    assert capsys.readouterr().out.strip() == "Um hello"  # fillers kept: override applied
    assert "remove_fillers = true" in settings.read_text()  # file still has the default


def test_the_settings_file_on_github_is_valid():
    from cadence.config import load_config, project_root

    cfg = load_config(project_root() / "settings.toml")
    assert cfg.cleanup.llm in ("none", "claude", "local")


def test_a_mistake_in_settings_gives_a_clear_message(tmp_path, capsys):
    from cadence.cli import main

    bad = tmp_path / "settings.toml"
    bad.write_text('[cleanup]\nllm = "gpt"\n')
    with pytest.raises(SystemExit) as exit_info:
        main(["--config", str(bad), "clean", "hello"])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "there's a mistake in" in err and "cleanup.llm" in err

    bad.write_text("[cleanup\nllm = claude\n")  # broken TOML syntax
    with pytest.raises(SystemExit):
        main(["--config", str(bad), "clean", "hello"])
    assert "there's a mistake in" in capsys.readouterr().err


@pytest.mark.parametrize("remote", [
    "https://github.com/boyetteai24/cadence",
    "https://github.com/boyetteai24/cadence.git",
    "git@github.com:boyetteai24/cadence.git",
])
def test_github_edit_url(remote):
    from cadence.config import github_edit_url

    assert github_edit_url(remote) == "https://github.com/boyetteai24/cadence/edit/main/settings.toml"
    assert github_edit_url("https://gitlab.com/x/y") is None
