from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.ocr import debug_overlays_for_upload

st.set_page_config(page_title="献立表OCR MVP", page_icon="🍱", layout="wide")

st.title("🍱 献立表OCR MVP")
st.caption("原画像そのままOCRと前処理後画像を確認します。計算・Excel出力・抽出処理は停止中です。")

st.warning("まず原画像そのままOCRを確認します。二値化はOFF、前処理はグレースケールのみです。")

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

uploaded_file.seek(0)
file_bytes = uploaded_file.read()
st.write(f"アップロード済み: `{uploaded_file.name}`")

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
        st.image(overlay.preprocessed_image, caption="前処理後画像（グレースケールのみ・二値化OFF）", use_column_width=True)

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
                    caption=f"{crop.label} 前処理後（グレースケールのみ・二値化OFF） / 信頼度 {crop.confidence}",
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
