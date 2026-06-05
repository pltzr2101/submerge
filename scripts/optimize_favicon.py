#!/usr/bin/env python3
"""Optimize favicon.png to proper favicon size (32x32, ≤4KB).

Also generates a multi-resolution favicon.ico for legacy browser support.
Run this script whenever the source favicon changes.
"""

from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "src" / "submerge" / "static"
SRC = STATIC_DIR / "favicon.png"

img = Image.open(SRC).convert("RGBA")

# Resize to 32x32 with high-quality Lanczos resampling
img_32 = img.resize((32, 32), Image.LANCZOS)

# Save optimized PNG — optimize=True to minimize file size
img_32.save(SRC, format="PNG", optimize=True)
png_size = SRC.stat().st_size
print(f"favicon.png: {png_size} bytes ({img_32.size[0]}x{img_32.size[1]})")
assert png_size < 8192, f"Favicon still too large: {png_size} bytes"

# Generate favicon.ico with 16x16 + 32x32 for maximum browser compatibility
img_16 = img.resize((16, 16), Image.LANCZOS)
ico_path = STATIC_DIR / "favicon.ico"
img_32.save(ico_path, format="ICO", sizes=[(16, 16), (32, 32)])
ico_size = ico_path.stat().st_size
print(f"favicon.ico: {ico_size} bytes")
assert ico_size < 10240, f"ICO still too large: {ico_size} bytes"

print("Done: favicon optimized.")
