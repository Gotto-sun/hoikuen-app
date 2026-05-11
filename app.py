from __future__ import annotations

import io
import logging

import pandas as pd
import streamlit as st
from PIL import Image

from modules.ocr import debug_overlays_for_upload

st.set_page_config(page_title="献立表OCR MVP", page_icon="🍱", layout="wide")

st.title("🍱 献立表OCR MVP")
st.caption("Pillowで読み込んだ原画像を画面表示してから、原画像そのままOCRを確認します。")

st.warning("PNG/JPEG/TIFFはPillowで読み込みます。原画像が表示されるまでOCRへ進みません。二値化・トリミング・回転補正はOFFです。")

logger = logging.getLogger(__name__)

with st.sidebar:
    st.header("表示する枠")
    st.write("- 食材名列：青")
    st.write("- 3歳未満列：赤")
    st.divider()
    st.write("区分範囲")
    st.write("- 午前おやつ：青")
    st.write("- 昼食：緑")
    st.write("- 午後おやつ：紫")
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

suffix = uploaded_file.name.rsplit(".", 1)[-1].lower() if "." in uploaded_file.name else ""
if suffix != "pdf":
    try:
        with Image.open(io.BytesIO(file_bytes)) as opened_image:
            img = opened_image.convert("RGB")
    except Exception:  # noqa: BLE001 - OCR前に画像表示できない場合は止めます。
        st.error("画像を読み込めませんでした。")
        logger.exception("Pillow画像読み込み失敗: file_name=%s", uploaded_file.name)
        st.stop()

    logger.info("アップロード原画像サイズ: file_name=%s width=%s height=%s", uploaded_file.name, img.width, img.height)
    st.image(img, caption=f"読み込んだ原画像（{img.width}x{img.height}）", use_column_width=True)
    st.info(f"画像サイズ: {img.width} x {img.height}")
else:
    st.info("PDFはボタン押下後に画像化して表示します。")

current_upload_key = (uploaded_file.name, len(file_bytes))
if st.session_state.get("upload_key") != current_upload_key:
    st.session_state["upload_key"] = current_upload_key
    st.session_state.pop("debug_overlays", None)

if st.button("切り出し枠を表示する", type="primary"):
    with st.spinner("切り出し枠を作成しています。少しお待ちください..."):
        try:
            st.session_state["debug_overlays"] = debug_overlays_for_upload(
                uploaded_file.name, file_bytes, mime_type=uploaded_file.type
            )
        except Exception as exc:  # noqa: BLE001 - 画面で利用者にわかりやすく表示します。
            st.error(str(exc))
            st.stop()

debug_overlays = st.session_state.get("debug_overlays")
if not debug_overlays:
    st.stop()

st.subheader("切り出し枠プレビュー")
st.info("青が食材名列、赤が3歳未満列です。抽出・計算・Excel出力は実行しません。")

for overlay in debug_overlays:
    st.markdown(f"### {overlay.page_number}ページ目")

    st.markdown("#### 原画像そのままOCR")
    original_col, preprocessed_col = st.columns(2)
    with original_col:
        st.image(overlay.original_image, caption="読み込んだ原画像（RGB変換のみ）", use_column_width=True)
    with preprocessed_col:
        st.image(overlay.preprocessed_image, caption="OCR入力画像（原画像RGB・前処理OFF）", use_column_width=True)

    for diagnostic in overlay.diagnostics:
        if diagnostic.startswith("⚠️"):
            st.warning(diagnostic)
        else:
            st.info(diagnostic)

    st.text_area(
        f"原画像そのままOCR結果：{overlay.page_number}ページ目 / 信頼度 {overlay.original_ocr_confidence}",
        overlay.original_ocr_text or "（空）",
        height=220,
        key=f"raw-ocr-{overlay.page_number}",
    )

    st.image(overlay.image, caption="検出した切り出し枠", use_column_width=True)

    rows = [
        {
            "区分": box.section,
            "種類": "区分範囲" if box.kind == "section" else "列範囲",
            "表示名": box.label,
            "検出方法": box.source,
            "左X": box.box[0],
            "上Y": box.box[1],
            "右X": box.box[2],
            "下Y": box.box[3],
        }
        for box in overlay.boxes
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.markdown("#### OCR対象の切り出し小画像")
    for section in ["午前おやつ", "昼食", "午後おやつ"]:
        section_crops = [crop for crop in overlay.crops if crop.section == section]
        if not section_crops:
            continue
        st.markdown(f"##### {section}")
        columns = st.columns(len(section_crops))
        for column, crop in zip(columns, section_crops, strict=False):
            with column:
                st.image(crop.image, caption=f"{crop.label} 原画像", use_column_width=True)
                st.image(
                    crop.processed_image,
                    caption=f"{crop.label} OCR入力（前処理OFF） / 信頼度 {crop.confidence}",
                    use_column_width=True,
                )
                for diagnostic in crop.diagnostics:
                    if diagnostic.startswith("⚠️"):
                        st.warning(diagnostic)
                    else:
                        st.caption(diagnostic)
                st.text_area(
                    f"OCR結果：{section} / {crop.label}",
                    crop.ocr_text or "（空）",
                    height=140,
                    key=f"ocr-{overlay.page_number}-{section}-{crop.label}",
                )
