#!/bin/zsh
# Lafz installer for macOS (Apple Silicon recommended). Double-click or run: ./install_mac.command
set -e
cd "$(dirname "$0")"
echo "==> Checking Apple command line tools (needed to build the app launcher)…"
xcrun --find clang >/dev/null 2>&1 || { xcode-select --install; echo "Finish the Command Line Tools install, then run this again."; exit 1; }
echo "==> Installing uv (Python manager)…"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
echo "==> Installing Ollama (local cleanup model)…"
command -v ollama >/dev/null || { command -v brew >/dev/null && brew install ollama || echo "Install Ollama from https://ollama.com (optional: only needed for LLM cleanup)"; }
(brew services start ollama >/dev/null 2>&1 || (nohup ollama serve >/dev/null 2>&1 &)) || true
sleep 3; ollama pull qwen2.5:1.5b || echo "(skipped cleanup model; Lafz will use rule-based cleanup)"
echo "==> Installing Lafz's libraries…"
uv sync
if [ -d ~/Applications/Murmur.app ]; then
  echo "==> Replacing Murmur (Lafz's old name); your history and settings move over…"
  pkill -x Murmur || true
  mv ~/Applications/Murmur.app ~/.Trash/"Murmur $(date +%s).app"
  tccutil reset All local.murmur.dictation >/dev/null 2>&1 || true  # forget the old app's permissions
fi
echo "==> Building Lafz.app in ~/Applications…"
./build_app.sh
open ~/Applications/Lafz.app
echo "Done. Allow Lafz under Input Monitoring, Accessibility and Microphone (see README), then quit (⌘Q) and reopen it."
