# TidyTab 📌

A tiny **macOS menu-bar app** that tidies up your **Safari pinned tabs** —
unpinning or closing a whole row of them with one click, no terminal needed.

This is the source. To just **use** TidyTab, grab the signed build from the
[latest release](https://github.com/jacobhl3ca/safari-pinned-tab-automation/releases/latest/download/TidyTab.dmg)
or `brew install --cask jacobhl3ca/tap/tidytab` — see the [repo README](../README.md).

> The app name is set by a single constant — `APP_NAME = "TidyTab"` at the top of
> `tidytab.py` (and mirrored in `setup.py`). Change it there to rename everything.

---

## What it does

Two commands, straight from the menu bar or a global hotkey — no options to set
first:

- **Unpin pinned tabs (⌘⌥U)** — tabs stay open, just unpinned
- **Close pinned tabs (⌘⌥K)** — closes them outright
- **Stop (Space / Esc)** — breaks the loop at the next cycle
- Plus **Launch at login**, **Auto-update on launch**, and **Check for Updates…**
- **Grant Accessibility…** appears in the menu *only while the permission is
  missing*, and disappears once it's granted

Either command locates the pinned tabs in the **front Safari window** through the
**Accessibility API** (`find_pinned_tab_centers()`) — no fixed pixel spacing, no
parking the mouse anywhere — reports how many it found, and waits for your OK.
Then per tab: left-click → right-click for the context menu → press **Down**
(1× to reach *Unpin*, 3× to reach *Close*) → press **Enter**.

It bails out rather than clicking blind if Safari isn't running, if it can't find
any pinned tabs, or if Safari's window is on another Space.

**Stopping:** <kbd>Space</kbd> or <kbd>Esc</kbd> mid-run, or slam the mouse into
any screen corner (pyautogui's `FAILSAFE`). Errors surface as a notification — the
menu bar never crashes.

---

## Set up a virtual environment & install deps

```bash
cd tidytab            # this folder
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

A 📌 appears in the menu bar. Pick **Unpin** or **Close** from it (the ⌘⌥U / ⌘⌥K
hotkeys work too).

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

TidyTab works by **synthesizing mouse clicks and keystrokes**. macOS **silently
drops** synthesized input from apps that haven't been granted **Accessibility**
permission — the app will appear to "do nothing" if you skip this step.

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
  and nothing happens (see above).
- This automation **drives the real mouse and keyboard**. Don't touch the machine
  while it runs; <kbd>Space</kbd>, <kbd>Esc</kbd>, or a screen corner aborts it.
- It acts on the **front Safari window only**, and won't run if that window is on
  another Space (macOS won't always switch, and clicking into whatever *is* on
  screen would be worse than doing nothing).
- `py2app` may need to be `pip install`-ed into your venv before building.
- Don't `pip install pillow` into the build venv — py2app then bundles PIL and
  liblzma, the bundle jumps ~40 → 58 MB, and inside-out signing fails on
  `liblzma.5.dylib`. See [DISTRIBUTION.md](DISTRIBUTION.md).
