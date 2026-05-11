"""OCR前の画像補正処理です。"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageOps


def preprocess_image(image: Image.Image) -> Image.Image:
    """OCRしやすいように画像を最小限補正します。

    MVPでは安全で軽い補正に絞ります。
    - EXIF向き補正
    - グレースケール化
    - コントラスト強化
    - ノイズ除去
    - 白黒化
    """

    corrected = ImageOps.exif_transpose(image).convert("L")
    corrected = ImageEnhance.Contrast(corrected).enhance(1.6)

    array = np.array(corrected)
    denoised = cv2.medianBlur(array, 3)
    thresholded = cv2.adaptiveThreshold(
        denoised,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        11,
    )
    return Image.fromarray(thresholded)


def candidate_rotations(image: Image.Image) -> list[tuple[int, Image.Image]]:
    """0/90/180/270度の候補画像を返します。"""

    return [
        (0, image),
        (90, image.rotate(90, expand=True)),
        (180, image.rotate(180, expand=True)),
        (270, image.rotate(270, expand=True)),
    ]
