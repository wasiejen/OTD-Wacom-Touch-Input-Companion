# tests/ - how to run, and one trap

## Run

From the repo root with the tree venv (system Python lacks pytest - estate knowledge KW-06):

```bash
./.venv/Scripts/python.exe -m pytest tests/ -q
```

(`test/` exists only as a local leftover dir with no tracked files - do not
name it in the command; on a fresh clone it does not exist and pytest
aborts. The historical KW-06 command that includes `test/` only runs where
that stale dir survives.)

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
Some of those pins state a requirement today's build does not meet, so they
report `xfailed` with the fix unit named in the reason (A2 shutdown join, A3
Apply-Live durability, A4 held-input lock): when the fix unit lands, its pin
flips to green. An xfail whose printed reason does not match its stated lane
means the mechanism broke - read the reason, do not re-mark it.

The thread lanes synchronize on `threading` events and on the fake device's
`SignalDevice.first_read` / `closed_event` signals (conftest). Do not add
sleeps there: a sleep both slows the suite and hides a release that never ran.
