"""One shared 'wtd' logger for the whole package (C1 logging migration).

Output contract (review Item 3 / Item 11, the maintainer's hunting contract):
emitted lines are byte-identical to the pre-split print() text, so the
handlers format the bare message and the console sink resolves the CURRENT
sys.stdout at every emit (capsys-style captures and redirection keep
working). Both sinks carry the same line text; the file sink uses the
platform line ending (per-line content identical; line endings follow
the channel: LF on pipes/console, CRLF per line in Windows files). The handlers are
named 'wtd_stream' and 'wtd_file' so the logging module itself can tell
them apart. The package logger does not propagate: a host application's
root handlers must not double-print product lines. Level INFO keeps every
converted site visible exactly where print was visible before; DEBUG-gated
call sites keep their own explicit gate.

No-console file sink (follow-up R1, finishing review Item 11): tray mode
has no console, so stdout would vanish. The same line stream also lands in
%APPDATA%\\wtd\\wtd.log - the same per-user folder family as the config
lane (see APPDATA_DIR_NAME in wtd/config.py; the name is repeated here
because config imports this module first, so no import from config here).
The folder and the file are created on demand, at the first line that is
actually emitted (delay=True), the file rotates at 2 MB keeping 2 backups,
and lines are UTF-8. If the folder cannot be created (no %APPDATA% in an
odd service context) the file sink is skipped silently: the console sink
is the product's primary channel and never depends on the file.
install_sinks() is idempotent per handler name, so repeated flows (GUI,
CLI, tray, tests) never double-attach.
"""
import logging
import logging.handlers
import os
import sys

# Keep in sync with APPDATA_DIR_NAME in wtd/config.py (same folder family
# as the resolved config lane). Not imported from config: wtd/config.py
# imports this module first, and a reverse import would be circular.
LOG_DIR_NAME = "wtd"
LOG_FILE_NAME = "wtd.log"
# Rotation cap: the log grows at most ~2 MB x (1 + 2 backups).
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 2


def log_file_path():
    """The no-console sink path: %APPDATA%\\wtd\\wtd.log.

    WTD_LOG_FILE overrides the full path (test-harness seam only; the
    product never sets it) so suite runs never contaminate the
    maintainer's live hunting file - R1 reviewer P1-2.
    """
    override = os.environ.get("WTD_LOG_FILE")
    if override:
        return override
    return os.path.join(os.environ["APPDATA"], LOG_DIR_NAME, LOG_FILE_NAME)


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


class _LazyLogRotation(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that creates its parent folder on first open."""

    def _open(self):
        parent = os.path.dirname(self.baseFilename)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return logging.handlers.RotatingFileHandler._open(self)


def make_file_sink(path):
    """The rotating file sink: UTF-8, delay=True, %(message)s, named."""
    handler = _LazyLogRotation(path, mode="a", maxBytes=LOG_MAX_BYTES,
                               backupCount=LOG_BACKUP_COUNT, encoding="utf-8",
                               delay=True)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.set_name("wtd_file")
    return handler


def find_sink(logger, name):
    """The attached sink with this handler name, or None."""
    for handler in logger.handlers:
        if handler.get_name() == name:
            return handler
    return None


def install_sinks():
    """Attach the named sinks to the 'wtd' logger; idempotent per name.

    Returns the handlers newly added by THIS call (empty when both sinks
    are already attached, and for a file-sink path that cannot be opened
    later: the sink opens lazily at the first emitted line, so an
    unwritable target surfaces then as logging's own handled error, never
    as an import-time failure).
    """
    logger = logging.getLogger("wtd")
    had_handlers = bool(logger.handlers)
    added = []
    if find_sink(logger, "wtd_stream") is None:
        stream = logging.StreamHandler(_CurrentStdout())
        stream.setFormatter(logging.Formatter("%(message)s"))
        stream.set_name("wtd_stream")
        added.append(stream)
    if find_sink(logger, "wtd_file") is None:
        try:
            added.append(make_file_sink(log_file_path()))
        except Exception:
            pass  # console-only fallback: never break the product on the file
    for handler in added:
        logger.addHandler(handler)
    if not had_handlers:
        # First init only: a host or test that re-levels/re-routes the
        # logger afterwards keeps its setting across re-inits (R1 P2-4).
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return added


install_sinks()
logger = logging.getLogger("wtd")
