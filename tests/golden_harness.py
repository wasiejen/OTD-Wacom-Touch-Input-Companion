"""Golden-sequence harness for the touch gesture cascade (Phase C unit C0).

The harness is the report-in/actions-out oracle the C2 FSM extraction builds
on: a scripted list of HID touch reports (plus optional pen-proximity events
interleaved) runs through the REAL decode and cascade entry points --
``process_touch_report`` and ``parse_pen_packet`` on one shared TabletState
and ActionDispatcher, the same objects the Pen and Touch reader threads share
on a live connection -- and the harness records the ordered input sequence the
cascade emits at SendInput level.

Capture point
    The recorder replaces ``ctypes.windll.user32.SendInput`` with a decoding
    stub for the duration of one run -- the same opt-in shadow lane the
    conftest live-injection guard reserves for its ``stub_send_input``
    fixture. The guard stays installed for everything the harness does not
    shadow: an injection that ever bypasses the recorder hits the guard and
    fails the session by name. Goldens therefore capture emitted Win32 input
    events (virtual-key codes, button edges, move deltas, wheel deltas), NOT
    dispatcher function calls -- a renamed or restructured method keeps
    producing the same golden stream.

Time control
    The cascade reads wall time through ``time.time()`` (double-tap window,
    press-hold, tap duration, session cap). A run installs a fake clock over
    ``touch_controller.time`` and sets it to ``start + step.t`` before each
    report, so every scenario is fully deterministic -- goldens regenerate
    byte-identical.

The quirk rule
    The goldens capture CURRENT behavior, including latent quirks (e.g. the
    pinch ctrl press ignoring the 2f_pinch toggle, the stale scroll centroid
    after a pen interleave). The harness never normalizes, sorts, or filters
    the recorded stream. Fixes are C2 work; until then, quirks must stay
    visible in the goldens.

Not a test module: pytest does not collect this file. The pins live in
``tests/test_touch_cascade_goldens.py``; the fixture regeneration script is
``tests/generate_goldens.py``.
"""

import ctypes
import json
import subprocess
from pathlib import Path

import touch_controller
from vk_codes import vk_codes_dict
from touch_controller import (
    MAX_TOUCH_CONTACTS,
    ActionDispatcher,
    DriverConfig,
    TabletState,
    parse_pen_packet,
    process_touch_report,
)

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCENARIOS_PATH = TESTS_DIR / "golden_scenarios.json"
GOLDEN_PATH = TESTS_DIR / "goldens" / "touch_cascade.golden.json"

# Fake-clock origin. Deliberately far above any 0.0-initialized "never
# happened" state field (last_f1_tap_time), so the cascade's time-difference
# guards read as huge instead of as a fresh event.
FAKE_START = 1_000_000.0

# Touch sub-block statuses (touch_controller constants).
TOUCH_STATUS = {"down": 0xC0, "move": 0x80, "up": 0x20}

# Pen-proximity statuses accepted by parse_pen_packet: in-range marks the
# gate, 0x80 (out of range) opens it again and restarts the touch session
# bookkeeping.
PEN_STATUS = {"in": 0xE0, "tip_down": 0xE1, "out": 0x80}

# Win32 mouse flags used by the ActionDispatcher injection helpers.
_MOUSE_FLAG_BUTTON = {
    0x0002: ("left", False),
    0x0004: ("left", True),
    0x0008: ("right", False),
    0x0010: ("right", True),
    0x0020: ("middle", False),
    0x0040: ("middle", True),
    0x0080: ("x", False),
    0x0100: ("x", True),
}
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_ABSOLUTE = 0x8000
_MOUSEEVENTF_WHEEL = 0x0800
_MOUSEEVENTF_HWHEEL = 0x1000
_KEYEVENTF_KEYUP = 0x0002

# Readability labels for the VK codes this cascade emits, derived from the
# SAME vk_codes_dict the dispatcher's key_tap lookups use (their numeric values
# are the product's, e.g. ctrl 162 / alt 164 -- NOT the SDK VK_CONTROL guess an
# agent might hardcode). The golden value is the numeric vk; the name is a
# hint only, so an unmapped vk stays honest (name null).
VK_LABEL_KEYS = ("ctrl", "alt", "shift", "tab", "enter", "lwin",
                 "left", "up", "right", "down", "z", "y", "d")
VK_NAMES = {}
for _label in VK_LABEL_KEYS:
    VK_NAMES.setdefault(vk_codes_dict[_label], _label)


def _signed32(value):
    """The SIGNED value Windows reads from the ULONG mouseData field.

    MOUSEINPUT.mouseData is a c_ulong, so a negative wheel delta arrives here
    wrapped (2**32-12 for -12). The desktop interprets that field as a signed
    LONG; the golden records what the desktop acts on, one documented
    conversion away from the raw ULONG round-trip the injection makes.
    """
    value = int(value)
    if value >= 0x80000000:
        value -= 0x100000000
    return value


class FakeClock:
    """Deterministic stand-in for the ``time`` module inside touch_controller.

    The cascade path reads only ``time.time()``; ``sleep`` fails loud so a
    future cascade that blocks inside a report cannot hide a wall-clock wait
    behind a deterministic-looking golden.
    """

    def __init__(self, start):
        self.t = float(start)

    def set(self, t):
        self.t = float(t)

    def time(self):
        return self.t

    def sleep(self, seconds):
        raise AssertionError("cascade must not sleep inside one report: %r" % (seconds,))


def touch_block(slot, status, x, y):
    """One 8-byte touch sub-block, packed the way parse_sub_block unpacks it.

    Slot ids start at 1: slot id 0 parses as the block-header byte value the
    decoder treats as an empty block and drops (WM fixture rule).
    """
    return [slot, TOUCH_STATUS[status],
            (x >> 4) & 0xFF, (y >> 4) & 0xFF,
            ((x & 0xF) << 4) | (y & 0xF), 0, 0, 0]


def build_touch_report(blocks):
    """Encode logical [slot, status, x, y] blocks and frame them into one 0x02 report."""
    built = [touch_block(*block) for block in blocks]
    return [0x02, len(built)] + [b for block in built for b in block]


def decode_input(ii):
    """Decode one captured INPUT struct into a compact JSON-safe action dict."""
    kind = int(ii.type)
    if kind == 1:
        ki = ii.ki
        vk = int(ki.wVk)
        return {"e": "key", "vk": vk, "name": VK_NAMES.get(vk),
                "up": bool(int(ki.dwFlags) & _KEYEVENTF_KEYUP)}
    if kind == 0:
        mi = ii.mi
        flags = int(mi.dwFlags)
        if flags & _MOUSEEVENTF_WHEEL:
            return {"e": "wheel", "axis": "y", "delta": _signed32(mi.mouseData)}
        if flags & _MOUSEEVENTF_HWHEEL:
            return {"e": "wheel", "axis": "x", "delta": _signed32(mi.mouseData)}
        for bit, (button, up) in _MOUSE_FLAG_BUTTON.items():
            if flags & bit:
                return {"e": "btn", "btn": button, "up": up}
        if flags & _MOUSEEVENTF_MOVE:
            event = {"e": "move", "dx": int(mi.dx), "dy": int(mi.dy)}
            if flags & _MOUSEEVENTF_ABSOLUTE:
                event["abs"] = True
            return event
        return {"e": "mouse", "flags": flags}
    return {"e": "unknown", "type": kind}


def make_config(overrides):
    """Build one DriverConfig from the shipped defaults plus toggle/mapping flips.

    Only feature_toggles and action_mapping are overridable: threshold and
    sensitivity fields feed the ``sensitivities`` tables built at __init__, so
    overriding them here would fork a second sync rule (C0 pins the shipped
    motion defaults; scenarios tune coordinates and timing instead).
    """
    cfg = DriverConfig()
    for key, value in (overrides.get("toggles") or {}).items():
        cfg.feature_toggles[key] = value
    for key, value in (overrides.get("mapping") or {}).items():
        cfg.action_mapping[key] = value
    return cfg


class CascadeHarness:
    """One scripted scenario against the real decode + cascade, actions out."""

    def __init__(self, scenario, start=FAKE_START):
        self.scenario = scenario
        self.start = float(start)
        self.clock = FakeClock(self.start)
        self.cfg = make_config(scenario.get("config") or {})
        self.state = TabletState()
        self.ad = ActionDispatcher(self.state, self.cfg)
        self.actions = []
        self.steps = scenario["steps"]

    # -- capture plumbing -------------------------------------------------
    def _record(self, count, data, size):
        if count != 1:
            raise AssertionError(
                "cascade injected a multi-event SendInput (count=%r); the "
                "recorder decodes data[0] only -- extend it before accepting "
                "multi-event emissions" % (count,))
        ii = data[0] if data is not None else None
        if ii is not None:
            self.actions.append(decode_input(ii))
        return count

    def _dispatch(self, step):
        self.clock.set(self.start + step["t"])
        kind = step["kind"]
        if kind == "touch":
            report = build_touch_report(step["blocks"])
            process_touch_report(report, self.state, self.ad, self.cfg)
        elif kind == "pen":
            parse_pen_packet([0x02, PEN_STATUS[step["status"]]], self.state)
        else:
            raise ValueError("unknown step kind: %r" % (kind,))

    def run(self):
        """Run every step; return the ordered emitted-action sequence.

        The recorder shadows the live-injection guard and the fake clock
        shadows the real one only for this window; both are restored in the
        finally so a failing scenario cannot leak either swap into the suite.
        """
        user32 = ctypes.windll.user32
        previous_send = user32.SendInput
        previous_time = touch_controller.time
        touch_controller.time = self.clock
        user32.SendInput = self._record
        try:
            for step in self.steps:
                self._dispatch(step)
        finally:
            touch_controller.time = previous_time
            user32.SendInput = previous_send
        return self.actions


# ==============================================================================
# shared file plumbing (scenarios, goldens, canonical serialization)
# ==============================================================================

def canonical_json(obj):
    """The ONE canonical serialization the goldens and pins compare byte-exact."""
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=True)


def load_scenarios():
    return json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]


def load_golden_doc():
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def run_scenario(scenario):
    """Run one scenario dict, return the ordered action list."""
    return CascadeHarness(scenario).run()


def run_all_scenarios():
    """Run every scripted scenario; return {name: {"desc", "actions", "note"?}}."""
    payloads = {}
    for scenario in load_scenarios():
        entry = {"desc": scenario["desc"], "actions": run_scenario(scenario)}
        if "note" in scenario:
            entry["note"] = scenario["note"]
        payloads[scenario["name"]] = entry
    return payloads


def source_commit_info():
    """HEAD short hash + subject of the tree the goldens were generated from."""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
                          capture_output=True, text=True).stdout.strip()
    subject = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=str(REPO_ROOT),
                             capture_output=True, text=True).stdout.strip()
    return head, subject


def build_golden_doc(scenario_payloads):
    head, subject = source_commit_info()
    return {
        "_meta": {
            "comment": ("GENERATED FILE - do not edit. Goldens capture CURRENT "
                        "cascade behavior including latent quirks (C0 rule: no "
                        "fixes ride the harness). Regenerate with "
                        "'python tests/generate_goldens.py' from the repo root; "
                        "the pin module compares action streams byte-exact."),
            "generated_by": "tests/generate_goldens.py",
            "harness": "tests/golden_harness.py",
            "scenarios_file": "tests/golden_scenarios.json",
            "source_commit": head,
            "source_commit_subject": subject,
        },
        "scenarios": scenario_payloads,
    }


def write_golden_file(doc, path=GOLDEN_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(doc) + "\n", encoding="utf-8")
