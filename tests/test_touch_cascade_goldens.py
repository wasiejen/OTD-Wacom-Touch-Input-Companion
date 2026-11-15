"""CI pins: the touch cascade still emits the recorded golden action streams.

Phase C unit C0. These pins are the contract C2 (FSM extraction) builds on:
scenario input -> ordered SendInput-level actions, byte-exact on the canonical
JSON form the golden fixture stores (see golden_harness.py for the capture
lane). Goldens capture CURRENT behavior including latent quirks; a red pin
here means the cascade's emitted stream changed. If that is a deliberate
behavior change, regenerate and review the fixture diff:

    python tests/generate_goldens.py

Nothing in this module talks to a device or a desktop: the harness shadows the
conftest live-injection guard with its own recording stub, and the pins assert
the guard stayed clean afterwards.
"""

import ctypes
import subprocess

import pytest

import conftest
import golden_harness as gh


# ==============================================================================
# per-scenario golden comparisons (byte-exact canonical JSON)
# ==============================================================================

def _scenarios_and_goldens():
    doc = gh.load_golden_doc()
    scenarios = {s["name"]: s for s in gh.load_scenarios()}
    goldens = doc["scenarios"]
    assert set(scenarios) == set(goldens), (
        "scenario file and golden fixture disagree on the scenario set")
    return scenarios, goldens


@pytest.mark.parametrize("name", sorted(
    s["name"] for s in gh.load_scenarios()))
def test_cascade_matches_golden_action_sequence(name, capsys):
    """One scenario's emitted action stream equals its stored golden, byte-exact.

    capsys keeps the cascade's own prints (palm-rejection notices) out of the
    test log; the assertions are about the recorded SendInput stream.
    """
    scenarios, goldens = _scenarios_and_goldens()
    observed = gh.run_scenario(scenarios[name])
    canonical_observed = gh.canonical_json(observed)
    canonical_golden = gh.canonical_json(goldens[name]["actions"])
    assert canonical_observed == canonical_golden, (
        "cascade action stream diverged from the golden for %r:\nobserved: %s"
        % (name, canonical_observed))


# ==============================================================================
# harness and fixture integrity
# ==============================================================================

def test_golden_regenerate_is_deterministic_in_process(capsys):
    """Two consecutive full runs serialize byte-identical (the C0 gate)."""
    first = gh.canonical_json(gh.run_all_scenarios())
    second = gh.canonical_json(gh.run_all_scenarios())
    assert first == second


def test_golden_header_names_its_generation_source():
    """_meta states the generator, the harness, and an existing source commit."""
    meta = gh.load_golden_doc()["_meta"]
    assert meta["generated_by"] == "tests/generate_goldens.py"
    assert meta["harness"] == "tests/golden_harness.py"
    assert meta["scenarios_file"] == "tests/golden_scenarios.json"
    commit = meta["source_commit"]
    assert len(commit) == 40, "source_commit must be a full 40-char hash"
    probe = subprocess.run(["git", "cat-file", "-e", commit],
                           cwd=str(gh.REPO_ROOT), capture_output=True)
    assert probe.returncode == 0, "golden source_commit does not exist in git"
    assert "quirk" in meta["comment"].lower(), (
        "the quirk-preservation rule must stay stated in the fixture header")


def test_scenario_set_covers_the_required_c0_lanes():
    """The curated set stays at or above the C0 minimum and lane-complete.

    The names below are the coverage list the reviewer checks against the
    cascade branches; deleting or renaming a scenario is a coverage decision,
    not a cleanup.
    """
    names = {s["name"] for s in gh.load_scenarios()}
    assert len(names) >= 14
    assert len(names) == len(gh.load_scenarios()), "scenario names must be unique"
    required = [
        "1f_drag", "1f_tap_single", "1f_double_tap_drag_left_hold_release",
        "1f_double_tap_static_drag_holds", "1f_tap_outside_double_window",
        "1f_press_hold_left_drag",
        "2f_scroll_up", "2f_scroll_down", "2f_scroll_left", "2f_scroll_right",
        "2f_pinch_out", "2f_pinch_in",
        "2f_to_1f_scroll_continuation", "2f_tap_default_disabled",
        "2f_tap_enabled_right_click", "2f_press_hold",
        "2f_pinch_to_3f_release_ctrl",
        "3f_tap", "3f_swipe_right_in_cap", "3f_swipe_left",
        "3f_pinch_in_discrete_undo", "session_cap_blocks_3f_swipe",
        "4f_swipe_right_next", "5f_alt_tab_flow",
        "pen_gate_touch_ignored", "pen_touch_interleave_mid_scroll",
    ]
    missing = sorted(set(required) - names)
    assert not missing, "required C0 coverage scenarios missing: %s" % missing


def test_scenario_steps_are_wellformed_and_time_monotonic():
    """A malformed scenario edit fails loud instead of silently skipping lanes."""
    for scenario in gh.load_scenarios():
        assert scenario["desc"], scenario["name"]
        previous_t = -1.0
        for step in scenario["steps"]:
            assert step["kind"] in ("touch", "pen"), scenario["name"]
            assert step["t"] >= previous_t, (
                "%s: step times must not run backwards" % scenario["name"])
            previous_t = step["t"]
            if step["kind"] == "touch":
                for block in step["blocks"]:
                    slot, status, _x, _y = block
                    assert 1 <= slot <= gh.MAX_TOUCH_CONTACTS, (
                        "%s: slot ids start at 1 and stay inside the slot table"
                        % scenario["name"])
                    assert status in gh.TOUCH_STATUS, scenario["name"]
            else:
                assert step["status"] in gh.PEN_STATUS, scenario["name"]


# ==============================================================================
# capture-lane safety: the injection guard stays in charge outside the recorder
# ==============================================================================

def test_harness_run_leaves_no_injection_offender_and_restores_state(capsys):
    """The recorder, not the guard, absorbed every event, and both swaps undo.

    The guard replaces SendInput for the whole session; a harness run must
    leave zero new offenders and put the guard lambda back afterwards, so a
    later leak in any other test still names its owner.
    """
    user32 = ctypes.windll.user32
    guard_before = user32.SendInput
    offenders_before = len(conftest.inject_offenders)

    scenario = {"name": "safety", "desc": "safety", "steps": [
        {"t": 0.0, "kind": "touch", "blocks": [[1, "down", 600, 600]]},
        {"t": 0.016, "kind": "touch", "blocks": [[1, "up", 0, 0]]},
    ]}
    harness = gh.CascadeHarness(scenario)
    actions = harness.run()
    capsys.readouterr()

    assert actions, "the recorder must see the tap's injected events"
    assert user32.SendInput is guard_before, "SendInput swap was not restored"
    assert conftest.inject_offenders == [] or len(conftest.inject_offenders) == offenders_before, (
        "harness injection reached the guard: %r"
        % (conftest.inject_offenders[offenders_before:],))
    assert gh.touch_controller.time is not harness.clock, "clock swap was not restored"


def test_fake_clock_refuses_sleep_loud():
    """A cascade that ever blocks inside one report must not hide behind it."""
    clock = gh.FakeClock(0.0)
    with pytest.raises(AssertionError):
        clock.sleep(0.001)
