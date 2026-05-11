"""OCR前の画像補正処理です。"""

from __future__ import annotations

from PIL import Image, ImageOps


def preprocess_image(image: Image.Image) -> Image.Image:
    """OCR前処理は一旦OFFにし、PillowでRGB化した原画像を返します。"""

    return ImageOps.exif_transpose(image).convert("RGB")


def candidate_rotations(image: Image.Image) -> list[tuple[int, Image.Image]]:
    """回転補正は一旦OFFにし、0度の原画像だけを返します。"""

    return [(0, image)]
