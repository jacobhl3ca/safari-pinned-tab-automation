import os, sys, json, time, tempfile, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tidytab, rumps
from rumps.rumps import NSApp as D

tmp = tempfile.mkdtemp()
tidytab.PREFS_PATH = os.path.join(tmp, "prefs.json")
fails = 0
def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name); fails += (not cond)

# 1. category registered with a BOOL signature
sel = b"applicationShouldHandleReopen:hasVisibleWindows:"
check("delegate responds to reopen selector", bool(D.instancesRespondToSelector_(sel)))
sig = D.instanceMethodSignatureForSelector_(sel)
check(f"return type is BOOL ({sig.methodReturnType()})", sig.methodReturnType() in (b"c", b"B", b"Z"))
check(f"2nd arg is BOOL ({sig.getArgumentTypeAtIndex_(3)})", sig.getArgumentTypeAtIndex_(3) in (b"c", b"B", b"Z"))

class Item:
    def __init__(self): self.visible = True
    def setVisible_(self, v): self.visible = v
def fake():
    f = types.SimpleNamespace(_nsapp=types.SimpleNamespace(nsstatusitem=Item()), _relaunching=False)
    for m in ("_set_icon_visible", "_set_icon_hidden", "_apply_icon_visibility", "_on_reopen",
              "_begin_running_ui", "_end_running_ui"):
        setattr(f, m, types.MethodType(getattr(tidytab.TidyTabApp, m), f))
    return f
def prefs(): return tidytab.load_prefs()
def setp(d): tidytab.save_prefs(d)

# 2. launch-by-hand with hidden pref -> shown + pref cleared
setp({"hide_icon": True}); sys.argv = ["TidyTab"]; f = fake(); f._apply_icon_visibility()
check("hand launch shows icon", f._nsapp.nsstatusitem.visible is True and prefs()["hide_icon"] is False)
# 3. login launch keeps hidden
setp({"hide_icon": True}); sys.argv = ["TidyTab", "--login"]; f = fake(); f._apply_icon_visibility()
check("login launch stays hidden", f._nsapp.nsstatusitem.visible is False and prefs()["hide_icon"] is True)
# 4. update relaunch (fresh stamp) keeps hidden, consumes stamp
setp({"hide_icon": True, "relaunched_at": time.time()}); sys.argv = ["TidyTab"]; f = fake(); f._apply_icon_visibility()
check("update relaunch stays hidden + stamp consumed", f._nsapp.nsstatusitem.visible is False and "relaunched_at" not in prefs())
# 5. stale stamp -> treated as hand launch
setp({"hide_icon": True, "relaunched_at": time.time() - 999}); f = fake(); f._apply_icon_visibility()
check("stale relaunch stamp shows icon", f._nsapp.nsstatusitem.visible is True)
# 6. default (no pref) visible, other prefs kept
setp({"color": "White", "onboarded": True}); f = fake(); f._apply_icon_visibility()
check("no pref -> visible, other prefs kept", f._nsapp.nsstatusitem.visible is True and prefs().get("onboarded") is True)
# 7. reopen via the real ObjC delegate shows icon
setp({"hide_icon": True}); f = fake(); f._nsapp.nsstatusitem.visible = False
setattr(rumps.App, "*app_instance", f)
ret = D.alloc().init().applicationShouldHandleReopen_hasVisibleWindows_(None, False)
check("reopen -> shown + pref cleared + returns True", ret is True and f._nsapp.nsstatusitem.visible is True and prefs()["hide_icon"] is False)
# 8. reopen during self-update relaunch is ignored
setp({"hide_icon": True}); f = fake(); f._nsapp.nsstatusitem.visible = False; f._relaunching = True
setattr(rumps.App, "*app_instance", f)
D.alloc().init().applicationShouldHandleReopen_hasVisibleWindows_(None, True)
check("reopen ignored while relaunching", f._nsapp.nsstatusitem.visible is False and prefs()["hide_icon"] is True)
# 9. run shows icon, end re-hides
setp({"hide_icon": True}); f = fake(); f._nsapp.nsstatusitem.visible = False
f._start_space_monitor = lambda: None; f._stop_space_monitor = lambda: None; f._apply_idle = lambda: None
f._watchdog = object()
tidytab.TidyTabApp._begin_running_ui.__wrapped__ if False else None
vis_during = None
f.title = ""
try:
    f._begin_running_ui()
except Exception as e:
    pass
vis_during = f._nsapp.nsstatusitem.visible
f._watchdog = None; f._end_running_ui()
check("run shows icon, end hides it again", vis_during is True and f._nsapp.nsstatusitem.visible is False)
# 10. login-item agent gets --login; old agent upgraded
agent = os.path.join(tmp, "agent.plist"); tidytab.LAUNCH_AGENT = agent
tidytab._app_executable = lambda: "/Applications/TidyTab.app/Contents/MacOS/TidyTab"
open(agent, "w").write("<plist><array><string>/Applications/TidyTab.app/Contents/MacOS/TidyTab</string></array></plist>")
tidytab._upgrade_login_item()
txt = open(agent).read()
check("old agent upgraded with --login", "<string>--login</string>" in txt and "RunAtLoad" in txt)
import plistlib
pl = plistlib.loads(txt.encode())
check("agent plist parses, args = [exe, --login]", pl["ProgramArguments"][1:] == ["--login"] and pl["Label"] == "com.jacob.tidytab")
print("FAILS:", fails)

# 11. installed-path: follows the running bundle under /Applications, else default
import importlib
def path_for(res):
    if res is None: os.environ.pop("RESOURCEPATH", None)
    else: os.environ["RESOURCEPATH"] = res
    return tidytab._installed_app_path()
check("subfolder copy updates in place",
      path_for("/Applications/2. Browsers/TidyTab.app/Contents/Resources") == "/Applications/2. Browsers/TidyTab.app")
check("top-level copy unchanged", path_for("/Applications/TidyTab.app/Contents/Resources") == "/Applications/TidyTab.app")
check("dmg / Downloads copy -> default", path_for("/Volumes/TidyTab/TidyTab.app/Contents/Resources") == "/Applications/TidyTab.app")
check("source run -> default", path_for(None) == "/Applications/TidyTab.app")
os.environ["RESOURCEPATH"] = "/Applications/2. Browsers/TidyTab.app/Contents/Resources"
t2 = importlib.reload(tidytab)
check("swap paths are siblings", t2._SWAP_STAGING == "/Applications/2. Browsers/.TidyTab.new"
      and t2._SWAP_BACKUP == "/Applications/2. Browsers/.TidyTab.old")
os.environ.pop("RESOURCEPATH", None)
print("FAILS:", fails)
