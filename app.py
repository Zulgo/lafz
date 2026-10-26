"""Murmur — private push-to-talk dictation for macOS and Windows.
Hold the hotkey (Right-Option on Mac, Right-Ctrl on Windows), speak, release -> text is cleaned up, shown in the window,
copied, and pasted at the cursor. Double-tap for hands-free. Fully local: Whisper (STT) + Ollama (cleanup)."""
import os, sys

if sys.stdout is None or sys.stderr is None:  # pythonw.exe (Windows shortcut) has no console; progress bars need one
    sys.stdout = sys.stderr = open(os.path.join(os.path.expanduser("~"), "murmur.log"), "a", buffering=1,
                                   encoding="utf-8")

import math, queue, shutil, subprocess, threading, time
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import ttk

import numpy as np
import sounddevice as sd

import engine
import system
from system import MAC

HERE = os.path.dirname(os.path.abspath(__file__))
HOTKEY_CHOICES = ({"alt_r": "Right Option ⌥", "cmd_r": "Right Command ⌘", "ctrl_r": "Right Control ⌃"} if MAC else
                  {"ctrl_r": "Right Ctrl", "shift_r": "Right Shift", "alt_r": "Right Alt (may open app menus)"})
UNDO_NAME = "⌃⌘Z" if MAC else "Ctrl+Alt+Z"
MODIFIERS = {"cmd", "cmd_l", "cmd_r", "ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r", "alt_gr", "shift", "shift_l",
             "shift_r"}
TAP_MAX = 0.3      # a press shorter than this is a tap
DOUBLE_TAP = 0.35  # second tap within this window -> hands-free
MAX_RECORDING = 10 * 60  # seconds; stops a hands-free recording that was forgotten
DEFAULTS = {
    "hotkey": "alt_r" if MAC else "ctrl_r", "mic": "", "model": "whisper-accurate", "style": "clean",
    "live_preview": True, "sounds": True, "auto_paste": True, "start_minimized": False, "start_at_login": False,
    "keep_recordings": True,
    "dictionary": "# One word per line, or 'wrong -> right'\n",
    "app_rules": ("# App name = style (raw, clean, formal, bullets)\nTerminal = raw\niTerm2 = raw\n" if MAC else
                  "# App name = style (raw, clean, formal, bullets)\nWindowsTerminal = raw\ncmd = raw\n"),
    "first_run": True,
}


class Murmur:
    def __init__(self):
        self.settings = {**DEFAULTS, **system.load_json("settings.json", {})}
        self.settings["model"] = engine.model_key(self.settings["model"])
        self.history = system.load_json("history.json", [])
        self.root = tk.Tk()
        self.root.title("Murmur")
        self.root.geometry("520x640")
        self.root.minsize(440, 520)
        self.events, self.jobs = queue.Queue(), queue.Queue()
        # All model work (load, transcribe, preview, compare) runs on this one thread: some MLX models
        # only work on the thread that loaded them, and it also serializes access to the model.
        self.stt_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")
        self.stt, self.stt_error = None, ""
        self.frames, self.rate, self.stream, self.level, self.rec_start = [], engine.SR, None, 0.0, 0.0
        self.recording, self.state, self.session = False, "idle", 0
        self.press_t, self.ignore_up, self.tap_timer, self.discard = 0.0, False, None, False
        self.target, self.last_paste, self.preview, self.preview_busy = None, None, "", False
        self.hotkey_down, self.mods, self.undo_armed = False, set(), False
        self.hud_visible, self.ollama_pulling = False, False
        self.apply_theme()
        self.build_ui()
        self.build_hud()
        self.menubar = None
        if MAC:  # icon in the menu bar, next to Wi-Fi etc.
            import menubar
            self.menubar = menubar.MenuBar(on_open=self.open_from_menu, on_settings=self.open_settings_from_menu,
                                           on_undo=lambda: self.events.put(("undo",)), on_quit=self.root.destroy)
        if MAC:  # closing hides the window (dictation keeps working); click the Dock icon to bring it back
            self.root.protocol("WM_DELETE_WINDOW", self.root.withdraw)
            self.root.createcommand("::tk::mac::ReopenApplication", self.show_window)
        else:  # no tray icon on Windows, so minimize instead of vanishing
            self.root.protocol("WM_DELETE_WINDOW", self.root.iconify)
        self.root.after(50, self.poll)
        self.root.after(100, self.tick)
        self.stt_thread.submit(self.load_model)
        threading.Thread(target=self.worker, daemon=True).start()
        threading.Thread(target=engine.warm_ollama, daemon=True).start()
        self.key_watch = system.watch_keys(self.on_key)  # keep a reference, or the macOS monitors stop
        if self.settings["first_run"]:
            self.tabs.select(self.setup_tab)
            self.save_setting("first_run", False)
        elif self.settings["start_minimized"]:
            self.root.after(300, self.root.withdraw if MAC else self.root.iconify)

    # ================= settings =================
    def save_setting(self, key, value):
        self.settings[key] = value
        system.save_json("settings.json", self.settings)

    def bind_var(self, var, key, on_change=None):
        def changed(*_):
            self.save_setting(key, var.get())
            if on_change:
                on_change()
        var.trace_add("write", changed)
        return var

    # ================= look =================
    def apply_theme(self):
        self.text_colors, self.canvas_bg = {}, None
        icon = os.path.join(HERE, "assets", "icon_256.png")
        if os.path.exists(icon):
            self.icon = tk.PhotoImage(file=icon)
            self.root.iconphoto(True, self.icon)
        style = ttk.Style()
        style.configure("Status.TLabel", font=("", 15, "bold"))
        style.configure("Hint.TLabel", foreground="gray")
        style.configure("Small.TLabel", font=("", 10), foreground="gray")
        if MAC:
            self.canvas_bg = "systemWindowBackgroundColor"  # follows light/dark mode
            return
        if not system.system_dark_mode():
            return
        bg, fg, field = "#202020", "#f0f0f0", "#2b2b2b"
        style.theme_use("clam")
        style.configure(".", background=bg, foreground=fg, fieldbackground=field, bordercolor="#3a3a3a")
        style.configure("TButton", background="#333333")
        style.map("TButton", background=[("active", "#444444")])
        style.configure("TNotebook.Tab", background=field, foreground=fg)
        style.map("TNotebook.Tab", background=[("selected", bg)])
        style.configure("TCombobox", fieldbackground=field, foreground=fg)
        self.root.configure(bg=bg)
        self.canvas_bg = bg
        self.text_colors = {"bg": field, "fg": fg, "insertbackground": fg}

    def show_window(self):
        self.root.deiconify()
        self.root.lift()

    def open_from_menu(self):
        self.show_window()
        from AppKit import NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def open_settings_from_menu(self):
        self.tabs.select(self.settings_tab)
        self.open_from_menu()

    # ================= UI =================
    def build_ui(self):
        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=8)
        self.build_dictate_tab()
        self.build_settings_tab()
        self.build_dictionary_tab()
        self.build_setup_tab()
        self.tabs.bind("<<NotebookTabChanged>>", lambda _: self.refresh_setup())

    def build_dictate_tab(self):
        f = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(f, text="Dictate")
        self.status = ttk.Label(f, text="Loading speech model…", style="Status.TLabel", wraplength=460)
        self.status.pack(anchor="w")
        self.hint = ttk.Label(f, style="Hint.TLabel", wraplength=460)
        self.hint.pack(anchor="w", pady=(2, 8))
        self.update_hint()
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Style").pack(side="left")
        self.style_var = tk.StringVar(value=engine.STYLES[self.settings["style"]])
        box = ttk.Combobox(row, textvariable=self.style_var, values=list(engine.STYLES.values()), state="readonly",
                           width=22)
        box.pack(side="left", padx=6)
        box.bind("<<ComboboxSelected>>", lambda _: self.save_setting(
            "style", next(k for k, v in engine.STYLES.items() if v == self.style_var.get())))
        self.paste_var = self.bind_var(tk.BooleanVar(value=self.settings["auto_paste"]), "auto_paste")
        ttk.Checkbutton(row, text="Auto-paste", variable=self.paste_var).pack(side="left", padx=8)
        ttk.Label(f, text="Latest (editable)").pack(anchor="w", pady=(10, 2))
        self.text = tk.Text(f, height=6, wrap="word", font=("", 13), relief="flat", padx=6, pady=6,
                            **self.text_colors)
        self.text.pack(fill="x")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=6)
        self.copy_btn = ttk.Button(bar, text="Copy", command=self.copy_latest)
        self.copy_btn.pack(side="left")
        ttk.Button(bar, text=f"Undo last paste ({UNDO_NAME})", command=lambda: self.events.put(("undo",))).pack(
            side="left", padx=6)
        head = ttk.Frame(f)
        head.pack(fill="x", pady=(8, 2))
        ttk.Label(head, text="History").pack(side="left")
        ttk.Button(head, text="Clear", command=self.clear_history).pack(side="right")
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self.render_history())
        ttk.Entry(head, textvariable=self.search).pack(side="right", fill="x", expand=True, padx=6)
        ttk.Label(head, text="Search").pack(side="right")
        wrap = ttk.Frame(f)
        wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, highlightthickness=0, **({"bg": self.canvas_bg} if self.canvas_bg else {}))
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.rows = ttk.Frame(self.canvas)
        self.rows.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        win = self.canvas.create_window((0, 0), window=self.rows, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(win, width=e.width))
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", self.on_wheel)
        self.render_history()

    def on_wheel(self, e):
        if str(e.widget).startswith(str(self.canvas)):
            self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def build_settings_tab(self):
        f = self.settings_tab = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(f, text="Settings")
        grid = ttk.Frame(f)
        grid.pack(fill="x")

        def combo(r, label, values, current, on_pick):
            ttk.Label(grid, text=label).grid(row=r, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=current)
            box = ttk.Combobox(grid, textvariable=var, values=values, state="readonly", width=34)
            box.grid(row=r, column=1, sticky="w", padx=8)
            box.bind("<<ComboboxSelected>>", lambda _: on_pick(var.get()))
            return box

        combo(0, "Hotkey", list(HOTKEY_CHOICES.values()), HOTKEY_CHOICES[self.settings["hotkey"]],
              lambda v: (self.save_setting("hotkey", next(k for k, n in HOTKEY_CHOICES.items() if n == v)),
                         self.update_hint()))
        self.mic_box = combo(1, "Microphone", [], self.settings["mic"] or "System default",
                             lambda v: self.save_setting("mic", "" if v == "System default" else v))
        self.mic_box.bind("<Button-1>", lambda _: self.refresh_mics(), add="+")
        self.refresh_mics()
        self.model_box = combo(2, "Speech model", list(engine.MODELS.values()), engine.MODELS[self.settings["model"]],
                               lambda v: self.change_model(next(k for k, n in engine.MODELS.items() if n == v)))
        ttk.Button(grid, text="Compare models on my recent recordings…", command=self.open_compare).grid(
            row=3, column=1, sticky="w", padx=8, pady=(2, 0))
        ttk.Separator(f).pack(fill="x", pady=10)
        for key, label, cb in (("live_preview", "Show words live while speaking", None),
                               ("sounds", "Play sounds on start / finish", None),
                               ("start_minimized", "Start minimized", None),
                               ("start_at_login", "Start Murmur at login",
                                lambda: system.set_start_at_login(self.settings["start_at_login"])),
                               ("keep_recordings", f"Keep my last {system.KEEP_RECORDINGS} recordings on this "
                                                   "computer (for model comparison)",
                                lambda: None if self.settings["keep_recordings"] else system.clear_recordings())):
            var = self.bind_var(tk.BooleanVar(value=self.settings[key]), key, cb)
            ttk.Checkbutton(f, text=label, variable=var).pack(anchor="w", pady=2)
        ttk.Separator(f).pack(fill="x", pady=10)
        ttk.Label(f, text="Per-app style").pack(anchor="w")
        ttk.Label(f, text="Use a different style in specific apps, e.g. raw text in the terminal, formal in Mail. "
                          "Match is on the app name.", style="Small.TLabel", wraplength=460).pack(anchor="w")
        self.rules_text = tk.Text(f, height=6, wrap="none", font=("Menlo" if MAC else "Consolas", 12),
                                  relief="flat", padx=6, pady=6, **self.text_colors)
        self.rules_text.insert("1.0", self.settings["app_rules"])
        self.rules_text.pack(fill="x", pady=4)
        self.rules_text.bind("<FocusOut>", lambda _: self.save_setting(
            "app_rules", self.rules_text.get("1.0", "end").rstrip() + "\n"))

    def build_dictionary_tab(self):
        f = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(f, text="Dictionary")
        ttk.Label(f, text="Names and words Murmur should get right", style="Status.TLabel").pack(anchor="w")
        ttk.Label(f, text="Add one per line (e.g. Dustin, Kubernetes, Priya). If a word keeps coming out wrong, "
                          "add a fix like:  dust in -> Dustin",
                  style="Hint.TLabel", wraplength=460).pack(
            anchor="w", pady=(2, 8))
        self.dict_text = tk.Text(f, wrap="word", font=("", 13), relief="flat", padx=6, pady=6, **self.text_colors)
        self.dict_text.insert("1.0", self.settings["dictionary"])
        self.dict_text.pack(fill="both", expand=True)
        self.dict_saved = ttk.Label(f, text="", style="Small.TLabel")
        self.dict_saved.pack(anchor="e")
        self.dict_text.bind("<KeyRelease>", lambda _: self.save_dictionary())

    def save_dictionary(self):
        self.save_setting("dictionary", self.dict_text.get("1.0", "end").rstrip() + "\n")
        words, fixes = engine.parse_dictionary(self.settings["dictionary"])
        self.dict_saved.config(text=f"Saved · {len(words)} words, {len(fixes)} fixes")

    def build_setup_tab(self):
        self.setup_tab = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(self.setup_tab, text="Setup")
        ttk.Label(self.setup_tab, text="Setup checklist", style="Status.TLabel").pack(anchor="w")
        ttk.Label(self.setup_tab, text="Everything should be green. Permission changes on Mac apply after a restart.",
                  style="Hint.TLabel", wraplength=460).pack(anchor="w", pady=(2, 10))
        self.checks = ttk.Frame(self.setup_tab)
        self.checks.pack(fill="x")
        bar = ttk.Frame(self.setup_tab)
        bar.pack(fill="x", pady=12)
        ttk.Button(bar, text="Check again", command=self.refresh_setup).pack(side="left")
        if MAC:
            ttk.Button(bar, text="Restart Murmur", command=self.restart).pack(side="left", padx=6)

    def setup_items(self):
        """(name, ok, detail, button label, action) for each requirement."""
        items = []
        if self.stt is not None:
            items.append(("Speech model", True, "Loaded", None, None))
        elif self.stt_error:
            items.append(("Speech model", False, self.stt_error[:80], "Retry", self.change_model_same))
        else:
            items.append(("Speech model", None, "Loading… (first run downloads it)", None, None))
        neural = engine.silero() is not None
        items.append(("Speech detection", neural, "Silero VAD (trained speech detector)" if neural else
                      "Basic detector — assets/silero_vad.onnx or onnxruntime missing", None, None))
        mic = system.mic_status()
        items.append(("Microphone", mic == "ok",
                      {"ok": "Allowed", "ask": "Not asked yet", "denied": "Blocked", "none": "No microphone found"}[mic],
                      None if mic == "ok" else "Allow", system.request_mic))
        if MAC:
            ok = system.input_monitoring_ok()
            items.append(("Input Monitoring (hotkey)", ok, "Allowed" if ok else "Needed for the hotkey",
                          None if ok else "Open Settings", system.request_input_monitoring))
            ok = system.accessibility_ok()
            items.append(("Accessibility (paste)", ok, "Allowed" if ok else "Needed to paste and undo",
                          None if ok else "Open Settings", system.request_accessibility))
        st = "pulling" if self.ollama_pulling else engine.ollama_status()
        items.append(("AI cleanup (Ollama)", None if st == "pulling" else st == "ok",
                      {"ok": "Ready", "pulling": "Downloading model…",
                       "no-model": f"Model {engine.LLM_MODEL} not downloaded",
                       "not-running": "Not running (optional — basic cleanup works without it)"}[st],
                      {"no-model": "Download", "not-running": "Start" if self.ollama_path() else "Get Ollama"}.get(st),
                      {"no-model": self.pull_model, "not-running": self.start_ollama}.get(st)))
        return items

    def refresh_setup(self):
        for w in self.checks.winfo_children():
            w.destroy()
        for name, ok, detail, button, action in self.setup_items():
            row = ttk.Frame(self.checks, padding=(0, 6))
            row.pack(fill="x")
            if button:
                ttk.Button(row, text=button, command=lambda a=action: (a(), self.root.after(
                    1500, self.refresh_setup))).pack(side="right")
            mark = {True: "✅", False: "❌", None: "⏳"}[ok]
            ttk.Label(row, text=f"{mark}  {name}").pack(anchor="w")
            ttk.Label(row, text=detail, style="Small.TLabel", wraplength=300).pack(anchor="w", padx=(30, 0))

    def setup_incomplete(self):
        return [name for name, ok, *_ in self.setup_items() if ok is False and not name.startswith("AI cleanup")]

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
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def update_hint(self):
        name = HOTKEY_CHOICES[self.settings["hotkey"]].split(" (")[0]
        self.hint.config(text=f"Hold {name} and speak · double-tap for hands-free · {UNDO_NAME} undoes the last paste")

    def refresh_mics(self):
        try:
            host = sd.query_devices(kind="input")["hostapi"]
            names = [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0 and d["hostapi"] == host]
        except Exception:
            names = []
        self.mic_box.configure(values=["System default"] + list(dict.fromkeys(names)))

    def mic_index(self):
        name = self.settings["mic"]
        if not name:
            return None
        for i, d in enumerate(sd.query_devices()):
            if d["name"] == name and d["max_input_channels"] > 0:
                return i
        return None  # unplugged -> system default

    # ---- history ----
    def render_history(self):
        for w in self.rows.winfo_children():
            w.destroy()
        q = self.search.get().lower().strip()
        shown = [h for h in self.history if q in h["text"].lower() or q in h.get("app", "").lower()][:100]
        if not shown:
            ttk.Label(self.rows, text="Nothing yet." if not q else "No matches.", style="Hint.TLabel").pack(
                anchor="w", pady=8)
        for h in shown:
            row = ttk.Frame(self.rows, padding=(0, 4))
            row.pack(fill="x")
            ttk.Button(row, text="Copy", width=5, command=lambda t=h["text"]: self.copy(t, "Copied from history")).pack(
                side="right", padx=4)
            meta = time.strftime("%b %d, %H:%M", time.localtime(h["time"])) + (f" · {h['app']}" if h.get("app") else "")
            ttk.Label(row, text=meta, style="Small.TLabel").pack(anchor="w")
            body = ttk.Label(row, text=h["text"], wraplength=380, cursor="hand2")
            body.pack(anchor="w", fill="x")
            body.bind("<Button-1>", lambda _, t=h["text"]: self.set_latest(t))
            ttk.Separator(self.rows).pack(fill="x")

    def clear_history(self):
        self.history = []
        system.save_json("history.json", self.history)
        self.render_history()

    def set_latest(self, text):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)

    def copy(self, text, msg=None):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        if msg:
            self.status.config(text=msg)

    def copy_latest(self):
        self.copy(self.text.get("1.0", "end").strip())
        self.copy_btn.config(text="Copied ✓")
        self.root.after(1200, lambda: self.copy_btn.config(text="Copy"))

    # ================= on-screen pill =================
    # A small capsule: live waveform while listening, pulsing dots while transcribing (white) / cleaning (violet).
    # Drawn on a canvas and animated at ~30 fps only while visible.
    PILL_H, PILL_BARS, PILL_FPS_MS = 34, 9, 33
    PILL_BG, PILL_KEY = "#141416", "#010203"  # PILL_KEY: transparent color on Windows

    def build_hud(self):
        self.hud_mode, self.hud_size, self.level_smooth, self.anim_on = None, None, 0.0, False
        self.native_pill = None
        if MAC:  # frosted-glass native pill (pill.py); the Tk canvas pill below is the Windows version
            import pill
            self.native_pill = pill.MacPill()
            return
        self.hud = tk.Toplevel(self.root)
        self.hud.overrideredirect(True)
        self.hud.attributes("-topmost", True)
        if MAC:
            self.hud.attributes("-transparent", True)
            clear = "systemTransparent"
        else:
            self.hud.attributes("-transparentcolor", self.PILL_KEY)
            clear = self.PILL_KEY
        self.hud.configure(bg=clear)
        self.pill = tk.Canvas(self.hud, width=110, height=self.PILL_H, bg=clear, highlightthickness=0, bd=0)
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
        """Size the capsule for the current mode (wider when live text is shown) and rebuild its items."""
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
        if self.menubar is not None:
            self.menubar.set_state(self.hud_mode or "idle", self.state == "locked")
        if self.native_pill is not None:
            text = self.preview.strip() if self.hud_mode == "listening" and self.settings["live_preview"] else ""
            self.native_pill.update(t, self.level, self.state == "locked", text)
            self.root.after(16, self.animate)  # ~60 fps; Core Animation smooths between frames
            return
        h, c = self.PILL_H, self.pill
        if self.hud_mode == "listening":
            self.pill_layout()  # widens once live text arrives
            self.level_smooth += (self.level - self.level_smooth) * (0.5 if self.level > self.level_smooth else 0.15)
            c.itemconfigure("dot", fill="#ffb340" if self.state == "locked" else "#ff453a")
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

    def set_status(self, msg, hud=None):
        self.status.config(text=msg)
        if self.menubar is not None:
            self.menubar.set_status(msg if len(msg) <= 60 else msg[:59] + "…")
        self.hud_show(hud) if hud else self.hud_hide()

    def tick(self):
        """Stop forgotten recordings and keep the pill in 'listening' mode while recording."""
        if self.recording and time.monotonic() - self.rec_start > MAX_RECORDING:
            self.state = "idle"
            self.finish_recording()
        if self.recording and self.hud_mode != "listening":
            self.hud_show("listening")
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

    def load_model(self):
        """Runs on the stt thread."""
        self.stt = None
        engine.free_memory()
        try:
            self.stt = engine.load_stt(self.settings["model"])
            self.stt_error = ""
            self.events.put(("ready",))
        except Exception as e:
            self.stt_error = str(e)
            self.events.put(("status", f"Could not load speech model: {e}", None))

    def transcribe(self, audio, prompt, still_wanted=None):
        """Run the current model on the stt thread and wait for the text. Recordings made while a model is
        loading wait for it, because the load is queued on the same thread."""
        def run():
            if still_wanted and not still_wanted():
                return None
            if self.stt is None:
                raise RuntimeError(self.stt_error or "speech model isn't loaded")
            return self.stt(audio, prompt)
        return self.stt_thread.submit(run).result()

    def change_model(self, choice):
        self.save_setting("model", choice)
        self.change_model_same()

    def change_model_same(self):
        self.stt_error = ""
        self.set_status("Loading speech model…")
        self.stt_thread.submit(self.load_model)

    # ---- model comparison on the user's own recordings ----
    def open_compare(self):
        recs = system.list_recordings()
        win = tk.Toplevel(self.root)
        win.title("Compare speech models")
        win.geometry("680x600")
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)
        if not recs:
            ttk.Label(frame, text="No recordings yet. Dictate a few times (with 'Keep my last recordings' on in "
                                  "Settings), then come back.", wraplength=600).pack(anchor="w")
            return
        status = ttk.Label(frame, text="Starting… (each model downloads once on first use)", style="Status.TLabel",
                           wraplength=640)
        status.pack(anchor="w")
        ttk.Label(frame, text=f"Runs every model on your last {len(recs)} recordings. Compare the text to what you "
                              "actually said, and pick the one that gets it right most often.", style="Hint.TLabel",
                  wraplength=640).pack(anchor="w", pady=(2, 8))
        out = tk.Text(frame, wrap="word", font=("", 12), relief="flat", padx=8, pady=8, **self.text_colors)
        out.pack(fill="both", expand=True)
        out.tag_configure("head", font=("", 12, "bold"))
        out.tag_configure("model", foreground="gray")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(8, 0))
        threading.Thread(target=self.run_compare, args=(recs, win, status, out, buttons), daemon=True).start()

    def run_compare(self, recs, win, status, out, buttons):
        audio = [system.load_recording(r) for r in recs]
        prompt = engine.whisper_prompt(engine.parse_dictionary(self.settings["dictionary"])[0])
        results, timing = {}, {}
        run_on_stt = lambda fn, *a: self.stt_thread.submit(fn, *a).result()
        for key, name in engine.MODELS.items():
            self.events.put(("compare_status", win, status, f"Loading {name}…"))
            current = key == self.settings["model"] and self.stt is not None
            try:
                stt = self.stt if current else run_on_stt(engine.load_stt, key)
            except Exception as e:
                results[key] = [f"(could not load: {e})"] * len(recs)
                continue
            self.events.put(("compare_status", win, status, f"Running {name}…"))
            texts, total = [], 0.0
            for a in audio:
                t0 = time.time()
                texts.append(run_on_stt(stt, a, prompt))
                total += time.time() - t0
            results[key], timing[key] = texts, total / len(recs)
            del stt
            if not current:
                run_on_stt(engine.free_memory)
        self.events.put(("compare_done", win, status, out, buttons, recs, results, timing))

    def ev_compare_status(self, win, status, msg):
        if win.winfo_exists():
            status.config(text=msg)

    def ev_compare_done(self, win, status, out, buttons, recs, results, timing):
        if not win.winfo_exists():
            return
        status.config(text="Done. Which one matches what you said?")
        for i, rec in enumerate(recs):
            when = time.strftime("%b %d, %H:%M", time.localtime(rec["time"]))
            out.insert("end", f"Recording {i + 1} · {when} · {rec.get('seconds', '?')} s\n", "head")
            for key, name in engine.MODELS.items():
                out.insert("end", f"{name.split(' (')[0]}: ", "model")
                out.insert("end", results[key][i] + "\n")
            out.insert("end", "\n")
        out.insert("end", "Average time per recording\n", "head")
        for key, secs in timing.items():
            out.insert("end", f"{engine.MODELS[key].split(' (')[0]}: {secs:.2f} s\n")
        out.configure(state="disabled")
        for key, name in engine.MODELS.items():
            ttk.Button(buttons, text=f"Use {name.split(' (')[0]}", command=lambda k=key: (
                self.model_box.set(engine.MODELS[k]), self.change_model(k), win.destroy())).pack(side="left", padx=(0, 6))

    def ev_ready(self):
        missing = self.setup_incomplete()
        if missing:
            self.set_status("⚠ Setup incomplete: " + ", ".join(missing) + " — see the Setup tab")
            self.tabs.select(self.setup_tab)
        else:
            self.set_status("Ready")
        self.refresh_setup()

    def ev_status(self, msg, hud):
        self.set_status(msg, hud)

    def ev_setup_changed(self):
        self.refresh_setup()

    # ---- hotkey state machine: hold = push-to-talk, double-tap = hands-free ----
    def ev_key_down(self, t):
        if self.stt is None:
            return self.set_status("Still loading the speech model…")
        if self.state == "idle":
            self.start_recording()
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
            return self.set_status("Nothing to undo")
        target, self.last_paste = self.last_paste, None
        system.refocus(target)
        self.root.after(150, lambda: system.send_shortcut("z"))
        self.set_status("Undid the last paste")

    # ---- recording ----
    def start_recording(self):
        self.target, self.frames, self.preview, self.discard = system.frontmost(), [], "", False
        self.session += 1

        def callback(data, *_):
            self.frames.append(data[:, 0].copy())
            self.level = engine.level(data[:, 0])
        self.stream = sd.InputStream(device=self.mic_index(), channels=1, dtype="float32", callback=callback)
        self.rate = int(self.stream.samplerate)
        self.stream.start()
        self.recording, self.rec_start = True, time.monotonic()
        self.set_status("Listening…", "listening")
        self.sound("start")
        self.root.after(1000, self.schedule_preview, self.session)

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
            return self.set_status("Cancelled")
        style = self.style_for(self.target)
        self.set_status("Transcribing…", "transcribing")
        self.jobs.put((np.concatenate(self.frames), self.rate, style, self.paste_var.get(), self.target,
                       self.settings["dictionary"]))

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
            prompt = engine.whisper_prompt(engine.parse_dictionary(self.settings["dictionary"])[0])
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

    # ---- processing (one job at a time, in order) ----
    def worker(self):
        while True:
            self.process(*self.jobs.get())

    def process(self, raw, rate, style, auto_paste, target, dictionary):
        try:
            audio, voiced_s = engine.prepare(engine.resample(raw, rate))
            if audio is None:
                self.events.put(("failed", "Didn't hear any speech — check the microphone in Settings"))
                return
            words, fixes = engine.parse_dictionary(dictionary)
            text = self.transcribe(audio, engine.whisper_prompt(words))
            if engine.is_hallucination(text, voiced_s):
                self.events.put(("failed", "No speech detected"))
                return
            if self.settings["keep_recordings"]:
                system.save_recording(audio, {"time": time.time(), "text": text, "model": self.settings["model"],
                                              "seconds": round(len(audio) / engine.SR, 1)})
            text = engine.apply_fixes(text, fixes)
            note = ""
            if style != "raw":
                self.events.put(("status", "Cleaning up…", "cleaning"))
                text, note = engine.cleanup(text, style)
            self.events.put(("result", text, note, target, auto_paste))
        except Exception as e:
            self.events.put(("failed", f"Error: {e}"))

    def ev_failed(self, msg):
        self.set_status(msg)
        self.sound("error")

    def ev_result(self, text, note, target, auto_paste):
        self.history.insert(0, {"text": text, "time": time.time(), "app": target.name if target else ""})
        del self.history[500:]
        system.save_json("history.json", self.history)
        self.render_history()
        self.set_latest(text)
        self.copy(text)
        self.set_status("Done — copied" + (f" · {note}" if note else ""))
        self.sound("done")
        if auto_paste and target is not None:
            system.refocus(target)
            self.root.after(150, lambda: system.send_shortcut("v"))
            self.last_paste = target

    # ---- global keyboard (system.watch_keys: main thread on Mac, pynput thread on Windows) ----
    def on_key(self, name, down):
        if down:
            self.on_press(name)
        else:
            self.on_release(name)

    def on_press(self, name):
        if name == self.settings["hotkey"]:
            if not self.hotkey_down:  # ignore key auto-repeat
                self.hotkey_down = True
                self.events.put(("key_down", time.monotonic()))
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


if __name__ == "__main__":
    Murmur().root.mainloop()
