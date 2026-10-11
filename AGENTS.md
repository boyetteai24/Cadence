# AGENTS.md — Cadence development guide

This is the master specification for Cadence. Read it before changing code, and keep it current
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
| `src/cadence/config.py` | Typed dataclass config loaded from TOML; validation |
| `src/cadence/default_config.toml` | Commented template for the user's `settings.toml` (copied on first run) |
| `settings.toml` (project root, git-ignored) | The user's settings; `config.default_config_path()` |
| `src/cadence/app.py` | `Dictation` state machine + pipeline; `run()` wires real components |
| `src/cadence/audio.py` | Microphone capture; `read()` exposes audio while still recording |
| `src/cadence/segmenter.py` | Energy-based pause detection: phrase cut points, trailing silence |
| `src/cadence/streaming.py` | Local-agreement word commit logic for live word-by-word typing |
| `src/cadence/stt/` | `Transcriber` protocol, `build_transcriber()`, faster-whisper backend |
| `src/cadence/cleanup/` | `CleanupChain`, `build_cleaner()`, `rules.py`, `llm.py` (local + Claude) |
| `src/cadence/hotkey.py` | Pure `HotkeyMatcher` + pynput `GlobalHotkeyListener` |
| `src/cadence/output.py` | `PasteInjector`, `TypeInjector` |
| `src/cadence/tray.py` | pystray icon (state colors, menu) |
| `src/cadence/overlay.py` | Unfocusable mic button (left-click dictates, right-drag moves) + status pill (Tk thread): click to dictate, listening + mic meter, transcribing, errors; position saved in `overlay.json` |
| `src/cadence/win32.py` | ctypes Win32 helpers: SendInput paste chord, physical modifier state, foreground window, dismiss Start/Search |
| `src/cadence/sounds.py` | Start/stop/error beeps |
| `src/cadence/cli.py` | Entry point and dev commands (`transcribe`, `clean`, `devices`, ...) |

Input: the hotkey (`hotkey_down`/`hotkey_up`, modes `tap_or_hold` (default), `hold`, `toggle`)
and the overlay's mic button (`click`) drive the same `Dictation` methods; Esc calls `cancel`.
A tap shorter than `TAP_SECONDS` latches hands-free recording until the next press or click.

Live typing (`stt.live_mode`) runs a thread per recording that hands parts of the audio to
the worker as non-final `Job`s of the current `Utterance`, advancing `Dictation._cut`:
- `"words"` (default, `streaming.py`): every `pass_interval` the not-yet-committed audio is
  re-transcribed with word timestamps. Words that are `lag_words` behind the newest word and
  identical in two consecutive passes are committed as an already-transcribed `Job(text=...)`.
  At a pause (`pause_seconds`), everything is committed. Audio is cut halfway into the gap
  after the last committed word. Falls back to `"pauses"` when `cleanup.llm` is on.
- `"pauses"` (`segmenter.py`): at each pause after `min_chunk_seconds`, the phrase is queued
  as audio and transcribed by the worker.
Each part is transcribed with the utterance's earlier text as Whisper context, and cleaned
with `continuation=True` when it continues an unfinished sentence (no forced capital).
Cleaned text is typed immediately when the utterance is hands-free (tap/click) and
`output.live_insert` is on; otherwise it is buffered and inserted with the final job, which
covers all audio after the last cut. Never inject keystrokes while the user holds the
hotkey. Cleanup can't reach back into text that was already typed.

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
  (`cadence -v` / `cadence.log`) and record benchmarks in `ROADMAP.md`.
- **Privacy.** Default config must make no network calls after the model download. Do not log
  audio. Transcripts are logged only at DEBUG level (`cadence -v`); INFO logs timings only.
- **No word limits.** Don't add caps on recording length or text length; chunk instead.
- **Config.** Every new option gets a default in `config.py`, a plain-language commented entry
  in `default_config.toml` (a test checks that every option appears there), and validation if
  it's an enum. Users edit `settings.toml` in the project root, created from that template on
  first run and git-ignored. `--set section.name=value` / `--llm` override any option for
  one run (`config.apply_overrides`, type-checked against the defaults); never write them back. Secrets (the Claude API key) are never read from settings
  files, only from environment variables (`ANTHROPIC_API_KEY`); this repo is public.
- **Claude API.** Use the official `anthropic` SDK. Default model `claude-haiku-5-5`, thinking
  disabled for latency, `max_retries=0` with a short timeout. Check `stop_reason`
  (`refusal`, `max_tokens`) before using the text.

## Development workflow

```powershell
py -3 -m venv .venv; .\.venv\Scripts\activate
pip install -e ".[all,dev]"
pytest                          # unit tests; run on any OS, no GPU or mic needed
python -m cadence -v               # run the app with console logging
python -m cadence transcribe sample.wav --repeat 3   # STT + cleanup timing
python -m cadence clean "um so I I think, no wait, I know"   # cleanup only
```

`COMMANDS.md` is the user's copy-paste PowerShell cheat sheet. The user isn't familiar with
PowerShell and runs commands exactly as written, so whenever a command, flag, path or setup
step changes, update `COMMANDS.md` in the same change. Mark one-time steps, and say in the
update notes (PR description) when a reinstall is needed.

Git and PR conventions: PR descriptions and commit messages describe the change only. No
Claude attribution or advertising lines ("Generated with Claude Code", Co-Authored-By
trailers) and no links to Claude sessions.

Before committing: `pytest` passes, new behavior has tests, `ROADMAP.md` updated (move items to
Completed, add any known bugs and new benchmark numbers).
