# tests/ - how to run, and one trap

## Run

From the repo root with the tree venv (system Python lacks pytest - estate knowledge KW-06):

```bash
./.venv/Scripts/python.exe -m pytest tests/ -q
```

Do not name `test/` in the command: it was a local leftover dir with no tracked
files (removed in unit B3, estate plan Phase B), and a command that names a
missing directory makes pytest abort. The historical KW-06 command includes it -
run the one above instead.

## TEST-CWD GUARD (estate TODO #425)

`tests/conftest.py` refuses to load when a `user.cfg` sits in the pytest
CWD. Reason: `touch_controller.CONFIG_FILE_PATH` is the bare relative name
`user.cfg` and several test modules instantiate `DriverConfig()` without
chdir - a stray config left in the CWD silently overrides the mapping
defaults those tests assume (measured: deterministic touch/pen thread
failures). Either a debug run left the file here, or you run the app itself
from this directory and it saved its live config here. The guard aborts the
run at conftest import time (before any test), prints the exact path, and
never deletes or moves the file: MOVE it out of the run CWD - do not delete
it if it is your live app config.

If you want to test config loading on purpose, chdir into a tmp dir inside
the test (the existing pattern) or pass an explicit path - do not place a
`user.cfg` in the run CWD.

## Lifecycle pins (estate WTD plan Phase A / unit A1)

`tests/test_lifecycle_pins.py` covers the four lifetime lanes the architecture
review found uncovered: held-input release on shutdown (including whether the
shutdown path joins its readers), Apply-Live across pause/reconnect,
concurrent reader exits on one dispatcher, and the multi-device primary bind.
The shutdown lanes are green since the owned-shutdown fix (estate plan A2):
`main()`'s finally joins the reader handles the manager recorded inside one
bounded budget, then force-releases through each connection's dispatcher, so
even a wedged HID read cannot leave a key down or hang the exit.

All of those lanes are green now: the owned shutdown (A2), the Apply-Live
write-through (A3) and the held-input lock (A4) each flipped the requirement it
pinned. If a lifecycle lane ever fails, read its docstring - it names the
guarantee the product owes and the unit that established it. A future lane for
an unmet requirement reports `xfailed` with the fix unit in the reason; an
xfail whose reason does not match its stated lane means the mechanism broke -
read the reason, do not re-mark it.

Apply-Live durability across the churn (A3) is green since the write-through:
`gui.SettingsWindow.apply_settings` also stores the applied sections under the
focused device's identity key, so a pause/resume/reconnect refork inherits what
the user applied, and one device's Apply never reaches another.

The thread lanes synchronize on `threading` events and on the fake device's
`SignalDevice.first_read` / `closed_event` signals (conftest). Do not add
sleeps there: a sleep both slows the suite and hides a release that never ran.
