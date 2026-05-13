"""食材名の表記ゆれ補正です。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class NormalizedFood:
    name: str
    supplier: str
    order_unit: str
    loss_rate: float
    found_in_master: bool
    match_type: str = "未一致"
    distance: int | None = None


REQUIRED_COLUMNS = ["正式名称", "別名", "ロス率", "発注単位", "仕入先"]
MAX_LEVENSHTEIN_DISTANCE = 2


def ensure_food_master(path: Path) -> None:
    """食材マスタがない場合にサンプルを作ります。"""

    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '正式名称,別名,ロス率,発注単位,仕入先' "\n"
        'にんじん,人参;ニンジン;人じん;にんん;にんヒじん;0 80 66 9;080669,1.10,kg,青果業者' "\n"
        'たまねぎ,玉ねぎ;玉葱;玉ねき;タマネギ;玉ネギ;たまねを;療半と,1.08,kg,青果業者' "\n"
        'じゃがいも,じゃが芋;ジャガイモ;とゃがいも;馬鈴薯;がし,1.10,kg,青果業者' "\n"
        'カットわかめ,わかめ;若布;カット若布,1.00,袋,食品業者' "\n"
        'ひじき,ひじ;ヒジキ,1.00,kg,食品業者' "\n"
        '牛乳,乳;ぎゅうにゅう;ミルク;Fh,1.00,L,乳製品業者' "\n"
        'キャベツ,キャヘツ;きゃべつ,1.15,kg,青果業者' "\n"
        '豚ひき肉,豚挽き肉;豚ひき内;豚ミンチ;評Oき琴;評0き琴,1.05,kg,精肉業者' "\n"
        '木綿豆腐,木綿とうふ;木綿豆富;豆放,1.00,丁,食品業者' "\n"
        'もやし,もや;よやし,1.00,袋,青果業者' "\n"
        'きゅうり,きゆうり;胡瓜;きゅうの,1.10,kg,青果業者' "\n"
        '食パン,a emw;aemw,1.00,斤,食品業者' "\n"
        'いちごジャム,でちこジャ;苺ジャム;60 42 7;60427,1.00,個,食品業者' "\n"
        'はくさい,白菜,1.15,kg,青果業者' "\n"
        'しめじ,シメジ;きのこ,1.00,袋,青果業者' "\n"
        'えのきたけ,えのき;えのき茸;エノキ;きのこ,1.00,袋,青果業者' "\n"
        'しいたけ,椎茸;シイタケ;きのこ,1.00,袋,青果業者' "\n"
        'まいたけ,舞茸;マイタケ;きのこ,1.00,袋,青果業者' "\n"
        'エリンギ,きのこ,1.00,袋,青果業者' "\n"
        'ヨーグルト,牧場の朝,1.00,パック,乳製品業者' "\n"
        '缶詰,ツナ;みかん缶;桃缶;パイン缶,1.00,缶,食品業者' "\n"
        '鶏モモ肉,鶏モモ肉,1.05,kg,精肉業者' "\n"
        '鶏モモ肉(皮なし),鶏もも肉;鶏モモ;鶏肉;とりもも肉,1.05,kg,精肉業者' "\n"
        'はるさめ,春雨,1.00,袋,食品業者' "\n"
        'ハム,ロースハム,1.00,パック,食品業者' "\n"
        'ねぎ,ネギ;葱,1.10,kg,青果業者' "\n"
        'コーン缶,コーン;とうもろこし缶;トウモロコシ缶,1.00,缶,食品業者' "\n"
        'ホットケーキミックス,HM;ホットケーキMIX;ホットケーキMix,1.00,袋,食品業者' "\n"
        'バター,無塩バター,1.00,個,乳製品業者' "\n"
        'マーマレード,ママレード,1.00,個,食品業者' "\n"
        'ほうれんそう,ほうれん草;ホウレンソウ,1.10,kg,青果業者' "\n"
        '中華めん,中華麺;中華メン,1.00,袋,食品業者' "\n"
        '豚肉(もも),豚もも肉;豚肉もも;豚モモ肉;豚肉;豚肉（もも）;豚もも;豚肉もも肉,1.05,kg,精肉業者' "\n"
        'たけのこ,筍;竹の子,1.00,kg,青果業者' "\n"
        'かまぼこ,蒲鉾,1.00,本,食品業者' "\n"
        'ブロッコリー,ブロツコリー;ブロコリー;ブロッコリ;プロッコリー,1.10,kg,青果業者' "\n"
        'チーズ,スライスチーズ;粉チーズ,1.00,個,乳製品業者' "\n"
        '調整豆乳,豆乳;調製豆乳,1.00,L,食品業者' "\n"
        '鮭(皮なし),鮭;さけ;サケ,1.00,kg,鮮魚業者' "\n"
        'さつまいも,さつま芋;サツマイモ;薩摩芋,1.10,kg,青果業者' "\n"
        'ベーコン,ベ-コン,1.00,パック,食品業者' "\n"
        'わかめふりかけ,若布ふりかけ,1.00,袋,食品業者' "\n"
        'さわら,鰆;サワラ,1.00,kg,鮮魚業者' "\n"
        'ちくわ,竹輪,1.00,本,食品業者' "\n"
        'グリーンアスパラガス,アスパラ;アスパラガス;グリーンアスパラ,1.10,kg,青果業者' "\n"
        'ウインナーソーセージ,ウインナー;ソーセージ;ウィンナー;ウィンナーソーセージ,1.00,袋,食品業者' "\n"
        '鶏ひき肉,鶏挽き肉;鶏ミンチ,1.05,kg,精肉業者' "\n"
        'パプリカ(赤),赤パプリカ;パプリカ赤,1.10,kg,青果業者' "\n"
        'しらす干し,しらす;シラス干し,1.00,kg,鮮魚業者' "\n"
        'チンゲンサイ,青梗菜;チンゲン菜,1.10,kg,青果業者' "\n"
        'オレンジ,みかん,1.00,個,青果業者' "\n"
        'マカロニ,マカロ二,1.00,袋,食品業者' "\n"
        'きな粉,きなこ;黄粉,1.00,袋,食品業者' "\n"
        '油揚げ,油あげ,1.00,枚,食品業者' "\n"
        'かぼちゃ,南瓜;カボチャ,1.10,kg,青果業者' "\n"
        'ごぼう,牛蒡;ゴボウ,1.10,kg,青果業者' "\n"
        'フライドポテト,ポテトフライ,1.00,袋,食品業者' "\n"
        'なめこ,ナメコ,1.00,袋,青果業者' "\n"
        '片栗粉,片栗;片困粉;用本明;有本塊,1.00,kg,食品業者' "\n"
        'クリームコーン缶,クリームコーン;コーン缶;クリームコーンかん;クリームコーン館,1.00,缶,食品業者' "\n"
        '豆乳,とうにゅう;トウニュウ;豆孔;豆礼,1.00,L,食品業者' "\n"
        'オレンジ濃縮果汁,オレンジ果汁;オレンジ濃縮;濃縮オレンジ果汁;オレンジのうしゅく果汁,1.00,L,食品業者' "\n"
        '粉かんてん,粉寒天;かんてん;寒天;粉かんでん,1.00,袋,食品業者' "\n"
        'みかん缶,みかん;蜜柑缶;ミカン缶;みかんかん,1.00,缶,食品業者' "\n"
        'SBカレーフレーク,S&Bカレーフレーク;カレーフレーク;ＳＢカレーフレーク;Ｓ＆Ｂカレーフレーク;SBカレー,1.00,袋,食品業者' "\n"
        'だいこん,大根;ダイコン;だいこ;たいこん,1.15,kg,青果業者' "\n"
        'ツナ油漬,ツナ;ツナ缶;ツナ油漬け;ツナ油づけ,1.00,缶,食品業者' "\n"
        'パイン缶,パイン;パイナップル缶;パインかん;パイナップル,1.00,缶,食品業者' "\n"
        'スパゲティ,スパゲッティ;スパゲテイ;パスタ,1.00,kg,食品業者' "\n"
        'パイシート(冷凍),パイシート;冷凍パイシート;パイシート（冷凍）;バイシート,1.00,枚,食品業者' "\n",
        encoding="utf-8-sig",
    )

def load_food_master(path: Path) -> pd.DataFrame:
    ensure_food_master(path)
    master = pd.read_csv(path, encoding="utf-8-sig")
    for column in REQUIRED_COLUMNS:
        if column not in master.columns:
            master[column] = ""
    return master.fillna("")


def _loss_rate(row: pd.Series) -> float:
    try:
        return float(row["ロス率"] or 1.0)
    except (TypeError, ValueError):
        return 1.0


def _normalized_from_row(
    row: pd.Series,
    *,
    match_type: str,
    distance: int | None = None,
) -> NormalizedFood:
    return NormalizedFood(
        name=str(row["正式名称"]).strip(),
        supplier=str(row["仕入先"]).strip(),
        order_unit=str(row["発注単位"]).strip(),
        loss_rate=_loss_rate(row),
        found_in_master=True,
        match_type=match_type,
        distance=distance,
    )


def _levenshtein_distance(left: str, right: str) -> int:
    """2つの文字列のレーベンシュタイン距離を返します。"""

    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            insert_cost = current[right_index - 1] + 1
            delete_cost = previous[right_index] + 1
            replace_cost = previous[right_index - 1] + (left_char != right_char)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def _candidate_names(row: pd.Series) -> list[str]:
    official_name = str(row["正式名称"]).strip()
    aliases = [alias.strip() for alias in str(row["別名"]).split(";") if alias.strip()]
    return [name for name in [official_name, *aliases] if name]


def normalize_food_name(raw_name: str, master: pd.DataFrame) -> NormalizedFood:
    """食材名をマスタの正式名称に寄せます。"""

    cleaned = raw_name.strip()
    compact = "".join(cleaned.split())
    for _, row in master.iterrows():
        if cleaned in _candidate_names(row) or compact in _candidate_names(row):
            return _normalized_from_row(row, match_type="完全一致")

    for _, row in master.iterrows():
        for candidate_name in _candidate_names(row):
            candidate_compact = "".join(candidate_name.split())
            if len(candidate_compact) >= 2 and (candidate_compact in compact or compact in candidate_compact):
                return _normalized_from_row(row, match_type="部分一致")

    best_row: pd.Series | None = None
    best_distance: int | None = None
    for _, row in master.iterrows():
        for candidate_name in _candidate_names(row):
            distance = _levenshtein_distance(compact, "".join(candidate_name.split()))
            max_allowed = min(MAX_LEVENSHTEIN_DISTANCE, max(1, len(candidate_name) // 3))
            if distance > max_allowed:
                continue
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_row = row

    if best_row is not None and best_distance is not None:
        return _normalized_from_row(best_row, match_type="類似補正", distance=best_distance)

    return NormalizedFood(
        name=cleaned,
        supplier="",
        order_unit="",
        loss_rate=1.0,
        found_in_master=False,
    )
