# Contributing to Lafz

Thanks for helping! Lafz is a small project, so every bug report, test and pull request matters.

## Most wanted

- **Windows testing.** The Windows version has never run on real hardware. Installing it and reporting what happens is hugely useful.
- **Intel Macs** and **non-US keyboard layouts** (the paste/undo shortcuts assume QWERTY-style key positions).
- **Accuracy reports:** which speech model works best for your accent or microphone (use *Settings → Compare models*).

## Reporting a bug

Open an [issue](https://github.com/Zulgo/lafz/issues) with:

1. Your OS and version, and your chip (e.g. "macOS 15, M1, 8 GB").
2. What you did, what you expected, and what happened.
3. The end of the log: `~/Library/Logs/Lafz.log` (Mac) or `lafz.log` in your user folder (Windows).

Please **don't** attach recordings or history unless you're comfortable sharing them; they may contain private speech.

## Development setup

```bash
git clone https://github.com/Zulgo/lafz.git && cd lafz
uv sync
uv run python app.py
```

- Code style: plain Python, short functions, comments only where something isn't obvious. Match the surrounding code.
- macOS UI pieces (`pill.py`, `menubar.py`, key monitoring in `system.py`) must run on the **main thread**.
- Speech models run on a single dedicated thread (`Lafz.stt_thread` in `app.py`). MLX models only work on the thread that loaded them.
- Before opening a pull request, run `uv run pytest` and `uvx pyflakes *.py`, and check that a dictation works end to end.
  The same tests run automatically on macOS and Windows for every pull request.
- The window is plain HTML/CSS/JavaScript in `ui/`, served by `webui.py`; no build step is needed.

## Pull requests

- Keep each pull request focused on one change.
- Describe what you changed and how you tested it.
- By contributing, you agree that your contribution is licensed under the [MIT License](LICENSE).
