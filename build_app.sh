#!/bin/zsh
# Builds ~/Applications/Murmur.app: a small native app that runs app.py inside its own process (Python embedded),
# so macOS shows it as "Murmur" with the bear icon in the Dock and attributes permissions to Murmur.app.
set -e
cd "$(dirname "$0")"
PROJ="$PWD"
APP="$HOME/Applications/Murmur.app"  # outside iCloud-synced Desktop, which breaks codesign
[ -x .venv/bin/python ] || { echo "Run 'uv sync' first."; exit 1; }
PYINFO=$(.venv/bin/python -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR')); print(sysconfig.get_config_var('INCLUDEPY')); print(sysconfig.get_config_var('LDVERSION'))")
PY_LIB=$(echo "$PYINFO" | sed -n 1p); PY_INC=$(echo "$PYINFO" | sed -n 2p); PY_VER=$(echo "$PYINFO" | sed -n 3p)
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
#include <Python.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

/* Runs app.py with the project's virtual environment, inside this process. Opening Murmur again (Dock, Spotlight,
   Finder) reaches the app directly, and the Dock shows this bundle's name and icon. */
int main(void) {
    char log[4096];
    snprintf(log, sizeof log, "%s/Library/Logs/Murmur.log", getenv("HOME"));
    freopen(log, "a", stdout);
    dup2(fileno(stdout), fileno(stderr));
    setvbuf(stdout, NULL, _IOLBF, 0);
    unsetenv("VIRTUAL_ENV");
    unsetenv("PYTHONHOME");
    setenv("PYTHONUNBUFFERED", "1", 1);
    setenv("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", 1);  /* apps get a bare PATH */
    if (chdir(PROJ) != 0) return 1;

    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    /* Pretend to be the venv's python, so Python finds pyvenv.cfg and the project's installed packages. */
    PyStatus status = PyConfig_SetBytesString(&config, &config.program_name, PROJ "/.venv/bin/python");
    char *argv[] = {"Murmur", PROJ "/app.py"};
    if (!PyStatus_Exception(status)) status = PyConfig_SetBytesArgv(&config, 2, argv);
    if (!PyStatus_Exception(status)) status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) Py_ExitStatusException(status);
    return Py_RunMain();
}
C
clang -O2 -DPROJ="\"$PROJ\"" -I"$PY_INC" -L"$PY_LIB" -lpython"$PY_VER" -Wl,-rpath,"$PY_LIB" \
  -o "$APP/Contents/MacOS/Murmur" "$SRC"
xattr -cr "$APP"
# Identify the app by its bundle ID rather than the build's hash, so macOS keeps permissions across rebuilds.
codesign --force --deep --sign - -r='designated => identifier "local.murmur.dictation"' "$APP"
echo "Built $APP"
