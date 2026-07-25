from app.inkselect import circled_groups


def _loop(x0, y0, x1, y1):
    """A nearly-closed rectangle-ish loop with enough points to qualify."""
    xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
    return [
        [x0, y0], [xm, y0], [x1, y0], [x1, ym], [x1, y1],
        [xm, y1], [x0, y1], [x0, ym], [x0 + 1, y0 + 1],
    ]


QUESTION = [[10.0, 10.0], [60.0, 15.0], [90.0, 40.0]]


def test_circle_selects_enclosed_stroke():
    outside = [[200.0, 200.0], [250.0, 210.0]]
    strokes = [QUESTION, _loop(0, 0, 100, 50), outside]
    groups = circled_groups(strokes)
    assert groups == [{"loop": 1, "inside": [0]}]


def test_empty_loop_is_not_a_selection():
    # A handwritten 'o': closed but encloses nothing.
    strokes = [QUESTION, _loop(200, 200, 215, 215)]
    assert circled_groups(strokes) == []


def test_open_arc_is_not_a_selection():
    arc = [[0.0, 0.0], [50.0, -10.0], [100.0, 0.0], [110.0, 30.0],
           [100.0, 60.0], [50.0, 70.0], [10.0, 60.0], [0.0, 45.0]]
    arc[-1] = [60.0, 45.0]  # ends far from the start relative to its size
    strokes = [QUESTION, arc]
    assert circled_groups(strokes) == []


def test_multiple_circles_in_drawing_order():
    q2 = [[10.0, 110.0], [60.0, 115.0], [90.0, 140.0]]
    strokes = [QUESTION, _loop(0, 0, 100, 50), q2, _loop(0, 100, 100, 150)]
    groups = circled_groups(strokes)
    assert [g["loop"] for g in groups] == [1, 3]
    assert [g["inside"] for g in groups] == [[0], [2]]
