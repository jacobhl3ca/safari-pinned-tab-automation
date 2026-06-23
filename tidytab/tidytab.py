#!/usr/bin/env python3
"""
TidyTab — a macOS menu-bar app that bulk unpins or closes Safari pinned tabs.

It tries to auto-locate the pinned tabs via the macOS Accessibility API and act on
each one (no need to position the mouse). If it can't read the tabs, it falls back
to the original "hover the rightmost pinned tab" sweep.

⚠️  Requires Accessibility permission (System Settings → Privacy & Security →
    Accessibility). Without it, macOS silently drops the synthesized clicks AND the
    app can't read Safari's tabs — so TidyTab checks/prompts for it up front.

Menu bar: a white pin icon by default (switchable to red). While a run is in
progress the menu bar shows "Space to stop" and the Space key (or slamming the
mouse into a screen corner) aborts.
"""

import os
import time
import threading

import rumps
import pyautogui

from AppKit import NSEvent, NSWorkspace
try:
    from AppKit import NSEventMaskKeyDown
except ImportError:  # older pyobjc constant name / fallback
    NSEventMaskKeyDown = 1 << 10

from ApplicationServices import (
    AXIsProcessTrusted,
    AXIsProcessTrustedWithOptions,
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
    AXValueGetValue,
    kAXValueCGPointType,
    kAXValueCGSizeType,
    kAXTrustedCheckOptionPrompt,
)

# --- App identity (rename the app by changing this ONE constant) ---------------
APP_NAME = "TidyTab"

# --- pyautogui safety ----------------------------------------------------------
pyautogui.FAILSAFE = True   # slam mouse into a screen corner to abort instantly
pyautogui.PAUSE = 0.1

TAB_DISTANCE = 36           # px between pinned tabs (manual fallback sweep)
COUNTDOWN_SECONDS = 3       # manual-fallback countdown
SPACE_KEYCODE = 49          # macOS virtual key code for the spacebar
PINNED_MAX_WIDTH = 72       # a Safari tab this narrow (favicon-only) = pinned


# ============================================================================ #
# Accessibility helpers
# ============================================================================ #
def accessibility_trusted():
    """True if this process may use the Accessibility API / synthesize input."""
    return bool(AXIsProcessTrusted())


def prompt_accessibility():
    """Ask macOS to show the 'grant Accessibility' prompt for this app."""
    try:
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
    except Exception:
        return False


def open_accessibility_settings():
    os.system(
        "open 'x-apple.systempreferences:com.apple.preference.security"
        "?Privacy_Accessibility'"
    )


# ============================================================================ #
# Accessibility-API tab finder (read-only; returns pinned-tab centre points)
# ============================================================================ #
def _ax_attr(element, name):
    err, value = AXUIElementCopyAttributeValue(element, name, None)
    return value if err == 0 else None


def _ax_point(value):
    if value is None:
        return None
    ok, pt = AXValueGetValue(value, kAXValueCGPointType, None)
    return (pt.x, pt.y) if ok else None


def _ax_size(value):
    if value is None:
        return None
    ok, sz = AXValueGetValue(value, kAXValueCGSizeType, None)
    return (sz.width, sz.height) if ok else None


def _safari_pid():
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.bundleIdentifier() == "com.apple.Safari":
            return app.processIdentifier()
    return None


def _collect_radio_buttons(element, out, depth=0, max_depth=14):
    """Depth-first collect of AXRadioButton elements (Safari tabs are these)."""
    if depth > max_depth:
        return
    try:
        if _ax_attr(element, "AXRole") == "AXRadioButton":
            out.append(element)
        for child in (_ax_attr(element, "AXChildren") or []):
            _collect_radio_buttons(child, out, depth + 1, max_depth)
    except Exception:
        pass


def find_pinned_tab_centers():
    """
    Return screen-coordinate centres of Safari's pinned tabs, left-to-right.

    Pinned tabs are the leftmost contiguous run of favicon-only (narrow) tabs.
    Returns [] if Safari isn't readable — the caller then falls back to manual.
    """
    pid = _safari_pid()
    if not pid:
        return []
    app = AXUIElementCreateApplication(pid)
    window = _ax_attr(app, "AXMainWindow")
    if window is None:
        windows = _ax_attr(app, "AXWindows") or []
        window = windows[0] if windows else None
    if window is None:
        return []

    radios = []
    _collect_radio_buttons(window, radios)

    tabs = []
    for el in radios:
        pos = _ax_point(_ax_attr(el, "AXPosition"))
        sz = _ax_size(_ax_attr(el, "AXSize"))
        if pos and sz and sz[0] > 0:
            tabs.append((pos[0], pos[1], sz[0], sz[1]))
    if not tabs:
        return []

    tabs.sort(key=lambda t: t[0])  # by x (left → right)
    centers = []
    for x, y, w, h in tabs:
        if w <= PINNED_MAX_WIDTH:
            centers.append((x + w / 2.0, y + h / 2.0))
        else:
            break  # first wide (titled) tab ends the pinned run
    return centers


# ============================================================================ #
# Menu-bar app
# ============================================================================ #
def _res(name):
    """Path to a bundled resource (works in dev and inside the py2app bundle)."""
    base = os.environ.get("RESOURCEPATH", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


class TidyTabApp(rumps.App):
    def __init__(self):
        super().__init__(
            APP_NAME,
            icon=_res("menubar_icon.png"),
            template=True,            # white on the dark menu bar by default
            quit_button=None,
        )

        self._operation = "unpin"            # "unpin" | "close"
        self._stop_flag = threading.Event()
        self._worker = None
        self._space_monitor = None
        self._watchdog = None

        # Operation submenu (mutually exclusive, default Unpin)
        self._op_unpin = rumps.MenuItem("Unpin tabs", callback=self._set_unpin)
        self._op_close = rumps.MenuItem("Close tabs", callback=self._set_close)
        self._op_unpin.state = True
        op_menu = rumps.MenuItem("Operation")
        op_menu.add(self._op_unpin)
        op_menu.add(self._op_close)

        # Icon-color submenu (White default, Red option)
        self._ic_white = rumps.MenuItem("White", callback=self._set_icon_white)
        self._ic_red = rumps.MenuItem("Red", callback=self._set_icon_red)
        self._ic_white.state = True
        color_menu = rumps.MenuItem("Icon color")
        color_menu.add(self._ic_white)
        color_menu.add(self._ic_red)

        self.menu = [
            op_menu,
            None,
            rumps.MenuItem("Tidy pinned tabs", callback=self._run),
            rumps.MenuItem("Stop", callback=self._stop),
            None,
            color_menu,
            rumps.MenuItem("Grant Accessibility…", callback=self._grant_accessibility),
            None,
            rumps.MenuItem("Quit", callback=self._quit),
        ]

        # On launch, nudge for Accessibility if it isn't granted yet.
        self._launch_check = rumps.Timer(self._launch_accessibility_check, 1.0)
        self._launch_check.start()

    # ---- launch / permission ---------------------------------------------- #
    def _launch_accessibility_check(self, timer):
        timer.stop()
        if not accessibility_trusted():
            rumps.notification(
                APP_NAME,
                "Accessibility permission needed",
                "Enable TidyTab in Privacy & Security → Accessibility so it can "
                "read and control Safari's tabs.",
            )

    def _grant_accessibility(self, _sender):
        prompt_accessibility()
        open_accessibility_settings()

    # ---- operation toggle -------------------------------------------------- #
    def _set_unpin(self, _s):
        self._operation = "unpin"
        self._op_unpin.state, self._op_close.state = True, False

    def _set_close(self, _s):
        self._operation = "close"
        self._op_unpin.state, self._op_close.state = False, True

    # ---- icon color -------------------------------------------------------- #
    def _set_icon_white(self, _s):
        self.template = True
        self.icon = _res("menubar_icon.png")
        self._ic_white.state, self._ic_red.state = True, False

    def _set_icon_red(self, _s):
        self.template = False
        self.icon = _res("menubar_icon_red.png")
        self._ic_white.state, self._ic_red.state = False, True

    # ---- run / stop -------------------------------------------------------- #
    def _run(self, _sender):
        if self._worker and self._worker.is_alive():
            rumps.notification(APP_NAME, "Already running",
                               "Press Space to stop the current run.")
            return
        if not accessibility_trusted():
            prompt_accessibility()
            open_accessibility_settings()
            rumps.alert(
                f"{APP_NAME} needs Accessibility",
                "Enable TidyTab under Privacy & Security → Accessibility, "
                "then choose “Tidy pinned tabs” again.",
            )
            return

        self._stop_flag.clear()
        self._begin_running_ui()
        self._worker = threading.Thread(target=self._automation_loop, daemon=True)
        self._worker.start()

    def _stop(self, _sender):
        self._stop_flag.set()

    def _quit(self, _sender):
        self._stop_flag.set()
        self._stop_space_monitor()
        rumps.quit_application()

    # ---- running-state UI (menu bar hint + Space monitor + watchdog) ------- #
    def _begin_running_ui(self):
        self.title = "  Space to stop"
        self._start_space_monitor()
        if self._watchdog is None:
            self._watchdog = rumps.Timer(self._check_done, 0.4)
            self._watchdog.start()

    def _end_running_ui(self):
        self.title = ""
        self._stop_space_monitor()
        if self._watchdog is not None:
            self._watchdog.stop()
            self._watchdog = None

    def _check_done(self, _timer):
        # Runs on the main thread; clean up the UI once the worker finishes.
        if self._worker is None or not self._worker.is_alive():
            self._end_running_ui()

    def _start_space_monitor(self):
        if self._space_monitor is not None:
            return

        def handler(event):
            try:
                if event.keyCode() == SPACE_KEYCODE:
                    self._stop_flag.set()
            except Exception:
                pass

        self._space_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, handler
        )

    def _stop_space_monitor(self):
        if self._space_monitor is not None:
            NSEvent.removeMonitor_(self._space_monitor)
            self._space_monitor = None

    # ---- the work ---------------------------------------------------------- #
    def _tidy_one(self, x, y, operation):
        """Unpin/close a single tab whose centre is (x, y)."""
        pyautogui.moveTo(x, y, duration=0.08)
        pyautogui.click()
        time.sleep(0.1)
        pyautogui.click(button="right")
        time.sleep(0.12)
        downs = 3 if operation == "close" else 1
        for _ in range(downs):
            pyautogui.press("down")
            time.sleep(0.04)
        time.sleep(0.05)
        pyautogui.press("enter")
        time.sleep(0.15)

    def _automation_loop(self):
        operation = self._operation
        try:
            centers = find_pinned_tab_centers()
        except Exception:
            centers = []

        try:
            if centers:
                rumps.notification(
                    APP_NAME,
                    f"Tidying {len(centers)} pinned tab(s)",
                    "Press Space (or slam the mouse to a corner) to stop.",
                )
                time.sleep(1.0)  # brief grace so the user can abort
                done = 0
                # Act rightmost-first: removing a tab never shifts the tabs to
                # its LEFT, so the remaining cached centres stay valid.
                for x, y in reversed(centers):
                    if self._stop_flag.is_set():
                        break
                    self._tidy_one(x, y, operation)
                    done += 1
                if not self._stop_flag.is_set():
                    rumps.notification(APP_NAME, "Done",
                                       f"Processed {done} pinned tab(s).")
            else:
                self._manual_fallback(operation)
        except pyautogui.FailSafeException:
            rumps.notification(APP_NAME, "Stopped",
                               "Fail-safe triggered (mouse moved to a corner).")
        except Exception as exc:  # never crash the menu bar
            rumps.notification(APP_NAME, "Error", str(exc))

    def _manual_fallback(self, operation):
        """Old behaviour: user hovers the rightmost pinned tab; sweep left."""
        rumps.notification(
            APP_NAME,
            "Couldn't auto-detect pinned tabs",
            "Hover the RIGHTMOST pinned tab — starting in "
            f"{COUNTDOWN_SECONDS}s. Press Space or hit a corner to stop.",
        )
        time.sleep(COUNTDOWN_SECONDS)
        cycle = 0
        while not self._stop_flag.is_set():
            cycle += 1
            current_x, current_y = pyautogui.position()
            self._tidy_one(current_x, current_y, operation)
            new_x = current_x - TAB_DISTANCE
            if new_x < 0:
                rumps.notification(APP_NAME, "Done",
                                   f"Reached the left edge after {cycle} cycle(s).")
                break
            pyautogui.moveTo(new_x, current_y, duration=0.1)
            time.sleep(0.2)


if __name__ == "__main__":
    TidyTabApp().run()
