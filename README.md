# Cadence

Voice dictation for Windows that works in any app. Hold **Ctrl + Win**, speak naturally, let go,
and cleaned-up text is pasted wherever your cursor is.

- **Local:** speech recognition runs on your NVIDIA GPU with
  [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (`large-v3-turbo`). Nothing leaves
  your machine unless you turn on Claude.
- **Cleanup:** removes "um"/"uh", stutters and repeated words, applies corrections like
  *"at 3, no wait, 4"* and *"scratch that"*, and understands "new line" and "new paragraph".
  For more thorough grammar polish you can add a local LLM (Ollama, llama.cpp, LM Studio) or
  Claude Haiku.
- **No limits, no subscription:** dictations can be any length.

## Install (Windows 10/11)

Requirements: Python 3.11+ and a recent NVIDIA driver. You don't need to install the CUDA
toolkit; the CUDA libraries are installed through pip.

```powershell
git clone https://github.com/boyetteai24/cadence; cd cadence
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
.\.venv\Scripts\pythonw.exe -m cadence        # runs in the background (tray icon)
```

The first start downloads the Whisper model (~1.6 GB). A small pill at the bottom of the screen
says "Cadence is loading…", then "Cadence ready". While you hold the shortcut it shows **Listening**
with a live mic meter, then **Transcribing** until the text is pasted. The tray icon (click the
**^** by the clock if it's hidden) shows the same states:

| Tray color | Meaning |
|------------|---------|
| grey | loading |
| blue | ready |
| red | listening |
| yellow | transcribing |

To start Cadence when you sign in: `.\.venv\Scripts\python.exe -m cadence autostart on`

## Use

There are three ways to dictate:

- **Hold `Ctrl+Win`**, speak, and let go.
- **Tap `Ctrl+Win`** once and talk hands-free. Tap again to finish.
- **Click the mic button** just above the taskbar, talk, then click the "Listening" pill to
  finish. Clicking it doesn't move your cursor, so the text still goes where you were typing.
  To move the button, **right-click and drag** it. Cadence remembers where you put it.

**When you tap or click, text appears as you talk.** Each word is typed once you're about
five words past it. That's far enough back that an "um" or a "no wait" right after it can
still be handled. Whenever you pause briefly, everything so far is typed. When you hold the
shortcut, the same work happens in the background, but the text waits until you let go
(typing while keys are held would trigger shortcuts). Either way, the wait after you stop is
only for your last few words.

Set `live_mode = "pauses"` to type whole phrases at each pause instead, or `"off"` to
transcribe everything after you stop. With an AI cleanup model switched on, Cadence uses
`"pauses"`, because the AI needs whole phrases to work with.

**Esc** cancels the current recording. Words that were already typed stay.
- **Corrections:** "…at 3pm, no wait, 4pm" → "…at 4pm". "Scratch that" deletes the previous sentence.
- **Formatting:** say "new line" or "new paragraph".
- If text didn't land where you wanted it, use **tray → Copy last dictation**.

## Settings

All settings live in **`settings.toml` in the Cadence folder** (next to this README). It's
created the first time Cadence starts, and every option has a comment explaining it. Open it
in Notepad, or use **tray → Open settings**. Save it, then restart Cadence.

`settings.toml` is ignored by git, so `git pull` never overwrites your changes. To start over,
delete the file and it will be recreated with the defaults.

The options you're most likely to change:

```toml
[hotkey]
combo = "ctrl+win"        # e.g. "ctrl_r", "ctrl+alt+space", "f9"
mode = "tap_or_hold"      # or "hold" (push-to-talk only) or "toggle"

[cleanup]
llm = "none"              # "claude" = Claude Haiku polishes your text; "local" = a model on your PC

[stt]
live_mode = "words"       # "words", "pauses" or "off"
lag_words = 5             # how many words behind your voice the typing stays
pass_interval = 0.4       # seconds between re-checks while you talk
pause_seconds = 0.6       # how long a pause ends a phrase
model = "large-v3-turbo"  # "distil-large-v3" is faster; "small.en" runs on weak GPUs/CPU

[dictionary]
words = ["Kubernetes", "Anthropic"]
```

### AI cleanup with a local LLM

1. Install [Ollama](https://ollama.com) and run `ollama pull qwen2.5:3b-instruct`.
2. In `settings.toml`, set `llm = "local"`.

Any OpenAI-compatible server works. Point `local_url` and `local_model` at it.

### AI cleanup with Claude Haiku

1. Get an API key from https://console.anthropic.com.
2. Store it in an environment variable, never in a file. In PowerShell, run:
   `setx ANTHROPIC_API_KEY "sk-ant-..."` (with your real key), then close and reopen PowerShell.
3. In `settings.toml`, set `llm = "claude"`, then restart Cadence.

Only the transcribed text is sent to Anthropic, never audio. Dictations shorter than
`min_words_for_llm` words skip Claude. If the API is slow or unreachable, Cadence types the
rules-cleaned text instead. With an AI model on, text is typed phrase by phrase at pauses
rather than word by word, because the AI needs whole phrases.

## Troubleshooting

- **Logs:** `%APPDATA%\Cadence\cadence.log`, or run `python -m cadence -v` to see them in a console.
- **Runs on CPU (slow):** the log says why CUDA failed. Run `pip install -e ".[cuda]"` and update
  your NVIDIA driver.
- **Wrong microphone:** run `python -m cadence devices` and set `audio.device`.
- **Nothing pastes into an admin window:** Windows blocks input from non-admin apps. Run Cadence
  as administrator.
- **Terminal pastes don't work:** set `paste_keys = "ctrl+shift+v"`.
- **Measure speed:** `python -m cadence transcribe recording.wav --repeat 3`

## Development

See [AGENTS.md](AGENTS.md) for architecture and guidelines and [ROADMAP.md](ROADMAP.md) for
status. Run `pip install -e ".[all,dev]"` and then `pytest`.
