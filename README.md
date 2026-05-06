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

- `index.html`: アプリ本体です。HTML、CSS、JavaScript をこの1ファイル内にまとめています。
- `.nojekyll`: GitHub Pages に Jekyll 処理をさせないための空ファイルです。

## ページを書きかえる方法

`index.html` を編集して保存し、公開ブランチに push してください。GitHub Pages がブランチ直下の `index.html` をそのまま配信します。
