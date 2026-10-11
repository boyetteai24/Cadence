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

## Give feedback on a dictation

Right after a dictation, the pill at the bottom of the screen says **"Pasted · Feedback"**
for a few seconds. Click it, or press **Ctrl+Alt+F** at any time (or use tray icon →
**Give feedback on last dictation**). A window opens showing:

- what you said (as Cadence heard it) and what was typed;
- a box with the typed text, so you can fix it to **what it should have said**;
- a **Notes** box for what went wrong.

Both boxes are optional. **Ctrl+Enter** saves, **Esc** closes. Every review is kept in a
private file on your PC (never on GitHub), and your 20 most recent ones are sent to Claude
with each dictation, so it picks up your preferences straight away.

**Send your feedback to Claude Code** so it can be built into Cadence permanently. This
copies your recent reviews to the clipboard; then paste them into the chat:

```powershell
.\.venv\Scripts\python.exe -m cadence feedback --copy
```

(Add `--all` to copy every review instead of the last 30. Without `--copy`, they're printed in
the window.)

---

## Use a different setting for one run only

Add options after `-m cadence`. `settings.toml` is **not** changed. The next normal start
uses your saved settings again.

Claude cleanup is **on by default** (as soon as your API key is set, see below).

**Rules-only cleanup (no AI, nothing sent online), this run only:**

```powershell
.\.venv\Scripts\python.exe -m cadence -v --llm none
```

**Any setting, this run only.** Write `--set section.name=value`, using the names in
`settings.toml`: the part in `[brackets]`, a dot, then the setting name.

```powershell
.\.venv\Scripts\python.exe -m cadence -v --set stt.beam_size=1
```

**Several at once:**

```powershell
.\.venv\Scripts\python.exe -m cadence -v --llm none --set stt.beam_size=1 --set hotkey.mode=hold
```

Some useful ones:

| Option | What it does |
|---|---|
| `--llm claude` / `--llm none` / `--llm local` | AI cleanup: Claude, off, or a model on your PC |
| `--set stt.beam_size=1` | Slightly faster transcription, slightly more mistakes |
| `--set output.restore_clipboard=true` | Put back what you had copied, instead of leaving the dictation on the clipboard |
| `--set hotkey.mode=hold` | Push-to-talk only (no tapping) |
| `--set ui.sounds=false` | No beeps |
| `--set ui.button=false` | No mic button on screen |

If you misspell a setting, Cadence stops and tells you what's wrong.

---

## Change a setting permanently (on GitHub)

Your settings live in `settings.toml` on GitHub, and every setting has an explanation next
to it. The repository is **public**, so never put keys, passwords or personal details in it.

1. Open it for editing: tray icon → **Edit settings on GitHub**, or go to
   https://github.com/boyetteai24/cadence/edit/main/settings.toml
2. Change the value, then click **Commit changes** (straight to `main` is fine).
3. On your PC: quit Cadence, then in PowerShell in the Cadence folder:

```powershell
git pull
```

4. Start Cadence again.

If you made a typo, Cadence won't start and tells you which line is wrong. Fix it on GitHub,
`git pull`, and start again.

**Don't also edit `settings.toml` on your PC** (in Notepad), or `git pull` will complain that
your local changes would be overwritten. If that happens, throw away the local edits and pull:

```powershell
git checkout -- settings.toml
git pull
```

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

**Test that Claude works** (no speaking needed). It prints the cleaned text, then one line
saying whether Claude was used:

```powershell
.\.venv\Scripts\python.exe -m cadence clean "um so like I I think we should, you know, maybe go"
```

| Last line says | Meaning |
|---|---|
| `[AI cleanup: claude worked (… ms)]` | All set: Claude is cleaning your dictations |
| `Claude is OFF because ANTHROPIC_API_KEY isn't set` | Set the key (above), then open a **new** PowerShell window |
| `claude failed: …authentication…` | The key is wrong or was revoked; set it again |
| `claude failed: …credit…` or `…billing…` | Add API credits at console.anthropic.com (Plans & Billing) |
| `AI cleanup: off` | `settings.toml` says `llm = "none"`; change it to `llm = "claude"` on GitHub, then `git pull` |

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

**List microphones** (put the number in `settings.toml` on GitHub, under `[audio]`, as `device = 1`):

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

**Show which version is running:** it's the first line of the log, `Cadence 0.9.0 starting`.
