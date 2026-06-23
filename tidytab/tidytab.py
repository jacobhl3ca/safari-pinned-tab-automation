#!/usr/bin/env python3
"""
TidyTab — a macOS menu-bar wrapper around the Safari pinned-tab automation.

This is a thin, no-terminal GUI front-end for the interactive CLI in
`safari_pinned_tab_automation.py`. It lives in the menu bar (rumps), lets you
pick the operation (unpin / close) and direction (right-to-left / left-to-right)
from menus, then runs the SAME pyautogui cycle as the source script in a
background thread so the menu bar stays responsive.

⚠️  This app SYNTHESIZES mouse clicks and keystrokes via pyautogui. macOS will
    silently drop those events unless the app is granted Accessibility
    permission (System Settings → Privacy & Security → Accessibility). See README.

How the cycle works (identical to the source script):
  1. left-click          (select the pinned tab under the mouse)
  2. right-click         (open the context menu)
  3. press Down          (1× to "Unpin", 3× to "Close" — to reach that menu item)
  4. press Enter         (activate the menu item)
  5. move / don't move   (right-to-left: shift the mouse 36px left each cycle
                          until it goes off the left edge; left-to-right: stay
                          put, because the tabs shift left on their own)
"""

import time
import threading

import rumps
import pyautogui

# --- App identity --------------------------------------------------------------
# Rename the app by changing this ONE constant (also used by setup.py).
APP_NAME = "TidyTab"

# --- pyautogui safety settings (mirrors the source script) ---------------------
pyautogui.FAILSAFE = True   # slam the mouse into a screen corner to abort instantly
pyautogui.PAUSE = 0.1       # small pause after each pyautogui call (faster than default)

# Distance (px) between adjacent Safari pinned tabs — used to step left.
TAB_DISTANCE = 36

# How long we wait after "Run" is clicked so the user can position the mouse.
COUNTDOWN_SECONDS = 3


class TidyTabApp(rumps.App):
    """Menu-bar app that wraps the pinned-tab automation loop."""

    def __init__(self):
        # title="📌" gives a pin glyph in the menu bar; no icon file needed.
        super().__init__(APP_NAME, title="📌", quit_button=None)

        # --- runtime state ---
        self._operation = "unpin"          # "unpin" | "close"
        self._direction = "right_to_left"  # "right_to_left" | "left_to_right"
        self._stop_flag = threading.Event()  # set() → break the loop
        self._worker = None                # the background automation thread

        # --- Operation submenu (mutually exclusive, default Unpin) ---
        self._op_unpin = rumps.MenuItem("Unpin tabs", callback=self._set_unpin)
        self._op_close = rumps.MenuItem("Close tabs", callback=self._set_close)
        self._op_unpin.state = True  # default
        operation_menu = rumps.MenuItem("Operation")
        operation_menu.add(self._op_unpin)
        operation_menu.add(self._op_close)

        # --- Direction submenu (mutually exclusive, default Right→Left) ---
        self._dir_rtl = rumps.MenuItem(
            "Right → Left (recommended)", callback=self._set_rtl
        )
        self._dir_ltr = rumps.MenuItem("Left → Right", callback=self._set_ltr)
        self._dir_rtl.state = True  # default
        direction_menu = rumps.MenuItem("Direction")
        direction_menu.add(self._dir_rtl)
        direction_menu.add(self._dir_ltr)

        # --- Build the menu ---
        self.menu = [
            operation_menu,
            direction_menu,
            None,  # separator
            rumps.MenuItem(
                f"Run ({COUNTDOWN_SECONDS}s countdown)", callback=self._run
            ),
            rumps.MenuItem("Stop", callback=self._stop),
            None,  # separator
            rumps.MenuItem("Quit", callback=self._quit),
        ]

    # ------------------------------------------------------------------ #
    # Menu callbacks: Operation
    # ------------------------------------------------------------------ #
    def _set_unpin(self, _sender):
        self._operation = "unpin"
        self._op_unpin.state = True
        self._op_close.state = False

    def _set_close(self, _sender):
        self._operation = "close"
        self._op_unpin.state = False
        self._op_close.state = True

    # ------------------------------------------------------------------ #
    # Menu callbacks: Direction
    # ------------------------------------------------------------------ #
    def _set_rtl(self, _sender):
        self._direction = "right_to_left"
        self._dir_rtl.state = True
        self._dir_ltr.state = False

    def _set_ltr(self, _sender):
        self._direction = "left_to_right"
        self._dir_rtl.state = False
        self._dir_ltr.state = True

    # ------------------------------------------------------------------ #
    # Menu callbacks: Run / Stop / Quit
    # ------------------------------------------------------------------ #
    def _run(self, _sender):
        # Don't start a second loop on top of a running one.
        if self._worker and self._worker.is_alive():
            rumps.notification(
                APP_NAME,
                "Already running",
                "An automation loop is already in progress. Use Stop first.",
            )
            return

        # Tell the user to get the mouse into position; we give them a countdown.
        op_label = "Closing" if self._operation == "close" else "Unpinning"
        rumps.notification(
            APP_NAME,
            f"{op_label} pinned tabs in {COUNTDOWN_SECONDS}s",
            "Move your mouse over the RIGHTMOST pinned Safari tab now. "
            "Slam the mouse into a screen corner to abort.",
        )

        # Clear any prior stop request and launch the worker thread.
        self._stop_flag.clear()
        self._worker = threading.Thread(target=self._automation_loop, daemon=True)
        self._worker.start()

    def _stop(self, _sender):
        # Signal the loop to break at the top of its next cycle.
        self._stop_flag.set()

    def _quit(self, _sender):
        # Make sure a running loop is asked to stop before we tear down.
        self._stop_flag.set()
        rumps.quit_application()

    # ------------------------------------------------------------------ #
    # The automation loop — same pyautogui cycle as the source script.
    # Runs on a background thread; everything here must NOT touch UI except
    # via rumps.notification (which is thread-safe for our purposes).
    # ------------------------------------------------------------------ #
    def _automation_loop(self):
        # Snapshot the chosen options so menu changes mid-run don't affect us.
        operation_type = self._operation
        direction_type = self._direction

        # Give the user COUNTDOWN_SECONDS to move the mouse into position.
        time.sleep(COUNTDOWN_SECONDS)

        cycle = 0
        try:
            while True:
                # Stop requested from the menu?
                if self._stop_flag.is_set():
                    break

                cycle += 1

                # Current mouse position (the tab we're about to act on).
                current_x, current_y = pyautogui.position()

                # 1. Left click — select the pinned tab under the mouse.
                pyautogui.click()
                time.sleep(0.1)

                # 2. Right click — open the context menu.
                pyautogui.click(button="right")
                time.sleep(0.1)

                # 3. Press Down arrow(s) — different count for close vs unpin.
                if operation_type == "close":
                    # 3× Down to reach "Close Tab" in the context menu.
                    pyautogui.press("down")
                    time.sleep(0.05)
                    pyautogui.press("down")
                    time.sleep(0.05)
                    pyautogui.press("down")
                else:  # unpin
                    # 1× Down to reach "Unpin Tab".
                    pyautogui.press("down")
                time.sleep(0.1)

                # 4. Press Enter — activate the highlighted menu item.
                pyautogui.press("enter")
                time.sleep(0.1)

                # 5. Move (or not) based on direction.
                if direction_type == "left_to_right":
                    # Don't move — the action shifts remaining tabs left for us.
                    pass
                else:
                    # Right-to-left: step the mouse 36px left each cycle.
                    new_x = current_x - TAB_DISTANCE
                    if new_x < 0:
                        # Off the left edge → we're done.
                        rumps.notification(
                            APP_NAME,
                            "Done",
                            f"Reached the leftmost edge after {cycle} cycle(s).",
                        )
                        break
                    pyautogui.moveTo(new_x, current_y, duration=0.1)
                time.sleep(0.2)

        except pyautogui.FailSafeException:
            # Mouse hit a screen corner — clean, intentional abort.
            rumps.notification(
                APP_NAME,
                "Stopped",
                "Fail-safe triggered (mouse moved to a screen corner).",
            )
        except Exception as exc:  # noqa: BLE001 — surface ANY error as a notice.
            # Never crash the menu bar; show the error as a notification instead.
            rumps.notification(APP_NAME, "Error", str(exc))


if __name__ == "__main__":
    TidyTabApp().run()
