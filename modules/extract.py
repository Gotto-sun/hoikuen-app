"""OCR全文から食材候補を抽出します。"""

from __future__ import annotations

import re

import pandas as pd

from modules.normalize import normalize_food_name

UNIT_PATTERN = r"kg|KG|ｋｇ|g|G|ｇ|ml|ML|ｍｌ|cc|CC|L|Ｌ|l|個|本|枚|袋|缶|束|玉|パック"
QUANTITY_RE = re.compile(rf"(?P<quantity>\d+(?:\.\d+)?)\s*(?P<unit>{UNIT_PATTERN})")
EXCLUDE_WORDS = [
    "作り方",
    "手順",
    "炒め",
    "煮る",
    "焼く",
    "蒸す",
    "揚げ",
    "スチコン",
    "オーブン",
    "鍋",
    "フライパン",
]


def _should_exclude(line: str) -> bool:
    if not line.strip():
        return True
    if line.strip().startswith("※"):
        return True
    return any(word in line for word in EXCLUDE_WORDS)


def _clean_food_name(line: str, match: re.Match[str]) -> str:
    food_name = line[: match.start()] + line[match.end() :]
    food_name = re.sub(r"[：:、,。()（）\[\]【】]", " ", food_name)
    food_name = re.sub(r"\s+", " ", food_name)
    return food_name.strip()


def extract_food_candidates(text: str, master: pd.DataFrame, ocr_confidence: float) -> pd.DataFrame:
    """OCR全文から食材候補の表を作ります。"""

    rows: list[dict[str, object]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if _should_exclude(line):
            continue

        quantity_match = QUANTITY_RE.search(line)
        if not quantity_match:
            if any(char.isdigit() for char in line):
                rows.append(
                    {
                        "行番号": line_number,
                        "元の行": line,
                        "食材名": line,
                        "補正後食材名": line,
                        "数量": "",
                        "単位": "",
                        "OCR信頼度": ocr_confidence,
                        "要確認": True,
                        "備考": "数量または単位が見つかりません",
                        "仕入先": "",
                        "発注単位": "",
                    }
                )
            continue

        raw_food_name = _clean_food_name(line, quantity_match)
        quantity = float(quantity_match.group("quantity"))
        unit = quantity_match.group("unit")
        normalized = normalize_food_name(raw_food_name, master)

        notes: list[str] = []
        needs_review = False
        if ocr_confidence < 70:
            needs_review = True
            notes.append("OCR信頼度が低いです")
        if not normalized.found_in_master:
            needs_review = True
            notes.append("食材マスタにありません")
        if not raw_food_name:
            needs_review = True
            notes.append("食材名が空です")

        rows.append(
            {
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
        )

    columns = [
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
    return pd.DataFrame(rows, columns=columns)
