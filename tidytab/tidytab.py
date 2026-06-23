#!/usr/bin/env python3
"""
TidyTab — a macOS menu-bar app that bulk unpins or closes Safari pinned tabs.

It auto-locates the pinned tabs via the macOS Accessibility API, shows you a
confirmation ("Unpin N pinned tabs?"), and on OK acts on each one — no need to
position the mouse. If it can't read the tabs, it offers a manual fallback.

⚠️  Requires Accessibility permission (System Settings → Privacy & Security →
    Accessibility). Without it macOS blocks the app from reading/controlling Safari.

Menu bar: the classic 📌 pushpin by default; the "Icon color" submenu can switch
it to a white/red/… tinted pin. While a run is in progress the menu bar shows
"Space to stop" — Space (or slamming the mouse to a screen corner) aborts.
"""

import os
import time
import threading

import rumps
import pyautogui

from AppKit import NSEvent, NSWorkspace
try:
    from AppKit import NSEventMaskKeyDown
except ImportError:
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
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1

TAB_DISTANCE = 36
COUNTDOWN_SECONDS = 3        # manual-fallback countdown
SPACE_KEYCODE = 49
PINNED_MAX_WIDTH = 72       # a Safari tab this narrow (favicon-only) = pinned

# Menu-bar "Icon color" options → bundled silhouette pngs (None = classic emoji).
CLASSIC = "Classic 📌"
COLOR_FILES = {
    "White": ("menubar_white.png", True),    # template → adapts to the menu bar
    "Red": ("menubar_red.png", False),
    "Orange": ("menubar_orange.png", False),
    "Yellow": ("menubar_yellow.png", False),
    "Green": ("menubar_green.png", False),
    "Blue": ("menubar_blue.png", False),
    "Purple": ("menubar_purple.png", False),
    "Pink": ("menubar_pink.png", False),
}


# ============================================================================ #
# Accessibility helpers
# ============================================================================ #
def accessibility_trusted():
    return bool(AXIsProcessTrusted())


def prompt_accessibility():
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
# Accessibility-API tab finder (read-only)
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
    """Screen-coordinate centres of Safari's pinned tabs (left→right), or []."""
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

    tabs.sort(key=lambda t: t[0])
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
    base = os.environ.get("RESOURCEPATH", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


class TidyTabApp(rumps.App):
    def __init__(self):
        # Default look = white pin (template image) in the menu bar.
        super().__init__(APP_NAME, icon=_res("menubar_white.png"), template=True, quit_button=None)

        self._operation = "unpin"            # "unpin" | "close"
        self._mode = ("auto", [])            # set by _run before the worker starts
        self._stop_flag = threading.Event()
        self._worker = None
        self._space_monitor = None
        self._watchdog = None

        # Idle menu-bar appearance (restored after a run); default = white pin.
        self._idle_title = ""
        self._idle_icon = _res("menubar_white.png")
        self._idle_template = True

        # Operation submenu (Unpin default)
        self._op_unpin = rumps.MenuItem("Unpin tabs", callback=self._set_unpin)
        self._op_close = rumps.MenuItem("Close tabs", callback=self._set_close)
        self._op_unpin.state = True
        op_menu = rumps.MenuItem("Operation")
        op_menu.add(self._op_unpin)
        op_menu.add(self._op_close)

        # Icon-color submenu: Classic 📌 (default) + tinted pins
        color_menu = rumps.MenuItem("Icon color")
        self._icon_items = []
        for label in [CLASSIC] + list(COLOR_FILES.keys()):
            it = rumps.MenuItem(label, callback=self._set_icon)
            self._icon_items.append(it)
            color_menu.add(it)
        self._icon_items[1].state = True  # White (default)

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

        self._launch_check = rumps.Timer(self._launch_accessibility_check, 1.0)
        self._launch_check.start()

    # ---- launch / permission ---------------------------------------------- #
    def _launch_accessibility_check(self, timer):
        timer.stop()
        if not accessibility_trusted():
            rumps.notification(
                APP_NAME, "Accessibility permission needed",
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
    def _apply_idle(self):
        self.template = self._idle_template
        self.icon = self._idle_icon
        self.title = self._idle_title

    def _set_icon(self, sender):
        for it in self._icon_items:
            it.state = (it is sender)
        if sender.title == CLASSIC:
            self._idle_title, self._idle_icon, self._idle_template = "📌", None, False
        else:
            fname, template = COLOR_FILES[sender.title]
            self._idle_title, self._idle_icon, self._idle_template = "", _res(fname), template
        if not (self._worker and self._worker.is_alive()):
            self._apply_idle()

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

        op_label = "Close" if self._operation == "close" else "Unpin"
        try:
            centers = find_pinned_tab_centers()
        except Exception:
            centers = []

        # Confirm BEFORE doing anything — never just-start.
        if centers:
            ok = rumps.alert(
                title=f"{op_label} {len(centers)} pinned tab(s)?",
                message=(f"TidyTab will {op_label.lower()} {len(centers)} pinned "
                         "tab(s) in Safari.\n\nPress Space or move the mouse to a "
                         "screen corner to stop mid-run."),
                ok=op_label, cancel="Cancel",
            )
            if ok != 1:
                return
            self._mode = ("auto", centers)
        else:
            ok = rumps.alert(
                title="No pinned tabs detected",
                message=("TidyTab couldn't read Safari's pinned tabs — make sure a "
                         "Safari window is frontmost (and that TidyTab has "
                         "Accessibility permission).\n\nRun in manual mode instead? "
                         "You'll hover the rightmost pinned tab and it sweeps left."),
                ok="Manual run", cancel="Cancel",
            )
            if ok != 1:
                return
            self._mode = ("manual", [])

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

    # ---- running-state UI -------------------------------------------------- #
    def _begin_running_ui(self):
        self.title = "  Space to stop"
        self._start_space_monitor()
        if self._watchdog is None:
            self._watchdog = rumps.Timer(self._check_done, 0.4)
            self._watchdog.start()

    def _end_running_ui(self):
        self._apply_idle()                  # restore 📌 / chosen color
        self._stop_space_monitor()
        if self._watchdog is not None:
            self._watchdog.stop()
            self._watchdog = None

    def _check_done(self, _timer):
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
        mode, centers = self._mode
        operation = self._operation
        try:
            if mode == "auto":
                time.sleep(0.4)  # brief; the user already confirmed
                done = 0
                # Act rightmost-first: removing a tab never shifts the tabs to its
                # LEFT, so the remaining cached centres stay valid.
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
        except Exception as exc:
            rumps.notification(APP_NAME, "Error", str(exc))

    def _manual_fallback(self, operation):
        rumps.notification(
            APP_NAME, "Manual mode",
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
