"""#476 - shared Tk-init guard: broken runner Tk must SKIP GUI roots, never ERROR.

CI 2026-11-27 showed "806 passed, 6 errors": every root-creating GUI fixture
died with _tkinter.TclError ("Can't find a usable tk.tcl ... clamTheme.tcl
no such file") - the GitHub hosted Windows runner's Tk toolcache was corrupt
(environment flakiness, not product). conftest.make_tk_root() is the suite's
single root-construction site: it turns an init-time TclError into a loud
module skip naming the cause, while a working host is unchanged.

These pins prove the guard contract without needing a broken Tk: pytest
renders a pytest.skip.Exception raised during fixture setup as SKIPPED, so
calling the guard with Tk patched broken and asserting the raised exception
IS the skip-mechanism pin (a subprocess pytest run would only re-prove
pytest's own reporting).
"""

import os
import subprocess
import sys
import tkinter
from pathlib import Path

import pytest

from conftest import make_tk_root


class _BrokenTk:
    """Stands in for tkinter.Tk on a corrupt-toolcache runner (#476 CI)."""

    def __init__(self):
        raise tkinter.TclError(
            'Can\'t find a usable tk.tcl: no such file or directory: '
            '"C:/Tcl/lib/tk8.6/clamTheme.tcl"')


def test_broken_tk_init_skips_with_the_named_cause(monkeypatch):
    """The guard skips loudly, naming the cause and the original error."""
    monkeypatch.setattr(tkinter, "Tk", _BrokenTk)
    with pytest.raises(pytest.skip.Exception) as excinfo:
        make_tk_root()
    msg = str(excinfo.value)
    assert "tk runtime unusable on this host" in msg
    # Loud: the original TclError text rides along for diagnosis.
    assert "clamTheme.tcl" in msg


def test_working_host_gets_the_root_object_back_untouched(monkeypatch):
    """On a working host the guard is plain Tk construction: root comes back."""
    sentinel = object()

    class _OkTk:
        def __new__(cls):
            return sentinel

    monkeypatch.setattr(tkinter, "Tk", _OkTk)
    assert make_tk_root() is sentinel


def test_non_tclerror_init_failure_still_errors(monkeypatch):
    """Only TclError is the unusable-runtime class; other failures stay loud.

    The guard wraps root CONSTRUCTION only, so an init failure outside the
    TclError class is never silenced into a skip.
    """
    class _WeirdTk:
        def __init__(self):
            raise RuntimeError("not a TclError")

    monkeypatch.setattr(tkinter, "Tk", _WeirdTk)
    with pytest.raises(RuntimeError):
        make_tk_root()


_TESTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_DIR.parent

# The needle below must not occur verbatim in this file (the invariant pin
# scans every tests/ source), so it is assembled by concatenation.
_TK_CALL = "tk." + "Tk()"

# Process-wide broken-Tk plugin for the nested e2e run below (report-level
# SKIPPED-vs-ERROR proof the in-process pins cannot give).
_BROKEN_TK_PLUGIN = (
    "import tkinter\n"
    "\n"
    "\n"
    "def _boom(*args, **kwargs):\n"
    '    raise tkinter.TclError("simulated #476: Cant find a usable tk.tcl")\n'
    "\n"
    "\n"
    "tkinter.Tk = _boom\n"
)

# A two-line GUI module: module fixture routes through the shared guard.
_MINI_GUI_TEST = (
    "import pytest\n"
    "\n"
    "from conftest import make_tk_root\n"
    "\n"
    "\n"
    "@pytest.fixture(scope=\"module\")\n"
    "def mini_root():\n"
    "    root = make_tk_root()\n"
    "    root.withdraw()\n"
    "    yield root\n"
    "\n"
    "\n"
    "def test_mini_gui_lane(mini_root):\n"
    "    assert mini_root is not None\n"
)


def test_broken_runner_report_shows_skipped_not_error(tmp_path):
    """#476 P2 (committed e2e leg): a broken-Tk pytest process reports SKIPPED.

    Runs a nested pytest over a minimal GUI module with tkbreak patching
    tkinter.Tk process-wide before collection: the report must show the
    skip (exit 0, named cause) and no error - the exact report shape CI
    produces, and the harness that catches module-skip fallout the
    in-process pins cannot see.
    """
    (tmp_path / "tkbreak.py").write_text(_BROKEN_TK_PLUGIN, encoding="utf-8")
    mini = tmp_path / "test_mini_gui.py"
    mini.write_text(_MINI_GUI_TEST, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path), str(_TESTS_DIR), env.get("PYTHONPATH", "")])
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(mini), "-p", "tkbreak",
         "-rs", "-q"],
        cwd=str(_REPO_ROOT), env=env, capture_output=True, text=True,
        timeout=120)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "1 skipped" in out, out
    assert "1 error" not in out and "1 failed" not in out, out
    assert "tk runtime unusable on this host" in out, out


def test_no_direct_tk_root_construction_outside_the_guard():
    """#476 invariant pin: inside tests/ roots are built ONLY via the guard.

    Guards the conftest section comment: a new GUI test file constructing
    a root directly would silently reintroduce the ERROR class this unit
    removed on broken-Tk runners.
    """
    offenders = []
    for path in sorted(_TESTS_DIR.rglob("*.py")):
        if path.name == "conftest.py":
            continue
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if _TK_CALL in line and not stripped.startswith("#"):
                offenders.append("%s:%d: %s" % (path.name, lineno, stripped))
    assert offenders == []
