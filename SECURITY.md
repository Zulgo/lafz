# Security

Murmur processes your voice locally and has no server component. It never needs API keys or passwords.

What Murmur stores, all on your own computer:

- settings and dictation history (`~/Library/Application Support/Murmur` on Mac, `%APPDATA%\Murmur` on Windows)
- your last 10 recordings, for model comparison (can be turned off in Settings)

On macOS it asks for Microphone, Accessibility and Input Monitoring permissions, which it needs to record, watch the hotkey and paste text. It only reacts to its own hotkey and the undo shortcut.

## Reporting a vulnerability

Please report security problems privately through GitHub's
[private vulnerability reporting](https://github.com/Zulgo/murmur/security/advisories/new)
instead of a public issue. You'll get a reply as soon as possible.
