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

# The menu-bar pin bundled into Resources. One image only — it's a *template*
# image, so macOS tints it to match a light or dark menu bar automatically.
DATA_FILES = ["menubar_white.png"]

OPTIONS = {
    # argv_emulation interferes with input synthesis / event handling; leave off.
    "argv_emulation": False,
    # App (Dock/Finder) icon.
    "iconfile": "TidyTab.icns",
    "plist": {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": "com.jacob.tidytab",
        "CFBundleVersion": "1.2.0",
        "CFBundleShortVersionString": "1.2.0",
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
    # ⚠️ Do NOT add "PyObjCTools" here — it's a NAMESPACE package and py2app's
    # modulegraph dies on it ("No module named 'PyObjCTools'"), same failure as
    # `rubicon` below. tidytab.py's `PyObjCTools.AppHelper` import is collected
    # anyway because rumps itself imports AppHelper.
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
