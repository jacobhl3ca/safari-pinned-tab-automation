# TidyTab 📌

A tiny **macOS menu-bar app** that bulk **pins**, **unpins**, or **closes** your
Safari tabs — one command instead of right-clicking through them one at a time.

**[tidytab.jacobhl.com](https://tidytab.jacobhl.com)** · Developer ID-signed and
Apple-notarized · free.

> This repo started life as the interactive CLI script that solved the problem
> (`safari_pinned_tab_automation.py`, still here — see [Origins](#-origins)).
> TidyTab is that idea as a real, shippable app, and is what you probably want.

---

## 📦 Install

**Download the app** — [TidyTab.dmg](https://github.com/jacobhl3ca/safari-pinned-tab-automation/releases/latest/download/TidyTab.dmg)
(always the latest release), open it, drag TidyTab to Applications.

**Or with Homebrew:**

```bash
brew install --cask jacobhl3ca/tap/tidytab
```

Both paths install the same signed, notarized build, so macOS opens it without a
Gatekeeper detour. The app checks GitHub for newer releases and can update itself.

### One-time permission

TidyTab needs **Accessibility** permission — that's how it reads Safari's tab
positions and drives the clicks. It prompts on first run; if you'd rather do it up
front: **System Settings → Privacy & Security → Accessibility → enable TidyTab**.
Without it macOS silently drops everything the app does and it will look broken.

---

## 🚀 Using it

Three commands, from the menu-bar dropdown or from anywhere via a global hotkey:

| Command | Hotkey | What it does |
| --- | --- | --- |
| **Unpin pinned tabs** | <kbd>⌘⌥U</kbd> | Tabs stay open, just no longer pinned |
| **Close pinned tabs** | <kbd>⌘⌥K</kbd> | Closes them outright |
| **Pin all tabs** | <kbd>⌘⌥P</kbd> | Pins every unpinned tab in the front window |

It finds the relevant tabs in the **front Safari window** by itself, tells you how
many it found, and waits for you to confirm before it touches anything. Mid-run,
<kbd>Space</kbd>, <kbd>Esc</kbd>, or slamming the mouse into a screen corner stops
it immediately.

Also in the menu: **Stop** (with its <kbd>Space</kbd> / <kbd>Esc</kbd> shortcut shown),
**Launch at login**, **Auto-update on launch**, and **Check for Updates…**. A
**Grant Accessibility…** item appears only while that permission is missing.

### How it works

Safari has no batch-unpin, and unpinning shifts the remaining tabs left — so blind
"click, unpin, move right 36px" automation lands on the wrong tab. TidyTab locates
each pinned tab through the macOS **Accessibility API** instead of assuming a fixed
spacing, then for each one: left-click → right-click for the context menu → select
the exact *Pin Tab*, *Unpin Tab*, or *Close Tab* item through Accessibility.

It refuses to click at all if it can't find the tabs, if Safari isn't running, or if
Safari's window is on another Space — so it can never fire clicks into the wrong app.

---

## 🛠 Build from source

The app lives in [`tidytab/`](tidytab/) — a [rumps](https://github.com/jaredks/rumps)
menu-bar app packaged with py2app. See **[tidytab/README.md](tidytab/README.md)** to
run it in dev mode and build the `.app`, and **[tidytab/DISTRIBUTION.md](tidytab/DISTRIBUTION.md)**
for the codesign + notarization recipe.

Requirements: macOS, Python 3.9+, Safari.

---

## 🕰 Origins

[`safari_pinned_tab_automation.py`](safari_pinned_tab_automation.py) is the original
interactive terminal script — the same trick, run from a prompt, with a
`tab_distance` you tune by hand. It's kept here because it's where this came from and
it still works if you want the dependency-free version:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python safari_pinned_tab_automation.py
```

It asks for operation (unpin/close) and direction (left-to-right lets the tabs shift
under a parked cursor; right-to-left steps the mouse), then you park the pointer on
the first pinned tab and press ENTER. <kbd>Ctrl-C</kbd> or a screen corner stops it.
The app supersedes it — notably by detecting the tabs rather than assuming 36px
spacing.

---

## 📄 License

MIT — use and modify freely.
