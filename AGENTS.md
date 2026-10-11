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
| R11 | Local unless the user opts in | No network calls (except the one-time model download) unless `ANTHROPIC_API_KEY` is set |
| R12 | Claude Haiku cleanup (owner's default) | `cleanup.llm = "claude"` is the default but only active when `ANTHROPIC_API_KEY` is set; otherwise rules only, nothing sent |
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
| `settings.toml` (project root, tracked, public) | The user's settings, edited on GitHub then pulled; `config.default_config_path()` |
| `src/cadence/default_config.toml` | Commented template: used to recreate `settings.toml` if it's deleted |
| `src/cadence/app.py` | `Dictation` state machine + pipeline; `run()` wires real components |
| `src/cadence/audio.py` | Microphone capture; `read()` exposes audio while still recording |
| `src/cadence/segmenter.py` | Energy-based pause detection: phrase cut points, trailing silence |
| `src/cadence/stt/` | `Transcriber` protocol, `build_transcriber()`, faster-whisper backend |
| `src/cadence/cleanup/` | `CleanupChain`, `build_cleaner()`, `rules.py`, `llm.py` (local + Claude) |
| `src/cadence/hotkey.py` | Pure `HotkeyMatcher` + pynput `GlobalHotkeyListener` |
| `src/cadence/output.py` | `PasteInjector`, `TypeInjector` |
| `src/cadence/tray.py` | pystray icon (state colors, menu) |
| `src/cadence/overlay.py` | Unfocusable mic button (left-click dictates, right-drag moves) + status pill (Tk thread): click to dictate, listening + mic meter, transcribing, errors; position saved in `overlay.json` |
| `src/cadence/win32.py` | ctypes Win32 helpers: SendInput paste chord, physical modifier state, foreground window, dismiss Start/Search |
| `src/cadence/feedback.py` | `FeedbackStore` (every review, JSON lines in the private settings folder, never in the repo), lessons for the LLM prompt, review dump |
| `src/cadence/feedback_window.py` | Tk feedback window (heard / typed / corrected / notes); takes focus, unlike the overlay |
| `src/cadence/sounds.py` | Start/stop/error beeps |
| `src/cadence/cli.py` | Entry point and dev commands (`transcribe`, `clean`, `devices`, ...) |

Input: the hotkey (`hotkey_down`/`hotkey_up`, modes `tap_or_hold` (default), `hold`, `toggle`)
and the overlay's mic button (`click`) drive the same `Dictation` methods; Esc calls `cancel`.
A tap shorter than `TAP_SECONDS` latches hands-free recording until the next press or click.

Nothing is typed while the user is talking; the whole dictation is typed once, when they
stop (owner's decision: no live typing).

Background transcription (`stt.background`, `segmenter.py`): while recording, a thread polls
`Recorder.read()` and, at each pause (`pause_seconds`) after at least `min_chunk_seconds`,
queues the finished phrase as a non-final `Job` of the current `Utterance`, advancing
`Dictation._cut`. The worker transcribes it with the utterance's earlier text as Whisper
context and adds the raw text to `Utterance.pending`. The final job covers all audio after
the last cut; then all pending text is cleaned in one pass (rules + LLM), so repeats and
corrections that span a pause get fixed and the LLM sees everything, and typed once. This
keeps the wait after the user stops to roughly the last phrase. With
`restore_clipboard = false` (default) the dictation stays on the clipboard, so text that
missed a text box can be pasted.

Feedback: after each paste, `Dictation.result_listeners` fire; the overlay shows "Pasted ·
Feedback" for `feedback.prompt_seconds`. Clicking it, the `feedback.hotkey` (second
`GlobalHotkeyListener`) or the tray item opens `FeedbackWindow` on the overlay's Tk thread
(`Overlay.request_feedback` is thread-safe). Saved reviews go to `FeedbackStore`. With
`feedback.teach_ai`, `build_cleaner` gives the LLM cleaner an `extra_instructions` callable
that re-reads the last `teach_count` reviews on every request and appends them to the system
prompt (`feedback.lessons_for_prompt`). `cadence feedback --copy` exports reviews for the
owner to paste into a session; turn recurring problems into rules, tests, dictionary words or
prompt changes, and log them in ROADMAP's feedback log.

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
  "text appeared N ms after you stopped" (`after_stop`); with background transcription it should stay
  under ~500 ms regardless of dictation length. Log timings for every dictation
  (`cadence -v` / `cadence.log`) and record benchmarks in `ROADMAP.md`.
- **Privacy.** Without `ANTHROPIC_API_KEY` set, the default config must make no network calls
  after the model download. With it, only transcribed text (never audio) goes to Anthropic. Do not log
  audio. Transcripts are logged only at DEBUG level (`cadence -v`); INFO logs timings only.
- **No word limits.** Don't add caps on recording length or text length; chunk instead.
- **Config.** Every new option gets a default in `config.py`, a plain-language commented entry
  in `default_config.toml` (a test checks that every option appears there), and validation if
  it's an enum. When adding an option, also add it to the tracked `settings.toml` in the root,
  which the user edits on GitHub and pulls (they don't edit it locally). It is public: never
  put secrets or personal details in it. When a default changes, update both files; the
  user's `settings.toml` is theirs, so change its values only when they ask. `--set section.name=value` / `--llm` override any option for
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
trailers) and no links to Claude sessions. The owner wants PRs merged into `main`
automatically: once `pytest` passes and the PR is open, merge it yourself (merge commit), then
reset the working branch from `origin/main` before the next change. Tell the owner it's merged
and whether they need to `git pull`, reinstall or restart.

Before committing: `pytest` passes, new behavior has tests, `ROADMAP.md` updated (move items to
Completed, add any known bugs and new benchmark numbers).
