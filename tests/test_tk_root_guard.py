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

import io
import os
import re
import subprocess
import sys
import tkinter
import tokenize
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

# Every executable root-construction spelling (tk.Tk() and tkinter.Tk(),
# the spelling make_tk_root itself guards) is the invariant target; string
# literals and comments are masked out before matching, so prose may still
# document the old spelling.
_DIRECT_TK = re.compile(r"\btk(inter)?\.Tk\s*\(")

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

def _mask_strings_and_comments(src):
    """Return src lines with STRING/COMMENT/FSTRING text blanked out.

    The invariant pin targets executable construction only; masking lets
    documentation and string payloads mention the old spellings freely.
    """
    rows = [list(line) for line in src.splitlines()]
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return ["".join(r) for r in rows]
    text_types = {tokenize.STRING, tokenize.COMMENT,
                  getattr(tokenize, "FSTRING_MIDDLE", tokenize.STRING)}
    for tok in tokens:
        if tok.type not in text_types:
            continue
        (sr, sc), (er, ec) = tok.start, tok.end
        if sr == er:
            rows[sr - 1][sc:ec] = [" "] * (ec - sc)
            continue
        rows[sr - 1][sc:] = [" "] * (len(rows[sr - 1]) - sc)
        for r in range(sr, er - 1):
            rows[r] = [" "] * len(rows[r])
        rows[er - 1][:ec] = [" "] * ec
    return ["".join(r) for r in rows]


def test_broken_runner_report_shows_skipped_not_error(tmp_path):
    """#476 P2 (committed e2e leg): a broken-Tk pytest process reports SKIPPED.

    Runs a nested pytest over the repo's own root-creating GUI module
    tests/test_gui_move_flush.py - the file that ERRORed 6x in the #476 CI
    run - with tkbreak patching tkinter.Tk process-wide before collection:
    the report must show loud skips (exit 0, named cause), no failed and
    no error - the exact report shape CI produces, and the harness that
    catches module-skip fallout the in-process pins cannot see.
    """
    (tmp_path / "tkbreak.py").write_text(_BROKEN_TK_PLUGIN, encoding="utf-8")
    gui_file = _TESTS_DIR / "test_gui_move_flush.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(gui_file), "-p", "tkbreak",
         "-rs", "-q"],
        cwd=str(_REPO_ROOT), env=env, capture_output=True, text=True,
        timeout=120)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    # >= 6: the #476 CI module had six root-creating lanes; each must skip.
    assert out.count("tk runtime unusable on this host") >= 6, out
    # Case-sensitive pytest summary markers (the reason text carries the
    # word "TclError", so a lowercase substring check would self-match).
    assert "FAILED" not in out, out
    assert "ERROR" not in out, out


def test_no_direct_tk_root_construction_outside_the_guard():
    """#476 invariant pin: inside tests/ roots are built ONLY via the guard.

    Both executable spellings (tk.Tk() and tkinter.Tk()) are caught in every
    tests/ source outside conftest; strings and comments are masked first so
    documentation may still mention the old spelling. A new GUI file
    constructing a root directly would silently reintroduce the ERROR class
    this unit removed on broken-Tk runners.
    """
    offenders = []
    for path in sorted(_TESTS_DIR.rglob("*.py")):
        if path.name == "conftest.py":
            continue
        masked = _mask_strings_and_comments(
            path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(masked, 1):
            if _DIRECT_TK.search(line):
                offenders.append("%s:%d: %s" % (path.name, lineno,
                                                line.strip()))
    assert offenders == []
