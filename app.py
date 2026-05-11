from __future__ import annotations

import logging
from pathlib import Path

import streamlit as st

from modules.extract import extract_food_candidates
from modules.normalize import load_food_master
from modules.ocr import raw_ocr_pages_for_upload

st.set_page_config(page_title="OCRエンジン直読み確認モード", page_icon="🍱", layout="wide")

st.title("🍱 原画像OCR全文から食材抽出")
st.caption("固定表OCRは使わず、原画像OCR全文から食材名を補正し、3歳未満の数量を抽出します。")

st.info("原画像OCR全文 → 食材名補正 → 数値抽出の順で処理します。")

logger = logging.getLogger(__name__)
FOOD_MASTER_PATH = "data/food_master.csv"

with st.sidebar:
    st.header("確認順")
    st.write("1. 画像表示OK")
    st.write("2. OCR全文表示OK")
    st.write("3. 食材名補正")
    st.write("4. 3歳未満数量抽出")
    st.divider()
    st.write("使わない処理")
    st.write("- 固定表切り出し")
    st.write("- 二値化")
    st.write("- トリミング")
    st.write("- 回転補正")
    st.divider()
    st.write("対応ファイル")
    st.write("- 画像: jpg / jpeg / png / tif / tiff")
    st.write("- PDF")

uploaded_file = st.file_uploader(
    "献立表ファイルをアップロードしてください",
    type=["jpg", "jpeg", "png", "tif", "tiff", "pdf"],
)

if uploaded_file is None:
    st.info("まず画像またはPDFを選んでください。")
    st.stop()

file_bytes = uploaded_file.getvalue()
st.write(f"アップロード済み: `{uploaded_file.name}`")

current_upload_key = (uploaded_file.name, len(file_bytes))
if st.session_state.get("upload_key") != current_upload_key:
    st.session_state["upload_key"] = current_upload_key
    st.session_state.pop("raw_ocr_pages", None)

if st.button("原画像OCR全文から食材を抽出する", type="primary"):
    with st.spinner("原画像OCR全文を取得し、食材と数量を抽出しています..."):
        try:
            st.session_state["raw_ocr_pages"] = raw_ocr_pages_for_upload(
                uploaded_file.name, file_bytes, mime_type=uploaded_file.type
            )
        except Exception as exc:  # noqa: BLE001 - 画面で利用者にわかりやすく表示します。
            st.error(str(exc))
            logger.exception("原画像OCR確認モード失敗: file_name=%s", uploaded_file.name)
            st.stop()

raw_ocr_pages = st.session_state.get("raw_ocr_pages")
if not raw_ocr_pages:
    st.info("ボタンを押すと、原画像表示とOCR全文表示を実行します。")
    st.stop()

st.subheader("原画像OCR確認")
st.info("この画面では固定表OCR・表切り出しを実行しません。取得済みOCR全文だけから抽出します。")

for page in raw_ocr_pages:
    st.markdown(f"### {page.page_number}ページ目")
    st.image(
        page.original_image,
        caption=f"Pillowで読み込んだ原画像（{page.original_image.width}x{page.original_image.height}）",
        use_column_width=True,
    )

    for diagnostic in page.diagnostics:
        if diagnostic.startswith("⚠️"):
            st.warning(diagnostic)
        else:
            st.info(diagnostic)

    st.text_area(
        f"OCR全文：{page.page_number}ページ目 / 信頼度 {page.ocr_confidence}",
        page.ocr_text or "（空）",
        height=360,
        key=f"raw-ocr-{page.page_number}",
    )

    candidates = extract_food_candidates(page.ocr_text, load_food_master(Path(FOOD_MASTER_PATH)), page.ocr_confidence)
    st.markdown("#### 抽出結果")
    if candidates.empty:
        st.warning("食材を抽出できませんでした。")
    else:
        st.dataframe(
            candidates[["補正後食材名", "数量", "単位", "元の行", "備考"]],
            use_container_width=True,
            hide_index=True,
        )
