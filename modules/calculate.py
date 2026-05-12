"""MVP用の簡易集計処理です。"""

from __future__ import annotations

import pandas as pd


def aggregate_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    """同じ食材・単位を合計します。"""

    if candidates.empty:
        return candidates.copy()

    work = candidates[candidates["要確認"] != True].copy() if "要確認" in candidates.columns else candidates.copy()
    if work.empty:
        return work
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
    grouped["発注数量"] = grouped["必要量"]
    return grouped
