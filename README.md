# ほいくえんアプリ

このリポジトリは、ビルド処理を使わずに `index.html` だけを GitHub Pages で公開する静的サイトです。

## GitHub Pages の設定

GitHub Actions や Jekyll は使いません。GitHub Pages はブランチ直下の静的ファイルをそのまま配信する設定にしてください。

1. GitHubでこのリポジトリを開きます。
2. 上のメニューから **Settings** を押します。
3. 左のメニューから **Pages** を押します。
4. **Build and deployment** の **Source** を **Deploy from a branch** にします。
5. **Branch** は公開したいブランチ（例: `work`）、フォルダは **/ (root)** を選びます。
6. **Save** を押します。
7. 表示された公開URLをスマホやPCのブラウザで開くと `index.html` が表示されます。

## 構成

- `index.html`: アプリ本体です。HTML、CSS、JavaScript をこの1ファイル内にまとめています。写真OCR、献立内容の解析、食品名と数量の集計、発注書xlsxの作成までブラウザ内で実行します。
- `.nojekyll`: GitHub Pages に Jekyll 処理をさせないための空ファイルです。

## 使い方

1. スマホまたはPCのブラウザで `index.html` を開きます。
2. **カメラで撮る** または **画像を選ぶ** から献立表の写真を取り込みます。
3. OCR後に献立内容を解析し、食品名と3歳未満児量を集計します。
4. **OCR後に発注書Excelを自動ダウンロードする** がオンの場合は、集計完了後に `.xlsx` が自動で作成されます。オフの場合や手直し後は **発注書Excel（.xlsx）を作成** を押してください。

## ページを書きかえる方法

`index.html` を編集して保存し、公開ブランチに push してください。GitHub Pages がブランチ直下の `index.html` をそのまま配信します。
