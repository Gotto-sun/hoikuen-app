"""原画像OCR全文から食材候補を抽出します。"""

from __future__ import annotations

import logging
import re

import pandas as pd

from modules.normalize import normalize_food_name

UNIT_PATTERN = r"kg|KG|ｋｇ|g|G|ｇ|ml|ML|ｍｌ|cc|CC|L|Ｌ|l|個|本|枚|袋|缶|束|玉|パック"
NUMBER_RE = re.compile(r"(?<![0-9.])([0-9]+(?:\.[0-9]+)?)(?![0-9.])")
JAPANESE_RE = re.compile(r"[ぁ-んァ-ヶ一-龯々〆〇]")
ASCII_RE = re.compile(r"[A-Za-z]")
SYMBOL_ONLY_RE = re.compile(r"[\W_ー－―]+", re.UNICODE)
EXCLUDE_WORDS = [
    "作り方",
    "つくり方",
    "手順",
    "炒め",
    "煮る",
    "焼く",
    "蒸す",
    "揚げ",
    "切る",
    "する",
    "します",
    "混ぜる",
    "入れる",
    "加える",
    "ください",
    "です",
    "ます",
    "※",
    "注釈",
    "文章",
    "スチコン",
    "オーブン",
    "鍋",
    "フライパン",
    "機器",
    "コンソメ",
    "水",
    "塩",
    "食塩",
    "砂糖",
    "酢",
    "米",
    "精白米",
]
SENTENCE_MARKERS = ["。", "、", "です", "ます", "してください", "ため", "こと", "もの"]
NOISE_KANA = {"を", "ゑ", "ゐ"}
FORCED_INGREDIENT_CORRECTIONS = {
    "しょうゆせんべい": ("しょうゆせんべい", "しょうゆせんし", "せんい"),
    "牛乳": ("牛乳", "乳", "ぎゅうにゅう", "ミルク", "Fh"),
    "ひじき": ("ひじき", "ひじ", "ヒジキ"),
    "豚ひき肉": ("豚ひき肉", "豚挽き肉", "豚ひき内", "豚ミンチ", "評Oき琴", "評0き琴"),
    "木綿豆腐": ("木綿豆腐", "木綿とうふ", "木綿豆富", "豆放"),
    "たまねぎ": ("たまねぎ", "玉ねぎ", "玉葱", "タマネギ", "玉ネギ", "たまねを", "療半と"),
    "片栗粉": ("片栗粉", "片栗", "片困粉", "用本明", "有本塊"),
    "もやし": ("もやし", "もや"),
    "きゅうり": ("きゅうり", "きゆうり", "胡瓜", "きゅうの"),
    "カットわかめ": ("カットわかめ", "カット若布", "わかめ", "若布"),
    "じゃがいも": ("じゃがいも", "ジャガイモ", "じゃが芋", "とゃがいも", "馬鈴薯", "がし"),
    "にんじん": ("にんじん", "にんん", "にんヒじん", "人参", "ニンジン", "0 80 66 9", "080669"),
    "食パン": ("食パン", "a emw", "aemw"),
    "いちごジャム": ("いちごジャム", "苺ジャム", "でちこジャ", "60 42 7", "60427"),
}
CORRECTIONS = {alias: label for label, aliases in FORCED_INGREDIENT_CORRECTIONS.items() for alias in aliases}
COLUMNS = [
    "区分",
    "行番号",
    "元の行",
    "食材名",
    "補正後食材名",
    "数量",
    "単位",
    "OCR信頼度",
    "要確認",
    "備考",
    "仕入先",
    "発注単位",
]
EXCLUDED_COLUMNS = ["行番号", "元の行", "除外対象", "除外理由"]
logger = logging.getLogger(__name__)


def _normalize_line(value: str) -> str:
    table = str.maketrans("０１２３４５６７８９，．", "0123456789,.")
    return re.sub(r"\s+", " ", str(value or "").translate(table)).strip()


def _should_exclude(line: str) -> bool:
    return bool(_line_exclusion_reason(line))


def _line_exclusion_reason(line: str) -> str:
    compact = re.sub(r"\s+", "", line)
    if not compact or compact.startswith("※"):
        return "空行または注釈行"
    if re.fullmatch(r"[0-9]+[.)）．、]?", compact):
        return "数字だけの行"
    if re.fullmatch(r"[A-Za-z]+", compact):
        return "英字のみ"
    if any(word in line for word in EXCLUDE_WORDS):
        return "調理手順・文章に使う語を含みます"
    return ""


def _correct_name(name: str) -> str:
    compact = re.sub(r"\s+", "", name)
    if compact == "乳":
        return "牛乳"
    if compact.startswith("ひじ"):
        return "ひじき"
    for wrong, corrected in CORRECTIONS.items():
        if wrong and wrong in compact:
            return corrected
    return name.strip()


def _clean_food_name(raw_name: str) -> str:
    food_name = re.sub(r"[：:、,。()（）\[\]【】]", " ", raw_name)
    food_name = re.sub(r"^[□■◇◆☑✓・*\-－—\s]+", "", food_name)
    food_name = re.sub(r"^[0-9]+[.)）．、\s]+", "", food_name)
    food_name = re.sub(r"\s+", " ", food_name)
    return _correct_name(food_name.strip())


def _japanese_text_length(value: str) -> int:
    return len(JAPANESE_RE.findall(value))


def _food_name_exclusion_reason(name: str) -> str:
    compact = re.sub(r"\s+", "", name)
    if not compact:
        return "食材名が空です"
    if SYMBOL_ONLY_RE.fullmatch(compact):
        return "記号のみです"
    if ASCII_RE.search(compact):
        return "英字を含むOCRノイズです"
    if not JAPANESE_RE.search(compact):
        return "ひらがな・カタカナ・漢字がありません"
    if _japanese_text_length(compact) < 2:
        return "食材名が1文字以下です"
    if any(noise in compact for noise in NOISE_KANA):
        return "意味不明なかな文字を含みます"
    if any(word == compact or word in compact for word in EXCLUDE_WORDS):
        return "動詞・調理手順の語です"
    if any(marker in compact for marker in SENTENCE_MARKERS) or len(compact) >= 18:
        return "文章の可能性があります"
    if re.fullmatch(r"([ぁ-ん])\1{2,}", compact):
        return "意味不明な文字列です"
    if re.search(r"[ー－―]{2,}|っ[ー－―]+", compact):
        return "記号ノイズです"
    return ""


def _unit_exclusion_reason(unit: str) -> str:
    if not unit.strip():
        return "単位がありません"
    if not re.fullmatch(UNIT_PATTERN, unit.strip()):
        return "対応外の単位です"
    return ""


def _numbers_from_row(line: str) -> list[str]:
    normalized = _normalize_line(line).replace(",", "")
    return [match.group(1) for match in NUMBER_RE.finditer(normalized)]


def _name_left_of_numbers(line: str) -> str:
    match = NUMBER_RE.search(_normalize_line(line).replace(",", ""))
    if not match:
        return ""
    return _clean_food_name(line[: match.start()])


def _excluded_row(line_number: int, line: str, target: str, reason: str) -> dict[str, object]:
    logger.info("OCR食材候補を除外: 行%s target=%s reason=%s", line_number, target, reason)
    return {"行番号": line_number, "元の行": line, "除外対象": target, "除外理由": reason}


def _row_from_values(
    *,
    line_number: int,
    line: str,
    raw_food_name: str,
    quantity: float | str,
    unit: str,
    master: pd.DataFrame,
    ocr_confidence: float,
    forced_review_note: str = "",
    section: str = "",
) -> dict[str, object]:
    normalized = normalize_food_name(raw_food_name, master)
    notes: list[str] = []
    needs_review = False
    if forced_review_note:
        needs_review = True
        notes.append(forced_review_note)
    if ocr_confidence < 70:
        needs_review = True
        notes.append("OCR信頼度が低いです")
    if normalized.match_type == "類似補正":
        notes.append(f"食材マスタの類似名に補正しました（距離{normalized.distance}）")
    if not normalized.found_in_master:
        needs_review = True
        notes.append("食材マスタにありません。要確認")

    return {
        "区分": section,
        "行番号": line_number,
        "元の行": line,
        "食材名": raw_food_name,
        "補正後食材名": normalized.name,
        "数量": quantity,
        "単位": unit,
        "OCR信頼度": ocr_confidence,
        "要確認": needs_review,
        "備考": "、".join(notes),
        "仕入先": normalized.supplier,
        "発注単位": normalized.order_unit,
    }


def _ocr_lines(text: str) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    for line_number, raw_line in enumerate(str(text or "").splitlines(), start=1):
        line = _normalize_line(raw_line)
        if line:
            rows.append((line_number, line))
    return rows


def _compact_for_match(value: str) -> str:
    return re.sub(r"\s+", "", _normalize_line(value))


def _correct_name_from_ocr_line(line: str) -> str:
    compact = _compact_for_match(line)
    if not compact:
        return ""
    for alias, corrected in sorted(CORRECTIONS.items(), key=lambda item: len(_compact_for_match(item[0])), reverse=True):
        alias_compact = _compact_for_match(alias)
        if not alias_compact:
            continue
        if alias_compact.isdigit():
            if compact == alias_compact:
                return corrected
            continue
        if alias_compact in compact:
            return corrected
    return ""


def _under_three_quantity_from_numbers(numbers: list[str]) -> str:
    if len(numbers) >= 4:
        return numbers[2]
    if len(numbers) >= 3:
        return numbers[1]
    if len(numbers) >= 2:
        return numbers[1]
    if numbers:
        return numbers[0]
    return ""


def _quantity_near_ocr_line(lines: list[tuple[int, str]], index: int) -> str:
    for row_index in (index, index + 1):
        if row_index >= len(lines):
            continue
        numbers = _numbers_from_row(lines[row_index][1])
        quantity = _under_three_quantity_from_numbers(numbers)
        if not quantity:
            continue
        try:
            if float(quantity) > 0:
                return quantity
        except ValueError:
            continue
    return ""


def extract_food_candidates(text: str, master: pd.DataFrame, ocr_confidence: float) -> pd.DataFrame:
    """固定表OCRを使わず、原画像OCR全文から補正辞書で食材と3歳未満量を抽出します。"""

    rows: list[dict[str, object]] = []
    excluded_rows: list[dict[str, object]] = []
    seen: set[str] = set()
    lines = _ocr_lines(text)

    for index, (line_number, line) in enumerate(lines):
        corrected_name = _correct_name_from_ocr_line(line)
        if not corrected_name:
            line_reason = _line_exclusion_reason(line) or "補正辞書に一致しません"
            excluded_rows.append(_excluded_row(line_number, line, line, line_reason))
            continue

        if _line_exclusion_reason(corrected_name) or any(word == corrected_name for word in EXCLUDE_WORDS):
            excluded_rows.append(_excluded_row(line_number, line, corrected_name, "除外対象の食材です"))
            continue

        quantity_text = _quantity_near_ocr_line(lines, index)
        if not NUMBER_RE.fullmatch(quantity_text):
            excluded_rows.append(_excluded_row(line_number, line, corrected_name, "3歳未満の数値を取得できません"))
            continue

        key = f"{corrected_name}|{quantity_text}|g"
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            _row_from_values(
                line_number=line_number,
                line=line,
                raw_food_name=corrected_name,
                quantity=float(quantity_text),
                unit="g",
                master=master,
                ocr_confidence=ocr_confidence,
                section="OCR全文",
            )
        )

    accepted_foods = [str(row["補正後食材名"]) for row in rows]
    logger.info("OCR採用食材: %s", ", ".join(accepted_foods) if accepted_foods else "なし")
    candidates = pd.DataFrame(rows, columns=COLUMNS)
    candidates.attrs["accepted_foods"] = accepted_foods
    candidates.attrs["excluded_rows"] = pd.DataFrame(excluded_rows, columns=EXCLUDED_COLUMNS)
    return candidates
