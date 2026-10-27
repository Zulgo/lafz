"""macOS main window: native frosted-glass window hosting Lafz's web interface (WKWebView). Main thread only."""
import json

from AppKit import (NSApplication, NSAppearance, NSAppearanceNameDarkAqua, NSBackingStoreBuffered, NSMakeRect,
                    NSMenu, NSMenuItem, NSObject, NSViewHeightSizable, NSViewWidthSizable,
                    NSVisualEffectBlendingModeBehindWindow, NSVisualEffectMaterialSidebar, NSVisualEffectStateActive,
                    NSVisualEffectView, NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskFullSizeContentView,
                    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable, NSWindowStyleMaskTitled,
                    NSWindowTitleHidden)
from Foundation import NSURL, NSURLRequest
from WebKit import WKWebView, WKWebViewConfiguration

TITLEBAR = 28  # translucent strip at the top: holds the window buttons and lets the user drag the window


class _Delegate(NSObject):
    def windowShouldClose_(self, window):
        window.orderOut_(None)  # closing hides; Lafz keeps running in the menu bar
        return False


class MainWindow:
    def __init__(self, url):
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable |
                 NSWindowStyleMaskResizable | NSWindowStyleMaskFullSizeContentView)
        self.window = w = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 1120, 740), style, NSBackingStoreBuffered, False)
        w.setTitle_("Lafz")
        w.setTitlebarAppearsTransparent_(True)
        w.setTitleVisibility_(NSWindowTitleHidden)
        w.setMinSize_((880, 580))
        w.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        w.setReleasedWhenClosed_(False)
        w.center()
        w.setFrameAutosaveName_("LafzMain")
        self.delegate = _Delegate.alloc().init()
        w.setDelegate_(self.delegate)

        glass = NSVisualEffectView.alloc().initWithFrame_(w.contentView().bounds())
        glass.setMaterial_(NSVisualEffectMaterialSidebar)
        glass.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        glass.setState_(NSVisualEffectStateActive)
        w.setContentView_(glass)
        b = glass.bounds()
        self.web = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, b.size.width, b.size.height - TITLEBAR), WKWebViewConfiguration.alloc().init())
        self.web.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.web.setValue_forKey_(False, "drawsBackground")  # transparent page areas show the frosted glass
        glass.addSubview_(self.web)
        self.web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))
        _install_edit_menu()

    def show(self, page=None):
        if page:
            self.web.evaluateJavaScript_completionHandler_(f"window.lafzGo && lafzGo({json.dumps(page)})", None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def hide(self):
        self.window.orderOut_(None)


def _install_edit_menu():
    """Cmd+C/V/X/A/Z reach text fields through the menu bar; Tk's default menu bar has no Edit menu."""
    menu_bar = NSApplication.sharedApplication().mainMenu()
    if menu_bar is None or menu_bar.itemWithTitle_("Edit") is not None:
        return
    edit = NSMenu.alloc().initWithTitle_("Edit")
    for title, action, key in (("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), (None, None, None),
                               ("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"),
                               ("Select All", "selectAll:", "a")):
        if title is None:
            edit.addItem_(NSMenuItem.separatorItem())
        else:
            edit.addItemWithTitle_action_keyEquivalent_(title, action, key)
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Edit", None, "")
    item.setSubmenu_(edit)
    menu_bar.addItem_(item)

