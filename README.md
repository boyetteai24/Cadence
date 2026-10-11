# Flow

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
git clone <this repo> Flow; cd Flow
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
.\.venv\Scripts\pythonw.exe -m flow        # runs in the background (tray icon)
```

The first start downloads the Whisper model (~1.6 GB). A small pill at the bottom of the screen
says "Flow is loading…", then "Flow ready". While you hold the shortcut it shows **Listening**
with a live mic meter, then **Transcribing** until the text is pasted. The tray icon (click the
**^** by the clock if it's hidden) shows the same states:

| Tray color | Meaning |
|------------|---------|
| grey | loading |
| blue | ready |
| red | listening |
| yellow | transcribing |

To start Flow when you sign in: `.\.venv\Scripts\python.exe -m flow autostart on`

## Use

- **Hold `Ctrl+Win`**, speak, release. To use a press-to-start, press-to-stop shortcut instead,
  set `mode = "toggle"`.
- **Esc** cancels the current recording.
- **Corrections:** "…at 3pm, no wait, 4pm" → "…at 4pm". "Scratch that" deletes the previous sentence.
- **Formatting:** say "new line" or "new paragraph".
- If text didn't land where you wanted it, use **tray → Copy last dictation**.

## Configure

Run `python -m flow init-config` (or use **tray → Open config file**) to create
`%APPDATA%\Flow\config.toml`. Every option has a comment in that file. The main ones:

```toml
[hotkey]
combo = "ctrl+win"        # e.g. "ctrl_r", "ctrl+alt+space", "f9"
mode = "hold"             # or "toggle"

[stt]
model = "large-v3-turbo"  # "distil-large-v3" is faster; "small.en" runs on weak GPUs/CPU

[cleanup]
llm = "none"              # "local" or "claude" for AI grammar polish

[dictionary]
words = ["Kubernetes", "Anthropic"]
[dictionary.replacements]
"my email" = "me@example.com"
```

### AI cleanup with a local LLM

1. Install [Ollama](https://ollama.com) and run `ollama pull qwen2.5:3b-instruct`.
2. In the config file, set `llm = "local"`.

Any OpenAI-compatible server works. Point `local_url` and `local_model` at it.

### AI cleanup with Claude Haiku

1. `setx ANTHROPIC_API_KEY "sk-ant-..."`, then sign out and back in (or restart Flow).
2. In the config file, set `llm = "claude"`.

Only the transcribed text is sent to Anthropic, never audio. Utterances shorter than
`min_words_for_llm` words skip the LLM. If the API is slow or unreachable, Flow pastes the
rules-cleaned text instead.

## Troubleshooting

- **Logs:** `%APPDATA%\Flow\flow.log`, or run `python -m flow -v` to see them in a console.
- **Runs on CPU (slow):** the log says why CUDA failed. Run `pip install -e ".[cuda]"` and update
  your NVIDIA driver.
- **Wrong microphone:** run `python -m flow devices` and set `audio.device`.
- **Nothing pastes into an admin window:** Windows blocks input from non-admin apps. Run Flow
  as administrator.
- **Terminal pastes don't work:** set `paste_keys = "ctrl+shift+v"`.
- **Measure speed:** `python -m flow transcribe recording.wav --repeat 3`

## Development

See [AGENTS.md](AGENTS.md) for architecture and guidelines and [ROADMAP.md](ROADMAP.md) for
status. Run `pip install -e ".[all,dev]"` and then `pytest`.
