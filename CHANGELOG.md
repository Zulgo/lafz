# Changelog

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
