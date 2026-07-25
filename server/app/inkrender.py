"""Rasterize handwriting strokes (from the tablet's scratchpad) into a PNG.

The image is cropped to the ink's bounding box (plus margin) so the writing
fills the frame; small models transcribe much better with large glyphs.
"""

import io

from PIL import Image, ImageDraw


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

    scale = target_width / ink_w
    width = target_width
    height = max(32, int(ink_h * scale))

    def tx(p: list[float]) -> tuple[float, float]:
        return ((p[0] - min_x + margin) * scale, (p[1] - min_y + margin) * scale)

    image = Image.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(image)
    # Pen width relative to the writing size, not the canvas.
    line_width = max(3, int(height * 0.02))
    for stroke in strokes:
        pts = [tx(p) for p in stroke if len(p) >= 2]
        if len(pts) == 1:
            x, y = pts[0]
            r = line_width / 2
            draw.ellipse([x - r, y - r, x + r, y + r], fill=0)
        elif len(pts) > 1:
            draw.line(pts, fill=0, width=line_width, joint="curve")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
