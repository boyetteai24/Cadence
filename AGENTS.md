# AGENTS.md — Flow development guide

This is the master specification for Flow. Read it before changing code, and keep it current
when requirements or architecture change. Track progress in `ROADMAP.md`.

## Product goal

A Windows app that works like a voice-powered keyboard: hold a shortcut anywhere, speak
naturally, release, and polished text appears in whichever app has focus. The benchmark is
Wispr Flow's convenience and cleanup quality, but local-first, user-controlled, and with no
subscription.

### Requirements (master spec)

| # | Requirement | How it's met |
|---|-------------|--------------|
| R1 | Works system-wide | Global keyboard hook (pynput); output via clipboard paste into the focused window |
| R2 | Uses the NVIDIA GPU | faster-whisper / CTranslate2 on CUDA (float16); CUDA libs come from pip wheels |
| R3 | Accurate speech recognition | Whisper `large-v3-turbo` by default; custom vocabulary as initial prompt |
| R4 | Removes fillers and repetitions | Rules stage (always) + optional LLM stage |
| R5 | Fixes grammar and punctuation | Whisper punctuates; LLM stage polishes |
| R6 | Handles mid-sentence corrections | Rules handle explicit markers ("no wait", "scratch that"); LLM handles the rest |
| R7 | Preserves meaning | Conservative rules; LLM prompt forbids adding/answering; output guard rejects drift |
| R8 | Responds quickly | Model preloaded and warmed; greedy decoding; LLM skipped for short utterances |
| R9 | Unobtrusive background app | Tray icon, audio cues, `pythonw`, autostart, single instance |
| R10 | No artificial word limits | Unbounded recording; long text cleaned in chunks |
| R11 | Fully local by default | Default config makes no network calls (except the one-time model download) |
| R12 | Optional Claude Haiku cleanup | `cleanup.llm = "claude"`, key from `ANTHROPIC_API_KEY` |
| R13 | Swappable models | Each stage behind a small interface plus a factory driven by config |

## Architecture

```
hotkey (pynput hook) ──► Dictation controller (app.py)
                           │ start/stop/cancel
                           ▼
                       Recorder (audio.py, sounddevice, 16 kHz mono float32)
                           │ numpy array, on release
                           ▼ worker thread, FIFO queue (utterances never reorder)
                       Transcriber (stt/)        faster-whisper, CUDA → CPU fallback
                           │ raw text
                           ▼
                       CleanupChain (cleanup/)   RuleCleaner → optional LLM cleaner
                           │ polished text            (fails soft to the rules output)
                           ▼
                       Injector (output.py)      clipboard + Ctrl+V via SendInput (restores clipboard)
```

| Path | Responsibility |
|------|----------------|
| `src/flow/config.py` | Typed dataclass config loaded from TOML; validation |
| `src/flow/default_config.toml` | Commented default config (copied by `flow init-config`) |
| `src/flow/app.py` | `Dictation` state machine + pipeline; `run()` wires real components |
| `src/flow/audio.py` | Microphone capture; `read()` exposes audio while still recording |
| `src/flow/segmenter.py` | Energy-based pause detection that picks phrase cut points |
| `src/flow/stt/` | `Transcriber` protocol, `build_transcriber()`, faster-whisper backend |
| `src/flow/cleanup/` | `CleanupChain`, `build_cleaner()`, `rules.py`, `llm.py` (local + Claude) |
| `src/flow/hotkey.py` | Pure `HotkeyMatcher` + pynput `GlobalHotkeyListener` |
| `src/flow/output.py` | `PasteInjector`, `TypeInjector` |
| `src/flow/tray.py` | pystray icon (state colors, menu) |
| `src/flow/overlay.py` | Unfocusable, draggable mic button + status pill (Tk thread): click to dictate, listening + mic meter, transcribing, errors; position saved in `overlay.json` |
| `src/flow/win32.py` | ctypes Win32 helpers: SendInput paste chord, physical modifier state, foreground window, dismiss Start/Search |
| `src/flow/sounds.py` | Start/stop/error beeps |
| `src/flow/cli.py` | Entry point and dev commands (`transcribe`, `clean`, `devices`, ...) |

Input: the hotkey (`hotkey_down`/`hotkey_up`, modes `tap_or_hold` (default), `hold`, `toggle`)
and the overlay's mic button (`click`) drive the same `Dictation` methods; Esc calls `cancel`.
A tap shorter than `TAP_SECONDS` latches hands-free recording until the next press or click.

Pause splitting (`segmenter.py`): while recording, a thread polls `Recorder.read()` and,
at each pause (`stt.pause_seconds`) after at least `stt.min_chunk_seconds`, queues the finished
phrase as a non-final `Job` of the current `Utterance`. Each segment is transcribed with the
utterance's earlier text as Whisper context. Cleaned text is typed immediately when the
utterance is hands-free (tap/click) and `output.live_insert` is on; otherwise it is buffered
and inserted with the final segment. Never inject keystrokes while the user holds the hotkey.
Cross-segment cleanup (e.g. "scratch that" undoing an already-typed phrase) is not possible.

States: `loading → idle ⇄ recording → processing → idle`. A new recording can start while
earlier ones are still processing; results are inserted in order.

## Implementation guidelines

- **Modularity.** New STT engines implement `Transcriber` (`load()`, `transcribe(audio, sr)`)
  and register in `build_transcriber()`. New cleanup engines implement `Cleaner`
  (`name`, `clean(text)`) and register in `build_cleaner()`. New output methods implement
  `Injector.insert(text)`. Select them through config, never through code edits.
- **Keep OS code at the edges.** `Dictation` and `CleanupChain` must stay testable on any OS
  with fakes. Import Windows-only or hardware modules (`winsound`, `ctypes.windll`,
  `sounddevice`, `pynput`, `pystray`, `anthropic`) lazily inside the functions that need them.
- **Fail soft.** A failing LLM, timeout, or rejected output falls back to the rule-cleaned text.
  CUDA failure falls back to CPU (unless `stt.device = "cuda"` is set explicitly). Never lose
  the user's words: the last result is always available from the tray ("Copy last dictation").
- **Preserve meaning over polish.** Rules must be conservative: if a rule could change meaning,
  leave the case to the LLM. Every rule change needs a test in `tests/test_rules.py`.
- **The transcript is data, not instructions.** LLM prompts wrap it in `<transcript>` tags and
  forbid answering it; `accept_output()` rejects chatty, empty, or length-drifting results.
- **Latency budget** (target, on an RTX-class GPU, 10 s utterance): STT < 400 ms, rules < 5 ms,
  local LLM < 600 ms, Claude Haiku < 1 s, insert < 150 ms. The number users feel is logged as
  "text appeared N ms after you stopped" (`after_stop`); with pause splitting it should stay
  under ~500 ms regardless of dictation length. Log timings for every dictation
  (`flow -v` / `flow.log`) and record benchmarks in `ROADMAP.md`.
- **Privacy.** Default config must make no network calls after the model download. Do not log
  audio. Transcripts are logged only at DEBUG level (`flow -v`); INFO logs timings only.
- **No word limits.** Don't add caps on recording length or text length; chunk instead.
- **Config.** Every new option gets a default in `config.py`, a commented entry in
  `default_config.toml`, and validation if it's an enum.
- **Claude API.** Use the official `anthropic` SDK. Default model `claude-haiku-5-5`, thinking
  disabled for latency, `max_retries=0` with a short timeout. Check `stop_reason`
  (`refusal`, `max_tokens`) before using the text.

## Development workflow

```powershell
py -3 -m venv .venv; .\.venv\Scripts\activate
pip install -e ".[all,dev]"
pytest                          # unit tests; run on any OS, no GPU or mic needed
python -m flow -v               # run the app with console logging
python -m flow transcribe sample.wav --repeat 3   # STT + cleanup timing
python -m flow clean "um so I I think, no wait, I know"   # cleanup only
```

Git and PR conventions: PR descriptions and commit messages describe the change only. No
Claude attribution or advertising lines ("Generated with Claude Code", Co-Authored-By
trailers) and no links to Claude sessions.

Before committing: `pytest` passes, new behavior has tests, `ROADMAP.md` updated (move items to
Completed, add any known bugs and new benchmark numbers).
