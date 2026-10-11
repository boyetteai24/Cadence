"""Command-line entry point.

    flow                     run the dictation app (tray + global hotkey)
    flow transcribe FILE     transcribe + clean an audio file, with timings
    flow clean "TEXT"        run only the cleanup stage on some text
    flow devices             list microphones
    flow init-config         write the default config file
    flow autostart on|off    start Flow when you sign in to Windows
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import time
from importlib import resources
from pathlib import Path

from flow.config import Config, config_dir, default_config_path, load_config


def setup_logging(verbose: bool) -> None:
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:  # None under pythonw.exe
        handlers.append(logging.StreamHandler())
    try:
        config_dir().mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                config_dir() / "flow.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
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
    path.write_text(resources.files("flow").joinpath("default_config.toml").read_text("utf-8"), "utf-8")


def _single_instance() -> bool:
    """Prevent two copies from double-pasting. Windows only."""
    if sys.platform != "win32":
        return True
    import ctypes

    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\FlowDictationSingleInstance")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def cmd_run(cfg: Config, args) -> int:
    if not _single_instance():
        logging.error("Flow is already running")
        return 1
    from flow.app import run

    run(cfg)
    return 0


def cmd_transcribe(cfg: Config, args) -> int:
    from faster_whisper import decode_audio

    from flow.cleanup import build_cleaner
    from flow.stt import build_transcriber

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
    from flow.cleanup import build_cleaner

    if args.no_llm:
        cfg.cleanup.llm = "none"
    print(build_cleaner(cfg).clean(args.text))
    return 0


def cmd_devices(cfg: Config, args) -> int:
    from flow.audio import list_input_devices

    print("\n".join(list_input_devices()))
    return 0


def cmd_init_config(cfg: Config, args) -> int:
    path = default_config_path()
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
        if args.state == "on":
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            exe = pythonw if pythonw.exists() else Path(sys.executable)
            winreg.SetValueEx(key, "Flow", 0, winreg.REG_SZ, f'"{exe}" -m flow')
            print("Flow will start when you sign in.")
        else:
            try:
                winreg.DeleteValue(key, "Flow")
            except FileNotFoundError:
                pass
            print("Autostart removed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flow", description="System-wide voice dictation.")
    parser.add_argument("--config", type=Path, help="config file (default: %(default)s)",
                        default=default_config_path())
    parser.add_argument("-v", "--verbose", action="store_true")
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
    p = sub.add_parser("init-config", help="write the default config file")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("autostart", help="start with Windows")
    p.add_argument("state", choices=["on", "off"])

    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    cfg = load_config(args.config)
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
