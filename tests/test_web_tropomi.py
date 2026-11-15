import numpy as np

from past_so2 import config
from past_so2.web import tropomi as wt

WINDOW = config.load()["carra_window"]


def test_window_contains_reykjanes_and_excludes_points_beyond_its_edges():
    assert wt.in_window(63.88, -22.45, WINDOW)
    assert wt.in_window(WINDOW["corner_lat"], WINDOW["corner_lon"], WINDOW)  # centre of cell (0, 0)
    assert not wt.in_window(WINDOW["corner_lat"] - 0.05, WINDOW["corner_lon"] - 0.1, WINDOW)
    assert not wt.in_window(59.0, -22.0, WINDOW)


def test_grid_spacing_is_reproduced():
    # Two points 2.5 km apart along a meridian near the tangent latitude map to about one cell.
    x, y = wt.lcc_xy(np.array([71.9, 71.9 + 2.5 / 111.13]), np.array([-36.0, -36.0]), WINDOW)
    assert abs(np.hypot(x[1] - x[0], y[1] - y[0]) - 2500.0) < 15.0


def test_bbox_encloses_the_window_corners():
    west, south, east, north = wt.window_bbox(WINDOW)
    assert west <= WINDOW["corner_lon"] and south < 60.2 and east > -6.8 and north > 69.1


def test_colour_ramp_is_monotonic_in_opacity():
    alpha = wt.colours(np.array([-3.0, 0.0, 1.0, 5.0, 20.0, 80.0]))[:, 3]
    assert (np.diff(alpha.astype(int)) >= 0).all() and alpha[0] == alpha[1] and alpha[-1] == alpha[-2]
