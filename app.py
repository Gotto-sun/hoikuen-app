from __future__ import annotations

from pathlib import Path

import streamlit as st

from modules.extract import extract_food_candidates
from modules.normalize import load_food_master
from modules.ocr import run_ocr_for_upload

FOOD_MASTER_PATH = Path("data/food_master.csv")

st.set_page_config(page_title="献立表OCR MVP", page_icon="🍱", layout="wide")

st.title("🍱 献立表OCR MVP")
st.caption("画像/PDFを見出しごとに分割し、食材候補を画面に表示します。")

st.warning(
    "OCR結果は間違う可能性があります。発注前に必ず確認・修正してください。"
)

with st.sidebar:
    st.header("読み取り対象")
    st.write("- 午前おやつ")
    st.write("- 昼食")
    st.write("- 午後おやつ")
    st.divider()
    st.write("対応ファイル")
    st.write("- 画像: jpg / jpeg / png")
    st.write("- PDF")

uploaded_file = st.file_uploader(
    "献立表ファイルをアップロードしてください",
    type=["jpg", "jpeg", "png", "pdf"],
)

if uploaded_file is None:
    st.info("まず画像またはPDFを選んでください。")
    st.stop()

file_bytes = uploaded_file.getvalue()
st.write(f"アップロード済み: `{uploaded_file.name}`")

if st.button("OCRを実行する", type="primary"):
    with st.spinner("OCRを実行しています。少しお待ちください..."):
        try:
            st.session_state["ocr_result"] = run_ocr_for_upload(uploaded_file.name, file_bytes)
            st.session_state.pop("candidates", None)
        except Exception as exc:  # noqa: BLE001 - 画面で利用者にわかりやすく表示します。
            st.error(str(exc))
            st.stop()

ocr_result = st.session_state.get("ocr_result")
if not ocr_result:
    st.stop()

st.subheader("1. 見出し別OCR結果")
col1, col2, col3 = st.columns(3)
col1.metric("OCRエンジン", ocr_result.engine)
col2.metric("平均信頼度", f"{ocr_result.confidence}%")
col3.metric("採用した向き", "自動判定" if ocr_result.rotation == -1 else f"{ocr_result.rotation}度")

ocr_text = st.text_area(
    "見出し別OCR結果（区分 / 食材名列 / 3歳未満列）",
    value=ocr_result.text,
    height=280,
)

master = load_food_master(FOOD_MASTER_PATH)
candidates = extract_food_candidates(
    ocr_text,
    master,
    ocr_result.confidence,
)
st.session_state["candidates"] = candidates

st.subheader("2. 全食材候補")
accepted_foods = candidates.attrs.get("accepted_foods", [])
excluded_rows = candidates.attrs.get("excluded_rows")

st.caption("採用された食材")
if accepted_foods:
    st.write("、".join(accepted_foods))
else:
    st.write("なし")

st.caption("除外された理由（ログ）")
if excluded_rows is not None and not excluded_rows.empty:
    st.dataframe(
        excluded_rows,
        use_container_width=True,
        hide_index=True,
    )
else:
    st.write("なし")

if candidates.empty:
    st.warning("食材候補が見つかりませんでした。見出し別OCR結果を確認してください。")
    st.stop()

st.dataframe(
    candidates,
    use_container_width=True,
    hide_index=True,
)

st.info("計算処理とExcel出力は停止中です。まず読み取り結果だけ確認してください。")
