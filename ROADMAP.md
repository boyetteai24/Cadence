# ROADMAP

Status of Flow against the master spec in `AGENTS.md`. Update this file with every change.

## Completed — v0.1 (minimal reliable version)

- [x] Global hotkey, hold-to-talk (default `Ctrl+Win`) and toggle modes; `Esc` cancels
- [x] Start-menu suppression when the hotkey includes the Win key
- [x] Microphone capture with no length limit; optional always-open stream for lower latency
- [x] faster-whisper STT on CUDA (float16), automatic CPU (int8) fallback, warm-up at load
- [x] pip-installed CUDA/cuDNN DLLs discovered automatically on Windows
- [x] Silence / accidental-tap gate and a filter for common Whisper hallucinations
- [x] Rules cleanup: fillers, stutters, repeated words/phrases, "no wait"/"sorry, I meant"
      corrections, "scratch that", "new line"/"new paragraph", capitalization, custom replacements
- [x] Optional LLM polish: any OpenAI-compatible local server (Ollama, llama.cpp, LM Studio) or
      Claude Haiku; chunking for long text; output guard; fail-soft fallback to rules output
- [x] Custom vocabulary passed to Whisper and the LLM
- [x] Clipboard-paste output with clipboard restore; synthetic typing alternative
- [x] Ordered background processing queue (you can start the next dictation while one processes)
- [x] Tray icon with state colors, "Copy last dictation", AI-cleanup switch, open config
- [x] Audio cues, single-instance guard, autostart (`flow autostart on`), rotating log file
- [x] Dev commands: `transcribe` (with timings / RTF), `clean`, `devices`, `init-config`
- [x] On-screen status pill (listening + live mic meter, transcribing, ready, errors); never takes focus
- [x] Paste via native SendInput after physical key release; target app logged; clipboard restored in background
- [x] Clickable on-screen mic button (doesn't take focus); tap-or-hold hotkey mode (default)
- [x] Start-menu fix: mask key sent after Windows registers the Win key; Start/Search closed
      automatically if it still grabbed focus before pasting; our own injected keys ignored
- [x] Pause splitting: phrases transcribed at each pause while you talk; typed live when
      hands-free, buffered while the hotkey is held; earlier text passed as Whisper context
- [x] "Text appeared N ms after you stopped" latency logged per dictation
- [x] Mic button moved by right-click + drag; position remembered across restarts (drag tracks
      the real cursor via Win32 polling, since Tk motion events didn't arrive on Windows)
- [x] Unit tests for rules, LLM stage (fake client + stub server), pipeline, hotkeys, config

## Next — v0.2 (everyday polish)

- [ ] **Validate on target hardware** (Windows 11 + NVIDIA GPU): run the full app, fill in the
      benchmark table below, tune defaults (`paste_delay_ms`, `beam_size`, model choice)
- [ ] Live preview of the current (not yet finished) phrase in the overlay
- [ ] App-aware behavior: detect the foreground app (e.g. terminals use `ctrl+shift+v`;
      code editors skip trailing punctuation; chat apps casual tone)
- [ ] Exclude dictation from Windows clipboard history (`ExcludeClipboardContentFromMonitorProcessing`)
- [ ] Hot-reload of the config file; settings window instead of editing TOML
- [ ] Stuck-key protection: verify modifier state with `GetAsyncKeyState` after lock/unlock
- [ ] Recommended local LLM presets + prompt tuned/evaluated on a small dictation test set

## Later — v0.3+

- [ ] Command mode: select text, hold a second hotkey, say "make this more formal"
- [ ] Personal dictionary learned from corrections; snippets ("insert my address")
- [ ] Optional local history with search (opt-in, stored locally)
- [ ] Packaged installer (PyInstaller/Nuitka + Inno Setup) so Python isn't required
- [ ] Alternative STT backends (e.g. NVIDIA Parakeet via NeMo/ONNX, whisper.cpp)
- [ ] Cleanup evaluation harness: fixed transcripts → expected outputs, scored per backend

## Known bugs / limitations

- **Windows tests (2026-10-10):** transcription and overlay work on CUDA. The paste went to
  `SearchHost.exe`: releasing Ctrl+Win opened Start/Search, which stole focus (the Start-menu
  mask key was injected from inside the hook callback, too early). Fixed as above; needs re-test.
- The mic button's click handling is untested on real hardware (Tk not available in the dev sandbox).
- Clipboard restore only preserves text; images/files on the clipboard are left replaced by the
  transcript rather than restored.
- Apps running as Administrator don't receive input from a non-elevated Flow (Windows UIPI).
  Run Flow elevated if you need to dictate into elevated apps.
- Live insertion types each phrase separately, so "scratch that" or a correction can only fix
  text within the current phrase, and the optional LLM sees one phrase at a time.
- Pause splitting has only been tested with simulated audio; `pause_seconds` and
  `min_chunk_seconds` may need tuning on real speech.
- `type` output mode sends newlines as Enter, which submits messages in chat apps.
- Rules-only mode handles corrections only with explicit markers and short replacements
  (≤3 words); everything else needs the LLM stage.
- Some keyboard layouts/remote-desktop sessions may report keys differently to pynput.

## Performance benchmarks

Measure with `python -m flow transcribe <file> --repeat 3` (report the warm runs) and the
per-dictation timings in `flow.log`.

| Date | Hardware | Model / compute | Audio | STT | Cleanup | Insert | Notes |
|------|----------|-----------------|-------|-----|---------|--------|-------|
| 2026-10-10 | NVIDIA GPU (user's machine, model TBD) | large-v3-turbo / float16 | 9.6 s | 1081 ms | rules 1 ms | 144 ms* | first real dictation; *insert included the old blocking 120 ms clipboard wait. Model load 94.6 s incl. first download |
| 2026-10-10 | same | same | 18.5 s | 1245 ms | rules 1 ms | 30 ms | warm load 4.6 s; RTF 0.067 |

Targets (from `AGENTS.md`): STT < 400 ms, rules < 5 ms, local LLM < 600 ms, Haiku < 1 s,
insert < 150 ms for a 10-second utterance.
