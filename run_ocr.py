#!/usr/bin/env python3
"""画像資料をOCRし、確認しやすいExcelへ転記するコマンドです。

使い方:
  1. input_images/ に jpg / jpeg / png / bmp / tiff を入れる
  2. python run_ocr.py を実行する
  3. output/ocr_転記結果.xlsx を確認する
"""

from __future__ import annotations

import datetime as dt
import importlib
import importlib.util
import io
import logging
import multiprocessing as mp
import queue
import re
import statistics
import sys
import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

Workbook = None
Alignment = None
Font = None
PatternFill = None
get_column_letter = None
Image = None
ImageEnhance = None
ImageFilter = None
ImageOps = None
ImageStat = None

INPUT_DIR = Path("input_images")
OUTPUT_DIR = Path("output")
PROCESSED_DIR = OUTPUT_DIR / "processed_images"
EXCEL_PATH = OUTPUT_DIR / "ocr_転記結果.xlsx"
LOG_PATH = OUTPUT_DIR / "ocr_log.txt"

REQUIRED_MODULES = {
    "PIL": "pillow",
    "openpyxl": "openpyxl",
}

def load_required_dependencies() -> None:
    missing = [package for module, package in REQUIRED_MODULES.items() if importlib.util.find_spec(module) is None]
    if missing:
        message = (
            "必要なPythonライブラリが足りません: "
            + ", ".join(missing)
            + "\n先に `pip install -r requirements.txt` を実行してください。"
        )
        print(message, file=sys.stderr)
        raise SystemExit(1)

    global Workbook, Alignment, Font, PatternFill, get_column_letter
    global Image, ImageEnhance, ImageFilter, ImageOps, ImageStat

    openpyxl_module = importlib.import_module("openpyxl")
    openpyxl_styles = importlib.import_module("openpyxl.styles")
    openpyxl_utils = importlib.import_module("openpyxl.utils")
    Workbook = openpyxl_module.Workbook
    Alignment = openpyxl_styles.Alignment
    Font = openpyxl_styles.Font
    PatternFill = openpyxl_styles.PatternFill
    get_column_letter = openpyxl_utils.get_column_letter

    Image = importlib.import_module("PIL.Image")
    ImageEnhance = importlib.import_module("PIL.ImageEnhance")
    ImageFilter = importlib.import_module("PIL.ImageFilter")
    ImageOps = importlib.import_module("PIL.ImageOps")
    ImageStat = importlib.import_module("PIL.ImageStat")


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
ORIENTATIONS = (0,)
LOW_CONFIDENCE_THRESHOLD = 70.0
VERY_LOW_CONFIDENCE_THRESHOLD = 45.0
MAX_IMAGE_WIDTH = 800
MAX_IMAGE_HEIGHT = 1200
MAX_FILE_SIZE_BYTES = 60 * 1024 * 1024
JPEG_QUALITY = 55
MAX_OCR_IMAGE_EDGE = 30000
MAX_OCR_IMAGE_PIXELS = 250_000_000
OCR_TIMEOUT_SECONDS = 20
TESSERACT_CALL_TIMEOUT_SECONDS = 18
OCR_CANDIDATE_LIMIT = 2
OCR_DPI = 300
TIMEOUT_MESSAGE = "タイムアウト：画像が重すぎるため処理を中断しました"

HEADERS = [
    "処理日時",
    "元ファイル名",
    "採用した向き",
    "補正方法",
    "OCRエンジン",
    "読み取り全文",
    "抽出した食材",
    "抽出した数量",
    "抽出した単位",
    "抽出した日付",
    "抽出した金額",
    "OCR内の数量候補",
    "抽出した電話番号",
    "信頼度",
    "要確認フラグ",
    "備考",
    "補正後画像パス",
]

DATE_PATTERNS = [
    r"(?:20\d{2}|19\d{2})[./\-年]\s*(?:0?[1-9]|1[0-2])[./\-月]\s*(?:0?[1-9]|[12]\d|3[01])日?",
    r"(?:令和|平成|昭和)\s*[元0-9０-９]{1,2}\s*年\s*(?:0?[1-9]|1[0-2]|[０-９]{1,2})\s*月\s*(?:0?[1-9]|[12]\d|3[01]|[０-９]{1,2})\s*日?",
    r"R\s*[0-9０-９]{1,2}[./\-]\s*(?:0?[1-9]|1[0-2])[./\-]\s*(?:0?[1-9]|[12]\d|3[01])",
    r"H\s*[0-9０-９]{1,2}[./\-]\s*(?:0?[1-9]|1[0-2])[./\-]\s*(?:0?[1-9]|[12]\d|3[01])",
]
AMOUNT_PATTERN = r"(?:¥|￥)?\s*[0-9０-９]{1,3}(?:[,，][0-9０-９]{3})*(?:円|\s*yen)?|[0-9０-９]+\s*円"
QUANTITY_PATTERN = r"[0-9０-９]+(?:[.,．][0-9０-９]+)?\s*(?:個|本|枚|箱|袋|束|冊|台|件|人|名|kg|ＫＧ|g|ｇ|L|ｍ?l|ml|パック|ケース|セット|食|円)"
PHONE_PATTERN = r"(?:0\d{1,4}[-ー−]?\d{1,4}[-ー−]?\d{3,4}|0[789]0[-ー−]?\d{4}[-ー−]?\d{4})"
POSTAL_PATTERN = r"〒?\s*\d{3}[-ー−]\d{4}"


@dataclass
class OcrCandidate:
    engine: str
    text: str = ""
    confidence: float = 0.0
    angle: int = 0
    method: str = ""
    image: Image.Image | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return self.confidence + japanese_count(self.text) * 0.08 + digit_count(self.text) * 0.04


@dataclass
class ExtractedFields:
    dates: list[str]
    amounts: list[str]
    quantities: list[str]
    phones: list[str]
    postal_codes: list[str]


@dataclass
class IngredientRow:
    name: str
    quantity: str
    unit: str
    weekday: str = ""


@dataclass
class SourceIngredientRow:
    cells: list[str]
    row_text: str
    weekday: str
    under_three_column: int

def optional_module(name: str) -> Any | None:
    if importlib.util.find_spec(name) is None:
        return None
    return importlib.import_module(name)

def setup() -> None:
    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    PROCESSED_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        encoding="utf-8",
        force=True,
    )
    logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))

def supported_images() -> list[Path]:
    return sorted(path for path in INPUT_DIR.iterdir() if path.suffix.lower() in SUPPORTED_EXTENSIONS and path.is_file())

def validate_ocr_image(image: Image.Image, context: str) -> None:
    width, height = image.size
    logging.info("OCR前画像サイズ: context=%s mode=%s width=%s height=%s", context, image.mode, width, height)
    if width <= 0 or height <= 0:
        raise ValueError(f"OCR停止: 画像サイズが異常です（{context}: {width}x{height}）")
    if width > MAX_OCR_IMAGE_EDGE or height > MAX_OCR_IMAGE_EDGE or width * height > MAX_OCR_IMAGE_PIXELS:
        raise ValueError(f"OCR停止: 画像サイズが大きすぎます（{context}: {width}x{height}）")


def log_image_blankness(image: Image.Image, context: str) -> None:
    gray = ImageOps.grayscale(image)
    stat = ImageStat.Stat(gray)
    mean = stat.mean[0]
    stddev = stat.stddev[0]
    min_value, max_value = gray.getextrema()
    logging.info(
        "OCR前画像状態: context=%s width=%s height=%s mean=%.1f stddev=%.1f min=%s max=%s",
        context,
        gray.width,
        gray.height,
        mean,
        stddev,
        min_value,
        max_value,
    )
    if stddev < 1.0 or min_value == max_value:
        logging.warning("OCR前画像が空白の可能性があります: %s", context)
    elif mean > 252 and stddev < 8:
        logging.warning("OCR前画像が白飛びの可能性があります: %s", context)
    elif mean < 3 and stddev < 8:
        logging.warning("OCR前画像が真っ黒の可能性があります: %s", context)


def load_image(path: Path) -> Image.Image:
    file_size = path.stat().st_size
    if file_size > MAX_FILE_SIZE_BYTES:
        logging.warning(
            "画像ファイルが大きいため、異常サイズ判定後にOCRします: %s size=%.1fMB",
            path,
            file_size / 1024 / 1024,
        )

    file_bytes = path.read_bytes()
    image = Image.open(io.BytesIO(file_bytes))
    image = image.convert("RGB")

    logging.info(
        "画像確認: %s size=%.1fMB resolution=%sx%s",
        path,
        file_size / 1024 / 1024,
        image.width,
        image.height,
    )
    validate_ocr_image(image, f"読み込み後原画像 {path.name}")
    log_image_blankness(image, f"読み込み後原画像 {path.name}")
    return image

def lighten_image_for_ocr(image: Image.Image, path: Path) -> Image.Image:
    image = image.convert("L")
    scale = min(1.0, MAX_IMAGE_WIDTH / max(1, image.width), MAX_IMAGE_HEIGHT / max(1, image.height))
    if scale < 1.0:
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
        logging.info("OCR前軽量化: %s resized=%sx%s", path, image.width, image.height)

    safe_stem = re.sub(r"[^0-9A-Za-zぁ-んァ-ン一-龥_-]+", "_", path.stem).strip("_") or "image"
    temp_path = PROCESSED_DIR / f"{safe_stem}_light.jpg"
    image.save(temp_path, format="JPEG", quality=JPEG_QUALITY, optimize=True, dpi=(OCR_DPI, OCR_DPI))
    logging.info("OCR前JPEG軽量化: %s mode=grayscale max_width=%s dpi=%s quality=%s temp=%s", path, MAX_IMAGE_WIDTH, OCR_DPI, JPEG_QUALITY, temp_path)
    with Image.open(temp_path) as reopened:
        return ImageOps.exif_transpose(reopened).convert("L")

def trim_margin(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    inverted = ImageOps.invert(gray)
    bbox = inverted.point(lambda p: 255 if p > 18 else 0).getbbox()
    if not bbox:
        return image
    left, top, right, bottom = bbox
    pad = 12
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(image.width, right + pad)
    bottom = min(image.height, bottom + pad)
    if right - left < image.width * 0.15 or bottom - top < image.height * 0.15:
        return image
    return image.crop((left, top, right, bottom))

def upscale(image: Image.Image, min_width: int = 1000) -> Image.Image:
    if image.width >= min_width:
        return image
    ratio = min_width / max(1, image.width)
    new_size = (int(image.width * ratio), int(image.height * ratio))
    return image.resize(new_size, Image.Resampling.LANCZOS)

def gamma_correct(image: Image.Image, gamma: float) -> Image.Image:
    inv = 1.0 / gamma
    table = [min(255, max(0, int((i / 255.0) ** inv * 255))) for i in range(256)]
    return image.point(table)

def otsu_threshold(gray: Image.Image) -> Image.Image:
    hist = gray.histogram()
    total = sum(hist)
    sum_total = sum(index * value for index, value in enumerate(hist))
    sum_background = 0.0
    weight_background = 0
    max_variance = 0.0
    threshold = 128
    for index, value in enumerate(hist):
        weight_background += value
        if weight_background == 0:
            continue
        weight_foreground = total - weight_background
        if weight_foreground == 0:
            break
        sum_background += index * value
        mean_background = sum_background / weight_background
        mean_foreground = (sum_total - sum_background) / weight_foreground
        variance = weight_background * weight_foreground * (mean_background - mean_foreground) ** 2
        if variance > max_variance:
            max_variance = variance
            threshold = index
    return gray.point(lambda p: 255 if p > threshold else 0)

def pil_preprocess_variants(image: Image.Image) -> list[tuple[str, Image.Image]]:
    validate_ocr_image(image, "原画像そのままOCR")
    log_image_blankness(image, "原画像そのままOCR")
    gray = ImageOps.grayscale(image)
    validate_ocr_image(gray, "グレースケールOCR")
    log_image_blankness(gray, "グレースケールOCR")
    return [("原画像RGBそのまま", image), ("グレースケールのみ", gray)]

def opencv_preprocess_variants(image: Image.Image) -> list[tuple[str, Image.Image]]:
    cv2 = optional_module("cv2")
    np = optional_module("numpy")
    if cv2 is None or np is None:
        return []

    arr = np.array(image)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    gray = deskew_cv2(gray, cv2, np)
    return [("傾き補正+グレースケール", Image.fromarray(gray))]

def deskew_cv2(gray: Any, cv2: Any, np: Any) -> Any:
    coords = np.column_stack(np.where(gray < 245))
    if len(coords) < 50:
        return gray
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.4 or abs(angle) > 12:
        return gray
    height, width = gray.shape[:2]
    matrix = cv2.getRotationMatrix2D((width // 2, height // 2), angle, 1.0)
    return cv2.warpAffine(gray, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

def tesseract_candidate(image: Image.Image, angle: int, method: str) -> OcrCandidate:
    pytesseract = optional_module("pytesseract")
    if pytesseract is None:
        return OcrCandidate("Tesseract", angle=angle, method=method, notes=["pytesseractが未インストールです"])

    configs = [f"--oem 3 --psm 6 --dpi {OCR_DPI}"]
    lang = "jpn+eng"
    best = OcrCandidate("Tesseract", angle=angle, method=method, image=image)
    for config in configs:
        data = run_tesseract_data(pytesseract, image, lang, config)
        if data is None and lang != "eng":
            data = run_tesseract_data(pytesseract, image, "eng", config)
        if data is None:
            continue
        confidences = []
        for raw_conf in data.get("conf", []):
            try:
                conf = float(raw_conf)
            except ValueError:
                continue
            if conf >= 0:
                confidences.append(conf)
        text = reconstruct_ocr_rows(data, image)
        confidence = statistics.mean(confidences) if confidences else 0.0
        candidate = OcrCandidate("Tesseract", text, confidence, angle, method, image)
        if candidate.score > best.score:
            best = candidate
    if not best.text:
        best.notes.append("Tesseractで文字を読み取れませんでした")
    return best

def reconstruct_ocr_rows(data: dict[str, Any], image: Image.Image | None = None) -> str:
    words = []
    for index, raw_text in enumerate(data.get("text", [])):
        text = normalize_ocr_line(str(raw_text).strip())
        if not text:
            continue
        try:
            left = int(float(data.get("left", [0])[index]))
            top = int(float(data.get("top", [0])[index]))
            width = int(float(data.get("width", [0])[index]))
            height = int(float(data.get("height", [0])[index]))
            block = int(float(data.get("block_num", [0])[index]))
            par = int(float(data.get("par_num", [0])[index]))
            line = int(float(data.get("line_num", [0])[index]))
        except (ValueError, TypeError, IndexError):
            continue
        if width <= 0 or height <= 0:
            continue
        words.append({"text": text, "left": left, "top": top, "right": left + width, "bottom": top + height, "height": height, "line_key": (block, par, line)})
    if not words:
        return ""

    rule_bands = detect_horizontal_rule_bands(image) if image is not None else []
    if rule_bands:
        grouped = group_words_by_rule_bands(words, rule_bands)
    else:
        grouped = group_words_by_tesseract_lines(words)
    rows = [join_positioned_words(row) for row in grouped]
    return "\n".join(row for row in rows if row)

def detect_horizontal_rule_bands(image: Image.Image | None) -> list[tuple[int, int]]:
    cv2 = optional_module("cv2")
    np = optional_module("numpy")
    if image is None or cv2 is None or np is None:
        return []
    try:
        gray = np.array(ImageOps.grayscale(image))
        binary = cv2.threshold(gray, 190, 255, cv2.THRESH_BINARY_INV)[1]
        kernel_width = max(30, image.width // 3)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, 1))
        horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        row_hits = np.where(horizontal.sum(axis=1) > 255 * image.width * 0.35)[0]
    except Exception as exc:
        logging.info("横罫線検出をスキップ: %s", exc)
        return []
    bands: list[tuple[int, int]] = []
    for y in row_hits.tolist():
        if not bands or y > bands[-1][1] + 2:
            bands.append((y, y))
        else:
            bands[-1] = (bands[-1][0], y)
    return bands

def group_words_by_rule_bands(words: list[dict[str, Any]], bands: list[tuple[int, int]]) -> list[list[dict[str, Any]]]:
    separators = [int((start + end) / 2) for start, end in bands]
    rows_by_band: dict[int, list[dict[str, Any]]] = {}
    for word in words:
        center_y = int((word["top"] + word["bottom"]) / 2)
        row_index = sum(1 for y in separators if y < center_y)
        rows_by_band.setdefault(row_index, []).append(word)
    rows = [row for _index, row in sorted(rows_by_band.items()) if row]
    return split_tall_rule_rows(rows)

def split_tall_rule_rows(rows: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    split_rows: list[list[dict[str, Any]]] = []
    for row in rows:
        heights = [word["height"] for word in row]
        median_height = statistics.median(heights) if heights else 12
        row.sort(key=lambda item: (item["top"], item["left"]))
        buckets: list[list[dict[str, Any]]] = []
        for word in row:
            center_y = (word["top"] + word["bottom"]) / 2
            for bucket in buckets:
                bucket_center = statistics.mean((item["top"] + item["bottom"]) / 2 for item in bucket)
                if abs(center_y - bucket_center) <= max(8, median_height * 0.7):
                    bucket.append(word)
                    break
            else:
                buckets.append([word])
        split_rows.extend(buckets)
    return split_rows

def group_words_by_tesseract_lines(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    for word in words:
        grouped.setdefault(word["line_key"], []).append(word)
    rows = list(grouped.values())
    rows.sort(key=lambda row: (statistics.mean(item["top"] for item in row), min(item["left"] for item in row)))
    return rows

def join_positioned_words(words: list[dict[str, Any]]) -> str:
    words = sorted(words, key=lambda item: item["left"])
    if not words:
        return ""
    heights = [word["height"] for word in words]
    median_height = statistics.median(heights) if heights else 12
    parts = [words[0]["text"]]
    prev = words[0]
    for word in words[1:]:
        gap = word["left"] - prev["right"]
        separator = "\t" if gap > max(18, median_height * 1.4) else " "
        parts.append(separator + word["text"])
        prev = word
    return normalize_ocr_line("".join(parts))

def run_tesseract_data(pytesseract: Any, image: Image.Image, lang: str, config: str) -> dict[str, Any] | None:
    try:
        return pytesseract.image_to_data(image, lang=lang, config=config, output_type=pytesseract.Output.DICT, timeout=TESSERACT_CALL_TIMEOUT_SECONDS)
    except Exception as exc:
        logging.info("Tesseract失敗 lang=%s config=%s: %s", lang, config, exc)
        return None

def best_tesseract_orientation(source: Image.Image) -> tuple[OcrCandidate, list[OcrCandidate]]:
    best = OcrCandidate("Tesseract", notes=[])
    candidates: list[OcrCandidate] = []
    pattern_count = 0
    for angle in ORIENTATIONS:
        rotated = source.rotate(angle, expand=True)
        for method, processed in pil_preprocess_variants(rotated):
            if pattern_count >= OCR_CANDIDATE_LIMIT:
                logging.info("OCR候補上限に到達: limit=%s", OCR_CANDIDATE_LIMIT)
                return best, candidates
            pattern_count += 1
            candidate = tesseract_candidate(processed, angle, method)
            candidates.append(candidate)
            logging.info("OCR候補結果: method=%s angle=%s text_length=%s confidence=%.1f", method, angle, len(candidate.text.strip()), candidate.confidence)
            if pattern_count == 1 and not candidate.text.strip():
                logging.info("1回目OCR結果が空のため、別パターンで再OCRします")
            if candidate.score > best.score:
                best = candidate
    return best, candidates

def easyocr_candidate(image: Image.Image, angle: int, method: str) -> OcrCandidate | None:
    easyocr = optional_module("easyocr")
    np = optional_module("numpy")
    if easyocr is None or np is None:
        return None
    try:
        reader = easyocr.Reader(["ja", "en"], gpu=False, verbose=False)
        results = reader.readtext(np.array(image), detail=1, paragraph=False)
    except Exception as exc:
        logging.info("EasyOCR失敗: %s", exc)
        return None
    texts = [str(item[1]).strip() for item in results if len(item) >= 2 and str(item[1]).strip()]
    confidences = [float(item[2]) * 100 for item in results if len(item) >= 3]
    return OcrCandidate("EasyOCR", "\n".join(texts), statistics.mean(confidences) if confidences else 0.0, angle, method, image)

def paddleocr_candidate(image: Image.Image, angle: int, method: str) -> OcrCandidate | None:
    paddleocr_module = optional_module("paddleocr")
    np = optional_module("numpy")
    if paddleocr_module is None or np is None:
        return None
    try:
        ocr = paddleocr_module.PaddleOCR(use_angle_cls=True, lang="japan", show_log=False)
        results = ocr.ocr(np.array(image), cls=True)
    except Exception as exc:
        logging.info("PaddleOCR失敗: %s", exc)
        return None
    texts: list[str] = []
    confidences: list[float] = []
    for page in results or []:
        for line in page or []:
            if len(line) >= 2 and len(line[1]) >= 2:
                text, conf = line[1][0], line[1][1]
                if str(text).strip():
                    texts.append(str(text).strip())
                confidences.append(float(conf) * 100)
    return OcrCandidate("PaddleOCR", "\n".join(texts), statistics.mean(confidences) if confidences else 0.0, angle, method, image)

def collect_candidates(source: Image.Image) -> tuple[OcrCandidate, list[OcrCandidate]]:
    best, candidates = best_tesseract_orientation(source)
    if not candidates:
        candidates = [best]
    best = max(candidates, key=lambda item: item.score)
    return best, candidates

def japanese_count(text: str) -> int:
    return len(re.findall(r"[ぁ-んァ-ン一-龥]", text))

def digit_count(text: str) -> int:
    return len(re.findall(r"[0-9０-９]", text))

def normalize_value(value: str) -> str:
    table = str.maketrans("０１２３４５６７８９，．ー−￥", "0123456789,.-ー¥")
    return re.sub(r"\s+", "", value.translate(table))

def unique_matches(patterns: str | Iterable[str], text: str) -> list[str]:
    if isinstance(patterns, str):
        patterns = [patterns]
    seen: set[str] = set()
    values: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            value = match if isinstance(match, str) else "".join(match)
            value = value.strip()
            key = normalize_value(value)
            if value and key not in seen:
                seen.add(key)
                values.append(value)
    return values

def extract_fields(text: str) -> ExtractedFields:
    return ExtractedFields(
        dates=unique_matches(DATE_PATTERNS, text),
        amounts=unique_matches(AMOUNT_PATTERN, text),
        quantities=unique_matches(QUANTITY_PATTERN, text),
        phones=unique_matches(PHONE_PATTERN, text),
        postal_codes=unique_matches(POSTAL_PATTERN, text),
    )


UNIT_PATTERN = r"kg|㎏|キロ|g|グラム|ml|cc|L|リットル|個|本|袋|パック|玉|束|枚|缶|箱|尾|切|片|丁|株|房|杯|膳|食|人前"
IGNORED_LINE_PATTERN = re.compile(r"OCR全文|発注書|納品書|納品日|使用日|検品者|合計|金額|単価|摘要|チェック|ページ|請求|消費税|小計|担当|取引先|電話|FAX|〒|住所")
SENTENCE_NOISE_PATTERN = re.compile(r"作り方|つくり方|手順|注釈|説明|説明文|調理方法|下処理|切る|切って|切り|ゆでる|茹でる|煮る|煮込む|焼く|炒める|蒸す|揚げる|混ぜる|和える|加える|入れる|のせる|盛る|塗る|洗う|さらす|水気|一口大|短冊|千切り|みじん切り|いちょう切り|薄切り|乱切り|小房|皮をむく|火を通す|味を調える|味をととのえる|味を整える|を塗って|してください|しましょう|します|しました|する|です|ます|もう|食べる|食べます")
EXCLUDED_INGREDIENT_PATTERN = re.compile(r"スチコン|オーブン|コンビモード|レンジ|機器|器具|コンソメ|ョヨンツメ|ヨ肥外|片栗粉|片栗|片困粉|用本明|有本塊|米$|^米$|精白米|白米|ごはん|御飯|だし|出汁|だし汁|煮干しだし|かつおだし|昆布だし|水$|調味料|調味料全般|食塩|塩$|砂糖|しょうゆ$|醤油$|みそ|味噌|酢$|油$|サラダ油|ごま油|酒$|みりん|こしょう|胡椒|ソース|ケチャップ|マヨネーズ|中華だし|カレー粉")
WEEKDAY_PEOPLE = {"月": 5, "火": 7, "水": 7, "木": 7, "金": 7}
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
FIXED_ORDER_RULES = []
ROUNDING_ORDER_RULES = [
    ("牛乳", "本", 2.0, {"ml": 450.0, "g": 450.0}, re.compile(r"牛乳|ミルク")),
    ("キャベツ", "個", 0.25, {"g": 1200.0}, re.compile(r"キャベツ")),
    ("白菜", "個", 0.125, {"g": 2000.0}, re.compile(r"白菜")),
    ("しめじ", "袋", 1.0, {"g": 100.0}, re.compile(r"しめじ|シメジ")),
    ("きのこ類", "袋", 1.0, {"g": 100.0}, re.compile(r"きのこ|えのき|しいたけ|椎茸|まいたけ|舞茸|エリンギ|マッシュルーム")),
    ("ヨーグルト", "パック", 2.0, {"個": 3.0, "g": 210.0}, re.compile(r"ヨーグルト|牧場の朝")),
    ("コーン缶", "缶", 1.0, {"缶": 1.0, "個": 1.0}, re.compile(r"コーン缶|とうもろこし缶|トウモロコシ缶")),
    ("みかん缶", "缶", 1.0, {"缶": 1.0, "個": 1.0}, re.compile(r"みかん缶|蜜柑缶|ミカン缶|みかんかん")),
    ("ツナ油漬け缶", "缶", 1.0, {"缶": 1.0, "個": 1.0}, re.compile(r"ツナ油漬け缶|ツナ油漬け|ツナ油漬|ツナ油づけ|ツナ缶|ツナ")),
    ("缶詰", "缶", 1.0, {"缶": 1.0, "個": 1.0}, re.compile(r"缶詰|桃缶|パイン缶")),
]

PRIORITY_FOOD_PATTERN = re.compile(r"にんじん|人参|たまねぎ|玉ねぎ|玉葱|じゃがいも|馬鈴薯|キャベツ|白菜|きゅうり|胡瓜|もやし|わかめ|若布|ひじき|しめじ|えのき|しいたけ|椎茸|まいたけ|舞茸|エリンギ|きのこ|豚ひき肉|豚挽き肉|豚肉|鶏肉|牛肉|ミンチ|豆腐|木綿豆腐|絹豆腐|油揚げ|卵|玉子|牛乳|ミルク|食パン|パン|ジャム|ヨーグルト|チーズ|米粉|小麦粉|せんべい|ツナ|ツナ缶|コーン缶|みかん缶|缶詰|鮭|さけ|さば|鯖|白身魚|ちくわ|ハム|ベーコン|コーン|バナナ|りんご|みかん|いちご")
LOOSE_NUMBER_PATTERN = re.compile(r"(?<![0-9])([0-9]+(?:\.[0-9]+)?)(?:\s*(" + UNIT_PATTERN + r"))?", re.IGNORECASE)
CANONICAL_INGREDIENT_PATTERNS = [
    ("しょうゆせんべい", re.compile(r"しょう\s*ゆ?\s*せんべい|しょうゆ?\s*せんべい|しょうゆせんし|醤油\s*せんべい|せんい")),
    ("牛乳", re.compile(r"牛乳|ミルク|(?:^|[^A-Za-z])Fh(?:$|[^A-Za-z])")),
    ("ひじき", re.compile(r"ひじき")),
    ("豚ひき肉", re.compile(r"豚\s*(?:ひき|挽き|挽)\s*(?:肉|内)|(?:^|[^ぁ-んァ-ン一-龥])ひき\s*内|豚ミンチ|評[O0]き琴")),
    ("木綿豆腐", re.compile(r"木綿\s*豆腐|震記一一意|豆\s*(?:褒|腐|放)")),
    ("たまねぎ", re.compile(r"たまねぎ|玉ねぎ|玉葱|たまねを|療半と")),
    ("もやし", re.compile(r"もやし|(?:^|[^ぁ-んァ-ン一-龥])もや(?:$|[^ぁ-んァ-ン一-龥])")),
    ("きゅうり", re.compile(r"きゅうり|きゆうり|きゅうの|胡瓜")),
    ("カットわかめ", re.compile(r"カット\s*わかめ|わかめ|若布")),
    ("じゃがいも", re.compile(r"じゃがいも|とゃがいも|馬鈴薯|(?:^|[^ぁ-んァ-ン一-龥])がし(?:$|[^ぁ-んァ-ン一-龥])")),
    ("にんじん", re.compile(r"にんじん|にんん|にんヒじん|人参|ニンジン|(?<![0-9])0\s*80\s*66\s*9(?![0-9])|(?<![0-9])080669(?![0-9])")),
    ("食パン", re.compile(r"食パン|a\s*emw", re.IGNORECASE)),
    ("いちごジャム", re.compile(r"いちご\s*ジャム|でちこ\s*ジャ|苺\s*ジャム|(?<![0-9])60\s*42\s*7(?![0-9])")),
    ("キャベツ", re.compile(r"キャベツ|キャヘツ|きゃべつ")),
    ("白菜", re.compile(r"白菜|はくさい")),
    ("しめじ", re.compile(r"しめじ|シメジ")),
    ("えのき", re.compile(r"えのき|エノキ")),
    ("しいたけ", re.compile(r"しいたけ|椎茸|シイタケ")),
    ("まいたけ", re.compile(r"まいたけ|舞茸|マイタケ")),
    ("エリンギ", re.compile(r"エリンギ")),
    ("ヨーグルト", re.compile(r"ヨーグルト|牧場の朝")),
    ("鶏もも(皮なし)", re.compile(r"鶏もも\s*(?:肉)?|鶏モモ(?!肉)|鶏肉|とりもも肉|鶏モモ肉(?:\(?皮なし\)?|（皮なし）)")),
    ("コーン缶", re.compile(r"コーン缶|とうもろこし缶|トウモロコシ缶")),
    ("ブロッコリー", re.compile(r"ブロッコリー|ブロツコリー|ブロコリー|ブロッコリ|プロッコリー")),
    ("オレンジ濃縮果汁", re.compile(r"オレンジ濃縮果汁|オレンジ果汁|オレンジ濃縮|濃縮オレンジ果汁|オレンジのうしゅく果汁")),
    ("みかん缶", re.compile(r"みかん缶|蜜柑缶|ミカン缶|みかんかん")),
    ("ツナ油漬け缶", re.compile(r"ツナ油漬け缶|ツナ油漬け|ツナ油漬|ツナ油づけ|ツナ缶|ツナ")),
    ("缶詰", re.compile(r"缶詰|桃缶|パイン缶")),
]



def corrected_ingredient_from_text(value: str) -> str:
    compact = re.sub(r"\s+", "", normalize_ocr_line(value))
    if not compact or SENTENCE_NOISE_PATTERN.search(compact):
        return ""
    for canonical, pattern in CANONICAL_INGREDIENT_PATTERNS:
        if pattern.search(compact):
            return canonical
    return ""

def normalize_ocr_line(value: str) -> str:
    table = str.maketrans("０１２３４５６７８９，．ｋＫｇＧｍＭｌＬ", "0123456789,.kkggmmll")
    text = str(value or "").translate(table)
    text = re.sub(r"([0-9])[ \f\v]*(キロ|KG)", r"\1kg", text, flags=re.IGNORECASE)
    text = re.sub(r"([0-9])[ \f\v]*(グラム|G)(?=\s|$)", r"\1g", text, flags=re.IGNORECASE)
    text = re.sub(r"[|＿_~〜=<>《》]+", " ", text)
    return re.sub(r"[ \f\v\r\n]+", " ", text).strip()

def normalize_unit(value: str) -> str:
    unit = normalize_ocr_line(value)
    lower = unit.lower()
    if lower in {"kg", "㎏", "キロ"}:
        return "kg"
    if lower in {"g", "グラム"}:
        return "g"
    if lower == "ml":
        return "ml"
    return unit

def is_garbled_text(value: str) -> bool:
    compact = re.sub(r"\s+", "", str(value or ""))
    if not compact:
        return True
    japanese = len(re.findall(r"[ぁ-んァ-ン一-龥]", compact))
    readable = japanese + len(re.findall(r"[A-Za-z0-9]", compact))
    broken = len(re.findall(r"[�□■◇◆○●]", compact))
    return broken > 0 or (len(compact) >= 12 and readable / len(compact) < 0.45)

def clean_ingredient_name(value: str) -> str:
    name = strip_non_ingredient_prefix(value)
    name = re.sub(r"OCR全文", " ", name)
    name = re.sub(r"月曜日|火曜日|水曜日|木曜日|金曜日|[月火水木金]曜", " ", name)
    name = re.sub(r"^[月火水木金]\s+", "", name)
    name = re.sub(r"^[0-9０-９]+[.)）．、\s]+", "", name)
    name = re.sub(r"^[□■◇◆☑✓・*\-－—\s]+", "", name)
    name = re.sub(r"[：:].*$", "", name)
    return correct_ingredient_name(re.sub(r"\s+", " ", name).strip())

def correct_ingredient_name(name: str) -> str:
    corrected = corrected_ingredient_from_text(name)
    return corrected or str(name or "").strip()

def strip_non_ingredient_prefix(value: str) -> str:
    name = normalize_ocr_line(value)
    unit = UNIT_PATTERN
    name = re.sub(r"OCR全文", " ", name)
    name = re.sub(r"月曜日|火曜日|水曜日|木曜日|金曜日|[月火水木金]曜", " ", name)
    name = re.sub(r"(?:3歳以上児?|３歳以上児?|以上児|幼児|職員|合計|総量|使用量|数量|分量)\s*[0-9０-９]+(?:[.,．][0-9０-９]+)?\s*(?:" + unit + r")?", " ", name, flags=re.IGNORECASE)
    name = re.sub(r"[0-9０-９]+(?:[.,．][0-9０-９]+)?\s*(?:" + unit + r")(?=.*(?:3歳未満児?|３歳未満児?|未満児|乳児))", " ", name, flags=re.IGNORECASE)
    name = re.sub(r"(?:3歳未満児?|３歳未満児?|未満児|乳児).*$", " ", name, flags=re.IGNORECASE)
    name = re.sub(r"^[月火水木金]\s+", "", name)
    name = re.sub(r"^[0-9０-９]+[.)）．、\s]+", "", name)
    name = re.sub(r"^[□■◇◆☑✓・*\-－—\s]+", "", name)
    return re.sub(r"\s+", " ", name).strip()

def is_suspicious_ingredient_name(value: str) -> bool:
    name = re.sub(r"\s+", "", str(value or ""))
    if not name:
        return True
    japanese = len(re.findall(r"[ぁ-んァ-ン一-龥]", name))
    digits = len(re.findall(r"[0-9]", name))
    return japanese == 0 or digits > japanese or len(name) > 32 or is_garbled_text(name) or bool(SENTENCE_NOISE_PATTERN.search(name))


def extract_fixed_layout_ingredient_rows(image: Image.Image) -> tuple[list[IngredientRow], str]:
    """固定レイアウト表を「1行=1食材」として読み、3歳未満量だけを採用する。"""
    rows: list[IngredientRow] = []
    seen: set[str] = set()
    fixed_text_lines: list[str] = []
    review_candidates: list[str] = []
    pytesseract = optional_module("pytesseract")
    if pytesseract is None:
        logging.warning("固定表OCRをスキップ: pytesseractが未インストールです")
        return rows, ""

    source = ImageOps.grayscale(image)
    for area_label, x_ratio, y_ratio, width_ratio, height_ratio in FIXED_MENU_TABLE_AREAS:
        box = ratio_crop_box(source, x_ratio, y_ratio, width_ratio, height_ratio)
        table = source.crop(box)
        for top, bottom in detect_table_row_ranges(table):
            row_top = box[1] + top
            row_bottom = box[1] + bottom
            row_height = max(2, row_bottom - row_top)
            name_box = fixed_cell_box(box, row_top, row_top + row_height, "food_name")
            under_three_box = fixed_cell_box(box, row_top, row_top + row_height, "under_three")

            name = clean_ingredient_name(ocr_fixed_cell(pytesseract, source.crop(name_box), "jpn+eng"))
            quantity_text = ocr_fixed_cell(pytesseract, source.crop(under_three_box), "eng")
            quantity = quantity_from_under_three_cell(quantity_text)
            row_log = f"固定表行\t{area_label}\t{name or '要確認'}\t数量要確認\t"

            if not name or is_excluded_ingredient(name) or is_suspicious_ingredient_name(name):
                continue
            if not is_numeric_cell(quantity):
                review_candidates.append(row_log)
                add_ingredient_row(rows, seen, name, "数量要確認", "", "月")
                continue

            fixed_text_lines.append(f"固定表行\t{area_label}\t{name}\t{quantity}\tg")
            add_ingredient_row(rows, seen, name, quantity, "g", "月")

    if not fixed_text_lines and review_candidates:
        fixed_text_lines.extend(review_candidates[:10])
    return rows, "\n".join(fixed_text_lines)



def numeric_values_from_table_row(value: str) -> list[str]:
    """数値列OCRから、同じ行に並ぶ数値だけを取り出す。"""
    normalized = normalize_ocr_line(value).replace(",", "")
    matches = re.findall(r"(?<![0-9.])[0-9]+(?:\.[0-9]+)?(?![0-9.])", normalized)
    values: list[str] = []
    for match in matches:
        try:
            number = float(match)
        except ValueError:
            continue
        if number < 0:
            continue
        values.append(match)
    return values

def ratio_crop_box(image: Image.Image, x: float, y: float, width: float, height: float) -> tuple[int, int, int, int]:
    left = round(image.width * x)
    top = round(image.height * y)
    right = round(image.width * (x + width))
    bottom = round(image.height * (y + height))
    return max(0, left), max(0, top), min(image.width, right), min(image.height, bottom)


def detect_table_row_ranges(table: Image.Image) -> list[tuple[int, int]]:
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
    grouped = group_line_positions(line_positions)
    ranges: list[tuple[int, int]] = []
    for start, end in zip(grouped, grouped[1:]):
        top = start + 2
        bottom = end - 2
        if bottom - top >= max(10, int(gray.height * 0.035)):
            ranges.append((top, bottom))
    if ranges:
        return ranges
    fallback_count = max(1, round(gray.height / 38))
    return [(round(gray.height * index / fallback_count), round(gray.height * (index + 1) / fallback_count)) for index in range(fallback_count)]


def group_line_positions(values: list[int]) -> list[int]:
    groups: list[list[int]] = []
    for value in values:
        if not groups or value - groups[-1][-1] > 2:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [round(sum(group) / len(group)) for group in groups]



def fixed_cell_box(
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


def quantity_from_under_three_cell(value: str) -> str:
    normalized = normalize_ocr_line(value).replace(",", "")
    match = re.search(r"(?<![0-9.])[0-9]+(?:\.[0-9]+)?(?![0-9.])", normalized)
    return match.group(0) if match else ""

def ocr_fixed_cell(pytesseract: Any, image: Image.Image, lang: str) -> str:
    cell = ImageOps.grayscale(image)
    scale = 3
    cell = cell.resize((max(1, cell.width * scale), max(1, cell.height * scale)), Image.Resampling.LANCZOS)
    try:
        text = pytesseract.image_to_string(cell, lang=lang, config=f"--oem 3 --psm 7 --dpi {OCR_DPI}")
    except Exception as exc:
        logging.info("固定表セルOCR失敗: %s", exc)
        return ""
    return normalize_ocr_line(text)


def extract_ingredient_rows(text: str) -> list[IngredientRow]:
    lines = split_ocr_rows_for_ingredients(text)
    rows: list[IngredientRow] = []
    seen: set[str] = set()
    candidates_for_log: list[str] = []
    current_weekday = ""

    for index, line in enumerate(lines):
        weekday = detect_weekday(line)
        if weekday:
            current_weekday = weekday

        if not is_loose_ingredient_candidate(line):
            continue

        candidates_for_log.append(line)
        name = loose_ingredient_name(line)
        corrected_name = corrected_ingredient_from_text(line)
        number_source = under_three_quantity_near_ingredient(lines, index, corrected_name or name)
        if number_source:
            quantity, unit = number_source
            add_ingredient_row(rows, seen, corrected_name or name, quantity, unit, weekday or current_weekday)
        elif corrected_name:
            add_ingredient_row(rows, seen, corrected_name, "数量要確認", "要確認", weekday or current_weekday)

    if not rows and candidates_for_log:
        logging.info("食材候補のみ抽出: %s", " / ".join(candidates_for_log[:30]))
    return rows


def is_loose_ingredient_candidate(line: str) -> bool:
    text = normalize_ocr_line(line)
    compact = re.sub(r"\s+", "", text)
    if is_ignored_source_row(compact) or re.fullmatch(r"[月火水木金](?:曜日|曜)?", compact):
        return False
    if corrected_ingredient_from_text(text):
        return True
    japanese = len(re.findall(r"[ぁ-んァ-ン一-龥]", compact))
    if japanese < 2:
        return False
    if PRIORITY_FOOD_PATTERN.search(compact):
        return True
    return bool(re.search(r"[ぁ-んァ-ン一-龥]{2,}", compact))


def nearest_number_source(lines: list[str], index: int) -> tuple[str, str] | None:
    search_indexes = [index]
    for offset in (1, 2):
        search_indexes.extend([index + offset, index - offset])
    for row_index in search_indexes:
        if 0 <= row_index < len(lines):
            found = last_quantity_in_line(lines[row_index])
            if found:
                return found
    return None


def nearby_ocr_row_indexes(index: int, line_count: int) -> list[int]:
    candidates = [index, index + 1, index - 1, index + 2, index - 2]
    return [row_index for row_index in candidates if 0 <= row_index < line_count]


def under_three_quantity_near_ingredient(lines: list[str], index: int, ingredient_name: str = "") -> tuple[str, str] | None:
    search_indexes = [index]
    if index + 1 < len(lines):
        search_indexes.append(index + 1)

    for row_index in search_indexes:
        line = lines[row_index]
        if row_index != index and is_loose_ingredient_candidate(line) and not row_mentions_ingredient(line, ingredient_name):
            continue
        found = under_three_quantity_from_cells(split_ocr_cells(line))
        if found:
            return found
    return None


def row_mentions_ingredient(line: str, ingredient_name: str) -> bool:
    corrected = corrected_ingredient_from_text(line)
    if corrected and ingredient_name:
        return corrected == ingredient_name
    compact_line = re.sub(r"\s+", "", normalize_ocr_line(line))
    compact_name = re.sub(r"\s+", "", normalize_ocr_line(ingredient_name))
    return bool(compact_name and compact_name in compact_line)


def under_three_quantity_from_cells(cells: list[str]) -> tuple[str, str] | None:
    quantity_index = choose_same_row_quantity_index(cells)
    if quantity_index >= 0 and is_numeric_cell(cells[quantity_index]):
        quantity = normalize_value(cells[quantity_index]).replace(",", "")
        return quantity, guess_unit_near_quantity(cells, quantity_index)
    return None


def last_quantity_in_line(line: str) -> tuple[str, str] | None:
    matches = list(LOOSE_NUMBER_PATTERN.finditer(normalize_ocr_line(line)))
    valid: list[tuple[str, str]] = []
    for match in matches:
        quantity = normalize_value(match.group(1)).replace(",", "")
        try:
            if float(quantity) > 0:
                valid.append((quantity, normalize_unit(match.group(2) or "g")))
        except ValueError:
            continue
    return valid[-1] if valid else None


def loose_ingredient_name(line: str) -> str:
    corrected = corrected_ingredient_from_text(line)
    if corrected:
        return corrected
    name = normalize_ocr_line(line)
    name = re.sub(r"(?:" + UNIT_PATTERN + r")", " ", name, flags=re.IGNORECASE)
    name = re.sub(r"[0-9]+(?:\.[0-9]+)?", " ", name)
    name = re.sub(r"3歳未満児?|３歳未満児?|未満児|乳児|3歳以上児?|３歳以上児?|以上児|幼児|職員|合計|総量|使用量|数量|分量", " ", name)
    name = re.sub(r"日付|曜日|献立日|使用日|単位|食材|食品|材料|品名|食料|料理名", " ", name)
    return clean_ingredient_name(name)


def collect_source_ingredient_rows(lines: list[str]) -> list[SourceIngredientRow]:
    rows: list[SourceIngredientRow] = []
    current_weekday = ""
    under_three_column = -1

    for raw_line in lines:
        cells = split_ocr_cells(raw_line)
        row_text = normalize_ocr_line(" ".join(cells) if cells else raw_line)
        if not cells:
            continue

        weekday = detect_weekday(row_text)
        if weekday:
            current_weekday = weekday

        header_column = find_under_three_column(cells)
        if header_column >= 0:
            under_three_column = header_column
            continue

        if is_document_noise_row(row_text) or not row_has_number_value(cells):
            continue

        rows.append(SourceIngredientRow(cells, row_text, weekday or current_weekday, under_three_column))
    return rows


def extract_under_three_from_source_row(source_row: SourceIngredientRow) -> tuple[str, str, str] | None:
    quantity_index = choose_under_three_quantity_index(source_row.cells, source_row.under_three_column)
    if quantity_index < 0:
        return None

    quantity = source_row.cells[quantity_index]
    if not is_numeric_cell(quantity):
        return None

    name = ingredient_name_left_of_column(source_row.cells, quantity_index)
    if not name:
        name = ingredient_name_before_quantity(source_row.row_text, quantity)
    unit = guess_unit_near_quantity(source_row.cells, quantity_index)
    return name, quantity, unit


def choose_under_three_quantity_index(cells: list[str], under_three_column: int) -> int:
    numeric_indexes = [index for index, cell in enumerate(cells) if is_number_cell(cell)]
    if not numeric_indexes:
        return -1
    if len(numeric_indexes) >= 4:
        return numeric_indexes[2]
    if len(numeric_indexes) >= 3:
        return numeric_indexes[1]
    if under_three_column >= 0:
        right_side = [index for index in numeric_indexes if index >= under_three_column]
        if right_side:
            return right_side[0]
    if len(numeric_indexes) >= 2:
        return numeric_indexes[1]
    return numeric_indexes[0]

def choose_same_row_quantity_index(cells: list[str]) -> int:
    numeric_indexes = [index for index, cell in enumerate(cells) if is_number_cell(cell)]
    if not numeric_indexes:
        return -1
    if len(numeric_indexes) >= 4:
        return numeric_indexes[2]
    if len(numeric_indexes) >= 3:
        return numeric_indexes[1]
    if len(numeric_indexes) >= 2:
        return numeric_indexes[1]
    return numeric_indexes[0]


def row_has_number_value(cells: list[str]) -> bool:
    return any(is_number_cell(cell) for cell in cells)

def is_document_noise_row(value: str) -> bool:
    text = re.sub(r"\s+", "", str(value or ""))
    return not text or text.startswith("※") or IGNORED_LINE_PATTERN.search(text) is not None or is_garbled_text(text)


def is_ignored_source_row(value: str) -> bool:
    text = re.sub(r"\s+", "", str(value or ""))
    return is_document_noise_row(text) or SENTENCE_NOISE_PATTERN.search(text) is not None

def find_under_three_column(cells: list[str]) -> int:
    for index, cell in enumerate(cells):
        compact = re.sub(r"\s+", "", normalize_ocr_line(cell))
        if re.search(r"3歳未満|３歳未満|未満児|乳児", compact) and not re.search(r"人数|対象|区分|年齢", compact):
            return index
    return -1

def is_number_cell(value: str) -> bool:
    text = normalize_value(normalize_ocr_line(value)).replace(",", "")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text):
        return False
    try:
        return float(text) >= 0
    except ValueError:
        return False


def is_numeric_cell(value: str) -> bool:
    text = normalize_value(normalize_ocr_line(value)).replace(",", "")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text):
        return False
    try:
        return float(text) > 0
    except ValueError:
        return False

def ingredient_name_left_of_column(cells: list[str], quantity_index: int) -> str:
    name_parts: list[str] = []
    for index in range(quantity_index - 1, -1, -1):
        cell = normalize_ocr_line(cells[index])
        compact = re.sub(r"\s+", "", cell)
        if not compact:
            continue
        if detect_weekday(cell) or re.fullmatch(r"(?:" + UNIT_PATTERN + r")", compact, re.IGNORECASE):
            continue
        if re.search(r"日付|曜日|献立日|使用日|単位|3歳未満|３歳未満|未満児|乳児|食材|食品|材料|品名|食料|料理名", compact):
            break
        name_parts.insert(0, cell)
    return " ".join(name_parts).strip()

def ingredient_name_before_quantity(row_text: str, quantity: str) -> str:
    normalized = normalize_ocr_line(row_text)
    quantity_text = re.escape(normalize_ocr_line(quantity))
    match = re.search(quantity_text, normalized)
    if not match:
        return ""
    before = normalized[:match.start()]
    before = re.sub(r"(?i)(?:" + UNIT_PATTERN + r")\s*$", " ", before)
    return before.strip()

def split_ocr_rows_for_ingredients(text: str) -> list[str]:
    normalized_text = str(text or "").replace("OCR全文", "\n")
    rough_rows = re.split(r"\r?\n|[|｜]", normalized_text)
    rows: list[str] = []
    for raw_row in rough_rows:
        row = normalize_ocr_line(raw_row)
        if row:
            rows.append(row)
    return rows

def split_ocr_cells(line: str) -> list[str]:
    protected = normalize_ocr_line(line)
    cells = [cell.strip() for cell in re.split(r"\t|,|，", protected) if cell.strip()]
    if len(cells) > 1:
        return expand_quantity_unit_cells(cells)
    return expand_quantity_unit_cells(split_marker_tokens(protected))


def expand_quantity_unit_cells(cells: list[str]) -> list[str]:
    expanded: list[str] = []
    for cell in cells:
        match = re.fullmatch(r"([0-9]+(?:[.]?[0-9]+)?)(" + UNIT_PATTERN + r")", normalize_ocr_line(cell), re.IGNORECASE)
        if match:
            expanded.extend([match.group(1), match.group(2)])
        else:
            expanded.append(cell)
    return expanded


def split_marker_tokens(line: str) -> list[str]:
    normalized = normalize_ocr_line(line)
    tokens = [token for token in re.split(r"\s{2,}", normalized) if token]
    if len(tokens) > 1:
        return tokens
    return [token for token in re.split(r"\s+", normalized) if token]

def guess_unit_near_quantity(cells: list[str], quantity_index: int) -> str:
    candidates = []
    if quantity_index + 1 < len(cells):
        candidates.append(cells[quantity_index + 1])
    if quantity_index > 0:
        candidates.append(cells[quantity_index - 1])
    if cells:
        candidates.append(cells[-1])
    return next((value for value in candidates if re.fullmatch(r"(?i)(?:" + UNIT_PATTERN + r")", normalize_ocr_line(value))), "g")

def detect_weekday(value: str) -> str:
    text = normalize_ocr_line(value)
    match = re.search(r"月曜日|火曜日|水曜日|木曜日|金曜日|(?:^|[\s,，])([月火水木金])(?:曜)?(?:[\s,，]|$)", text)
    if not match:
        return ""
    return (match.group(1) or match.group(0))[0]

def add_ingredient_row(rows: list[IngredientRow], seen: set[str], name_value: str, quantity_value: str, unit_value: str, weekday: str) -> None:
    name = clean_ingredient_name(name_value)
    qty = normalize_value(quantity_value).replace(",", "")
    unit = normalize_unit(unit_value)
    if is_suspicious_ingredient_name(name) or is_excluded_ingredient(name):
        return
    if quantity_value == "数量要確認":
        key = f"{weekday}|{normalize_ingredient_for_grouping(name)}|数量要確認|要確認"
        if key not in seen:
            seen.add(key)
            rows.append(IngredientRow(name, "数量要確認", "", weekday))
        return
    try:
        numeric_qty = float(qty)
    except ValueError:
        return
    effective_weekday = weekday if weekday in WEEKDAY_PEOPLE else "月"
    key = f"{effective_weekday}|{normalize_ingredient_for_grouping(name)}|{qty}|{unit}"
    if key in seen or numeric_qty <= 0:
        return
    seen.add(key)
    rows.append(IngredientRow(name, qty, unit, effective_weekday))

def is_excluded_ingredient(name: str) -> bool:
    return bool(EXCLUDED_INGREDIENT_PATTERN.search(re.sub(r"\s+", "", str(name or ""))))

def normalize_ingredient_for_grouping(name: str) -> str:
    return re.sub(r"^(冷凍|国産|生|千切り|皮むき)", "", re.sub(r"[（(].*?[）)]", "", clean_ingredient_name(name))).strip()


_FOOD_MASTER_NAMES: set[str] | None = None


def food_master_names() -> set[str]:
    global _FOOD_MASTER_NAMES
    if _FOOD_MASTER_NAMES is not None:
        return _FOOD_MASTER_NAMES
    path = Path("data/food_master.csv")
    names: set[str] = set()
    if not path.exists():
        return names
    try:
        with path.open(encoding="utf-8-sig", newline="") as csv_file:
            for row in csv.DictReader(csv_file):
                name = normalize_ingredient_for_grouping(row.get("正式名称", ""))
                if name:
                    names.add(name)
    except OSError as exc:
        logging.warning("食材マスタを読めませんでした: %s", exc)
    _FOOD_MASTER_NAMES = names
    return names


def is_in_food_master(name: str) -> bool:
    key = normalize_ingredient_for_grouping(name)
    return bool(key and key in food_master_names())

def fixed_order_key(name: str) -> str | None:
    for _label, key, pattern in FIXED_ORDER_RULES:
        if pattern.search(name):
            return key
    return None

def rounding_order_rule(name: str) -> tuple[str, str, float, dict[str, float]] | None:
    for label, unit, step, base, pattern in ROUNDING_ORDER_RULES:
        if pattern.search(name):
            return label, unit, step, base
    return None

def convert_order_quantity(quantity: str, unit: str) -> tuple[float, str]:
    amount = float(normalize_value(str(quantity)).replace(",", ""))
    normalized_unit = normalize_unit(unit)
    if normalized_unit == "kg":
        return amount * 1000, "g"
    if normalized_unit in {"L", "l", "リットル"}:
        return amount * 1000, "ml"
    if normalized_unit == "グラム":
        return amount, "g"
    if normalized_unit == "cc":
        return amount, "ml"
    return amount, normalized_unit

def convert_to_purchase_unit(quantity: float, unit: str, rule: tuple[str, str, float, dict[str, float]]) -> float:
    if unit == rule[1]:
        return quantity
    base = rule[3].get(unit)
    if base and base > 0:
        return quantity / base
    return quantity

def ceil_to_step(quantity: float, step: float) -> float:
    if step <= 0:
        return quantity
    return int((quantity + step - 0.0000001) / step) * step

def format_order_quantity(quantity: float, unit: str, keep_unit: bool = False) -> tuple[str, str]:
    if not keep_unit and unit == "g" and quantity >= 1000:
        quantity, unit = quantity / 1000, "kg"
    if not keep_unit and unit == "ml" and quantity >= 1000:
        quantity, unit = quantity / 1000, "L"
    if abs(quantity - round(quantity)) < 0.000001:
        return str(int(round(quantity))), unit
    return (f"{quantity:.3f}".rstrip("0").rstrip("."), unit)

def build_order_rows(source_rows: list[IngredientRow]) -> list[IngredientRow]:
    aggregate: dict[tuple[str, str], dict[str, Any]] = {}
    uncertain_rows: list[IngredientRow] = []
    uncertain_seen: set[str] = set()
    for row in source_rows:
        if row.quantity == "数量要確認" or not is_in_food_master(row.name):
            key = normalize_ingredient_for_grouping(row.name)
            if key and key not in uncertain_seen and not is_excluded_ingredient(row.name):
                uncertain_seen.add(key)
                uncertain_rows.append(IngredientRow(row.name, "数量要確認", ""))
            continue
        if is_excluded_ingredient(row.name) or row.weekday not in WEEKDAY_PEOPLE:
            continue
        people = WEEKDAY_PEOPLE[row.weekday]
        try:
            quantity, unit = convert_order_quantity(row.quantity, row.unit)
        except ValueError:
            continue
        quantity *= people
        if quantity <= 0:
            continue
        if rule := rounding_order_rule(row.name):
            quantity = convert_to_purchase_unit(quantity, unit, rule)
            name, unit, step = rule[0], rule[1], rule[2]
        else:
            name, step = normalize_ingredient_for_grouping(row.name), None
        key = (name, unit)
        if key not in aggregate:
            aggregate[key] = {"quantity": 0.0, "step": step}
        aggregate[key]["quantity"] += quantity
        if step:
            aggregate[key]["step"] = step

    order_rows: list[IngredientRow] = []
    for (name, unit), info in aggregate.items():
        quantity = float(info["quantity"])
        if info.get("step"):
            quantity = ceil_to_step(quantity, float(info["step"]))
        if unit == "缶":
            quantity = int(quantity + 0.999999)
        formatted_quantity, formatted_unit = format_order_quantity(quantity, unit, keep_unit=(name == "にんじん"))
        if name and formatted_quantity != "0":
            order_rows.append(IngredientRow(name, formatted_quantity, formatted_unit))
    return sorted(order_rows, key=lambda row: row.name)

def format_ingredient_column(rows: list[IngredientRow], field_name: str) -> str:
    return " / ".join(str(getattr(row, field_name)) for row in rows)

def garbled_ratio(text: str) -> float:
    if not text:
        return 1.0
    suspicious = len(re.findall(r"[�□■◆◇●○]|[\\|]{2,}|[_]{3,}", text))
    return suspicious / max(1, len(text))

def has_unusual_numbers(text: str) -> bool:
    numbers = [normalize_value(item) for item in re.findall(r"[0-9０-９][0-9０-９,，.．]{5,}", text)]
    for number in numbers:
        compact = re.sub(r"[^0-9]", "", number)
        if len(compact) >= 10 and not compact.startswith(("0", "20", "19")):
            return True
        if re.search(r"(\d)\1{5,}", compact):
            return True
    return False

def critical_sets(fields: ExtractedFields) -> tuple[set[str], set[str], set[str]]:
    return (
        {normalize_value(item) for item in fields.dates},
        {normalize_value(item) for item in fields.amounts},
        {normalize_value(item) for item in fields.quantities},
    )

def disagreement(candidates: list[OcrCandidate]) -> bool:
    useful = [candidate for candidate in candidates if candidate.text]
    if len(useful) < 2:
        return False
    base = critical_sets(extract_fields(useful[0].text))
    for candidate in useful[1:]:
        current = critical_sets(extract_fields(candidate.text))
        if current != base and any(base_item or current_item for base_item, current_item in zip(base, current)):
            return True
    return False

def confirmation_reasons(candidate: OcrCandidate, candidates: list[OcrCandidate], fields: ExtractedFields) -> list[str]:
    reasons: list[str] = []
    if candidate.confidence < LOW_CONFIDENCE_THRESHOLD:
        reasons.append("信頼度が低い")
    if candidate.confidence < VERY_LOW_CONFIDENCE_THRESHOLD:
        reasons.append("信頼度がかなり低い")
    if garbled_ratio(candidate.text) > 0.02:
        reasons.append("文字化けが多い可能性")
    if has_unusual_numbers(candidate.text):
        reasons.append("数字が不自然")
    if disagreement(candidates):
        reasons.append("複数OCR結果の数字・日付・金額が不一致")
    if candidate.confidence < LOW_CONFIDENCE_THRESHOLD and (fields.dates or fields.amounts or fields.quantities):
        reasons.append("日付・金額・数量は低信頼度のため要確認")
    if not candidate.text.strip():
        reasons.append("読み取り全文が空")
    return list(dict.fromkeys(reasons))

def save_processed_image(candidate: OcrCandidate, original_path: Path) -> str:
    if candidate.image is None:
        return ""
    safe_stem = re.sub(r"[^0-9A-Za-zぁ-んァ-ン一-龥_-]+", "_", original_path.stem).strip("_") or "image"
    out_path = PROCESSED_DIR / f"{safe_stem}_angle{candidate.angle}.png"
    candidate.image.save(out_path)
    return str(out_path)

def row_for_error(path: Path, message: str) -> list[Any]:
    failure_text = f"【読み取り失敗】{message}"
    return [
        dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        path.name,
        "",
        "原画像直OCR（前処理なし）",
        "",
        failure_text,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        0,
        "要確認",
        f"エラー: {message}",
        "",
    ]

def process_image(path: Path) -> list[Any]:
    result_queue: mp.Queue = mp.Queue(maxsize=1)
    process = mp.Process(target=process_image_worker, args=(path, result_queue), daemon=True)
    process.start()
    process.join(OCR_TIMEOUT_SECONDS)
    if process.is_alive():
        process.terminate()
        process.join(3)
        if process.is_alive():
            process.kill()
            process.join(1)
        logging.error("%s: %s", TIMEOUT_MESSAGE, path)
        return row_for_error(path, TIMEOUT_MESSAGE)
    try:
        ok, payload = result_queue.get_nowait()
    except queue.Empty:
        message = "読み込み失敗: OCR結果を受け取れませんでした"
        logging.error("%s path=%s exitcode=%s", message, path, process.exitcode)
        return row_for_error(path, message)
    if ok:
        return payload
    logging.error("読み込み失敗: %s reason=%s", path, payload)
    return row_for_error(path, f"読み込み失敗: {payload}")

def process_image_worker(path: Path, result_queue: mp.Queue) -> None:
    try:
        if Image is None:
            load_required_dependencies()
            setup()
        result_queue.put((True, process_image_inner(path)))
    except Exception as exc:
        logging.exception("読み込み失敗: %s reason=%s", path, exc)
        result_queue.put((False, str(exc)))

def direct_image_ocr(image: Image.Image) -> str:
    pytesseract = optional_module("pytesseract")
    if pytesseract is None:
        logging.warning("OCRをスキップ: pytesseractが未インストールです")
        return ""
    try:
        return str(pytesseract.image_to_string(image, lang="jpn") or "")
    except Exception as exc:
        logging.exception("原画像OCR失敗: %s", exc)
        return ""


def process_image_inner(path: Path) -> list[Any]:
    logging.info("処理開始: %s", path)
    source = load_image(path)
    raw_text = direct_image_ocr(source)
    best = OcrCandidate("Tesseract", raw_text, 100.0 if raw_text.strip() else 0.0, 0, "原画像直OCR（前処理なし）", source)
    candidates = [best]
    logging.info("原画像OCR全文: %s\n%s", path, raw_text if raw_text else "空")
    print(f"原画像OCR全文 ({path.name}):")
    print(raw_text)
    if not raw_text.strip():
        logging.error("読み取り失敗: %s OCR結果が空", path)
        return row_for_error(path, "OCR結果が空です。抽出せずに停止しました")
    source_ingredient_rows = extract_ingredient_rows(raw_text)
    ingredient_rows = build_order_rows(source_ingredient_rows)
    fields = extract_fields(raw_text)
    reasons = confirmation_reasons(best, candidates, fields)
    notes = reasons + best.notes
    if not ingredient_rows:
        notes.append("読み取り失敗: 食材名・数量・単位を抽出できませんでした")
        logging.error("抽出失敗: %s 食材名・数量・単位=0件 OCR全文=%s", path, raw_text[:500])
    if fields.postal_codes:
        notes.append("郵便番号: " + " / ".join(fields.postal_codes))
    processed_path = save_processed_image(best, path)
    engines = ", ".join(candidate.engine for candidate in candidates)
    logging.info("処理完了: %s confidence=%.1f angle=%s", path, best.confidence, best.angle)
    return [
        dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        path.name,
        f"{best.angle}°",
        best.method,
        engines,
        raw_text,
        format_ingredient_column(ingredient_rows, "name"),
        format_ingredient_column(ingredient_rows, "quantity"),
        format_ingredient_column(ingredient_rows, "unit"),
        " / ".join(fields.dates),
        " / ".join(fields.amounts),
        " / ".join(fields.quantities),
        " / ".join(fields.phones),
        round(best.confidence, 1),
        "要確認" if reasons else "OK",
        " / ".join(notes),
        processed_path,
    ]

def write_excel(rows: list[list[Any]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "OCR転記結果"
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)

    header_fill = PatternFill("solid", fgColor="F97316")
    header_font = Font(color="FFFFFF", bold=True)
    yellow_fill = PatternFill("solid", fgColor="FFF2CC")
    red_fill = PatternFill("solid", fgColor="F4CCCC")

    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for row_index in range(2, sheet.max_row + 1):
        confidence = sheet.cell(row_index, 14).value or 0
        needs_confirmation = sheet.cell(row_index, 15).value == "要確認"
        fill = red_fill if float(confidence) < VERY_LOW_CONFIDENCE_THRESHOLD else yellow_fill if needs_confirmation else None
        for col_index in range(1, sheet.max_column + 1):
            cell = sheet.cell(row_index, col_index)
            cell.alignment = Alignment(vertical="top", wrap_text=(col_index == 6 or col_index == 16))
            if fill:
                cell.fill = fill

    widths = [20, 24, 12, 28, 24, 60, 30, 18, 14, 26, 24, 24, 24, 12, 14, 45, 44]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    workbook.save(EXCEL_PATH)

def main() -> int:
    load_required_dependencies()
    setup()
    logging.info("OCR転記処理を開始しました")
    rows: list[list[Any]] = []
    images = supported_images()
    if not images:
        logging.info("input_images内に対応画像がありません")
    total = len(images)
    for index, path in enumerate(images, start=1):
        percent_before = 0 if total == 0 else round(((index - 1) / total) * 100)
        logging.info("進捗: %s%% (%s/%s)", percent_before, index - 1, total)
        rows.append(process_image(path))
        percent_after = 100 if total == 0 else round((index / total) * 100)
        logging.info("進捗: %s%% (%s/%s)", percent_after, index, total)
    valid_rows = [row for row in rows if len(row) >= len(HEADERS) and str(row[5]).strip()]
    if not valid_rows:
        message = "読み取り失敗: 処理対象画像がないか、失敗理由を作成できませんでした"
        logging.error(message)
        print(message)
        print(f"ログ: {LOG_PATH}")
        return 1
    write_excel(valid_rows)
    logging.info("Excelを保存しました: %s", EXCEL_PATH)
    print(f"完了: {EXCEL_PATH}")
    print(f"ログ: {LOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
