# Cadence commands (copy and paste)

Every command below is meant to be copied and pasted into PowerShell as is.
Commands marked **(one time)** only need to run once. If you've already done that step,
skip it. Running it again doesn't hurt, it's just slower.

---

## Before anything: open PowerShell in the Cadence folder

All commands below must run **inside the Cadence folder** (the folder with `README.md` in it).

1. Open the Cadence folder in File Explorer.
2. Click the address bar at the top (where the folder path is shown).
3. Type `powershell` and press **Enter**.

A PowerShell window opens, already in the right folder. To check, paste this; it should
list `README.md`, `COMMANDS.md`, `src` and others:

```powershell
dir
```

---

## Start Cadence

**With a log window** (best for testing, since you can see what's happening and copy errors):

```powershell
.\.venv\Scripts\python.exe -m cadence -v
```

**In the background** (no window, just the tray icon and the mic button):

```powershell
.\.venv\Scripts\pythonw.exe -m cadence
```

Wait for the pill at the bottom of the screen to say "Cadence ready". The first start ever
downloads the speech model (~1.6 GB) and can take a few minutes. After that it takes seconds.

---

## If the text didn't go anywhere

If your cursor wasn't in a text box when you finished talking, the dictation is still on
your clipboard: click where you want it and press **Ctrl+V**. (Or: tray icon → **Copy last
dictation**.)

---

## Stop Cadence

Any one of these:

- Right-click the tray icon (by the clock, maybe under the **^** arrow), then **Quit**.
- Click the PowerShell window running Cadence and press **Ctrl+C**.
- Close the PowerShell window running Cadence.

---

## Use a different setting for one run only

Add options after `-m cadence`. Your saved `settings.toml` is **not** changed. The next
normal start uses your saved settings again.

Claude cleanup is **on by default** (as soon as your API key is set, see below).

**Rules-only cleanup (no AI, nothing sent online), this run only:**

```powershell
.\.venv\Scripts\python.exe -m cadence -v --llm none
```

**Any setting, this run only.** Write `--set section.name=value`, using the names in
`settings.toml`: the part in `[brackets]`, a dot, then the setting name.

```powershell
.\.venv\Scripts\python.exe -m cadence -v --set stt.lag_words=3
```

**Several at once:**

```powershell
.\.venv\Scripts\python.exe -m cadence -v --llm none --set stt.beam_size=1 --set hotkey.mode=hold
```

Some useful ones:

| Option | What it does |
|---|---|
| `--llm claude` / `--llm none` / `--llm local` | AI cleanup: Claude, off, or a model on your PC |
| `--set stt.lag_words=3` | Text follows your voice more closely (default 5) |
| `--set output.live_insert=true` | Text appears while you talk (when you tap or click), instead of all at the end |
| `--set stt.beam_size=1` | Slightly faster transcription, slightly more mistakes |
| `--set output.restore_clipboard=true` | Put back what you had copied, instead of leaving the dictation on the clipboard |
| `--set hotkey.mode=hold` | Push-to-talk only (no tapping) |
| `--set ui.sounds=false` | No beeps |
| `--set ui.button=false` | No mic button on screen |

If you misspell a setting, Cadence stops and tells you what's wrong.

---

## Change a setting permanently

Open the settings file in Notepad, change it, save, then restart Cadence:

```powershell
notepad settings.toml
```

(Or: tray icon → **Open settings**.) Every setting has an explanation next to it. If the file
doesn't exist yet, start Cadence once and it gets created.

**Reset all settings to the current defaults.** Do this after an update whose notes say
"defaults changed", since your `settings.toml` keeps the values it was created with. Stop
Cadence first, then:

```powershell
del settings.toml
```

The next start creates a fresh one. (Any changes you'd made are lost, so note them first.)

---

## Claude API key

**Set it (one time).** Replace `sk-ant-...` with your real key, keeping the quotes:

```powershell
setx ANTHROPIC_API_KEY "sk-ant-..."
```

Then **close PowerShell and open a new one** (the key only shows up in new windows) and
start Cadence. Claude cleanup is on by default, and the log no longer warns
"ANTHROPIC_API_KEY ... isn't set". Without the key, Cadence uses the built-in rules only.

**Check it's set** (prints "set" or "not set", never the key itself, so it's safe to
screenshot):

```powershell
if ($env:ANTHROPIC_API_KEY) { "set" } else { "not set" }
```

**Remove it:**

```powershell
reg delete HKCU\Environment /v ANTHROPIC_API_KEY /f
```

The key is never stored in any Cadence file, so it can't end up on GitHub.

---

## Get the latest version

After a pull request has been merged on GitHub, stop Cadence, then:

```powershell
git pull
```

Then start Cadence again. If the update notes say **"reinstall needed"**, also run the
installer (next section) before starting.

---

## Install or reinstall

**(one time)** Downloads everything Cadence needs (a few GB the first time). Run it on a new
computer, after a "reinstall needed" update, or if Cadence won't start after an update:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

**(one time, already done if you cloned with git)** Point your copy at the renamed repository:

```powershell
git remote set-url origin https://github.com/boyetteai24/cadence
```

---

## Start Cadence automatically when you sign in

```powershell
.\.venv\Scripts\python.exe -m cadence autostart on
```

Turn it off again:

```powershell
.\.venv\Scripts\python.exe -m cadence autostart off
```

---

## Troubleshooting

**List microphones** (use the number in `settings.toml` under `[audio]`, `device = ...`):

```powershell
.\.venv\Scripts\python.exe -m cadence devices
```

**Open the log file** (useful when Cadence runs in the background):

```powershell
notepad $env:APPDATA\Cadence\cadence.log
```

**Test the cleanup rules on some text**, without speaking:

```powershell
.\.venv\Scripts\python.exe -m cadence clean "um so I I think we should meet at 3, no wait, 4"
```

**Measure transcription speed** on a recording (replace `recording.wav` with your file):

```powershell
.\.venv\Scripts\python.exe -m cadence transcribe recording.wav --repeat 3
```

**Show which version is running:** it's the first line of the log, `Cadence 0.6.0 starting`.
