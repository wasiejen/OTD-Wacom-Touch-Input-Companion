# probe/ — hardware probes, NOT automated tests

Purpose: scripts that observe the real Wacom device (raw touch reports, HID
enumeration) for troubleshooting during development.
Allowed: standalone scripts guarded by `if __name__ == "__main__"`.
Excluded: pytest suites and CI-runnable code — those live in `tests/`.
Usage: run manually with a connected tablet, e.g.
`python probe/touch_stream_probe.py`.

## interface_probe.py — interface decision tool

Run: `python probe/interface_probe.py` (venv python, no arguments).
The probe is strictly read-only: it never sends an output report, never
claims an interface (shared-mode open), and input reads are bounded
(8 reports within 2 s per path, then close).

Output sections:

- `== Enumeration ==` — every HID path of devices with vendor id 0x056A,
  grouped per tablet instance: interface number, usage page/usage, product
  string, and a usage hint (descriptor guess only — verify by decoding).
  Device-free run: the enumeration section prints "No HID device found"
  and the interface section does not appear.
- `== Interface probe (bounded read) ==` — appears only when a device is
  connected: per path, whether input reports are readable and whether the
  first readable reports decode as `touch` or `pen` data (a few sample
  reports are echoed). Output-report availability is not probed: probing
  it would require sending data, which this tool never does.

Reading the output:

- `decodes as touch` — touch-slot format (id 0x02, contact count + 8-byte
  sub-blocks, as `process_touch_report` parses).
- `decodes as pen` — short pen status report (id 0x02, status byte 0xE0/
  0xE1/0xE2/0x80, as `parse_pen_packet` reads).
- `none seen in 2.0s window (idle)` — interface is silent; touch and pen
  interfaces are usually quiet until the tablet is touched or hovered.

The pen-data path for pen support is decided from this output, not from
guesses or the usage-page hint. Move the pen/touch finger while the probe
runs to make the interfaces speak.
