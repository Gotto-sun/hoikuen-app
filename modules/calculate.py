"""3歳未満児量を基準にした発注用集計処理です。"""

from __future__ import annotations

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
    ("鶏もも(皮なし)", re.compile(r"鶏もも皮なし|鶏もも肉|鶏モモ(?!肉)|鶏モモ肉|鶏肉|とりもも肉")),
    ("クリームコーン缶", re.compile(r"クリームコーン缶|クリームコーン|クリームコーンかん|クリームコーン館")),
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
    ("きゅうり", re.compile(r"きゅうり|きゆうり|胡瓜|きゅうの")),
    ("もやし", re.compile(r"もやし|よやし|(?:^|[^ぁ-んァ-ン一-龥])もや(?:$|[^ぁ-んァ-ン一-龥])")),
    ("木綿豆腐", re.compile(r"木綿豆腐|木綿とうふ|木綿豆富|豆放")),
    ("みかん缶", re.compile(r"みかん缶|ミカン缶|蜜柑缶|みかんかん")),
    ("ツナ油漬け缶", re.compile(r"ツナ油漬け缶|ツナ油漬け?|ツナ油づけ|ツナ缶|ツナ")),
    ("パイン缶", re.compile(r"パイン缶|パインかん|パイナップル缶|パイン|パイナップル")),
    ("缶詰", re.compile(r"缶詰|桃缶")),
    ("ほうれんそう", re.compile(r"ほうれんそう|ほうれん草|ホウレンソウ")),
    ("中華めん", re.compile(r"中華めん|中華麺|中華メン")),
    ("豚肉(もも)", re.compile(r"豚肉もも|豚もも肉|豚モモ肉|豚肉")),
    ("たけのこ", re.compile(r"たけのこ|筍|竹の子")),
    ("かまぼこ", re.compile(r"かまぼこ|蒲鉾")),
    ("ブロッコリー", re.compile(r"ブロッコリー|ブロツコリー|ブロコリー|ブロッコリ|プロッコリー")),
    ("チーズ", re.compile(r"チーズ|スライスチーズ|粉チーズ")),
    ("豆乳", re.compile(r"豆乳|とうにゅう|トウニュウ|豆孔|豆礼")),
    ("オレンジ濃縮果汁", re.compile(r"オレンジ濃縮果汁|オレンジ果汁|オレンジ濃縮|濃縮オレンジ果汁|オレンジのうしゅく果汁")),
    ("粉かんてん", re.compile(r"粉かんてん|粉寒天|かんてん|寒天|粉かんでん")),
    ("SBカレーフレーク", re.compile(r"SBカレーフレーク|S&Bカレーフレーク|ＳＢカレーフレーク|Ｓ＆Ｂカレーフレーク|カレーフレーク|SBカレー")),
    ("だいこん", re.compile(r"だいこん|大根|ダイコン|だいこ|たいこん")),
    ("スパゲティ", re.compile(r"スパゲティ|スパゲッティ|スパゲテイ|パスタ")),
    ("パイシート(冷凍)", re.compile(r"パイシート冷凍|冷凍パイシート|パイシート|バイシート")),
    ("調整豆乳", re.compile(r"調整豆乳|調製豆乳")),
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
    ("オレンジ", re.compile(r"オレンジ")),
    ("マカロニ", re.compile(r"マカロニ|マカロ二")),
    ("きな粉", re.compile(r"きな粉|きなこ|黄粉")),
    ("油揚げ", re.compile(r"油揚げ|油あげ")),
    ("かぼちゃ", re.compile(r"かぼちゃ|南瓜|カボチャ")),
    ("ごぼう", re.compile(r"ごぼう|牛蒡|ゴボウ")),
    ("フライドポテト", re.compile(r"フライドポテト|ポテトフライ")),
    ("なめこ", re.compile(r"なめこ|ナメコ")),
]

def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _standard_name(name: object) -> str:
    original_compact = _compact(name)
    if "鶏もも(皮なし)" in str(name or "") or "鶏もも皮なし" in original_compact or "鶏モモ肉皮なし" in original_compact:
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


def _format_quantity(quantity: float) -> str:
    if abs(quantity - round(quantity)) < 1e-9:
        return str(int(round(quantity)))
    return f"{quantity:.3f}".rstrip("0").rstrip(".")


def _people_count(weekday: object) -> int:
    return 5 if str(weekday or "").strip().startswith("月") else 7


def aggregate_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    """3歳未満児量を人数分に再計算し、食材名と単位ごとに合算します。"""

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
    work["人数"] = work["曜日"].apply(_people_count) if "曜日" in work.columns else 7
    work["数量"] = work["数量"] * work["人数"]
    grouped = (
        work.groupby(["補正後食材名", "単位", "仕入先"], dropna=False, as_index=False)
        .agg(
            必要量=("数量", "sum"),
            OCR信頼度=("OCR信頼度", "min"),
            要確認=("要確認", "max"),
            備考=("備考", lambda values: "、".join(sorted({str(value) for value in values if str(value)}))),
        )
        .rename(columns={"補正後食材名": "食材名"})
    )
    grouped["発注数量"] = grouped["必要量"].apply(lambda quantity: _format_quantity(float(quantity)))
    grouped["発注単位"] = grouped["単位"]
    grouped = grouped[grouped["発注数量"] != "0"]
    return grouped.sort_values("食材名").reset_index(drop=True)
