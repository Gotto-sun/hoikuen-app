"""OCR実行処理です。"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
import re
from importlib import import_module, util
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytesseract
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageSequence, ImageStat



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
# 料理ブロック内の横罫線行だけを読みます。
# 左40%は食材名、右60%は数値列として固定し、右から2番目を3歳未満列にします。
FIXED_MENU_COLUMNS = {
    "food_name": (0.00, 0.40),
    "total": (0.40, 0.55),
    "over_three": (0.55, 0.70),
    "under_three": (0.70, 0.85),
    "staff": (0.85, 1.00),
}
DEBUG_TARGET_COLUMNS = ("food_name", "under_three")
DEBUG_COLUMN_LABELS = {
    "food_name": "食材名",
    "under_three": "3歳未満",
}
DEBUG_COLUMN_COLORS = {
    "食材名": (0, 90, 255),
    "3歳未満": (255, 0, 0),
}
SECTION_DEBUG_COLORS = {
    "午前おやつ": (0, 150, 255),
    "昼食": (0, 190, 90),
    "午後おやつ": (170, 80, 255),
}
FIXED_ROW_MARKER = "固定表行"
FIXED_OCR_DPI = 300
MAX_OCR_IMAGE_EDGE = 30000
MAX_OCR_IMAGE_PIXELS = 250_000_000
logger = logging.getLogger(__name__)

NUMBER_RE = re.compile(r"(?<![0-9.])[0-9]+(?:\.[0-9]+)?(?![0-9.])")
FORCED_INGREDIENT_NAMES = (
    "牛乳",
    "ひじき",
    "豚ひき肉",
    "木綿豆腐",
    "たまねぎ",
    "もやし",
    "きゅうり",
    "カットわかめ",
    "じゃがいも",
    "にんじん",
    "食パン",
    "いちごジャム",
    "キャベツ",
    "ほうれんそう",
    "しめじ",
    "中華めん",
    "豚肉(もも)",
    "はくさい",
    "たけのこ",
    "かまぼこ",
    "ブロッコリー",
    "クリームコーン缶",
    "豆乳",
    "オレンジ濃縮果汁",
    "粉かんてん",
    "みかん缶",
    "SBカレーフレーク",
    "だいこん",
    "ツナ油漬",
    "パイン缶",
    "スパゲティ",
    "パイシート(冷凍)",
    "チーズ",
    "ヨーグルト",
    "調整豆乳",
    "鮭(皮なし)",
    "さつまいも",
    "ベーコン",
    "えのきたけ",
    "わかめふりかけ",
    "さわら",
    "バター",
    "ちくわ",
    "グリーンアスパラガス",
    "ウインナーソーセージ",
    "鶏ひき肉",
    "パプリカ(赤)",
    "しらす干し",
    "チンゲンサイ",
    "オレンジ",
    "マカロニ",
    "きな粉",
    "油揚げ",
    "かぼちゃ",
    "ごぼう",
    "フライドポテト",
    "なめこ",
)


def _assert_valid_ocr_image(image: Image.Image, context: str) -> None:
    """OCR前に画像サイズを必ずログへ出し、異常サイズなら止めます。"""

    width, height = image.size
    logger.info("OCR前画像サイズ: context=%s mode=%s width=%s height=%s", context, image.mode, width, height)
    if width <= 0 or height <= 0:
        logger.error("OCR停止: 画像サイズが0です context=%s width=%s height=%s", context, width, height)
        raise RuntimeError(f"OCR停止: 画像サイズが異常です（{context}: {width}x{height}）")
    if width > MAX_OCR_IMAGE_EDGE or height > MAX_OCR_IMAGE_EDGE or width * height > MAX_OCR_IMAGE_PIXELS:
        logger.error("OCR停止: 画像サイズが大きすぎます context=%s width=%s height=%s", context, width, height)
        raise RuntimeError(f"OCR停止: 画像サイズが大きすぎます（{context}: {width}x{height}）")


def _image_blankness_message(image: Image.Image, context: str) -> str:
    """白飛び/真っ黒/空白の疑いをログと画面表示用メッセージにします。"""

    gray = ImageOps.grayscale(image)
    stat = ImageStat.Stat(gray)
    mean = stat.mean[0]
    stddev = stat.stddev[0]
    min_value, max_value = gray.getextrema()
    message = (
        f"{context}: {gray.width}x{gray.height} / 平均 {mean:.1f} / "
        f"ばらつき {stddev:.1f} / 最小 {min_value} / 最大 {max_value}"
    )
    logger.info("OCR前画像状態: %s", message)
    if stddev < 1.0 or min_value == max_value:
        logger.warning("OCR前画像が空白の可能性があります: %s", message)
        return f"⚠️ 空白の可能性あり: {message}"
    if mean > 252 and stddev < 8:
        logger.warning("OCR前画像が白飛びの可能性があります: %s", message)
        return f"⚠️ 白飛びの可能性あり: {message}"
    if mean < 3 and stddev < 8:
        logger.warning("OCR前画像が真っ黒の可能性があります: %s", message)
        return f"⚠️ 真っ黒の可能性あり: {message}"
    return f"OK: {message}"


def _preprocess_for_ocr(image: Image.Image, context: str) -> Image.Image:
    """前処理・二値化・トリミング・表分割を一旦OFFにし、原画像RGBだけをOCRへ渡します。"""

    processed = ImageOps.exif_transpose(image).convert("RGB")
    _assert_valid_ocr_image(processed, f"{context}（前処理OFF）")
    _image_blankness_message(processed, f"{context}（前処理OFF）")
    return processed


def _preprocess_image(image: Image.Image) -> Image.Image:
    try:
        from modules.preprocess import preprocess_image
    except Exception:  # noqa: BLE001 - デバッグ表示は前処理なしでも動かします。
        return ImageOps.exif_transpose(image).convert("RGB")
    return preprocess_image(image)


def _candidate_rotations(image: Image.Image):
    try:
        from modules.preprocess import candidate_rotations
    except Exception:  # noqa: BLE001 - 前処理依存が使えない環境では回転なしで進めます。
        return [(0, image)]
    return candidate_rotations(image)


@dataclass
class OCRResult:
    """OCR結果を画面と後続処理で使いやすく持つための型です。"""

    text: str
    confidence: float
    engine: str
    rotation: int


@dataclass(frozen=True)
class RawOCRPageResult:
    """原画像OCR確認モードで表示するページ単位の結果です。"""

    page_number: int
    original_image: Image.Image
    ocr_text: str
    ocr_confidence: float
    diagnostics: list[str]


@dataclass(frozen=True)
class SectionArea:
    """見出しごとに分割したOCR対象エリアです。"""

    label: str
    box: tuple[int, int, int, int]
    source: str


@dataclass(frozen=True)
class DebugBox:
    """画面確認用に表示する切り出し枠です。"""

    section: str
    kind: str
    label: str
    box: tuple[int, int, int, int]
    source: str


@dataclass(frozen=True)
class DebugCropResult:
    """OCR位置確認用に切り出した小画像と読み取り結果です。"""

    section: str
    label: str
    box: tuple[int, int, int, int]
    image: Image.Image
    processed_image: Image.Image
    ocr_text: str
    confidence: float
    diagnostics: list[str]


@dataclass(frozen=True)
class DebugOverlayResult:
    """切り出し枠を描画したデバッグ画像です。"""

    page_number: int
    image: Image.Image
    original_image: Image.Image
    preprocessed_image: Image.Image
    original_ocr_text: str
    original_ocr_confidence: float
    diagnostics: list[str]
    boxes: list[DebugBox]
    crops: list[DebugCropResult]


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
    _assert_valid_ocr_image(image, "PaddleOCR原画像")
    processed = _preprocess_for_ocr(image, "PaddleOCR")

    with NamedTemporaryFile(suffix=".png") as temp_file:
        processed.save(temp_file.name)
        result = paddle_ocr.ocr(temp_file.name, cls=True)

    return _parse_paddle_result(result)


def run_tesseract(image: Image.Image, lang: str = "jpn+eng") -> OCRResult:
    """Tesseractで原画像全体をOCRします。回転補正・前処理は一旦OFFです。"""

    _assert_valid_ocr_image(image, "Tesseract入力画像")
    processed = _preprocess_for_ocr(image, "Tesseract原画像全体")
    text = pytesseract.image_to_string(processed, lang=lang)
    data = pytesseract.image_to_data(
        processed,
        lang=lang,
        output_type=pytesseract.Output.DICT,
    )
    return OCRResult(
        text=text.strip(),
        confidence=_confidence_from_data(data),
        engine="Tesseract原画像全体",
        rotation=0,
    )


def run_raw_pillow_rgb_ocr(image: Image.Image, lang: str = "jpn+eng", context: str = "原画像そのままOCR") -> OCRResult:
    """Pillowで読み込んだ原画像をRGB化し、そのままTesseractへ渡します。"""

    rgb = ImageOps.exif_transpose(image).convert("RGB")
    _assert_valid_ocr_image(rgb, context)
    _image_blankness_message(rgb, context)
    config = f"--oem 3 --psm 6 --dpi {FIXED_OCR_DPI}"
    text = pytesseract.image_to_string(rgb, lang=lang, config=config).strip()
    data = pytesseract.image_to_data(
        rgb,
        lang=lang,
        config=config,
        output_type=pytesseract.Output.DICT,
    )
    confidence = _confidence_from_data(data)
    logger.info("OCR全文 START: context=%s engine=Tesseract原画像RGB confidence=%s", context, confidence)
    logger.info("%s", text or "（空）")
    logger.info("OCR全文 END: context=%s", context)
    return OCRResult(
        text=text,
        confidence=confidence,
        engine="Tesseract原画像RGB",
        rotation=0,
    )

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
    # 横罫線で上下が区切られている行だけを食材行の候補にします。
    # 罫線が見つからない場合の等分割は、料理名や文章を拾う原因になるため行いません。
    return ranges


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


def _fixed_column_box(table_box: tuple[int, int, int, int], column_name: str) -> tuple[int, int, int, int]:
    left_ratio, right_ratio = FIXED_MENU_COLUMNS[column_name]
    table_width = table_box[2] - table_box[0]
    left = table_box[0] + round(table_width * left_ratio)
    right = table_box[0] + round(table_width * right_ratio)
    return left, table_box[1], right, table_box[3]


def _ocr_fixed_cell(image: Image.Image, lang: str) -> tuple[str, float]:
    processed = _preprocess_for_ocr(image, "固定セルOCR")
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



def _is_recipe_note_or_instruction(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact or compact.startswith("※"):
        return True
    if re.fullmatch(r"[0-9０-９]+[.)）．、]?", compact):
        return True
    return bool(
        re.search(
            r"作り方|つくり方|手順|注釈|調理方法|下処理|切る|切って|切り|ゆでる|茹でる|煮る|焼く|炒める|蒸す|揚げる|混ぜる|和える|加える|入れる|のせる|盛る|塗る|してください|します|です",
            compact,
        )
    )


def _is_recipe_title_row(food_name: str, quantity: str) -> bool:
    compact = re.sub(r"\s+", "", str(food_name or ""))
    if quantity or _is_recipe_note_or_instruction(compact):
        return False
    if NUMBER_RE.search(compact):
        return False
    return bool(re.search(r"[ぁ-んァ-ヶ一-龯々〆〇]{2,}", compact))


def _is_ingredient_list_row(food_name: str, quantity: str) -> bool:
    compact = re.sub(r"\s+", "", str(food_name or ""))
    if not compact or not quantity:
        return False
    if _is_recipe_note_or_instruction(compact):
        return False
    if NUMBER_RE.search(compact):
        return False
    return bool(re.search(r"[ぁ-んァ-ヶ一-龯々〆〇]{2,}", compact))


def _is_forced_ingredient_name(food_name: str) -> bool:
    compact = re.sub(r"\s+", "", str(food_name or ""))
    return any(name in compact for name in FORCED_INGREDIENT_NAMES)


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

    _assert_valid_ocr_image(image, "見出し検出入力")
    data = pytesseract.image_to_data(
        image,
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
    try:
        heading_boxes = _find_heading_boxes(image)
    except Exception:  # noqa: BLE001 - 見出し検出に失敗しても固定位置の枠は表示します。
        heading_boxes = {}
    if not heading_boxes:
        return [_fallback_section_area(image, label) for label in SECTION_LABELS]

    areas: list[SectionArea] = []
    sorted_headings = sorted(
        ((label, heading_boxes[label]) for label in SECTION_LABELS if label in heading_boxes),
        key=lambda item: item[1][1],
    )
    heading_by_label = dict(sorted_headings)

    for label in SECTION_LABELS:
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


def _debug_boxes_for_image(image: Image.Image) -> list[DebugBox]:
    source = ImageOps.exif_transpose(image).convert("RGB")
    section_areas = _section_areas_from_headings(source)
    boxes: list[DebugBox] = []
    for area in section_areas:
        boxes.append(DebugBox(section=area.label, kind="section", label=area.label, box=area.box, source=area.source))
        for column_name in DEBUG_TARGET_COLUMNS:
            boxes.append(
                DebugBox(
                    section=area.label,
                    kind="column",
                    label=DEBUG_COLUMN_LABELS[column_name],
                    box=_fixed_column_box(area.box, column_name),
                    source=area.source,
                )
            )
    return boxes


def _draw_text_label(draw: ImageDraw.ImageDraw, position: tuple[int, int], text: str, fill: tuple[int, int, int]) -> None:
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 16)
    except OSError:
        font = ImageFont.load_default()
    x, y = position
    text_box = draw.textbbox((x, y), text, font=font)
    background = (255, 255, 255)
    draw.rectangle((text_box[0] - 2, text_box[1] - 2, text_box[2] + 2, text_box[3] + 2), fill=background)
    draw.text((x, y), text, fill=fill, font=font)


def _ocr_debug_crop(image: Image.Image, label: str) -> tuple[str, float, Image.Image, list[str]]:
    diagnostics: list[str] = []
    try:
        _assert_valid_ocr_image(image, f"切り出しOCR {label} 原画像")
        diagnostics.append(_image_blankness_message(image, f"切り出しOCR {label} 原画像"))
        processed = _preprocess_for_ocr(image, f"切り出しOCR {label}")
        diagnostics.append(_image_blankness_message(processed, f"切り出しOCR {label} 前処理OFF"))
    except Exception as exc:  # noqa: BLE001 - デバッグ画面にOCR失敗を表示します。
        fallback = Image.new("L", (1, 1), 255)
        return f"OCRエラー: {exc}", 0.0, fallback, diagnostics

    lang = "eng" if label == "3歳未満" else "jpn+eng"
    config = f"--oem 3 --psm 6 --dpi {FIXED_OCR_DPI}"
    try:
        text = pytesseract.image_to_string(processed, lang=lang, config=config).strip()
        data = pytesseract.image_to_data(
            processed,
            lang=lang,
            config=config,
            output_type=pytesseract.Output.DICT,
        )
    except Exception as exc:  # noqa: BLE001 - デバッグ画面にOCR失敗を表示します。
        return f"OCRエラー: {exc}", 0.0, processed, diagnostics
    return text, _confidence_from_data(data), processed, diagnostics


def _debug_crops_for_image(image: Image.Image, boxes: list[DebugBox]) -> list[DebugCropResult]:
    crops: list[DebugCropResult] = []
    for box in boxes:
        if box.kind != "column":
            continue
        crop = image.crop(box.box)
        ocr_text, confidence, processed_image, diagnostics = _ocr_debug_crop(crop, box.label)
        crops.append(
            DebugCropResult(
                section=box.section,
                label=box.label,
                box=box.box,
                image=crop,
                processed_image=processed_image,
                ocr_text=ocr_text,
                confidence=confidence,
                diagnostics=diagnostics,
            )
        )
    return crops


def build_debug_overlay(image: Image.Image, page_number: int = 1) -> DebugOverlayResult:
    """OCR前に、固定表のどの範囲を読む予定かを画像へ描画します。"""

    base = ImageOps.exif_transpose(image).convert("RGB")
    _assert_valid_ocr_image(base, f"{page_number}ページ目 原画像")
    diagnostics = [_image_blankness_message(base, f"{page_number}ページ目 原画像")]
    try:
        original_ocr = run_raw_pillow_rgb_ocr(base)
        original_ocr_text = original_ocr.text
        original_ocr_confidence = original_ocr.confidence
    except Exception as exc:  # noqa: BLE001 - デバッグ画面にOCR失敗を表示します。
        logger.exception("原画像そのままOCR失敗: page=%s", page_number)
        original_ocr_text = f"OCRエラー: {exc}"
        original_ocr_confidence = 0.0

    preprocessed = base.copy()
    diagnostics.append(_image_blankness_message(preprocessed, f"{page_number}ページ目 前処理OFF"))

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    boxes = _debug_boxes_for_image(base)

    for box in boxes:
        if box.kind != "section":
            continue
        color = SECTION_DEBUG_COLORS.get(box.section, (0, 150, 255))
        overlay_draw.rectangle(box.box, fill=(*color, 35), outline=(*color, 255), width=5)

    annotated = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(annotated)

    for box in boxes:
        if box.kind == "section":
            color = SECTION_DEBUG_COLORS.get(box.section, (0, 150, 255))
            draw.rectangle(box.box, outline=color, width=5)
            _draw_text_label(draw, (box.box[0] + 6, box.box[1] + 6), f"{box.label}（{box.source}）", color)
        else:
            color = DEBUG_COLUMN_COLORS.get(box.label, (255, 0, 0))
            draw.rectangle(box.box, outline=color, width=4)
            _draw_text_label(draw, (box.box[0] + 4, box.box[1] + 28), box.label, color)

    crops = _debug_crops_for_image(base, boxes)
    return DebugOverlayResult(
        page_number=page_number,
        image=annotated,
        original_image=base,
        preprocessed_image=preprocessed,
        original_ocr_text=original_ocr_text,
        original_ocr_confidence=original_ocr_confidence,
        diagnostics=diagnostics,
        boxes=boxes,
        crops=crops,
    )


def build_raw_ocr_page(image: Image.Image, page_number: int = 1) -> RawOCRPageResult:
    """固定表処理を使わず、Pillow原画像をそのまま表示・OCRします。"""

    base = ImageOps.exif_transpose(image).convert("RGB")
    context = f"{page_number}ページ目 原画像そのままOCR"
    _assert_valid_ocr_image(base, context)
    diagnostics = [_image_blankness_message(base, context)]
    try:
        ocr_result = run_raw_pillow_rgb_ocr(base, context=context)
        ocr_text = ocr_result.text
        ocr_confidence = ocr_result.confidence
    except Exception as exc:  # noqa: BLE001 - 画面にOCR失敗を表示します。
        logger.exception("原画像そのままOCR失敗: page=%s", page_number)
        ocr_text = f"OCRエラー: {exc}"
        ocr_confidence = 0.0
        logger.info("OCR全文 START: context=%s engine=Tesseract原画像RGB confidence=%s", context, ocr_confidence)
        logger.info("%s", ocr_text)
        logger.info("OCR全文 END: context=%s", context)

    return RawOCRPageResult(
        page_number=page_number,
        original_image=base,
        ocr_text=ocr_text,
        ocr_confidence=ocr_confidence,
        diagnostics=diagnostics,
    )


def raw_ocr_pages_for_upload(
    file_name: str,
    file_bytes: bytes,
    mime_type: str | None = None,
) -> list[RawOCRPageResult]:
    """アップロードファイルを原画像のままOCRする確認モードです。"""

    images = images_from_upload(file_name, file_bytes, mime_type=mime_type)
    return [build_raw_ocr_page(image, page_number=index + 1) for index, image in enumerate(images)]


def debug_overlays_for_upload(
    file_name: str,
    file_bytes: bytes,
    mime_type: str | None = None,
) -> list[DebugOverlayResult]:
    """アップロードファイルから切り出し枠確認用の画像を作ります。"""

    images = images_from_upload(file_name, file_bytes, mime_type=mime_type)
    return [build_debug_overlay(image, page_number=index + 1) for index, image in enumerate(images)]


def run_fixed_layout_ocr(image: Image.Image) -> OCRResult:
    """固定表OCRは一旦停止し、原画像そのままOCRだけを返します。"""

    logger.info("固定表OCR停止中: 原画像そのままOCRへ切り替えます")
    return run_raw_pillow_rgb_ocr(image, context="固定表OCR停止中の原画像OCR")


def run_ocr_for_image(image: Image.Image) -> OCRResult:
    """まず読み込んだ原画像そのままでOCRします。"""

    return run_raw_pillow_rgb_ocr(image)


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


def _images_from_upload_bytes(file_bytes: bytes, context: str = "Pillow読み込み後画像") -> list[Image.Image]:
    """PNG/JPEG/TIFFなどのアップロード画像をPillow + BytesIOで統一して読み込みます。"""

    try:
        with Image.open(BytesIO(file_bytes)) as image:
            frames: list[Image.Image] = []
            for frame_index, frame in enumerate(ImageSequence.Iterator(image), start=1):
                rgb = frame.convert("RGB")
                _assert_valid_ocr_image(rgb, f"{context} {frame_index}ページ目")
                _image_blankness_message(rgb, f"{context} {frame_index}ページ目")
                frames.append(rgb.copy())
    except Exception as exc:  # noqa: BLE001 - 利用者向けに読み込み失敗理由を固定します。
        logger.exception("Pillow画像読み込み失敗: context=%s pillow_error=%s", context, exc)
        raise RuntimeError("画像を読み込めませんでした。") from exc

    if not frames:
        logger.error("Pillow画像読み込み失敗: context=%s pillow_error=no frames", context)
        raise RuntimeError("画像を読み込めませんでした。")

    return frames


def images_from_upload(
    file_name: str,
    file_bytes: bytes,
    mime_type: str | None = None,
) -> list[Image.Image]:
    """アップロードされた画像/PDFをPIL画像の一覧にします。"""

    suffix = Path(file_name).suffix.lower()
    if suffix == ".pdf":
        images = []
        for index, image in enumerate(pdf_to_images(file_bytes), start=1):
            rgb = image.convert("RGB")
            _assert_valid_ocr_image(rgb, f"PDF変換後画像 {index}ページ目")
            _image_blankness_message(rgb, f"PDF変換後画像 {index}ページ目")
            images.append(rgb)
        return images

    return _images_from_upload_bytes(file_bytes)


def run_ocr_for_upload(
    file_name: str,
    file_bytes: bytes,
    mime_type: str | None = None,
) -> OCRResult:
    """アップロードファイル全体をOCRします。"""

    images = images_from_upload(file_name, file_bytes, mime_type=mime_type)
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
