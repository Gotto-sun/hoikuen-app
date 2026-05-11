from __future__ import annotations

import datetime as dt
from pathlib import Path

import streamlit as st

from modules.export_excel import build_order_dataframe, dataframe_to_excel_bytes
from modules.extract import extract_food_candidates
from modules.normalize import load_food_master
from modules.ocr import run_ocr_for_upload

FOOD_MASTER_PATH = Path("data/food_master.csv")

st.set_page_config(page_title="献立表OCR MVP", page_icon="🍱", layout="wide")

st.title("🍱 献立表OCR MVP")
st.caption("画像/PDFからOCR全文を表示し、食材候補を確認してExcel出力します。")

st.warning(
    "OCR結果は間違う可能性があります。発注前に必ず確認・修正してください。"
)

with st.sidebar:
    st.header("基本情報")
    order_date = st.date_input("発注日", value=dt.date.today())
    use_date = st.date_input("使用日", value=dt.date.today())
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

st.subheader("1. OCR全文")
col1, col2, col3 = st.columns(3)
col1.metric("OCRエンジン", ocr_result.engine)
col2.metric("平均信頼度", f"{ocr_result.confidence}%")
col3.metric("採用した向き", "自動判定" if ocr_result.rotation == -1 else f"{ocr_result.rotation}度")

ocr_text = st.text_area(
    "OCR結果全文（必要ならここで直接修正できます）",
    value=ocr_result.text,
    height=280,
)

if st.button("食材候補を抽出する"):
    master = load_food_master(FOOD_MASTER_PATH)
    st.session_state["candidates"] = extract_food_candidates(
        ocr_text,
        master,
        ocr_result.confidence,
    )

candidates = st.session_state.get("candidates")
if candidates is None:
    st.stop()

st.subheader("2. 食材候補の確認・修正")
if candidates.empty:
    st.warning("食材候補が見つかりませんでした。OCR全文を確認してください。")
    st.stop()

edited_candidates = st.data_editor(
    candidates,
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "要確認": st.column_config.CheckboxColumn("要確認"),
        "数量": st.column_config.NumberColumn("数量", min_value=0.0, step=0.1),
        "OCR信頼度": st.column_config.NumberColumn("OCR信頼度", min_value=0.0, max_value=100.0),
    },
)

st.subheader("3. Excel出力")
st.write("このMVPでは、確認・修正した表を新しいExcelとして出力します。既存Excelは上書きしません。")

order_df = build_order_dataframe(
    edited_candidates,
    order_date=order_date.isoformat(),
    use_date=use_date.isoformat(),
)

with st.expander("出力前プレビュー", expanded=True):
    st.dataframe(order_df, use_container_width=True)

excel_bytes = dataframe_to_excel_bytes(order_df)
file_name = f"order_{use_date.strftime('%Y%m%d')}.xlsx"
st.download_button(
    "Excelをダウンロードする",
    data=excel_bytes,
    file_name=file_name,
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
