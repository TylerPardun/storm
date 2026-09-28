"""One label per CWA, placed inside it (ui/map/widget.py cwa_label_point)."""
from ui.map.widget import _inside, cwa_label_point


def test_a_label_sits_inside_an_l_shaped_area():
    # an L whose bounding-box center (2, 2) is outside it
    ring = [[0, 0], [4, 0], [4, 1], [1, 1], [1, 4], [0, 4], [0, 0]]
    lon, lat = cwa_label_point([ring])
    assert _inside([tuple(p) for p in ring], lon, lat)


def test_the_largest_part_gets_the_label():
    island = [[10, 10], [10.1, 10], [10.1, 10.1], [10, 10.1], [10, 10]]
    main = [[0, 0], [3, 0], [3, 3], [0, 3], [0, 0]]
    lon, lat = cwa_label_point([island, main])
    assert 0 < lon < 3 and 0 < lat < 3


def test_no_usable_ring_gives_no_label():
    assert cwa_label_point([[[0, 0], [1, 1]]]) is None
