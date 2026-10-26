#!/bin/zsh
# Builds ~/Applications/Murmur.app: a small native launcher that runs app.py with this project's environment.
# The launcher stays alive as the parent process, so macOS attributes permissions to "Murmur" (not uv/python).
set -e
cd "$(dirname "$0")"
PROJ="$PWD"
APP="$HOME/Applications/Murmur.app"  # outside iCloud-synced Desktop, which breaks codesign
[ -x .venv/bin/python ] || { echo "Run 'uv sync' first."; exit 1; }
ln -sf python .venv/bin/Murmur  # Python started via this name shows as "Murmur" in the Dock
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
ICONSET="$(mktemp -d)/Murmur.iconset"
mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s assets/icon_1024.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) assets/icon_1024.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/Murmur.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Murmur</string>
<key>CFBundleDisplayName</key><string>Murmur</string>
<key>CFBundleIdentifier</key><string>local.murmur.dictation</string>
<key>CFBundleExecutable</key><string>Murmur</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>3</string>
<key>CFBundleIconFile</key><string>Murmur</string>
<key>LSUIElement</key><true/>
<key>NSMicrophoneUsageDescription</key><string>Murmur records your voice to transcribe it locally.</string>
<key>NSDesktopFolderUsageDescription</key><string>Murmur runs from its project folder on your Desktop.</string>
<key>NSDocumentsFolderUsageDescription</key><string>Murmur runs from its project folder in Documents.</string>
</dict></plist>
PLIST
SRC="$(mktemp -d)/launcher.c"
cat > "$SRC" <<'C'
#include <fcntl.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
int main(void) {
    char py[4096], script[4096], logp[4096];
    snprintf(py, sizeof py, "%s/.venv/bin/Murmur", PROJ);
    if (access(py, X_OK) != 0) snprintf(py, sizeof py, "%s/.venv/bin/python", PROJ);
    snprintf(script, sizeof script, "%s/app.py", PROJ);
    snprintf(logp, sizeof logp, "%s/Library/Logs/Murmur.log", getenv("HOME"));
    unsetenv("VIRTUAL_ENV");
    setenv("PYTHONUNBUFFERED", "1", 1);
    setenv("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", 1);  /* apps get a bare PATH */
    if (chdir(PROJ) != 0) return 1;
    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_addopen(&fa, 1, logp, O_WRONLY | O_CREAT | O_APPEND, 0644);
    posix_spawn_file_actions_adddup2(&fa, 1, 2);
    char *argv[] = {py, script, NULL};
    pid_t pid;
    if (posix_spawn(&pid, py, &fa, NULL, argv, environ) != 0) return 1;
    int status;
    waitpid(pid, &status, 0);
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
C
clang -O2 -DPROJ="\"$PROJ\"" -o "$APP/Contents/MacOS/Murmur" "$SRC"
xattr -cr "$APP"
# Identify the app by its bundle ID rather than the build's hash, so macOS keeps permissions across rebuilds.
codesign --force --deep --sign - -r='designated => identifier "local.murmur.dictation"' "$APP"
echo "Built $APP"
