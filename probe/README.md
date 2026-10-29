# probe/ — hardware probes, NOT automated tests

Purpose: scripts that observe the real Wacom device (raw touch reports, HID
enumeration) for troubleshooting during development.
Allowed: standalone scripts guarded by `if __name__ == "__main__"`.
Excluded: pytest suites and CI-runnable code — those live in `tests/`.
Usage: run manually with a connected tablet, e.g.
`python probe/touch_stream_probe.py`.
