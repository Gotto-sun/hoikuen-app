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


SECTION_LABELS = ["午前おやつ", "昼食", "午後おやつ"]
FALLBACK_MENU_TABLE_AREAS = {
    "午前おやつ": (0.04, 0.18, 0.92, 0.16),
    "昼食": (0.04, 0.34, 0.92, 0.36),
    "午後おやつ": (0.04, 0.70, 0.92, 0.18),
}
TABLE_X_RATIO = 0.04
TABLE_WIDTH_RATIO = 0.92
SECTION_BOTTOM_RATIO = 0.92
MIN_SECTION_HEIGHT_RATIO = 0.08
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


@dataclass(frozen=True)
class SectionArea:
    """見出しごとに分割したOCR対象エリアです。"""

    label: str
    box: tuple[int, int, int, int]
    source: str


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


def _compact_heading_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _heading_matches(text: str, label: str) -> bool:
    compact = _compact_heading_text(text)
    if label in compact:
        return True
    if label == "午前おやつ":
        return "午前" in compact and "やつ" in compact
    if label == "午後おやつ":
        return "午後" in compact and "やつ" in compact
    return label == "昼食" and "昼" in compact and "食" in compact


def _find_heading_boxes(image: Image.Image) -> dict[str, tuple[int, int, int, int]]:
    """ページ内の3見出し位置だけを探します。食材本文の全文OCRには使いません。"""

    processed = preprocess_image(image)
    data = pytesseract.image_to_data(
        processed,
        lang="jpn+eng",
        config="--oem 3 --psm 6",
        output_type=pytesseract.Output.DICT,
    )
    found: dict[str, tuple[int, int, int, int]] = {}
    count = len(data.get("text", []))

    for index in range(count):
        words: list[str] = []
        lefts: list[int] = []
        tops: list[int] = []
        rights: list[int] = []
        bottoms: list[int] = []
        for offset in range(4):
            current = index + offset
            if current >= count:
                break
            text = str(data["text"][current] or "").strip()
            if not text:
                continue
            words.append(text)
            left = int(data["left"][current])
            top = int(data["top"][current])
            width = int(data["width"][current])
            height = int(data["height"][current])
            lefts.append(left)
            tops.append(top)
            rights.append(left + width)
            bottoms.append(top + height)
            joined = "".join(words)
            for label in SECTION_LABELS:
                if label not in found and _heading_matches(joined, label):
                    found[label] = (min(lefts), min(tops), max(rights), max(bottoms))
    return found


def _fallback_section_area(image: Image.Image, label: str) -> SectionArea:
    return SectionArea(
        label=label,
        box=_ratio_crop_box(image, *FALLBACK_MENU_TABLE_AREAS[label]),
        source="固定位置",
    )


def _section_areas_from_headings(image: Image.Image) -> list[SectionArea]:
    heading_boxes = _find_heading_boxes(image)
    if not heading_boxes:
        return [_fallback_section_area(image, label) for label in SECTION_LABELS]

    areas: list[SectionArea] = []
    sorted_headings = sorted(
        ((label, heading_boxes[label]) for label in SECTION_LABELS if label in heading_boxes),
        key=lambda item: item[1][1],
    )
    heading_by_label = dict(sorted_headings)

    for index, label in enumerate(SECTION_LABELS):
        fallback = _fallback_section_area(image, label)
        heading = heading_by_label.get(label)
        if heading is None:
            areas.append(fallback)
            continue

        next_tops = [box[1] for other_label, box in sorted_headings if box[1] > heading[1] and other_label != label]
        table_top = min(image.height - 1, heading[3] + max(2, round(image.height * 0.004)))
        table_bottom = (
            min(next_tops) - max(2, round(image.height * 0.004))
            if next_tops
            else round(image.height * SECTION_BOTTOM_RATIO)
        )
        min_height = round(image.height * MIN_SECTION_HEIGHT_RATIO)
        if table_bottom - table_top < min_height:
            areas.append(fallback)
            continue

        left = round(image.width * TABLE_X_RATIO)
        right = round(image.width * (TABLE_X_RATIO + TABLE_WIDTH_RATIO))
        areas.append(
            SectionArea(
                label=label,
                box=(max(0, left), max(0, table_top), min(image.width, right), min(image.height, table_bottom)),
                source="見出し検出",
            )
        )

    return areas


def run_fixed_layout_ocr(image: Image.Image) -> OCRResult:
    """見出しごとのエリア内で、食材名列と3歳未満列だけをOCRします。"""

    source = ImageOps.exif_transpose(image).convert("L")
    section_areas = _section_areas_from_headings(source)
    lines: list[str] = []
    confidences: list[float] = []

    for area in section_areas:
        table = source.crop(area.box)
        row_ranges = _detect_table_row_ranges(table)
        if not row_ranges:
            lines.append(f"区分	{area.label}	検出行なし	{area.source}")
            continue

        section_row_count = 0
        lines.append(f"区分	{area.label}	{area.source}	{len(row_ranges)}行")
        for top, bottom in row_ranges:
            row_top = area.box[1] + top
            row_bottom = area.box[1] + bottom
            if row_bottom <= row_top:
                continue

            name_box = _fixed_cell_box(area.box, row_top, row_bottom, "food_name")
            under_three_box = _fixed_cell_box(area.box, row_top, row_bottom, "under_three")
            name_text, name_confidence = _ocr_fixed_cell(source.crop(name_box), "jpn+eng")
            quantity_text, quantity_confidence = _ocr_fixed_cell(source.crop(under_three_box), "eng")
            food_name = " ".join(name_text.split())
            quantity = _quantity_from_under_three_text(quantity_text)
            if name_confidence > 0:
                confidences.append(name_confidence)
            if quantity_confidence > 0:
                confidences.append(quantity_confidence)
            if food_name:
                section_row_count += 1
                lines.append(
                    f"{FIXED_ROW_MARKER}	{area.label}	{food_name}	{quantity or '数量要確認'}	g"
                )

        if section_row_count == 0:
            lines.append(f"{FIXED_ROW_MARKER}	{area.label}	食材名要確認	数量要確認	g")

    average_confidence = round(sum(confidences) / len(confidences), 1) if confidences else 0.0
    return OCRResult(
        text="\n".join(lines),
        confidence=average_confidence,
        engine="Tesseract見出し分割・固定X座標OCR",
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
