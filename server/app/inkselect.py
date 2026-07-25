"""Select which page ink is the question: a pen-drawn loop around handwriting
marks it - a lasso the server can actually see (the native selection tool's
state never reaches disk).

A stroke counts as a selection loop when it is nearly closed and encloses at
least one other stroke; groups come back in drawing order so the oldest
unanswered circle is asked first.
"""

Stroke = list[list[float]]


def _bbox(stroke: Stroke) -> tuple[float, float, float, float]:
    xs = [p[0] for p in stroke]
    ys = [p[1] for p in stroke]
    return min(xs), min(ys), max(xs), max(ys)


def _is_closed(stroke: Stroke) -> bool:
    if len(stroke) < 8:
        return False
    x0, y0, x1, y1 = _bbox(stroke)
    size = max(x1 - x0, y1 - y0)
    if size <= 0:
        return False
    gap = ((stroke[0][0] - stroke[-1][0]) ** 2 + (stroke[0][1] - stroke[-1][1]) ** 2) ** 0.5
    return gap <= 0.35 * size


def _inside(point: list[float], polygon: Stroke) -> bool:
    x, y = point[0], point[1]
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _encloses(loop: Stroke, stroke: Stroke, min_fraction: float = 0.7) -> bool:
    hits = sum(1 for p in stroke if _inside(p, loop))
    return bool(stroke) and hits / len(stroke) >= min_fraction


def circled_groups(strokes: list[Stroke]) -> list[dict]:
    """[{"loop": i, "inside": [j, ...]}, ...] in drawing order.

    A loop with nothing inside (an 'o' in handwriting) is not a selection;
    strokes claimed by an earlier group are not reconsidered.
    """
    groups: list[dict] = []
    claimed: set[int] = set()
    for i, stroke in enumerate(strokes):
        if i in claimed or not _is_closed(stroke):
            continue
        inside = [
            j
            for j, other in enumerate(strokes)
            if j != i and j not in claimed and _encloses(stroke, other)
        ]
        if not inside:
            continue
        groups.append({"loop": i, "inside": inside})
        claimed.add(i)
        claimed.update(inside)
    return groups
