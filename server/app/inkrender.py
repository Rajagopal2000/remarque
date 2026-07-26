"""Rasterize handwriting strokes (from the tablet's scratchpad) into a PNG.

The image is cropped to the ink's bounding box (plus margin) so the writing
fills the frame; small models transcribe much better with large glyphs.
"""

import io

from PIL import Image, ImageDraw


def _smooth(pts: list[tuple[float, float]], subdivisions: int = 4) -> list[tuple[float, float]]:
    """Catmull-Rom interpolation through the captured points.

    The tablet panel samples the pen at event-delivery rate, well below the
    digitizer's native rate, so fast cursive arrives as sparse polygons;
    splining them back into curves reads much better for the vision model.
    Endpoints are preserved.
    """
    if len(pts) < 3:
        return pts
    padded = [pts[0], *pts, pts[-1]]
    out: list[tuple[float, float]] = []
    for i in range(len(padded) - 3):
        p0, p1, p2, p3 = padded[i], padded[i + 1], padded[i + 2], padded[i + 3]
        out.append(p1)
        for j in range(1, subdivisions):
            t = j / subdivisions
            t2, t3 = t * t, t * t * t
            out.append(
                tuple(
                    0.5
                    * (
                        2 * p1[k]
                        + (-p0[k] + p2[k]) * t
                        + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                        + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3
                    )
                    for k in (0, 1)
                )
            )
    out.append(pts[-1])
    return out


def render_strokes(
    strokes: list[list[list[float]]],
    canvas_w: float,
    canvas_h: float,
    target_width: int = 1200,
) -> bytes:
    """strokes is a list of polylines; each polyline is a list of [x, y] points."""
    if canvas_w <= 0 or canvas_h <= 0:
        raise ValueError("canvas dimensions must be positive")

    points = [p for stroke in strokes for p in stroke if len(p) >= 2]
    if not points:
        raise ValueError("no ink points")
    min_x = min(p[0] for p in points)
    max_x = max(p[0] for p in points)
    min_y = min(p[1] for p in points)
    max_y = max(p[1] for p in points)
    margin = max(10.0, 0.06 * max(max_x - min_x, max_y - min_y))
    ink_w = max(1.0, max_x - min_x + 2 * margin)
    ink_h = max(1.0, max_y - min_y + 2 * margin)

    # Render supersampled, then downscale: the resampling antialiases the
    # strokes, and a thinner pen keeps small cursive loops (o, e, a) open
    # instead of filling them into blobs the vision model misreads.
    ss = 2
    scale = target_width * ss / ink_w
    width = target_width * ss
    height = max(32 * ss, int(ink_h * scale))

    def tx(p: list[float]) -> tuple[float, float]:
        return ((p[0] - min_x + margin) * scale, (p[1] - min_y + margin) * scale)

    image = Image.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(image)
    # Pen width relative to the writing size, not the canvas.
    line_width = max(3, int(height * 0.014))
    for stroke in strokes:
        pts = [tx(p) for p in stroke if len(p) >= 2]
        if len(pts) == 1:
            x, y = pts[0]
            r = line_width / 2
            draw.ellipse([x - r, y - r, x + r, y + r], fill=0)
        elif len(pts) > 1:
            draw.line(_smooth(pts), fill=0, width=line_width, joint="curve")
    image = image.resize((width // ss, height // ss), Image.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
