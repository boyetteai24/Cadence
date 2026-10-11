"""Configuration: typed dataclasses loaded from `settings.toml`.

The settings file lives in the project folder (next to README.md) when Cadence runs from a
source checkout. It is tracked in git: the owner edits it on GitHub and pulls. Installed
without a checkout, it lives in %APPDATA%\\Cadence (Windows) or ~/.config/cadence. If it's
missing, it is recreated from default_config.toml. Every field has a default, so missing
lines are fine.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class HotkeyConfig:
    # Keys joined by "+". Names: ctrl, ctrl_l, ctrl_r, alt, alt_l, alt_r, shift, shift_l,
    # shift_r, win, space, tab, caps_lock, esc, f1..f24, or a single character.
    combo: str = "ctrl+win"
    # "tap_or_hold": hold to talk, or tap to start hands-free and tap again to stop.
    # "hold": record only while the combo is held (push-to-talk).
    # "toggle": press once to start, again to stop.
    mode: str = "tap_or_hold"


@dataclass
class AudioConfig:
    device: str | int | None = None  # None = system default input
    sample_rate: int = 16000
    # Keep the input stream open between dictations: removes ~50-150 ms of start-up
    # latency and avoids clipping the first word, but Windows shows the mic as in use.
    keep_stream_open: bool = False
    min_seconds: float = 0.3  # shorter recordings are treated as accidental taps
    min_peak: float = 0.01  # recordings quieter than this are treated as silence


@dataclass
class SttConfig:
    backend: str = "faster-whisper"
    model: str = "large-v3-turbo"  # any faster-whisper model name or local path
    device: str = "auto"  # auto | cuda | cpu
    compute_type: str = "auto"  # auto | float16 | int8_float16 | int8 | float32
    language: str | None = "en"  # None = auto-detect (slightly slower)
    beam_size: int = 5  # 5 = more accurate; 1 = greedy (fastest)
    vad_filter: bool = True  # skip silent stretches; also curbs hallucinations
    # Transcribe while you talk, so little is left to do when you stop:
    # "pauses": each phrase at the next pause (whole phrases: most accurate).
    # "words":  each word once it's `lag_words` words behind you, and at every pause.
    #           (With live typing and an LLM on, "pauses" is used: the LLM needs phrases.)
    # "off":    everything after you stop.
    # Unless output.live_insert is on, nothing is typed until you stop, and then the whole
    # dictation is cleaned up in one go.
    live_mode: str = "pauses"
    lag_words: int = 5  # how far behind your voice the typing stays in "words" mode
    pass_interval: float = 0.4  # seconds between re-transcription passes in "words" mode
    pause_seconds: float = 0.6  # how long a pause ends a phrase
    min_chunk_seconds: float = 2.0  # "pauses" mode: don't split off phrases shorter than this


@dataclass
class CleanupConfig:
    remove_fillers: bool = True
    voice_commands: bool = True  # "new line", "new paragraph"
    # LLM polish stage: "claude" (Claude Haiku; only active when ANTHROPIC_API_KEY is set,
    # otherwise rules only), "local" (OpenAI-compatible server such as Ollama / llama.cpp /
    # LM Studio), or "none" (rules only).
    llm: str = "claude"
    min_words_for_llm: int = 4  # very short utterances skip the LLM
    timeout_s: float = 10.0
    local_url: str = "http://localhost:11434/v1"
    local_model: str = "qwen2.5:3b-instruct"
    claude_model: str = "claude-haiku-5-5"  # API key: ANTHROPIC_API_KEY environment variable only
    chunk_words: int = 600  # long dictations are cleaned in chunks of about this size


@dataclass
class OutputConfig:
    method: str = "paste"  # "paste" (clipboard + Ctrl+V) or "type" (synthetic keystrokes)
    paste_keys: str = "ctrl+v"  # e.g. "ctrl+shift+v" for some terminals
    # False: your dictation stays on the clipboard, so if it didn't land in a text box you
    # can just press Ctrl+V. True: whatever you had copied before is put back.
    restore_clipboard: bool = False
    paste_delay_ms: int = 400  # wait before restoring the clipboard (runs in background)
    trailing_space: bool = True  # so consecutive dictations don't run together
    # False (default): nothing is typed until you stop, then the whole dictation is cleaned
    # up and pasted at once (best cleanup). True: when hands-free (tap or click), type each
    # part as soon as it's ready, while you keep talking. Holding the hotkey always waits.
    live_insert: bool = False


@dataclass
class UiConfig:
    sounds: bool = True
    tray: bool = True
    overlay: bool = True  # status pill near the bottom of the screen
    button: bool = True  # clickable mic button in the overlay (needs overlay = true)


@dataclass
class DictionaryConfig:
    # Words/names Whisper should prefer (passed as an initial prompt).
    words: list[str] = field(default_factory=list)
    # Case-insensitive whole-phrase replacements applied after transcription.
    replacements: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    dictionary: DictionaryConfig = field(default_factory=DictionaryConfig)


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Cadence"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cadence"


def project_root() -> Path | None:
    """The source checkout this package runs from (where pyproject.toml is), if any."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").is_file() else None


def github_edit_url(remote_url: str, branch: str = "main") -> str | None:
    """'https://github.com/owner/repo(.git)' or 'git@github.com:owner/repo.git' ->
    the GitHub page for editing settings.toml on `branch`."""
    m = re.match(r"(?:https://|git@)github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?/?$", remote_url.strip())
    return f"https://github.com/{m[1]}/{m[2]}/edit/{branch}/settings.toml" if m else None


def settings_edit_url() -> str | None:
    """Where to edit settings.toml on GitHub, from this checkout's `origin` remote."""
    import subprocess

    root = project_root()
    if root is None:
        return None
    try:
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"], cwd=root, capture_output=True,
            text=True, timeout=5, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return github_edit_url(remote)


def default_config_path() -> Path:
    root = project_root()
    return (root if root else config_dir()) / "settings.toml"


def _apply(obj: Any, data: dict[str, Any], prefix: str = "") -> None:
    fields = {f.name: f for f in dataclasses.fields(obj)}
    for key, value in data.items():
        if key not in fields:
            log.warning("Unknown config key ignored: %s%s", prefix, key)
            continue
        current = getattr(obj, key)
        if dataclasses.is_dataclass(current):
            if not isinstance(value, dict):
                raise ValueError(f"Config section [{prefix}{key}] must be a table")
            _apply(current, value, f"{prefix}{key}.")
        else:
            setattr(obj, key, value)


def _parse_value(raw: str) -> Any:
    """TOML syntax when it parses (5, 0.4, true, "text", ["a"]), else a plain string, so
    `--set cleanup.llm=claude` works without quotes."""
    try:
        return tomllib.loads(f"v = {raw}")["v"]
    except tomllib.TOMLDecodeError:
        return raw


def apply_overrides(cfg: Config, overrides: list[str]) -> list[str]:
    """Apply "section.key=value" settings for this run only (settings.toml is unchanged).
    Returns a description of each change; raises ValueError with a helpful message."""
    applied = []
    for item in overrides:
        key, sep, raw = item.partition("=")
        key = key.strip()
        section_name, dot, name = key.partition(".")
        section = getattr(cfg, section_name, None) if dot else None
        if not sep or section is None or not dataclasses.is_dataclass(section) or not any(
            f.name == name for f in dataclasses.fields(section)
        ):
            raise ValueError(
                f"Unknown setting {item!r}. Use section.name=value with the names from "
                f"settings.toml, e.g. cleanup.llm=claude or stt.lag_words=3"
            )
        value = _parse_value(raw.strip())
        current = getattr(section, name)
        if isinstance(current, float) and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if current is not None and type(value) is not type(current):
            raise ValueError(
                f"{key} expects {type(current).__name__} (like {current!r}), got {raw.strip()!r}"
            )
        setattr(section, name, value)
        applied.append(f"{key} = {value!r}")
    validate(cfg)
    return applied


def config_from_dict(data: dict[str, Any]) -> Config:
    cfg = Config()
    _apply(cfg, data)
    validate(cfg)
    return cfg


def load_config(path: Path | None = None) -> Config:
    path = path or default_config_path()
    if not path.exists():
        log.info("No config at %s; using defaults", path)
        return Config()
    with path.open("rb") as f:
        return config_from_dict(tomllib.load(f))


def validate(cfg: Config) -> None:
    if cfg.hotkey.mode not in ("tap_or_hold", "hold", "toggle"):
        raise ValueError("hotkey.mode must be 'tap_or_hold', 'hold' or 'toggle'")
    if cfg.stt.live_mode not in ("words", "pauses", "off"):
        raise ValueError("stt.live_mode must be 'words', 'pauses' or 'off'")
    if cfg.cleanup.llm not in ("none", "local", "claude"):
        raise ValueError("cleanup.llm must be 'none', 'local' or 'claude'")
    if cfg.output.method not in ("paste", "type"):
        raise ValueError("output.method must be 'paste' or 'type'")
    if cfg.stt.device not in ("auto", "cuda", "cpu"):
        raise ValueError("stt.device must be 'auto', 'cuda' or 'cpu'")
    if cfg.stt.language == "":
        cfg.stt.language = None
