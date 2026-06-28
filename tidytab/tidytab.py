#!/usr/bin/env python3
"""
TidyTab — a macOS menu-bar app that bulk unpins or closes Safari pinned tabs.

Pick "Unpin pinned tabs" or "Close pinned tabs" from the menu. TidyTab auto-locates
the pinned tabs via the macOS Accessibility API, shows a confirmation ("Unpin N
pinned tabs?"), and on OK acts on each one — no need to position the mouse. If it
can't read the tabs it offers a manual fallback.

⚠️  Requires Accessibility permission (System Settings → Privacy & Security →
    Accessibility). Without it macOS blocks the app from reading/controlling Safari.

Menu bar: a white pin by default (Icon color → classic 📌 / colors). While a run is
in progress it shows "Space to stop" — Space (or a screen-corner slam) aborts.
"""

from __future__ import annotations

import os
import re
import json
import time
import threading
import subprocess
from typing import Any, Optional
import urllib.request

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
VERSION = "1.1.3"

PREFS_PATH = os.path.expanduser("~/Library/Application Support/TidyTab/prefs.json")
REPO = "jacobhl3ca/safari-pinned-tab-automation"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

# --- pyautogui safety ----------------------------------------------------------
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05      # faster per-call pause (safe: the loop self-corrects/stops)

TAB_DISTANCE = 36
COUNTDOWN_SECONDS = 3
SPACE_KEYCODE = 49
ESC_KEYCODE = 53
PINNED_MAX_WIDTH = 72
ICON_DIM = (15, 15)         # menu-bar icon point size (square pin, matches neighbor menu-bar glyph height)

# Icon-color options: (label, silhouette file or None for the full-colour emoji, template?)
# The flat colour tints come first; the realistic emoji pin sits at the bottom since
# it isn't a colour like the others.
ICON_OPTIONS = [
    ("White", "menubar_white.png", True),       # default; template adapts to the bar
    ("Red", "menubar_red.png", False),
    ("Orange", "menubar_orange.png", False),
    ("Yellow", "menubar_yellow.png", False),
    ("Green", "menubar_green.png", False),
    ("Blue", "menubar_blue.png", False),
    ("Purple", "menubar_purple.png", False),
    ("Pink", "menubar_pink.png", False),
    ("Real 📌", None, False),                    # the full-colour emoji pin (not a flat tint)
]


# ============================================================================ #
# Accessibility helpers
# ============================================================================ #
def accessibility_trusted() -> bool:
    return bool(AXIsProcessTrusted())


def prompt_accessibility() -> bool:
    try:
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
    except Exception:
        return False


def open_accessibility_settings() -> None:
    os.system(
        "open 'x-apple.systempreferences:com.apple.preference.security"
        "?Privacy_Accessibility'"
    )


def activate_safari():
    """Bring Safari to the front AND onto the current Space. Plain `activate` won't
    switch Spaces — *simulating a Dock click* is the one path macOS lets carry you to
    a window on another Space (same technique as Jacob's morning calendar popup)."""
    script = (
        'tell application "Safari"\n'
        '  activate\n'
        '  try\n'
        '    set index of window 1 to 1\n'
        '  end try\n'
        'end tell\n'
        'try\n'
        '  tell application "System Events" to tell process "Dock" '
        'to tell list 1 to click UI element "Safari"\n'
        'end try\n'
    )
    try:
        subprocess.run(["osascript", "-e", script], timeout=8,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def safari_window_on_screen():
    """True iff Safari has a real window on the CURRENT Space (on-screen).

    If Safari is on another Desktop/Space, `activate` may not switch to it (depends
    on a Mission Control pref), so we refuse to click rather than click blindly.
    """
    try:
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGWindowListOptionOnScreenOnly,
            kCGNullWindowID,
        )
        wins = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []
        for w in wins:
            if w.get("kCGWindowOwnerName") == "Safari" and w.get("kCGWindowLayer", 0) == 0:
                b = w.get("kCGWindowBounds", {})
                if b.get("Height", 0) > 120:   # a real browser window, not a tiny element
                    return True
        return False
    except Exception:
        return True   # never block on an inspection error


# --- Launch at login (via a LaunchAgent plist) ---------------------------------
LAUNCH_AGENT = os.path.expanduser("~/Library/LaunchAgents/com.jacob.tidytab.plist")


def _app_executable():
    res = os.environ.get("RESOURCEPATH")
    if res:  # …/TidyTab.app/Contents/Resources  →  …/TidyTab.app/Contents/MacOS/TidyTab
        contents = os.path.dirname(res)
        return os.path.join(contents, "MacOS", "TidyTab")
    return None


def login_item_enabled():
    return os.path.exists(LAUNCH_AGENT)


def set_login_item(enabled):
    exe = _app_executable()
    if enabled and exe:
        os.makedirs(os.path.dirname(LAUNCH_AGENT), exist_ok=True)
        plist = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>'
            '<key>Label</key><string>com.jacob.tidytab</string>'
            f'<key>ProgramArguments</key><array><string>{exe}</string></array>'
            '<key>RunAtLoad</key><true/></dict></plist>\n'
        )
        with open(LAUNCH_AGENT, "w") as f:
            f.write(plist)
    else:
        try:
            os.remove(LAUNCH_AGENT)
        except OSError:
            pass


# --- Preferences (persist the chosen menu-bar colour across launches) -----------
def load_prefs():
    try:
        with open(PREFS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_prefs(d):
    try:
        os.makedirs(os.path.dirname(PREFS_PATH), exist_ok=True)
        with open(PREFS_PATH, "w") as f:
            json.dump(d, f)
    except Exception:
        pass


# --- Update check (compare bundled VERSION to the latest GitHub release tag) -----
def _ver_tuple(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3])


def latest_release_version():
    try:
        req = urllib.request.Request(RELEASES_API, headers={"User-Agent": "TidyTab"})
        data = json.load(urllib.request.urlopen(req, timeout=8))
        return (data.get("tag_name") or "").lstrip("v")
    except Exception:
        return None


# ============================================================================ #
# Accessibility-API tab finder (read-only)
# ============================================================================ #
def _ax_attr(element: Any, name: str) -> Any:
    err, value = AXUIElementCopyAttributeValue(element, name, None)
    return value if err == 0 else None


def _ax_point(value: Any) -> Optional[tuple[float, float]]:
    if value is None:
        return None
    ok, pt = AXValueGetValue(value, kAXValueCGPointType, None)
    return (pt.x, pt.y) if ok else None


def _ax_size(value: Any) -> Optional[tuple[float, float]]:
    if value is None:
        return None
    ok, sz = AXValueGetValue(value, kAXValueCGSizeType, None)
    return (sz.width, sz.height) if ok else None


def _safari_pid() -> Optional[int]:
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.bundleIdentifier() == "com.apple.Safari":
            return app.processIdentifier()
    return None


def _collect_radio_buttons(element: Any, out: list, depth: int = 0, max_depth: int = 14) -> None:
    if depth > max_depth:
        return
    try:
        if _ax_attr(element, "AXRole") == "AXRadioButton":
            out.append(element)
        for child in (_ax_attr(element, "AXChildren") or []):
            _collect_radio_buttons(child, out, depth + 1, max_depth)
    except Exception:
        pass


def find_pinned_tabs() -> list[tuple[Any, tuple[float, float]]]:
    """Return [(ax_element, (cx, cy)), ...] for Safari's pinned tabs, left→right; []
    if none/unreadable. The element lets us try a click-free close via AXPress."""
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

    items = []
    for el in radios:
        pos = _ax_point(_ax_attr(el, "AXPosition"))
        sz = _ax_size(_ax_attr(el, "AXSize"))
        if pos and sz and sz[0] > 0:
            items.append((el, pos[0], pos[1], sz[0], sz[1]))
    if not items:
        return []

    items.sort(key=lambda t: t[1])  # by x
    out = []
    for el, x, y, w, h in items:
        if w <= PINNED_MAX_WIDTH:
            out.append((el, (x + w / 2.0, y + h / 2.0)))
        else:
            break  # first wide (titled) tab ends the pinned run
    return out


def find_pinned_tab_centers():
    return [c for _, c in find_pinned_tabs()]


def close_tab_via_ax(element):
    """Try to close a tab click-free by AXPress-ing its close button. Returns True on
    success, False if no close button was found (caller falls back to clicking)."""
    try:
        from ApplicationServices import AXUIElementPerformAction
        for child in (_ax_attr(element, "AXChildren") or []):
            if _ax_attr(child, "AXRole") != "AXButton":
                continue
            label = ((_ax_attr(child, "AXDescription") or "") + " " +
                     (_ax_attr(child, "AXTitle") or "")).lower()
            if "close" in label:
                return AXUIElementPerformAction(child, "AXPress") == 0
        return False
    except Exception:
        return False


# ============================================================================ #
# Menu-bar app
# ============================================================================ #
def _res(name: str) -> str:
    base = os.environ.get("RESOURCEPATH", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


class TidyTabApp(rumps.App):
    def __init__(self):
        super().__init__(APP_NAME, quit_button=None)

        self._mode = ("auto", [])
        self._operation = "unpin"
        self._stop_flag = threading.Event()
        self._worker = None
        self._space_monitor = None
        self._watchdog = None

        # Idle menu-bar look (restored after a run); default = white pin.
        self._idle_icon = _res("menubar_white.png")
        self._idle_title = ""
        self._idle_template = True

        self._login_item = rumps.MenuItem("Launch at login", callback=self._toggle_login)
        self._login_item.state = login_item_enabled()
        self._autoupdate_item = rumps.MenuItem("Auto-update on launch", callback=self._toggle_autoupdate)
        self._autoupdate_item.state = load_prefs().get("auto_update", True)

        # Two explicit actions (no hidden mode) — clear what each does.
        self.menu = [
            rumps.MenuItem("Unpin pinned tabs  (⌘⌥U)", callback=self._run_unpin),
            rumps.MenuItem("Close pinned tabs  (⌘⌥K)", callback=self._run_close),
            rumps.MenuItem("Stop", callback=self._stop),
            None,
            self._build_color_menu(),
            self._login_item,
            self._autoupdate_item,
            rumps.MenuItem("Grant Accessibility…", callback=self._grant_accessibility),
            None,
            rumps.MenuItem(f"Check for Updates…  (v{VERSION})", callback=self._check_updates),
            rumps.MenuItem("Quit", callback=self._quit),
        ]

        self._load_saved_color()   # restore the menu-bar colour chosen last time
        self._apply_idle()

        self._launch_check = rumps.Timer(self._launch_accessibility_check, 1.0)
        self._launch_check.start()
        self._hotkey_monitor = None
        self._start_hotkey_monitor()
        self._auto_update_on_launch()  # self-update on launch if a newer release exists
        self._update_timer = rumps.Timer(lambda _t: self._auto_update_on_launch(), 14400)
        self._update_timer.start()     # …and re-check every ~4h for long-running sessions

    def _toggle_login(self, sender):
        sender.state = not sender.state
        set_login_item(bool(sender.state))

    def _toggle_autoupdate(self, sender):
        sender.state = not sender.state
        prefs = load_prefs(); prefs["auto_update"] = bool(sender.state); save_prefs(prefs)

    def _load_saved_color(self):
        saved = load_prefs().get("color")
        if not saved or saved not in self._icon_map:
            return
        for it in self._icon_items:
            it.state = (it.title == saved)
        fname, template = self._icon_map[saved]
        if fname is None:
            self._idle_icon, self._idle_title, self._idle_template = None, "📌", False
        else:
            self._idle_icon, self._idle_title, self._idle_template = _res(fname), "", template

    def _check_updates(self, _sender=None):
        """Manual (menu) check — runs on the main thread so it shows a VISIBLE modal
        result (rumps notifications were silently not displaying)."""
        latest = latest_release_version()   # brief block; fine for a user-initiated click
        if latest is None:
            rumps.alert(APP_NAME, "Couldn't reach GitHub to check for updates. "
                                  "Check your connection and try again.")
        elif _ver_tuple(latest) > _ver_tuple(VERSION):
            if rumps.alert(APP_NAME, f"Update available: v{latest}.\nDownload and install now?",
                           ok="Update", cancel="Later") == 1:
                self._do_self_update(latest)
        else:
            rumps.alert(APP_NAME, f"You're on the latest version (v{VERSION}).")

    def _auto_update_on_launch(self):
        if not load_prefs().get("auto_update", True):
            return

        def work():
            latest = latest_release_version()
            if not (latest and _ver_tuple(latest) > _ver_tuple(VERSION)):
                return
            if load_prefs().get("update_attempted") == latest:   # tried + still behind → don't loop
                rumps.notification(APP_NAME, f"Update v{latest} available",
                                   "Auto-update didn't apply — use “Check for Updates…”.")
                return
            self._do_self_update(latest)
        threading.Thread(target=work, daemon=True).start()

    def _do_self_update(self, latest):
        """Download the latest notarized dmg, verify its signature, swap it into
        /Applications, and relaunch. Guarded against update loops + bad downloads."""
        try:
            import shutil
            rumps.notification(APP_NAME, f"Updating to v{latest}…",
                               "Downloading — TidyTab will relaunch.")
            prefs = load_prefs(); prefs["update_attempted"] = latest; save_prefs(prefs)
            dmg = "/tmp/TidyTab_update.dmg"
            urllib.request.urlretrieve(
                f"https://github.com/{REPO}/releases/latest/download/TidyTab.dmg", dmg)
            mnt = "/tmp/tidytab_update_mnt"
            subprocess.run(["hdiutil", "detach", mnt],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # clear any stale mount
            subprocess.run(["hdiutil", "attach", dmg, "-nobrowse", "-mountpoint", mnt],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            src = os.path.join(mnt, "TidyTab.app")
            ok = os.path.exists(src) and subprocess.run(
                ["codesign", "--verify", "--quiet", src]).returncode == 0
            if ok:
                staging = "/Applications/.TidyTab.new"
                shutil.rmtree(staging, ignore_errors=True)
                shutil.copytree(src, staging)
                shutil.rmtree("/Applications/TidyTab.app", ignore_errors=True)
                os.rename(staging, "/Applications/TidyTab.app")
            subprocess.run(["hdiutil", "detach", mnt],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ok:
                subprocess.Popen(["open", "/Applications/TidyTab.app"])
                rumps.quit_application()
            else:
                rumps.notification(APP_NAME, "Update skipped",
                                   "The downloaded update failed verification — try again later.")
        except Exception as exc:
            rumps.notification(APP_NAME, "Update failed", str(exc))

    def _start_hotkey_monitor(self):
        # Always-on global hotkeys: ⌘⌥U = Unpin, ⌘⌥K = Close (no need to open the menu).
        if self._hotkey_monitor is not None:
            return

        def handler(event):
            try:
                flags = event.modifierFlags()
                if (flags & (1 << 20)) and (flags & (1 << 19)):   # ⌘ and ⌥
                    ch = (event.charactersIgnoringModifiers() or "").lower()
                    if ch == "u":
                        self._run_unpin(None)
                    elif ch == "k":
                        self._run_close(None)
            except Exception:
                pass

        self._hotkey_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, handler
        )

    # ---- icon color submenu ----------------------------------------------- #
    def _build_color_menu(self):
        color_menu = rumps.MenuItem("Icon color")
        self._icon_items = []
        self._icon_map = {}
        for label, fname, template in ICON_OPTIONS:
            it = rumps.MenuItem(label, callback=self._set_icon)
            self._icon_items.append(it)
            self._icon_map[label] = (fname, template)
            color_menu.add(it)
        self._icon_items[0].state = True  # White (default)
        return color_menu

    def _apply_idle(self):
        if self._idle_icon is None:           # classic 📌 emoji
            self.icon = None
            self.title = self._idle_title
        else:                                 # tinted pin image (resized to ICON_DIM)
            self.template = self._idle_template
            self.icon = self._idle_icon
            self.title = ""
            # Force the live status item to image-only. rumps' fallbackOnName() can
            # leave the app name ("TidyTab") next to the pin when title+image are
            # briefly both empty during a colour swap; setting the image + clearing
            # the title LAST guarantees just the icon shows.
            try:
                img = self._icon_nsimage
                item = getattr(getattr(self, "_nsapp", None), "nsstatusitem", None)
                if img is not None and item is not None:
                    img.setSize_(ICON_DIM)
                    item.setImage_(img)
                    item.setTitle_("")
            except Exception:
                pass

    def _set_icon(self, sender):
        for it in self._icon_items:
            it.state = (it is sender)
        fname, template = self._icon_map[sender.title]
        if fname is None:
            self._idle_icon, self._idle_title, self._idle_template = None, "📌", False
        else:
            self._idle_icon, self._idle_title, self._idle_template = _res(fname), "", template
        prefs = load_prefs(); prefs["color"] = sender.title; save_prefs(prefs)
        if not (self._worker and self._worker.is_alive()):
            self._apply_idle()

    # ---- launch / permission ---------------------------------------------- #
    def _launch_accessibility_check(self, timer):
        timer.stop()
        if not load_prefs().get("onboarded"):
            self._onboard()                 # first launch → friendly walkthrough
        elif not accessibility_trusted():
            rumps.notification(
                APP_NAME, "Accessibility permission needed",
                "Enable TidyTab in Privacy & Security → Accessibility so it can "
                "read and control Safari's tabs.",
            )

    def _onboard(self):
        prefs = load_prefs(); prefs["onboarded"] = True; save_prefs(prefs)
        resp = rumps.alert(
            title=f"Welcome to {APP_NAME} 📌",
            message=(
                "TidyTab clears your Safari pinned tabs in one sweep.\n\n"
                "• Click the menu-bar pin → “Unpin pinned tabs” or “Close pinned tabs”\n"
                "• Or use the hotkeys ⌘⌥U (unpin) / ⌘⌥K (close)\n"
                "• It confirms the count first; press Space, Esc, or a screen corner to stop\n\n"
                "One-time setup: TidyTab needs Accessibility permission to control Safari. "
                "Click “Open Settings,” switch on TidyTab under Accessibility, and you're ready."
            ),
            ok="Open Settings", cancel="Later",
        )
        if resp == 1:
            prompt_accessibility()
            open_accessibility_settings()

    def _grant_accessibility(self, _sender):
        prompt_accessibility()
        open_accessibility_settings()

    # ---- run / stop -------------------------------------------------------- #
    def _run_unpin(self, _sender):
        self._operation = "unpin"
        self._start()

    def _run_close(self, _sender):
        self._operation = "close"
        self._start()

    def _start(self):
        if self._worker and self._worker.is_alive():
            rumps.notification(APP_NAME, "Already running",
                               "Press Space to stop the current run.")
            return
        if not accessibility_trusted():
            prompt_accessibility()
            open_accessibility_settings()
            rumps.alert(
                f"{APP_NAME} needs Accessibility",
                "Enable TidyTab under Privacy & Security → Accessibility, then try again.",
            )
            return

        if _safari_pid() is None:
            rumps.alert(APP_NAME,
                        "Safari isn't running. Open Safari with some pinned tabs, then try again.")
            return

        # Bring Safari to the front first (handles it being on another Space) so we
        # never click into the wrong app, then detect its pinned tabs.
        activate_safari()
        time.sleep(0.7)

        # If Safari is on a DIFFERENT Space and macOS didn't switch to it, its window
        # isn't on-screen — refuse to click rather than click into whatever IS here.
        if not safari_window_on_screen():
            rumps.alert(
                f"{APP_NAME}: Safari is on another Space",
                "Safari's window is on a different desktop/Space and macOS didn't switch "
                "to it, so TidyTab won't click. Switch to the Safari window yourself (or "
                "turn on System Settings → Desktop & Dock → Mission Control → “When "
                "switching to an application, switch to a Space with open windows for the "
                "application”), then run TidyTab again. It acts on the FRONT Safari "
                "window's pinned tabs.",
            )
            return

        op_label = "Close" if self._operation == "close" else "Unpin"
        try:
            centers = find_pinned_tab_centers()
        except Exception:
            centers = []

        if not centers:
            rumps.alert(
                f"{APP_NAME}: no pinned tabs found",
                "Couldn't find pinned tabs in the front Safari window. Make sure Safari "
                "is open with pinned tabs (and TidyTab has Accessibility permission), "
                "then try again. (TidyTab won't click unless it has located the tabs.)",
            )
            return

        n = len(centers)
        tabs_word = "tab" if n == 1 else "tabs"
        ok = rumps.alert(
            title=f"{op_label} {n} pinned {tabs_word}?",
            message=(f"TidyTab will {op_label.lower()} {n} pinned {tabs_word} in "
                     "Safari.\n\nPress Space or Esc to stop mid-run."),
            ok=op_label, cancel="Cancel",
        )
        if ok != 1:
            return
        self._mode = ("auto", centers)

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
        self.title = "  Space/Esc to stop"
        self._start_space_monitor()
        if self._watchdog is None:
            self._watchdog = rumps.Timer(self._check_done, 0.4)
            self._watchdog.start()

    def _end_running_ui(self):
        self._apply_idle()
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
                if event.keyCode() in (SPACE_KEYCODE, ESC_KEYCODE):
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
        _, centers = self._mode
        operation = self._operation
        try:
            # The confirm dialog stole focus — bring Safari back before clicking.
            activate_safari()
            time.sleep(0.5)
            done = 0
            expected = len(centers)
            last_count = None
            row_y = None
            # Self-correcting: re-detect after every action, act on the rightmost
            # remaining pinned tab, stop when none are left, and bail if the count
            # ISN'T dropping — so a missed click can never become runaway clicking.
            while not self._stop_flag.is_set():
                current = find_pinned_tabs()
                if not current:
                    break                                   # all done
                if last_count is not None and len(current) >= last_count:
                    rumps.notification(APP_NAME, "Stopped",
                                       "A pinned tab didn't change — stopping to be safe.")
                    break
                last_count = len(current)
                el, (x, cy) = current[-1]                   # rightmost remaining pinned tab
                if row_y is None:
                    row_y = cy                              # lock the row's y → no vertical jitter
                # Click-free close when the tab exposes a close button via the AX API;
                # otherwise (and always for unpin) fall back to synthesized clicks on
                # a single locked y, so the cursor sweeps cleanly left, not up-and-down.
                if operation == "close" and close_tab_via_ax(el):
                    pass
                else:
                    self._tidy_one(x, row_y, operation)
                done += 1
                if done > expected + 3:                     # hard cap; never loop forever
                    break
                time.sleep(0.12)
            if not self._stop_flag.is_set():
                rumps.notification(APP_NAME, "Done",
                                   f"Processed {done} pinned tab{'' if done == 1 else 's'}.")
        except pyautogui.FailSafeException:
            rumps.notification(APP_NAME, "Stopped",
                               "Fail-safe triggered (mouse moved to a corner).")
        except Exception as exc:
            rumps.notification(APP_NAME, "Error", str(exc))


if __name__ == "__main__":
    TidyTabApp().run()
