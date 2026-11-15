"""One shared 'wtd' logger for the whole package (C1 logging migration).

Output contract (review Item 3 / Item 11, the maintainer's hunting contract):
emitted lines are byte-identical to the pre-split print() text, so the
handler formats the bare message and resolves the CURRENT sys.stdout at
every emit (capsys-style captures and redirection keep working). The handler
is named 'wtd_stream' so the logging module itself can tell this sink apart.
The package logger does not propagate: a host application's root handlers
must not double-print product lines. Level INFO keeps every converted site
visible exactly where print was visible before; DEBUG-gated call sites keep
their own explicit gate.
"""
import logging
import sys


class _CurrentStdout:
    """Stream that delegates each call to the current sys.stdout."""

    def write(self, msg):
        return sys.stdout.write(msg)

    def flush(self):
        stream = getattr(sys, "stdout", None)
        if stream is not None:
            stream.flush()

    def isatty(self):
        stream = getattr(sys, "stdout", None)
        return stream is not None and stream.isatty()


def _install():
    logger = logging.getLogger("wtd")
    if not logger.handlers:
        handler = logging.StreamHandler(_CurrentStdout())
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.set_name("wtd_stream")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


logger = _install()
