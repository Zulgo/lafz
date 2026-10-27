"""macOS recording indicator: a frosted-glass rounded rectangle (native NSVisualEffectView), animated with Core Animation.
Never takes focus. Must be created and updated on the main thread (Tk's thread on macOS)."""
import math

from AppKit import (NSAnimationContext, NSBackingStoreBuffered, NSColor, NSFont, NSFontWeightMedium,
                    NSLineBreakByTruncatingHead, NSMakeRect, NSObject, NSPanel, NSScreen, NSStatusWindowLevel, NSTextField,
                    NSView, NSViewHeightSizable, NSViewWidthSizable, NSVisualEffectBlendingModeBehindWindow,
                    NSVisualEffectMaterialHUDWindow, NSVisualEffectStateActive, NSVisualEffectView,
                    NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorFullScreenAuxiliary,
                    NSWindowCollectionBehaviorStationary, NSWindowStyleMaskBorderless,
                    NSWindowStyleMaskNonactivatingPanel)
from Quartz import CALayer, CATransaction, CATransform3DMakeScale

H, RADIUS, BOTTOM = 44, 16, 96
GLASS_ALPHA = 0.7  # how solid the frosted glass is (1 = macOS's standard HUD)
BARS, BAR_W, BAR_GAP = 11, 3, 3
WIDTHS = {"listening": 128, "listening_text": 380, "transcribing": 72, "cleaning": 72}


def rgba(r, g, b, a=1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r / 255, g / 255, b / 255, a).CGColor()


WHITE, DIM = rgba(255, 255, 255, 0.95), rgba(255, 255, 255, 0.35)
RED, AMBER = rgba(255, 69, 58), rgba(255, 179, 64)
VIOLET, VIOLET_DIM = rgba(190, 168, 255), rgba(190, 168, 255, 0.35)


class MacPill:
    def __init__(self):
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTHS["listening"], H),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel, NSBackingStoreBuffered, False)
        p = self.panel
        p.setOpaque_(False)
        p.setBackgroundColor_(NSColor.clearColor())
        p.setLevel_(NSStatusWindowLevel)
        p.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary |
                                 NSWindowCollectionBehaviorFullScreenAuxiliary)
        p.setIgnoresMouseEvents_(True)
        p.setHidesOnDeactivate_(False)
        p.setHasShadow_(True)
        p.setReleasedWhenClosed_(False)

        # A rounded container clips everything: the glass, and on top of it the dots, bars and text (kept fully opaque).
        box = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTHS["listening"], H))
        box.setWantsLayer_(True)
        box.layer().setCornerRadius_(RADIUS)
        box.layer().setCornerCurve_("continuous")  # Apple's smooth "squircle" corners
        box.layer().setMasksToBounds_(True)
        p.setContentView_(box)

        glass = NSVisualEffectView.alloc().initWithFrame_(box.bounds())
        glass.setMaterial_(NSVisualEffectMaterialHUDWindow)  # the blurred dark glass of macOS's volume/brightness HUD
        glass.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        glass.setState_(NSVisualEffectStateActive)
        glass.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        glass.setAlphaValue_(GLASS_ALPHA)  # see more of what's behind
        box.addSubview_(glass)

        self.canvas = NSView.alloc().initWithFrame_(box.bounds())
        self.canvas.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.canvas.setWantsLayer_(True)
        box.addSubview_(self.canvas)
        root = self.canvas.layer()
        root.setCornerRadius_(RADIUS)
        root.setCornerCurve_("continuous")
        root.setBorderWidth_(0.5)
        root.setBorderColor_(rgba(255, 255, 255, 0.10))  # a hairline edge, softer than before

        def layer(w, h, color):
            lay = CALayer.layer()
            lay.setBounds_(((0, 0), (w, h)))
            lay.setCornerRadius_(min(w, h) / 2)
            lay.setBackgroundColor_(color)
            root.addSublayer_(lay)
            return lay

        self.dot = layer(8, 8, RED)
        self.dot.setShadowColor_(RED)
        self.dot.setShadowRadius_(4)
        self.dot.setShadowOpacity_(0.8)
        self.dot.setShadowOffset_((0, 0))
        self.bars = [layer(BAR_W, 4, WHITE) for _ in range(BARS)]
        self.dots = [layer(7, 7, WHITE) for _ in range(3)]

        self.label = NSTextField.labelWithString_("")
        self.label.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightMedium))
        self.label.setTextColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, 0.82))
        self.label.setLineBreakMode_(NSLineBreakByTruncatingHead)
        box.addSubview_(self.label)

        self.mode, self.visible, self.level, self.width = None, False, 0.0, WIDTHS["listening"]

    # ---- show / hide ----
    def _place(self, width, animate):
        screen = NSScreen.mainScreen().frame()
        rect = NSMakeRect(screen.origin.x + (screen.size.width - width) / 2, screen.origin.y + BOTTOM, width, H)
        if animate:
            NSAnimationContext.beginGrouping()
            NSAnimationContext.currentContext().setDuration_(0.18)
            self.panel.animator().setFrame_display_(rect, True)
            NSAnimationContext.endGrouping()
            self.panel.performSelector_withObject_afterDelay_("invalidateShadow", None, 0.2)  # once the resize is done
        else:
            self.panel.setFrame_display_(rect, True)
        self.panel.invalidateShadow()  # the shadow follows the rounded shape, not the window's rectangle
        self.width = width

    def show(self, mode):
        self.mode = mode
        if not self.visible:
            self.visible = True
            NSObject.cancelPreviousPerformRequestsWithTarget_(self.panel)  # a pending orderOut from hide()
            self._place(WIDTHS[mode], animate=False)
            self.panel.setAlphaValue_(0.0)
            self.panel.orderFrontRegardless()  # shows without activating Lafz
            NSAnimationContext.beginGrouping()
            NSAnimationContext.currentContext().setDuration_(0.15)
            self.panel.animator().setAlphaValue_(1.0)
            NSAnimationContext.endGrouping()

    def hide(self):
        if not self.visible:
            return
        self.visible, self.mode = False, None
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.15)
        self.panel.animator().setAlphaValue_(0.0)
        NSAnimationContext.endGrouping()
        self.panel.performSelector_withObject_afterDelay_("orderOut:", None, 0.16)  # after the fade; show() cancels it

    # ---- per-frame update (called ~60x/s while visible) ----
    def update(self, t, level, locked=False, text="", command=False):
        if not self.visible:
            return
        want = WIDTHS["listening_text" if self.mode == "listening" and text else self.mode]
        if want != self.width:
            self._place(want, animate=True)
        # Ease toward the input level: fast attack, slow release, like a VU meter.
        self.level += (level - self.level) * (0.45 if level > self.level else 0.12)
        CATransaction.begin()
        CATransaction.setAnimationDuration_(0.09)  # Core Animation interpolates between frames -> smooth motion
        listening = self.mode == "listening"
        self.dot.setHidden_(not listening)
        for b in self.bars:
            b.setHidden_(not listening)
        for d in self.dots:
            d.setHidden_(listening)
        mid = H / 2
        if listening:
            color = VIOLET if command else AMBER if locked else RED
            self.dot.setBackgroundColor_(color)
            self.dot.setShadowColor_(color)
            self.dot.setPosition_((20, mid))
            for i, bar in enumerate(self.bars):
                center = 1 - abs(i - (BARS - 1) / 2) / BARS  # taller in the middle
                wobble = 0.6 + 0.4 * math.sin(t * 8 + i * 1.1)
                h = 4 + 22 * min(1.0, self.level * 1.5) * center * wobble
                bar.setBounds_(((0, 0), (BAR_W, h)))
                bar.setPosition_((38 + i * (BAR_W + BAR_GAP), mid))
            self.label.setHidden_(not text)
            if text:
                x = 38 + BARS * (BAR_W + BAR_GAP) + 10
                self.label.setFrame_(NSMakeRect(x, mid - 9, WIDTHS["listening_text"] - x - 16, 18))
                self.label.setStringValue_(text)
        else:
            self.label.setHidden_(True)
            bright, dim = (WHITE, DIM) if self.mode == "transcribing" else (VIOLET, VIOLET_DIM)
            for i, d in enumerate(self.dots):
                pulse = 0.5 + 0.5 * math.sin(t * 6.5 - i * 2.1)  # evenly spaced wave, one dot always lit
                d.setPosition_((self.width / 2 + (i - 1) * 15, mid))
                d.setBackgroundColor_(bright if pulse > 0.5 else dim)
                s = 0.75 + 0.4 * pulse
                d.setTransform_(CATransform3DMakeScale(s, s, 1))
        CATransaction.commit()
