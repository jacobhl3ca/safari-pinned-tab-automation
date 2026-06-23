# Distributing TidyTab

How to make TidyTab downloadable by other people — and an honest assessment of
whether it could ever ship on the Mac App Store. (Short answer: it can't; the
viable path is a **notarized `.dmg` on GitHub Releases**.)

---

## a. Mac App Store viability — **NO. Not possible.**

TidyTab **cannot be distributed through the Mac App Store**, and this is a hard
architectural blocker, not a paperwork problem.

**Why:** every app on the Mac App Store is required to run inside the **App
Sandbox**. TidyTab's entire reason for existing is to do two things the sandbox
categorically forbids:

1. **Synthesize system-wide input** — it posts global mouse-click and keystroke
   events (`CGEvent`, via `pyautogui`) that land in *another* app (Safari). The
   sandbox does not allow an app to post global input events to the system /
   other processes.
2. **Drive / control another application** — it reaches outside its own process
   to manipulate Safari's UI. Sandboxed apps may not control other apps this way
   (it depends on the macOS **Accessibility API**, `AXIsProcessTrusted`, which is
   incompatible with the sandbox — accessibility-style automation tools are
   explicitly rejected from the App Store).

Even setting the sandbox aside, the App Review Guidelines reject apps whose core
function is to automate/simulate user input or control other apps in this manner.
There is no entitlement that re-enables this for a sandboxed Store app. So:
**App Store is off the table.** Stop here — don't spend time on a Store build.

> The same constraint is why other input-automation / window-manager tools
> (e.g. older versions of BetterTouchTool, Keyboard Maestro, many "auto-clicker"
> utilities) are sold as direct downloads, **not** on the Mac App Store.

---

## b. The viable path — direct download of a **notarized** app

Jacob already has a **paid Apple Developer account** (he ships HideScore,
`id6766885311`, and Tonight NYC, `id6763027650`), so he has a **Team ID** and can
create a **Developer ID Application** signing certificate. That's everything
needed to ship a notarized, double-clickable app outside the Store.

The flow is: **build → codesign (Developer ID + hardened runtime) → notarize →
staple → package as .dmg**.

### 0. One-time: create the Developer ID Application certificate

If he hasn't already got one on this Mac:

- Xcode → **Settings → Accounts → [team] → Manage Certificates → +** →
  **Developer ID Application**, **or**
- developer.apple.com → Certificates → **+** → *Developer ID Application* →
  follow the CSR steps, then download + double-click to install into the login
  keychain.

Find the exact identity string (you'll paste it into `codesign`):

```bash
security find-identity -v -p codesigning
# Look for a line like:
#   "Developer ID Application: Jacob <Lastname> (TEAMID1234)"
```

### 1. Build the `.app` (py2app)

```bash
cd /Users/jacob/CascadeProjects/tabtidy
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt          # rumps, pyautogui, pyobjc, py2app
rm -rf build dist                        # clean any prior build
python setup.py py2app
# → produces dist/TidyTab.app  (~39 MB)
```

> Build note (already fixed in `setup.py`): py2app chokes on the `rubicon`
> **namespace** package that `pyautogui`→`MouseInfo` drags in
> (`ImportError: No module named 'rubicon'`). `setup.py` now lists
> `excludes: ["mouseinfo", "rubicon", "tkinter"]` — TidyTab never calls MouseInfo,
> so dropping it is safe and trims the bundle. If you ever hit a runtime
> `ModuleNotFoundError` for something TidyTab *does* use, add that module to
> `packages`/`includes` in `setup.py` and rebuild.

### 2. Codesign with Developer ID + hardened runtime

Notarization **requires** the hardened runtime (`--options runtime`). Sign the
whole bundle, frameworks and all (`--deep`):

```bash
codesign --deep --force --options runtime \
  --sign "Developer ID Application: <Your Name> (<TEAMID>)" \
  dist/TidyTab.app

# Verify it took:
codesign --verify --deep --strict --verbose=2 dist/TidyTab.app
spctl --assess --type execute --verbose dist/TidyTab.app   # may say "rejected" until notarized+stapled — that's expected here
```

> py2app bundles its own Python; `--deep` signs the embedded
> `python` binary and the `Frameworks/` dylibs too. If a nested binary fails to
> sign, sign the offending file directly first, then re-run the `--deep` sign on
> the bundle. (A `--timestamp` is included automatically when signing for
> Developer ID with a network connection — keep the Mac online.)

### 3. Notarize with `notarytool`, then staple

Store credentials once (uses an **app-specific password** from
appleid.apple.com, *not* your normal Apple ID password):

```bash
xcrun notarytool store-credentials "tabtidy-notary" \
  --apple-id "jacobhl3ca@gmail.com" \
  --team-id "<TEAMID>" \
  --password "<app-specific-password>"
```

Zip the **app** (preserve the bundle with `--keepParent`), submit, wait, staple:

```bash
ditto -c -k --keepParent dist/TidyTab.app TidyTab.zip

xcrun notarytool submit TidyTab.zip \
  --keychain-profile "tabtidy-notary" \
  --wait
# → wait for "status: Accepted". If "Invalid", run:
#   xcrun notarytool log <submission-id> --keychain-profile "tabtidy-notary"
# the JSON log names exactly which binary/entitlement failed.

# Staple the ticket INTO the .app so it validates offline:
xcrun stapler staple dist/TidyTab.app
xcrun stapler validate dist/TidyTab.app
```

> You notarize the **app** (the zip is just a transport). After it's Accepted,
> staple the original `dist/TidyTab.app`, *then* package that stapled app into the
> `.dmg` in the next step. (You can alternatively notarize the finished `.dmg`
> and staple that — either works; stapling the app is simplest here.)

### 4. Package as a `.dmg` for a clean download

Simple, no extra tooling:

```bash
hdiutil create -volname "TidyTab" \
  -srcfolder dist/TidyTab.app \
  -ov -format UDZO \
  TidyTab.dmg
```

Nicer (drag-to-Applications layout, custom background) with
[`create-dmg`](https://github.com/create-dmg/create-dmg):

```bash
brew install create-dmg
create-dmg \
  --volname "TidyTab" \
  --app-drop-link 450 120 \
  --icon "TidyTab.app" 150 120 \
  --window-size 600 320 \
  TidyTab.dmg dist/
```

Ship `TidyTab.dmg`.

### ⚠️ Runtime caveat — the user MUST grant Accessibility (notarization does NOT fix this)

Notarizing only clears **Gatekeeper** (so the app opens without a scary warning).
It does **nothing** for the permission TidyTab actually needs to function.
Because TidyTab synthesizes mouse/keyboard input, macOS will **silently drop
every event** unless the user adds TidyTab under:

**System Settings → Privacy & Security → Accessibility** → **+** → add
`TidyTab.app` → toggle it **ON**.

If they skip this, TidyTab "does nothing" — no error, no crash, just no effect.
A first-run note (and the README) must spell this out. (A first launch may also
show an **Automation** prompt for controlling Safari — the
`NSAppleEventsUsageDescription` in `setup.py` supplies the explanation text;
allow it.)

---

## c. Where to host the download

| Option | Effort | Pros | Cons |
|---|---|---|---|
| **GitHub Releases** ⭐ | Low | Free, versioned, public, direct `.dmg` link, no infra | Users must know the repo / find the release page |
| **Button on jacobhl.com** ⭐ | Low | Discoverable next to his other projects, one obvious "Download" CTA | Just a link — still points at the GitHub asset |
| **Homebrew cask** | Medium-High | Most "installable" (`brew install --cask tabtidy`), auto-updates | Needs a tap repo or a homebrew-cask PR; cask must reference a stable hosted `.dmg` + SHA; ongoing maintenance |

**Recommendation (pragmatic first step):** **GitHub Releases + a download button
on jacobhl.com.**

1. Push the source to the public repo **`jacobhl3ca/safari-pinned-tab-automation`**
   (the project's canonical name), create a tagged **Release** (e.g. `v1.0.0`),
   and **attach `TidyTab.dmg`** as a release asset. The asset gets a stable URL:
   `https://github.com/jacobhl3ca/safari-pinned-tab-automation/releases/latest/download/TidyTab.dmg`
2. On **jacobhl.com** add a small **"Download TidyTab"** button/tile (the site
   uses the bedimcode `.work__img` tiles; repo
   `jacobhl3ca/jacobhl3ca.github.io`, **`master`** branch) pointing at that
   `.../releases/latest/download/TidyTab.dmg` URL so it always serves the newest
   release.

Add a **Homebrew cask later** only if there's real demand — it's the most
polished install but the highest upkeep, and it still just wraps the same hosted
`.dmg`.

---

## d. Fallback — distributing **without** notarizing

If Jacob ever didn't want to notarize (he should — he already has the account, so
this is strictly worse), an **unsigned / ad-hoc-signed** `.app` or `.dmg` will
trip **Gatekeeper**. On modern macOS the user sees *"TidyTab can't be opened
because Apple cannot check it for malicious software"* — or, on a quarantined
download, the misleading *"TidyTab is damaged and can't be opened"*. Workarounds
the user would have to perform themselves:

- **Right-click (Control-click) → Open** → then **Open** in the dialog (registers
  a per-app exception), **or**
- Strip the quarantine flag from the download:
  ```bash
  xattr -dr com.apple.quarantine /Applications/TidyTab.app
  ```
- (On recent macOS, **System Settings → Privacy & Security → "Open Anyway"** after
  the first blocked launch.)

This shifts friction onto every downloader and looks sketchy ("damaged",
"malicious software"). **Recommendation: notarize.** Jacob already has the
Developer ID, so the only added cost is the one-time `notarytool` setup and ~1–2
minutes per release — and in return every user gets a clean double-click install.
The Accessibility-permission step in **(b)** is still required either way.
