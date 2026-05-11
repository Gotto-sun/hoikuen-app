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
        "正式名称,別名,ロス率,発注単位,仕入先\n"
        "にんじん,人参;ニンジン;人じん,1.10,kg,青果業者\n"
        "玉ねぎ,玉葱;タマネギ,1.08,kg,青果業者\n"
        "豚ひき肉,豚ひき内;豚ミンチ,1.05,kg,精肉業者\n"
        "いちごジャム,でちこジャ;苺ジャム,1.00,個,食品業者\n",
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
    for _, row in master.iterrows():
        if cleaned in _candidate_names(row):
            return _normalized_from_row(row, match_type="完全一致")

    best_row: pd.Series | None = None
    best_distance: int | None = None
    for _, row in master.iterrows():
        for candidate_name in _candidate_names(row):
            distance = _levenshtein_distance(cleaned, candidate_name)
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
