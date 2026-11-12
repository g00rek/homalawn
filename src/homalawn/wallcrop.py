"""Fit a picture to the phone's screen: scale it to cover the screen, keep the centre (`--crop center`).

The phone gets a picture of exactly its screen size, so Android has nothing left to scale or move:
what the person sees on the computer (the middle of the picture) is what the phone shows.
"""
from __future__ import annotations

import io

from PIL import Image, ImageOps


def cover_box(src_w: int, src_h: int, w: int, h: int, offset: float = 0.5) -> tuple[float, float, float, float]:
    """The part of a src_w x src_h picture that, scaled up or down, exactly fills w x h: the
    largest box with the screen's proportions. `offset` slides it along the axis with spare room:
    0.0 = flush with the left/top edge, 0.5 = centred, 1.0 = flush with the right/bottom edge.
    (left, top, right, bottom) in source pixels."""
    if min(src_w, src_h, w, h) <= 0:
        raise ValueError(f"cannot fit a {src_w}x{src_h} picture to a {w}x{h} screen")
    scale = max(w / src_w, h / src_h)  # cover: the shorter side (relative to the screen) fills it
    bw, bh = w / scale, h / scale
    o = min(1.0, max(0.0, offset))
    left, top = (src_w - bw) * o, (src_h - bh) * o
    return left, top, left + bw, top + bh


def crop_center(data: bytes, w: int, h: int) -> bytes:
    return crop_cover(data, w, h, 0.5)


def crop_cover(data: bytes, w: int, h: int, offset: float = 0.5) -> bytes:
    """The picture in `data` scaled to cover w x h and cut to exactly that size (framed by `offset`,
    0.5 = centred), as PNG.
    A camera photo is turned upright first (its EXIF orientation)."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (OSError, Image.DecompressionBombError) as e:
        raise ValueError(f"cannot read the picture: {e}")
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() or "transparency" in img.info else "RGB")
    if img.size != (w, h):
        img = img.resize((w, h), Image.Resampling.LANCZOS, box=cover_box(img.width, img.height, w, h, offset))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()
