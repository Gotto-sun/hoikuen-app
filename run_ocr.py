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
import logging
import re
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError
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
    global Image, ImageEnhance, ImageFilter, ImageOps

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


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
ORIENTATIONS = (0, 90, 180, 270)
LOW_CONFIDENCE_THRESHOLD = 70.0
VERY_LOW_CONFIDENCE_THRESHOLD = 45.0
MAX_IMAGE_WIDTH = 2000
MAX_IMAGE_HEIGHT = 2000
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024
OCR_TIMEOUT_SECONDS = 120
TESSERACT_CALL_TIMEOUT_SECONDS = 25

HEADERS = [
    "処理日時",
    "元ファイル名",
    "採用した向き",
    "補正方法",
    "OCRエンジン",
    "読み取り全文",
    "抽出した日付",
    "抽出した金額",
    "抽出した数量",
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


def load_image(path: Path) -> Image.Image:
    file_size = path.stat().st_size
    if file_size > MAX_FILE_SIZE_BYTES:
        raise ValueError(f"読み込み失敗: ファイルサイズが大きすぎます ({file_size / 1024 / 1024:.1f}MB)")

    with Image.open(path) as opened:
        width, height = opened.size
        logging.info(
            "画像確認: %s size=%.1fMB resolution=%sx%s",
            path,
            file_size / 1024 / 1024,
            width,
            height,
        )
        if width <= 0 or height <= 0:
            raise ValueError("読み込み失敗: 画像の解像度を確認できませんでした")
        image = ImageOps.exif_transpose(opened).convert("RGB")

    if image.width > MAX_IMAGE_WIDTH or image.height > MAX_IMAGE_HEIGHT:
        image.thumbnail((MAX_IMAGE_WIDTH, MAX_IMAGE_HEIGHT), Image.Resampling.LANCZOS)
        logging.info("OCR前縮小: %s resized=%sx%s", path, image.width, image.height)
    return image


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


def upscale(image: Image.Image, min_width: int = 1800) -> Image.Image:
    if image.width >= min_width:
        return image
    ratio = min(min_width / max(1, image.width), MAX_IMAGE_HEIGHT / max(1, image.height))
    if ratio <= 1:
        return image
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
    trimmed = trim_margin(image)
    scaled = upscale(trimmed)
    gray = ImageOps.grayscale(scaled)
    bright = ImageEnhance.Brightness(gray).enhance(1.18)
    contrast = ImageEnhance.Contrast(bright).enhance(1.65)
    gamma = gamma_correct(contrast, 1.25)
    denoised = gamma.filter(ImageFilter.MedianFilter(size=3))
    binary = gamma.point(lambda p: 255 if p > 165 else 0)
    otsu = otsu_threshold(denoised)
    variants = [
        ("余白カット+拡大+グレースケール", gray),
        ("明るさ補正+コントラスト強調", contrast),
        ("ガンマ補正+ノイズ除去", denoised),
        ("二値化", binary),
        ("Otsu threshold", otsu),
    ]
    variants.extend(opencv_preprocess_variants(scaled))
    return variants


def opencv_preprocess_variants(image: Image.Image) -> list[tuple[str, Image.Image]]:
    cv2 = optional_module("cv2")
    np = optional_module("numpy")
    if cv2 is None or np is None:
        return []

    arr = np.array(image)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    gray = deskew_cv2(gray, cv2, np)
    denoised = cv2.fastNlMeansDenoising(gray, None, 18, 7, 21)
    adaptive = cv2.adaptiveThreshold(
        denoised,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        9,
    )
    _, otsu = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return [
        ("傾き補正+ノイズ除去", Image.fromarray(denoised)),
        ("傾き補正+adaptive threshold", Image.fromarray(adaptive)),
        ("傾き補正+Otsu threshold", Image.fromarray(otsu)),
    ]


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

    configs = ["--oem 3 --psm 6", "--oem 3 --psm 11"]
    lang = "jpn+eng"
    best = OcrCandidate("Tesseract", angle=angle, method=method, image=image)
    for config in configs:
        data = run_tesseract_data(pytesseract, image, lang, config)
        if data is None and lang != "eng":
            data = run_tesseract_data(pytesseract, image, "eng", config)
        if data is None:
            continue
        text_parts = []
        confidences = []
        for raw_text, raw_conf in zip(data.get("text", []), data.get("conf", [])):
            text = str(raw_text).strip()
            if text:
                text_parts.append(text)
            try:
                conf = float(raw_conf)
            except ValueError:
                continue
            if conf >= 0:
                confidences.append(conf)
        text = "\n".join(text_parts)
        confidence = statistics.mean(confidences) if confidences else 0.0
        candidate = OcrCandidate("Tesseract", text, confidence, angle, method, image)
        if candidate.score > best.score:
            best = candidate
    if not best.text:
        best.notes.append("Tesseractで文字を読み取れませんでした")
    return best


def run_tesseract_data(pytesseract: Any, image: Image.Image, lang: str, config: str) -> dict[str, Any] | None:
    try:
        return pytesseract.image_to_data(image, lang=lang, config=config, output_type=pytesseract.Output.DICT, timeout=TESSERACT_CALL_TIMEOUT_SECONDS)
    except Exception as exc:
        logging.info("Tesseract失敗 lang=%s config=%s: %s", lang, config, exc)
        return None


def best_tesseract_orientation(source: Image.Image) -> OcrCandidate:
    best = OcrCandidate("Tesseract", notes=[])
    for angle in ORIENTATIONS:
        rotated = source.rotate(angle, expand=True)
        for method, processed in pil_preprocess_variants(rotated):
            candidate = tesseract_candidate(processed, angle, method)
            if candidate.score > best.score:
                best = candidate
    return best


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
    tesseract = best_tesseract_orientation(source)
    candidates = [tesseract]
    if tesseract.confidence < LOW_CONFIDENCE_THRESHOLD:
        for fallback in (easyocr_candidate, paddleocr_candidate):
            candidate = fallback(tesseract.image or source, tesseract.angle, tesseract.method)
            if candidate is not None:
                candidates.append(candidate)
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
    return [
        dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        path.name,
        "",
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
    try:
        return run_with_timeout(lambda: process_image_inner(path), OCR_TIMEOUT_SECONDS, path)
    except TimeoutError:
        message = f"読み込み失敗: OCR処理が{OCR_TIMEOUT_SECONDS}秒を超えました"
        logging.error("%s: %s", message, path)
        return row_for_error(path, message)
    except Exception as exc:
        logging.exception("読み込み失敗: %s reason=%s", path, exc)
        return row_for_error(path, f"読み込み失敗: {exc}")


def run_with_timeout(func: Any, timeout_seconds: int, path: Path) -> Any:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"ocr-{path.stem[:16]}")
    future = executor.submit(func)
    try:
        return future.result(timeout=timeout_seconds)
    except TimeoutError:
        future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        if future.done():
            executor.shutdown(wait=False, cancel_futures=True)


def process_image_inner(path: Path) -> list[Any]:
    logging.info("処理開始: %s", path)
    source = load_image(path)
    best, candidates = collect_candidates(source)
    fields = extract_fields(best.text)
    reasons = confirmation_reasons(best, candidates, fields)
    notes = reasons + best.notes
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
        best.text,
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
        confidence = sheet.cell(row_index, 11).value or 0
        needs_confirmation = sheet.cell(row_index, 12).value == "要確認"
        fill = red_fill if float(confidence) < VERY_LOW_CONFIDENCE_THRESHOLD else yellow_fill if needs_confirmation else None
        for col_index in range(1, sheet.max_column + 1):
            cell = sheet.cell(row_index, col_index)
            cell.alignment = Alignment(vertical="top", wrap_text=(col_index == 6 or col_index == 13))
            if fill:
                cell.fill = fill

    widths = [20, 24, 12, 28, 24, 60, 26, 24, 24, 24, 12, 14, 45, 44]
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
    for path in images:
        rows.append(process_image(path))
    write_excel(rows)
    logging.info("Excelを保存しました: %s", EXCEL_PATH)
    print(f"完了: {EXCEL_PATH}")
    print(f"ログ: {LOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
