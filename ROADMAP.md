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
- [x] Unit tests for rules, LLM stage (fake client + stub server), pipeline, hotkeys, config

## Next — v0.2 (everyday polish)

- [ ] **Validate on target hardware** (Windows 11 + NVIDIA GPU): run the full app, fill in the
      benchmark table below, tune defaults (`paste_delay_ms`, `beam_size`, model choice)
- [ ] Small floating overlay near the cursor/taskbar showing listening/processing + mic level
- [ ] App-aware behavior: detect the foreground app (e.g. terminals use `ctrl+shift+v`;
      code editors skip trailing punctuation; chat apps casual tone)
- [ ] Exclude dictation from Windows clipboard history (`ExcludeClipboardContentFromMonitorProcessing`)
- [ ] Hot-reload of the config file; settings window instead of editing TOML
- [ ] Stuck-key protection: verify modifier state with `GetAsyncKeyState` after lock/unlock
- [ ] Recommended local LLM presets + prompt tuned/evaluated on a small dictation test set

## Later — v0.3+

- [ ] Streaming transcription while speaking (process completed VAD segments early) to cut
      end-of-speech latency on long dictations
- [ ] Command mode: select text, hold a second hotkey, say "make this more formal"
- [ ] Personal dictionary learned from corrections; snippets ("insert my address")
- [ ] Optional local history with search (opt-in, stored locally)
- [ ] Packaged installer (PyInstaller/Nuitka + Inno Setup) so Python isn't required
- [ ] Alternative STT backends (e.g. NVIDIA Parakeet via NeMo/ONNX, whisper.cpp)
- [ ] Cleanup evaluation harness: fixed transcripts → expected outputs, scored per backend

## Known bugs / limitations

- Not yet run end-to-end on Windows hardware; developed and unit-tested on Linux with fakes
  (the model download host was unreachable from the dev sandbox).
- Clipboard restore only preserves text; images/files on the clipboard are left replaced by the
  transcript rather than restored.
- Apps running as Administrator don't receive input from a non-elevated Flow (Windows UIPI).
  Run Flow elevated if you need to dictate into elevated apps.
- `type` output mode sends newlines as Enter, which submits messages in chat apps.
- Rules-only mode handles corrections only with explicit markers and short replacements
  (≤3 words); everything else needs the LLM stage.
- Some keyboard layouts/remote-desktop sessions may report keys differently to pynput.

## Performance benchmarks

Measure with `python -m flow transcribe <file> --repeat 3` (report the warm runs) and the
per-dictation timings in `flow.log`.

| Date | Hardware | Model / compute | Audio | STT | Cleanup | Insert | Notes |
|------|----------|-----------------|-------|-----|---------|--------|-------|
| _pending_ | NVIDIA GPU (user's machine) | large-v3-turbo / float16 | 10 s | — | rules — / local — / Haiku — | — | first measurement to fill in |

Targets (from `AGENTS.md`): STT < 400 ms, rules < 5 ms, local LLM < 600 ms, Haiku < 1 s,
insert < 150 ms for a 10-second utterance.
