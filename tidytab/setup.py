"""
py2app build script for TidyTab.

Build the double-clickable app with:

    python setup.py py2app

The resulting app is written to ./dist/TidyTab.app

NOTE: TidyTab synthesizes mouse clicks / keystrokes via pyautogui. macOS will
SILENTLY DROP those events unless the built .app is granted Accessibility
permission (System Settings → Privacy & Security → Accessibility). See README.md.
"""

from setuptools import setup

# Keep the app name in lock-step with tidytab.py's APP_NAME constant.
APP_NAME = "TidyTab"

# The script that becomes the app's entry point.
APP = ["tidytab.py"]

# Menu-bar pin silhouettes bundled into Resources (white + color options;
# the default look is the classic 📌 emoji set as the app title, no image).
DATA_FILES = [
    "menubar_white.png", "menubar_red.png", "menubar_orange.png",
    "menubar_yellow.png", "menubar_green.png", "menubar_blue.png",
    "menubar_purple.png", "menubar_pink.png",
]

OPTIONS = {
    # argv_emulation interferes with input synthesis / event handling; leave off.
    "argv_emulation": False,
    # App (Dock/Finder) icon.
    "iconfile": "TidyTab.icns",
    "plist": {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": "com.jacob.tidytab",
        "CFBundleVersion": "1.1.2",
        "CFBundleShortVersionString": "1.1.2",
        # Menu-bar-only app: no Dock icon, no app-switcher entry.
        "LSUIElement": True,
        # macOS shows this string when prompting for Automation / Apple Events.
        # pyautogui ultimately drives Safari, so an Automation usage note helps.
        "NSAppleEventsUsageDescription": (
            "TidyTab controls Safari pinned tabs by synthesizing mouse clicks "
            "and keystrokes. It also requires Accessibility permission "
            "(System Settings → Privacy & Security → Accessibility)."
        ),
    },
    # rumps + pyautogui + the Accessibility framework py2app must bundle.
    "packages": ["rumps", "pyautogui", "ApplicationServices"],
    # Exclude pyautogui's optional MouseInfo GUI helper (TidyTab never calls it).
    # MouseInfo pulls in `rubicon-objc`, which is a NAMESPACE package — py2app's
    # legacy `imp.find_module`-based bootstrap collector can't resolve it and dies
    # with "ImportError: No module named 'rubicon'". Excluding it sidesteps that
    # and drops dead weight from the bundle. tkinter is likewise unused.
    "excludes": ["mouseinfo", "rubicon", "tkinter"],
}

setup(
    name=APP_NAME,
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
