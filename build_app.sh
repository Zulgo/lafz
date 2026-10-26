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
SRC="$(mktemp -d)/launcher.m"
cat > "$SRC" <<'C'
#import <Cocoa/Cocoa.h>
#include <fcntl.h>
#include <spawn.h>
#include <sys/wait.h>
extern char **environ;
static char py[4096], script[4096], logp[4096];
static posix_spawn_file_actions_t fa;

static pid_t start_murmur(void) {
    char *argv[] = {py, script, NULL};
    pid_t pid = 0;
    posix_spawn(&pid, py, &fa, NULL, argv, environ);
    return pid;
}

/* Opening Murmur again (Spotlight, Finder, Launchpad) sends "reopen" to this launcher, not to Python.
   Start Murmur once more: the new copy finds the running one, asks it to show its window, and exits. */
@interface Reopen : NSObject <NSApplicationDelegate> @end
@implementation Reopen
- (BOOL)applicationShouldHandleReopen:(NSApplication *)app hasVisibleWindows:(BOOL)visible {
    start_murmur();
    return NO;
}
@end

int main(void) {
    @autoreleasepool {
        snprintf(py, sizeof py, "%s/.venv/bin/Murmur", PROJ);
        if (access(py, X_OK) != 0) snprintf(py, sizeof py, "%s/.venv/bin/python", PROJ);
        snprintf(script, sizeof script, "%s/app.py", PROJ);
        snprintf(logp, sizeof logp, "%s/Library/Logs/Murmur.log", getenv("HOME"));
        unsetenv("VIRTUAL_ENV");
        setenv("PYTHONUNBUFFERED", "1", 1);
        setenv("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", 1);  /* apps get a bare PATH */
        if (chdir(PROJ) != 0) return 1;
        posix_spawn_file_actions_init(&fa);
        posix_spawn_file_actions_addopen(&fa, 1, logp, O_WRONLY | O_CREAT | O_APPEND, 0644);
        posix_spawn_file_actions_adddup2(&fa, 1, 2);
        /* The launcher stays alive as Murmur's parent, so macOS attributes permissions to Murmur.app. */
        pid_t pid = start_murmur();
        if (pid <= 0) return 1;
        dispatch_source_t ended = dispatch_source_create(DISPATCH_SOURCE_TYPE_PROC, pid, DISPATCH_PROC_EXIT,
                                                         dispatch_get_main_queue());
        dispatch_source_set_event_handler(ended, ^{
            int status;
            waitpid(pid, &status, 0);
            exit(WIFEXITED(status) ? WEXITSTATUS(status) : 1);
        });
        dispatch_resume(ended);
        Reopen *delegate = [Reopen new];
        NSApplication.sharedApplication.delegate = delegate;
        [NSApplication.sharedApplication run];
    }
}
C
clang -O2 -fobjc-arc -framework Cocoa -DPROJ="\"$PROJ\"" -o "$APP/Contents/MacOS/Murmur" "$SRC"
xattr -cr "$APP"
# Identify the app by its bundle ID rather than the build's hash, so macOS keeps permissions across rebuilds.
codesign --force --deep --sign - -r='designated => identifier "local.murmur.dictation"' "$APP"
echo "Built $APP"
