"""固定レイアウト表OCRから食材候補を抽出します。"""

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
    "注釈",
    "文章",
    "スチコン",
    "オーブン",
    "鍋",
    "フライパン",
    "機器",
]
SENTENCE_MARKERS = ["。", "、", "です", "ます", "してください", "ため", "こと", "もの"]
NOISE_KANA = {"を", "ゑ", "ゐ"}
CORRECTIONS = {
    "豚ひき内": "豚ひき肉",
    "とゃがいも": "じゃがいも",
    "にんん": "にんじん",
    "でちこジャ": "いちごジャム",
    "きゆうり": "きゅうり",
    "せんい": "しょうゆせんべい",
}
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
    for wrong, corrected in CORRECTIONS.items():
        if wrong in compact:
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


def extract_food_candidates(text: str, master: pd.DataFrame, ocr_confidence: float) -> pd.DataFrame:
    """固定X座標OCR済みの行から、3歳未満列の数量だけ候補化します。"""

    rows: list[dict[str, object]] = []
    excluded_rows: list[dict[str, object]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = _normalize_line(raw_line)
        line_reason = _line_exclusion_reason(line)
        if line_reason:
            excluded_rows.append(_excluded_row(line_number, line, line, line_reason))
            continue

        fixed_cells = [cell.strip() for cell in raw_line.split("\t")]
        if not (fixed_cells and fixed_cells[0] == "固定表行" and len(fixed_cells) >= 5):
            excluded_rows.append(_excluded_row(line_number, line, line, "固定表行ではありません"))
            continue

        raw_food_name = _clean_food_name(fixed_cells[2])
        food_reason = _food_name_exclusion_reason(raw_food_name)
        if food_reason:
            excluded_rows.append(_excluded_row(line_number, line, raw_food_name, food_reason))
            continue

        quantity_text = _normalize_line(fixed_cells[3]).replace(",", "")
        if not NUMBER_RE.fullmatch(quantity_text):
            excluded_rows.append(_excluded_row(line_number, line, raw_food_name, "数量がありません"))
            continue

        unit = _normalize_line(fixed_cells[4])
        unit_reason = _unit_exclusion_reason(unit)
        if unit_reason:
            excluded_rows.append(_excluded_row(line_number, line, raw_food_name, unit_reason))
            continue

        rows.append(
            _row_from_values(
                line_number=line_number,
                line=line,
                raw_food_name=raw_food_name,
                quantity=float(quantity_text),
                unit=unit,
                master=master,
                ocr_confidence=ocr_confidence,
                section=fixed_cells[1],
            )
        )

    accepted_foods = [str(row["補正後食材名"]) for row in rows]
    logger.info("OCR採用食材: %s", ", ".join(accepted_foods) if accepted_foods else "なし")
    candidates = pd.DataFrame(rows, columns=COLUMNS)
    candidates.attrs["accepted_foods"] = accepted_foods
    candidates.attrs["excluded_rows"] = pd.DataFrame(excluded_rows, columns=EXCLUDED_COLUMNS)
    return candidates
