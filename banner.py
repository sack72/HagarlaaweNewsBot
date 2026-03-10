"""
banner.py — Visual Banner Generator for Hagarlaawe News Bot

Generates clean, professional PNG banners for Telegram and Facebook.
Each banner uses category-specific colors and centered text.
"""

import os
import logging
from PIL import Image, ImageDraw, ImageFont

# Banner dimensions (optimized for Telegram/Facebook)
BANNER_WIDTH = 1200
BANNER_HEIGHT = 400

# Font paths (try system fonts, fallback to default)
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    """Try to load a bold system font, fall back to default."""
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    # Fallback: Pillow's built-in font (small but works)
    return ImageFont.load_default()


def generate_banner(
    text: str,
    bg_color: tuple = (30, 80, 160),
    text_color: tuple = (255, 255, 255),
    output_path: str = "/tmp/banner_latest.png",
    width: int = BANNER_WIDTH,
    height: int = BANNER_HEIGHT,
) -> str:
    """
    Generate a professional banner image.

    Args:
        text: Banner text (e.g. "FED POLICY UPDATE")
        bg_color: RGB tuple for background
        text_color: RGB tuple for text
        output_path: Where to save the PNG
        width: Image width in pixels
        height: Image height in pixels

    Returns:
        Path to the generated image file.
    """
    try:
        # Create canvas
        img = Image.new("RGB", (width, height), color=bg_color)
        draw = ImageDraw.Draw(img)

        # --- Subtle gradient overlay ---
        for y in range(height):
            alpha = int(40 * (y / height))  # Darken bottom slightly
            draw.line(
                [(0, y), (width, y)],
                fill=(
                    max(bg_color[0] - alpha, 0),
                    max(bg_color[1] - alpha, 0),
                    max(bg_color[2] - alpha, 0),
                )
            )

        # --- Decorative top and bottom bars ---
        bar_height = 6
        accent_color = (
            min(bg_color[0] + 60, 255),
            min(bg_color[1] + 60, 255),
            min(bg_color[2] + 60, 255),
        )
        draw.rectangle([0, 0, width, bar_height], fill=accent_color)
        draw.rectangle([0, height - bar_height, width, height], fill=accent_color)

        # --- Main text ---
        # Auto-size: start large and reduce until it fits
        font_size = 72
        font = _get_font(font_size)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]

        while text_w > width - 120 and font_size > 28:
            font_size -= 4
            font = _get_font(font_size)
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]

        text_h = bbox[3] - bbox[1]
        x = (width - text_w) // 2
        y = (height - text_h) // 2 - 10

        # Drop shadow
        shadow_offset = 3
        draw.text(
            (x + shadow_offset, y + shadow_offset),
            text,
            font=font,
            fill=(0, 0, 0, 80)
        )

        # Main text
        draw.text((x, y), text, font=font, fill=text_color)

        # --- Thin separator line under text ---
        line_y = y + text_h + 20
        line_margin = width // 4
        draw.line(
            [(line_margin, line_y), (width - line_margin, line_y)],
            fill=accent_color,
            width=2
        )

        # --- Branding watermark ---
        brand_text = "HMM NEWS"
        brand_font = _get_font(18)
        brand_bbox = draw.textbbox((0, 0), brand_text, font=brand_font)
        brand_w = brand_bbox[2] - brand_bbox[0]
        draw.text(
            ((width - brand_w) // 2, height - 40),
            brand_text,
            font=brand_font,
            fill=(*accent_color, 180)
        )

        # Save
        img.save(output_path, "PNG", quality=95)
        logging.info(f"✅ Banner generated: {output_path}")
        return output_path

    except Exception as e:
        logging.error(f"❌ Banner generation failed: {e}")
        return ""


# --- CLI test ---
if __name__ == "__main__":
    # Quick test: generate one of each category
    test_cases = [
        ("ECONOMIC DATA UPDATE", (30, 80, 160)),
        ("FED POLICY UPDATE", (140, 20, 20)),
        ("IRAN WAR UPDATE", (180, 90, 20)),
        ("GEOPOLITICAL NEWS", (90, 50, 140)),
        ("GLOBAL MARKET UPDATE", (100, 100, 100)),
    ]
    for i, (txt, col) in enumerate(test_cases):
        path = generate_banner(txt, bg_color=col, output_path=f"/tmp/banner_test_{i}.png")
        print(f"Generated: {path}")
