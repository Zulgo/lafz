# Security

Murmur processes your voice locally and has no server component. It never needs API keys or passwords.

What Murmur stores, all on your own computer:

- settings and dictation history (`~/Library/Application Support/Murmur` on Mac, `%APPDATA%\Murmur` on Windows)
- your last 10 recordings, for model comparison (can be turned off in Settings)
- learned corrections and vocabulary: word pairs and counts only (`learning.json`; can be turned off in Settings)

To learn from corrections, Murmur re-reads the text field it just pasted into, for at most 90 seconds, and only compares
the passage around its own pasted text. It never reads password fields and never stores the field's content.

Murmur's window is a small web page served only on 127.0.0.1 (this computer). Every request must carry a secret token
created at launch, and requests addressed to any other host name are refused, so websites and other computers can't
reach it.

On macOS it asks for Microphone and Accessibility permissions (Input Monitoring only if needed), which it needs to record, watch the hotkey and paste text. It only reacts to its own hotkey and the undo shortcut.

## Reporting a vulnerability

Please report security problems privately through GitHub's
[private vulnerability reporting](https://github.com/Zulgo/murmur/security/advisories/new)
instead of a public issue. You'll get a reply as soon as possible.
