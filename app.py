from __future__ import annotations

import logging

import streamlit as st

from modules.ocr import raw_ocr_pages_for_upload

st.set_page_config(page_title="OCR全文確認モード", page_icon="🍱", layout="wide")

st.title("🍱 OCR全文確認")
st.caption("画像/PDFを読み込んだら、OCR処理を必ず実行して全文を表示します。")

logger = logging.getLogger(__name__)

with st.sidebar:
    st.header("確認順")
    st.write("1. 画像表示OK")
    st.write("2. OCR処理を強制実行")
    st.write("3. OCR全文を必ず表示")
    st.divider()
    st.write("一旦停止中")
    st.write("- 食材抽出")
    st.write("- Excel出力")
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

# ファイルアップロード後に必ずOCR関数を呼びます。
if "raw_ocr_pages" not in st.session_state:
    with st.spinner("OCR全文を取得しています..."):
        try:
            st.session_state["raw_ocr_pages"] = raw_ocr_pages_for_upload(
                uploaded_file.name, file_bytes, mime_type=uploaded_file.type
            )
        except Exception as exc:  # noqa: BLE001 - 画面で利用者にわかりやすく表示します。
            print("OCR ERROR:", exc)
            logger.exception("OCR全文確認モード失敗: file_name=%s", uploaded_file.name)
            st.error(f"OCRエラー: {exc}")
            st.stop()

raw_ocr_pages = st.session_state.get("raw_ocr_pages", [])

st.subheader("原画像OCR確認")
st.info("OCR全文の表示を優先するため、抽出処理は一旦停止しています。")

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

    text = page.ocr_text or ""
    if not text.strip():
        st.warning("OCRが空です")

    label = "OCR全文" if len(raw_ocr_pages) == 1 else f"OCR全文（{page.page_number}ページ目）"
    st.text_area(label, text, height=400, key=f"raw-ocr-{page.page_number}")

    # OCR全文の確認を優先するため、extract処理は一旦コメントアウトします。
    # candidates = extract_food_candidates(
    #     page.ocr_text,
    #     load_food_master(Path(FOOD_MASTER_PATH)),
    #     page.ocr_confidence,
    # )
