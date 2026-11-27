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

import tkinter

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
    """On a working host the guard is tk.Tk() unchanged: the root comes back."""
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
