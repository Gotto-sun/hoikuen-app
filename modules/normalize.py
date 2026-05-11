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


REQUIRED_COLUMNS = ["正式名称", "別名", "ロス率", "発注単位", "仕入先"]


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


def normalize_food_name(raw_name: str, master: pd.DataFrame) -> NormalizedFood:
    """食材名をマスタの正式名称に寄せます。"""

    cleaned = raw_name.strip()
    for _, row in master.iterrows():
        official_name = str(row["正式名称"]).strip()
        aliases = [alias.strip() for alias in str(row["別名"]).split(";") if alias.strip()]
        names = [official_name, *aliases]
        if cleaned in names:
            return NormalizedFood(
                name=official_name,
                supplier=str(row["仕入先"]).strip(),
                order_unit=str(row["発注単位"]).strip(),
                loss_rate=float(row["ロス率"] or 1.0),
                found_in_master=True,
            )

    return NormalizedFood(
        name=cleaned,
        supplier="",
        order_unit="",
        loss_rate=1.0,
        found_in_master=False,
    )
