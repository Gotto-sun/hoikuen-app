"""3歳未満児量を基準にした発注用集計処理です。"""

from __future__ import annotations

import math
import re

import pandas as pd

EXCLUDED_INGREDIENT_PATTERN = re.compile(
    r"米$|^米$|精白米|白米|ごはん|御飯|だし|出汁|だし汁|水$|塩$|食塩|砂糖|酢$|"
    r"コンソメ|調味料|調味料全般|しょうゆ$|醤油$|みそ|味噌|油$|サラダ油|ごま油|酒$|みりん|"
    r"こしょう|胡椒|ソース|ケチャップ|マヨネーズ|中華だし|カレー粉|片栗粉|しょうゆせんべい"
)
SENTENCE_PATTERN = re.compile(
    r"作り方|つくり方|手順|説明|説明文|調理方法|下処理|切る|切って|煮る|焼く|炒める|蒸す|"
    r"揚げる|混ぜる|加える|入れる|してください|します|です|ます"
)
STANDARD_NAME_RULES = [
    ("鶏もも(皮なし)", re.compile(r"鶏もも\s*(?:肉)?|鶏モモ(?!肉)|鶏肉|とりもも肉|鶏モモ肉(?:皮なし|（皮なし）)")),
    ("鶏モモ肉", re.compile(r"鶏モモ肉")),
    ("コーン缶", re.compile(r"コーン缶|とうもろこし缶|トウモロコシ缶|コーン")),
    ("ホットケーキミックス", re.compile(r"ホットケーキミックス|ホットケーキMIX|HM")),
    ("バター", re.compile(r"バター|無塩バター")),
    ("牛乳", re.compile(r"牛乳|ミルク")),
    ("キャベツ", re.compile(r"キャベツ|きゃべつ")),
    ("はくさい", re.compile(r"白菜|はくさい")),
    ("にんじん", re.compile(r"にんじん|人参|ニンジン")),
    ("しめじ", re.compile(r"しめじ|シメジ")),
    ("えのきたけ", re.compile(r"えのきたけ|えのき茸|えのき|エノキ")),
    ("しいたけ", re.compile(r"しいたけ|椎茸|シイタケ")),
    ("まいたけ", re.compile(r"まいたけ|舞茸|マイタケ")),
    ("エリンギ", re.compile(r"エリンギ")),
    ("ヨーグルト", re.compile(r"ヨーグルト|牧場の朝")),
    ("みかん缶", re.compile(r"みかん缶|みかんかん|ミカン缶|蜜柑缶")),
    ("ツナ油漬け缶", re.compile(r"ツナ油漬け缶|ツナ油漬け|ツナ油漬|ツナ油づけ|ツナ缶|ツナ")),
    ("缶詰", re.compile(r"缶詰|桃缶|パイン缶")),
    ("ほうれんそう", re.compile(r"ほうれんそう|ほうれん草|ホウレンソウ")),
    ("中華めん", re.compile(r"中華めん|中華麺|中華メン")),
    ("豚肉(もも)", re.compile(r"豚肉もも|豚もも肉|豚モモ肉|豚肉")),
    ("たけのこ", re.compile(r"たけのこ|筍|竹の子")),
    ("かまぼこ", re.compile(r"かまぼこ|蒲鉾")),
    ("ブロッコリー", re.compile(r"ブロッコリー|ブロツコリー")),
    ("チーズ", re.compile(r"チーズ|スライスチーズ|粉チーズ")),
    ("調整豆乳", re.compile(r"調整豆乳|調製豆乳|豆乳")),
    ("鮭(皮なし)", re.compile(r"鮭皮なし|鮭|さけ|サケ")),
    ("さつまいも", re.compile(r"さつまいも|さつま芋|サツマイモ|薩摩芋")),
    ("ベーコン", re.compile(r"ベーコン|ベ-コン")),
    ("わかめふりかけ", re.compile(r"わかめふりかけ|若布ふりかけ")),
    ("さわら", re.compile(r"さわら|鰆|サワラ")),
    ("ちくわ", re.compile(r"ちくわ|竹輪")),
    ("グリーンアスパラガス", re.compile(r"グリーンアスパラガス|グリーンアスパラ|アスパラガス|アスパラ")),
    ("ウインナーソーセージ", re.compile(r"ウインナーソーセージ|ウィンナーソーセージ|ウインナー|ウィンナー|ソーセージ")),
    ("鶏ひき肉", re.compile(r"鶏ひき肉|鶏挽き肉|鶏ミンチ")),
    ("パプリカ(赤)", re.compile(r"パプリカ赤|赤パプリカ")),
    ("しらす干し", re.compile(r"しらす干し|シラス干し|しらす")),
    ("チンゲンサイ", re.compile(r"チンゲンサイ|青梗菜|チンゲン菜")),
    ("オレンジ濃縮果汁", re.compile(r"オレンジ濃縮果汁|オレンジ果汁|オレンジ濃縮|濃縮オレンジ果汁|オレンジのうしゅく果汁")),
    ("オレンジ", re.compile(r"オレンジ|みかん")),
    ("マカロニ", re.compile(r"マカロニ|マカロ二")),
    ("きな粉", re.compile(r"きな粉|きなこ|黄粉")),
    ("油揚げ", re.compile(r"油揚げ|油あげ")),
    ("かぼちゃ", re.compile(r"かぼちゃ|南瓜|カボチャ")),
    ("ごぼう", re.compile(r"ごぼう|牛蒡|ゴボウ")),
    ("フライドポテト", re.compile(r"フライドポテト|ポテトフライ")),
    ("なめこ", re.compile(r"なめこ|ナメコ")),
]
ROUNDING_RULES = [
    ("牛乳", "本", 2.0, {"ml": 450.0, "g": 450.0, "L": 0.45, "l": 0.45}),
    ("キャベツ", "個", 0.25, {"g": 1200.0, "kg": 1.2}),
    ("はくさい", "個", 0.125, {"g": 2000.0, "kg": 2.0}),
    ("しめじ", "袋", 1.0, {"g": 100.0, "kg": 0.1}),
    ("えのきたけ", "袋", 1.0, {"g": 100.0, "kg": 0.1}),
    ("しいたけ", "袋", 1.0, {"g": 100.0, "kg": 0.1}),
    ("まいたけ", "袋", 1.0, {"g": 100.0, "kg": 0.1}),
    ("エリンギ", "袋", 1.0, {"g": 100.0, "kg": 0.1}),
    ("ヨーグルト", "パック", 2.0, {"個": 3.0, "g": 210.0}),
    ("コーン缶", "缶", 1.0, {"缶": 1.0, "個": 1.0, "g": 190.0}),
    ("ツナ油漬け缶", "缶", 1.0, {"缶": 1.0, "個": 1.0}),
    ("みかん缶", "缶", 1.0, {"缶": 1.0, "個": 1.0}),
    ("缶詰", "缶", 1.0, {"缶": 1.0, "個": 1.0}),
]


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _standard_name(name: object) -> str:
    original_compact = _compact(name)
    if "鶏もも(皮なし)" in str(name or "") or "鶏モモ肉(皮なし)" in str(name or "") or "鶏モモ肉皮なし" in original_compact or "鶏モモ肉（皮なし）" in original_compact:
        return "鶏もも(皮なし)"
    cleaned = re.sub(r"[（(].*?[）)]", "", str(name or "")).strip()
    compact = _compact(cleaned)
    for standard, pattern in STANDARD_NAME_RULES:
        if pattern.search(compact):
            return standard
    return cleaned


def _valid_row(row: pd.Series) -> bool:
    name = _standard_name(row.get("補正後食材名", row.get("食材名", "")))
    if not name or EXCLUDED_INGREDIENT_PATTERN.search(_compact(name)) or SENTENCE_PATTERN.search(_compact(name)):
        return False
    quantity = pd.to_numeric(row.get("数量", None), errors="coerce")
    return bool(pd.notna(quantity) and float(quantity) > 0)


def _ceil_to_step(quantity: float, step: float) -> float:
    if step <= 0:
        return quantity
    return math.ceil((quantity - 1e-9) / step) * step


def _format_quantity(quantity: float) -> str:
    if abs(quantity - round(quantity)) < 1e-9:
        return str(int(round(quantity)))
    return f"{quantity:.3f}".rstrip("0").rstrip(".")


def _convert_purchase_quantity(name: str, quantity: float, unit: str) -> tuple[float, str]:
    normalized_unit = str(unit or "g").strip()
    if name == "にんじん":
        if normalized_unit == "kg":
            return quantity * 1000, "g"
        return quantity, "g"
    for rule_name, order_unit, step, base_units in ROUNDING_RULES:
        if name != rule_name:
            continue
        base = base_units.get(normalized_unit)
        converted = quantity / base if base else quantity
        return _ceil_to_step(converted, step), order_unit
    if normalized_unit == "缶":
        return math.ceil(quantity), "缶"
    return quantity, normalized_unit


def aggregate_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    """3歳未満児量だけを食材名ごとに合算し、発注単位に変換します。"""

    if candidates.empty:
        return candidates.copy()

    work = candidates[candidates["要確認"] != True].copy() if "要確認" in candidates.columns else candidates.copy()
    if work.empty:
        return work

    work = work[work.apply(_valid_row, axis=1)].copy()
    if work.empty:
        return pd.DataFrame(columns=["食材名", "単位", "発注単位", "仕入先", "必要量", "OCR信頼度", "要確認", "備考", "発注数量"])

    work["補正後食材名"] = work.apply(lambda row: _standard_name(row.get("補正後食材名", row.get("食材名", ""))), axis=1)
    work["数量"] = pd.to_numeric(work["数量"], errors="coerce")
    grouped = (
        work.groupby(["補正後食材名", "単位", "発注単位", "仕入先"], dropna=False, as_index=False)
        .agg(
            必要量=("数量", "sum"),
            OCR信頼度=("OCR信頼度", "min"),
            要確認=("要確認", "max"),
            備考=("備考", lambda values: "、".join(sorted({str(value) for value in values if str(value)}))),
        )
        .rename(columns={"補正後食材名": "食材名"})
    )
    converted = grouped.apply(
        lambda row: _convert_purchase_quantity(str(row["食材名"]), float(row["必要量"]), str(row["単位"])), axis=1
    )
    grouped["発注数量"] = [_format_quantity(quantity) for quantity, _unit in converted]
    grouped["発注単位"] = [unit for _quantity, unit in converted]
    grouped = grouped[grouped["発注数量"] != "0"]
    return grouped.sort_values("食材名").reset_index(drop=True)
