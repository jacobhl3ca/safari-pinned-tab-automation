# TidyTab 📌

A tiny **macOS menu-bar app** that tidies up your **Safari pinned tabs** —
unpinning or closing a whole row of them with one click, no terminal needed.

It auto-locates pinned tabs via the macOS Accessibility API, confirms the count,
then acts on each one. No need to position the mouse or pick a direction.

> The app name is set by a single constant — `APP_NAME = "TidyTab"` at the top of
> `tidytab.py` (and mirrored in `setup.py`). Change it there to rename everything.

---

## What it does

Click the 📌 pin in your menu bar and choose an action:

- **Unpin pinned tabs  (⌘⌥U)** — removes the pin from every pinned tab
- **Close pinned tabs  (⌘⌥K)** — closes every pinned tab entirely
- **Stop** — aborts a run in progress (or press **Space** / **Esc**)

TidyTab shows a confirmation ("Unpin 5 pinned tabs?") before acting. On OK it
auto-detects the rightmost pinned tab, acts on it, re-detects, and repeats until
none remain. It stops automatically if a tab doesn't change (safety guard) or if
you abort.

**Fail-safe:** press **Space** or **Esc**, or slam the mouse into any screen
corner, to abort instantly.

---

## Set up a virtual environment & install deps

```bash
cd /path/to/safari-pinned-tab-automation/tidytab
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

(`requirements.txt` pulls in `rumps`, `pyautogui`, `pyobjc`, and `py2app`.)

---

## Run in development

```bash
source .venv/bin/activate
python tidytab.py
```

A 📌 appears in the menu bar. Choose **Unpin pinned tabs** or **Close pinned tabs**.

> In dev mode, the **terminal/Python** running the script is what needs
> Accessibility permission (see below). When you build the `.app`, the app
> bundle itself needs it.

---

## Build the double-clickable app

```bash
source .venv/bin/activate
python setup.py py2app
```

This produces **`dist/TidyTab.app`** — a real, double-clickable, menu-bar-only
app (no Dock icon, thanks to `LSUIElement: True` in the plist). Drag it to
`/Applications` if you like.

> If `py2app` isn't found, install it first: `pip install py2app`
> (it's already in `requirements.txt`).

---

## ⚠️ One-time macOS permission (required — read this!)

TidyTab works by **reading the Accessibility API** to find tabs and
**synthesizing mouse clicks and keystrokes** to act on them. macOS **silently
drops** synthesized input and blocks API reads from apps that haven't been
granted **Accessibility** permission.

1. Open **System Settings → Privacy & Security → Accessibility**.
2. Click **+** and add the app that's doing the synthesizing:
   - **Built `.app`:** add `TidyTab.app` (from `dist/` or `/Applications`).
   - **Dev mode (`python tidytab.py`):** add your **terminal app** (Terminal,
     iTerm, etc.) — that's the process actually sending the events.
3. Toggle it **ON**. If it was already listed, remove and re-add it after a
   rebuild so macOS re-grants the updated binary.

You may also see an **Automation** prompt the first time it controls Safari
(the `NSAppleEventsUsageDescription` note explains why) — allow it.

---

## Caveats

- **Accessibility permission is mandatory** — without it, clicks/keys are dropped
  and tab detection fails (see above).
- This automation **drives the real mouse and keyboard**. Don't touch the machine
  while it runs; keep a screen corner or the Space key handy as abort fail-safes.
- `py2app` may need to be `pip install`-ed into your venv before building.
