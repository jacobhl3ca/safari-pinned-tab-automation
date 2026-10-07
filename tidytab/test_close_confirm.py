"""Close-confirm checks: Return and Esc both cancel the "Close N pinned tabs?"
prompt, and only a click on Close runs it. Unpin and Pin use _alert (Return =
the action). _start stays on this desktop when it has a Safari window. The Hide alert has the Launch-at-login line.

Key proof is in process: a timer posts a synthetic keyDown into the app's own
event queue while the real NSAlert is modal. Each dialog is up for under 1 s.
Run it outside the sandbox: inside it the app can't activate, so the alert is
never the key window and Return does nothing."""
import os, sys, tempfile, threading, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tidytab
from AppKit import (NSApplication, NSApp, NSEvent, NSRunLoop, NSModalPanelRunLoopMode,
                    NSTimer, NSAlertFirstButtonReturn)

tidytab.PREFS_PATH = os.path.join(tempfile.mkdtemp(), "prefs.json")
fails = 0
def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name); fails += (not cond)

app = NSApplication.sharedApplication()
app.setActivationPolicy_(1)  # accessory, like LSUIElement

def bind(f, *names):
    for m in names:
        setattr(f, m, types.MethodType(getattr(tidytab.TidyTabApp, m), f))
    return f

# 1. button layout
f = bind(types.SimpleNamespace(), "_build_destructive_alert", "_confirm_destructive")
a = f._build_destructive_alert("Close 3 pinned tabs?", "msg", "Close")
b = list(a.buttons())
check(f"button 0 = Cancel, key {b[0].keyEquivalent()!r}", b[0].title() == "Cancel" and b[0].keyEquivalent() == "\r")
check(f"button 1 = Close, key {b[1].keyEquivalent()!r}", b[1].title() == "Close" and b[1].keyEquivalent() == "")
check("Close is marked destructive",
      not b[1].respondsToSelector_(b"hasDestructiveAction") or bool(b[1].hasDestructiveAction()))

# 2. real modal: Return, Esc, click
def run_with(action):
    """Run _confirm_destructive; 0.3 s in, do `action(alert)`. A 3 s fallback
    stops the modal and marks a timeout (so a key that does nothing fails)."""
    state = {"timeout": False}
    real_build = tidytab.TidyTabApp._build_destructive_alert
    def build(self, *args):
        state["alert"] = real_build(self, *args)
        return state["alert"]
    f._build_destructive_alert = types.MethodType(build, f)
    def fire(t):
        # keys go to the KEY window; activation can lag the first dialog a beat
        if state.get("fired") or not state["alert"].window().isKeyWindow():
            return
        state["fired"] = True
        t.invalidate()
        action(state["alert"])
    def fallback(_t):
        if NSApp().modalWindow() is not None:
            state["timeout"] = True
            NSApp().stopModalWithCode_(NSAlertFirstButtonReturn)
    loop = NSRunLoop.currentRunLoop()
    loop.addTimer_forMode_(NSTimer.timerWithTimeInterval_repeats_block_(0.3, True, fire), NSModalPanelRunLoopMode)
    loop.addTimer_forMode_(NSTimer.timerWithTimeInterval_repeats_block_(3.0, False, fallback), NSModalPanelRunLoopMode)
    result = f._confirm_destructive("Close 3 pinned tabs?", "TidyTab will close 3 pinned tabs in Safari.", "Close")
    f._build_destructive_alert = types.MethodType(real_build, f)
    return result, ("timeout" if state["timeout"] else "") + ("" if state.get("fired") else " never-key")

def key(chars, code):
    def post(alert):
        w = alert.window()
        for typ in (10, 11):  # keyDown, keyUp
            ev = NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
                typ, (0, 0), 0, 0, w.windowNumber(), None, chars, chars, False, code)
            NSApp().postEvent_atStart_(ev, False)
    return post

r, to = run_with(key("\r", 36))
check(f"Return -> cancel (result {r}, timeout {to})", r is False and not to)
r, to = run_with(key("\x1b", 53))
check(f"Esc -> cancel (result {r}, timeout {to})", r is False and not to)
r, to = run_with(lambda alert: alert.buttons()[1].performClick_(None))
check(f"click Close -> True (result {r}, timeout {to})", r is True and not to)

# 3. _start routing
activated, raised_here = [], [True]
for name, val in [("accessibility_trusted", lambda: True), ("_safari_pid", lambda: 1),
                  ("activate_safari", lambda: activated.append(1)),
                  ("raise_safari_window_here", lambda: raised_here[0]),
                  ("safari_window_on_screen", lambda: True),
                  ("find_pinned_tab_centers", lambda: [(1, 1), (2, 2)]),
                  ("find_unpinned_tab_centers", lambda: [(1, 1)])]:
    setattr(tidytab, name, val)
tidytab.time = types.SimpleNamespace(sleep=lambda _s: None)

def start(op, confirm, alert_ret=1):
    calls = []
    s = types.SimpleNamespace(_worker=None, _operation=op, _stop_flag=threading.Event())
    s._confirm_destructive = lambda *a: calls.append(("confirm", a)) or confirm
    s._alert = lambda *a, **k: calls.append(("alert", k)) or alert_ret
    s._begin_running_ui = lambda: calls.append(("ui",))
    s._automation_loop = lambda: None
    s._notify = lambda *a: None
    bind(s, "_start")
    s._start()
    return calls, s._worker

calls, w = start("close", False)
check(f"close + Cancel: no run {[c[0] for c in calls]}", [c[0] for c in calls] == ["confirm"] and w is None)
check("close prompt text unchanged",
      calls[0][1] == ("Close 2 pinned tabs?",
                      "TidyTab will close 2 pinned tabs in Safari.\n\nPress Space or Esc to stop mid-run.",
                      "Close"))
calls, w = start("close", True)
check(f"close + Close: run starts {[c[0] for c in calls]}", [c[0] for c in calls] == ["confirm", "ui"] and w is not None)
calls, w = start("unpin", True, alert_ret=1)
check(f"unpin uses _alert {[c[0] for c in calls]}", [c[0] for c in calls] == ["alert", "ui"]
      and calls[0][1]["ok"] == "Unpin")
calls, w = start("pin", True, alert_ret=0)
check(f"pin + Cancel uses _alert, no run {[c[0] for c in calls]}", [c[0] for c in calls] == ["alert"] and w is None)

# 3b. desktop: a Safari window here -> no Dock-click activate; none here -> fallback
activated.clear(); raised_here[0] = True
start("pin", True, alert_ret=0)
check(f"Safari window on this desktop: activate_safari not called ({len(activated)})", activated == [])
activated.clear(); raised_here[0] = False
start("pin", True, alert_ret=0)
check(f"no Safari window here: activate_safari called ({len(activated)})", activated == [1])
raised_here[0] = True

# 4. Hide alert line + shortcut order
seen = {}
h = types.SimpleNamespace(_alert=lambda **k: seen.update(k) or 0, _set_icon_hidden=lambda v: None)
bind(h, "_hide_icon")._hide_icon(None)
check("Hide alert has the Launch at login line",
      "To keep the icon hidden after a restart, turn on Launch at login." in seen.get("message", ""))
src = open(tidytab.__file__.replace(".pyc", ".py")).read()
check("Hide alert uses ⌥⌘ order", "⌥⌘U / ⌥⌘K / ⌥⌘P" in seen.get("message", ""))
check("welcome alert uses ⌥⌘ order", "• Or use ⌥⌘U (unpin) / ⌥⌘K (close) / ⌥⌘P (pin all)" in src)

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
