"""Alert checks (1.2.6): every _alert is the key window, so its `ok` button draws
blue and takes Return, even when another app takes key a beat after the alert
opens. Also the "stay on this desktop" window match in raise_safari_window_here.

Key proof is in process, as in test_close_confirm.py: a timer posts a synthetic
keyDown into the app's own event queue while the real NSAlert is modal. Each
dialog is up for about 1 s. The steal test activates Finder for a moment.
Run it outside the sandbox: inside it the app can't activate."""
import os, sys, tempfile, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tidytab
from AppKit import (NSApplication, NSApp, NSEvent, NSRunLoop, NSModalPanelRunLoopMode,
                    NSTimer, NSAlertFirstButtonReturn, NSRunningApplication)

tidytab.PREFS_PATH = os.path.join(tempfile.mkdtemp(), "prefs.json")
fails = 0
def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name); fails += (not cond)

app = NSApplication.sharedApplication()
app.setActivationPolicy_(1)  # accessory, like LSUIElement

f = types.SimpleNamespace()
f._alert = types.MethodType(tidytab.TidyTabApp._alert, f)

def key(chars, code):
    def post(alert):
        w = alert.window()
        for typ in (10, 11):  # keyDown, keyUp
            ev = NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
                typ, (0, 0), 0, 0, w.windowNumber(), None, chars, chars, False, code)
            NSApp().postEvent_atStart_(ev, False)
    return post

def at(delay, fn):
    NSRunLoop.currentRunLoop().addTimer_forMode_(
        NSTimer.timerWithTimeInterval_repeats_block_(delay, False, lambda _t: fn()),
        NSModalPanelRunLoopMode)

def run_alert(kwargs, steps):
    """Run f._alert(**kwargs). `steps` = [(delay, fn(state))]. A 3 s fallback
    stops the modal and marks a timeout (so a key that does nothing fails)."""
    state = {"timeout": False}
    real_build = tidytab._build_alert
    def build(*a):
        state["alert"] = real_build(*a)
        return state["alert"]
    tidytab._build_alert = build
    for delay, fn in steps:
        at(delay, lambda fn=fn: fn(state))
    def fallback():
        if NSApp().modalWindow() is not None:
            state["timeout"] = True
            NSApp().stopModalWithCode_(NSAlertFirstButtonReturn)
    at(3.0, fallback)
    try:
        state["result"] = f._alert(**kwargs)
    finally:
        tidytab._build_alert = real_build
    return state

def when_key(action):
    """Post `action` once the alert is key (poll every 0.1 s, up to 1 s)."""
    def go(state, tries=[0]):
        if state["alert"].window().isKeyWindow() or tries[0] >= 10:
            state["key_at_post"] = state["alert"].window().isKeyWindow()
            # read while modal: AppKit clears the default key once the alert closes
            state["buttons"] = [(x.title(), x.keyEquivalent()) for x in state["alert"].buttons()]
            action(state["alert"])
        else:
            tries[0] += 1
            at(0.1, lambda: go(state, tries))
    return go

# 1. buttons + Return / Esc
st = run_alert(dict(title="Unpin 6 pinned tabs?", message="msg", ok="Unpin", cancel="Cancel"),
               [(0.3, when_key(key("\r", 36)))])
check(f"buttons while modal = Unpin/Return, Cancel/Esc {st['buttons']}",
      st["buttons"] == [("Unpin", "\r"), ("Cancel", "\x1b")])
check(f"Return -> 1 (result {st['result']}, timeout {st['timeout']}, key {st.get('key_at_post')})",
      st["result"] == 1 and not st["timeout"] and st.get("key_at_post"))
st = run_alert(dict(title="Unpin 6 pinned tabs?", message="msg", ok="Unpin", cancel="Cancel"),
               [(0.3, when_key(key("\x1b", 53)))])
check(f"Esc -> 0 (result {st['result']}, timeout {st['timeout']})", st["result"] == 0 and not st["timeout"])
st = run_alert(dict(title=tidytab.APP_NAME, message="You're on the latest version."),
               [(0.3, when_key(key("\r", 36)))])
b = list(st["alert"].buttons())
check(f"message-only: one OK button {[x.title() for x in b]}", [x.title() for x in b] == ["OK"])
check(f"message-only: Return -> 1 (result {st['result']}, timeout {st['timeout']})",
      st["result"] == 1 and not st["timeout"])

# 2. steal: Finder takes key 0.1 s in; the retry timer takes it back by 0.8 s
finder = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.finder")
def steal(state):
    finder[0].activateWithOptions_(2)
def look(state):
    state["key_at_0_8"] = state["alert"].window().isKeyWindow()
    key("\r", 36)(state["alert"])
st = run_alert(dict(title="Unpin 6 pinned tabs?", message="msg", ok="Unpin", cancel="Cancel"),
               [(0.1, steal), (0.8, look)])
check(f"steal: alert key again at 0.8 s ({st.get('key_at_0_8')})", st.get("key_at_0_8") is True)
check(f"steal: Return -> 1, no timeout (result {st['result']}, timeout {st['timeout']})",
      st["result"] == 1 and not st["timeout"])

# 3. raise_safari_window_here: CG + AX patched
import Quartz, ApplicationServices, AppKit
saved = {n: getattr(tidytab, n) for n in ("_safari_pid", "AXUIElementCreateApplication",
                                          "_ax_attr", "_ax_point", "_ax_size")}
saved_cg = Quartz.CGWindowListCopyWindowInfo
saved_perform = ApplicationServices.AXUIElementPerformAction
saved_running = AppKit.NSRunningApplication

def setup(cg_windows, ax_windows):
    log = {"raised": [], "activated": []}
    Quartz.CGWindowListCopyWindowInfo = lambda *_a: cg_windows
    tidytab._safari_pid = lambda: 4242
    tidytab.AXUIElementCreateApplication = lambda pid: ("app", pid)
    tidytab._ax_attr = lambda el, name: (ax_windows if name == "AXWindows"
                                         else el[name] if isinstance(el, dict) else None)
    tidytab._ax_point = lambda v: v
    tidytab._ax_size = lambda v: v
    ApplicationServices.AXUIElementPerformAction = lambda el, act: log["raised"].append((el["id"], act)) or 0
    class Running:
        def activateWithOptions_(self, opts): log["activated"].append(opts); return True
    AppKit.NSRunningApplication = types.SimpleNamespace(
        runningApplicationWithProcessIdentifier_=lambda pid: Running())
    return log

safari = lambda x, y, w, h, layer=0: {"kCGWindowOwnerName": "Safari", "kCGWindowLayer": layer,
                                      "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h}}
try:
    cg = [{"kCGWindowOwnerName": "Finder", "kCGWindowLayer": 0,
           "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 500, "Height": 500}},
          safari(100, 40, 1200, 800)]
    check(f"bounds = first on-screen Safari window {tidytab._on_screen_safari_bounds() if setup(cg, []) else ''}",
          tidytab._on_screen_safari_bounds() == {"X": 100, "Y": 40, "Width": 1200, "Height": 800})
    ax = [{"id": "other", "AXPosition": (900, 40), "AXSize": (1200, 800)},
          {"id": "here", "AXPosition": (101.5, 41), "AXSize": (1199, 801.5)}]
    log = setup(cg, ax)
    r = tidytab.raise_safari_window_here()
    check(f"match within 2 pt: AXRaise on that window, True ({r}, {log})",
          r is True and log["raised"] == [("here", "AXRaise")] and log["activated"] == [2])
    log = setup(cg, [{"id": "far", "AXPosition": (104, 40), "AXSize": (1200, 800)}])
    r = tidytab.raise_safari_window_here()
    check(f"off by 4 pt: no raise, False ({r}, {log})", r is False and not log["raised"] and not log["activated"])
    log = setup([cg[0], safari(0, 0, 400, 100)], ax)   # only a tiny Safari element on screen
    r = tidytab.raise_safari_window_here()
    check(f"no on-screen Safari window: False, nothing raised ({r}, {log})",
          r is False and not log["raised"] and not log["activated"])
    check("safari_window_on_screen agrees (False)", tidytab.safari_window_on_screen() is False)
    def boom(*_a): raise RuntimeError("CG error")
    Quartz.CGWindowListCopyWindowInfo = boom
    check("CG error: safari_window_on_screen -> True, raise_here -> False",
          tidytab.safari_window_on_screen() is True and tidytab.raise_safari_window_here() is False)
finally:
    for n, v in saved.items():
        setattr(tidytab, n, v)
    Quartz.CGWindowListCopyWindowInfo = saved_cg
    ApplicationServices.AXUIElementPerformAction = saved_perform
    AppKit.NSRunningApplication = saved_running

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
