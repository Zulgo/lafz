# Security

Murmur processes your voice on your own computer. It has no server component, no account and no telemetry, and it
never needs API keys or passwords.

## What Murmur stores (all on your computer)

| Data | Where | Protection |
| --- | --- | --- |
| Settings, dictation history, learned words (`learning.json`) | `~/Library/Application Support/Murmur` (Mac), `%APPDATA%\Murmur` (Windows) | Readable by your user account only |
| Your last 10 recordings (for model comparison) | `recordings/` in the same folder | Owner-only; turn off in Settings to delete them |
| Error messages (never your dictated text) | `~/Library/Logs/Murmur.log` (Mac), `murmur.log` in your user folder (Windows) | Your account |

These files aren't encrypted by Murmur. Turn on FileVault (Mac) or BitLocker (Windows) to protect them if your computer
is lost or stolen.

## Network

- After the one-time model downloads (Hugging Face, Ollama), Murmur makes no internet connections.
- The AI cleanup and command mode talk only to Ollama on `localhost`.
- Murmur's window is served by a small web server bound to **127.0.0.1** (not reachable from your network). Every
  request needs a secret token created at each launch; requests addressed to any other host name are refused
  (DNS-rebinding protection), and no cross-origin access is allowed, so websites can't read or control Murmur.

## macOS permissions

Murmur asks for **Microphone**, **Accessibility** (to paste, undo and learn from corrections) and, only if needed,
**Input Monitoring**. It reacts only to its own hotkey and undo shortcut, never records other keystrokes, never reads
password fields, and reads only the few words around text it pasted itself.

Murmur.app is signed ad hoc, so macOS ties these permissions to the exact build: no other program can claim them.
After an update that changes the app, macOS asks you to allow them again.

## Things to be aware of

- **Murmur runs from its folder.** Murmur.app runs the Python code in the folder you installed it in, with the
  permissions you granted. Anything that can change files in that folder could change what Murmur does. Install it in
  your own home folder, keep it writable only by you, and don't run untrusted software that could tamper with it.
- **Installers download tools.** `install_mac.command` and `install_windows.bat` install uv with its official
  installer script and Ollama via Homebrew or winget. Read them before running if you prefer.
- **Windows:** the window opens in Microsoft Edge's app mode; the address contains a per-launch token, which Edge may
  keep in its history. The token stops working when Murmur quits.

## Reporting a vulnerability

Please report security problems privately through GitHub's
[private vulnerability reporting](https://github.com/Zulgo/murmur/security/advisories/new)
instead of a public issue. You'll get a reply as soon as possible.
