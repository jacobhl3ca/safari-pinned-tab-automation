#!/usr/bin/env python3
"""
TidyTab — a macOS menu-bar app that bulk pins, unpins, or closes Safari tabs.

Pick "Unpin pinned tabs", "Close pinned tabs", or "Pin all tabs" from the menu.
TidyTab auto-locates the relevant tabs via the macOS Accessibility API, shows a
confirmation, and on OK acts on each one — no need to position the mouse.

⚠️  Requires Accessibility permission (System Settings → Privacy & Security →
    Accessibility). Without it macOS blocks the app from reading/controlling Safari.

Menu bar: a white pin that adapts to the bar (light/dark). While a run is in
progress it shows "Space to stop" — Space (or a screen-corner slam) aborts.
"""

import os
import re
import sys
import json
import time
import threading
import subprocess
import urllib.request

import objc
import rumps
import pyautogui
from rumps.rumps import NSApp as _RumpsDelegate

from AppKit import (NSAlert, NSAlertFirstButtonReturn, NSAlertSecondButtonReturn, NSAppearance,
                    NSApplication, NSEvent, NSModalPanelRunLoopMode, NSRunLoop, NSTimer,
                    NSUserDefaults, NSWorkspace)
from PyObjCTools.AppHelper import callAfter
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
VERSION = "1.2.6"
# Developer ID team. The self-updater pins downloaded builds to this, so only a
# .app we signed can replace the running one. Must match the certificate used in
# DISTRIBUTION.md step 2 ("Developer ID Application: … (V45QZXMDAW)").
TEAM_ID = "V45QZXMDAW"

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
ICON_DIM = (20, 20)         # menu-bar icon FOOTPRINT in points = rumps' own default (NSStatusItem fits a
                            # template image to the ~22pt bar regardless). The visible pin SIZE is controlled
                            # by transparent padding baked into menubar_white.png (~70% ink, ~30% margin),
                            # so the pin's ink lands ~14pt — flush with neighbor SF-Symbol glyphs.

# Menu labels for the three actions + Stop. Each shortcut is a real key equivalent on
# its item (see _with_shortcut), so macOS draws it in the menu's right-hand shortcut
# column, lined up like any Mac menu. Shortcuts typed into the title text could never
# line up: the menu font is proportional. The hotkeys themselves come from the global
# monitor in _start_hotkey_monitor; a status-bar menu's key equivalents only act while
# that menu is open.
# The shortcut column starts after the WIDEST title in the whole menu, so every other
# title stays short (≤ "Hide menu-bar icon…"): the version lives in the update item's
# tooltip, not its title. Stop is gray while no run is active.
UNPIN_TITLE = "Unpin pinned tabs"
CLOSE_TITLE = "Close pinned tabs"
PIN_TITLE = "Pin all tabs"
STOP_TITLE = "Stop"
CMD_OPT = (1 << 20) | (1 << 19)     # ⌘⌥ — the same two bits the hotkey monitor checks
ESC_KEY = "\x1b"                    # drawn as ⎋; Space stops a run too (_start_space_monitor)
GRANT_TITLE = "Grant Accessibility…"
HIDE_ICON_TITLE = "Hide menu-bar icon…"

# Launch-at-login passes this flag, so a login launch can keep a hidden icon hidden
# while a launch the user makes by hand brings it back (see _apply_icon_visibility).
LOGIN_ARG = "--login"
RELAUNCH_WINDOW = 120       # seconds: a self-update relaunch inside this window also stays hidden


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


def _on_screen_safari_bounds():
    """Bounds dict (X, Y, Width, Height) of the front Safari window on the CURRENT
    Space, or None. Raises on an inspection error; callers pick the safe default."""
    from Quartz import (
        CGWindowListCopyWindowInfo,
        kCGWindowListOptionOnScreenOnly,
        kCGNullWindowID,
    )
    wins = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []
    for w in wins:   # front to back
        if w.get("kCGWindowOwnerName") == "Safari" and w.get("kCGWindowLayer", 0) == 0:
            b = w.get("kCGWindowBounds", {})
            if b.get("Height", 0) > 120:   # a real browser window, not a tiny element
                return b
    return None


def safari_window_on_screen():
    """True iff Safari has a real window on the CURRENT Space (on-screen).

    If Safari is on another Desktop/Space, `activate` may not switch to it (depends
    on a Mission Control pref), so we refuse to click rather than click blindly.
    """
    try:
        return _on_screen_safari_bounds() is not None
    except Exception:
        return True   # never block on an inspection error


def raise_safari_window_here():
    """Bring forward the Safari window on the CURRENT Space and activate Safari
    without the Dock click, so macOS does not jump to the Space of Safari's
    most-recent window. Returns False when this Space has no Safari window (or AX
    can't match it); the caller then falls back to activate_safari()."""
    try:
        bounds = _on_screen_safari_bounds()
        pid = _safari_pid()
        if not bounds or pid is None:
            return False
        from ApplicationServices import AXUIElementPerformAction
        from AppKit import NSRunningApplication
        want = (bounds.get("X", 0), bounds.get("Y", 0),
                bounds.get("Width", 0), bounds.get("Height", 0))
        for win in (_ax_attr(AXUIElementCreateApplication(pid), "AXWindows") or []):
            pos = _ax_point(_ax_attr(win, "AXPosition"))
            size = _ax_size(_ax_attr(win, "AXSize"))
            if not pos or not size:
                continue
            if all(abs(a - b) <= 2 for a, b in zip(pos + size, want)):
                if AXUIElementPerformAction(win, "AXRaise") != 0:
                    return False
                running = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
                if running is None:
                    return False
                running.activateWithOptions_(1 << 1)   # NSApplicationActivateIgnoringOtherApps
                return True
        return False
    except Exception:
        return False


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
            f'<key>ProgramArguments</key><array><string>{exe}</string>'
            f'<string>{LOGIN_ARG}</string></array>'
            '<key>RunAtLoad</key><true/></dict></plist>\n'
        )
        with open(LAUNCH_AGENT, "w") as f:
            f.write(plist)
    else:
        try:
            os.remove(LAUNCH_AGENT)
        except OSError:
            pass


def _upgrade_login_item():
    """Agents written before v1.2.4 launch without LOGIN_ARG. Rewrite them once,
    so a login launch can be told apart from a launch by hand."""
    try:
        if login_item_enabled() and _app_executable():
            with open(LAUNCH_AGENT) as f:
                if LOGIN_ARG not in f.read():
                    set_login_item(True)
    except Exception:
        pass


def _launched_unattended(prefs):
    """True for a launch the user did not make by hand: launch-at-login, or the
    self-updater's relaunch (it stamps `relaunched_at` just before it relaunches)."""
    if LOGIN_ARG in sys.argv[1:]:
        return True
    return time.time() - prefs.get("relaunched_at", 0) < RELAUNCH_WINDOW


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


def _installed_app_path():
    """The running bundle when it lives anywhere under /Applications (a subfolder
    such as "/Applications/2. Browsers" too), else /Applications/TidyTab.app.

    🐛 to v1.2.3 this was always /Applications/TidyTab.app, so a copy kept in a
    subfolder updated into a SECOND copy and stayed old itself. Spotlight then
    opened the old copy, which never got the reopen that shows a hidden icon."""
    res = os.environ.get("RESOURCEPATH")
    if res:
        bundle = os.path.dirname(os.path.dirname(res))   # …/X.app/Contents/Resources → …/X.app
        if bundle.startswith("/Applications/") and bundle.endswith(".app"):
            return bundle
    return "/Applications/TidyTab.app"


APP_PATH_INSTALLED = _installed_app_path()
# Siblings of the installed copy: same volume, so the swap's renames stay atomic.
_SWAP_STAGING = os.path.join(os.path.dirname(APP_PATH_INSTALLED), ".TidyTab.new")
_SWAP_BACKUP = os.path.join(os.path.dirname(APP_PATH_INSTALLED), ".TidyTab.old")


def _swap_in_place(src):
    """Replace the installed app with the verified copy at `src`.

    🐛 v1.2.2 destroyed before it swapped:
        shutil.rmtree(installed, ignore_errors=True)
        os.rename(staging, installed)
    Anything that threw between those two lines — a rename across devices, a
    permissions error, the process dying — left the machine with *no* app, and
    the only handler posted a notification. So: copy first, move the live copy
    aside, swap, and only then delete the old one. Any failure puts the old copy
    back. A failed update must leave a working app, never an empty slot.
    """
    import shutil
    shutil.rmtree(_SWAP_STAGING, ignore_errors=True)
    shutil.rmtree(_SWAP_BACKUP, ignore_errors=True)
    shutil.copytree(src, _SWAP_STAGING, symlinks=True)

    moved_aside = False
    if os.path.exists(APP_PATH_INSTALLED):
        os.rename(APP_PATH_INSTALLED, _SWAP_BACKUP)   # same volume → atomic
        moved_aside = True
    try:
        os.rename(_SWAP_STAGING, APP_PATH_INSTALLED)
    except Exception:
        if moved_aside and not os.path.exists(APP_PATH_INSTALLED):
            os.rename(_SWAP_BACKUP, APP_PATH_INSTALLED)   # roll back
        raise
    shutil.rmtree(_SWAP_BACKUP, ignore_errors=True)
    shutil.rmtree(_SWAP_STAGING, ignore_errors=True)


def _restore_orphaned_app():
    """If a previous swap died mid-flight, the app is sitting in one of the
    scratch paths and /Applications is empty. Put it back at launch."""
    try:
        if os.path.exists(APP_PATH_INSTALLED):
            return
        for candidate in (_SWAP_BACKUP, _SWAP_STAGING):
            if os.path.isdir(candidate):
                os.rename(candidate, APP_PATH_INSTALLED)
                return
    except Exception:
        pass


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


def _find_tabs():
    """Return Safari tab records left→right as (element, center, width, pinned).

    Current Safari exposes AXSubrole=AXTabButton and an AXIdentifier containing
    isPinned=true/false. Width is retained for older Safari versions that don't
    expose the pinned state.
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

    items = []
    for el in radios:
        subrole = _ax_attr(el, "AXSubrole")
        if subrole and subrole != "AXTabButton":
            continue
        pos = _ax_point(_ax_attr(el, "AXPosition"))
        sz = _ax_size(_ax_attr(el, "AXSize"))
        if pos and sz and sz[0] > 0:
            identifier = _ax_attr(el, "AXIdentifier") or ""
            pinned = None
            match = re.search(r"(?:^|[?&])isPinned=(true|false)(?:&|$)", identifier)
            if match:
                pinned = match.group(1) == "true"
            items.append((el, (pos[0] + sz[0] / 2.0, pos[1] + sz[1] / 2.0),
                          sz[0], pinned))

    items.sort(key=lambda t: t[1][0])
    return items


def _split_tabs(items):
    """Return (pinned, unpinned) lists, preferring Safari's explicit AX state."""
    if any(item[3] is not None for item in items):
        pinned = [(el, center) for el, center, _width, state in items if state is True]
        unpinned = [(el, center) for el, center, _width, state in items if state is False]
        return pinned, unpinned

    # Compatibility fallback for older Safari: pinned tabs are the narrow prefix.
    pinned = []
    unpinned = []
    seen_unpinned = False
    for el, center, width, _state in items:
        if not seen_unpinned and width <= PINNED_MAX_WIDTH:
            pinned.append((el, center))
        else:
            seen_unpinned = True
            unpinned.append((el, center))
    return pinned, unpinned


def find_pinned_tabs():
    """Return [(ax_element, (cx, cy)), ...] for pinned tabs, left→right."""
    pinned, _unpinned = _split_tabs(_find_tabs())
    return pinned


def find_unpinned_tabs():
    """Return [(ax_element, (cx, cy)), ...] for unpinned tabs, left→right."""
    _pinned, unpinned = _split_tabs(_find_tabs())
    return unpinned


def find_pinned_tab_centers():
    return [c for _, c in find_pinned_tabs()]


def find_unpinned_tab_centers():
    return [c for _, c in find_unpinned_tabs()]


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


def press_tab_context_menu_item(title):
    """Press a named item in the currently open Safari tab context menu."""
    try:
        from ApplicationServices import AXUIElementPerformAction

        pid = _safari_pid()
        if not pid:
            return False
        app = AXUIElementCreateApplication(pid)
        window = _ax_attr(app, "AXMainWindow")
        if window is None:
            return False

        def find_menu_item(element, depth=0):
            if depth > 4:
                return None
            role = _ax_attr(element, "AXRole")
            children = _ax_attr(element, "AXChildren") or []
            if role == "AXMenu":
                menu_items = [
                    child for child in children
                    if _ax_attr(child, "AXRole") == "AXMenuItem"
                ]
                menu_titles = {_ax_attr(child, "AXTitle") for child in menu_items}
                # Distinguish the tab context menu from Safari's main Window menu.
                if {"Duplicate Tab", "Close Tab"} <= menu_titles:
                    for child in menu_items:
                        if _ax_attr(child, "AXTitle") == title:
                            return child
            for child in children:
                found = find_menu_item(child, depth + 1)
                if found is not None:
                    return found
            return None

        item = find_menu_item(window)
        return item is not None and AXUIElementPerformAction(item, "AXPress") == 0
    except Exception:
        return False


# ============================================================================ #
# Menu-bar app
# ============================================================================ #
def _res(name):
    base = os.environ.get("RESOURCEPATH", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def _with_shortcut(item, key, modifiers):
    """Give a rumps item a real key equivalent, so the menu draws its shortcut in
    the shortcut column. rumps' own `key=` always means ⌘ alone, so the modifier
    mask goes straight onto the NSMenuItem."""
    item._menuitem.setKeyEquivalent_(key)
    item._menuitem.setKeyEquivalentModifierMask_(modifiers)
    return item


def _build_alert(title, message, buttons):
    """An informational NSAlert built the way rumps.alert builds one (dark
    appearance in Dark Mode), with `buttons` added in order."""
    alert = NSAlert.alloc().init()
    alert.setMessageText_("" if title is None else str(title))
    alert.setInformativeText_(str(message))
    alert.setAlertStyle_(0)  # informational, same as rumps.alert
    if NSUserDefaults.standardUserDefaults().stringForKey_("AppleInterfaceStyle") == "Dark":
        alert.window().setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameVibrantDark"))
    for b in buttons:
        alert.addButtonWithTitle_(b)
    return alert


KEY_RETRY_INTERVAL = 0.15   # seconds between "is the alert still key?" checks
KEY_RETRY_FIRES = 8         # ~1.2 s: covers a late Safari activation after a Space switch


def _run_modal_key(alert):
    """alert.runModal(), but the alert is the key window. An activation that lands
    after ours (Safari finishing a Space switch) leaves the modal on screen but not
    key: the default button draws gray and Return goes to the other app. A timer
    in the modal run loop takes key back whenever it is lost in the first ~1.2 s."""
    app = NSApplication.sharedApplication()
    def make_key():
        try:
            app.activateIgnoringOtherApps_(True)
            alert.window().makeKeyAndOrderFront_(None)
        except Exception:
            pass
    try:
        app.activateIgnoringOtherApps_(True)
    except Exception:
        pass
    fires = [0]
    def check(timer):
        fires[0] += 1
        if not alert.window().isKeyWindow():
            make_key()
        if fires[0] >= KEY_RETRY_FIRES:
            timer.invalidate()
    timer = NSTimer.timerWithTimeInterval_repeats_block_(KEY_RETRY_INTERVAL, True, check)
    NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSModalPanelRunLoopMode)
    try:
        return alert.runModal()
    finally:
        timer.invalidate()


class NSApp(objc.Category(_RumpsDelegate)):
    """Opening TidyTab again while it runs (Finder, Spotlight, Launchpad) makes
    macOS send it a "reopen" event. With the menu-bar icon hidden that is the way
    back, the same as other menu-bar apps (Rectangle, Maccy). rumps' delegate has
    no handler for it, so this category adds one."""

    @objc.typedSelector(b"Z@:@Z")
    def applicationShouldHandleReopen_hasVisibleWindows_(self, _sender, _has_windows):
        app = getattr(rumps.App, "*app_instance", None)
        if app is not None:
            app._on_reopen()
        return True


class TidyTabApp(rumps.App):
    def __init__(self):
        super().__init__(APP_NAME, quit_button=None)

        self._mode = ("auto", [])
        self._operation = "unpin"
        self._stop_flag = threading.Event()
        self._worker = None
        self._space_monitor = None
        self._watchdog = None
        self._relaunching = False   # the self-updater's own `open` is not a reopen by the user

        # Idle menu-bar look (restored after a run): the white template pin.
        self._idle_icon = _res("menubar_white.png")
        self._idle_template = True

        self._login_item = rumps.MenuItem("Launch at login", callback=self._toggle_login)
        self._login_item.state = login_item_enabled()
        self._autoupdate_item = rumps.MenuItem("Auto-update", callback=self._toggle_autoupdate)
        self._autoupdate_item.state = load_prefs().get("auto_update", True)
        self._hide_icon_item = rumps.MenuItem(HIDE_ICON_TITLE, callback=self._hide_icon)
        self._stop_item = _with_shortcut(rumps.MenuItem(STOP_TITLE, callback=self._stop), ESC_KEY, 0)
        # The version sits in a tooltip: in the title it made this the widest row,
        # which pushed the shortcut column far right of every action.
        update_item = rumps.MenuItem("Check for Updates…", callback=self._check_updates)
        update_item._menuitem.setToolTip_(f"{APP_NAME} {VERSION}")

        # Three explicit actions (no hidden mode) — clear what each does. Every
        # action shows its shortcut in the menu's shortcut column.
        self.menu = [
            _with_shortcut(rumps.MenuItem(UNPIN_TITLE, callback=self._run_unpin), "u", CMD_OPT),
            _with_shortcut(rumps.MenuItem(CLOSE_TITLE, callback=self._run_close), "k", CMD_OPT),
            _with_shortcut(rumps.MenuItem(PIN_TITLE, callback=self._run_pin), "p", CMD_OPT),
            self._stop_item,
            None,
            self._login_item,
            self._autoupdate_item,
            self._hide_icon_item,
            None,
            update_item,
            # Always-present escape hatch. A self-update can fail for reasons the
            # app cannot fix from inside (truncated download, a dmg that won't
            # mount, a signature that doesn't match) — and until v1.2.1 it failed
            # on every single run. Without this the only signal was a notification
            # saying "skipped", with nowhere to go; RELEASES_PAGE was defined and
            # never used. A user should never have to know the repo URL to recover.
            # No "…": it just opens a web page. "…" marks the items that open a
            # dialog first (Hide menu-bar icon…, Check for Updates…).
            rumps.MenuItem("Download page", callback=self._open_releases_page),
            _with_shortcut(rumps.MenuItem(f"Quit {APP_NAME}", callback=self._quit), "q", 1 << 20),
        ]

        # "Grant Accessibility…" is setup-only: it appears just while the permission
        # is missing (see _sync_accessibility_item).
        self._sync_accessibility_item()
        self._grant_timer = rumps.Timer(self._sync_accessibility_item, 5)
        self._grant_timer.start()

        self._apply_idle()
        _upgrade_login_item()
        # before_start fires after rumps creates the status item and before the
        # event loop draws it, so a hidden icon never flashes on screen.
        rumps.events.before_start.register(self._apply_icon_visibility)

        self._launch_check = rumps.Timer(self._launch_accessibility_check, 1.0)
        self._launch_check.start()
        self._hotkey_monitor = None
        self._start_hotkey_monitor()
        _restore_orphaned_app()        # a swap that died mid-flight left the app aside
        self._auto_update_on_launch()  # self-update on launch if a newer release exists
        self._update_timer = rumps.Timer(lambda _t: self._auto_update_on_launch(), 14400)
        self._update_timer.start()     # …and re-check every ~4h for long-running sessions

    def _toggle_login(self, sender):
        sender.state = not sender.state
        set_login_item(bool(sender.state))

    def _toggle_autoupdate(self, sender):
        sender.state = not sender.state
        prefs = load_prefs(); prefs["auto_update"] = bool(sender.state); save_prefs(prefs)

    # ---- hide / show the menu-bar icon -------------------------------------- #
    def _set_icon_visible(self, visible):
        item = getattr(getattr(self, "_nsapp", None), "nsstatusitem", None)
        if item is not None:
            item.setVisible_(bool(visible))

    def _set_icon_hidden(self, hidden):
        prefs = load_prefs(); prefs["hide_icon"] = bool(hidden); save_prefs(prefs)
        self._set_icon_visible(not hidden)

    def _apply_icon_visibility(self):
        """Launch: a hidden icon stays hidden only for a login launch or an update
        relaunch. A launch by hand shows it again, so "open TidyTab" always brings
        the icon back, running or not."""
        prefs = load_prefs()
        hidden = bool(prefs.get("hide_icon")) and _launched_unattended(prefs)
        prefs.pop("relaunched_at", None)
        prefs["hide_icon"] = hidden
        save_prefs(prefs)
        self._set_icon_visible(not hidden)

    def _hide_icon(self, _sender):
        if self._alert(
            title="Hide the menu-bar icon?",
            message=(
                "TidyTab keeps running and ⌥⌘U / ⌥⌘K / ⌥⌘P still work. "
                "The icon shows during a run, so the stop hint stays visible.\n\n"
                "To show the icon again, open TidyTab again (Applications or Spotlight).\n\n"
                "To keep the icon hidden after a restart, turn on Launch at login."
            ),
            ok="Hide Icon", cancel="Cancel",
        ) == 1:
            self._set_icon_hidden(True)

    def _on_reopen(self):
        if not self._relaunching:
            self._set_icon_hidden(False)

    def _sync_accessibility_item(self, _timer=None):
        """Show "Grant Accessibility…" ONLY while the permission is missing.

        It's one-time setup, so it's noise in the menu once granted — but it has to
        come BACK on its own if macOS ever drops the grant (a self-update swaps the
        whole .app bundle, which can invalidate it), otherwise the app looks broken
        with no way to fix it from the menu.
        """
        try:
            needed = not accessibility_trusted()
            present = GRANT_TITLE in self.menu
            if needed and not present:
                self.menu.insert_after(
                    self._autoupdate_item.title,
                    rumps.MenuItem(GRANT_TITLE, callback=self._grant_accessibility),
                )
            elif present and not needed:
                del self.menu[GRANT_TITLE]
        except Exception:
            pass

    # ---- main-thread UI helpers -------------------------------------------- #
    def _alert(self, title=None, message="", ok=None, cancel=None):
        """Same contract as rumps.alert (1 = ok, 0 = cancel), but the dialog is
        always the key window, so `ok` draws blue and takes Return.

        TidyTab is LSUIElement (no Dock icon), so its windows do NOT come forward
        on their own, and Safari activating a beat late (a Space switch) took key
        away from the 1.2.5 dialog: gray button, Return went to Safari.
        _run_modal_key activates first and takes key back for the first ~1.2 s.
        Must be called on the MAIN thread.
        """
        buttons = [ok or "OK"]
        if cancel:
            buttons.append(cancel if isinstance(cancel, str) else "Cancel")
        alert = _build_alert(title, message, buttons)
        btns = alert.buttons()
        btns[0].setKeyEquivalent_("\r")
        if len(btns) > 1:
            btns[1].setKeyEquivalent_("\x1b")
        return 1 if _run_modal_key(alert) == NSAlertFirstButtonReturn else 0

    def _build_destructive_alert(self, title, message, action):
        """An NSAlert whose default button (Return) is Cancel. The action button
        has no key equivalent, so it needs a click."""
        alert = _build_alert(title, message, ["Cancel", action])
        cancel, act = alert.buttons()
        if act.respondsToSelector_(b"setHasDestructiveAction:"):
            act.setHasDestructiveAction_(True)
        cancel.setKeyEquivalent_("\r")  # AppKit gives a "Cancel" button Esc; Esc still cancels
        act.setKeyEquivalent_("")
        return alert

    def _confirm_destructive(self, title, message, action):
        """Confirm where Return and Esc both cancel; the action needs a click.
        Returns True only when the action button is clicked. Main thread only."""
        alert = self._build_destructive_alert(title, message, action)

        # With no button on Esc, NSAlert picks one on its own — in tests it
        # sometimes picked the action. Map Return, Enter and Esc to Cancel here.
        app = NSApplication.sharedApplication()
        def on_key(event):
            if event.keyCode() in (36, 76, 53) and app.modalWindow() == alert.window():
                app.stopModalWithCode_(NSAlertFirstButtonReturn)
                return None
            return event
        monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, on_key)
        try:
            return _run_modal_key(alert) == NSAlertSecondButtonReturn
        finally:
            NSEvent.removeMonitor_(monitor)

    def _notify(self, title, subtitle, message):
        """Post a notification from ANY thread — Cocoa UI must be touched on the
        main thread, and firing NSUserNotification off a worker thread is what
        made update banners appear late / stutter."""
        callAfter(rumps.notification, title, subtitle, message)

    def _open_releases_page(self, _sender=None):
        """Open the GitHub releases page. Safe from any thread — `open` hands off
        to LaunchServices rather than touching Cocoa UI here."""
        try:
            subprocess.Popen(["open", RELEASES_PAGE])
        except Exception:
            pass

    def _offer_download_page(self, reason):
        """MAIN thread only: say why the update didn't apply and offer the page.

        Only shown when the user asked for the update — at launch a modal would
        ambush them, so the auto path notifies and leaves the "Download page"
        menu item as the way through.
        """
        if self._alert(APP_NAME, f"{reason}\n\nOpen the download page to install it manually?",
                       ok="Open page", cancel="Later") == 1:
            self._open_releases_page()

    def _check_updates(self, _sender=None):
        """Manual (menu) check.

        The GitHub fetch runs on a BACKGROUND thread: doing it inline blocked the
        main run loop for up to 8s (DNS + TLS + request), which froze the menu bar
        and made the result dialog crawl in. The answer is marshalled back to the
        main thread to be shown."""
        def work():
            latest = latest_release_version()
            callAfter(self._show_update_result, latest)
        threading.Thread(target=work, daemon=True).start()

    def _show_update_result(self, latest):
        if latest is None:
            self._alert(APP_NAME, "Couldn't reach GitHub to check for updates. "
                                  "Check your connection and try again.")
        elif _ver_tuple(latest) > _ver_tuple(VERSION):
            if self._alert(APP_NAME, f"Update available: v{latest} (you have v{VERSION}).\nDownload and install now?",
                           ok="Update", cancel="Later") == 1:
                # manual=True: they asked, so a failure earns a dialog with a way
                # out rather than a notification that dead-ends.
                threading.Thread(target=self._do_self_update, args=(latest,),
                                 kwargs={"manual": True}, daemon=True).start()
        else:
            self._alert(APP_NAME, f"You're on the latest version (v{VERSION}).")

    def _auto_update_on_launch(self):
        if not load_prefs().get("auto_update", True):
            return

        def work():
            latest = latest_release_version()
            if not (latest and _ver_tuple(latest) > _ver_tuple(VERSION)):
                return
            if load_prefs().get("update_attempted") == latest:   # tried + still behind → don't loop
                self._notify(APP_NAME, f"Update v{latest} available",
                             "Auto-update didn't apply — use “Download page” in the menu to install it.")
                return
            self._do_self_update(latest)
        threading.Thread(target=work, daemon=True).start()

    def _do_self_update(self, latest, manual=False):
        """Download the latest notarized dmg, verify its signature, swap it into
        /Applications, and relaunch. Guarded against update loops + bad downloads.

        `manual` = the user picked "Update" in the dialog, so a failure gets a
        dialog offering the download page. The launch path leaves it False: a
        modal at startup is an ambush, so it only notifies.
        """
        try:
            import shutil
            self._notify(APP_NAME, f"Updating to v{latest}…",
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
            ok = False
            try:
                src = os.path.join(mnt, "TidyTab.app")
                # 🐛 v1.2.1: this was `codesign --verify --quiet`. codesign has no
                # --quiet flag, so it exited 2 (usage error) on EVERY run — `ok` was
                # always False, so no auto-update ever applied. Worse, the loop guard
                # above records `update_attempted` before the download, so the next
                # launch short-circuits to "use Check for Updates…" instead of
                # retrying. Anyone on auto-update has been silently stuck.
                #
                # The replacement also fixes what the original *would* have done if
                # it worked: a bare `--verify` only asks "is this signature intact",
                # which any signature satisfies — including one an attacker applied
                # to a swapped dmg. Pinning the requirement to Apple's anchor plus
                # our Developer ID team means only a build we signed can replace the
                # running app.
                ok = os.path.exists(src) and subprocess.run(
                    ["codesign", "--verify", "-R",
                     f"=anchor apple generic and certificate leaf[subject.OU] = {TEAM_ID}",
                     src],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
                if ok:
                    _swap_in_place(src)
            finally:
                # 🐛 v1.2.2: the detach lived *after* the swap inside the outer try,
                # so anything that threw mid-swap skipped it and left the dmg
                # mounted. A finally detaches on every path.
                subprocess.run(["hdiutil", "detach", mnt],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ok:
                # Keep a hidden icon hidden across the relaunch (_launched_unattended).
                self._relaunching = True
                prefs = load_prefs(); prefs["relaunched_at"] = time.time(); save_prefs(prefs)
                subprocess.Popen(["open", APP_PATH_INSTALLED])
                callAfter(rumps.quit_application)
            else:
                self._notify(APP_NAME, "Update skipped",
                             "The download failed verification — install it from the Download page.")
                if manual:
                    callAfter(self._offer_download_page,
                              f"The v{latest} download didn't pass signature verification, "
                              "so TidyTab left the installed copy alone.")
        except Exception as exc:
            self._notify(APP_NAME, "Update failed", str(exc))
            if manual:
                callAfter(self._offer_download_page,
                          f"The v{latest} update couldn't be applied: {exc}")

    def _start_hotkey_monitor(self):
        # Always-on global hotkeys: ⌘⌥U = Unpin, ⌘⌥K = Close, ⌘⌥P = Pin all.
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
                    elif ch == "p":
                        self._run_pin(None)
            except Exception:
                pass

        self._hotkey_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, handler
        )

    def _apply_idle(self):
        """Restore the idle look: the white template pin, no title, Stop gray."""
        self._stop_item.set_callback(None)
        self.template = self._idle_template
        self.icon = self._idle_icon
        self.title = ""
        # Force the live status item to image-only. rumps' fallbackOnName() can
        # leave the app name ("TidyTab") next to the pin when title+image are
        # briefly both empty; setting the image + clearing the title LAST
        # guarantees just the icon shows.
        try:
            img = self._icon_nsimage
            item = getattr(getattr(self, "_nsapp", None), "nsstatusitem", None)
            if img is not None and item is not None:
                img.setSize_(ICON_DIM)
                item.setImage_(img)
                item.setTitle_("")
        except Exception:
            pass

    # ---- launch / permission ---------------------------------------------- #
    def _launch_accessibility_check(self, timer):
        timer.stop()
        if not load_prefs().get("onboarded"):
            self._onboard()                 # first launch → friendly walkthrough
        elif not accessibility_trusted():
            self._notify(
                APP_NAME, "Accessibility permission needed",
                "Enable TidyTab in Privacy & Security → Accessibility so it can "
                "read and control Safari's tabs.",
            )

    def _onboard(self):
        prefs = load_prefs(); prefs["onboarded"] = True; save_prefs(prefs)
        resp = self._alert(
            title=f"Welcome to {APP_NAME} 📌",
            message=(
                "TidyTab manages all the tabs in your front Safari window in one sweep.\n\n"
                "• Choose “Unpin pinned tabs,” “Close pinned tabs,” or “Pin all tabs”\n"
                "• Or use ⌥⌘U (unpin) / ⌥⌘K (close) / ⌥⌘P (pin all)\n"
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

    def _run_pin(self, _sender):
        self._operation = "pin"
        self._start()

    def _start(self):
        if self._worker and self._worker.is_alive():
            self._notify(APP_NAME, "Already running",
                         "Press Space to stop the current run.")
            return
        if not accessibility_trusted():
            prompt_accessibility()
            open_accessibility_settings()
            self._alert(
                f"{APP_NAME} needs Accessibility",
                "Enable TidyTab under Privacy & Security → Accessibility, then try again.",
            )
            return

        if _safari_pid() is None:
            self._alert(APP_NAME,
                        "Safari isn't running. Open Safari with some pinned tabs, then try again.")
            return

        # Bring Safari to the front first so we never click into the wrong app, then
        # detect its pinned tabs. A Safari window on THIS desktop wins; only with none
        # here do we let macOS go to Safari's Space.
        if not raise_safari_window_here():
            activate_safari()
        time.sleep(0.7)

        # If Safari is on a DIFFERENT Space and macOS didn't switch to it, its window
        # isn't on-screen — refuse to click rather than click into whatever IS here.
        if not safari_window_on_screen():
            self._alert(
                f"{APP_NAME}: Safari is on another Space",
                "Safari's window is on a different desktop/Space and macOS didn't switch "
                "to it, so TidyTab won't click. Switch to the Safari window yourself (or "
                "turn on System Settings → Desktop & Dock → Mission Control → “When "
                "switching to an application, switch to a Space with open windows for the "
                "application”), then run TidyTab again. It acts on the FRONT Safari "
                "window's pinned tabs.",
            )
            return

        op_label = {"close": "Close", "pin": "Pin"}.get(self._operation, "Unpin")
        target_label = "unpinned" if self._operation == "pin" else "pinned"
        try:
            centers = (find_unpinned_tab_centers() if self._operation == "pin"
                       else find_pinned_tab_centers())
        except Exception:
            centers = []

        if not centers:
            self._alert(
                f"{APP_NAME}: no {target_label} tabs found",
                f"Couldn't find {target_label} tabs in the front Safari window. Make sure Safari "
                f"is open with {target_label} tabs (and TidyTab has Accessibility permission), "
                "then try again. (TidyTab won't click unless it has located the tabs.)",
            )
            return

        n = len(centers)
        tabs_word = "tab" if n == 1 else "tabs"
        title = f"{op_label} {n} {target_label} {tabs_word}?"
        message = (f"TidyTab will {op_label.lower()} {n} {target_label} {tabs_word} in "
                   "Safari.\n\nPress Space or Esc to stop mid-run.")
        if self._operation == "close":
            # ⌥⌘K is global: typed by accident, Return must not close every pinned tab.
            ok = self._confirm_destructive(title, message, op_label)
        else:
            ok = self._alert(title=title, message=message, ok=op_label, cancel="Cancel") == 1
        if not ok:
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
        self._set_icon_visible(True)    # a hidden icon shows for the run: the stop hint lives here
        self.title = "  Space/Esc to stop"
        self._stop_item.set_callback(self._stop)
        self._start_space_monitor()
        if self._watchdog is None:
            self._watchdog = rumps.Timer(self._check_done, 0.4)
            self._watchdog.start()

    def _end_running_ui(self):
        self._apply_idle()
        self._set_icon_visible(not load_prefs().get("hide_icon"))
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
        if operation == "pin":
            menu_deadline = time.monotonic() + 0.8
            while time.monotonic() < menu_deadline:
                if press_tab_context_menu_item("Pin Tab"):
                    time.sleep(0.15)
                    return
                time.sleep(0.05)

            # Fallback when Safari doesn't expose the open menu through AX: Home
            # normalizes selection regardless of which item appeared under the mouse.
            pyautogui.press("home")
            downs = 0
        else:
            # Preserve the proven v1.1.6 Unpin/Close keyboard behavior.
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
        target_label = "unpinned" if operation == "pin" else "pinned"
        try:
            # The confirm dialog stole focus — bring Safari back before clicking,
            # on the same desktop the confirm was on.
            if not raise_safari_window_here():
                activate_safari()
            time.sleep(0.5)
            done = 0
            expected = len(centers)
            row_y = None
            completed = False
            # Self-correcting: re-detect after every action, act on the rightmost
            # remaining target tab, stop when none are left, and bail if the count
            # isn't dropping — so a missed click can never become runaway clicking.
            while not self._stop_flag.is_set():
                current = (find_unpinned_tabs() if operation == "pin"
                           else find_pinned_tabs())
                if not current:
                    completed = True
                    break
                previous_count = len(current)
                previous_pinned_count = (
                    len(find_pinned_tabs()) if operation == "pin" else None
                )
                el, (x, cy) = current[-1]                   # rightmost remaining target tab
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

                # Pinning has a visible Safari animation and its AX state can lag
                # behind the menu action. Poll briefly rather than declaring a
                # successful action stuck on the very next read.
                action_succeeded = False
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline and not self._stop_flag.is_set():
                    if operation == "pin":
                        action_succeeded = len(find_pinned_tabs()) > previous_pinned_count
                    else:
                        action_succeeded = len(find_pinned_tabs()) < previous_count
                    if action_succeeded:
                        break
                    time.sleep(0.1)
                if self._stop_flag.is_set():
                    break
                if not action_succeeded:
                    self._notify(
                        APP_NAME, "Stopped",
                        f"A {target_label} tab didn't change — stopping to be safe.",
                    )
                    break
                # Safari creates a fresh ordinary tab when its last one is pinned.
                # "Pin all" means the tabs present at confirmation time, not that
                # browser-generated replacement, so stop after the original count.
                if operation == "pin" and done >= expected:
                    completed = True
                    break
            if completed and not self._stop_flag.is_set():
                verb = {"close": "Closed", "pin": "Pinned"}.get(operation, "Unpinned")
                self._notify(APP_NAME, "Done",
                             f"{verb} {done} tab{'' if done == 1 else 's'}.")
        except pyautogui.FailSafeException:
            self._notify(APP_NAME, "Stopped",
                         "Fail-safe triggered (mouse moved to a corner).")
        except Exception as exc:
            self._notify(APP_NAME, "Error", str(exc))


if __name__ == "__main__":
    TidyTabApp().run()
