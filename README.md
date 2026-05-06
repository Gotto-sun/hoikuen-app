# ほいくえんアプリ

このリポジトリは、`index.html` を GitHub Pages で公開するための設定を入れています。

## スマホで見られるURLにする手順

1. GitHubでこのリポジトリを開きます。
2. 上のメニューから **Settings** を押します。
3. 左のメニューから **Pages** を押します。
4. **Build and deployment** の **Source** を **GitHub Actions** にします。
5. `work` ブランチに変更を push します。
6. 上のメニューから **Actions** を押します。
7. **Deploy static site to GitHub Pages** が緑のチェックになるまで待ちます。
8. もう一度 **Settings → Pages** を開くと、公開URLが出ます。
9. そのURLをスマホのブラウザで開くと `index.html` が見られます。

## ページを書きかえる方法

`index.html` を編集して保存し、`work` ブランチに push してください。GitHub Actions が自動で公開ページを更新します。
