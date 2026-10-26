"""Murmur OS integration for macOS and Windows: focus, keystrokes, sounds, login item, permissions, settings files."""
import json, os, subprocess, sys

MAC = sys.platform == "darwin"
WINDOWS = sys.platform == "win32"
BUNDLE_ID = "local.murmur.dictation"


# ---- settings / history files ----
def config_dir():
    base = (os.path.expanduser("~/Library/Application Support") if MAC else
            os.environ.get("APPDATA", os.path.expanduser("~")))
    path = os.path.join(base, "Murmur")
    os.makedirs(path, exist_ok=True)
    return path


def load_json(name, default):
    try:
        with open(os.path.join(config_dir(), name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(name, data):
    path = os.path.join(config_dir(), name)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(path + ".tmp", path)


# ---- recent recordings (kept locally for model comparison) ----
KEEP_RECORDINGS = 10


def _recordings_dir():
    path = os.path.join(config_dir(), "recordings")
    os.makedirs(path, exist_ok=True)
    return path


def save_recording(audio, meta):
    """Store a prepared 16 kHz clip with its metadata; keep only the newest KEEP_RECORDINGS."""
    import numpy as np
    stem = os.path.join(_recordings_dir(), str(int(meta["time"] * 1000)))
    np.save(stem + ".npy", audio)
    with open(stem + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    for old in list_recordings()[KEEP_RECORDINGS:]:
        _remove_recording(old["stem"])


def list_recordings():
    """Newest first: [{'stem', 'time', 'text', 'model', 'seconds'}]."""
    folder = _recordings_dir()
    out = []
    for name in os.listdir(folder):
        if name.endswith(".json"):
            stem = os.path.join(folder, name[:-5])
            try:
                with open(stem + ".json", encoding="utf-8") as f:
                    out.append({**json.load(f), "stem": stem})
            except (OSError, ValueError):
                pass
    return sorted(out, key=lambda r: r.get("time", 0), reverse=True)


def load_recording(rec):
    import numpy as np
    return np.load(rec["stem"] + ".npy")


def clear_recordings():
    for rec in list_recordings():
        _remove_recording(rec["stem"])


def _remove_recording(stem):
    for ext in (".npy", ".json"):
        try:
            os.remove(stem + ext)
        except OSError:
            pass


# ---- the app being dictated into ----
class Target:
    def __init__(self, handle, name):
        self.handle, self.name = handle, name


def frontmost():
    """The app the user is dictating into (None if it's Murmur itself)."""
    if MAC:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None or app.processIdentifier() == os.getpid():
            return None
        return Target(app, app.localizedName() or "")
    if WINDOWS:
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return None
        name = ""
        proc = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if proc:
            buf, size = ctypes.create_unicode_buffer(260), wintypes.DWORD(260)
            if kernel32.QueryFullProcessImageNameW(proc, 0, buf, ctypes.byref(size)):
                name = os.path.splitext(os.path.basename(buf.value))[0]
            kernel32.CloseHandle(proc)
        return Target(hwnd, name)
    return None


def refocus(target):
    if target is None:
        return
    if MAC:
        from AppKit import NSApplication, NSApplicationActivateIgnoringOtherApps, NSRunningApplication
        app = target.handle
        if hasattr(app, "activateFromApplication_options_"):  # macOS 14+: hand activation over cooperatively
            NSApplication.sharedApplication().yieldActivationToApplication_(app)
            app.activateFromApplication_options_(NSRunningApplication.currentApplication(), 0)
        else:
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    elif WINDOWS:
        import ctypes
        ctypes.windll.user32.SetForegroundWindow(target.handle)


# Device-dependent modifier bits (left/right) in NSEvent.modifierFlags(), and the key codes that change them.
_MAC_MODIFIERS = {58: ("alt_l", 0x20), 61: ("alt_r", 0x40), 55: ("cmd_l", 0x08), 54: ("cmd_r", 0x10),
                  59: ("ctrl_l", 0x01), 62: ("ctrl_r", 0x2000), 56: ("shift_l", 0x02), 60: ("shift_r", 0x04)}
_MAC_Z = 6


def mac_key(event):
    """(name, is_down) for a macOS key event Murmur cares about, else None."""
    from AppKit import NSEventTypeFlagsChanged, NSEventTypeKeyDown, NSEventTypeKeyUp
    kind, code = event.type(), event.keyCode()
    if kind == NSEventTypeFlagsChanged and code in _MAC_MODIFIERS:
        name, bit = _MAC_MODIFIERS[code]
        return name, bool(event.modifierFlags() & bit)
    if kind in (NSEventTypeKeyDown, NSEventTypeKeyUp) and code == _MAC_Z and not event.isARepeat():
        return "z", kind == NSEventTypeKeyDown
    return None


def watch_keys(on_key):
    """Call on_key(name, is_down) for modifier keys ('alt_r', 'cmd_l', ...) and 'z', in any app.
    macOS: native NSEvent monitors on the main thread. (pynput's listener thread calls a keyboard-layout API that
    newer macOS only allows on the main thread, which crashes the app.) Windows: pynput.
    Returns objects that must be kept alive."""
    if MAC:
        from AppKit import NSEvent, NSEventMaskFlagsChanged, NSEventMaskKeyDown, NSEventMaskKeyUp

        def handle(event):
            key = mac_key(event)
            if key:
                on_key(*key)

        mask = NSEventMaskFlagsChanged | NSEventMaskKeyDown | NSEventMaskKeyUp
        return (NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, handle),   # other apps
                NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, lambda e: (handle(e), e)[1]))  # Murmur
    from pynput import keyboard

    def name(key):
        if getattr(key, "vk", None) == 0x5A:
            return "z"
        return getattr(key, "name", None)

    listener = keyboard.Listener(on_press=lambda k: name(k) and on_key(name(k), True),
                                 on_release=lambda k: name(k) and on_key(name(k), False))
    listener.start()
    return listener


def send_shortcut(char):
    """Press Cmd+char (Mac) / Ctrl+char (Windows) in the focused app, e.g. 'v' to paste, 'z' to undo."""
    if MAC:
        from Quartz import CGEventCreateKeyboardEvent, CGEventPost, CGEventSetFlags, kCGEventFlagMaskCommand, \
            kCGHIDEventTap
        code = {"v": 9, "z": 6}[char]  # QWERTY key positions (right for QWERTY/QWERTZ/AZERTY-style layouts)
        for down in (True, False):
            event = CGEventCreateKeyboardEvent(None, code, down)
            CGEventSetFlags(event, kCGEventFlagMaskCommand)
            CGEventPost(kCGHIDEventTap, event)
        return
    from pynput import keyboard
    kb = keyboard.Controller()
    with kb.pressed(keyboard.Key.cmd if MAC else keyboard.Key.ctrl):
        kb.tap(char)


# ---- sounds ----
def play(kind):
    """kind: 'start', 'done' or 'error'. Non-blocking."""
    try:
        if MAC:
            name = {"start": "Tink", "done": "Pop", "error": "Basso"}[kind]
            subprocess.Popen(["afplay", "-v", "0.4", f"/System/Library/Sounds/{name}.aiff"])
        elif WINDOWS:
            import winsound
            winsound.MessageBeep({"start": winsound.MB_OK, "done": winsound.MB_ICONASTERISK,
                                  "error": winsound.MB_ICONHAND}[kind])
    except Exception:
        pass


# ---- start at login ----
def _launch_agent():
    return os.path.expanduser(f"~/Library/LaunchAgents/{BUNDLE_ID}.plist")


def set_start_at_login(enabled):
    if MAC:
        path = _launch_agent()
        if enabled:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{BUNDLE_ID}</string>
<key>ProgramArguments</key><array><string>/usr/bin/open</string><string>-b</string><string>{BUNDLE_ID}</string></array>
<key>RunAtLoad</key><true/>
</dict></plist>
""")
        elif os.path.exists(path):
            os.remove(path)
    elif WINDOWS:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                             winreg.KEY_SET_VALUE)
        if enabled:
            pyw = sys.executable.replace("python.exe", "pythonw.exe")
            winreg.SetValueEx(key, "Murmur", 0, winreg.REG_SZ,
                              f'"{pyw}" "{os.path.abspath(sys.argv[0])}"')
        else:
            try:
                winreg.DeleteValue(key, "Murmur")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)


# ---- permissions (macOS) ----
PRIVACY_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_"


def mic_status():
    """'ok', 'denied', 'ask' (not asked yet) or 'none' (no microphone)."""
    if MAC:
        try:
            from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
            return {3: "ok", 0: "ask"}.get(AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio),
                                           "denied")
        except ImportError:
            return "ok"
    import sounddevice as sd
    return "ok" if any(d["max_input_channels"] > 0 for d in sd.query_devices()) else "none"


def request_mic():
    if MAC and mic_status() == "ask":
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVMediaTypeAudio, lambda granted: None)
    else:
        open_privacy("Microphone")


def input_monitoring_ok():
    if not MAC:
        return True
    from Quartz import CGPreflightListenEventAccess
    return bool(CGPreflightListenEventAccess())


def request_input_monitoring():
    from Quartz import CGRequestListenEventAccess
    CGRequestListenEventAccess()
    open_privacy("ListenEvent")


def accessibility_ok():
    if not MAC:
        return True
    from ApplicationServices import AXIsProcessTrusted
    return bool(AXIsProcessTrusted())


def request_accessibility():
    from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
    AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    open_privacy("Accessibility")


def open_privacy(pane):
    if MAC:
        subprocess.Popen(["open", PRIVACY_URL + pane])
    elif WINDOWS and pane == "Microphone":
        os.startfile("ms-settings:privacy-microphone")


def open_url(url):
    if MAC:
        subprocess.Popen(["open", url])
    elif WINDOWS:
        os.startfile(url)


def system_dark_mode():
    if MAC:
        try:
            from AppKit import NSUserDefaults
            return NSUserDefaults.standardUserDefaults().stringForKey_("AppleInterfaceStyle") == "Dark"
        except Exception:
            return False
    if WINDOWS:
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        except OSError:
            return False
    return False
