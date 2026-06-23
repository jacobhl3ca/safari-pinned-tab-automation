# TidyTab 📌

A tiny **macOS menu-bar app** that tidies up your **Safari pinned tabs** —
unpinning or closing a whole row of them with one click, no terminal needed.

It's a GUI wrapper around an interactive CLI automation script. Under the hood it
synthesizes the exact same mouse-click / keystroke cycle as the original script
(`safari_pinned_tab_automation.py`), but driven from a menu-bar dropdown instead
of question-and-answer prompts.

> The app name is set by a single constant — `APP_NAME = "TidyTab"` at the top of
> `tidytab.py` (and mirrored in `setup.py`). Change it there to rename everything.

---

## What it does

Pick your options from the menu bar, then hit **Run**:

- **Operation** — *Unpin tabs* (default) or *Close tabs*
- **Direction** — *Right → Left (recommended)* (default) or *Left → Right*
- **Run (3s countdown)** — shows a notification telling you to put the mouse over
  the **rightmost** pinned Safari tab, waits 3 seconds, then runs the loop.
- **Stop** — breaks the loop at the next cycle.
- **Quit**

Each cycle does: left-click the tab → right-click for the context menu →
press **Down** (1× to reach *Unpin*, 3× to reach *Close*) → press **Enter**.
For *Right → Left* it then steps the mouse 36px left each cycle until it runs off
the left edge and stops. For *Left → Right* it stays put, because removing a tab
shifts the rest left automatically.

**Fail-safe:** slam the mouse into any screen corner to abort instantly
(pyautogui's `FAILSAFE`). Errors surface as a notification — the menu bar never
crashes.

---

## Set up a virtual environment & install deps

```bash
cd /Users/jacob/CascadeProjects/tabtidy
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

A 📌 appears in the menu bar. Use the menus, then **Run**.

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
  while it runs; keep a screen corner handy as the abort fail-safe.
- The 36px tab spacing assumes default Safari pinned-tab sizing; if your tabs
  look denser/wider, adjust `TAB_DISTANCE` in `tidytab.py`.
- `py2app` may need to be `pip install`-ed into your venv before building.
