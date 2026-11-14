# tests/ — how to run, and one trap

## Run

From the repo root with the tree venv (system Python lacks pytest — estate knowledge KW-06):

```bash
./.venv/Scripts/python.exe -m pytest tests/ test/ -q
```

## TEST-CWD GUARD (estate TODO #425)

`tests/conftest.py` refuses to load when a `user.cfg` sits in the pytest
CWD. Reason: `touch_controller.CONFIG_FILE_PATH` is the bare relative name
`user.cfg` and several test modules instantiate `DriverConfig()` without
chdir — a stray config left in the CWD by any debug run silently overrides
the mapping defaults those tests assume (measured: deterministic touch/pen
thread failures). The guard aborts the run at conftest import time (before
any test), prints the exact path, and never deletes or moves the file:
move or delete it yourself and re-run.

If you want to test config loading on purpose, chdir into a tmp dir inside
the test (the existing pattern) or pass an explicit path — do not place a
`user.cfg` in the run CWD.
