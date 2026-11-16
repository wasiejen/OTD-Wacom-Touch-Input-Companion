"""F3 (2026-11-16): initial window geometry from the primary work area.

Maintainer report: at 150% scaling the fixed 460x650 window hid most tab
content (his manual resize measured ~870x1600). The fix computes the
initial geometry from the WORK AREA in the process's DPI space
(SPI_GETWORKAREA - tkinter geometry lives in the same space). Pins are
PURE: work area in, geometry out, deterministic - no tkinter, no ctypes.
"""

import gui


def test_needed_size_when_the_work_area_is_generous():
    # A 150%-scaled 4K desktop reports a virtual work area ~2560x1350:
    # the wanted 870x1600 fits in width, height caps at 70% = 945,
    # centered: x=(2560-870)/2=845, y=(1350-945)/2=202 (floor).
    assert gui.initial_window_geometry((0, 0, 2560, 1350)) == (870, 945, 845, 202)


def test_work_area_fill_caps_a_taller_than_screen_request():
    # 100% on 1920x1080: height caps at 70% of 1080 = 756.
    assert gui.initial_window_geometry((0, 0, 1920, 1080)) == (870, 756, 525, 162)


def test_negative_origin_work_area_keeps_the_offset():
    # Left-of-origin primary: the offset rides through to the position.
    width, height, x, y = gui.initial_window_geometry((-1920, 0, 1920, 1080))
    assert (width, height) == (870, 756)
    assert x == -1920 + 525
    assert y == 162


def test_never_larger_than_the_work_area():
    # Tiny area: BOTH dimensions shrink below the wanted size and the
    # whole window stays inside the area.
    width, height, x, y = gui.initial_window_geometry((0, 0, 300, 200))
    assert (width, height) == (210, 140)
    assert width <= 300 and height <= 200
    assert x >= 0 and y >= 0 and x + width <= 300 and y + height <= 200


def test_geometry_is_deterministic_pure_math():
    a = gui.initial_window_geometry((0, 0, 2560, 1350))
    b = gui.initial_window_geometry((0, 0, 2560, 1350))
    assert a == b
    assert all(isinstance(v, int) for v in a)


def test_fill_rule_exact_on_common_resolutions():
    # Reviewer P2 (60d0651), GUI-B B3 re-pin: 0.7 is NOT exact in IEEE
    # floats (1080*0.7 = 755.99999999999995), so this leg now genuinely
    # locks the helper's +0.5 rounding: 756 and 945 on two common
    # resolutions, and any future non-exact fill cannot lose a pixel.
    assert gui.initial_window_geometry((0, 0, 1000, 1080))[1] == 756
    assert gui.initial_window_geometry((0, 0, 1000, 1350))[1] == 945
