from __future__ import annotations

import logging

import streamlit as st

from modules.ocr import raw_ocr_pages_for_upload

st.set_page_config(page_title="OCRエンジン直読み確認モード", page_icon="🍱", layout="wide")

st.title("🍱 OCRエンジン直読み確認モード")
st.caption("Pillowで読み込んだ原画像を表示し、原画像をそのままOCRへ渡します。")

st.warning("固定表OCR・切り出し・二値化・トリミング・回転補正は一旦OFFです。原画像OCRで文字が出るか先に確認します。")

logger = logging.getLogger(__name__)

with st.sidebar:
    st.header("確認順")
    st.write("1. 画像表示OK")
    st.write("2. OCR全文表示OK")
    st.write("3. 固定表OCR再開")
    st.divider()
    st.write("現在OFF")
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

if st.button("原画像を表示してOCR全文を確認する", type="primary"):
    with st.spinner("Pillowで原画像を読み込み、原画像のままOCRしています..."):
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
st.info("この画面では固定表OCRを実行しません。Pillowで読み込んだ原画像をそのままOCRしています。")

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
