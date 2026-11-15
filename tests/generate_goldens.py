"""Regenerate the touch-cascade golden fixture (Phase C unit C0).

Run from the repo root::

    python tests/generate_goldens.py

Rewrites ``tests/goldens/touch_cascade.golden.json``: runs every scenario in
``tests/golden_scenarios.json`` through the real decode + cascade with the
SendInput capture recorder (see golden_harness.py) and stores the emitted
action streams. The ``_meta`` block names the HEAD commit the goldens were
generated from. The output is deterministic -- two runs on the same tree
diff empty.

The goldens capture CURRENT behavior including latent quirks (C0 rule: this
harness documents, it does not fix). If a pin in
``tests/test_touch_cascade_goldens.py`` goes red without a deliberate behavior
change, treat it as a regression, not as a fixture-refresh chore.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # product modules (conftest does this for pytest)

import golden_harness


def main():
    payloads = golden_harness.run_all_scenarios()
    doc = golden_harness.build_golden_doc(payloads)
    golden_harness.write_golden_file(doc)
    total_actions = sum(len(p["actions"]) for p in payloads.values())
    print("wrote %s" % golden_harness.GOLDEN_PATH.relative_to(golden_harness.REPO_ROOT))
    print("scenarios=%d actions=%d source_commit=%s"
          % (len(payloads), total_actions, doc["_meta"]["source_commit"]))


if __name__ == "__main__":
    main()
