"""Murmur — private push-to-talk dictation for macOS and Windows.
Hold the hotkey (Right-Option on Mac, Right-Ctrl on Windows), speak, release -> text is cleaned up and pasted at the
cursor. Double-tap for hands-free; add Shift for command mode. Fully local: Whisper (STT) + Ollama (cleanup).

Tk runs the event loop (and the Windows recording pill); the window itself is a local web app (webui.py + ui/),
shown in a native frosted window on macOS (macwindow.py) and as an Edge app window on Windows."""
import os, sys

if sys.stdout is None or sys.stderr is None:  # pythonw.exe (Windows shortcut) has no console; progress bars need one
    sys.stdout = sys.stderr = open(os.path.join(os.path.expanduser("~"), "murmur.log"), "a", buffering=1,
                                   encoding="utf-8")

import json, math, queue, shutil, subprocess, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk

import numpy as np
import sounddevice as sd

import engine
import learning
import system
import webui
from system import MAC

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = "0.4.0"
HOTKEY_CHOICES = ({"alt_r": "Right Option ⌥", "cmd_r": "Right Command ⌘", "ctrl_r": "Right Control ⌃"} if MAC else
                  {"ctrl_r": "Right Ctrl", "shift_r": "Right Shift", "alt_r": "Right Alt (may open app menus)"})
UNDO_NAME = "⌃⌘Z" if MAC else "Ctrl+Alt+Z"
MODIFIERS = {"cmd", "cmd_l", "cmd_r", "ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r", "alt_gr", "shift", "shift_l",
             "shift_r"}
TAP_MAX = 0.3      # a press shorter than this is a tap
DOUBLE_TAP = 0.35  # second tap within this window -> hands-free
MAX_RECORDING = 10 * 60  # seconds; stops a hands-free recording that was forgotten
IDLE_UNLOAD = 10 * 60    # memory saver: unload the speech model after this long without dictating
TYPING_WPM, SPEAKING_WPM = 40, 130  # for the "time saved" estimate
DEFAULTS = {
    "hotkey": "alt_r" if MAC else "ctrl_r", "mic": "", "model": "whisper-accurate", "language": "en",
    "style": "clean", "live_preview": True, "sounds": True, "auto_paste": True, "restore_clipboard": True,
    "voice_commands": True, "self_corrections": True, "start_minimized": False, "start_at_login": False, "show_in_dock": True,
    "memory_saver": True, "keep_recordings": True, "learn": True,
    "dictionary": "# One word per line, or 'wrong -> right'\n",
    "app_rules": ("# App name = style (raw, clean, formal, bullets)\nTerminal = raw\niTerm2 = raw\n" if MAC else
                  "# App name = style (raw, clean, formal, bullets)\nWindowsTerminal = raw\ncmd = raw\n"),
    "first_run": True,
}
BOOL_SETTINGS = {k for k, v in DEFAULTS.items() if isinstance(v, bool)} - {"first_run"}


class Murmur:
    def __init__(self):
        system.protect_files()
        self.settings = {**DEFAULTS, **system.load_json("settings.json", {})}
        self.settings["model"] = engine.model_key(self.settings["model"])
        engine.KEEP_ALIVE = "10m" if self.settings["memory_saver"] else "30m"
        self.history = system.load_json("history.json", [])
        self.learner = learning.Learner()
        self.watch = None  # a pasted passage being checked for corrections
        self.latest = self.latest_original = self.history[0]["text"] if self.history else ""
        self.root = tk.Tk()  # event loop + clipboard; the visible window is the web interface
        self.root.withdraw()
        self.events, self.jobs = queue.Queue(), queue.Queue()
        # All model work (load, transcribe, preview, compare) runs on this one thread: some MLX models
        # only work on the thread that loaded them, and it also serializes access to the model.
        self.stt_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")
        self.stt, self.stt_error, self.stt_status, self.last_used = None, "", "loading", time.monotonic()
        self.frames, self.rate, self.stream, self.level, self.rec_start = [], engine.SR, None, 0.0, 0.0
        self.recording, self.state, self.session = False, "idle", 0
        self.press_t, self.ignore_up, self.tap_timer, self.discard = 0.0, False, None, False
        self.target, self.last_paste, self.preview, self.preview_busy = None, None, "", False
        self.command, self.selection, self.busy = False, "", 0  # busy: dictations still being processed
        self.hotkey_down, self.mods, self.undo_armed, self.keys_seen = False, set(), False, False
        self.hud_visible, self.ollama_pulling = False, False
        self.status_text, self.toast = "Loading speech model…", None
        self.setup, self.mics, self.compare = [], [], {"running": False, "status": "", "results": None}
        self.dirty, self.window = True, None
        self.build_hud()
        self.menubar = None
        if MAC:  # icon in the menu bar, next to Wi-Fi etc.
            import menubar
            self.menubar = menubar.MenuBar(on_open=self.show_window, on_settings=lambda: self.show_window("settings"),
                                           on_undo=lambda: self.events.put(("undo",)), on_quit=self.quit)
            system.set_show_in_dock(self.settings["show_in_dock"])
            system.set_dock_icon(os.path.join(HERE, "assets", "icon_1024.png"))
        self.ui = webui.Server(lambda action: self.events.put(("ui", action)))
        system.save_json("instance.json", {"port": self.ui.port, "token": self.ui.token, "pid": os.getpid()})
        if MAC:
            import macwindow
            self.window = macwindow.MainWindow(self.ui.url)
            self.root.createcommand("::tk::mac::ReopenApplication", self.show_window)  # Dock icon click
            self.root.createcommand("::tk::mac::Quit", self.quit)  # Cmd+Q and the Dock's Quit
        self.refresh_mics()
        self.refresh_setup()
        self.root.after(50, self.poll)
        self.root.after(100, self.tick)
        self.stt_thread.submit(self.load_model)
        threading.Thread(target=self.worker, daemon=True).start()
        threading.Thread(target=engine.warm_ollama, daemon=True).start()
        self.key_watch = system.watch_keys(self.on_key)  # keep a reference, or the macOS monitors stop
        if self.settings["first_run"]:
            self.save_setting("first_run", False)
            self.show_window("setup")
        elif not self.settings["start_minimized"]:
            self.show_window()

    # ================= window =================
    def show_window(self, page=None):
        if self.window is not None:
            self.window.show(page)
        else:  # Windows: an app-style Edge window (Edge ships with Windows 10/11); any browser as a fallback
            url = self.ui.url + (f"#{page}" if page else "")
            edge = shutil.which("msedge") or next((p for p in (
                os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe")) if os.path.exists(p)), None)
            if edge:
                subprocess.Popen([edge, f"--app={url}", "--window-size=1180,780"])
            else:
                import webbrowser
                webbrowser.open(url)

    def quit(self):
        self.root.destroy()  # the instance file is removed when mainloop returns (see __main__)

    def forget_instance(self):
        """Remove instance.json if it's ours, so a later launch doesn't try to hand off to a dead copy."""
        if (system.load_json("instance.json", {}) or {}).get("pid") == os.getpid():
            try:
                os.remove(os.path.join(system.config_dir(), "instance.json"))
            except OSError:
                pass

    # ================= settings =================
    def save_setting(self, key, value):
        self.settings[key] = value
        system.save_json("settings.json", self.settings)
        self.dirty = True

    def apply_setting(self, key, value):
        """A setting changed in the interface: save it and apply any side effects."""
        if key not in DEFAULTS:
            return
        old = self.settings.get(key)
        self.save_setting(key, value)
        if key in ("model", "language") and value != old:
            self.change_model_same()
        elif key == "keep_recordings" and not value:
            system.clear_recordings()
        elif key == "start_at_login":
            system.set_start_at_login(value)
        elif key == "show_in_dock":
            system.set_show_in_dock(value)
        elif key == "memory_saver":
            engine.KEEP_ALIVE = "10m" if value else "30m"

    def vocabulary(self):
        """(words, fixes) for Whisper: the user's Dictionary plus what Murmur learned. Main thread only."""
        words, fixes = engine.parse_dictionary(self.settings["dictionary"])
        if self.settings["learn"]:
            words = words + self.learner.hints()
            fixes = self.learner.fixes() + fixes  # the user's own fixes are applied last, so they win
        return words, fixes

    def refresh_mics(self):
        try:
            host = sd.query_devices(kind="input")["hostapi"]
            names = [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0 and d["hostapi"] == host]
        except Exception:
            names = []
        self.mics = list(dict.fromkeys(names))
        self.dirty = True

    def mic_index(self):
        name = self.settings["mic"]
        if not name:
            return None
        for i, d in enumerate(sd.query_devices()):
            if d["name"] == name and d["max_input_channels"] > 0:
                return i
        return None  # unplugged -> system default

    # ================= state for the interface =================
    def set_status(self, msg, hud=None):
        self.status_text = msg
        if self.menubar is not None:
            self.menubar.set_status(msg if len(msg) <= 60 else msg[:59] + "…")
        self.hud_show(hud) if hud else self.hud_hide()
        self.dirty = True

    def notify(self, msg):
        """A short toast in the window."""
        self.toast = {"id": time.time(), "text": msg}
        self.dirty = True

    def build_state(self):
        today = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        count = lambda h: len(h["text"].split())
        words_today = sum(count(h) for h in self.history if h["time"] >= today)
        total_words = sum(count(h) for h in self.history)
        mode = (("command" if self.command else "listening") if self.recording else
                self.hud_mode if self.hud_visible else "idle")
        fixes = self.learner.fixes()
        user_words, user_fixes = engine.parse_dictionary(self.settings["dictionary"])
        return {
            "version": VERSION, "platform": "mac" if MAC else "windows",
            "status": self.status_text, "mode": mode, "locked": self.state == "locked",
            "level": round(self.level, 3) if self.recording else 0, "preview": self.preview if self.recording else "",
            "model_status": self.stt_status, "toast": self.toast,
            "latest": self.latest,
            "history": [{**h, "i": i} for i, h in enumerate(self.history[:300])],
            "stats": {"words_today": words_today, "dictations_today": sum(h["time"] >= today for h in self.history),
                      "total_words": total_words, "dictations": len(self.history),
                      "minutes_saved": round(total_words * (1 / TYPING_WPM - 1 / SPEAKING_WPM))},
            "settings": {k: v for k, v in self.settings.items() if k != "first_run"},
            "options": {"hotkeys": HOTKEY_CHOICES, "models": engine.MODELS, "languages": engine.LANGUAGES,
                        "styles": engine.STYLES, "mics": self.mics, "undo": UNDO_NAME},
            "dictionary": {"words": len(user_words), "fixes": len(user_fixes)},
            "learned": {"fixes": fixes[:40],
                        "words": [w for w in self.learner.hints() if w not in {r for _, r in fixes}][:40]},
            "setup": self.setup, "setup_ok": not any(i["ok"] is False and i["required"] for i in self.setup),
            "compare": self.compare,
        }

    # ================= actions from the interface (Tk thread) =================
    def ev_ui(self, action):
        kind = action.get("type")
        if kind == "set":
            self.apply_setting(action["key"], action["value"])
        elif kind == "copy":
            self.copy(action["text"])
            self.notify("Copied")
        elif kind == "copy_latest":
            edited = action.get("text", "").strip()
            if self.settings["learn"] and self.latest_original and edited != self.latest_original:
                self.learn_from(self.latest_original, edited)  # corrected in Murmur's own box
                self.latest_original = self.latest = edited
            self.copy(edited)
            self.notify("Copied")
        elif kind == "undo":
            self.ev_undo()
        elif kind == "delete_history":  # by time stamp: positions shift when a new dictation arrives
            self.history = [h for h in self.history if h["time"] != action["time"]]
            system.save_json("history.json", self.history)
        elif kind == "clear_history":
            self.history = []
            system.save_json("history.json", self.history)
        elif kind == "save_dictionary":
            self.save_setting("dictionary", action["text"].rstrip() + "\n")
        elif kind == "save_rules":
            self.save_setting("app_rules", action["text"].rstrip() + "\n")
        elif kind == "forget_fix":
            self.learner.forget_fix(action["wrong"], action["right"])
        elif kind == "forget_word":
            self.learner.forget_word(action["word"])
        elif kind == "reset_learning":
            self.learner.reset()
        elif kind == "setup":
            self.setup_action(action["id"])
        elif kind == "refresh":
            self.refresh_setup()
            self.refresh_mics()
        elif kind == "compare":
            self.start_compare()
        elif kind == "restart":
            self.restart()
        elif kind == "quit":
            self.quit()
        elif kind == "show":  # a second launch asked this instance to show its window
            self.show_window()
        self.dirty = True

    # ================= setup checklist =================
    def refresh_setup(self):
        items = []
        if self.stt_status == "error":
            items.append(dict(id="model", name="Speech model", ok=False, detail=self.stt_error[:90], button="Retry"))
        else:
            items.append(dict(id="model", name="Speech model", ok=None if self.stt_status == "loading" else True,
                              detail={"loading": "Loading… (the first launch downloads it)", "ready": "Loaded",
                                      "sleeping": "Resting to save memory, wakes up when you dictate"}[self.stt_status],
                              button=None))
        neural = engine.silero() is not None
        items.append(dict(id="vad", name="Speech detection", ok=neural, button=None,
                          detail="Silero VAD (trained speech detector)" if neural else
                          "Basic detector: assets/silero_vad.onnx or onnxruntime is missing"))
        mic = system.mic_status()
        items.append(dict(id="mic", name="Microphone", ok=mic == "ok", button=None if mic == "ok" else "Allow",
                          detail={"ok": "Allowed", "ask": "Not asked yet", "denied": "Blocked in System Settings",
                                  "none": "No microphone found"}[mic]))
        if MAC:
            ok = system.accessibility_ok()
            items.append(dict(id="accessibility", name="Accessibility", ok=ok, button=None if ok else "Open Settings",
                              detail="Allowed" if ok else "Needed to paste, undo and learn from corrections"))
            granted = system.input_monitoring_ok()
            items.append(dict(id="input", name="Input Monitoring", ok=True if granted or self.keys_seen else None,
                              button=None if granted or self.keys_seen else "Open Settings", required=False,
                              detail="Allowed" if granted else "Not needed: the hotkey already works" if self.keys_seen
                              else "Only needed if the hotkey doesn't respond"))
        st = "pulling" if self.ollama_pulling else engine.ollama_status()
        items.append(dict(id="ollama", name="AI cleanup (Ollama)", ok=None if st == "pulling" else st == "ok",
                          required=False, button={"no-model": "Download",
                                                  "not-running": "Start" if self.ollama_path() else "Get Ollama"}.get(st),
                          detail={"ok": f"Ready ({engine.LLM_MODEL})", "pulling": "Downloading the model…",
                                  "no-model": f"Model {engine.LLM_MODEL} isn't downloaded yet",
                                  "not-running": "Optional: basic cleanup works without it"}[st]))
        for item in items:
            item.setdefault("required", True)
        self.setup = items
        self.dirty = True

    def setup_action(self, item):
        if item == "model":
            self.change_model_same()
        elif item == "mic":
            system.request_mic()
        elif item == "accessibility":
            system.request_accessibility()
        elif item == "input":
            system.request_input_monitoring()
        elif item == "ollama":
            (self.pull_model if engine.ollama_status() == "no-model" else self.start_ollama)()
        self.root.after(1500, self.refresh_setup)

    def ollama_path(self):
        return shutil.which("ollama") or next((p for p in ("/opt/homebrew/bin/ollama", "/usr/local/bin/ollama",
                                               os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"))
                                               if os.path.exists(p)), None)

    def start_ollama(self):
        path = self.ollama_path()
        if not path:
            return system.open_url("https://ollama.com/download")
        subprocess.Popen([path, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def pull_model(self):
        path = self.ollama_path()
        if not path or self.ollama_pulling:
            return
        self.ollama_pulling = True

        def run():
            subprocess.run([path, "pull", engine.LLM_MODEL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.ollama_pulling = False
            self.events.put(("setup_changed",))
        threading.Thread(target=run, daemon=True).start()

    def restart(self):
        if MAC:
            from AppKit import NSBundle
            bundle = NSBundle.mainBundle()
            if bundle.bundleIdentifier() == system.BUNDLE_ID:  # running inside Murmur.app: restart as the app itself
                os.execv(bundle.executablePath(), [bundle.executablePath()])
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def ev_setup_changed(self):
        self.refresh_setup()

    # ================= learning from corrections =================
    def start_watch(self, pasted):
        """Remember the field Murmur just pasted into, to see later whether the user corrected the text."""
        found = system.focused_text_field()
        if found is None:
            return
        self.watch = {"field": found[0], "cursor": found[1], "pasted": pasted, "last": None,
                      "until": time.monotonic() + 90}
        self.root.after(10_000, self.check_watch, self.watch)

    def check_watch(self, watch, final=False):
        """Learn once the edit has settled (unchanged between two checks), at the next dictation, or after 90 s."""
        if watch is not self.watch:
            return
        text = system.read_field(watch["field"])
        region = learning.locate(watch["pasted"], text, near=watch["cursor"]) if text else None
        if region is None:  # field closed, cleared or the passage was deleted: nothing to learn
            self.watch = None
            return
        changed = region != watch["pasted"]
        if changed and (final or region == watch["last"]):
            self.learn_from(watch["pasted"], region)
            self.watch = None
        elif final or time.monotonic() > watch["until"]:
            if not changed:
                self.learner.observe_text(region)  # kept as is: the user accepted these words
            self.watch = None
        else:
            watch["last"] = region
            self.root.after(10_000, self.check_watch, watch)

    def learn_from(self, original, corrected):
        new = self.learner.observe_correction(original, corrected)
        self.learner.observe_text(corrected)
        if new:
            self.notify("Learned: " + ", ".join(f"{a} → {b}" for a, b in new))
        self.dirty = True

    # ================= clipboard =================
    def copy(self, text):
        if not system.copy_text(text):
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()

    def save_clipboard(self):
        if MAC:
            return system.clipboard_snapshot()
        try:
            return {"text": self.root.clipboard_get()}
        except tk.TclError:
            return None

    def restore_clipboard(self, saved, ours):
        if MAC:
            return system.clipboard_restore(saved)
        try:
            if saved is not None and self.root.clipboard_get() == ours:  # only if the user hasn't copied since
                self.root.clipboard_clear()
                self.root.clipboard_append(saved["text"])
        except tk.TclError:
            pass

    # ================= on-screen pill =================
    # macOS: frosted-glass native pill (pill.py). Windows: a small Tk canvas capsule, animated at ~30 fps.
    PILL_H, PILL_BARS, PILL_FPS_MS = 34, 9, 33
    PILL_BG, PILL_KEY = "#141416", "#010203"  # PILL_KEY: transparent color on Windows

    def build_hud(self):
        self.hud_mode, self.hud_size, self.level_smooth, self.anim_on = None, None, 0.0, False
        self.native_pill = None
        if MAC:
            import pill
            self.native_pill = pill.MacPill()
            return
        self.hud = tk.Toplevel(self.root)
        self.hud.overrideredirect(True)
        self.hud.attributes("-topmost", True)
        self.hud.attributes("-transparentcolor", self.PILL_KEY)
        self.hud.configure(bg=self.PILL_KEY)
        self.pill = tk.Canvas(self.hud, width=110, height=self.PILL_H, bg=self.PILL_KEY, highlightthickness=0, bd=0)
        self.pill.pack()
        self.hud.withdraw()

    def hud_show(self, mode):
        """mode: 'listening', 'transcribing' or 'cleaning'."""
        self.hud_mode = mode
        if self.native_pill is not None:
            self.native_pill.show(mode)
            self.hud_visible = True
        else:
            self.pill_layout()
            if not self.hud_visible:
                self.hud_visible = True
                self.hud.deiconify()
                self.hud.lift()
                system.refocus(self.target)  # showing the pill activated Murmur; hand focus back to the user's app
        if not self.anim_on:
            self.anim_on = True
            self.animate()

    def hud_hide(self):
        self.hud_visible, self.hud_mode = False, None
        if self.menubar is not None:
            self.menubar.set_state("idle")
        if self.native_pill is not None:
            self.native_pill.hide()
        else:
            self.hud.withdraw()

    def pill_layout(self):
        """Windows pill: size the capsule for the current mode (wider when live text is shown)."""
        h = self.PILL_H
        text = self.hud_mode == "listening" and self.settings["live_preview"] and bool(self.preview)
        w = 330 if text else (112 if self.hud_mode == "listening" else 76)
        if self.hud_size == (w, self.hud_mode, text):
            return
        self.hud_size = (w, self.hud_mode, text)
        c = self.pill
        c.delete("all")
        c.configure(width=w, height=h)
        c.create_line(h / 2, h / 2, w - h / 2, h / 2, width=h, capstyle="round", fill=self.PILL_BG)  # capsule
        self.pill_items = []
        if self.hud_mode == "listening":
            c.create_oval(13, h / 2 - 4, 21, h / 2 + 4, fill="#ff453a", outline="", tags="dot")
            self.pill_items = [c.create_line(0, 0, 0, 0, width=3, capstyle="round", fill="white")
                               for _ in range(self.PILL_BARS)]
            if text:
                c.create_text(104, h / 2, anchor="w", fill="#b4b4bb", font=("", 12), tags="preview")
        else:
            self.pill_items = [c.create_oval(0, 0, 0, 0, outline="") for _ in range(3)]
        x = (self.hud.winfo_screenwidth() - w) // 2
        self.hud.geometry(f"{w}x{h}+{x}+{self.hud.winfo_screenheight() - 140}")

    def animate(self):
        if not self.hud_visible:
            self.anim_on = False
            return
        t = time.monotonic()
        locked, command = self.state == "locked", self.command and self.hud_mode == "listening"
        if self.menubar is not None:
            self.menubar.set_state(self.hud_mode or "idle", locked, command)
        if self.native_pill is not None:
            text = self.preview.strip() if self.hud_mode == "listening" and self.settings["live_preview"] else ""
            self.native_pill.update(t, self.level, locked, text, command)
            self.root.after(16, self.animate)  # ~60 fps; Core Animation smooths between frames
            return
        h, c = self.PILL_H, self.pill
        if self.hud_mode == "listening":
            self.pill_layout()  # widens once live text arrives
            self.level_smooth += (self.level - self.level_smooth) * (0.5 if self.level > self.level_smooth else 0.15)
            c.itemconfigure("dot", fill="#bf5af2" if command else "#ffb340" if locked else "#ff453a")
            for i, bar in enumerate(self.pill_items):
                center = 1 - abs(i - (self.PILL_BARS - 1) / 2) / self.PILL_BARS  # taller in the middle
                wobble = 0.55 + 0.45 * math.sin(t * 9 + i * 1.3)
                half = 2 + 11 * min(1.0, self.level_smooth * 1.6) * center * wobble
                x = 32 + i * 7
                c.coords(bar, x, h / 2 - half, x, h / 2 + half)
            if self.preview:
                tail = self.preview.strip()
                c.itemconfigure("preview", text=("…" + tail[-30:]) if len(tail) > 30 else tail)
        elif self.hud_mode in ("transcribing", "cleaning"):
            bright, dim = ("#ffffff", "#77777f") if self.hud_mode == "transcribing" else ("#b9a2ff", "#5f5389")
            w = self.hud_size[0]
            for i, dot in enumerate(self.pill_items):
                pulse = 0.5 + 0.5 * math.sin(t * 7 - i * 2.1)  # evenly spaced, so one dot is always lit
                r = 2.5 + 1.5 * pulse
                x = w / 2 + (i - 1) * 13
                c.coords(dot, x - r, h / 2 - r, x + r, h / 2 + r)
                c.itemconfigure(dot, fill=bright if pulse > 0.5 else dim)
        self.root.after(self.PILL_FPS_MS, self.animate)

    def tick(self):
        """Every 100 ms: stop forgotten recordings, rest the model when idle, and publish state to the window."""
        now = time.monotonic()
        if self.recording and now - self.rec_start > MAX_RECORDING:
            self.state = "idle"
            self.finish_recording()
        if self.recording and self.hud_mode != "listening":
            self.hud_show("listening")
        if (self.settings["memory_saver"] and self.stt_status == "ready" and not self.recording and not self.busy
                and now - self.last_used > IDLE_UNLOAD):
            self.stt_status = "sleeping"
            self.stt_thread.submit(self.unload_model)
            self.refresh_setup()
        if self.dirty or self.recording:
            self.dirty = False
            self.ui.publish(self.build_state())
        self.root.after(100, self.tick)

    # ================= events: other threads -> queue -> Tk thread =================
    def poll(self):
        while not self.events.empty():
            kind, *args = self.events.get()
            try:
                getattr(self, "ev_" + kind)(*args)
            except Exception as e:  # never let one failure stop event handling
                print(f"error in {kind}: {e!r}")
                self.stop_stream()
                self.state = "idle"
                self.set_status(f"Error: {e}")
                self.sound("error")
        self.root.after(50, self.poll)

    def sound(self, kind):
        if self.settings["sounds"]:
            system.play(kind)

    # ================= speech model (stt thread) =================
    def load_model(self):
        self.stt = None
        engine.free_memory()
        try:
            self.stt = engine.load_stt(self.settings["model"], self.settings["language"])
            self.stt_error, self.stt_status, self.last_used = "", "ready", time.monotonic()
            self.events.put(("ready",))
        except Exception as e:
            self.stt_error, self.stt_status = str(e), "error"
            self.events.put(("status", f"Could not load speech model: {e}", None))

    def unload_model(self):
        """Memory saver: free the speech model's memory; the next dictation loads it again."""
        if self.stt_status == "sleeping":
            self.stt = None
            engine.free_memory()

    def transcribe(self, audio, prompt, still_wanted=None):
        """Run the current model on the stt thread and wait for the text. Recordings made while a model is
        loading wait for it, because the load is queued on the same thread."""
        def run():
            if still_wanted and not still_wanted():
                return None
            if self.stt is None:
                raise RuntimeError(self.stt_error or "speech model isn't loaded")
            self.last_used = time.monotonic()
            return self.stt(audio, prompt)
        return self.stt_thread.submit(run).result()

    def change_model_same(self):
        self.stt_error, self.stt_status = "", "loading"
        self.set_status("Loading speech model…")
        self.stt_thread.submit(self.load_model)
        self.refresh_setup()

    def ev_ready(self):
        self.refresh_setup()
        missing = [i["name"] for i in self.setup if i["ok"] is False and i["required"]]
        if missing:
            self.set_status("Setup needed: " + ", ".join(missing))
        elif not self.recording and not self.busy:  # don't hide "Transcribing…" of a dictation still in progress
            self.set_status("Ready")

    def ev_status(self, msg, hud):
        self.set_status(msg, hud)

    # ---- model comparison on the user's own recordings ----
    def start_compare(self):
        if self.compare["running"]:
            return
        recs = system.list_recordings()
        if not recs:
            self.compare = {"running": False, "status": "No recordings yet. Dictate a few times first.",
                            "results": None}
            return
        self.compare = {"running": True, "status": "Starting…", "results": None}
        prompt = engine.whisper_prompt(self.vocabulary()[0])
        threading.Thread(target=self.run_compare, args=(recs, prompt), daemon=True).start()

    def run_compare(self, recs, prompt):
        audio = [system.load_recording(r) for r in recs]
        results, timing = {}, {}
        run_on_stt = lambda fn, *a: self.stt_thread.submit(fn, *a).result()
        for key, name in engine.MODELS.items():
            self.events.put(("compare_status", f"Loading {name.split(' (')[0]}…"))
            current = key == self.settings["model"] and self.stt is not None
            try:
                stt = self.stt if current else run_on_stt(engine.load_stt, key, self.settings["language"])
            except Exception as e:
                results[key] = [f"(could not load: {e})"] * len(recs)
                continue
            self.events.put(("compare_status", f"Running {name.split(' (')[0]}…"))
            texts, total = [], 0.0
            for a in audio:
                t0 = time.time()
                texts.append(run_on_stt(stt, a, prompt))
                total += time.time() - t0
            results[key], timing[key] = texts, round(total / len(recs), 2)
            del stt
            if not current:
                run_on_stt(engine.free_memory)
        rows = [{"time": r["time"], "seconds": r.get("seconds"), "texts": {k: v[i] for k, v in results.items()}}
                for i, r in enumerate(recs)]
        self.events.put(("compare_done", {"rows": rows, "timing": timing}))

    def ev_compare_status(self, msg):
        self.compare = {**self.compare, "status": msg}
        self.dirty = True

    def ev_compare_done(self, results):
        self.compare = {"running": False, "status": "Done. Which one matches what you said?", "results": results}
        self.dirty = True

    # ================= hotkey: hold = push-to-talk, double-tap = hands-free, + Shift = command mode =================
    def ev_key_down(self, t, shift=False):
        if self.stt_status == "error":
            return self.set_status(f"Speech model failed to load: {self.stt_error}")
        if self.stt_status == "sleeping":  # memory saver: wake the model up; it loads while the user speaks
            self.stt_status = "loading"
            self.stt_thread.submit(self.load_model)
        if self.state == "idle":
            self.start_recording(command=shift)
            self.state, self.press_t = "holding", t
        elif self.state == "tap_pending":  # second tap -> lock recording on
            self.root.after_cancel(self.tap_timer)
            self.state, self.ignore_up = "locked", True
        elif self.state == "locked":  # tap while hands-free -> finish
            self.state, self.ignore_up = "idle", True
            self.finish_recording()

    def ev_key_up(self, t):
        if self.ignore_up:
            self.ignore_up = False
            return
        if self.state != "holding":
            return
        if t - self.press_t < TAP_MAX:
            self.state = "tap_pending"
            self.tap_timer = self.root.after(int(DOUBLE_TAP * 1000), self.single_tap_timeout)
        else:
            self.state = "idle"
            self.finish_recording()

    def single_tap_timeout(self):
        if self.state == "tap_pending":
            self.state = "idle"
            self.stop_stream()
            self.set_status("Ready — hold to talk, double-tap for hands-free")

    def ev_undo_armed(self):
        if self.recording:  # the undo shortcut overlapped the hotkey; don't keep that recording
            self.discard = True

    def ev_undo(self):
        if self.recording:
            return
        if self.last_paste is None:
            return self.notify("Nothing to undo")
        target, self.last_paste = self.last_paste, None
        system.refocus(target)
        self.root.after(150, lambda: system.send_shortcut("z"))
        self.set_status("Undid the last paste")

    # ================= recording =================
    def start_recording(self, command=False):
        if self.watch is not None:
            self.check_watch(self.watch, final=True)
        self.target, self.frames, self.preview, self.discard = system.frontmost(), [], "", False
        self.command, self.selection = command, system.selected_text() if command else ""
        self.session += 1

        def callback(data, *_):
            self.frames.append(data[:, 0].copy())
            self.level = engine.level(data[:, 0])
        self.stream = self.open_microphone(callback)
        self.rate = int(self.stream.samplerate)
        self.recording, self.rec_start = True, time.monotonic()
        self.set_status(("Command: say what to do with the selected text" if self.selection else
                         "Command: say what to write") if command else "Listening…", "listening")
        self.sound("start")
        self.root.after(1000, self.schedule_preview, self.session)

    def open_microphone(self, callback):
        """Start recording. PortAudio reads the device list once at startup, so after AirPods or a headset connect or
        disconnect, opening the microphone fails; re-read the devices and try once more."""
        for attempt in range(2):
            try:
                stream = sd.InputStream(device=self.mic_index(), channels=1, dtype="float32", callback=callback)
                stream.start()
                return stream
            except sd.PortAudioError as e:
                if attempt:
                    raise RuntimeError("couldn't open the microphone — check that it's connected") from e
                sd._terminate()
                sd._initialize()
                self.refresh_mics()

    def stop_stream(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
        self.stream, self.recording, self.level = None, False, 0.0

    def finish_recording(self):
        self.stop_stream()
        if self.discard or not self.frames:
            self.command = False
            return self.set_status("Cancelled")
        style = self.style_for(self.target)
        self.set_status("Transcribing…", "transcribing")
        self.busy += 1
        self.jobs.put((np.concatenate(self.frames), self.rate, style, self.settings["auto_paste"], self.target,
                       *self.vocabulary(), self.command, self.selection))
        self.command = False

    def style_for(self, target):
        """Per-app rule if one matches the app name, else the chosen style."""
        if target is not None and target.name:
            for line in self.settings["app_rules"].splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    app, style = (s.strip() for s in line.split("=", 1))
                    if app and app.lower() in target.name.lower() and style.lower() in engine.STYLES:
                        return style.lower()
        return self.settings["style"]

    # ---- live preview ----
    def tail_audio(self, seconds):
        """The last `seconds` of the recording, without concatenating the whole (possibly long) recording."""
        need, parts = int(seconds * self.rate), []
        for block in reversed(self.frames[:]):
            parts.append(block)
            need -= len(block)
            if need <= 0:
                break
        return np.concatenate(parts[::-1])

    def schedule_preview(self, session):
        if not self.recording or session != self.session:
            return
        if self.settings["live_preview"] and not self.preview_busy and self.frames and self.stt is not None:
            self.preview_busy = True
            # Last 10 s only: keeps each preview short, so it never holds up the final transcription for long.
            prompt = engine.whisper_prompt(self.vocabulary()[0])
            threading.Thread(target=self.run_preview, args=(self.tail_audio(10), self.rate, prompt, session),
                             daemon=True).start()
        self.root.after(1000, self.schedule_preview, session)

    def run_preview(self, audio, rate, prompt, session):
        try:
            clip, _ = engine.prepare(engine.resample(audio, rate))
            if clip is not None:
                # Skipped if the user already let go: the final transcription matters more than a stale preview.
                text = self.transcribe(clip, prompt, still_wanted=lambda: self.recording and session == self.session)
                if text is not None:
                    self.events.put(("preview", text, session))
        except Exception as e:
            print(f"preview failed: {e!r}")
        finally:
            self.preview_busy = False

    def ev_preview(self, text, session):
        if self.recording and session == self.session:
            self.preview = text

    # ================= processing (one job at a time, in order) =================
    def worker(self):
        while True:
            self.process(*self.jobs.get())

    def process(self, raw, rate, style, auto_paste, target, words, fixes, command=False, selection=""):
        try:
            audio, voiced_s = engine.prepare(engine.resample(raw, rate))
            if audio is None:
                self.events.put(("failed", "Didn't hear any speech — check the microphone in Settings"))
                return
            text = self.transcribe(audio, engine.whisper_prompt(words))
            if engine.is_hallucination(text, voiced_s):
                self.events.put(("failed", "No speech detected"))
                return
            if self.settings["keep_recordings"]:
                system.save_recording(audio, {"time": time.time(), "text": text, "model": self.settings["model"],
                                              "seconds": round(len(audio) / engine.SR, 1)})
            text = engine.apply_fixes(text, fixes)
            if command:  # the speech was an instruction for the local AI, not text to paste
                self.events.put(("status", "Working on it…", "cleaning"))
                result, note = engine.run_command(text, selection)
                if result is None:
                    self.events.put(("failed", note))
                    return
                self.events.put(("result", result, "", target, auto_paste, text))
                return
            note = ""
            if style != "raw" and self.settings["self_corrections"]:  # "at 3, no wait, 4 pm" -> "at 4 pm"
                text, _ = engine.resolve_corrections(text)
            # Voice commands go first, so cleanup can't drop "new line" as filler; formal/bullet rewrites
            # restructure the text anyway, so for those the commands are applied to the result.
            line_by_line = self.settings["voice_commands"] and style in ("raw", "clean")
            if line_by_line:
                text = engine.apply_voice_commands(text)
            if style != "raw":
                self.events.put(("status", "Cleaning up…", "cleaning"))
                text, note = engine.cleanup_lines(text, style) if line_by_line else engine.cleanup(text, style)
            if self.settings["voice_commands"] and not line_by_line:
                text = engine.apply_voice_commands(text)
            self.events.put(("result", text, note, target, auto_paste, None))
        except Exception as e:
            self.events.put(("failed", f"Error: {e}"))

    def ev_failed(self, msg):
        self.busy = max(0, self.busy - 1)
        self.set_status(msg)
        self.sound("error")

    def ev_result(self, text, note, target, auto_paste, instruction=None):
        self.busy = max(0, self.busy - 1)
        entry = {"text": text, "time": time.time(), "app": target.name if target else ""}
        if instruction:
            entry["command"] = instruction
        self.history.insert(0, entry)
        del self.history[500:]
        system.save_json("history.json", self.history)
        self.latest = self.latest_original = text
        pasting = auto_paste and target is not None and system.accessibility_ok()  # macOS blocks the paste without it
        saved = self.save_clipboard() if pasting and self.settings["restore_clipboard"] else None
        self.copy(text)
        if saved is not None:
            if MAC:
                system.clipboard_mark(saved)
            self.root.after(1200, self.restore_clipboard, saved, text)  # after the paste has happened
        self.set_status(("Done" if saved is not None else "Done — copied") + (f" · {note}" if note else ""))
        self.sound("done")
        if pasting:
            system.refocus(target)
            self.root.after(150, lambda: system.send_shortcut("v"))
            self.last_paste = target
            if self.settings["learn"] and not instruction:
                self.watch = None
                self.root.after(600, self.start_watch, text)

    # ================= global keyboard (system.watch_keys: main thread on Mac, pynput thread on Windows) ==========
    def on_key(self, name, down, other_app=True):
        if other_app and not self.keys_seen:
            self.keys_seen = True  # the hotkey reaches Murmur from other apps: Input Monitoring isn't needed
            self.events.put(("setup_changed",))
        if down:
            self.on_press(name)
        else:
            self.on_release(name)

    def on_press(self, name):
        if name == self.settings["hotkey"]:
            if not self.hotkey_down:  # ignore key auto-repeat
                self.hotkey_down = True
                self.events.put(("key_down", time.monotonic(), any(m.startswith("shift") for m in self.mods)))
        if name in MODIFIERS:
            self.mods.add(name)
        elif name == "z" and self.undo_combo_held():
            self.undo_armed = True
            self.events.put(("undo_armed",))

    def on_release(self, name):
        if name == self.settings["hotkey"] and self.hotkey_down:
            self.hotkey_down = False
            self.events.put(("key_up", time.monotonic()))
        self.mods.discard(name)
        if self.undo_armed and not self.mods:  # wait until modifiers are up, so the app gets a clean Cmd/Ctrl+Z
            self.undo_armed = False
            self.events.put(("undo",))

    def undo_combo_held(self):
        ctrl = any(n.startswith("ctrl") for n in self.mods)
        return ctrl and any(n.startswith("cmd" if MAC else "alt") for n in self.mods)


def already_running():
    """If Murmur is already running, ask it to show its window and return True (so only one copy runs)."""
    info = system.load_json("instance.json", None)
    if not info:
        return False
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/action", json.dumps({"type": "show"}).encode(),
                                     {"Content-Type": "application/json", "X-Murmur-Token": info["token"]})
        urllib.request.urlopen(req, timeout=2).read()
        return True
    except Exception:
        return False


if __name__ == "__main__":
    if not already_running():
        murmur = Murmur()
        try:
            murmur.root.mainloop()
        finally:
            murmur.forget_instance()
