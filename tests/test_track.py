import numpy as np

from marbletrack.track import Polyline


def test_arc_length_and_point_at():
    line = Polyline.from_points([(0, 0), (1, 0), (1, 1)])
    assert line.s_range == (0.0, 2.0)
    xy, tan = line.point_at(np.array([0.5, 1.5]))
    np.testing.assert_allclose(xy, [[0.5, 0], [1, 0.5]])
    np.testing.assert_allclose(tan, [[1, 0], [0, 1]])


def test_projection_uses_vertex_s_not_spacing():
    # Image-space polyline with perspective: equal pixel steps, unequal progress.
    line = Polyline([(0, 0), (100, 0), (150, 0)], [0.0, 1.0, 2.0])
    (c,) = line.project((125, 3), max_dist=10)
    assert abs(c.s - 1.5) < 1e-9 and abs(c.dist - 3) < 1e-9
    assert abs(c.s_per_unit - 1 / 50) < 1e-12


def test_self_crossing_track_returns_one_candidate_per_pass():
    # A loop that crosses itself at (5, 0): an overpass seen from above.
    pts = [(0, 0), (10, 0), (10, 5), (5, 5), (5, -5)]
    line = Polyline.from_points(pts)
    cands = line.project((5, 0.2), max_dist=1.0)
    assert len(cands) == 2
    s_values = sorted(c.s for c in cands)
    assert abs(s_values[0] - 5.0) < 0.3 and abs(s_values[1] - 25.0) < 0.3


def test_far_point_has_no_candidates():
    line = Polyline.from_points([(0, 0), (1, 0)])
    assert line.project((0.5, 2.0), max_dist=0.1) == []
