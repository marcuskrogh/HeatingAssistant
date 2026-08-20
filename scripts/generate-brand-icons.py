#!/usr/bin/env python3
"""Render Heating Assistant brand PNGs from the shared SVG mark.

Source of truth for the glyph is custom_components/heating_assistant/icon.svg
(written by this script). Badge PNGs are rasterized with cairosvg so strokes
stay clean at 128–512 px.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import cairosvg
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
TEAL_RGB = "#00d4aa"
WORDMARK = (0, 168, 136, 255)

GLYPH_MARK = f"""\
  <line x1="31" y1="52" x2="71" y2="52"
        stroke="{{stroke}}" stroke-width="2.5" stroke-linecap="round" opacity="0.35"/>
  <path d="M 31 72 C 42 72, 45 53.5, 57 52.5 C 63 52, 67 52, 71 52"
        fill="none" stroke="{{stroke}}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="71" cy="52" r="3.6" fill="{{stroke}}"/>
  <path d="M 20 84 L 20 47 L 50 19 L 80 47 L 80 84 Z"
        fill="none" stroke="{{stroke}}" stroke-width="5.5" stroke-linejoin="round" stroke-linecap="round"/>
"""

GLYPH_SVG = f"""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" role="img" aria-label="Heating Assistant">
{GLYPH_MARK.format(stroke=TEAL_RGB)}
</svg>
"""

BADGE_SVG = f"""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect width="100" height="100" rx="22" ry="22" fill="{TEAL_RGB}"/>
  <g transform="translate(14 14) scale(0.72)">
{GLYPH_MARK.format(stroke="#ffffff")}
  </g>
</svg>
"""


def svg_to_png(svg: str, width: int, height: int | None = None) -> Image.Image:
    png = cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        output_width=width,
        output_height=height or width,
    )
    return Image.open(BytesIO(png)).convert("RGBA")


def render_logo(width: int = 500, height: int = 200) -> Image.Image:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    badge_size = int(height * 0.92)
    badge = svg_to_png(BADGE_SVG, badge_size)
    by = (height - badge_size) // 2
    bx = by
    img.alpha_composite(badge, (bx, by))
    font_path = Path("/usr/share/fonts/truetype/macos/Inter-SemiBold.ttf")
    font = ImageFont.truetype(str(font_path), max(18, int(height * 0.28)))
    draw = ImageDraw.Draw(img)
    text_x = bx + badge_size + int(height * 0.12)
    line1, line2 = "Heating", "Assistant"
    box1 = draw.textbbox((0, 0), line1, font=font)
    box2 = draw.textbbox((0, 0), line2, font=font)
    gap = int(height * 0.04)
    block_h = (box1[3] - box1[1]) + gap + (box2[3] - box2[1])
    y0 = (height - block_h) // 2 - box1[1]
    draw.text((text_x, y0), line1, font=font, fill=WORDMARK)
    draw.text((text_x, y0 + (box1[3] - box1[1]) + gap), line2, font=font, fill=WORDMARK)
    return img


def write_png(path: Path, image: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=True)
    print(f"wrote {path.relative_to(ROOT)} ({image.size[0]}×{image.size[1]})")


def main() -> None:
    integration = ROOT / "custom_components" / "heating_assistant"
    svg_path = integration / "icon.svg"
    svg_path.write_text(GLYPH_SVG, encoding="utf-8")
    print(f"wrote {svg_path.relative_to(ROOT)}")

    app_static = ROOT / "heatingassistant" / "app" / "static"
    (app_static / "img").mkdir(parents=True, exist_ok=True)
    (app_static / "img" / "logo.svg").write_text(GLYPH_SVG, encoding="utf-8")
    print("wrote heatingassistant/app/static/img/logo.svg")

    badge_512 = svg_to_png(BADGE_SVG, 512)
    badge_256 = svg_to_png(BADGE_SVG, 256)
    badge_128 = svg_to_png(BADGE_SVG, 128)

    brand = integration / "brand"
    write_png(brand / "icon.png", badge_256)
    write_png(brand / "icon@2x.png", badge_512)
    write_png(brand / "dark_icon.png", badge_256)
    write_png(brand / "dark_icon@2x.png", badge_512)
    write_png(brand / "logo.png", render_logo(512, 205))
    write_png(brand / "logo@2x.png", render_logo(1024, 410))
    write_png(brand / "dark_logo.png", render_logo(512, 205))
    write_png(brand / "dark_logo@2x.png", render_logo(1024, 410))

    brands = ROOT / "brands" / "custom_integrations" / "heating_assistant"
    write_png(brands / "icon.png", badge_256)
    write_png(brands / "icon@2x.png", badge_512)

    app = ROOT / "heating_assistant"
    write_png(app / "icon.png", badge_128)
    write_png(app / "logo.png", render_logo(500, 200))
    write_png(app_static / "img" / "favicon.png", badge_128)


if __name__ == "__main__":
    main()
