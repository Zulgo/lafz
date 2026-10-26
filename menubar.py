"""macOS menu bar icon (next to Wi-Fi, battery, ...) with a small menu. Main thread only."""
import os

from AppKit import (NSColor, NSImage, NSMenu, NSMenuItem, NSObject, NSStatusBar, NSVariableStatusItemLength)
import objc

BEAR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "menubar_bear.png")  # make_icon.py


class _Target(NSObject):
    """Receives menu clicks and forwards them to Python callables."""

    def initWithActions_(self, actions):
        self = objc.super(_Target, self).init()
        self.actions = actions
        return self

    def run_(self, sender):
        self.actions[sender.tag()]()


class MenuBar:
    def __init__(self, on_open, on_settings, on_undo, on_quit):
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self.item.button().setToolTip_("Murmur")
        actions = [on_open, on_settings, on_undo, on_quit]
        self.target = _Target.alloc().initWithActions_(actions)  # keep a reference; menus don't retain targets
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        self.status = menu.addItemWithTitle_action_keyEquivalent_("Murmur — starting…", None, "")
        self.status.setEnabled_(False)
        menu.addItem_(NSMenuItem.separatorItem())
        for tag, title in enumerate(("Open Murmur", "Settings…", "Undo Last Paste", None, "Quit Murmur")):
            if title is None:
                menu.addItem_(NSMenuItem.separatorItem())
                continue
            entry = menu.addItemWithTitle_action_keyEquivalent_(title, "run:", "")
            entry.setTarget_(self.target)
            entry.setTag_(tag if tag < 3 else 3)
        self.item.setMenu_(menu)
        self.icon = NSImage.alloc().initWithContentsOfFile_(BEAR) or \
            NSImage.imageWithSystemSymbolName_accessibilityDescription_("mic", "Murmur")
        self.icon.setSize_((18, 18))
        self.icon.setTemplate_(True)  # drawn black or white to match the menu bar, like the system icons
        self.icon.setAccessibilityDescription_("Murmur")
        self.item.button().setImage_(self.icon)
        self.state = None
        self.set_state("idle")

    def set_state(self, state, locked=False):
        """state: 'idle', 'listening', 'transcribing' or 'cleaning'."""
        if (state, locked) == self.state:
            return
        self.state = (state, locked)
        button = self.item.button()
        tint = None  # idle: plain black/white bear
        if state == "listening":
            tint = NSColor.systemOrangeColor() if locked else NSColor.systemRedColor()
        button.setContentTintColor_(tint)
        button.setAlphaValue_(0.5 if state in ("transcribing", "cleaning") else 1.0)  # dimmed while working

    def set_status(self, text):
        self.status.setTitle_(f"Murmur — {text}")
