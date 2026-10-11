"""Command-line entry point.

    cadence                     run the dictation app (tray + global hotkey)
    cadence transcribe FILE     transcribe + clean an audio file, with timings
    cadence clean "TEXT"        run only the cleanup stage on some text
    cadence devices             list microphones
    cadence init-config         write the default settings.toml
    cadence autostart on|off    start Cadence when you sign in to Windows

Any command also takes one-run overrides that leave settings.toml unchanged:
    cadence --llm claude
    cadence --set stt.lag_words=3 --set hotkey.mode=hold
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import time
from importlib import resources
from pathlib import Path

from cadence.config import Config, apply_overrides, config_dir, default_config_path, load_config


def setup_logging(verbose: bool) -> None:
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:  # None under pythonw.exe
        handlers.append(logging.StreamHandler())
    try:
        config_dir().mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                config_dir() / "cadence.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
            )
        )
    except OSError:
        pass
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def write_default_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(resources.files("cadence").joinpath("default_config.toml").read_text("utf-8"), "utf-8")


def _single_instance() -> bool:
    """Prevent two copies from double-pasting. Windows only."""
    if sys.platform != "win32":
        return True
    import ctypes

    # Same name as before the rename from "Flow", so an old copy and Cadence can't both run.
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\FlowDictationSingleInstance")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def cmd_run(cfg: Config, args) -> int:
    if not _single_instance():
        logging.error("Cadence is already running")
        return 1
    from cadence.app import run

    run(cfg)
    return 0


def cmd_transcribe(cfg: Config, args) -> int:
    from faster_whisper import decode_audio

    from cadence.cleanup import build_cleaner
    from cadence.stt import build_transcriber

    if args.no_llm:
        cfg.cleanup.llm = "none"
    t0 = time.perf_counter()
    transcriber = build_transcriber(cfg)
    transcriber.load()
    print(f"model load: {time.perf_counter() - t0:.2f}s", file=sys.stderr)
    audio = decode_audio(args.file, sampling_rate=16000)
    cleaner = build_cleaner(cfg)
    for i in range(args.repeat):
        t0 = time.perf_counter()
        tr = transcriber.transcribe(audio)
        t_stt = time.perf_counter() - t0
        t0 = time.perf_counter()
        text = cleaner.clean(tr.text)
        t_clean = time.perf_counter() - t0
        print(
            f"[run {i + 1}] audio {tr.audio_seconds:.1f}s | stt {t_stt * 1000:.0f}ms "
            f"(RTF {t_stt / max(tr.audio_seconds, 1e-6):.3f}) | cleanup {t_clean * 1000:.0f}ms",
            file=sys.stderr,
        )
    print("RAW:  ", tr.text)
    print("CLEAN:", text)
    return 0


def cmd_clean(cfg: Config, args) -> int:
    from cadence.cleanup import build_cleaner

    if args.no_llm:
        cfg.cleanup.llm = "none"
    chain = build_cleaner(cfg)
    print(chain.clean(args.text))
    print(describe_llm(cfg, chain), file=sys.stderr)
    return 0


def describe_llm(cfg: Config, chain) -> str:
    """One line saying whether the AI cleanup actually ran, and if not, why."""
    import os

    llm = cfg.cleanup.llm
    if llm == "none":
        return "[AI cleanup: off (llm = \"none\"); built-in rules only]"
    if chain.llm is None and llm == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        return ("[AI cleanup: Claude is OFF because ANTHROPIC_API_KEY isn't set in this "
                "PowerShell window. Set it (COMMANDS.md, 'Claude API key'), then open a new window]")
    status = chain.last_llm_status
    ms = chain.last_timings.get(chain.llm.name, 0) * 1000 if chain.llm else 0
    if status == "used":
        return f"[AI cleanup: {llm} worked ({ms:.0f} ms)]"
    return f"[AI cleanup: {llm} {status}; showing built-in rules output]"


def cmd_devices(cfg: Config, args) -> int:
    from cadence.audio import list_input_devices

    print("\n".join(list_input_devices()))
    return 0


def cmd_init_config(cfg: Config, args) -> int:
    path = args.config
    if path.exists() and not args.force:
        print(f"{path} already exists (use --force to overwrite)")
        return 1
    write_default_config(path)
    print(f"Wrote {path}")
    return 0


def cmd_autostart(cfg: Config, args) -> int:
    if sys.platform != "win32":
        print("autostart is only supported on Windows")
        return 1
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        try:
            winreg.DeleteValue(key, "Flow")  # entry from before the rename
        except FileNotFoundError:
            pass
        if args.state == "on":
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            exe = pythonw if pythonw.exists() else Path(sys.executable)
            winreg.SetValueEx(key, "Cadence", 0, winreg.REG_SZ, f'"{exe}" -m cadence')
            print("Cadence will start when you sign in.")
        else:
            try:
                winreg.DeleteValue(key, "Cadence")
            except FileNotFoundError:
                pass
            print("Autostart removed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cadence", description="System-wide voice dictation.")
    parser.add_argument("--config", type=Path, help="settings file (default: %(default)s)",
                        default=default_config_path())
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--set", action="append", default=[], metavar="SECTION.NAME=VALUE",
        help="change a setting for this run only, e.g. --set stt.lag_words=3 (repeatable; "
             "settings.toml is not changed)",
    )
    parser.add_argument(
        "--llm", choices=["none", "claude", "local"],
        help="AI cleanup for this run only (same as --set cleanup.llm=...)",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="run the dictation app (default)")
    p = sub.add_parser("transcribe", help="transcribe an audio file and print timings")
    p.add_argument("file")
    p.add_argument("--repeat", type=int, default=1, help="repeat to measure warm latency")
    p.add_argument("--no-llm", action="store_true")
    p = sub.add_parser("clean", help="clean up a piece of text")
    p.add_argument("text")
    p.add_argument("--no-llm", action="store_true")
    sub.add_parser("devices", help="list input devices")
    p = sub.add_parser("init-config", help="write the default settings.toml")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("autostart", help="start with Windows")
    p.add_argument("state", choices=["on", "off"])

    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    if args.command != "init-config" and not args.config.exists():
        write_default_config(args.config)
        logging.info("Created settings file %s", args.config)
    logging.info("Settings: %s", args.config)
    try:
        cfg = load_config(args.config)
    except Exception as e:  # TOML syntax error or an invalid value
        parser.exit(2, f"cadence: there's a mistake in {args.config}:\n  {e}\n"
                       "Fix that line (or undo your last change on GitHub), then try again.\n")
    overrides = args.set + ([f"cleanup.llm={args.llm}"] if args.llm else [])
    try:
        for change in apply_overrides(cfg, overrides):
            logging.info("This run only: %s", change)
    except ValueError as e:
        parser.error(str(e))
    handlers = {
        None: cmd_run,
        "run": cmd_run,
        "transcribe": cmd_transcribe,
        "clean": cmd_clean,
        "devices": cmd_devices,
        "init-config": cmd_init_config,
        "autostart": cmd_autostart,
    }
    return handlers[args.command](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
