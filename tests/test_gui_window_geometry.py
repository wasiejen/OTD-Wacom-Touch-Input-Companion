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
    # the wanted 870x1600 fits in width, height caps at 90% = 1215,
    # centered: x=(2560-870)/2=845, y=(1350-1215)/2=67 (floor).
    assert gui.initial_window_geometry((0, 0, 2560, 1350)) == (870, 1215, 845, 67)


def test_work_area_fill_caps_a_taller_than_screen_request():
    # 100% on 1920x1080: height caps at 90% of 1080 = 972.
    assert gui.initial_window_geometry((0, 0, 1920, 1080)) == (870, 972, 525, 54)


def test_negative_origin_work_area_keeps_the_offset():
    # Left-of-origin primary: the offset rides through to the position.
    width, height, x, y = gui.initial_window_geometry((-1920, 0, 1920, 1080))
    assert (width, height) == (870, 972)
    assert x == -1920 + 525
    assert y == 54


def test_never_larger_than_the_work_area():
    # Tiny area: BOTH dimensions shrink below the wanted size and the
    # whole window stays inside the area.
    width, height, x, y = gui.initial_window_geometry((0, 0, 300, 200))
    assert (width, height) == (270, 180)
    assert width <= 300 and height <= 200
    assert x >= 0 and y >= 0 and x + width <= 300 and y + height <= 200


def test_geometry_is_deterministic_pure_math():
    a = gui.initial_window_geometry((0, 0, 2560, 1350))
    b = gui.initial_window_geometry((0, 0, 2560, 1350))
    assert a == b
    assert all(isinstance(v, int) for v in a)


def test_float_fill_products_do_not_lose_a_pixel():
    # 1080*0.9 and 1350*0.9 land on 972.000...01 / 1215.000...02 in IEEE
    # floats; the pin-side rule (90% rounded) must still name 972/1215.
    assert gui.initial_window_geometry((0, 0, 1000, 1080))[1] == 972
    assert gui.initial_window_geometry((0, 0, 1000, 1350))[1] == 1215
