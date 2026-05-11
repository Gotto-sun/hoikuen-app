"""OCR実行処理です。"""

from __future__ import annotations

from dataclasses import dataclass
import re
from importlib import import_module, util
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

import pytesseract
from PIL import Image, ImageOps

from modules.preprocess import candidate_rotations, preprocess_image


FIXED_MENU_TABLE_AREAS = [
    ("午前おやつ", 0.04, 0.18, 0.92, 0.16),
    ("昼食", 0.04, 0.34, 0.92, 0.36),
    ("午後おやつ", 0.04, 0.70, 0.92, 0.18),
]
FIXED_MENU_COLUMNS = {
    "food_name": (0.00, 0.30),
    "total": (0.30, 0.48),
    "over_three": (0.48, 0.64),
    "under_three": (0.64, 0.80),
    "staff": (0.80, 1.00),
}
FIXED_ROW_MARKER = "固定表行"
FIXED_OCR_DPI = 300
NUMBER_RE = re.compile(r"(?<![0-9.])[0-9]+(?:\.[0-9]+)?(?![0-9.])")


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


def _ratio_crop_box(image: Image.Image, x: float, y: float, width: float, height: float) -> tuple[int, int, int, int]:
    left = round(image.width * x)
    top = round(image.height * y)
    right = round(image.width * (x + width))
    bottom = round(image.height * (y + height))
    return max(0, left), max(0, top), min(image.width, right), min(image.height, bottom)


def _group_line_positions(values: list[int]) -> list[int]:
    groups: list[list[int]] = []
    for value in values:
        if not groups or value - groups[-1][-1] > 2:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [round(sum(group) / len(group)) for group in groups]


def _detect_table_row_ranges(table: Image.Image) -> list[tuple[int, int]]:
    gray = ImageOps.grayscale(table)
    pixels = gray.load()
    line_positions: list[int] = []
    for y in range(gray.height):
        dark = 0
        for x in range(gray.width):
            if pixels[x, y] < 105:
                dark += 1
        if dark / max(1, gray.width) > 0.32:
            line_positions.append(y)

    grouped = _group_line_positions(line_positions)
    ranges: list[tuple[int, int]] = []
    for start, end in zip(grouped, grouped[1:]):
        top = start + 2
        bottom = end - 2
        if bottom - top >= max(10, int(gray.height * 0.035)):
            ranges.append((top, bottom))
    if ranges:
        return ranges

    fallback_count = max(1, round(gray.height / 38))
    return [
        (round(gray.height * index / fallback_count), round(gray.height * (index + 1) / fallback_count))
        for index in range(fallback_count)
    ]


def _fixed_cell_box(
    table_box: tuple[int, int, int, int],
    row_top: int,
    row_bottom: int,
    column_name: str,
) -> tuple[int, int, int, int]:
    left_ratio, right_ratio = FIXED_MENU_COLUMNS[column_name]
    table_width = table_box[2] - table_box[0]
    left = table_box[0] + round(table_width * left_ratio)
    right = table_box[0] + round(table_width * right_ratio)
    x_padding = max(1, round(table_width * 0.004))
    y_padding = max(1, round((row_bottom - row_top) * 0.08))
    return (
        min(right - 1, left + x_padding),
        min(row_bottom - 1, row_top + y_padding),
        max(left + 1, right - x_padding),
        max(row_top + 1, row_bottom - y_padding),
    )


def _ocr_fixed_cell(image: Image.Image, lang: str) -> tuple[str, float]:
    cell = ImageOps.grayscale(image)
    cell = cell.resize((max(1, cell.width * 3), max(1, cell.height * 3)), Image.Resampling.LANCZOS)
    processed = preprocess_image(cell)
    config = f"--oem 3 --psm 7 --dpi {FIXED_OCR_DPI}"
    text = pytesseract.image_to_string(processed, lang=lang, config=config)
    data = pytesseract.image_to_data(
        processed,
        lang=lang,
        config=config,
        output_type=pytesseract.Output.DICT,
    )
    return text.strip(), _confidence_from_data(data)


def _quantity_from_under_three_text(text: str) -> str:
    normalized = str(text or "").translate(str.maketrans("０１２３４５６７８９，．", "0123456789,."))
    normalized = normalized.replace(",", "")
    match = NUMBER_RE.search(normalized)
    return match.group(0) if match else ""


def run_fixed_layout_ocr(image: Image.Image) -> OCRResult:
    """固定X座標の食材名列と3歳未満列だけを別々にOCRします。"""

    source = ImageOps.exif_transpose(image).convert("L")
    lines: list[str] = []
    confidences: list[float] = []

    for area_label, x_ratio, y_ratio, width_ratio, height_ratio in FIXED_MENU_TABLE_AREAS:
        table_box = _ratio_crop_box(source, x_ratio, y_ratio, width_ratio, height_ratio)
        table = source.crop(table_box)
        for top, bottom in _detect_table_row_ranges(table):
            row_top = table_box[1] + top
            row_bottom = table_box[1] + bottom
            if row_bottom <= row_top:
                continue

            name_box = _fixed_cell_box(table_box, row_top, row_bottom, "food_name")
            under_three_box = _fixed_cell_box(table_box, row_top, row_bottom, "under_three")
            name_text, name_confidence = _ocr_fixed_cell(source.crop(name_box), "jpn+eng")
            quantity_text, quantity_confidence = _ocr_fixed_cell(source.crop(under_three_box), "eng")
            food_name = " ".join(name_text.split())
            quantity = _quantity_from_under_three_text(quantity_text)
            if name_confidence > 0:
                confidences.append(name_confidence)
            if quantity_confidence > 0:
                confidences.append(quantity_confidence)
            if food_name:
                lines.append(
                    f"{FIXED_ROW_MARKER}\t{area_label}\t{food_name}\t{quantity or '数量要確認'}\tg"
                )

    average_confidence = round(sum(confidences) / len(confidences), 1) if confidences else 0.0
    return OCRResult(
        text="\n".join(lines),
        confidence=average_confidence,
        engine="Tesseract固定X座標OCR",
        rotation=0,
    )


def run_ocr_for_image(image: Image.Image) -> OCRResult:
    """全文OCRを使わず、固定X座標の列切り出しOCRだけを実行します。"""

    return run_fixed_layout_ocr(image)


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
