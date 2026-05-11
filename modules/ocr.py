"""OCR実行処理です。"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module, util
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

import pytesseract
from PIL import Image

from modules.preprocess import candidate_rotations, preprocess_image


@dataclass
class OCRResult:
    """OCR結果を画面と後続処理で使いやすく持つための型です。"""

    text: str
    confidence: float
    engine: str
    rotation: int


def _confidence_from_data(data: dict[str, list[str]]) -> float:
    scores: list[float] = []
    for raw_score in data.get("conf", []):
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            continue
        if score >= 0:
            scores.append(score)
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 1)


def _paddleocr_available() -> bool:
    return util.find_spec("paddleocr") is not None


def _parse_paddle_result(result: object) -> OCRResult:
    lines: list[str] = []
    confidences: list[float] = []

    if not isinstance(result, list):
        return OCRResult(text="", confidence=0.0, engine="PaddleOCR", rotation=0)

    for page in result:
        if not page:
            continue
        for item in page:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            text_info = item[1]
            if not isinstance(text_info, (list, tuple)) or len(text_info) < 2:
                continue
            text = str(text_info[0]).strip()
            if text:
                lines.append(text)
            try:
                confidences.append(float(text_info[1]) * 100)
            except (TypeError, ValueError):
                continue

    confidence = round(sum(confidences) / len(confidences), 1) if confidences else 0.0
    return OCRResult(text="\n".join(lines), confidence=confidence, engine="PaddleOCR", rotation=0)


def run_paddleocr(image: Image.Image) -> OCRResult:
    """PaddleOCRでOCRします。インストール済みの場合に優先利用します。"""

    paddleocr_module = import_module("paddleocr")
    paddle_ocr = paddleocr_module.PaddleOCR(use_angle_cls=True, lang="japan", show_log=False)
    processed = preprocess_image(image)

    with NamedTemporaryFile(suffix=".png") as temp_file:
        processed.save(temp_file.name)
        result = paddle_ocr.ocr(temp_file.name, cls=True)

    return _parse_paddle_result(result)


def run_tesseract(image: Image.Image, lang: str = "jpn+eng") -> OCRResult:
    """TesseractでOCRします。

    4方向を試し、平均信頼度が一番高い結果を採用します。
    """

    best_result = OCRResult(text="", confidence=0.0, engine="Tesseract", rotation=0)

    for rotation, rotated in candidate_rotations(image):
        processed = preprocess_image(rotated)
        text = pytesseract.image_to_string(processed, lang=lang)
        data = pytesseract.image_to_data(
            processed,
            lang=lang,
            output_type=pytesseract.Output.DICT,
        )
        confidence = _confidence_from_data(data)
        if confidence > best_result.confidence or (not best_result.text and text.strip()):
            best_result = OCRResult(
                text=text.strip(),
                confidence=confidence,
                engine="Tesseract",
                rotation=rotation,
            )

    return best_result


def run_ocr_for_image(image: Image.Image) -> OCRResult:
    """PaddleOCRを優先し、使えない場合はTesseractでOCRします。"""

    if _paddleocr_available():
        try:
            paddle_result = run_paddleocr(image)
            if paddle_result.text.strip():
                return paddle_result
        except Exception:
            # PaddleOCRはOSや依存関係で失敗することがあるため、MVPではTesseractへ切り替えます。
            pass

    return run_tesseract(image)


def pdf_to_images(pdf_bytes: bytes) -> list[Image.Image]:
    """PDFを画像へ変換します。

    pdf2imageが使える環境ではそれを使います。
    Poppler未導入などで失敗した場合は、わかりやすいエラーを返します。
    """

    if util.find_spec("pdf2image") is None:
        raise RuntimeError("PDFを読むには pdf2image が必要です。")

    pdf2image_module = import_module("pdf2image")
    try:
        return pdf2image_module.convert_from_bytes(pdf_bytes, dpi=250)
    except Exception as exc:  # noqa: BLE001 - 環境依存エラーを利用者向け文に変換します。
        raise RuntimeError(
            "PDFを画像に変換できませんでした。WindowsではPopplerの追加インストールが必要な場合があります。"
        ) from exc


def images_from_upload(file_name: str, file_bytes: bytes) -> list[Image.Image]:
    """アップロードされた画像/PDFをPIL画像の一覧にします。"""

    suffix = Path(file_name).suffix.lower()
    if suffix == ".pdf":
        return pdf_to_images(file_bytes)

    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir) / file_name
        temp_path.write_bytes(file_bytes)
        with Image.open(temp_path) as image:
            return [image.copy()]


def run_ocr_for_upload(file_name: str, file_bytes: bytes) -> OCRResult:
    """アップロードファイル全体をOCRします。"""

    images = images_from_upload(file_name, file_bytes)
    results = [run_ocr_for_image(image) for image in images]

    combined_text = "\n\n".join(result.text for result in results if result.text)
    confidences = [result.confidence for result in results if result.confidence > 0]
    average_confidence = round(sum(confidences) / len(confidences), 1) if confidences else 0.0
    engines = ", ".join(sorted({result.engine for result in results}))

    return OCRResult(
        text=combined_text.strip(),
        confidence=average_confidence,
        engine=engines,
        rotation=0 if len(results) == 1 else -1,
    )
