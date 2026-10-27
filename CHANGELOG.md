# Changelog

## 0.5.0

### New
- **Self-corrections:** "let's meet at 3, no wait, 4 pm" becomes "let's meet at 4 pm". "Scratch that" removes the
  sentence before it. Turn it off in Settings.
- The Dock and app switcher show **Murmur** with the bear icon instead of "python3.12".

### Security
- Murmur.app's permissions are tied to its exact build, so no other program can claim them. After updating, macOS
  asks you to allow them again once.
- Murmur now sends text to Ollama only if Ollama runs under your own account. Before, another person signed in to the
  same computer could run a fake Ollama to read dictations and choose what Murmur pastes.
- Ollama is reached at `127.0.0.1` instead of `localhost`, which could resolve to a different address.

### Fixed
- The microphone works again after AirPods or a headset connect or disconnect.
- Opening Murmur from Spotlight or Finder while its window is closed shows the window.
- "Transcribing…" no longer disappears while the speech model is still loading.
- Deleting a history entry can no longer delete a different one when a new dictation arrives.
- Without Accessibility, the dictated text stays on the clipboard so you can paste it yourself.
- The Latest card keeps updating after you've clicked into it.

## 0.4.0

### New
- **New window design:** a frosted-glass window with a Home dashboard (live voice orb, stats, latest dictation), History grouped by day, Dictionary, Settings and Setup pages.
- **Command mode:** hold Shift + the hotkey to tell the local AI what to do with selected text, or what to write.
- **Voice commands:** "new line", "new paragraph" and "bullet point".
- **Keeps your clipboard:** what you had copied is restored after Murmur pastes.
- **Languages:** 16 languages plus auto-detect.
- **Memory saver:** the speech model rests after 10 idle minutes and wakes when you speak.
- **Menu-bar-only mode** on macOS (hide the Dock icon).
- **Learns from your corrections** and vocabulary, locally.
- Automated tests on macOS and Windows for every change; issue and pull request templates.

### Changed
- Cleanup model is now `qwen2.5:1.5b` (Apache 2.0): faster, lighter and more faithful than `qwen2.5:3b`.
- Input Monitoring is optional on macOS: Murmur detects when the hotkey already works without it.
- Only one copy of Murmur runs at a time; opening it again shows the existing window.
- Removed the Parakeet speech model; Murmur uses Whisper only.

### Fixed
- The cleanup safety check now works for every alphabet (Urdu, Arabic, Chinese and so on).

## 0.3.0

- First public release: push-to-talk dictation with Whisper, Silero VAD, local cleanup via Ollama, writing styles, per-app styles, dictionary, history, undo, model comparison, frosted recording indicator and menu bar icon on macOS.
