"""固定レイアウト表OCRから食材候補を抽出します。"""

from __future__ import annotations

import re

import pandas as pd

from modules.normalize import normalize_food_name

UNIT_PATTERN = r"kg|KG|ｋｇ|g|G|ｇ|ml|ML|ｍｌ|cc|CC|L|Ｌ|l|個|本|枚|袋|缶|束|玉|パック"
NUMBER_RE = re.compile(r"(?<![0-9.])([0-9]+(?:\.[0-9]+)?)(?![0-9.])")
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
    "注釈",
    "文章",
    "スチコン",
    "オーブン",
    "鍋",
    "フライパン",
    "機器",
]
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


def _normalize_line(value: str) -> str:
    table = str.maketrans("０１２３４５６７８９，．", "0123456789,.")
    return re.sub(r"\s+", " ", str(value or "").translate(table)).strip()


def _should_exclude(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    if not compact or compact.startswith("※"):
        return True
    if re.fullmatch(r"[0-9]+[.)）．、]?", compact):
        return True
    if re.fullmatch(r"[A-Za-z]{1,3}", compact):
        return True
    return any(word in line for word in EXCLUDE_WORDS)


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


def _numbers_from_row(line: str) -> list[str]:
    normalized = _normalize_line(line).replace(",", "")
    return [match.group(1) for match in NUMBER_RE.finditer(normalized)]


def _name_left_of_numbers(line: str) -> str:
    match = NUMBER_RE.search(_normalize_line(line).replace(",", ""))
    if not match:
        return ""
    return _clean_food_name(line[: match.start()])


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
    if not normalized.found_in_master:
        needs_review = True
        notes.append("食材マスタにありません")

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
    review_rows: list[dict[str, object]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = _normalize_line(raw_line)
        if _should_exclude(line):
            continue

        fixed_cells = [cell.strip() for cell in raw_line.split("\t")]
        if fixed_cells and fixed_cells[0] == "固定表行" and len(fixed_cells) >= 5:
            raw_food_name = _clean_food_name(fixed_cells[2])
            if not raw_food_name or _should_exclude(raw_food_name):
                continue
            quantity_text = fixed_cells[3].strip()
            if raw_food_name and quantity_text == "数量要確認":
                review_rows.append(
                    _row_from_values(
                        line_number=line_number,
                        line=line,
                        raw_food_name=raw_food_name,
                        quantity="数量要確認",
                        unit="",
                        master=master,
                        ocr_confidence=ocr_confidence,
                        forced_review_note="3歳未満量を確認してください",
                        section=fixed_cells[1],
                    )
                )
                continue
            if raw_food_name and NUMBER_RE.fullmatch(quantity_text):
                rows.append(
                    _row_from_values(
                        line_number=line_number,
                        line=line,
                        raw_food_name=raw_food_name,
                        quantity=float(quantity_text),
                        unit=fixed_cells[4] or "g",
                        master=master,
                        ocr_confidence=ocr_confidence,
                        section=fixed_cells[1],
                    )
                )
                continue

    rows.extend(review_rows)
    return pd.DataFrame(rows, columns=COLUMNS)
