"""Menu checks: shortcuts sit in the menu's own shortcut column (real key
equivalents, not title text), each shown shortcut runs the same action as its
global hotkey, and "…" marks only the items that open a dialog first.

Builds the real TidyTabApp with every launch side effect stubbed out (update
check, login-item rewrite, orphan restore, timers, hotkey registration)."""
import os, sys, tempfile, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tidytab, rumps
from rumps.rumps import NSApp as D

fails = 0
def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name); fails += (not cond)

tidytab.PREFS_PATH = os.path.join(tempfile.mkdtemp(), "prefs.json")
tidytab._upgrade_login_item = lambda: None
tidytab._restore_orphaned_app = lambda: None
tidytab.accessibility_trusted = lambda: True          # the normal menu, no "Grant Accessibility…"
tidytab.TidyTabApp._auto_update_on_launch = lambda self: None

class FakeTimer:
    def __init__(self, *a, **k): pass
    def start(self): pass
    def stop(self): pass
rumps.Timer = FakeTimer

handlers = []
tidytab.NSEvent = types.SimpleNamespace(
    addGlobalMonitorForEventsMatchingMask_handler_=lambda _mask, h: handlers.append(h) or object(),
    removeMonitor_=lambda _m: None,
)

app = tidytab.TidyTabApp()
ran = []
app._start = lambda: ran.append(app._operation)       # never touch Safari

items = list(app.menu._menu.itemArray())
def ns(title):
    return next(i for i in items if i.title() == title)
def callback(title):
    return D._ns_to_py_and_callback[ns(title)][1]

# 1. menu order and titles
expected = ["Unpin pinned tabs", "Close pinned tabs", "Pin all tabs", "Stop", "-",
            "Launch at login", "Auto-update", "Hide menu-bar icon…", "-",
            "Check for Updates…", "Download page", "Quit TidyTab"]
got = ["-" if i.isSeparatorItem() else i.title() for i in items]
check(f"menu order + titles {got}", got == expected)

# 2. shortcuts are real key equivalents with the exact modifier mask
for title, key, mask in [("Unpin pinned tabs", "u", tidytab.CMD_OPT),
                         ("Close pinned tabs", "k", tidytab.CMD_OPT),
                         ("Pin all tabs", "p", tidytab.CMD_OPT),
                         ("Stop", "\x1b", 0),
                         ("Quit TidyTab", "q", 1 << 20)]:
    i = ns(title)
    check(f"{title!r}: key {i.keyEquivalent()!r} mask {i.keyEquivalentModifierMask():#x}",
          i.keyEquivalent() == key and i.keyEquivalentModifierMask() == mask)
check("⌘⌥ mask = Command | Option bits", tidytab.CMD_OPT == (1 << 20) | (1 << 19))

# 3. no shortcut text left in any title, no stray shortcut on other items
check("no ⌘ / ⌥ in any title", not any(c in i.title() for i in items for c in "⌘⌥"))
check("no '(' in any title", [i.title() for i in items if "(" in i.title()] == [])
others = [i.title() for i in items if not i.isSeparatorItem()
          and i.title() not in ("Unpin pinned tabs", "Close pinned tabs", "Pin all tabs", "Stop",
                                "Quit TidyTab")]
check(f"no key equivalent on {others}", all(ns(t).keyEquivalent() == "" for t in others))

# 4. "…" only on the two items that open a dialog first
dots = sorted(i.title() for i in items if "…" in i.title())
check(f"'…' only on Hide + Check for Updates {dots}",
      dots == sorted([tidytab.HIDE_ICON_TITLE, "Check for Updates…"]))
check("no three-period '...' anywhere", not any("..." in i.title() for i in items))

# 5. each shown shortcut runs the same action as its global hotkey
check("hotkey monitor registered once", len(handlers) == 1)
class Ev:
    def __init__(self, ch, flags): self.ch, self.flags = ch, flags
    def modifierFlags(self): return self.flags
    def charactersIgnoringModifiers(self): return self.ch
for title, key, op in [("Unpin pinned tabs", "u", "unpin"),
                       ("Close pinned tabs", "k", "close"),
                       ("Pin all tabs", "p", "pin")]:
    ran.clear(); callback(title)(None)
    ran_menu = list(ran)
    ran.clear(); handlers[0](Ev(ns(title).keyEquivalent(), ns(title).keyEquivalentModifierMask()))
    check(f"{title!r}: menu click and ⌘⌥{key.upper()} both run {op!r} ({ran_menu} / {ran})",
          ran_menu == [op] and ran == [op])
ran.clear(); handlers[0](Ev("u", 1 << 20))                 # ⌘U alone is not the hotkey
check("⌘U without ⌥ runs nothing", ran == [])

# 6. Stop is gray while idle, live during a run, and sets the stop flag
check("Stop has no callback + no action when idle",
      callback("Stop") is None and ns("Stop").action() is None)
app._start_space_monitor = lambda: None
app._begin_running_ui()
check("Stop has _stop after _begin_running_ui",
      callback("Stop") == app._stop and ns("Stop").action() is not None)
app._stop_flag.clear(); callback("Stop")(None)
check("Stop sets the stop flag", app._stop_flag.is_set())
app._apply_idle()
check("Stop gray again after the run", callback("Stop") is None)

# 7. width: the shortcut column starts right after the widest title, so keep it
# close to "Unpin pinned tabs"
from AppKit import NSFont, NSFontAttributeName
font = NSFont.menuFontOfSize_(0)
def width(t):
    from Foundation import NSString
    return NSString.stringWithString_(t).sizeWithAttributes_({NSFontAttributeName: font}).width
widths = {i.title(): width(i.title()) for i in items if not i.isSeparatorItem()}
widest = max(widths, key=widths.get)
gap = widths[widest] - widths["Unpin pinned tabs"]
check(f"widest title {widest!r} {widths[widest]:.1f} pt ≤ 131", widths[widest] <= 131)
check(f"widest − 'Unpin pinned tabs' = {gap:.1f} pt ≤ 21", gap <= 21)
tip = ns("Check for Updates…").toolTip() or ""
check(f"update tooltip {tip!r} has the version", tidytab.VERSION in tip)

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
