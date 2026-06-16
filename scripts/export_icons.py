#!/usr/bin/env python3
"""Export iPS-UU PNG icon sizes from native drawing commands."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets/icons/png"
SIZES = (1024, 512, 256, 128, 64, 32, 16)


def scale(value: float, size: int) -> int:
    return round(value * size / 1024)


def box(coords: tuple[float, float, float, float], size: int) -> tuple[int, int, int, int]:
    return tuple(scale(value, size) for value in coords)  # type: ignore[return-value]


def draw_icon(size: int, mode: str = "main") -> Image.Image:
    mult = 4 if size < 128 else 2
    canvas = size * mult
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def s(value: float) -> int:
        return scale(value, canvas)

    def b(coords: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        return box(coords, canvas)

    if mode == "dark":
        bg = (22, 29, 39, 255)
        ring = (85, 157, 248, 255)
        ring2 = (184, 194, 207, 210)
        chip = (17, 24, 39, 255)
        chip_inner = (32, 41, 54, 255)
        pin = (100, 116, 139, 255)
        phone = (219, 226, 235, 255)
        screen = (11, 17, 25, 255)
        blue = (59, 130, 246, 255)
    elif mode == "mono":
        bg = (247, 248, 250, 255)
        ring = (31, 41, 55, 255)
        ring2 = (107, 114, 128, 255)
        chip = (36, 41, 51, 255)
        chip_inner = (48, 55, 69, 255)
        pin = (123, 132, 146, 255)
        phone = (229, 231, 235, 255)
        screen = (17, 24, 39, 255)
        blue = (75, 85, 99, 255)
    else:
        bg = (226, 232, 240, 255)
        ring = (47, 111, 215, 255)
        ring2 = (83, 96, 113, 220)
        chip = (28, 35, 46, 255)
        chip_inner = (34, 43, 55, 255)
        pin = (89, 100, 116, 255)
        phone = (236, 240, 246, 255)
        screen = (13, 18, 25, 255)
        blue = (47, 111, 215, 255)

    shadow = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(b((268, 279, 756, 767)), radius=s(74), fill=(0, 0, 0, 70))
    shadow = shadow.filter(ImageFilter.GaussianBlur(s(18)))
    image.alpha_composite(shadow, (0, s(18)))

    draw.rounded_rectangle(b((64, 64, 960, 960)), radius=s(214), fill=bg)
    draw.arc(b((138, 124, 884, 870)), start=210, end=326, fill=ring, width=s(42))
    draw.polygon([(s(433), s(133)), (s(417), s(212)), (s(492), s(183))], fill=ring)
    draw.arc(b((138, 168, 874, 910)), start=30, end=150, fill=ring2, width=s(38))
    draw.polygon([(s(596), s(891)), (s(614), s(812)), (s(538), s(839))], fill=ring2)

    draw.rounded_rectangle(b((268, 279, 756, 767)), radius=s(74), fill=chip)
    draw.rounded_rectangle(b((323, 334, 701, 712)), radius=s(34), fill=chip_inner, outline=(120, 131, 149, 255), width=max(1, s(10)))
    for y in (372, 438, 504, 570, 636):
        draw.line((s(214), s(y), s(268), s(y)), fill=pin, width=max(1, s(18)))
        draw.line((s(756), s(y), s(810), s(y)), fill=pin, width=max(1, s(18)))
    for x in (372, 438, 504, 570, 636):
        draw.line((s(x), s(225), s(x), s(279)), fill=pin, width=max(1, s(18)))
        draw.line((s(x), s(767), s(x), s(821)), fill=pin, width=max(1, s(18)))

    draw.rounded_rectangle(b((364, 197, 660, 827)), radius=s(62), fill=phone, outline=(255, 255, 255, 255), width=max(1, s(8)))
    draw.rounded_rectangle(b((397, 256, 627, 764)), radius=s(32), fill=screen)
    draw.rounded_rectangle(b((462, 223, 562, 235)), radius=s(6), fill=(148, 163, 184, 255))
    draw.ellipse(b((498, 779, 526, 807)), fill=(248, 250, 252, 255), outline=(148, 163, 184, 255), width=max(1, s(4)))

    if size >= 64:
        line = (232, 242, 255, 255)
        draw.line((s(451), s(520), s(525), s(520), s(561), s(484), s(561), s(416)), fill=line, width=max(2, s(20)), joint="curve")
        draw.line((s(532), s(406), s(561), s(376), s(591), s(406)), fill=line, width=max(2, s(20)), joint="curve")
        draw.rounded_rectangle(b((430, 560, 594, 656)), radius=s(18), fill=blue)
        draw.rounded_rectangle(b((459, 589, 565, 627)), radius=s(9), fill=(234, 242, 255, 255))
    else:
        draw.rounded_rectangle(b((430, 560, 594, 656)), radius=s(18), fill=blue)

    if mult > 1:
        image = image.resize((size, size), Image.Resampling.LANCZOS)
    return image


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for variant in ("main", "mono", "dark"):
        for size in SIZES:
            draw_icon(size, variant).save(OUT / f"ips-uu-icon-{variant}-{size}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
