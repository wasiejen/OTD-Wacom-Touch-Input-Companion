"""interface_probe.py — read-only Wacom interface enumeration.

Answers: which HID interface of a Wacom tablet (VID 0x056A) exposes pen
and touch data. Run it before deciding the pen-support interface.

Read-only guarantees:
  - never sends any output report to the device
  - never claims any interface (open_path is shared-mode on Windows)
  - input reads are bounded: MAX_REPORTS reports within READ_TIMEOUT
  - get_input_report is a FEATURE request (device state is not altered)

Run:  python probe/interface_probe.py
Exit code is always 0; device-free runs still print the enumeration.
"""
import re
import sys
import time

import hid

VENDOR_ID = 0x056A  # Wacom Co., Ltd.
MAX_REPORTS = 8     # stop after this many readable reports per path
READ_TIMEOUT = 2.0  # seconds: total read window per path
READ_INTERVAL = 0.05
REPORT_SIZE = 64

# Known Generic Desktop usages seen on Wacom tablets (hid.usage_page 0x0B).
USAGE_NAMES = {
    0x30: "Pen (digitizer)",
    0x31: "Digitizer (touch)",
    0x32: "Mouse",
    0x39: "Joystick-ish (vendor control)",
    0x02: "Keyboard",
}

PEN_STATUS = (0xE0, 0xE1, 0xE2, 0x80, 0x20, 0x22)


def decode_report(report):
    """Classify one input report as touch/ pen/ unknown.

    Heuristic based on the formats touch_controller.py parses:
      - touch: report id 0x02, len >= 10, count byte 1..10, first sub-block
        slot id 0x81 (SYNC) or 1..10 with a known status nibble
      - pen: report id 0x02, short len, status byte in PEN_STATUS
    """
    if not report:
        return "empty"
    if report[0] != 0x02:
        return "unknown"
    if len(report) >= 10:
        count = report[1]
        if 1 <= count <= 10:
            slot, status = report[2], report[3]
            if slot == 0x81 or (1 <= slot <= 10 and (status & 0x03) in (1, 2)):
                return "touch"
        return "unknown"
    if report[1] in PEN_STATUS:
        return "pen"
    return "unknown"


def usage_hint(usage_page, usage):
    if usage_page == 0x0B and usage in USAGE_NAMES:
        return USAGE_NAMES[usage]
    return None


def parent_instance(path):
    """Strip the interface suffix so one tablet groups its interfaces."""
    if isinstance(path, bytes):
        path = path.decode("utf-8", "replace")
    return re.sub(r'(?i)&?mi_[\da-f]+', '', path)


def format_reports(reports):
    lines = []
    for rep in reports[:3]:
        hex_data = " ".join(f"{b:02X}" for b in rep[:16])
        lines.append(f"    report: id=0x{rep[0]:02X} len={len(rep)} bytes[0:16]={hex_data} -> {decode_report(rep)}")
    return "\n".join(lines)


def probe_path(path, max_reports=MAX_REPORTS, timeout=READ_TIMEOUT):
    """Bounded read sample for one HID path. Returns (sample_line, error)."""
    dev = hid.device()
    try:
        dev.open_path(path)
    except OSError as e:
        return None, f"open failed: {e}"
    reports = []
    error = None
    try:
        start = time.monotonic()
        while len(reports) < max_reports and (time.monotonic() - start) < timeout:
            rep = dev.read(REPORT_SIZE)
            if rep:
                reports.append(rep)
            else:
                time.sleep(READ_INTERVAL)
        # Feature request: reads a device report, never changes device state.
        try:
            dev.get_input_report(0x02, 64)
            input_report = "get_input_report(0x02, 64): ok"
        except (OSError, ValueError) as e:
            input_report = f"get_input_report(0x02, 64): unsupported ({e})"
    except OSError as e:
        error = f"read failed: {e}"
        input_report = "readable reports: unknown"
    finally:
        dev.close()
    if reports:
        kinds = {decode_report(r) for r in reports}
        kinds.discard("unknown")
        kind = "/".join(sorted(kinds)) if kinds else "unknown format"
        sample = f"input reports: {len(reports)} readable -> decodes as {kind}\n      {input_report}\n{format_reports(reports)}"
    else:
        sample = f"input reports: none seen in {timeout:.1f}s window (idle)\n      {input_report}"
    if error:
        sample += f"\n      {error}"
    return sample, None


def enumerate_wacom():
    """All HID entries of Wacom devices, sorted by interface number."""
    return sorted(
        (d for d in hid.enumerate() if d.get("vendor_id") == VENDOR_ID),
        key=lambda d: (d.get("product_id", 0), d.get("interface_number", -1)),
    )


def print_enumeration(devices):
    print("== Enumeration ==")
    if not devices:
        print(f"  No HID device with vendor id 0x{VENDOR_ID:04X} found.")
        print("  Device-free run: plug the tablet in and re-run to see the interface section.")
        return
    groups = {}
    for dev in devices:
        groups.setdefault(parent_instance(dev.get("path", "")), []).append(dev)
    for parent, devs in groups.items():
        print(f"  Tablet instance: {parent}")
        for dev in devs:
            hint = usage_hint(dev.get("usage_page", 0), dev.get("usage", 0))
            line = (
                f"    iface={dev.get('interface_number', '?')}"
                f" usage_page=0x{dev.get('usage_page', 0):04X}"
                f" usage=0x{dev.get('usage', 0):04X}"
                f" product={dev.get('product_string', '?')}"
            )
            if hint:
                line += f"  hint: {hint} (descriptor guess, verify below)"
            print(line)
            print(f"      path: {dev.get('path', '')}")


def print_interfaces(devices):
    print("== Interface probe (bounded read) ==")
    for dev in devices:
        path = dev.get("path", "")
        print(f"  iface={dev.get('interface_number', '?')} usage=0x{dev.get('usage', 0):04X}")
        sample, error = probe_path(path)
        if error:
            print(f"      {error}")
        elif sample:
            print(f"      {sample}")
        print("      output reports: not probed (probe is read-only; output is never sent)")


def main():
    devices = enumerate_wacom()
    print_enumeration(devices)
    if devices:
        print()
        print_interfaces(devices)
    return 0


if __name__ == "__main__":
    sys.exit(main())
