# Cadence

> **New to PowerShell?** [COMMANDS.md](COMMANDS.md) has every command you need (start, stop,
> update, settings, one-run options, API key), ready to copy and paste.

Voice dictation for Windows that works in any app. Hold **Ctrl + Win**, speak naturally, let go,
and cleaned-up text is pasted wherever your cursor is.

- **Local:** speech recognition runs on your NVIDIA GPU with
  [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (`large-v3-turbo`). Nothing leaves
  your machine unless you set a Claude API key.
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
says "Cadence is loading…", then "Cadence ready". While you hold the shortcut it shows **Live**
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
- **Click the mic button**: just above the taskbar there's a slim bar; hover it and it turns
  into a mic button. Click, talk, then click the "Live" pill to finish. Clicking it
  doesn't move your cursor, so the text still goes where you were typing.
  To move it, **right-click and drag**. While you drag, outlines show the four snap spots
  (the middle of the bottom, top, left and right edges of the screen); drop it near one and
  it snaps there, or drop it anywhere else to leave it there. Cadence remembers where you put it.

**The text appears when you finish**, like Wispr Flow: your whole dictation is cleaned up
in one go and pasted at once. Cadence transcribes each phrase in the background during your
pauses while you talk, so when you stop it only has your last few words left to do.

**It also stays on your clipboard.** If your cursor wasn't in a text box when you finished,
click where you want the text and press **Ctrl+V**. (Set `restore_clipboard = true` to put
back what you had copied before instead.)

**Esc** cancels the current recording.
- **Corrections:** "…at 3pm, no wait, 4pm" → "…at 4pm". "Scratch that" deletes the previous sentence.
- **Formatting:** say "new line" or "new paragraph".
- If text didn't land where you wanted it, use **tray → Copy last dictation**.

**Feedback:** after a dictation, click **"Pasted · Feedback"** on the pill (or press
**Ctrl+Alt+F**) to see what you said and what was typed, then write what it should have said
and/or a note. Reviews are kept in a private file on your PC, and the 20 most recent are sent
to the AI cleanup with every dictation so it learns your preferences right away. To share them
for permanent fixes: `python -m cadence feedback --copy`, then paste into the chat.

## Settings

All settings live in **`settings.toml`** in the main project folder, on GitHub, and every
option has a comment explaining it. To change one, edit it on GitHub (tray → **Edit settings
on GitHub**), commit, then on your PC quit Cadence, run `git pull`, and start it again.

The repository is public, so `settings.toml` must never contain secrets or personal details.
The Claude API key is read only from the `ANTHROPIC_API_KEY` environment variable.

**Change a setting for one run only** by adding it to the start command. `settings.toml`
stays as it is, and the next normal start uses your saved settings again:

```powershell
.\.venv\Scripts\python.exe -m cadence --llm none                    # no AI cleanup this time
.\.venv\Scripts\python.exe -m cadence --set stt.beam_size=1          # any setting: section.name=value
.\.venv\Scripts\python.exe -m cadence -v --llm none --set hotkey.mode=hold --set ui.sounds=false
```

Put these options right after `-m cadence`. The names are the ones in `settings.toml`: the
part in `[brackets]`, a dot, then the setting. A misspelled name or a wrong kind of value
(e.g. text where a number belongs) stops Cadence with a message saying what's wrong.

The options you're most likely to change:

```toml
[hotkey]
combo = "ctrl+win"        # e.g. "ctrl_r", "ctrl+alt+space", "f9"
mode = "tap_or_hold"      # or "hold" (push-to-talk only) or "toggle"

[cleanup]
llm = "claude"            # Claude Haiku polishes your text (needs the API key); "none" = rules only

[stt]
background = true         # transcribe during your pauses, so the wait at the end is short
pause_seconds = 0.6       # how long a pause ends a phrase
beam_size = 5             # accuracy (5) vs. a little speed (1)
model = "large-v3-turbo"  # "distil-large-v3" is faster; "small.en" runs on weak GPUs/CPU

[output]
restore_clipboard = false # false = your dictation stays on the clipboard

[dictionary]
words = ["Cadence", "Wispr Flow"]   # names Whisper should spell right
```

### AI cleanup with a local LLM

1. Install [Ollama](https://ollama.com) and run `ollama pull qwen2.5:3b-instruct`.
2. In `settings.toml` (on GitHub), set `llm = "local"`, then `git pull`.

Any OpenAI-compatible server works. Point `local_url` and `local_model` at it.

### AI cleanup with Claude Haiku

1. Get an API key from https://console.anthropic.com.
2. Store it in an environment variable, never in a file. In PowerShell, run:
   `setx ANTHROPIC_API_KEY "sk-ant-..."` (with your real key), then close and reopen PowerShell.
3. Restart Cadence. Claude cleanup (`llm = "claude"`) is the default; without a key, Cadence
   uses the built-in rules only and sends nothing.

Only the transcribed text is sent to Anthropic, never audio. Dictations shorter than
`min_words_for_llm` words skip Claude. If the API is slow or unreachable, Cadence types the
rules-cleaned text instead. The AI sees your whole dictation at once.

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
