"""OCR前の画像補正処理です。"""

from __future__ import annotations

from PIL import Image, ImageOps


def preprocess_image(image: Image.Image) -> Image.Image:
    """OCR前処理はグレースケール化だけにします。

    二値化・ノイズ除去・コントラスト強化で白飛び/真っ黒になる可能性を避けるため、
    いったんOFFにしています。
    """

    return ImageOps.exif_transpose(image).convert("L")


def candidate_rotations(image: Image.Image) -> list[tuple[int, Image.Image]]:
    """0/90/180/270度の候補画像を返します。"""

    return [
        (0, image),
        (90, image.rotate(90, expand=True)),
        (180, image.rotate(180, expand=True)),
        (270, image.rotate(270, expand=True)),
    ]
