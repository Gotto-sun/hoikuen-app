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
    "説明",
    "説明文",
    "炒め",
    "煮る",
    "焼く",
    "蒸す",
    "切る",
    "する",
    "します",
    "混ぜる",
    "味を調える",
    "味をととのえる",
    "味を整える",
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
    "ヨ肥外",
    "ョヨンツメ",
    "調味料",
    "調味料全般",
    "だし汁",
    "出汁",
    "だし",
    "水",
    "塩",
    "食塩",
    "砂糖",
    "酢",
    "米",
    "精白米",
]
EXCLUDED_INGREDIENTS = {"しょうゆせんべい", "片栗粉"}
SENTENCE_MARKERS = ["。", "、", "です", "ます", "してください", "ため", "こと", "もの", "たら", "なら"]
NOISE_KANA = {"を", "ゑ", "ゐ"}
FORCED_INGREDIENT_CORRECTIONS = {
    "しょうゆせんべい": ("しょうゆせんべい", "醤油せんべい", "しょうゆせんし", "せんい"),
    "鶏もも(皮なし)": ("鶏もも(皮なし)", "鶏もも肉", "鶏モモ", "鶏モモ肉", "鶏肉", "とりもも肉"),
    "はるさめ": ("はるさめ", "春雨"),
    "ハム": ("ハム", "ロースハム"),
    "ねぎ": ("ねぎ", "ネギ", "葱"),
    "コーン缶": ("コーン缶", "コーン", "とうもろこし缶", "トウモロコシ缶"),
    "ホットケーキミックス": ("ホットケーキミックス", "ホットケーキMIX", "ホットケーキMix", "HM"),
    "バター": ("バター", "無塩バター"),
    "牛乳": ("牛乳", "乳", "ぎゅうにゅう", "ミルク", "Fh"),
    "マーマレード": ("マーマレード", "ママレード"),
    "ひじき": ("ひじき", "ひじ", "ヒジキ"),
    "豚ひき肉": ("豚ひき肉", "豚挽き肉", "豚ひき内", "豚ミンチ", "評Oき琴", "評0き琴"),
    "木綿豆腐": ("木綿豆腐", "木綿とうふ", "木綿豆富", "豆放"),
    "たまねぎ": ("たまねぎ", "玉ねぎ", "玉葱", "タマネギ", "玉ネギ", "たまねを", "療半と"),
    "もやし": ("もやし", "もや", "よやし"),
    "きゅうり": ("きゅうり", "きゆうり", "胡瓜", "きゅうの"),
    "カットわかめ": ("カットわかめ", "カット若布", "わかめ", "若布"),
    "じゃがいも": ("じゃがいも", "ジャガイモ", "じゃが芋", "とゃがいも", "馬鈴薯", "がし"),
    "にんじん": ("にんじん", "にんん", "にんヒじん", "人参", "ニンジン", "0 80 66 9", "080669"),
    "食パン": ("食パン", "a emw", "aemw"),
    "いちごジャム": ("いちごジャム", "苺ジャム", "でちこジャ", "60 42 7", "60427"),
    "キャベツ": ("キャベツ", "キャヘツ", "きゃべつ"),
    "ほうれんそう": ("ほうれんそう", "ほうれん草", "ホウレンソウ"),
    "しめじ": ("しめじ", "シメジ"),
    "中華めん": ("中華めん", "中華麺", "中華メン"),
    "豚肉(もも)": ("豚肉(もも)", "豚もも肉", "豚肉もも", "豚モモ肉", "豚肉", "豚肉（もも）", "豚もも", "豚肉もも肉"),
    "はくさい": ("はくさい", "白菜"),
    "たけのこ": ("たけのこ", "筍", "竹の子"),
    "かまぼこ": ("かまぼこ", "蒲鉾"),
    "ブロッコリー": ("ブロッコリー", "ブロコリー", "ブロッコリ", "プロッコリー", "ブロツコリー"),
    "クリームコーン缶": ("クリームコーン缶", "クリームコーン", "コーン缶", "クリームコーンかん", "クリームコーン館"),
    "豆乳": ("豆乳", "とうにゅう", "トウニュウ", "豆孔", "豆礼"),
    "オレンジ濃縮果汁": ("オレンジ濃縮果汁", "オレンジ果汁", "オレンジ濃縮", "濃縮オレンジ果汁", "オレンジのうしゅく果汁"),
    "粉かんてん": ("粉かんてん", "粉寒天", "かんてん", "寒天", "粉かんでん"),
    "みかん缶": ("みかん缶", "みかん", "みかんかん", "ミカン缶", "蜜柑缶"),
    "SBカレーフレーク": ("SBカレーフレーク", "S&Bカレーフレーク", "ＳＢカレーフレーク", "Ｓ＆Ｂカレーフレーク", "カレーフレーク", "SBカレー"),
    "だいこん": ("だいこん", "大根", "ダイコン", "だいこ", "たいこん"),
    "ツナ油漬け缶": ("ツナ油漬け缶", "ツナ油漬", "ツナ油漬け", "ツナ油づけ", "ツナ", "ツナ缶"),
    "パイン缶": ("パイン缶", "パイン", "パイナップル缶", "パインかん", "パイナップル"),
    "スパゲティ": ("スパゲティ", "スパゲッティ", "スパゲテイ", "パスタ"),
    "パイシート(冷凍)": ("パイシート(冷凍)", "パイシート（冷凍）", "パイシート", "冷凍パイシート", "バイシート"),
    "チーズ": ("チーズ", "スライスチーズ", "粉チーズ"),
    "ヨーグルト": ("ヨーグルト", "牧場の朝"),
    "調整豆乳": ("調整豆乳", "調製豆乳"),
    "鮭(皮なし)": ("鮭(皮なし)", "鮭", "さけ", "サケ"),
    "さつまいも": ("さつまいも", "さつま芋", "サツマイモ", "薩摩芋"),
    "ベーコン": ("ベーコン", "ベ-コン"),
    "えのきたけ": ("えのきたけ", "えのき", "えのき茸", "エノキ"),
    "わかめふりかけ": ("わかめふりかけ", "若布ふりかけ"),
    "さわら": ("さわら", "鰆", "サワラ"),
    "ちくわ": ("ちくわ", "竹輪"),
    "グリーンアスパラガス": ("グリーンアスパラガス", "グリーンアスパラ", "アスパラガス", "アスパラ"),
    "ウインナーソーセージ": ("ウインナーソーセージ", "ウィンナーソーセージ", "ウインナー", "ウィンナー", "ソーセージ"),
    "鶏ひき肉": ("鶏ひき肉", "鶏挽き肉", "鶏ミンチ"),
    "パプリカ(赤)": ("パプリカ(赤)", "赤パプリカ", "パプリカ赤"),
    "しらす干し": ("しらす干し", "シラス干し", "しらす"),
    "チンゲンサイ": ("チンゲンサイ", "青梗菜", "チンゲン菜"),
    "オレンジ": ("オレンジ",),
    "マカロニ": ("マカロニ", "マカロ二"),
    "きな粉": ("きな粉", "きなこ", "黄粉"),
    "油揚げ": ("油揚げ", "油あげ"),
    "かぼちゃ": ("かぼちゃ", "南瓜", "カボチャ"),
    "ごぼう": ("ごぼう", "牛蒡", "ゴボウ"),
    "フライドポテト": ("フライドポテト", "ポテトフライ"),
    "なめこ": ("なめこ", "ナメコ"),
    "しいたけ": ("しいたけ", "椎茸", "シイタケ"),
    "まいたけ": ("まいたけ", "舞茸", "マイタケ"),
    "エリンギ": ("エリンギ",),
    "缶詰": ("缶詰", "桃缶"),
}
WEEKDAY_PATTERN = re.compile(r"月曜日|火曜日|水曜日|木曜日|金曜日|[月火水木金](?:曜)?")

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
    "曜日",
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
    for wrong, corrected in sorted(CORRECTIONS.items(), key=lambda item: len(_compact_for_match(item[0])), reverse=True):
        if wrong and _compact_for_match(wrong) in compact:
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
    weekday: str = "",
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
        "曜日": weekday or _weekday_for_line(line),
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
    return ""


def _line_has_under_three_label(line: str) -> bool:
    compact = _compact_for_match(line)
    return re.search(r"3歳未満|３歳未満|未満児|乳児", compact) is not None and re.search(r"人数|対象|区分|年齢", compact) is None


def _line_has_total_usage_label(line: str) -> bool:
    compact = _compact_for_match(line)
    return re.search(r"総使用量|総量|合計|使用量合計|総重量", compact) is not None and not _line_has_under_three_label(line)


def _quantity_after_under_three_label(line: str) -> tuple[str, str]:
    normalized = _normalize_line(line).replace(",", "")
    match = re.search(
        r"(?:3歳未満|３歳未満|未満児|乳児)[^0-9０-９]*([0-9]+(?:\.[0-9]+)?)(?:\s*(" + UNIT_PATTERN + r"))?",
        normalized,
    )
    if not match:
        return "", ""
    return match.group(1), match.group(2) or "g"


def _weekday_for_line(line: str) -> str:
    match = WEEKDAY_PATTERN.search(_compact_for_match(line))
    return match.group(0)[:1] if match else ""


def _weekday_near_ocr_line(lines: list[tuple[int, str]], index: int) -> str:
    for row_index in range(index, -1, -1):
        weekday = _weekday_for_line(lines[row_index][1])
        if weekday:
            return weekday
    return ""


def _unit_after_quantity(line: str, quantity: str) -> str:
    normalized = _normalize_line(line).replace(",", "")
    match = re.search(r"(?<![0-9.])" + re.escape(quantity) + r"(?![0-9.])\s*(" + UNIT_PATTERN + r")", normalized)
    return match.group(1) if match else "g"


def _quantity_near_ocr_line(lines: list[tuple[int, str]], index: int) -> tuple[str, str]:
    if index >= len(lines):
        return "", ""
    line = lines[index][1]
    if _line_has_total_usage_label(line):
        return "", ""
    quantity, unit = _quantity_after_under_three_label(line)
    if not quantity:
        quantity = _under_three_quantity_from_numbers(_numbers_from_row(line))
        unit = _unit_after_quantity(line, quantity) if quantity else ""
    if not quantity:
        return "", ""
    try:
        return (quantity, unit or "g") if float(quantity) > 0 else ("", "")
    except ValueError:
        return "", ""


def extract_food_candidates(text: str, master: pd.DataFrame, ocr_confidence: float) -> pd.DataFrame:
    """固定表OCRを使わず、原画像OCR全文から補正辞書で食材と3歳未満量を抽出します。"""

    rows: list[dict[str, object]] = []
    excluded_rows: list[dict[str, object]] = []
    seen: set[str] = set()
    lines = _ocr_lines(text)

    for index, (line_number, line) in enumerate(lines):
        line_reason = _line_exclusion_reason(line)
        if line_reason:
            excluded_rows.append(_excluded_row(line_number, line, line, line_reason))
            continue

        corrected_name = _correct_name_from_ocr_line(line)
        if not corrected_name:
            excluded_rows.append(_excluded_row(line_number, line, line, "補正辞書に一致しません"))
            continue

        if corrected_name in EXCLUDED_INGREDIENTS or any(word == corrected_name for word in EXCLUDE_WORDS):
            excluded_rows.append(_excluded_row(line_number, line, corrected_name, "除外対象の食材です"))
            continue

        quantity_text, unit_text = _quantity_near_ocr_line(lines, index)
        if not NUMBER_RE.fullmatch(quantity_text):
            excluded_rows.append(_excluded_row(line_number, line, corrected_name, "3歳未満の数値を取得できません"))
            continue

        normalized = normalize_food_name(corrected_name, master)
        if not normalized.found_in_master:
            excluded_rows.append(_excluded_row(line_number, line, corrected_name, "食材マスタに一致しません"))
            continue

        key = f"{corrected_name}|{quantity_text}|{unit_text or 'g'}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            _row_from_values(
                line_number=line_number,
                line=line,
                raw_food_name=corrected_name,
                quantity=float(quantity_text),
                unit=unit_text or "g",
                master=master,
                ocr_confidence=ocr_confidence,
                section="OCR全文",
                weekday=_weekday_near_ocr_line(lines, index),
            )
        )

    accepted_foods = [str(row["補正後食材名"]) for row in rows]
    logger.info("OCR採用食材: %s", ", ".join(accepted_foods) if accepted_foods else "なし")
    candidates = pd.DataFrame(rows, columns=COLUMNS)
    candidates.attrs["accepted_foods"] = accepted_foods
    candidates.attrs["excluded_rows"] = pd.DataFrame(excluded_rows, columns=EXCLUDED_COLUMNS)
    return candidates
